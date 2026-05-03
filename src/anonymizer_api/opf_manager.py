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
        return self._manager.current_base().detect(text)


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

    # ------------------------------------------------------------------
    # Read-only state
    # ------------------------------------------------------------------

    def status(self) -> OPFStatus:
        with self._lock:
            return OPFStatus(
                available=self._available,
                enabled=self._enabled,
                loading=self._loading,
                error=self._error,
                in_flight_jobs=self._refcount,
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
            if self._refcount == 0:
                self._zero_refs.notify_all()

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
