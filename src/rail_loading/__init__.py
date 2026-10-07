"""Read every UK train operator's loading data into one schema.

>>> from rail_loading import READERS
>>> "northern" in READERS
True
"""

from __future__ import annotations

from rail_loading.departures import (
    ARRIVAL_TIME_OPERATORS,
    RESOLVED_COLUMN,
    SOURCE_COLUMN,
    add_resolved_departures,
    load_timetable_departures,
    resolve_departure_times,
)
from rail_loading.readers import (
    CANONICAL_COLUMNS,
    READERS,
    derive_on_off,
    load_operator,
)

__all__ = [
    "ARRIVAL_TIME_OPERATORS",
    "CANONICAL_COLUMNS",
    "READERS",
    "RESOLVED_COLUMN",
    "SOURCE_COLUMN",
    "add_resolved_departures",
    "derive_on_off",
    "load_operator",
    "load_timetable_departures",
    "resolve_departure_times",
]

__version__ = "0.1.0"
