"""Runtime toggle for the OPF model.

Owns a ``SubprocessOPFClient`` that's spawned on enable and torn down on
disable. Exposes a ``ToggledBaseClient`` whose ``detect()`` routes to
the live OPF subprocess (when enabled) or to the local
``MockPrivacyFilterClient`` (when disabled / unavailable).

State machine::

    OFF  ──enable()──▶  LOADING  ──worker emits 'ready'──▶  ON
    ON   ──disable()─▶  OFF      (subprocess shutdown, OS reclaims memory)
    *    ──disable()─▶  OFF      (idempotent)

In-flight jobs lease the current base via ``acquire()`` / ``release()``
so toggling OFF mid-job doesn't pull the rug — the subprocess stays
alive until refcount drops to zero (then ``disable()`` finishes).

This module never imports ``opf`` or ``torch`` directly — those live in
the subprocess. Importing it is safe even on a Catalina machine that
can't install the ML stack.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from anonymizer.client import MockPrivacyFilterClient, PrivacyFilterClient
from anonymizer.models import DetectedSpan
from anonymizer.regex_fallback_client import RegexFallbackClient
from anonymizer.subprocess_opf_client import OPFWorkerError, SubprocessOPFClient

logger = logging.getLogger(__name__)


@dataclass
class OPFStatus:
    available: bool
    enabled: bool
    loading: bool
    error: str | None = None
    in_flight_jobs: int = 0
    # Auto-disable settings: ``idle_timeout_seconds == 0`` means the
    # watchdog is off and the subprocess stays up until the user
    # explicitly disables it. ``seconds_until_auto_disable`` is ``None``
    # when not enabled or when the timer is off.
    idle_timeout_seconds: int = 0
    seconds_until_auto_disable: int | None = None


class ToggledBaseClient(PrivacyFilterClient):
    """Routes ``detect()`` to the OPF subprocess (if enabled) or to the mock.

    Bound once at app boot and then injected as the ``base`` of the
    augmented client. The augmented wrapper (case-normalisation + BR
    regex augmentations) is unchanged by the toggle — flipping OPF
    off only swaps the model side.
    """

    def __init__(self, manager: "OPFManager") -> None:
        self._manager = manager

    def detect(self, text: str) -> list[DetectedSpan]:
        target = self._manager.current_base()
        # Touch the idle clock whenever a real OPF call goes through —
        # the watchdog only auto-disables after N seconds of *no*
        # subprocess activity (and zero outstanding leases).
        if isinstance(target, SubprocessOPFClient):
            self._manager.touch()
        return target.detect(text)


class OPFManager:
    """Owns the OPF subprocess and the on/off state.

    Thread-safe. Designed for low-frequency toggles (single user clicking
    a button), so a single mutex around the state is plenty.
    """

    def __init__(
        self,
        *,
        available: bool,
        fallback_client: PrivacyFilterClient | None = None,
        use_mock_worker: bool = False,
        idle_timeout_seconds: int = 300,
    ) -> None:
        self._available = available
        # ``_fallback`` is the base used when OPF is OFF. Defaults to
        # ``RegexFallbackClient`` (email-only) — anything noisier (loose
        # name heuristics) creates phantom detections once the augmented
        # case-normalisation runs over caps-heavy Brazilian docs.
        # In tests (``use_mock_worker=True``) the fallback is the regex
        # ``MockPrivacyFilterClient`` so existing test fixtures keep
        # detecting names without spinning up the OPF subprocess.
        if fallback_client is not None:
            self._fallback: PrivacyFilterClient = fallback_client
        elif use_mock_worker:
            self._fallback = MockPrivacyFilterClient()
        else:
            self._fallback = RegexFallbackClient()
        self._use_mock_worker = use_mock_worker

        self._lock = threading.RLock()
        self._enabled = False
        self._loading = False
        self._error: str | None = None
        self._client: SubprocessOPFClient | None = None
        self._refcount = 0
        # Notified when refcount reaches zero so disable() can wait.
        self._zero_refs = threading.Condition(self._lock)

        # Idle watchdog state. Disable by setting timeout <= 0.
        self._idle_timeout = idle_timeout_seconds
        self._last_used_at = time.monotonic()
        self._stop_event = threading.Event()
        self._watchdog: threading.Thread | None = None
        if self._idle_timeout > 0:
            self._start_watchdog()

    # ------------------------------------------------------------------
    # Read-only state
    # ------------------------------------------------------------------

    def status(self) -> OPFStatus:
        with self._lock:
            seconds_left: int | None = None
            if (
                self._enabled
                and self._idle_timeout > 0
                and self._refcount == 0
            ):
                elapsed = time.monotonic() - self._last_used_at
                seconds_left = max(0, int(self._idle_timeout - elapsed))
            return OPFStatus(
                available=self._available,
                enabled=self._enabled,
                loading=self._loading,
                error=self._error,
                in_flight_jobs=self._refcount,
                idle_timeout_seconds=self._idle_timeout,
                seconds_until_auto_disable=seconds_left,
            )

    @property
    def available(self) -> bool:
        return self._available

    def current_base(self) -> PrivacyFilterClient:
        """Return whichever client is active right now (no refcount).

        Used by the ``ToggledBaseClient`` for one-off detect() calls
        where we accept that a concurrent disable() may turn the next
        call into a mock call. Long-running flows (job processing) must
        use ``acquire()`` / ``release()`` for stable semantics.
        """
        with self._lock:
            if self._enabled and self._client is not None and self._client.is_running():
                return self._client
            return self._fallback

    # ------------------------------------------------------------------
    # Toggle
    # ------------------------------------------------------------------

    def enable(self) -> None:
        """Spawn the OPF subprocess and wait for it to be ready.

        Idempotent: returns immediately if already enabled. Raises
        ``RuntimeError`` if OPF isn't available on this server (mock
        mode), or ``OPFWorkerError`` if the worker fails to boot.
        """
        if not self._available:
            raise RuntimeError("OPF is not available on this server (mock mode).")

        with self._lock:
            if self._enabled:
                return
            if self._loading:
                # Another caller is already booting it — wait until they're done.
                while self._loading:
                    self._zero_refs.wait(timeout=1.0)
                if self._enabled:
                    return
                raise OPFWorkerError(self._error or "worker failed to load")

            self._loading = True
            self._error = None

        # Heavy work outside the lock so /status keeps responding.
        client = SubprocessOPFClient(use_mock=self._use_mock_worker)
        try:
            client.start()
        except Exception as exc:
            with self._lock:
                self._loading = False
                self._error = f"{type(exc).__name__}: {exc}"
                self._zero_refs.notify_all()
            logger.error("OPF enable failed: %s", exc)
            raise

        with self._lock:
            self._client = client
            self._enabled = True
            self._loading = False
            self._last_used_at = time.monotonic()
            self._zero_refs.notify_all()
        logger.info("OPF enabled")

    def disable(self, *, wait_for_jobs: bool = True, timeout: float = 60.0) -> None:
        """Stop the OPF subprocess and release the model memory.

        If jobs are in flight (``refcount > 0``) and ``wait_for_jobs`` is
        true, blocks until they release before tearing down. Idempotent.
        """
        with self._lock:
            if not self._enabled and self._client is None:
                return
            self._enabled = False  # block new acquires immediately
            if wait_for_jobs:
                deadline_remaining = timeout
                while self._refcount > 0 and deadline_remaining > 0:
                    if not self._zero_refs.wait(timeout=min(deadline_remaining, 1.0)):
                        deadline_remaining -= 1.0
                if self._refcount > 0:
                    logger.warning(
                        "disable() proceeding with %d in-flight jobs after %.0fs timeout",
                        self._refcount,
                        timeout,
                    )
            client_to_stop = self._client
            self._client = None

        if client_to_stop is not None:
            try:
                client_to_stop.stop()
            except Exception as exc:
                logger.warning("error stopping OPF worker: %s", exc)
        logger.info("OPF disabled")

    # ------------------------------------------------------------------
    # Lease / refcount — used by JobService.process_job() so toggling
    # OFF mid-job doesn't change behaviour for that job.
    # ------------------------------------------------------------------

    def acquire(self) -> PrivacyFilterClient:
        """Lease the *current* base client and pin it for this caller.

        Returns whichever client is active (subprocess OPF or mock).
        While at least one acquire is outstanding, ``disable()`` blocks
        until ``release()`` is called the same number of times.
        """
        with self._lock:
            if self._enabled and self._client is not None and self._client.is_running():
                self._refcount += 1
                self._last_used_at = time.monotonic()
                return self._client
            return self._fallback

    def release(self, leased: PrivacyFilterClient) -> None:
        """Release a lease taken via ``acquire()``.

        Mock leases don't decrement (they were never refcounted). Pass
        the exact instance returned by ``acquire()``.
        """
        with self._lock:
            if leased is self._fallback:
                return
            if self._refcount <= 0:
                logger.error("release() called with refcount already 0")
                return
            self._refcount -= 1
            # Treat lease end as activity — the idle clock should
            # measure time since the last interaction in either
            # direction, not just the last acquire.
            self._last_used_at = time.monotonic()
            if self._refcount == 0:
                self._zero_refs.notify_all()

    def touch(self) -> None:
        """Reset the idle clock. Called on every detect() that hits the
        OPF subprocess via the ToggledBaseClient."""
        with self._lock:
            self._last_used_at = time.monotonic()

    # ------------------------------------------------------------------
    # Helper for the comparison endpoint — auto-load if not yet loaded.
    # ------------------------------------------------------------------

    def ensure_loaded(self) -> None:
        """Make sure OPF is enabled, loading it if needed.

        Used by the diagnostic detector-comparison flow which always
        wants the real model on the OPF side. After this call returns
        successfully, ``enabled`` is True and the toggle in the UI
        flips to ON.
        """
        with self._lock:
            if self._enabled:
                return
        self.enable()

    # ------------------------------------------------------------------
    # Idle watchdog — auto-disable after N seconds of no activity, so
    # users who flip the toggle ON and forget about it don't keep ~3 GB
    # of model weights resident indefinitely.
    # ------------------------------------------------------------------

    def _start_watchdog(self) -> None:
        # Poll roughly six times per timeout window, but never faster
        # than once every 5 s and never slower than once every 30 s.
        # Keeps the check responsive without spinning the CPU.
        poll = max(5.0, min(30.0, self._idle_timeout / 6))

        def loop() -> None:
            while not self._stop_event.wait(poll):
                try:
                    self._check_idle_once()
                except Exception:
                    logger.exception("OPF idle watchdog tick failed")

        t = threading.Thread(
            target=loop, daemon=True, name="opf-idle-watchdog"
        )
        t.start()
        self._watchdog = t

    def _check_idle_once(self) -> bool:
        """One pass of the watchdog. Returns True if it disabled OPF.

        Exposed (vs. inline in the loop) so tests can drive the timer
        deterministically without sleeping for the polling interval.
        """
        with self._lock:
            if self._idle_timeout <= 0:
                # Watchdog disabled — never auto-shut OPF down.
                return False
            if not self._enabled:
                return False
            if self._refcount > 0:
                # In-flight jobs are still using OPF — don't kill it.
                return False
            elapsed = time.monotonic() - self._last_used_at
            if elapsed < self._idle_timeout:
                return False
            logger.info(
                "OPF auto-disable: idle for %.0fs (timeout=%ds)",
                elapsed,
                self._idle_timeout,
            )
        # Drop the lock before disable() — it takes the same lock.
        self.disable(wait_for_jobs=False)
        return True

    def shutdown(self) -> None:
        """Stop the watchdog thread and tear down the subprocess.

        Called from the FastAPI lifespan shutdown handler. Idempotent.
        """
        self._stop_event.set()
        watchdog = self._watchdog
        if watchdog is not None and watchdog.is_alive():
            watchdog.join(timeout=2.0)
        self.disable(wait_for_jobs=False)
