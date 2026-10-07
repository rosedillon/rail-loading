from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd
import pytest

from rail_loading import CANONICAL_COLUMNS, READERS, derive_on_off, load_operator
from rail_loading.readers import _rail_period_dates, select_count_windows

if TYPE_CHECKING:
    import pathlib


class TestRegistry:
    def test_every_registered_reader_is_callable(self) -> None:
        assert all(callable(reader) for reader in READERS.values())

    def test_operators(self) -> None:
        assert sorted(READERS) == [
            "awc",
            "emr",
            "greater_anglia",
            "gtr",
            "gwr",
            "northern",
            "southeastern",
            "swr",
            "tpe",
        ]

    def test_crosscountry_has_no_reader(self) -> None:
        # Its feed is a 0-100 busyness band, not a passenger count.
        assert "xc" not in READERS
        assert "crosscountry" not in READERS


class TestNorthern:
    @pytest.fixture
    def frame(self, northern_folder: pathlib.Path) -> pd.DataFrame:
        return load_operator("northern", northern_folder)

    def test_returns_the_canonical_columns(self, frame: pd.DataFrame) -> None:
        assert list(frame.columns) == CANONICAL_COLUMNS

    def test_reads_the_period_files(self, frame: pd.DataFrame) -> None:
        assert len(frame) == 3
        assert set(frame["Departure from CRS"]) == {"LDS", "CFD", "YRK"}

    def test_ignores_other_files(self, frame: pd.DataFrame) -> None:
        # The formation/capacity table lives alongside the period files.
        assert "unit" not in frame.columns
        assert len(frame) == 3

    def test_published_boardings_kept(self, frame: pd.DataFrame) -> None:
        leeds = frame[frame["Departure from CRS"] == "LDS"]
        assert float(leeds["Mean Passengers On"].iloc[0]) == pytest.approx(100.0)

    def test_day_names_are_normalised(self, frame: pd.DataFrame) -> None:
        assert set(frame["Service Week Day"]) == {"Monday"}

    def test_operator_is_tagged(self, frame: pd.DataFrame) -> None:
        assert set(frame["Train Operator"]) == {"northern"}


class TestTpe:
    @pytest.fixture
    def frame(self, tpe_file: pathlib.Path) -> pd.DataFrame:
        return load_operator("tpe", tpe_file)

    def test_returns_the_canonical_columns(self, frame: pd.DataFrame) -> None:
        assert list(frame.columns) == CANONICAL_COLUMNS

    def test_reads_a_single_csv(self, frame: pd.DataFrame) -> None:
        assert len(frame) == 2

    def test_gbtt_departure(self, frame: pd.DataFrame) -> None:
        manchester = frame[frame["Departure from CRS"] == "MAN"]
        assert manchester["Departure Time"].iloc[0] == "08:15:00"

    def test_published_boardings_are_kept(self, frame: pd.DataFrame) -> None:
        manchester = frame[frame["Departure from CRS"] == "MAN"]
        assert float(manchester["Mean Passengers On"].iloc[0]) == pytest.approx(90.0)


class TestAvanti:
    @pytest.fixture
    def frame(self, awc_file: pathlib.Path) -> pd.DataFrame:
        return load_operator("awc", awc_file)

    def test_returns_the_canonical_columns(self, frame: pd.DataFrame) -> None:
        assert list(frame.columns) == CANONICAL_COLUMNS

    def test_load_is_read(self, frame: pd.DataFrame) -> None:
        euston = frame[frame["Departure from CRS"] == "EUS"]
        assert float(euston["Mean Load on Departure"].iloc[0]) == pytest.approx(250.0)

    def test_boardings_derived(self, frame: pd.DataFrame) -> None:
        # Avanti publishes no boardings; 250 -> 300 means at least 50
        # joined.
        stoke = frame[frame["Departure from CRS"] == "SOT"]
        assert float(stoke["Mean Passengers On"].iloc[0]) == pytest.approx(50.0)

    def test_capacity_is_seated_only(self, frame: pd.DataFrame) -> None:
        # Standard seated (400) + first class seated (100). The
        # published maximum standing capacity is left out, because load
        # against seats means something and load against crush capacity
        # does not.
        euston = frame[frame["Departure from CRS"] == "EUS"]
        assert float(euston["Mean Capacity"].iloc[0]) == pytest.approx(500.0)


