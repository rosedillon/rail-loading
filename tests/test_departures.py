from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pytest

from rail_loading import (
    ARRIVAL_TIME_OPERATORS,
    RESOLVED_COLUMN,
    SOURCE_COLUMN,
    add_resolved_departures,
    load_timetable_departures,
    resolve_departure_times,
)

if TYPE_CHECKING:
    import pathlib

_CALL_COLUMNS = [
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


@pytest.fixture
def calls_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """A calls table laid out like cif-timetable's output."""
    rows = [
        # atoc, stp, public, headcode, origin, dest, origin dep, stop,
        # dep
        ("NT", "P", "True", "1A01", "LDS", "YRK", "0700", "CFD", "0706"),
        # An overlay for the same train; only permanent schedules count.
        ("NT", "O", "True", "1A01", "LDS", "YRK", "0700", "CFD", "0710"),
        ("NT", "P", "True", "1A02", "LDS", "HGT", "0730", "CFD", "0736"),
        ("NT", "P", "True", "2Y99", "LDS", "YRK", "0800", "CFD", "0805"),
        ("NT", "P", "True", "1A04", "LDS", "YRK", "0900H", "CFD", "0907"),
        # Same key, two different times: ambiguous, so never used.
        ("NT", "P", "True", "1A06", "LDS", "YRK", "1000", "CFD", "1006"),
        ("NT", "P", "True", "1A06", "LDS", "YRK", "1000", "CFD", "1008"),
        ("VT", "P", "True", "1H03", "EUS", "MAN", "0900", "SOT", "1032"),
        # A pass, not a public call.
        ("NT", "P", "False", "1A07", "LDS", "YRK", "1100", "CFD", "1106"),
    ]
    path = tmp_path / "timetable_calls.csv"
    pd.DataFrame(rows, columns=_CALL_COLUMNS).to_csv(path, index=False)
    return path


# (operator, headcode, origin, destination, origin departure,
# departure)
Row = tuple[str, str, str, str, str | None, str | None]


def _loading(*rows: Row) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Train Operator": [row[0] for row in rows],
            "Headcode": [row[1] for row in rows],
            "Origin CRS": [row[2] for row in rows],
            "Destination CRS": [row[3] for row in rows],
            "Origin Departure Time": [row[4] for row in rows],
            "Departure from CRS": ["SOT" if row[0] == "awc" else "CFD" for row in rows],
            "Departure Time": [row[5] for row in rows],
        }
    )


def _resolve(calls_path: pathlib.Path, row: Row) -> tuple[object, object]:
    result = resolve_departure_times(_loading(row), calls_path)
    return result[RESOLVED_COLUMN].iloc[0], result[SOURCE_COLUMN].iloc[0]


class TestOperatorsNeedingImputation:
    def test_arrival_time_operators(self) -> None:
        # Avanti and GTR publish the call's scheduled arrival, not
        # departure.
        assert frozenset({"awc", "gtr"}) == ARRIVAL_TIME_OPERATORS


class TestLadder:
    def test_published_departure_wins(self, calls_path: pathlib.Path) -> None:
        row = ("tpe", "1A01", "LDS", "YRK", "07:00:00", "07:03:00")
        assert _resolve(calls_path, row) == ("07:03:00", "published")

    def test_exact_match(self, calls_path: pathlib.Path) -> None:
        row = ("northern", "1A01", "LDS", "YRK", "07:00:00", None)
        assert _resolve(calls_path, row) == ("07:06:00", "timetable_exact")

    def test_wrong_destination(self, calls_path: pathlib.Path) -> None:
        row = ("northern", "1A02", "LDS", "YRK", "07:30:00", None)
        assert _resolve(calls_path, row) == ("07:36:00", "timetable_no_destination")

    def test_pseudo_headcode(self, calls_path: pathlib.Path) -> None:
        row = ("northern", "0800LDSYRK", "LDS", "YRK", "08:00:00", None)
        assert _resolve(calls_path, row) == ("08:05:00", "timetable_no_headcode")

    def test_nearest_origin_time(self, calls_path: pathlib.Path) -> None:
        row = ("northern", "1A04", "LDS", "YRK", "09:02:00", None)
        assert _resolve(calls_path, row) == ("09:07:00", "timetable_nearest_origin")

    def test_ambiguous_key(self, calls_path: pathlib.Path) -> None:
        row = ("northern", "1A06", "LDS", "YRK", "10:00:00", None)
        assert _resolve(calls_path, row) == ("10:00:00", "origin_fallback")

    def test_non_public_call_ignored(self, calls_path: pathlib.Path) -> None:
        row = ("northern", "1A07", "LDS", "YRK", "11:00:00", None)
        assert _resolve(calls_path, row)[1] == "origin_fallback"

    def test_arrival_operator_uses_timetable(self, calls_path: pathlib.Path) -> None:
        row = ("awc", "1H03", "EUS", "MAN", "09:00:00", "10:30:00")
        assert _resolve(calls_path, row) == ("10:32:00", "timetable_exact")

    def test_arrival_operator_fallback(self, calls_path: pathlib.Path) -> None:
        row = ("awc", "1H99", "EUS", "MAN", "12:00:00", "13:30:00")
        assert _resolve(calls_path, row) == ("13:30:00", "published_arrival")

    def test_nothing_to_go_on(self, calls_path: pathlib.Path) -> None:
        row = ("northern", "1A08", "LDS", "YRK", None, None)
        resolved, source = _resolve(calls_path, row)
        assert pd.isna(resolved)
        assert source == "unresolved"


class TestLoadTimetableDepartures:
    def test_only_permanent_public_calls(self, calls_path: pathlib.Path) -> None:
        calls = load_timetable_departures(calls_path, ["northern"])
        first = calls[calls["headcode"] == "1A01"]
        assert list(first["stop_dep"]) == ["0706"]
        assert "1A07" not in set(calls["headcode"])

    def test_filters_to_the_operators_asked_for(self, calls_path: pathlib.Path) -> None:
        assert set(load_timetable_departures(calls_path, ["awc"])["operator"]) == {"awc"}

    def test_unknown_operator(self, calls_path: pathlib.Path) -> None:
        assert load_timetable_departures(calls_path, ["nosuchtoc"]).empty


class TestAddResolvedDepartures:
    def test_rows_and_published_column_are_kept(self, calls_path: pathlib.Path) -> None:
        frame = _loading(
            ("northern", "1A01", "LDS", "YRK", "07:00:00", None),
            ("tpe", "1A01", "LDS", "YRK", "07:00:00", "07:03:00"),
        )
        result = add_resolved_departures(frame, calls_path)
        assert len(result) == 2
        assert result["Departure Time"].iloc[1] == "07:03:00"
        assert list(result[SOURCE_COLUMN]) == ["timetable_exact", "published"]
