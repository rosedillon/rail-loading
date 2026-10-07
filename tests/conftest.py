"""Synthetic loading files, one per published layout.

Operator loading data is licensed through the Rail Data Marketplace, so
no real extract is committed. These fixtures copy each operator's layout
(the column names and value formats they publish) and fill it with
invented counts.

The layouts below were taken from real file headers, e.g. Northern's

    toc,Service Week Day,6# Headcode,Headcode,rsid,...,Mean Passengers
    On,...

and TPE's

    TOC,Service Week Day,Headcode,RSID,...,Departure from GBTT time,...

so a change to a reader's expected column names fails here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

if TYPE_CHECKING:
    import pathlib


@pytest.fixture
def northern_folder(tmp_path: pathlib.Path) -> pathlib.Path:
    """Northern: period CSVs already carrying passengers on and off."""
    folder = tmp_path / "northern-loading-data"
    folder.mkdir()
    frame = pd.DataFrame(
        {
            "toc": ["Northern"] * 3,
            "Service Week Day": ["Monday", "Monday", "Monday"],
            "6# Headcode": ["1A01", "1A01", "1A01"],
            "Headcode": ["1A01", "1A01", "1A01"],
            "rsid": ["NT000100", "NT000100", "NT000100"],
            "Service Description": ["Leeds to York"] * 3,
            "Origin CRS": ["LDS"] * 3,
            "Origin Tiploc": ["LEEDS"] * 3,
            "Origin Tiploc Name": ["Leeds"] * 3,
            "Origin Departure Time": ["07:00:00"] * 3,
            "Destination CRS": ["YRK"] * 3,
            "Destination Tiploc": ["YORK"] * 3,
            "Destination Tiploc Name": ["York"] * 3,
            "Departure from CRS": ["LDS", "CFD", "YRK"],
            "Departure from TIPLOC": ["LEEDS", "CRSSFLT", "YORK"],
            "Departure from TIPLOC Name": ["Leeds", "Cross Gates", "York"],
            # Northern publishes no UID; the timetable tag separates the
            # same headcode running under two timetables in one period.
            "Timetable": ["2025-05", "2025-05", "2025-05"],
            "RSP Period": ["P05", "P05", "P05"],
            "Period Start Date": ["2025-07-20"] * 3,
            "Period End Date": ["2025-08-16"] * 3,
            "Sample Size": ["20", "20", "20"],
            "Mean Capacity": ["300", "300", "300"],
            "Mean Load on Departure": ["100", "130", "0"],
            "Mean Passengers On": ["100", "40", "0"],
            "Mean Passengers Off": ["0", "10", "130"],
        }
    )
    frame.to_csv(folder / "Loading_Data_2025_P05.csv", index=False)
    # A formation table that lives in the same folder and must be
    # ignored.
    pd.DataFrame({"unit": ["150001"], "seats": [148]}).to_csv(
        folder / "Formation_And_Capacity.csv", index=False
    )
    return folder


@pytest.fixture
def tpe_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """TPE: one CSV with on and off counts and each call's departure."""
    path = tmp_path / "tpe-loading-data.csv"
    pd.DataFrame(
        {
            "TOC": ["TPE"] * 2,
            "Service Week Day": ["Tuesday", "Tuesday"],
            "Headcode": ["1E02", "1E02"],
            "RSID": ["TP000200", "TP000200"],
            "Service Description": ["Manchester to Leeds"] * 2,
            "Origin CRS": ["MAN", "MAN"],
            "Origin TIPLOC": ["MNCRPIC", "MNCRPIC"],
            "Origin TIPLOC Name": ["Manchester Piccadilly"] * 2,
            "Origin Departure Time": ["08:15:00", "08:15:00"],
            "Destination CRS": ["LDS", "LDS"],
            "Destination TIPLOC": ["LEEDS", "LEEDS"],
            "Destination TIPLOC Name": ["Leeds", "Leeds"],
            "Destination Arrival Time": ["09:05:00", "09:05:00"],
            "Departure from CRS": ["MAN", "HUD"],
            "Departure from TIPLOC": ["MNCRPIC", "HDRSFLD"],
            "Departure from TIPLOC Name": ["Manchester Piccadilly", "Huddersfield"],
            "Departure from GBTT time": ["08:15", "08:40"],
            "Next station CRS": ["HUD", "LDS"],
            "Next station TIPLOC": ["HDRSFLD", "LEEDS"],
            "Next station TIPLOC Name": ["Huddersfield", "Leeds"],
            "Next station GBTT time": ["08:40", "09:05"],
            "Fleet Type": ["185", "185"],
            "Sample Size": ["15", "15"],
            "Mean Occupancy": ["90", "100"],
            "Mean Passenger On": ["90", "30"],
            "Mean Passenger Off": ["0", "20"],
        }
    ).to_csv(path, index=False)
    return path


