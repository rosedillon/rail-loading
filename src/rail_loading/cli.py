"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys
from typing import TYPE_CHECKING

import pandas as pd

from rail_loading.departures import RESOLVED_COLUMN, SOURCE_COLUMN, add_resolved_departures
from rail_loading.readers import CANONICAL_COLUMNS, READERS, load_operator

if TYPE_CHECKING:
    from collections.abc import Sequence


def _cmd_operators(args: argparse.Namespace) -> int:
    if args.json:
        json.dump(sorted(READERS), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    print("Operators with a reader:")
    for name in sorted(READERS):
        print(f"  {name}")
    print("\nCrossCountry is not readable: it publishes a 0-100 busyness band, not counts.")
    return 0


def _cmd_read(args: argparse.Namespace) -> int:
    if args.operator not in READERS:
        print(
            f"error: no reader for {args.operator!r}. Known: {', '.join(sorted(READERS))}",
            file=sys.stderr,
        )
        return 1
    if not args.path.exists():
        print(f"error: {args.path} not found", file=sys.stderr)
        return 1

    mapping: dict[str, str] = {}
    if args.tiploc_map:
        mapping = json.loads(args.tiploc_map.read_text())

    frame = load_operator(args.operator, args.path, tiploc_to_crs=mapping, cache_dir=args.cache)
    if args.calls and not frame.empty:
        frame = add_resolved_departures(frame, args.calls)

    if args.csv:
        frame.to_csv(args.csv, index=False)
        print(f"Wrote {len(frame):,} rows to {args.csv}", file=sys.stderr)
        return 0

    if frame.empty:
        print("No rows read.", file=sys.stderr)
        return 0

    units = frame["load_units"].dropna().unique()
    derived = int(frame["Mean Passengers On"].notna().sum())
    summary = {
        "operator": args.operator,
        "rows": len(frame),
        "stations": int(frame["Departure from CRS"].nunique()),
        "load_units": sorted(str(unit) for unit in units),
        "rows_with_boardings": derived,
        "period_start": _as_date(frame["Period Start Date"].min()),
        "period_end": _as_date(frame["Period End Date"].max()),
    }
    columns = list(_PREVIEW_COLUMNS)
    if SOURCE_COLUMN in frame.columns:
        shares = frame[SOURCE_COLUMN].value_counts(normalize=True)
        summary["departure_sources"] = [f"{label} {share:.1%}" for label, share in shares.items()]
        columns.insert(columns.index("Departure Time") + 1, RESOLVED_COLUMN)

    if args.json:
        json.dump(summary, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0

    width = max(len(key) for key in summary)
    for key, value in summary.items():
        shown = ", ".join(value) if isinstance(value, list) else value
        print(f"{key.replace('_', ' '):<{width}}  {shown}")
    print()
    print(frame[columns].head(args.head).to_string(index=False))
    return 0


_PREVIEW_COLUMNS = [
    "Train Operator",
    "Headcode",
    "Service Week Day",
    "Departure from CRS",
    "Departure Time",
    "Mean Load on Departure",
    "Mean Passengers On",
    "load_units",
]


def _as_date(value: object) -> str | None:
    # NaT is not a Timestamp, so an empty column comes back as None.
    return str(value.date()) if isinstance(value, pd.Timestamp) else None


def _cmd_schema(args: argparse.Namespace) -> int:
    if args.json:
        json.dump(CANONICAL_COLUMNS, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    print("Canonical columns, in order:")
    for column in CANONICAL_COLUMNS:
        print(f"  {column}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rail-loading",
        description=(
            "Read a train operator's published passenger loading data into one "
            "canonical schema, whatever layout they publish it in."
        ),
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log progress")
    subparsers = parser.add_subparsers(dest="command", required=True)

    operators = subparsers.add_parser(
        "operators",
        help="List the operators with a reader.",
        description="List the operators this package can read.",
    )
    operators.add_argument("--json", action="store_true", help="emit JSON")
    operators.set_defaults(func=_cmd_operators)

    schema = subparsers.add_parser(
        "schema",
        help="Show the canonical column list.",
        description="The columns every reader returns, in order.",
    )
    schema.add_argument("--json", action="store_true", help="emit JSON")
    schema.set_defaults(func=_cmd_schema)

    read = subparsers.add_parser(
        "read",
        help="Read one operator's data.",
        description="Read an operator's loading data and summarise or export it.",
    )
    read.add_argument("operator", help=f"one of: {', '.join(sorted(READERS))}")
    read.add_argument("path", type=pathlib.Path, help="the operator's file or folder")
    read.add_argument("--csv", metavar="PATH", help="write the standardised rows to CSV")
    read.add_argument(
        "--tiploc-map",
        type=pathlib.Path,
        help="JSON TIPLOC->CRS map, needed by the operators that publish TIPLOCs (emr, swr)",
    )
    read.add_argument(
        "--calls",
        type=pathlib.Path,
        help="a cif-timetable calls CSV; fills in each count's timetabled departure",
    )
    read.add_argument(
        "--cache", type=pathlib.Path, help="directory to cache the standardised frame in"
    )
    read.add_argument("--head", type=int, default=10, help="preview rows to print")
    read.add_argument("--json", action="store_true", help="emit the summary as JSON")
    read.set_defaults(func=_cmd_read)

    # -v also works after the subcommand.
    for subcommand in subparsers.choices.values():
        subcommand.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            default=argparse.SUPPRESS,
            help="log progress",
        )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    return int(args.func(args))
