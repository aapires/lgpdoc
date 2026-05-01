"""Real PrivacyFilterClient backed by the OpenAI Privacy Filter (opf) model.

The model is loaded once per instance. `opf` is an optional dependency — import
errors are deferred to instantiation time so the rest of the package works
without the ML stack installed.
"""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

from .client import PrivacyFilterClient
from .models import DetectedSpan

if TYPE_CHECKING:
    # Only for type-checkers; not imported at runtime unless opf is available.
    from opf import OPF as _OPFType

logger = logging.getLogger(__name__)

_OPERATING_POINT_MAP: dict[str, str] = {
    "precision": "viterbi",
    "recall": "argmax",
    "viterbi": "viterbi",
    "argmax": "argmax",
}

_DEFAULT_CHECKPOINT = "~/.opf/privacy_filter"
_SOURCE = "openai_privacy_filter"


def _resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class OpenAIPrivacyFilterClient(PrivacyFilterClient):
    """Wraps ``opf.OPF`` and normalises its output to internal ``DetectedSpan``s.

    Parameters
    ----------
    checkpoint_path:
        Directory with the model checkpoint, or ``None`` to use the OPF default
        (``~/.opf/privacy_filter`` or the ``OPF_CHECKPOINT`` env var).
    device:
        ``"auto"`` detects CUDA; otherwise ``"cpu"`` or ``"cuda"``.
    operating_point:
        ``"precision"`` (default, viterbi decoder) or ``"recall"`` (argmax).
    min_confidence:
        Spans whose score is below this threshold are discarded.
    """

    def __init__(
        self,
        checkpoint_path: str | None = None,
        device: str = "auto",
        operating_point: str = "precision",
        min_confidence: float = 0.0,
    ) -> None:
        self._checkpoint_path = checkpoint_path or _DEFAULT_CHECKPOINT
        self._device = _resolve_device(device)
        self._decode_mode = _OPERATING_POINT_MAP.get(operating_point, "viterbi")
        self._min_confidence = min_confidence
        self._model: _OPFType | None = None

        logger.info(
            "OpenAIPrivacyFilterClient configured device=%s decode_mode=%s min_confidence=%s",
            self._device,
            self._decode_mode,
            self._min_confidence,
        )

    # ------------------------------------------------------------------
    # Lazy model loading — the heavy import happens only on first detect().
    # ------------------------------------------------------------------

    def _load_model(self) -> _OPFType:
        try:
            from opf import OPF
        except ImportError as exc:
            raise RuntimeError(
                "The 'opf' package is required for OpenAIPrivacyFilterClient. "
                "Install it with: pip install -e 'git+https://github.com/openai/privacy-filter.git#egg=opf'"
            ) from exc

        logger.info(
            "Loading OPF model from checkpoint=%r device=%s",
            self._checkpoint_path,
            self._device,
        )
        model = OPF(
            model=self._checkpoint_path,
            device=self._device,
            output_mode="typed",
            decode_mode=self._decode_mode,
        )
        logger.info("OPF model loaded successfully")
        return model

    @property
    def model(self) -> _OPFType:
        if self._model is None:
            self._model = self._load_model()
        return self._model

    # ------------------------------------------------------------------
    # PrivacyFilterClient interface
    # ------------------------------------------------------------------

    def detect(self, text: str) -> list[DetectedSpan]:
        result = self.model.redact(text)
        spans: list[DetectedSpan] = []

        for raw in result.detected_spans:
            score: float | None = getattr(raw, "score", None)
            if score is not None and score < self._min_confidence:
                logger.debug(
                    "Discarding span entity_type=%r score=%.3f below min_confidence=%.3f",
                    raw.label,
                    score,
                    self._min_confidence,
                )
                continue

            span_text: str = getattr(raw, "text", "")
            spans.append(
                DetectedSpan(
                    start=raw.start,
                    end=raw.end,
                    entity_type=raw.label,
                    confidence=score,
                    text_hash=_hash_text(span_text) if span_text else None,
                    source=_SOURCE,
                )
            )
            # Log only position and type — never the span text itself.
            logger.debug(
                "Detected entity_type=%r span=[%d:%d] confidence=%s",
                raw.label,
                raw.start,
                raw.end,
                f"{score:.3f}" if score is not None else "n/a",
            )

        return spans