@pytest.fixture
def awc_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """Avanti: load only, so boardings have to be derived."""
    path = tmp_path / "awc-loading-data.csv"
    pd.DataFrame(
        {
            "Train Operator": ["Avanti West Coast"] * 3,
            "Unique ID": ["A1", "A1", "A1"],
            "Train ID (Headcode)": ["1H03", "1H03", "1H03"],
            "Count on Arrival or Departure": ["Departure"] * 3,
            "Day of Count": ["Wednesday"] * 3,
            "Origin of Service (CRS)": ["EUS"] * 3,
            "Destination of Service (CRS)": ["MAN"] * 3,
            "Timetabled Departure from Origin": ["09:00"] * 3,
            "Timetabled Arrival Time at Destination Station": ["11:05"] * 3,
            "Station CRS": ["EUS", "SOT", "MAN"],
            "Station Scheduled Arrival Time": ["09:00", "10:30", "11:05"],
            "First Class Seated Capacity ": ["100"] * 3,
            "Standard Seated Capacity ": ["400"] * 3,
            "Maximum Standing Capacity": ["100"] * 3,
            "Number of Counts": ["10"] * 3,
            "Type of Count": ["Manual"] * 3,
            # Load rises then falls; boardings must come from the
            # differences.
            "Total Load (Mean)": ["250", "300", "0"],
        }
    ).to_csv(path, index=False)
    return path


@pytest.fixture
def unreadable_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """A CSV in Windows-1252, which some operators publish."""
    path = tmp_path / "cp1252-loading-data.csv"
    path.write_bytes(
        "TOC,Service Week Day,Headcode,Departure from CRS,Mean Capacity\n"
        "Op\xe9rateur,Monday,1A01,LDS,300\n".encode("cp1252")
    )
    return path


@pytest.fixture
def emr_file(tmp_path: pathlib.Path) -> pathlib.Path:
    """EMR: origin and destination are TIPLOCs, not CRS codes."""
    path = tmp_path / "emr-loading-data.csv"
    pd.DataFrame(
        {
            "Train Operator": ["East Midlands Railway"] * 3,
            "UniqueID": ["E1", "E1", "E1"],
            "TrainID": ["1B04", "1B04", "1B04"],
            "StartOfCountPeriod": ["2026-05-03"] * 3,
            "EndOfCountPeriod": ["2026-05-30"] * 3,
            "TimetabledDepartureTimeFromOrigin": ["04:47:00"] * 3,
            "Origin": ["DRBY"] * 3,
            "Destination": ["STPX"] * 3,
            "CallingPointNumber": ["1", "2", "3"],
            "StationCRS": ["DBY", "LBO", "STP"],
            "StationScheduledArrivalTime": [None, "05:01:00", "06:30:00"],
            "StationScheduledDepartureTime": ["04:47:00", "05:02:00", None],
            "DayOfCount": ["Tuesday"] * 3,
            "StandardSeatingCapacity": ["200"] * 3,
            "FirstClassSeatingCapacity": ["20"] * 3,
            "NumberOfCounts": ["4"] * 3,
            "TotalLoad_Mean": ["50", "80", "0"],
        }
    ).to_csv(path, index=False)
    return path


@pytest.fixture
def gwr_folder(tmp_path: pathlib.Path) -> pathlib.Path:
    """GWR: one file per rail period, dates written day first."""
    folder = tmp_path / "gwr-loading-data"
    folder.mkdir()
    pd.DataFrame(
        {
            "Unique ID (UID)": ["G1", "G1"],
            "Train ID (4 Char)": ["1P01", "1P01"],
            "Day of Count Text": ["Thursday", "Thursday"],
            "Origin of service CRS": ["PAD", "PAD"],
            "Destination of service CRS": ["RDG", "RDG"],
            "Timetabled departure time from origin": ["10:00", "10:00"],
            "Departure from CRS": ["PAD", "RDG"],
            "Departure from GBTT time": ["10:00", "10:25"],
            "Calling Sequence": ["1", "2"],
            # Calls of one train can carry slightly different windows.
            "Start of count period": ["06/07/2025", "07/07/2025"],
            "End of count period": ["02/08/2025", "01/08/2025"],
            "Number of counts": ["3", "3"],
            "Standard seating capacity": ["500", "500"],
            "First class seating capacity": ["50", "50"],
            "TotalLoad": ["300", "0"],
        }
    ).to_csv(folder / "GWR RDM Passenger Loading Data Period 2704.csv", index=False)
    return folder


