"""Fail-closed, installed-package compatibility probe for the optional sqlite-vec experiment."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

SQLITE_VEC_PIN = "0.1.9"
_PROBE_CONTRACT = "mdrack.sqlite-vec-compatibility-probe-v1"


class ProbeStatus(StrEnum):
    """Overall probe decision."""

    PASS = "pass"
    FAIL = "fail"


class ProbeOutcomeStatus(StrEnum):
    """One bounded compatibility outcome."""

    PASS = "pass"
    FAIL = "fail"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class ProbeOutcome:
    """One privacy-safe, JSON-serializable probe observation."""

    name: str
    status: ProbeOutcomeStatus
    facts: Mapping[str, bool | float | int | str]
    failure_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "name": self.name,
            "status": self.status.value,
            "facts": dict(sorted(self.facts.items())),
        }
        if self.failure_code is not None:
            result["failure_code"] = self.failure_code
        return result


@dataclass(frozen=True)
class ProbeReport:
    """Machine-readable result which is intentionally safe for public evidence."""

    status: ProbeStatus
    observed_version: str | None
    python_version: str
    sqlite_version: str
    outcomes: tuple[ProbeOutcome, ...]

    @property
    def failure_codes(self) -> tuple[str, ...]:
        return tuple(
            outcome.failure_code
            for outcome in self.outcomes
            if outcome.status is ProbeOutcomeStatus.FAIL and outcome.failure_code is not None
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "contract": _PROBE_CONTRACT,
            "status": self.status.value,
            "extension": {
                "distribution": "sqlite-vec",
                "expected_version": SQLITE_VEC_PIN,
                "observed_version": self.observed_version,
            },
            "environment": {
                "platform": sys.platform,
                "python": self.python_version,
                "sqlite": self.sqlite_version,
            },
            "decision": {
                "action": "keep_builtin",
                "backend_id": "builtin-exact-v1",
                "promotion_allowed": self.status is ProbeStatus.PASS,
                "failure_codes": list(self.failure_codes),
            },
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), allow_nan=False, separators=(",", ":"), sort_keys=True)


class _SQLiteVecModule(Protocol):
    __version__: str

    def load(self, connection: sqlite3.Connection) -> None: ...

    def serialize_float32(self, vector: list[float]) -> bytes: ...


class SQLiteVecCompatibilityProbe:
    """Exercise only the native extension contract; construction has no I/O."""

    def __init__(self, *, expected_version: str = SQLITE_VEC_PIN) -> None:
        if expected_version != SQLITE_VEC_PIN:
            raise ValueError("the compatibility probe only accepts its exact tested sqlite-vec pin")
        self._expected_version = expected_version

    def run(self) -> ProbeReport:
        """Run a synthetic local-only probe and fail closed on unsupported promotion."""
        module, observed_version, setup = self._load_module()
        if module is None:
            outcomes = (setup, *self._not_run_outcomes())
            return self._report(observed_version, outcomes)

        outcomes = (
            setup,
            self._run_case("float32_dimensions", lambda: self._probe_dimensions(module)),
            self._run_case("metrics", lambda: self._probe_metrics(module)),
            self._run_case("metadata_scope", lambda: self._probe_metadata_scope(module)),
            self._run_case("delete", lambda: self._probe_delete(module)),
            self._run_case("transactions", lambda: self._probe_transactions(module)),
            self._run_case("extensionless_reopen", lambda: self._probe_extensionless_reopen(module)),
            self._run_case("tie_boundary", lambda: self._probe_tie_boundary(module)),
            self._platform_outcome(),
        )
        return self._report(observed_version, outcomes)

    def _report(self, observed_version: str | None, outcomes: tuple[ProbeOutcome, ...]) -> ProbeReport:
        failed = any(item.status is ProbeOutcomeStatus.FAIL for item in outcomes)
        return ProbeReport(
            status=ProbeStatus.FAIL if failed else ProbeStatus.PASS,
            observed_version=observed_version,
            python_version=platform.python_version(),
            sqlite_version=sqlite3.sqlite_version,
            outcomes=outcomes,
        )

    def _load_module(self) -> tuple[_SQLiteVecModule | None, str | None, ProbeOutcome]:
        try:
            import sqlite_vec  # type: ignore[import-untyped]

            module = cast(_SQLiteVecModule, sqlite_vec)
            observed_version = importlib.metadata.version("sqlite-vec")
            if observed_version != self._expected_version or module.__version__ != self._expected_version:
                return (
                    None,
                    observed_version,
                    ProbeOutcome(
                        "installed_extension",
                        ProbeOutcomeStatus.FAIL,
                        {"exact_pin": False},
                        "unexpected_extension_version",
                    ),
                )
            connection = self._connection(module)
            try:
                vec_version = connection.execute("SELECT vec_version()").fetchone()[0]
            finally:
                connection.close()
            if vec_version != f"v{self._expected_version}":
                return (
                    None,
                    observed_version,
                    ProbeOutcome(
                        "installed_extension",
                        ProbeOutcomeStatus.FAIL,
                        {"exact_pin": True, "version_function_matches": False},
                        "extension_version_mismatch",
                    ),
                )
            return (
                module,
                observed_version,
                ProbeOutcome(
                    "installed_extension",
                    ProbeOutcomeStatus.PASS,
                    {"exact_pin": True, "version_function_matches": True},
                ),
            )
        except Exception as error:
            return (
                None,
                None,
                ProbeOutcome(
                    "installed_extension",
                    ProbeOutcomeStatus.FAIL,
                    {"extension_loaded": False},
                    self._failure_code(error),
                ),
            )

    def _connection(self, module: _SQLiteVecModule) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:")
        try:
            connection.enable_load_extension(True)
            module.load(connection)
        finally:
            connection.enable_load_extension(False)
        return connection

    def _run_case(
        self,
        name: str,
        action: Callable[[], ProbeOutcome],
    ) -> ProbeOutcome:
        try:
            outcome = action()
        except Exception as error:
            return ProbeOutcome(name, ProbeOutcomeStatus.FAIL, {"completed": False}, self._failure_code(error))
        if outcome.name != name:
            raise RuntimeError("probe case returned the wrong outcome name")
        return outcome

    def _probe_dimensions(self, module: _SQLiteVecModule) -> ProbeOutcome:
        connection = self._connection(module)
        try:
            facts: dict[str, bool | float | int | str] = {}
            for dimensions in (384, 1024):
                table = f"vec_dimensions_{dimensions}"
                vector = [1.0, *([0.0] * (dimensions - 1))]
                connection.execute(f"CREATE VIRTUAL TABLE {table} USING vec0(embedding float[{dimensions}])")
                connection.execute(
                    f"INSERT INTO {table}(rowid, embedding) VALUES(?, ?)",
                    (1, module.serialize_float32(vector)),
                )
                rows = connection.execute(
                    f"SELECT rowid, distance FROM {table} WHERE embedding MATCH ? AND k = ?",
                    (module.serialize_float32(vector), 1),
                ).fetchall()
                facts[f"f32_{dimensions}_result_count"] = len(rows)
                facts[f"f32_{dimensions}_distance_zero"] = len(rows) == 1 and float(rows[0][1]) == 0.0
            passed = all(facts.values())
            return ProbeOutcome(
                "float32_dimensions",
                ProbeOutcomeStatus.PASS if passed else ProbeOutcomeStatus.FAIL,
                facts,
                None if passed else "float32_dimension_unsupported",
            )
        finally:
            connection.close()

    def _probe_metrics(self, module: _SQLiteVecModule) -> ProbeOutcome:
        connection = self._connection(module)
        try:
            facts: dict[str, bool | float | int | str] = {}
            for metric, expected_distance in (("l2", 2**0.5), ("cosine", 1.0)):
                table = f"vec_metric_{metric}"
                connection.execute(
                    f"CREATE VIRTUAL TABLE {table} USING vec0(embedding float[2] distance_metric={metric})"
                )
                connection.execute(
                    f"INSERT INTO {table}(rowid, embedding) VALUES(?, ?)", (1, module.serialize_float32([1.0, 0.0]))
                )
                connection.execute(
                    f"INSERT INTO {table}(rowid, embedding) VALUES(?, ?)", (2, module.serialize_float32([0.0, 1.0]))
                )
                rows = connection.execute(
                    f"SELECT rowid, distance FROM {table} WHERE embedding MATCH ? AND k = ?",
                    (module.serialize_float32([1.0, 0.0]), 2),
                ).fetchall()
                facts[f"{metric}_ordered"] = [int(row[0]) for row in rows] == [1, 2]
                facts[f"{metric}_distance"] = len(rows) == 2 and abs(float(rows[1][1]) - expected_distance) < 0.000001
            passed = all(facts.values())
            return ProbeOutcome(
                "metrics",
                ProbeOutcomeStatus.PASS if passed else ProbeOutcomeStatus.FAIL,
                facts,
                None if passed else "metric_contract_failed",
            )
        finally:
            connection.close()

    def _probe_metadata_scope(self, module: _SQLiteVecModule) -> ProbeOutcome:
        connection = self._connection(module)
        try:
            connection.execute("CREATE VIRTUAL TABLE vec_metadata USING vec0(embedding float[2], scope TEXT)")
            connection.execute(
                "INSERT INTO vec_metadata(rowid, embedding, scope) VALUES(?, ?, ?)",
                (1, module.serialize_float32([1.0, 0.0]), "outside"),
            )
            connection.execute(
                "INSERT INTO vec_metadata(rowid, embedding, scope) VALUES(?, ?, ?)",
                (2, module.serialize_float32([0.8, 0.0]), "inside"),
            )
            rows = connection.execute(
                "SELECT rowid, distance FROM vec_metadata WHERE embedding MATCH ? AND k = ? AND scope = ?",
                (module.serialize_float32([1.0, 0.0]), 1, "inside"),
            ).fetchall()
            passed = [int(row[0]) for row in rows] == [2]
            return ProbeOutcome(
                "metadata_scope",
                ProbeOutcomeStatus.PASS if passed else ProbeOutcomeStatus.FAIL,
                {"filtered_result_count": len(rows), "scope_precedes_limit": passed},
                None if passed else "metadata_scope_postfiltered",
            )
        finally:
            connection.close()

    def _probe_delete(self, module: _SQLiteVecModule) -> ProbeOutcome:
        connection = self._connection(module)
        try:
            connection.execute("CREATE VIRTUAL TABLE vec_delete USING vec0(embedding float[2])")
            connection.execute(
                "INSERT INTO vec_delete(rowid, embedding) VALUES(?, ?)", (1, module.serialize_float32([1.0, 0.0]))
            )
            connection.execute("DELETE FROM vec_delete WHERE rowid = ?", (1,))
            rows = connection.execute(
                "SELECT rowid FROM vec_delete WHERE embedding MATCH ? AND k = ?",
                (module.serialize_float32([1.0, 0.0]), 10),
            ).fetchall()
            passed = not rows
            return ProbeOutcome(
                "delete",
                ProbeOutcomeStatus.PASS if passed else ProbeOutcomeStatus.FAIL,
                {"remaining_result_count": len(rows)},
                None if passed else "delete_failed",
            )
        finally:
            connection.close()

    def _probe_transactions(self, module: _SQLiteVecModule) -> ProbeOutcome:
        connection = self._connection(module)
        try:
            connection.execute("CREATE VIRTUAL TABLE vec_transaction USING vec0(embedding float[2])")
            connection.execute(
                "INSERT INTO vec_transaction(rowid, embedding) VALUES(?, ?)",
                (1, module.serialize_float32([1.0, 0.0])),
            )
            connection.commit()
            connection.execute("BEGIN")
            connection.execute(
                "INSERT INTO vec_transaction(rowid, embedding) VALUES(?, ?)",
                (2, module.serialize_float32([0.0, 1.0])),
            )
            connection.rollback()
            insert_rows = [
                int(row[0]) for row in connection.execute("SELECT rowid FROM vec_transaction ORDER BY rowid")
            ]
            connection.execute("BEGIN")
            connection.execute("DELETE FROM vec_transaction WHERE rowid = ?", (1,))
            connection.rollback()
            delete_rows = [
                int(row[0]) for row in connection.execute("SELECT rowid FROM vec_transaction ORDER BY rowid")
            ]
            passed = insert_rows == [1] and delete_rows == [1]
            return ProbeOutcome(
                "transactions",
                ProbeOutcomeStatus.PASS if passed else ProbeOutcomeStatus.FAIL,
                {"delete_rollback_preserved": delete_rows == [1], "insert_rollback_preserved": insert_rows == [1]},
                None if passed else "transaction_rollback_failed",
            )
        finally:
            connection.close()

    def _probe_extensionless_reopen(self, module: _SQLiteVecModule) -> ProbeOutcome:
        with tempfile.TemporaryDirectory(prefix="mdrack-sqlite-vec-probe-") as directory:
            database = Path(directory) / "probe.db"
            connection = sqlite3.connect(database)
            try:
                try:
                    connection.enable_load_extension(True)
                    module.load(connection)
                finally:
                    connection.enable_load_extension(False)
                connection.execute("CREATE VIRTUAL TABLE vec_reopen USING vec0(embedding float[2])")
                connection.execute(
                    "INSERT INTO vec_reopen(rowid, embedding) VALUES(?, ?)",
                    (1, module.serialize_float32([1.0, 0.0])),
                )
                connection.commit()
            finally:
                connection.close()
            reopened = sqlite3.connect(database)
            try:
                schema_open = reopened.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'vec_reopen'"
                ).fetchone() == ("vec_reopen",)
                try:
                    reopened.execute("SELECT count(*) FROM vec_reopen").fetchone()
                except sqlite3.DatabaseError:
                    query_requires_extension = True
                else:
                    query_requires_extension = False
            finally:
                reopened.close()
        passed = schema_open and query_requires_extension
        return ProbeOutcome(
            "extensionless_reopen",
            ProbeOutcomeStatus.PASS if passed else ProbeOutcomeStatus.FAIL,
            {"query_requires_extension": query_requires_extension, "schema_open_without_extension": schema_open},
            None if passed else "extensionless_reopen_contract_failed",
        )

    def _probe_tie_boundary(self, module: _SQLiteVecModule) -> ProbeOutcome:
        connection = self._connection(module)
        try:
            connection.execute("CREATE VIRTUAL TABLE vec_ties USING vec0(embedding float[2] distance_metric=l2)")
            for rowid, vector in ((10, [1.0, 0.0]), (20, [1.0, 0.0]), (30, [1.0, 0.0001])):
                connection.execute(
                    "INSERT INTO vec_ties(rowid, embedding) VALUES(?, ?)",
                    (rowid, module.serialize_float32(vector)),
                )
            query = module.serialize_float32([1.0, 0.0])
            k1 = connection.execute(
                "SELECT rowid, distance FROM vec_ties WHERE embedding MATCH ? AND k = ?",
                (query, 1),
            ).fetchall()
            k2 = connection.execute(
                "SELECT rowid, distance FROM vec_ties WHERE embedding MATCH ? AND k = ?",
                (query, 2),
            ).fetchall()
            constrained_k1 = connection.execute(
                "SELECT rowid, distance FROM vec_ties WHERE embedding MATCH ? AND k = ? AND distance <= ?",
                (query, 1, 0.0),
            ).fetchall()
            near = connection.execute(
                "SELECT rowid, distance FROM vec_ties WHERE embedding MATCH ? AND k = ?",
                (query, 3),
            ).fetchall()
        finally:
            connection.close()

        k1_rowid = int(k1[0][0]) if len(k1) == 1 else -1
        k2_rowids = [int(row[0]) for row in k2]
        near_distance = float(near[2][1]) if len(near) == 3 else -1.0
        full_tie_cohort = set(k2_rowids) == {10, 20}
        truncated_tie_cohort = len(k1) == 1 and k1_rowid in {10, 20} and len(constrained_k1) == 1
        near_tie_is_distinct = near_distance > 0.0
        if not full_tie_cohort or not truncated_tie_cohort or not near_tie_is_distinct:
            return ProbeOutcome(
                "tie_boundary",
                ProbeOutcomeStatus.FAIL,
                {
                    "candidate_limit_one_count": len(k1),
                    "near_tie_distinct": near_tie_is_distinct,
                    "tie_cohort_count_at_k2": len(k2),
                },
                "tie_probe_inconclusive",
            )
        return ProbeOutcome(
            "tie_boundary",
            ProbeOutcomeStatus.FAIL,
            {
                "candidate_limit_one_count": len(k1),
                "candidate_limit_one_rowid": k1_rowid,
                "distance_constraint_count": len(constrained_k1),
                "near_tie_distinct": near_tie_is_distinct,
                "tie_cohort_count_at_k2": len(k2),
            },
            "tie_boundary_requires_full_scan",
        )

    @staticmethod
    def _platform_outcome() -> ProbeOutcome:
        if sys.platform.startswith("linux"):
            return ProbeOutcome("platform_matrix", ProbeOutcomeStatus.PASS, {"linux_current": True})
        return ProbeOutcome(
            "platform_matrix",
            ProbeOutcomeStatus.NOT_RUN,
            {"linux_current": False},
            "linux_probe_not_run",
        )

    @staticmethod
    def _not_run_outcomes() -> tuple[ProbeOutcome, ...]:
        return tuple(
            ProbeOutcome(name, ProbeOutcomeStatus.NOT_RUN, {"completed": False}, "extension_not_loaded")
            for name in (
                "float32_dimensions",
                "metrics",
                "metadata_scope",
                "delete",
                "transactions",
                "extensionless_reopen",
                "tie_boundary",
                "platform_matrix",
            )
        )

    @staticmethod
    def _failure_code(error: Exception) -> str:
        if isinstance(error, sqlite3.OperationalError):
            return "sqlite_operational_error"
        if isinstance(error, sqlite3.DatabaseError):
            return "sqlite_database_error"
        if isinstance(error, ModuleNotFoundError):
            return "extension_unavailable"
        if isinstance(error, ValueError):
            return "invalid_probe_operation"
        return "probe_execution_failed"


def run_probe() -> ProbeReport:
    """Run the exact-pin probe without importing the extension during package import."""
    return SQLiteVecCompatibilityProbe().run()


def main() -> int:
    """Print only the JSON report; nonzero means the promotion gate failed."""
    report = run_probe()
    print(report.to_json())
    return 0 if report.status is ProbeStatus.PASS else 2
