"""Experimental sqlite-vec compatibility probe; no production backend is provided."""

from mdrack_sqlite_vec.probe import (
    SQLITE_VEC_PIN,
    ProbeOutcome,
    ProbeOutcomeStatus,
    ProbeReport,
    ProbeStatus,
    SQLiteVecCompatibilityProbe,
    run_probe,
)

__all__ = [
    "SQLITE_VEC_PIN",
    "ProbeOutcome",
    "ProbeOutcomeStatus",
    "ProbeReport",
    "ProbeStatus",
    "SQLiteVecCompatibilityProbe",
    "run_probe",
]
