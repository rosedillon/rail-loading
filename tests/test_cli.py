from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from rail_loading import cli
from rail_loading.cli import build_parser

if TYPE_CHECKING:
    import pathlib

    from _pytest.capture import CaptureFixture

SMOKE_ARGS = ["operators"]


class TestOperators:
    def test_lists_the_readers(self, capsys: CaptureFixture[str]) -> None:
        assert cli.main(["operators"]) == 0
        out = capsys.readouterr().out
        assert "northern" in out
        assert "CrossCountry" in out

    def test_json(self, capsys: CaptureFixture[str]) -> None:
        assert cli.main(["operators", "--json"]) == 0
        assert "northern" in json.loads(capsys.readouterr().out)


class TestSchema:
    def test_lists_the_canonical_columns(self, capsys: CaptureFixture[str]) -> None:
        assert cli.main(["schema"]) == 0
        assert "Mean Load on Departure" in capsys.readouterr().out

    def test_json(self, capsys: CaptureFixture[str]) -> None:
        assert cli.main(["schema", "--json"]) == 0
        assert "load_units" in json.loads(capsys.readouterr().out)


class TestRead:
    def test_summarises_an_operator(
        self, northern_folder: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        assert cli.main(["read", "northern", str(northern_folder)]) == 0
        out = capsys.readouterr().out
        assert "rows" in out
        assert "LDS" in out

    def test_json_summary(
        self, northern_folder: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        assert cli.main(["read", "northern", str(northern_folder), "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["operator"] == "northern"
        assert payload["rows"] == 3
        assert payload["load_units"] == ["passengers"]

    def test_csv_export(
        self,
        northern_folder: pathlib.Path,
        tmp_path: pathlib.Path,
        capsys: CaptureFixture[str],
    ) -> None:
        target = tmp_path / "out.csv"
        assert cli.main(["read", "northern", str(northern_folder), "--csv", str(target)]) == 0
        assert "Mean Load on Departure" in target.read_text()

    def test_calls_add_the_resolved_departure(
        self,
        northern_folder: pathlib.Path,
        tmp_path: pathlib.Path,
        capsys: CaptureFixture[str],
    ) -> None:
        calls = tmp_path / "timetable_calls.csv"
        calls.write_text(
            "atoc_code,stp_indicator,is_public_call,signalling_id,svc_origin_crs,"
            "svc_dest_crs,svc_origin_dep,crs,public_departure\n"
            "NT,P,True,1A01,LDS,YRK,0700,CFD,0706\n"
        )
        args = ["read", "northern", str(northern_folder), "--calls", str(calls), "--json"]
        assert cli.main(args) == 0
        sources = json.loads(capsys.readouterr().out)["departure_sources"]
        assert "timetable_exact 33.3%" in sources

    def test_unknown_operator_message(
        self, tmp_path: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        assert cli.main(["read", "nosuchtoc", str(tmp_path)]) == 1
        assert "northern" in capsys.readouterr().err

    def test_missing_path_is_an_error(
        self, tmp_path: pathlib.Path, capsys: CaptureFixture[str]
    ) -> None:
        assert cli.main(["read", "northern", str(tmp_path / "nope")]) == 1
        assert "not found" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "verbose"),
    [(SMOKE_ARGS, False), (["-v", *SMOKE_ARGS], True), ([*SMOKE_ARGS, "-v"], True)],
)
def test_verbose_flag(argv: list[str], verbose: bool) -> None:
    assert build_parser().parse_args(argv).verbose is verbose


def test_no_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main([])
    assert exit_info.value.code != 0
