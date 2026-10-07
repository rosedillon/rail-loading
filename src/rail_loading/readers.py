"""Read every operator's passenger loading data into one schema.

Train operators publish passenger counts through the Rail Data
Marketplace, each in its own layout. Column names, date formats, day
spellings (``Monday`` or ``1-Sun``), times as strings or Excel serials,
CSV or XLSX, and even what is being counted all vary. This module has
one reader per published layout, and each returns rows of
:data:`CANONICAL_COLUMNS`, so code downstream never needs to know which
operator a row came from.

Northern and TPE report passengers on and off at each stop. Everyone
else reports only the load on the train as it leaves each stop, so
boardings and alightings are derived from the change in load between
consecutive calls (:func:`derive_on_off`). That is a net figure.
Passengers swapping at the same stop cancel out, so a derived boarding
count is a lower bound and can be well short at a big interchange.

Every reader returns passenger counts and says so in the ``load_units``
column, so rows can be checked before they are mixed with an occupancy
percentage from somewhere else. CrossCountry is not read at all because
its feed is a 0-100 busyness band.

Readers are registered in :data:`READERS`, keyed by operator name.
"""

from __future__ import annotations

import datetime
import logging
import pathlib
import re
from collections.abc import Callable

import pandas as pd
from tqdm import tqdm

logger = logging.getLogger(__name__)

CANONICAL_COLUMNS = [
    "Train Operator",
    "Unique ID",
    "Headcode",
    "Service Week Day",
    "Origin CRS",
    "Destination CRS",
    "Origin Departure Time",
    "Departure from CRS",
    "Departure Time",
    "Call Sequence",
    "RSP Period",
    "Period Start Date",
    "Period End Date",
    "Sample Size",
    "Mean Capacity",
    "Mean Load on Departure",
    "Mean Passengers On",
    "Mean Passengers Off",
    "load_units",
]

# Bump an operator's entry when its reader's output changes, so its
# cached standardised frame is rebuilt.
READER_VERSION = {"gtr": 2}
_DEFAULT_READER_VERSION = 3

_MAX_LOAD = 3000

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DAY_BY_PREFIX = {d[:3].lower(): d for d in _DAY_NAMES}

# Identifies one run of one train within one count period, for deriving
# boardings from load changes along its calling pattern.
_INSTANCE_KEY = [
    "Train Operator",
    "Unique ID",
    "Headcode",
    "Service Week Day",
    "Origin CRS",
    "Destination CRS",
    "Origin Departure Time",
    "Period Start Date",
]


# Small normalisers.


