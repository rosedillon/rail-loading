# rail-loading

Read the passenger loading data that British train operators publish
through the Rail Data Marketplace, and get one table back whatever the
operator.

Nine operators publish counts of how many people were on each train as
it left each station. No two of them use the same layout. Column names
differ, days are `Monday` in one file and `1-Sun` in another, times are
strings or Excel serials, some files are CSV and some XLSX with a
two-row header, and only two operators report boardings at all. This
package has a reader for each layout and returns every one of them in
the same nineteen columns.

```python
from pathlib import Path
from rail_loading import load_operator

northern = load_operator("northern", Path("northern-loading-data"))
northern.groupby("Departure from CRS")["Mean Passengers On"].sum()
```

## Install

Python 3.11 or later.

```sh
pip install git+https://github.com/rosedillon/rail-loading
```

or, from a clone, `pip install .`. It depends on pandas, openpyxl (for
the operators that publish XLSX) and tqdm.

## Getting the data

The data is free but licensed. Register at the
[Rail Data Marketplace](https://raildata.org.uk), search for each
operator's "passenger loading" product, accept its licence and download
the files. Nothing from those extracts is included here, and the
`.gitignore` keeps them out of a clone.

Point `load_operator` (or the CLI) at the file or folder each operator
gives you:

| Key | Operator | Point at | Boardings | Departure time |
|---|---|---|---|---|
| `northern` | Northern | folder of `Loading_Data_*.csv` | published | origin only |
| `tpe` | TransPennine Express | one CSV | published | published |
| `awc` | Avanti West Coast | one CSV | derived | arrival only |
| `emr` | East Midlands Railway | one CSV | derived | published |
| `gwr` | Great Western Railway | folder of CSV or XLSX, one per period | derived | published |
| `gtr` | Govia Thameslink Railway | folder of XLSX, one per period | derived | arrival only |
| `swr` | South Western Railway | folder of XLSX, one per period | derived | published |
| `southeastern` | Southeastern | folder of "Average Passenger Counts" CSVs | derived | from Aug 2025 |
| `greater_anglia` | Greater Anglia | folder of "Average Passenger Counts" CSVs | derived | from Aug 2025 |

Files in a folder that do not match the expected pattern are ignored, so
Northern's formation tables can sit alongside its counts.

SWR identifies every location by TIPLOC rather than CRS code, so its
reader needs a TIPLOC to CRS mapping. Calls at a TIPLOC that is not in
the mapping are dropped, with a warning naming them; on SWR's data these
are mostly timing points that are not stations. EMR gives only the
train's origin and destination as TIPLOCs. Without a mapping those two
are taken from the first and last counted call instead, which is wrong
for a train whose first or last stop was not counted. A mapping can be
built from Network Rail's timetable with
[cif-timetable](https://github.com/rosedillon/cif-timetable):

```python
import json
from pathlib import Path
from cif_timetable import load_tiploc_crs_map

mapping = load_tiploc_crs_map(
    Path("CIF_ALL_FULL_DAILY_toc-full.json"), station_reference=Path("stations.csv")
)
Path("tiplocs.json").write_text(json.dumps(mapping))
```

CrossCountry also publishes on the Marketplace, but its feed is a 0-100
busyness band for each train, which cannot be turned into a passenger
count, so there is no reader for it.

## What comes back

One row per train, day of the week, station and count period:

| Column | Meaning |
|---|---|
| `Train Operator` | the key from the table above |
| `Unique ID` | the train's schedule UID, where the operator publishes one |
| `Headcode` | the four-character signalling ID |
| `Service Week Day` | `Monday` to `Sunday` |
| `Origin CRS`, `Destination CRS` | where the train starts and ends |
| `Origin Departure Time` | `HH:MM:SS` |
| `Departure from CRS` | the station the count was taken at |
| `Departure Time` | `HH:MM:SS`, as published, or empty (see below) |
| `Call Sequence` | the call's position along the train's route |
| `RSP Period` | the operator's own label for the count period |
| `Period Start Date`, `Period End Date` | the count window |
| `Sample Size` | how many counts the mean is taken over |
| `Mean Capacity` | seats, standard plus first class; Northern publishes its own figure |
| `Mean Load on Departure` | passengers on board as the train left |
| `Mean Passengers On`, `Mean Passengers Off` | boardings and alightings |
| `load_units` | what the load is measured in; every reader returns `passengers` |

`rail-loading schema` prints the list.

### Derived boardings are a lower bound

Only Northern and TPE count people getting on and off. For the other
seven operators boardings and alightings are worked out from the change
in load between one call and the next (`derive_on_off`). If 100 people
get off at a station and another 100 get on, the load does not change
and the derived figure is zero for both. At a busy interchange the
derived number can be well short of the real one. Published boardings
are never overwritten.

The terminus is taken to empty the train. Where a call in the middle was
not counted, the next counted call is compared against the last load
that was known, so a gap does not turn into a burst of invented
boardings.

### Cleaning

`load_operator` also

- drops rows with no station or day,
- voids loads above 3,000 passengers, which no British train carries
  and which are keying errors in the source,
- drops rows that appear identically in more than one file, and
- for Southeastern and Greater Anglia, skips a file whose count window
  is already covered by another. Both operators publish some windows
  twice, as a railway period and as a calendar month, and in 2024 as a
  capped and an uncapped copy. Uncapped files are preferred, then
  railway periods.

Where the capped and uncapped loads are both in one file, the uncapped
one is used, because the capped figure stops at the train's capacity.

Reading the XLSX operators is slow. Pass `cache_dir` and the cleaned
table is pickled there and reused until a source file changes.

## Departure times

Most operators publish the timetabled departure of the call each count
was taken at. Some do not:

- Northern publishes only the train's departure from its origin. Timing
  a Northern count by that puts it a median 29 minutes early, and about
  15% of calls land in the wrong time period.
- Avanti and GTR publish the scheduled arrival at each call, not the
  departure.
- Southeastern and Greater Anglia files from before August 2025 have no
  time at each call, and no real headcode either.

`add_resolved_departures` fills these in from the Network Rail
timetable. It needs the calls table that
[cif-timetable](https://github.com/rosedillon/cif-timetable) writes
(`timetable_calls.csv`), and matches each count to its schedule through
a ladder of keys, loosest last:

| Source | Matched on |
|---|---|
| `published` | the operator's own departure time; always used when present |
| `timetable_exact` | operator, headcode, origin, destination, origin time, station |
| `timetable_no_destination` | the same without the destination |
| `timetable_no_headcode` | the same without the headcode, for Southeastern and Greater Anglia rows that have only a made-up one |
| `timetable_nearest_origin` | the nearest origin time within 5 minutes |
| `published_arrival` | Avanti and GTR only: their published arrival |
| `origin_fallback` | the train's origin departure |
| `unresolved` | nothing to go on |

A key that leads to two different departure times is never used. Only
permanent schedules are read, so a short-term overlay cannot change a
match. The original `Departure Time` column is kept, and two columns are
added: `Resolved Departure Time` and `departure_time_source`, the rung
each row matched on. The second lets you report the mix or drop rungs
you do not trust:

```python
from rail_loading import add_resolved_departures

northern = add_resolved_departures(northern, Path("timetable/timetable_calls.csv"))
northern = northern[northern["departure_time_source"] != "origin_fallback"]
```

How many rows match depends on how close the timetable is to the count
dates. Northern's 2025 counts resolved against a May 2026 timetable
matched 61% exactly and 11% on the nearest origin time, and 27% fell
back to the origin, mostly because trains were retimed or withdrawn in
between. A timetable from
the same period as the counts does better.

## Command line

```console
$ rail-loading operators
$ rail-loading schema
$ rail-loading read northern northern-loading-data --head 3
operator             northern
rows                 3
stations             3
load units           passengers
rows with boardings  3
period start         2025-07-20
period end           2025-08-16

Train Operator Headcode Service Week Day Departure from CRS Departure Time  Mean Load on Departure  Mean Passengers On load_units
      northern     1A01           Monday                LDS           <NA>                     100                 100 passengers
      northern     1A01           Monday                CFD           <NA>                     130                  40 passengers
      northern     1A01           Monday                YRK           <NA>                       0                   0 passengers
```

(The figures are from the test fixtures, not real counts.)

Options for `read`:

| Option | |
|---|---|
| `--tiploc-map PATH` | JSON TIPLOC to CRS mapping, needed for `emr` and `swr` |
| `--calls PATH` | a cif-timetable calls CSV; adds the resolved departure time |
| `--csv PATH` | write every row to CSV instead of summarising |
| `--cache DIR` | reuse the cleaned table between runs |
| `--json` | print the summary as JSON |
| `--head N` | preview rows to print (default 10) |
| `-v` | log progress |

## Python API

| Name | |
|---|---|
| `load_operator(operator, path, tiploc_to_crs=None, cache_dir=None)` | read and clean one operator |
| `READERS` | operator key to reader function |
| `CANONICAL_COLUMNS` | the columns every reader returns, in order |
| `derive_on_off(frame)` | fill missing boardings and alightings from load changes |
| `add_resolved_departures(frame, calls_path)` | add the resolved departure columns |
| `resolve_departure_times(frame, calls_path)` | the same two columns, returned on their own |
| `load_timetable_departures(calls_path, operators)` | the timetable calls used for matching |
| `ARRIVAL_TIME_OPERATORS` | operators whose published time is an arrival |

## Development

```sh
git clone https://github.com/rosedillon/rail-loading
cd rail-loading
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
ruff check . && ruff format --check . && mypy src
```

The real files cannot be committed, so the tests build small synthetic
files that copy each operator's layout (the column names, header rows,
sheet names and value formats they publish) and fill it with invented
counts. A change to the columns an operator publishes shows up as a
failing test here.

## Licence

GPL-3.0. See [LICENSE](LICENSE).

Not affiliated with the Rail Data Marketplace or any train operator. The
loading data itself is licensed separately by each operator.
