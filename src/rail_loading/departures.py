"""Find the timetabled departure of each counted train from its station.

Most operators publish the GBTT departure of the call a count was taken
at. Northern publishes only the train's departure from its origin, and
Avanti and GTR publish the call's scheduled arrival instead. Timing a
Northern count by its origin departure puts it a median 29 minutes
early and lands about 15% of calls in the wrong time period.

This module fills the gap from the CIF timetable, which carries the same
GBTT departure the other operators publish, so an imputed value is that
quantity and not a proxy for it. Each loading row is matched to its
schedule through a ladder of progressively looser keys, and every row
records the rung it matched on in :data:`SOURCE_COLUMN`. A caller can
then report the mix, drop a rung they do not trust, or check that the
loose rungs are not carrying the result.

The timetable is read from a calls table with one row per calling point,
the ``timetable_calls.csv`` that ``cif-timetable`` writes.
"""

from __future__ import annotations

import functools
import logging
import pathlib
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Collection

logger = logging.getLogger(__name__)

# Operators whose "Departure Time" is really the call's scheduled
# arrival: their feeds publish no departure, so a timetabled departure
# is preferred and the published arrival becomes the fallback.
ARRIVAL_TIME_OPERATORS = frozenset({"awc", "gtr"})

# Loading-data operator key -> the ATOC codes it runs under in the
# timetable. Used to keep a headcode from matching another operator's
# train.
OPERATOR_ATOC_CODES: dict[str, tuple[str, ...]] = {
    "northern": ("NT",),
    "tpe": ("TP",),
    "awc": ("VT",),
    "emr": ("EM",),
    "gwr": ("GW",),
    "gtr": ("TL", "GN", "GX", "SN"),
    "swr": ("SW",),
    "southeastern": ("SE",),
    "greater_anglia": ("LE",),
    "xc": ("XC",),
}

RESOLVED_COLUMN = "Resolved Departure Time"
SOURCE_COLUMN = "departure_time_source"

# Ladder rungs, tightest first. Each is a (label, join keys) pair; the
# last rung matches on the nearest origin departure within a tolerance
# instead.
_EXACT_KEYS = ["operator", "headcode", "origin_crs", "dest_crs", "origin_dep", "stop_crs"]
_NO_DEST_KEYS = ["operator", "headcode", "origin_crs", "origin_dep", "stop_crs"]
# Southeastern and Greater Anglia synthesise a pseudo-headcode from the
# origin time and route for about half their rows, so those can never
# match a real signalling ID. The route, the origin departure minute and
# the calling station still pin the schedule without it.
_NO_HEADCODE_KEYS = ["operator", "origin_crs", "dest_crs", "origin_dep", "stop_crs"]
_NEAREST_BY = ["operator", "headcode", "origin_crs", "dest_crs", "stop_crs"]
# A headcode-and-stop-only rung was tried and dropped: it recovered
# another 2% of rows but got 13.8% of them into the wrong time period
# (p95 error six hours), against 18% for no imputation at all, so it was
# not worth the coverage.


def _hhmm(times: pd.Series) -> pd.Series:
    """Reduce ``HH:MM:SS`` or ``HHMM`` to ``HHMM``."""
    text = times.astype("string").str.replace(":", "", regex=False)
    return text.str[:4].where(text.str.len() >= 4)