def _clean_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Tidy whitespace, including non-breaking spaces, in headers."""
    df.columns = [re.sub(r"\s+", " ", str(c).replace("\xa0", " ")).strip() for c in df.columns]
    return df


def _crs(series: pd.Series) -> pd.Series:
    out = series.astype("string").str.strip().str.upper()
    return out.mask(out.isin(["", "NAN", "NONE", "<NA>"]))


def _num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype("string").str.replace(",", "", regex=False).str.rstrip("%"),
        errors="coerce",
    )


def _day(series: pd.Series) -> pd.Series:
    """Map any day spelling ("Mon", "1-Sun") to its full name."""
    text = series.astype("string").str.strip().str.lower().str.replace(r"^\d+-", "", regex=True)
    return text.str[:3].map(_DAY_BY_PREFIX)


def _time(series: pd.Series) -> pd.Series:
    """Normalise clock times ("6:02", "06:02:00") to ``HH:MM:SS``."""

    def _one(value: object) -> str | None:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        if isinstance(value, datetime.time | datetime.datetime | pd.Timestamp):
            return value.strftime("%H:%M:%S")
        text = str(value).strip()
        match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
        if not match:
            return None
        return f"{int(match.group(1)) % 24:02d}:{match.group(2)}:{match.group(3) or '00'}"

    return series.map(_one).astype("string")


def _date(series: pd.Series, dayfirst: bool = False) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", dayfirst=dayfirst).dt.normalize()


def _canonical(df: pd.DataFrame) -> pd.DataFrame:
    for col in CANONICAL_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    return df[CANONICAL_COLUMNS]


def derive_on_off(df: pd.DataFrame) -> pd.DataFrame:
    """Fill boardings and alightings from the change in load.

    Only rows whose ``Mean Passengers On`` is missing are filled, so
    operators that report real boardings keep them. Calls are ordered by
    ``Call Sequence``. A call at the service's destination is the
    terminus (everyone alights, departing load 0); operators that count
    departures only often omit that row, in which case alightings at the
    terminus stay unknown.
    """
    need = df["Mean Passengers On"].isna() & df["Mean Load on Departure"].notna()
    ordered = df["Call Sequence"].notna()
    if not (need & ordered).any():
        return df
    if not ordered.all():
        # Calls with no known order can't be differenced; leave them as
        # is.
        return pd.concat([derive_on_off(df[ordered]), df[~ordered]])
    df = df.sort_values([*_INSTANCE_KEY, "Call Sequence"], kind="stable")
    key = [df[c].astype("string").fillna("") for c in _INSTANCE_KEY]
    grouped = df.groupby(key, sort=False)["Mean Load on Departure"]
    is_first = grouped.cumcount().eq(0)
    is_terminus = grouped.cumcount(ascending=False).eq(0) & df["Departure from CRS"].eq(
        df["Destination CRS"]
    ).fillna(False)
    load = df["Mean Load on Departure"].astype(float).mask(is_terminus, 0.0)
    # Compare with the last *counted* call; before any counted call the
    # train's starting load is unknown, so boardings stay unknown too.
    last_known = load.groupby(key, sort=False).ffill()
    previous = last_known.groupby(key, sort=False).shift(1).mask(is_first, 0.0)
    delta = load - previous
    fill = df["Mean Passengers On"].isna()
    df.loc[fill, "Mean Passengers On"] = delta.clip(lower=0)[fill]
    df.loc[fill, "Mean Passengers Off"] = (-delta).clip(lower=0)[fill]
    return df


def _read_csv_any_encoding(file: pathlib.Path) -> pd.DataFrame:
    try:
        return pd.read_csv(file, dtype=str, encoding="utf-8-sig", low_memory=False)
    except UnicodeDecodeError:
        return pd.read_csv(file, dtype=str, encoding="cp1252", low_memory=False)


def _files(path: pathlib.Path, patterns: tuple[str, ...]) -> list[pathlib.Path]:
    if path.is_file():
        return [path]
    found = sorted({p for pattern in patterns for p in path.glob(pattern)})
    return [p for p in found if not p.name.startswith(("~$", "."))]


# Readers, one per published layout.


def read_northern(path: pathlib.Path, **_: object) -> pd.DataFrame:
    """Northern: period CSVs that carry passengers on and off."""
    chunks = []
    # The folder also holds formation/capacity tables; only period files
    # are loading data.
    for file in tqdm(_files(path, ("Loading_Data_*.csv",)), desc="Northern"):
        raw = pd.read_csv(file, dtype=str, low_memory=False)
        df = pd.DataFrame(
            {
                "Train Operator": "northern",
                # No UID is published; the timetable tag separates the
                # same headcode running under two timetables within one
                # period.
                "Unique ID": raw["Timetable"].astype("string"),
                "Headcode": raw["Headcode"],
                "Service Week Day": _day(raw["Service Week Day"]),
                "Origin CRS": _crs(raw["Origin CRS"]),
                "Destination CRS": _crs(raw["Destination CRS"]),
                "Origin Departure Time": _time(raw["Origin Departure Time"]),
                "Departure from CRS": _crs(raw["Departure from CRS"]),
                "RSP Period": raw["RSP Period"],
                "Period Start Date": _date(raw["Period Start Date"]),
                "Period End Date": _date(raw["Period End Date"]),
                "Sample Size": _num(raw["Sample Size"]),
                "Mean Capacity": _num(raw["Mean Capacity"]),
                "Mean Load on Departure": _num(raw["Mean Load on Departure"]),
                "Mean Passengers On": _num(raw["Mean Passengers On"]),
                "Mean Passengers Off": _num(raw["Mean Passengers Off"]),
                "load_units": "passengers",
            }
        )
        chunks.append(_canonical(df))
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


def read_tpe(path: pathlib.Path, **_: object) -> pd.DataFrame:
    """TransPennine Express: one CSV with passengers on and off."""
    raw = _clean_headers(pd.read_csv(path, dtype=str, low_memory=False))
    df = pd.DataFrame(
        {
            "Train Operator": "tpe",
            "Unique ID": raw.get("RSID"),
            "Headcode": raw["Headcode"],
            "Service Week Day": _day(raw["Service Week Day"]),
            "Origin CRS": _crs(raw["Origin CRS"]),
            "Destination CRS": _crs(raw["Destination CRS"]),
            "Origin Departure Time": _time(raw["Origin Departure Time"]),
            "Departure from CRS": _crs(raw["Departure from CRS"]),
            "Departure Time": _time(raw["Departure from GBTT time"]),
            "RSP Period": "1",
            "Sample Size": _num(raw["Sample Size"]),
            "Mean Load on Departure": _num(raw["Mean Occupancy"]),
            "Mean Passengers On": _num(raw["Mean Passenger On"]),
            "Mean Passengers Off": _num(raw["Mean Passenger Off"]),
            "load_units": "passengers",
        }
    )
    return _canonical(df)


def _order_by_time(df: pd.DataFrame) -> pd.Series:
    """Order calls by station time, allowing for midnight."""
    origin = pd.to_timedelta(df["Origin Departure Time"].fillna("00:00:00"), errors="coerce")
    station = pd.to_timedelta(df["Departure Time"], errors="coerce")
    minutes = (station - origin).dt.total_seconds() / 60
    return minutes.where(minutes >= 0, minutes + 24 * 60)


def read_awc(path: pathlib.Path, **_: object) -> pd.DataFrame:
    """Avanti West Coast: one CSV of mean loads with no call numbers."""
    raw = _clean_headers(pd.read_csv(path, dtype=str, low_memory=False))
    df = pd.DataFrame(
        {
            "Train Operator": "awc",
            "Unique ID": raw["Unique ID"],
            "Headcode": raw["Train ID (Headcode)"],
            "Service Week Day": _day(raw["Day of Count"]),
            "Origin CRS": _crs(raw["Origin of Service (CRS)"]),
            "Destination CRS": _crs(raw["Destination of Service (CRS)"]),
            "Origin Departure Time": _time(raw["Timetabled Departure from Origin"]),
            "Departure from CRS": _crs(raw["Station CRS"]),
            "Departure Time": _time(raw["Station Scheduled Arrival Time"]),
            "RSP Period": "1",
            "Sample Size": _num(raw["Number of Counts"]),
            "Mean Capacity": _num(raw["Standard Seated Capacity"])
            + _num(raw["First Class Seated Capacity"]).fillna(0),
            "Mean Load on Departure": _num(raw["Total Load (Mean)"]),
            "load_units": "passengers",
        }
    )
    df["Call Sequence"] = _order_by_time(df)
    return _canonical(df)


def read_emr(
    path: pathlib.Path, tiploc_to_crs: dict[str, str] | None = None, **_: object
) -> pd.DataFrame:
    """East Midlands Railway: CSV with origin/destination as TIPLOCs."""
    tiploc_to_crs = tiploc_to_crs or {}
    raw = _clean_headers(pd.read_csv(path, dtype=str, low_memory=False))
    df = pd.DataFrame(
        {
            "Train Operator": "emr",
            "Unique ID": raw["UniqueID"],
            "Headcode": raw["TrainID"],
            "Service Week Day": _day(raw["DayOfCount"]),
            "Origin CRS": _crs(raw["Origin"].str.strip().str.upper().map(tiploc_to_crs)),
            "Destination CRS": _crs(raw["Destination"].str.strip().str.upper().map(tiploc_to_crs)),
            "Origin Departure Time": _time(raw["TimetabledDepartureTimeFromOrigin"]),
            "Departure from CRS": _crs(raw["StationCRS"]),
            "Departure Time": _time(
                raw["StationScheduledDepartureTime"].fillna(raw["StationScheduledArrivalTime"])
            ),
            "Call Sequence": _num(raw["CallingPointNumber"]),
            "RSP Period": raw["StartOfCountPeriod"],
            "Period Start Date": _date(raw["StartOfCountPeriod"]),
            "Period End Date": _date(raw["EndOfCountPeriod"]),
            "Sample Size": _num(raw["NumberOfCounts"]),
            "Mean Capacity": _num(raw["StandardSeatingCapacity"])
            + _num(raw["FirstClassSeatingCapacity"]).fillna(0),
            "Mean Load on Departure": _num(raw["TotalLoad_Mean"]),
            "load_units": "passengers",
        }
    )
    return _canonical(_fill_origin_destination(df))


def _fill_origin_destination(df: pd.DataFrame) -> pd.DataFrame:
    key = ["Headcode", "Service Week Day", "Origin Departure Time", "Period Start Date"]
    key_cols = [df[c].astype("string").fillna("") for c in key]
    ordered = df.sort_values("Call Sequence", kind="stable")
    first = ordered.groupby(key_cols, sort=False)["Departure from CRS"].transform("first")
    last = ordered.groupby(key_cols, sort=False)["Departure from CRS"].transform("last")
    df["Origin CRS"] = df["Origin CRS"].fillna(first.reindex(df.index))
    df["Destination CRS"] = df["Destination CRS"].fillna(last.reindex(df.index))
    return df


def _gwr_frame(raw: pd.DataFrame, period: str) -> pd.DataFrame:
    raw = _clean_headers(raw)
    dayfirst = not pd.api.types.is_datetime64_any_dtype(raw["Start of count period"])
    return pd.DataFrame(
        {
            "Train Operator": "gwr",
            "Unique ID": raw["Unique ID (UID)"],
            "Headcode": raw["Train ID (4 Char)"],
            "Service Week Day": _day(raw["Day of Count Text"]),
            "Origin CRS": _crs(raw["Origin of service CRS"]),
            "Destination CRS": _crs(raw["Destination of service CRS"]),
            "Origin Departure Time": _time(raw["Timetabled departure time from origin"]),
            "Departure from CRS": _crs(raw["Departure from CRS"]),
            "Departure Time": _time(raw["Departure from GBTT time"]),
            "Call Sequence": _num(raw["Calling Sequence"]),
            "RSP Period": period,
            "Period Start Date": _date(raw["Start of count period"], dayfirst=dayfirst),
            "Period End Date": _date(raw["End of count period"], dayfirst=dayfirst),
            "Sample Size": _num(raw["Number of counts"]),
            "Mean Capacity": _num(raw["Standard seating capacity"])
            + _num(raw["First class seating capacity"]).fillna(0),
            "Mean Load on Departure": _num(raw["TotalLoad"]),
            "load_units": "passengers",
        }
    )


def read_gwr(path: pathlib.Path, **_: object) -> pd.DataFrame:
    """Great Western Railway: one CSV or XLSX per rail period."""
    chunks = []
    for file in tqdm(_files(path, ("*.csv", "*.xlsx")), desc="GWR"):
        match = re.search(r"Period\s*(\d{4})", file.name)
        period = match.group(1) if match else file.stem
        if file.suffix.lower() == ".xlsx":
            raw = pd.read_excel(file, dtype={"Calling Sequence": str})
        else:
            raw = _read_csv_any_encoding(file)
        df = _gwr_frame(raw, period)
        # Calls of one train/day can carry slightly different count
        # windows within the period; give them one shared window so the
        # calls group into a single run when boardings are derived.
        run = [df["Unique ID"].astype(str), df["Service Week Day"].astype(str)]
        df["Period Start Date"] = df.groupby(run)["Period Start Date"].transform("min")
        df["Period End Date"] = df.groupby(run)["Period End Date"].transform("max")
        chunks.append(_canonical(df))
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


def read_gtr(path: pathlib.Path, **_: object) -> pd.DataFrame:
    """Govia Thameslink Railway: one XLSX per period, a sheet per week.

    Each row is one dated run. Boardings are derived per dated run, then
    everything is averaged to one row per (train, day of week, station)
    within the rail period.
    """
    chunks = []
    for file in tqdm(_files(path, ("*.xlsx",)), desc="GTR"):
        sheets = pd.read_excel(file, sheet_name=None)
        for raw in sheets.values():
            if raw.empty or "Station CRS" not in raw.columns:
                continue
            raw = _clean_headers(raw)
            date = _date(raw["Date"])
            is_arrival = (
                raw["Count on arrival or departure?"].astype(str).str.lower().eq("arrival")
            )
            load = _num(raw["Total load"])
            df = pd.DataFrame(
                {
                    "Train Operator": "gtr",
                    "Unique ID": raw["Unique ID (UID)"],
                    "Headcode": raw["Train ID"],
                    "Service Week Day": _day(raw["Day of Count"]),
                    "Origin CRS": _crs(raw["Origin of service CRS"]),
                    "Destination CRS": _crs(raw["Destination of service CRS"]),
                    "Origin Departure Time": _time(raw["Timetabled departure time from origin"]),
                    "Departure from CRS": _crs(raw["Station CRS"]),
                    "Departure Time": _time(raw["Station Scheduled Arrival Time"]),
                    "Call Sequence": _num(raw["Calling point number"]),
                    "RSP Period": raw["Rail period"].astype(str),
                    "Period Start Date": date,
                    "Period End Date": date,
                    "Sample Size": _num(raw["Number of counts"]),
                    "Mean Capacity": _num(raw["Standard seating capacity"])
                    + _num(raw["First class seating capacity"]).fillna(0),
                    # An arrival count is the load arriving, not
                    # departing; only the terminus is counted that way,
                    # where departing load is 0.
                    "Mean Load on Departure": load.mask(is_arrival),
                    "load_units": "passengers",
                }
            )
            chunks.append(derive_on_off(_canonical(df)))
    if not chunks:
        return pd.DataFrame()
    daily = pd.concat(chunks, ignore_index=True)
    return _average_dated_runs(daily)


def _average_dated_runs(daily: pd.DataFrame) -> pd.DataFrame:
    """Average dated runs to one row per train, day and station."""
    key = [
        "Train Operator",
        "Unique ID",
        "Headcode",
        "Service Week Day",
        "Origin CRS",
        "Destination CRS",
        "Origin Departure Time",
        "Departure from CRS",
        "RSP Period",
        "load_units",
    ]
    for col in key:
        daily[col] = daily[col].astype("string").fillna("")
    out = (
        daily.groupby(key, sort=False)
        .agg(
            **{
                "Departure Time": ("Departure Time", "first"),
                "Call Sequence": ("Call Sequence", "median"),
                "Period Start Date": ("Period Start Date", "min"),
                "Period End Date": ("Period End Date", "max"),
                "Sample Size": ("Mean Load on Departure", "count"),
                "Mean Capacity": ("Mean Capacity", "mean"),
                "Mean Load on Departure": ("Mean Load on Departure", "mean"),
                "Mean Passengers On": ("Mean Passengers On", "mean"),
                "Mean Passengers Off": ("Mean Passengers Off", "mean"),
            }
        )
        .reset_index()
    )
    for col in key:
        out[col] = out[col].replace("", pd.NA)
    return _canonical(out)


_Window = tuple[pd.Timestamp | None, pd.Timestamp | None]

# Two dates such as "1st June 2025 - 28th June 2025".
_SWR_DAY = r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w+)\s+(\d{4})"
_SWR_PERIOD_DATES = re.compile(rf"{_SWR_DAY}\s*-\s*{_SWR_DAY}")


def _day_or_none(text: str) -> pd.Timestamp | None:
    value = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(value) else value


def _swr_period_dates(notes: pd.DataFrame) -> _Window:
    for value in notes.astype(str).to_numpy().ravel():
        match = _SWR_PERIOD_DATES.search(value)
        if match:
            d1, m1, y1, d2, m2, y2 = match.groups()
            return _day_or_none(f"{d1} {m1} {y1}"), _day_or_none(f"{d2} {m2} {y2}")
    return None, None


def _rail_period_dates(code: str) -> _Window:
    """Dates of a railway period such as ``"RY27 P03"`` (2026/27).

    P01 starts on 1 April; P02 starts on the first Sunday on or after 29
    April and every later period is four weeks long, ending the day
    before the next. P13 ends on 31 March.
    """
    match = re.search(r"RY(\d{2})\s*P(\d{2})", code)
    if not match:
        return None, None
    year = 2000 + int(match.group(1)) - 1
    period = int(match.group(2))
    april = pd.Timestamp(year=year, month=4, day=1)
    p2 = april + pd.Timedelta(days=28)
    p2 += pd.Timedelta(days=(6 - p2.weekday()) % 7)

    def _start(n: int) -> pd.Timestamp:
        return april if n == 1 else p2 + pd.Timedelta(weeks=4 * (n - 2))

    end = (
        pd.Timestamp(year=year + 1, month=3, day=31)
        if period == 13
        else _start(period + 1) - pd.Timedelta(days=1)
    )
    return _start(period), end


def read_swr(
    path: pathlib.Path, tiploc_to_crs: dict[str, str] | None = None, **_: object
) -> pd.DataFrame:
    """South Western Railway: XLSX with a two-row header and TIPLOCs."""
    tiploc_to_crs = tiploc_to_crs or {}
    chunks = []
    unmapped: set[str] = set()
    for file in tqdm(_files(path, ("*.xlsx",)), desc="SWR"):
        book = pd.read_excel(file, sheet_name=None, header=None)
        notes = book.get("Notes", pd.DataFrame())
        start, end = _swr_period_dates(notes)
        grid = book["Loadings Data"]
        top = grid.iloc[0].ffill()
        sub = grid.iloc[1]
        names = [
            str(s).strip() if pd.notna(s) else str(t).strip()
            for t, s in zip(top, sub, strict=True)
        ]
        raw = grid.iloc[2:].copy()
        # Layouts differ by year: "Calendar Day" became "ODT", and the
        # load was once headed "Average Train Load on Departure".
        names = [
            "ODT"
            if n == "Calendar Day"
            else "Average Train Load"
            if n.startswith("Average Train Load")
            else n
            for n in names
        ]
        raw.columns = names
        raw = raw.dropna(subset=["Location"])
        if start is None:
            start, end = _rail_period_dates(str(raw["Rail Period"].dropna().iloc[0]))
        tiplocs = raw["Location"].astype(str).str.strip().str.upper()
        unmapped.update(tiplocs[~tiplocs.isin(tiploc_to_crs)].unique())
        df = pd.DataFrame(
            {
                "Train Operator": "swr",
                "Unique ID": raw["Train UID"],
                "Headcode": raw["Headcode"],
                "Service Week Day": _day(raw["ODT"]),
                "Origin CRS": _crs(
                    raw["Origin"].astype(str).str.strip().str.upper().map(tiploc_to_crs)
                ),
                "Destination CRS": _crs(
                    raw["Destination"].astype(str).str.strip().str.upper().map(tiploc_to_crs)
                ),
                "Origin Departure Time": _time(raw["Origin Time"]),
                "Departure from CRS": _crs(tiplocs.map(tiploc_to_crs)),
                "Departure Time": _time(raw["Dep"].fillna(raw["Arr"])),
                "RSP Period": raw["Rail Period"].astype(str),
                "Period Start Date": start,
                "Period End Date": end,
                "Sample Size": _num(raw["Number of Readings"]),
                "Mean Capacity": _num(raw["Standard Seated"])
                + _num(raw["First Class Seats"]).fillna(0),
                "Mean Load on Departure": _num(raw["Average Train Load"]),
                "load_units": "passengers",
            }
        )
        # Rows are published in calling order within each train.
        df["Call Sequence"] = df.groupby(
            [raw["Train UID"].astype(str), raw["ODT"].astype(str)], sort=False
        ).cumcount()
        chunks.append(_canonical(df.dropna(subset=["Departure from CRS"])))
    if unmapped:
        logger.warning(
            "SWR: %d TIPLOC(s) have no CRS mapping and were dropped (mostly "
            "non-station timing points): %s",
            len(unmapped),
            ", ".join(sorted(unmapped)[:40]),
        )
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


def _average_counts_frame(raw: pd.DataFrame, operator: str) -> pd.DataFrame:
    """Read one Southeastern or Greater Anglia counts CSV.

    These are the "Average Passenger Counts" files. Both the current
    layout and the one used before August 2025 are read.
    """
    raw = _clean_headers(raw)
    if "Train UId" in raw.columns:  # layout from Aug 2025 onwards
        load = _num(raw["Total Load (Mean)"])
        is_arrival = raw["Count Event"].astype(str).str.lower().eq("arrival")
        df = pd.DataFrame(
            {
                "Unique ID": raw["Train UId"],
                "Headcode": raw["Headcode"],
                "Service Week Day": _day(raw["Day Of Count"]),
                "Origin CRS": _crs(raw["Origin CRS"]),
                "Destination CRS": _crs(raw["Destination CRS"]),
                "Origin Departure Time": _time(raw["Origin Time"]),
                "Departure from CRS": _crs(raw["Station CRS"]),
                "Departure Time": _time(raw["Station Departure"].fillna(raw["Station Arrival"])),
                "Call Sequence": _num(raw["Calling Point Number"]),
                "Period Start Date": _date(raw["Start Date"]),
                "Period End Date": _date(raw["End Date"]),
                "Sample Size": _num(raw["Number of Counts"]),
                "Mean Capacity": _num(raw["Standard Seating Capacity"])
                + _num(raw["First Class Seating Capacity"]).fillna(0),
                "Mean Load on Departure": load.mask(is_arrival),
            }
        )
    else:  # earlier layout: no UID/headcode, one "Order" per call
        origin_time = _time(raw["Start Time"])
        df = pd.DataFrame(
            {
                "Headcode": origin_time.str[:5].str.replace(":", "")
                + raw["Origin CRS"].astype(str)
                + raw["Destination CRS"].astype(str),
                "Service Week Day": _day(raw["Day of Week"]),
                "Origin CRS": _crs(raw["Origin CRS"]),
                "Destination CRS": _crs(raw["Destination CRS"]),
                "Origin Departure Time": origin_time,
                "Departure from CRS": _crs(raw["CRS"]),
                "Call Sequence": _num(raw["Order"]),
                "Period Start Date": _date(raw["Start Date"]),
                "Period End Date": _date(raw["End Date"]),
                "Sample Size": _num(raw["Observations"]),
                # Some files also publish an uncapped load; the capped
                # figure is truncated at the train's capacity, so prefer
                # uncapped.
                "Mean Load on Departure": _num(
                    raw["Average Loading (Uncapped)"]
                    if "Average Loading (Uncapped)" in raw.columns
                    else raw["Average Loading"]
                ),
            }
        )
    df["Train Operator"] = operator
    df["RSP Period"] = df["Period Start Date"].dt.strftime("%Y-%m-%d")
    df["load_units"] = "passengers"
    return _canonical(df)


_WINDOW_IN_NAME = re.compile(r"(\d{8}) to (\d{8})")
_RAIL_PERIOD_DAYS = 28


def select_count_windows(files: list[pathlib.Path]) -> list[pathlib.Path]:
    """Drop files that re-publish an already-covered count window.

    Southeastern and Greater Anglia publish some windows twice: a
    railway period (4 weeks from a Sunday) alongside a calendar month,
    and in 2024 a capped and an uncapped copy of the same period.
    Keeping both would count those weeks twice. Files with an uncapped
    load column are preferred, then railway-period files (they tile the
    year without overlap); any file whose window overlaps a kept file by
    more than half is dropped.
    """
    undated: list[pathlib.Path] = []
    dated: list[tuple[pathlib.Path, pd.Timestamp, pd.Timestamp]] = []
    for file in files:
        match = _WINDOW_IN_NAME.search(file.name)
        if match:
            dated.append((file, pd.Timestamp(match.group(1)), pd.Timestamp(match.group(2))))
        else:
            undated.append(file)

    def priority(item: tuple[pathlib.Path, pd.Timestamp, pd.Timestamp]) -> tuple[int, int, str]:
        file, start, end = item
        is_rail_period = (end - start).days + 1 == _RAIL_PERIOD_DAYS
        uncapped = any("Uncapped" in c for c in pd.read_csv(file, nrows=0).columns)
        return (0 if uncapped else 1, 0 if is_rail_period else 1, file.name)

    kept: list[tuple[pathlib.Path, pd.Timestamp, pd.Timestamp]] = []
    for item in sorted(dated, key=priority):
        file, start, end = item
        length = (end - start).days + 1
        overlap = max(
            ((min(end, k_end) - max(start, k_start)).days + 1 for _, k_start, k_end in kept),
            default=0,
        )
        if overlap > length / 2:
            logger.info("Skipping %s: its window is already covered.", file.name)
            continue
        kept.append(item)
    return sorted([*undated, *(file for file, _, _ in kept)])


def _average_counts_reader(operator: str, label: str) -> Callable[..., pd.DataFrame]:
    def reader(path: pathlib.Path, **_: object) -> pd.DataFrame:
        files = select_count_windows(_files(path, ("*.csv",)))
        chunks = [
            _average_counts_frame(pd.read_csv(file, dtype=str, low_memory=False), operator)
            for file in tqdm(files, desc=label)
        ]
        return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()

    reader.__doc__ = f"{label}: 'Average Passenger Counts' CSVs, one per period."
    return reader


Reader = Callable[..., pd.DataFrame]

READERS: dict[str, Reader] = {
    "northern": read_northern,
    "tpe": read_tpe,
    "awc": read_awc,
    "emr": read_emr,
    "gwr": read_gwr,
    "gtr": read_gtr,
    "swr": read_swr,
    "southeastern": _average_counts_reader("southeastern", "Southeastern"),
    "greater_anglia": _average_counts_reader("greater_anglia", "Greater Anglia"),
}


def _dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows that more than one file republishes identically.

    The same headcode can recur legitimately. A count period spanning a
    timetable change lists a train under both timetables, and operators
    without dates publish several runs. Rows are only dropped when every
    value, loads included, matches.
    """
    before = len(df)
    df = df.drop_duplicates()
    if len(df) < before:
        logger.info("Dropped %d identical row(s) republished across files.", before - len(df))
    return df