def _at(frame: pd.DataFrame, crs: str, column: str) -> Any:
    return frame.loc[frame["Departure from CRS"] == crs, column].iloc[0]


_EMR_TIPLOCS = {"DRBY": "DBY", "STPX": "STP"}
# WIMBLDN is a timing point with no CRS, so its row is dropped.
_SWR_TIPLOCS = {"WATRLMN": "WAT", "CLPHMJN": "CLJ", "WOKING": "WOK"}


class TestEmr:
    @pytest.fixture
    def frame(self, emr_file: pathlib.Path) -> pd.DataFrame:
        return load_operator("emr", emr_file, tiploc_to_crs=_EMR_TIPLOCS)

    def test_tiplocs_are_mapped_to_crs(self, frame: pd.DataFrame) -> None:
        assert set(frame["Origin CRS"]) == {"DBY"}
        assert set(frame["Destination CRS"]) == {"STP"}

    def test_departure_is_preferred_to_arrival(self, frame: pd.DataFrame) -> None:
        assert _at(frame, "LBO", "Departure Time") == "05:02:00"
        # The terminus has no departure, so its arrival stands in.
        assert _at(frame, "STP", "Departure Time") == "06:30:00"

    def test_ends_from_calls(self, emr_file: pathlib.Path) -> None:
        frame = load_operator("emr", emr_file)
        assert set(frame["Origin CRS"]) == {"DBY"}
        assert set(frame["Destination CRS"]) == {"STP"}

    def test_boardings_are_derived(self, frame: pd.DataFrame) -> None:
        assert float(_at(frame, "LBO", "Mean Passengers On")) == pytest.approx(30.0)
        assert float(_at(frame, "STP", "Mean Passengers Off")) == pytest.approx(80.0)


class TestGwr:
    @pytest.fixture
    def frame(self, gwr_folder: pathlib.Path) -> pd.DataFrame:
        return load_operator("gwr", gwr_folder)

    def test_period_from_file_name(self, frame: pd.DataFrame) -> None:
        assert set(frame["RSP Period"]) == {"2704"}

    def test_dates_are_read_day_first(self, frame: pd.DataFrame) -> None:
        assert frame["Period Start Date"].min() == pd.Timestamp("2025-07-06")

    def test_one_train_shares_one_count_window(self, frame: pd.DataFrame) -> None:
        assert frame["Period Start Date"].nunique() == 1
        assert frame["Period End Date"].max() == pd.Timestamp("2025-08-02")

    def test_capacity_adds_first_class(self, frame: pd.DataFrame) -> None:
        assert float(_at(frame, "PAD", "Mean Capacity")) == pytest.approx(550.0)


class TestGtr:
    @pytest.fixture
    def frame(self, gtr_folder: pathlib.Path) -> pd.DataFrame:
        return load_operator("gtr", gtr_folder)

    def test_runs_averaged_per_call(self, frame: pd.DataFrame) -> None:
        assert len(frame) == 3
        assert float(_at(frame, "GTW", "Mean Load on Departure")) == pytest.approx(230.0)

    def test_boardings_per_run_averaged(self, frame: pd.DataFrame) -> None:
        # Week 1 gains 100 at Gatwick and week 2 gains 140.
        assert float(_at(frame, "GTW", "Mean Passengers On")) == pytest.approx(120.0)

    def test_arrival_count_not_a_load(self, frame: pd.DataFrame) -> None:
        assert pd.isna(_at(frame, "BDM", "Mean Load on Departure"))
        assert float(_at(frame, "BDM", "Mean Passengers Off")) == pytest.approx(230.0)

    def test_sample_size_counts_the_runs(self, frame: pd.DataFrame) -> None:
        assert float(_at(frame, "BTN", "Sample Size")) == 2

    def test_window_spans_runs(self, frame: pd.DataFrame) -> None:
        assert frame["Period Start Date"].min() == pd.Timestamp("2024-04-01")
        assert frame["Period End Date"].max() == pd.Timestamp("2024-04-08")


