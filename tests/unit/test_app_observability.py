"""Closed app schemas, actual bytes, and unchanged logging host policy."""
from __future__ import annotations

import json
import logging
from enum import StrEnum
from io import StringIO
from pathlib import Path

import pytest

import mdrack_core.observability as core_events
from mdrack._observability import AppEvent, AppEventLogger
from mdrack_core.observability import SafeEvent, emit_event
from tests.privacy_oracle import SentinelMatrix


def test_app_file_bytes_keep_counts_unknown_and_zero_without_private_values(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("app-byte-proof")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    root_handlers = tuple(logging.getLogger().handlers)
    try:
        emitter = AppEventLogger(logger)
        for _ in range(3):
            emitter.info("image.ingest.completed", extra={
                "representation_count": 1, "unit_count": 1, "vector_count": 0, "byte_size": None,
            })
            emitter.warning("image.search.degraded", extra={"reason": "embedding_provider_error"})
            emitter.warning("image.search.degraded", extra={"reason": "PRIVATE_QUERY https://PRIVATE_HOST/path"})
        handler.flush()
        output = path.read_text(encoding="utf-8")
        assert tuple(logger.handlers) == (handler,)
        assert tuple(logging.getLogger().handlers) == root_handlers
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert output
    assert len(output.splitlines()) == 9
    assert '"vector_count":0' in output
    assert '"byte_size":null' in output
    assert '"reason":"embedding_provider_error"' in output
    assert "PRIVATE_QUERY" not in output
    assert "PRIVATE_HOST" not in output
    assert "[redacted]" in output
    assert "\"byte_size\":0" not in output


def test_all_privacy_sentinels_are_absent_from_nonempty_flushed_app_bytes(tmp_path: Path) -> None:
    path = tmp_path / "privacy.log"
    logger = logging.getLogger("app-sentinel-proof")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.FileHandler(path, encoding="utf-8")
    logger.addHandler(handler)
    sentinels = (*SentinelMatrix.complete().all_tokens, "PRIVATE_QUERY_SENTINEL")
    try:
        emitter = AppEventLogger(logger)
        emitter.info("image.ingest.completed", extra={"unit_count": 3, "vector_count": 0})
        for sentinel in sentinels:
            emitter.error("config.load.failed", extra={
                "config_ref": sentinel, "reason": sentinel, "status": sentinel,
            })
        handler.flush()
        output = path.read_text(encoding="utf-8")
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert output
    assert '"unit_count":3' in output
    assert '"vector_count":0' in output
    for sentinel in sentinels:
        assert sentinel not in output


def test_app_schema_errors_and_foreign_enums_do_not_bypass_closed_labels() -> None:
    class Foreign(StrEnum):
        LABEL = "embedding_provider_error"

    with pytest.raises(ValueError, match="unknown app event"):
        AppEvent("PRIVATE_QUERY", {})
    with pytest.raises(ValueError, match="outside app event schema"):
        AppEvent("image.ingest.completed", {"query": "PRIVATE_QUERY"})
    with pytest.raises(ValueError, match="numeric"):
        AppEvent("image.ingest.completed", {"byte_size": []})
    with pytest.raises(ValueError, match="finite"):
        AppEvent("image.ingest.completed", {"byte_size": float("nan")})
    event = AppEvent("image.search.degraded", {"reason": Foreign.LABEL})
    assert json.loads(event.to_log_message().split(" ", 1)[1]) == {"reason": "[redacted]"}


@pytest.mark.parametrize("where,error_type", [
    (where, error_type)
    for where in ("logger_filter", "handler_filter", "formatter", "emit")
    for error_type in (ValueError, RuntimeError, OSError)
    if not (where == "emit" and error_type is OSError)
])
def test_programming_failures_are_not_sink_losses(where: str, error_type: type[Exception]) -> None:
    class BrokenFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            raise error_type("synthetic programming defect")

    class BrokenFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            raise error_type("synthetic programming defect")

    class BrokenHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise error_type("synthetic programming defect")

    logger = logging.getLogger("programming-control")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler: logging.Handler = BrokenHandler() if where == "emit" else logging.StreamHandler()
    if where == "formatter":
        handler.setFormatter(BrokenFormatter())
    if where == "handler_filter":
        handler.addFilter(BrokenFilter())
    logger_filter = BrokenFilter()
    if where == "logger_filter":
        logger.addFilter(logger_filter)
    logger.addHandler(handler)
    before = core_events._diagnostic_loss_count
    try:
        with pytest.raises(error_type, match="programming defect"):
            emit_event(logger, SafeEvent("core.search.completed", {"result_count": 0}))
        assert core_events._diagnostic_loss_count == before
    finally:
        logger.removeHandler(handler)
        logger.removeFilter(logger_filter)


def test_closed_standard_stream_is_io_loss_not_programming_failure(capsys: pytest.CaptureFixture[str]) -> None:
    stream = StringIO()
    stream.close()
    handler = logging.StreamHandler(stream)
    logger = logging.getLogger("closed-stream-control")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    before = core_events._diagnostic_loss_count
    try:
        emit_event(logger, SafeEvent("core.search.completed", {"result_count": 0}))
        assert core_events._diagnostic_loss_count == min(before + 1, 2**31 - 1)
        assert capsys.readouterr().err == ""
    finally:
        logger.removeHandler(handler)


def test_standard_last_resort_keeps_readonly_stream_and_root_policy(capsys: pytest.CaptureFixture[str]) -> None:
    logger = logging.Logger("unconfigured-library", logging.WARNING)
    logger.propagate = False
    root_handlers = tuple(logging.getLogger().handlers)
    last_resort = logging.lastResort
    assert type(last_resort).__name__ == "_StderrHandler"
    for _ in range(3):
        AppEventLogger(logger).warning("image.search.degraded", extra={"reason": "embedding_provider_error"})
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count('"reason":"embedding_provider_error"') == 3
    assert logger.handlers == []
    assert logging.lastResort is last_resort
    assert tuple(logging.getLogger().handlers) == root_handlers


@pytest.mark.parametrize("spoof_standard", [False, True])
def test_unknown_stream_closed_attribute_does_not_hide_programming_error(spoof_standard: bool) -> None:
    class UnknownStream:
        closed = True

        def write(self, text: str) -> None:
            raise ValueError("synthetic programming defect")

        def flush(self) -> None:
            pass

    class SpoofedStandardStream(StringIO):
        closed = True

        def write(self, text: str) -> int:
            raise ValueError("synthetic programming defect")

    stream = SpoofedStandardStream() if spoof_standard else UnknownStream()
    handler = logging.StreamHandler(stream)
    logger = logging.Logger("unknown-stream-control", logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    before = core_events._diagnostic_loss_count
    try:
        with pytest.raises(ValueError, match="programming defect"):
            emit_event(logger, SafeEvent("core.search.completed", {"result_count": 0}))
        assert core_events._diagnostic_loss_count == before
    finally:
        logger.removeHandler(handler)
        handler.close()
        if isinstance(stream, StringIO):
            stream.close()


def test_known_closed_text_file_remains_io_loss(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    stream = (tmp_path / "closed.log").open("w", encoding="utf-8")
    stream.close()
    handler = logging.StreamHandler(stream)
    logger = logging.Logger("closed-file-control", logging.INFO)
    logger.addHandler(handler)
    before = core_events._diagnostic_loss_count
    try:
        emit_event(logger, SafeEvent("core.search.completed", {"result_count": 0}))
        assert core_events._diagnostic_loss_count == min(before + 1, 2**31 - 1)
        assert capsys.readouterr().err == ""
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_delayed_file_transfers_only_new_stream_and_preserves_lock_and_lifetime(tmp_path: Path) -> None:
    handler = logging.FileHandler(tmp_path / "delayed.log", delay=True, encoding="utf-8")
    logger = logging.Logger("delayed-file-control", logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)

    class Formatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            assert handler.lock._is_owned()
            return record.getMessage()

    handler.setFormatter(Formatter())
    original_methods = (handler.emit, handler.handleError, handler.format)
    try:
        assert handler.stream is None
        emit_event(logger, SafeEvent("core.search.completed", {"result_count": 0}))
        stream = handler.stream
        assert stream is not None and not stream.closed
        emit_event(logger, SafeEvent("core.search.completed", {"result_count": 1}))
        assert handler.stream is stream
        assert (handler.emit, handler.handleError, handler.format) == original_methods
        handler.flush()
        output = (tmp_path / "delayed.log").read_text(encoding="utf-8")
        assert '"result_count":0' in output and '"result_count":1' in output
    finally:
        logger.removeHandler(handler)
        handler.close()
    assert stream.closed
    assert not handler.lock._is_owned()


def test_loss_signal_saturates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core_events, "_diagnostic_loss_count", 2**31 - 1)
    core_events._note_sink_loss()
    assert core_events._diagnostic_loss_count == 2**31 - 1


def test_io_loss_signal_is_bounded_nonrecursive_and_other_handlers_still_receive(tmp_path: Path) -> None:
    class Broken(logging.Handler):
        calls = 0

        def emit(self, record: logging.LogRecord) -> None:
            self.calls += 1
            raise OSError("PRIVATE_EXCEPTION")

    broken = Broken()
    good = logging.FileHandler(tmp_path / "surviving.log", encoding="utf-8")
    logger = logging.getLogger("loss-control")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(broken)
    logger.addHandler(good)
    before = core_events._diagnostic_loss_count
    try:
        for _ in range(3):
            emit_event(logger, SafeEvent("core.search.completed", {"result_count": 2}))
        good.flush()
        output = (tmp_path / "surviving.log").read_text(encoding="utf-8")
        assert broken.calls == 3
        assert core_events._diagnostic_loss_count == min(before + 3, 2**31 - 1)
        assert output.count('"result_count":2') == 3
        assert "PRIVATE_EXCEPTION" not in output
    finally:
        logger.removeHandler(broken)
        logger.removeHandler(good)
        good.close()