def load_operator(
    operator: str,
    path: pathlib.Path,
    tiploc_to_crs: dict[str, str] | None = None,
    cache_dir: pathlib.Path | None = None,
) -> pd.DataFrame:
    """Read one operator's loading data into :data:`CANONICAL_COLUMNS`.

    With ``cache_dir`` set, the standardised frame is pickled there and
    reused while no source file is newer than the cache (the XLSX
    operators are slow to parse).
    """
    if operator not in READERS:
        raise ValueError(f"Unknown operator {operator!r}; expected one of {sorted(READERS)}.")
    if not path.exists():
        logger.warning("Loading data for %s not found at %s; skipped.", operator, path)
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    version = READER_VERSION.get(operator, _DEFAULT_READER_VERSION)
    cache = cache_dir / f"{operator}_v{version}.pkl" if cache_dir else None
    newest = max((p.stat().st_mtime for p in _files(path, ("*",))), default=0.0)
    if cache is not None and cache.exists() and cache.stat().st_mtime >= newest:
        logger.info("Using cached standardised %s data from %s", operator, cache)
        cached: pd.DataFrame = pd.read_pickle(cache)
        return cached

    logger.info("Reading %s loading data from %s ...", operator, path)
    df = READERS[operator](path, tiploc_to_crs=tiploc_to_crs)
    if df.empty:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    df = df.dropna(subset=["Departure from CRS", "Service Week Day"])
    # No British train carries more than about 3,000 people, so larger
    # values are keying errors in the source.
    impossible = (df["load_units"] == "passengers") & (df["Mean Load on Departure"] > _MAX_LOAD)
    if impossible.any():
        logger.warning(
            "%s: voided %d impossible load value(s) above %d.",
            operator,
            int(impossible.sum()),
            _MAX_LOAD,
        )
        df.loc[
            impossible, ["Mean Load on Departure", "Mean Passengers On", "Mean Passengers Off"]
        ] = pd.NA
    df = _dedupe(df)
    df = derive_on_off(df).reset_index(drop=True)
    logger.info("Loaded %d %s rows.", len(df), operator)

    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        df.to_pickle(cache)
    return df