class TestSwr:
    @pytest.fixture
    def frame(self, swr_folder: pathlib.Path) -> pd.DataFrame:
        return load_operator("swr", swr_folder, tiploc_to_crs=_SWR_TIPLOCS)

    def test_two_row_header(self, frame: pd.DataFrame) -> None:
        assert float(_at(frame, "WAT", "Mean Capacity")) == pytest.approx(440.0)
        assert float(_at(frame, "CLJ", "Mean Load on Departure")) == pytest.approx(260.0)

    def test_unmapped_timing_points_are_dropped(self, frame: pd.DataFrame) -> None:
        assert list(frame["Departure from CRS"]) == ["WAT", "CLJ", "WOK"]

    def test_departure_falls_back_to_arrival(self, frame: pd.DataFrame) -> None:
        assert _at(frame, "CLJ", "Departure Time") == "06:08:00"
        assert _at(frame, "WOK", "Departure Time") == "06:30:00"

    def test_dates_from_notes(self, frame: pd.DataFrame) -> None:
        assert frame["Period Start Date"].iloc[0] == pd.Timestamp("2024-12-15")
        assert frame["Period End Date"].iloc[0] == pd.Timestamp("2025-01-11")

    def test_day_abbreviations_are_expanded(self, frame: pd.DataFrame) -> None:
        assert set(frame["Service Week Day"]) == {"Monday"}


class TestRailPeriodDates:
    def test_period_ten(self) -> None:
        # The Notes sheet of the SWR fixture agrees with this.
        assert _rail_period_dates("RY25 P10") == (
            pd.Timestamp("2024-12-15"),
            pd.Timestamp("2025-01-11"),
        )

    def test_period_one(self) -> None:
        start, end = _rail_period_dates("RY25 P01")
        assert start == pd.Timestamp("2024-04-01")
        assert end == pd.Timestamp("2024-05-04")

    def test_period_thirteen(self) -> None:
        assert _rail_period_dates("RY25 P13")[1] == pd.Timestamp("2025-03-31")

    def test_unrecognised_code(self) -> None:
        assert _rail_period_dates("Period 4") == (None, None)


class TestAveragePassengerCounts:
    @pytest.fixture
    def frame(self, southeastern_folder: pathlib.Path) -> pd.DataFrame:
        return load_operator("southeastern", southeastern_folder)

    def test_both_layouts_are_read(self, frame: pd.DataFrame) -> None:
        assert set(frame["Departure from CRS"]) == {"SEV", "CHX", "DVP", "STP"}

    def test_republished_window_read_once(self, frame: pd.DataFrame) -> None:
        assert len(frame) == 4

    def test_uncapped_preferred(self, frame: pd.DataFrame) -> None:
        assert float(_at(frame, "SEV", "Mean Load on Departure")) == pytest.approx(450.0)

    def test_old_layout_headcode(self, frame: pd.DataFrame) -> None:
        assert _at(frame, "SEV", "Headcode") == "0700SEVCHX"

    def test_arrival_count_voided(self, frame: pd.DataFrame) -> None:
        assert pd.isna(_at(frame, "STP", "Mean Load on Departure"))
        assert _at(frame, "STP", "Departure Time") == "09:10:00"

    def test_greater_anglia_shares_the_reader(self, southeastern_folder: pathlib.Path) -> None:
        frame = load_operator("greater_anglia", southeastern_folder)
        assert set(frame["Train Operator"]) == {"greater_anglia"}


class TestSelectCountWindows:
    def test_railway_period_beats_month(self, southeastern_folder: pathlib.Path) -> None:
        kept = select_count_windows(sorted(southeastern_folder.glob("*.csv")))
        assert [file.name[:20] for file in kept] == [
            "20240602 to 20240629",
            "20250817 to 20250913",
        ]

    def test_files_without_a_window_are_kept(self, tmp_path: pathlib.Path) -> None:
        loose = tmp_path / "counts.csv"
        loose.write_text("a\n1\n")
        assert select_count_windows([loose]) == [loose]


