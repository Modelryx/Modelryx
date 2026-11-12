from __future__ import annotations

import subprocess

import httpx
from modelryx.services.processing import classify_processing_failure


def test_missing_reference_is_terminal_and_actionable() -> None:
    failure = classify_processing_failure(RuntimeError("Missing reference: parts/bolt.FCStd"))

    assert failure.code == "MISSING_REFERENCE"
    assert failure.retryable is False
    assert "relink" in failure.suggestion.casefold()


def test_resource_limit_is_terminal() -> None:
    failure = classify_processing_failure(
        RuntimeError("converter package exceeds the configured size limit")
    )

    assert failure.code == "RESOURCE_LIMIT"
    assert failure.retryable is False


def test_timeout_and_transport_failures_are_retryable() -> None:
    timeout = classify_processing_failure(subprocess.TimeoutExpired(["freecad"], 10))
    transport = classify_processing_failure(httpx.ConnectError("connection refused"))

    assert (timeout.code, timeout.retryable) == ("TIMEOUT", True)
    assert (transport.code, transport.retryable) == ("WORKER_UNAVAILABLE", True)


def test_unsupported_workbench_is_terminal() -> None:
    failure = classify_processing_failure(RuntimeError("Unsupported Assembly4 workbench object"))

    assert failure.code == "UNSUPPORTED_CAD_FEATURE"
    assert failure.retryable is False


def test_native_failure_signatures_are_specific_and_actionable() -> None:
    cases = [
        ("Permission denied opening /workspace/model.FCStd", "SOURCE_ACCESS_DENIED", False),
        ("Encrypted document is password protected", "PROTECTED_DOCUMENT", False),
        ("ImportError: DLL load failed while importing Part", "NATIVE_DEPENDENCY_MISSING", False),
        ("FreeCAD recompute failed: broken shape", "INVALID_MODEL_STATE", False),
        ("MemoryError: cannot allocate memory", "OUT_OF_MEMORY", True),
        ("FreeCAD terminated with segmentation fault", "NATIVE_PROCESS_CRASH", True),
    ]

    for message, code, retryable in cases:
        failure = classify_processing_failure(RuntimeError(message))
        assert (failure.code, failure.retryable) == (code, retryable)
        assert failure.suggestion