def _minutes(hhmm: pd.Series) -> pd.Series:
    """``HHMM`` -> minutes since midnight."""
    value = pd.to_numeric(hhmm, errors="coerce")
    return (value // 100) * 60 + (value % 100)


def _as_clock(hhmm: pd.Series) -> pd.Series:
    """``HHMM`` -> ``HH:MM:SS``, the format the operators publish."""
    text = hhmm.astype("string")
    return (text.str[:2] + ":" + text.str[2:4] + ":00").where(text.notna())


def _atoc_to_operator(operators: Collection[str]) -> dict[str, str]:
    return {
        code: operator for operator in operators for code in OPERATOR_ATOC_CODES.get(operator, ())
    }


@functools.lru_cache(maxsize=1)
def _read_all_departures(calls_path: pathlib.Path, chunksize: int) -> pd.DataFrame:
    """Every operator's public calls with a GBTT departure, read once.

    The calls table is most of a gigabyte and a hold-out test
    re-resolves each operator in turn, so the parsed result is memoised:
    the file is read once per process and each caller filters the frame
    it needs.
    """
    atoc_map = _atoc_to_operator(OPERATOR_ATOC_CODES)
    columns = [
        "atoc_code",
        "stp_indicator",
        "is_public_call",
        "signalling_id",
        "svc_origin_crs",
        "svc_dest_crs",
        "svc_origin_dep",
        "crs",
        "public_departure",
    ]
    frames = []
    for chunk in pd.read_csv(calls_path, usecols=columns, dtype=str, chunksize=chunksize):
        keep = chunk[
            (chunk["stp_indicator"] == "P")
            & (chunk["is_public_call"].astype(str) == "True")
            & chunk["atoc_code"].isin(atoc_map)
            & chunk["public_departure"].notna()
            & chunk["crs"].notna()
        ]
        if not keep.empty:
            frames.append(keep)
    if not frames:
        return pd.DataFrame()

    calls = pd.concat(frames, ignore_index=True)
    frame = pd.DataFrame(
        {
            "operator": calls["atoc_code"].map(atoc_map),
            "headcode": calls["signalling_id"],
            "origin_crs": calls["svc_origin_crs"],
            "dest_crs": calls["svc_dest_crs"],
            "origin_dep": _hhmm(calls["svc_origin_dep"]),
            "stop_crs": calls["crs"],
            "stop_dep": _hhmm(calls["public_departure"]),
        }
    )
    # Match the "string" dtype the loading side keys on, so the merges
    # line up.
    return frame.astype("string").dropna().drop_duplicates()


def load_timetable_departures(
    calls_path: pathlib.Path,
    operators: Collection[str],
    chunksize: int = 1_000_000,
) -> pd.DataFrame:
    """Permanent public calls with a GBTT departure, for ``operators``.

    One row per (operator, headcode, origin, destination, origin
    departure, calling station). Only permanent (STP ``P``) schedules
    are used, so short-term variations never override the base
    timetable.
    """
    wanted = {op for op in operators if op in OPERATOR_ATOC_CODES}
    if not wanted:
        return pd.DataFrame()
    calls = _read_all_departures(pathlib.Path(calls_path), chunksize)
    if calls.empty:
        return calls
    return calls[calls["operator"].isin(wanted)]


def _unambiguous(calls: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Collapse to keys that map to exactly one departure time.

    A headcode that calls at the same station at two different times
    under one key is dropped rather than guessed at, and those rows fall
    through to the next rung.
    """
    grouped = calls.groupby(keys, sort=False)["stop_dep"].agg(["first", "nunique"])
    kept = grouped[grouped["nunique"] == 1].drop(columns="nunique")
    return kept.rename(columns={"first": "stop_dep"}).reset_index()


def _match_nearest(
    unresolved: pd.DataFrame, calls: pd.DataFrame, tolerance_minutes: int
) -> pd.Series:
    """Match on the nearest origin departure, within a tolerance.

    Catches trains whose booked origin departure has shifted by a minute
    or two between the count and the timetable extract.
    """
    left = unresolved.copy()
    left["origin_min"] = _minutes(left["origin_dep"])
    right = _unambiguous(calls, [*_NEAREST_BY, "origin_dep"])
    right["origin_min"] = _minutes(right["origin_dep"])
    left = left[left["origin_min"].notna()]
    right = right[right["origin_min"].notna()]
    if left.empty or right.empty:
        return pd.Series(dtype="string")

    left = left.sort_values("origin_min")
    right = right.drop(columns="origin_dep").sort_values("origin_min")
    matched = pd.merge_asof(
        left,
        right,
        on="origin_min",
        by=_NEAREST_BY,
        direction="nearest",
        tolerance=tolerance_minutes,
    )
    return matched.set_index("row_key")["stop_dep"].dropna()


def resolve_departure_times(
    data: pd.DataFrame,
    calls_path: pathlib.Path,
    tolerance_minutes: int = 5,
) -> pd.DataFrame:
    """Add the resolved departure time and its provenance to ``data``.

    A published GBTT departure always wins. Rows without one (all of
    Northern, and gaps in any operator's data) are matched to the
    timetable through the key ladder. Avanti and GTR, which publish an
    arrival, prefer the timetabled departure and keep their arrival as
    the fallback. Anything still unmatched falls back to the train's
    origin departure.
    """
    operator = data["Train Operator"].astype("string")
    published = data["Departure Time"].astype("string")
    is_arrival = operator.isin(ARRIVAL_TIME_OPERATORS)

    resolved = published.where(published.notna() & ~is_arrival)
    source = pd.Series(pd.NA, index=data.index, dtype="string")
    source[resolved.notna()] = "published"

    need = resolved.isna()
    if need.any():
        keyed = pd.DataFrame(
            {
                "operator": operator[need],
                "headcode": data.loc[need, "Headcode"].astype("string"),
                "origin_crs": data.loc[need, "Origin CRS"].astype("string"),
                "dest_crs": data.loc[need, "Destination CRS"].astype("string"),
                "origin_dep": _hhmm(data.loc[need, "Origin Departure Time"]),
                "stop_crs": data.loc[need, "Departure from CRS"].astype("string"),
            }
        )
        # Resolve one row per distinct train-stop, then map back: a
        # train recurs across every count period in the file.
        trains = (
            keyed.drop_duplicates()
            .dropna(subset=["operator", "origin_crs", "origin_dep", "stop_crs"])
            .reset_index(drop=True)
        )
        trains["row_key"] = trains.index
        logger.info(
            "Imputing departure times for %d rows (%d distinct train calls) ...",
            int(need.sum()),
            len(trains),
        )
        calls = load_timetable_departures(calls_path, set(keyed["operator"].dropna()))
        if calls.empty:
            logger.warning("No timetable calls matched these operators; nothing imputed.")
        else:
            found = _resolve_ladder(trains, calls, tolerance_minutes)
            keys = pd.MultiIndex.from_frame(keyed[_EXACT_KEYS])
            lookup = trains.set_index(pd.MultiIndex.from_frame(trains[_EXACT_KEYS]))["row_key"]
            lookup = lookup[~lookup.index.duplicated()]
            row_key = pd.Series(lookup.reindex(keys).to_numpy(), index=keyed.index)
            for label, times in found.items():
                hit = pd.Series(row_key.map(times), index=keyed.index)
                fill = hit.notna() & resolved.isna()
                resolved[fill] = _as_clock(hit[fill])
                source[fill] = label

    # Avanti and GTR keep their published arrival before falling back
    # further.
    arrival_left = resolved.isna() & is_arrival & published.notna()
    resolved[arrival_left] = published[arrival_left]
    source[arrival_left] = "published_arrival"

    origin = data["Origin Departure Time"].astype("string")
    fallback = resolved.isna() & origin.notna()
    resolved[fallback] = origin[fallback]
    source[fallback] = "origin_fallback"
    source[resolved.isna()] = "unresolved"

    return pd.DataFrame({RESOLVED_COLUMN: resolved, SOURCE_COLUMN: source}, index=data.index)


def _resolve_ladder(
    trains: pd.DataFrame, calls: pd.DataFrame, tolerance_minutes: int
) -> dict[str, pd.Series]:
    """Run the key ladder, returning row_key -> departure per rung."""
    found: dict[str, pd.Series] = {}
    left = trains

    exact = _unambiguous(calls, _EXACT_KEYS)
    hit = left.merge(exact, on=_EXACT_KEYS, how="inner")
    found["timetable_exact"] = hit.set_index("row_key")["stop_dep"]
    left = left[~left["row_key"].isin(found["timetable_exact"].index)]

    if not left.empty:
        no_dest = _unambiguous(calls, _NO_DEST_KEYS)
        hit = left.merge(no_dest, on=_NO_DEST_KEYS, how="inner")
        found["timetable_no_destination"] = hit.set_index("row_key")["stop_dep"]
        left = left[~left["row_key"].isin(found["timetable_no_destination"].index)]

    if not left.empty:
        no_headcode = _unambiguous(calls, _NO_HEADCODE_KEYS)
        hit = left.merge(no_headcode, on=_NO_HEADCODE_KEYS, how="inner")
        found["timetable_no_headcode"] = hit.set_index("row_key")["stop_dep"]
        left = left[~left["row_key"].isin(found["timetable_no_headcode"].index)]

    if not left.empty:
        nearest = _match_nearest(left, calls, tolerance_minutes)
        found["timetable_nearest_origin"] = nearest

    for label, series in found.items():
        logger.info("  %-26s %7d train calls", label, len(series))
    return found


def add_resolved_departures(
    data: pd.DataFrame, calls_path: pathlib.Path, tolerance_minutes: int = 5
) -> pd.DataFrame:
    """Attach the resolved departures and log where they came from."""
    resolved = resolve_departure_times(data, calls_path, tolerance_minutes)
    data = pd.concat([data.drop(columns=resolved.columns, errors="ignore"), resolved], axis=1)
    mix = resolved[SOURCE_COLUMN].value_counts(normalize=True)
    logger.info(
        "Departure times resolved: %s",
        ", ".join(f"{label} {share:.1%}" for label, share in mix.items()),
    )
    return data