class TestDeriveOnOff:
    def _service(self, loads: list[float | None], destination: str = "CCC") -> pd.DataFrame:
        stations = ["AAA", "BBB", "CCC"][: len(loads)]
        return pd.DataFrame(
            {
                "Train Operator": ["op"] * len(loads),
                "Unique ID": ["1"] * len(loads),
                "Headcode": ["1A01"] * len(loads),
                "Service Week Day": ["Monday"] * len(loads),
                "Origin CRS": ["AAA"] * len(loads),
                "Destination CRS": [destination] * len(loads),
                "Origin Departure Time": ["07:00:00"] * len(loads),
                "Period Start Date": [pd.Timestamp("2025-01-01")] * len(loads),
                "Call Sequence": list(range(1, len(loads) + 1)),
                "Departure from CRS": stations,
                "Mean Load on Departure": loads,
                "Mean Passengers On": [None] * len(loads),
                "Mean Passengers Off": [None] * len(loads),
            }
        )

    def test_boardings_come_from_rising_load(self) -> None:
        result = derive_on_off(self._service([100.0, 150.0, 0.0]))
        assert list(result["Mean Passengers On"]) == pytest.approx([100.0, 50.0, 0.0])

    def test_alightings_come_from_falling_load(self) -> None:
        result = derive_on_off(self._service([100.0, 60.0, 0.0]))
        assert list(result["Mean Passengers Off"]) == pytest.approx([0.0, 40.0, 60.0])

    def test_terminus_empties(self) -> None:
        result = derive_on_off(self._service([100.0, 150.0, 0.0]))
        assert float(result["Mean Passengers Off"].iloc[-1]) == pytest.approx(150.0)

    def test_derived_boardings_are_net_not_gross(self) -> None:
        # 100 on and 100 off at the same stop leaves the load unchanged,
        # so the derived figure is 0, a lower bound on the true
        # boardings.
        result = derive_on_off(self._service([100.0, 100.0, 0.0]))
        assert float(result["Mean Passengers On"].iloc[1]) == pytest.approx(0.0)

    def test_published_boardings_kept(self) -> None:
        frame = self._service([100.0, 150.0, 0.0])
        frame.loc[1, "Mean Passengers On"] = 999.0
        result = derive_on_off(frame)
        assert float(result.loc[1, "Mean Passengers On"]) == pytest.approx(999.0)

    def test_gap_in_counts(self) -> None:
        # The middle call was not counted; comparing against the last
        # known load rather than a missing one keeps the arithmetic
        # honest.
        result = derive_on_off(self._service([100.0, None, 0.0]))
        assert result["Mean Passengers On"].notna().any()


class TestEncodings:
    def test_windows_1252_files_are_readable(self, unreadable_file: pathlib.Path) -> None:
        from rail_loading.readers import _read_csv_any_encoding

        frame = _read_csv_any_encoding(unreadable_file)
        assert "Op\xe9rateur" in set(frame["TOC"])


class TestLoadOperator:
    def test_duplicate_files(self, northern_folder: pathlib.Path) -> None:
        source = northern_folder / "Loading_Data_2025_P05.csv"
        (northern_folder / "Loading_Data_2025_P05_copy.csv").write_bytes(source.read_bytes())
        assert len(load_operator("northern", northern_folder)) == 3

    def test_missing_path(self, tmp_path: pathlib.Path) -> None:
        frame = load_operator("tpe", tmp_path / "nope.csv")
        assert frame.empty
        assert list(frame.columns) == CANONICAL_COLUMNS

    def test_unknown_operator_message(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(ValueError, match="northern"):
            load_operator("nosuchtoc", tmp_path)

    def test_caching_round_trips(
        self, northern_folder: pathlib.Path, tmp_path: pathlib.Path
    ) -> None:
        cache = tmp_path / "cache"
        first = load_operator("northern", northern_folder, cache_dir=cache)
        assert list(cache.glob("*.pkl"))
        second = load_operator("northern", northern_folder, cache_dir=cache)
        pd.testing.assert_frame_equal(first, second)

    def test_impossible_loads_are_voided(self, tmp_path: pathlib.Path) -> None:
        # No British train carries 99,999 people; that is a keying
        # error.
        path = tmp_path / "awc-loading-data.csv"
        pd.DataFrame(
            {
                "Train Operator": ["Avanti West Coast"],
                "Unique ID": ["A1"],
                "Train ID (Headcode)": ["1H03"],
                "Count on Arrival or Departure": ["Departure"],
                "Day of Count": ["Wednesday"],
                "Origin of Service (CRS)": ["EUS"],
                "Destination of Service (CRS)": ["MAN"],
                "Timetabled Departure from Origin": ["09:00"],
                "Timetabled Arrival Time at Destination Station": ["11:05"],
                "Station CRS": ["EUS"],
                "Station Scheduled Arrival Time": ["09:00"],
                "First Class Seated Capacity ": ["100"],
                "Standard Seated Capacity ": ["400"],
                "Maximum Standing Capacity": ["100"],
                "Number of Counts": ["10"],
                "Type of Count": ["Manual"],
                "Total Load (Mean)": ["99999"],
            }
        ).to_csv(path, index=False)

        frame = load_operator("awc", path)
        assert frame["Mean Load on Departure"].isna().all()
