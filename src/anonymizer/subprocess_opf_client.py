"""PrivacyFilterClient implementation that drives an out-of-process OPF worker.

Owning OPF in its own subprocess is the only reliable way to fully
return the ~3 GB of model weights to the operating system when the user
disables it — torch/glibc on CPU don't release those allocations even
after ``del`` + ``gc.collect()``. ``OPFManager`` (in the API layer)
spawns this client when the user enables the toggle and stops it when
they disable, at which point the kernel reclaims everything cleanly.

Communication is line-delimited JSON over stdin/stdout to
``scripts/opf_worker.py``. See that file for the protocol.
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from pathlib import Path

from .client import PrivacyFilterClient
from .models import DetectedSpan

logger = logging.getLogger(__name__)

# Worst-case wait for the worker's first "ready" line. The model load
# itself takes ~30s on macOS CPU; bump the budget for cold disks.
_DEFAULT_READY_TIMEOUT = 180.0
# Per-call timeout for ``detect()``. Long blocks on CPU push this hard.
_DEFAULT_DETECT_TIMEOUT = 300.0


class OPFWorkerError(RuntimeError):
    """Raised when the worker subprocess fails to start, dies, or replies with an error."""


def _default_worker_script() -> Path:
    """Locate ``scripts/opf_worker.py`` relative to the repo root.

    Resolves from this file's location: ``src/anonymizer/...`` →
    ``../../scripts/opf_worker.py``.
    """
    return Path(__file__).resolve().parents[2] / "scripts" / "opf_worker.py"


class SubprocessOPFClient(PrivacyFilterClient):
    """Talks to ``scripts/opf_worker.py`` via JSON over stdio.

    Lifecycle:

        client = SubprocessOPFClient()
        client.start()           # blocks until "ready"
        spans = client.detect(text)
        client.stop()            # graceful shutdown, then OS reclaims memory
    """

    def __init__(
        self,
        *,
        python_bin: str | None = None,
        worker_script: Path | None = None,
        use_mock: bool = False,
        ready_timeout: float = _DEFAULT_READY_TIMEOUT,
        detect_timeout: float = _DEFAULT_DETECT_TIMEOUT,
    ) -> None:
        self._python_bin = python_bin or sys.executable
        self._worker_script = worker_script or _default_worker_script()
        self._use_mock = use_mock
        self._ready_timeout = ready_timeout
        self._detect_timeout = detect_timeout

        self._proc: subprocess.Popen | None = None
        # Serialise stdin writes / stdout reads — only one detect() at a time.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return  # already running

        cmd = [self._python_bin, "-u", str(self._worker_script)]
        if self._use_mock:
            cmd.append("--mock")

        logger.info("starting OPF worker subprocess use_mock=%s", self._use_mock)
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # Wait for "ready" — error/exit means the worker failed to boot.
        try:
            self._wait_for_event("ready", timeout=self._ready_timeout)
        except OPFWorkerError:
            self._kill_proc()
            raise
        logger.info("OPF worker ready pid=%s", self._proc.pid)

    def stop(self, *, timeout: float = 10.0) -> None:
        if self._proc is None:
            return
        with self._lock:
            if self._proc.poll() is None:
                try:
                    self._send({"action": "shutdown"})
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "OPF worker pid=%s did not exit within %.1fs — terminating",
                        self._proc.pid,
                        timeout,
                    )
                    self._kill_proc()
            self._proc = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ------------------------------------------------------------------
    # PrivacyFilterClient
    # ------------------------------------------------------------------

    def detect(self, text: str) -> list[DetectedSpan]:
        if not self.is_running():
            raise OPFWorkerError("worker is not running — call start() first")

        with self._lock:
            self._send({"action": "detect", "text": text})
            response = self._read_one(timeout=self._detect_timeout)

        if "spans" not in response:
            raise OPFWorkerError(
                f"unexpected response: {response.get('event', 'unknown')!r} "
                f"message={response.get('message', '')!r}"
            )

        spans: list[DetectedSpan] = []
        for raw in response["spans"]:
            spans.append(
                DetectedSpan(
                    start=raw["start"],
                    end=raw["end"],
                    entity_type=raw["entity_type"],
                    confidence=raw.get("confidence"),
                    text_hash=raw.get("text_hash"),
                    source=raw.get("source"),
                )
            )
        return spans

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _send(self, payload: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise OPFWorkerError("worker stdin is not available")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def _read_one(self, *, timeout: float) -> dict:
        """Read one JSON line from worker stdout, with a soft timeout."""
        if self._proc is None or self._proc.stdout is None:
            raise OPFWorkerError("worker stdout is not available")

        # We rely on the OS pipe + the worker writing reasonably promptly.
        # If we ever need a hard timeout we'd switch to selectors; for now
        # a process-level liveness check after the read covers the common
        # failure modes (worker died, pipe closed).
        result: dict = {}
        line_holder: list[str] = []

        def reader() -> None:
            try:
                line_holder.append(self._proc.stdout.readline())  # type: ignore[union-attr]
            except Exception:
                line_holder.append("")

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive():
            # Worker stuck — treat as fatal so the manager can recycle it.
            self._kill_proc()
            raise OPFWorkerError(f"worker did not respond within {timeout:.1f}s")

        line = (line_holder[0] or "").strip()
        if not line:
            # Empty line == EOF == worker died.
            stderr_tail = self._drain_stderr_tail()
            raise OPFWorkerError(
                f"worker exited unexpectedly (rc={self._proc.poll()}). "
                f"stderr_tail={stderr_tail!r}"
            )
        try:
            result = json.loads(line)
        except json.JSONDecodeError as exc:
            raise OPFWorkerError(f"worker returned invalid JSON: {line!r}") from exc

        if result.get("event") == "error":
            raise OPFWorkerError(result.get("message", "unknown error"))
        return result

    def _wait_for_event(self, event_name: str, *, timeout: float) -> None:
        # Worker emits "loading" then "ready" — drain anything until we see
        # the event we want (or an error).
        deadline = timeout
        while True:
            response = self._read_one(timeout=deadline)
            if response.get("event") == event_name:
                return
            # else: loop, but reset the budget very loosely. We don't expect
            # more than 2-3 events before "ready".

    def _drain_stderr_tail(self, max_chars: int = 500) -> str:
        if self._proc is None or self._proc.stderr is None:
            return ""
        try:
            data = self._proc.stderr.read() or ""
        except Exception:
            return ""
        return data[-max_chars:]

    def _kill_proc(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.kill()
        except Exception:
            pass
        try:
            self._proc.wait(timeout=2.0)
        except Exception:
            pass
        self._proc = None