@pytest.fixture
def gtr_folder(tmp_path: pathlib.Path) -> pathlib.Path:
    """GTR: XLSX with one sheet per week and one row per dated run."""
    folder = tmp_path / "gtr-loading-data"
    folder.mkdir()

    def week(dates: list[str], loads: list[str]) -> pd.DataFrame:
        rows = len(dates)
        return pd.DataFrame(
            {
                "Date": dates,
                "Rail period": ["P01"] * rows,
                "Unique ID (UID)": ["T1"] * rows,
                "Train ID": ["9T01"] * rows,
                "Day of Count": ["Monday"] * rows,
                "Origin of service CRS": ["BTN"] * rows,
                "Destination of service CRS": ["BDM"] * rows,
                "Timetabled departure time from origin": ["07:00"] * rows,
                "Station CRS": ["BTN", "GTW", "BDM"] * (rows // 3),
                "Station Scheduled Arrival Time": ["07:00", "07:30", "09:00"] * (rows // 3),
                "Calling point number": ["1", "2", "3"] * (rows // 3),
                "Count on arrival or departure?": ["Departure", "Departure", "Arrival"]
                * (rows // 3),
                "Number of counts": ["1"] * rows,
                "Standard seating capacity": ["600"] * rows,
                "First class seating capacity": ["0"] * rows,
                "Total load": loads,
            }
        )

    with pd.ExcelWriter(folder / "2024-25 P01 GTR Passenger Loading.xlsx") as writer:
        week(["2024-04-01"] * 3, ["100", "200", "180"]).to_excel(
            writer, sheet_name="Week 1", index=False
        )
        week(["2024-04-08"] * 3, ["120", "260", "250"]).to_excel(
            writer, sheet_name="Week 2", index=False
        )
        pd.DataFrame({"Notes": ["Counts are automatic."]}).to_excel(
            writer, sheet_name="Notes", index=False
        )
    return folder


@pytest.fixture
def swr_folder(tmp_path: pathlib.Path) -> pathlib.Path:
    """SWR: a two-row header, TIPLOCs and the dates on a Notes sheet."""
    folder = tmp_path / "swr-loading-data"
    folder.mkdir()
    top = [
        "Location", "Train UID", "Headcode", "Calendar Day", "Origin",
        "Destination", "Origin Time", "Timing", None, "Rail Period",
        "Number of Readings", "Capacity", None,
        "Average Train Load on Departure",
    ]  # fmt: skip
    sub = [
        None, None, None, None, None, None, None, "Arr", "Dep", None, None,
        "Standard Seated", "First Class Seats", None,
    ]  # fmt: skip
    rows = [
        ["WATRLMN", "W1", "1A01", "Mon", "WATRLMN", "WOKING", "06:00", None,
         "06:00", "RY25 P10", "5", "400", "40", "150"],
        ["CLPHMJN", "W1", "1A01", "Mon", "WATRLMN", "WOKING", "06:00", "06:07",
         "06:08", "RY25 P10", "5", "400", "40", "260"],
        ["WIMBLDN", "W1", "1A01", "Mon", "WATRLMN", "WOKING", "06:00", None,
         None, "RY25 P10", "5", "400", "40", "260"],
        ["WOKING", "W1", "1A01", "Mon", "WATRLMN", "WOKING", "06:00", "06:30",
         None, "RY25 P10", "5", "400", "40", "0"],
    ]  # fmt: skip
    with pd.ExcelWriter(folder / "SWR Passenger Loadings - RY25 P10.xlsx") as writer:
        pd.DataFrame(["Counts for 15th December 2024 - 11th January 2025"]).to_excel(
            writer, sheet_name="Notes", index=False, header=False
        )
        pd.DataFrame([top, sub, *rows]).to_excel(
            writer, sheet_name="Loadings Data", index=False, header=False
        )
    return folder


@pytest.fixture
def southeastern_folder(tmp_path: pathlib.Path) -> pathlib.Path:
    """Southeastern: both CSV layouts, and a window published twice."""
    folder = tmp_path / "southeastern-loading-data"
    folder.mkdir()
    old = {
        "Start Time": ["07:00", "07:00"],
        "Origin CRS": ["SEV", "SEV"],
        "Destination CRS": ["CHX", "CHX"],
        "Day of Week": ["Monday", "Monday"],
        "CRS": ["SEV", "CHX"],
        "Order": ["1", "2"],
        "Start Date": ["2024-06-02", "2024-06-02"],
        "End Date": ["2024-06-29", "2024-06-29"],
        "Observations": ["8", "8"],
        "Average Loading": ["400", "0"],
        "Average Loading (Uncapped)": ["450", "0"],
    }
    pd.DataFrame(old).to_csv(
        folder / "20240602 to 20240629 - Average Passenger Counts.csv", index=False
    )
    # The calendar month re-publishes the same weeks and must be
    # skipped.
    pd.DataFrame(old).to_csv(
        folder / "20240601 to 20240630 - Average Passenger Counts.csv", index=False
    )
    pd.DataFrame(
        {
            "Train UId": ["S1", "S1"],
            "Headcode": ["2D10", "2D10"],
            "Day Of Count": ["Friday", "Friday"],
            "Origin CRS": ["DVP", "DVP"],
            "Destination CRS": ["STP", "STP"],
            "Origin Time": ["08:00", "08:00"],
            "Station CRS": ["DVP", "STP"],
            "Station Departure": ["08:00", None],
            "Station Arrival": [None, "09:10"],
            "Calling Point Number": ["1", "2"],
            "Start Date": ["2025-08-17", "2025-08-17"],
            "End Date": ["2025-09-13", "2025-09-13"],
            "Number of Counts": ["6", "6"],
            "Standard Seating Capacity": ["500", "500"],
            "First Class Seating Capacity": ["0", "0"],
            "Total Load (Mean)": ["320", "300"],
            "Count Event": ["Departure", "Arrival"],
        }
    ).to_csv(folder / "20250817 to 20250913 - Average Passenger Counts.csv", index=False)
    return folder
