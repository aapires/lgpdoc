"""Tests for OpenAIPrivacyFilterClient — no model download required.

The client supports lazy loading of the OPF model. Tests inject a fake model
directly into ``client._model`` and use ``device="cpu"`` to avoid importing
torch at all — keeping the suite fast and side-effect-free.
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from anonymizer.privacy_filter_client import OpenAIPrivacyFilterClient, _hash_text


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

def _make_raw_span(label: str, start: int, end: int, text: str, score: float = 0.9):
    """Build a fake span object mimicking opf's DetectedSpan."""
    span = MagicMock()
    span.label = label
    span.start = start
    span.end = end
    span.text = text
    span.score = score
    return span


def _make_fake_model(spans):
    """Return a fake OPF model whose .redact() returns the given spans."""
    result = MagicMock()
    result.detected_spans = spans
    return MagicMock(redact=MagicMock(return_value=result))


def _client_with_fake_model(spans, **kwargs) -> OpenAIPrivacyFilterClient:
    """Construct a client with the fake model already injected."""
    client = OpenAIPrivacyFilterClient(device="cpu", **kwargs)
    client._model = _make_fake_model(spans)
    return client


# ---------------------------------------------------------------------------
# 1. Basic span normalisation
# ---------------------------------------------------------------------------

def test_detect_normalises_spans():
    raw = [_make_raw_span("private_email", 10, 27, "alice@example.com", score=0.98)]
    client = _client_with_fake_model(raw)
    spans = client.detect("Contact alice@example.com for info")

    assert len(spans) == 1
    s = spans[0]
    assert s.entity_type == "private_email"
    assert s.start == 10
    assert s.end == 27
    assert s.confidence == pytest.approx(0.98)
    assert s.source == "openai_privacy_filter"


# ---------------------------------------------------------------------------
# 2. text_hash is SHA-256, never raw text
# ---------------------------------------------------------------------------

def test_text_hash_is_sha256():
    raw_text = "alice@example.com"
    expected_hash = hashlib.sha256(raw_text.encode()).hexdigest()

    raw = [_make_raw_span("private_email", 0, len(raw_text), raw_text, score=0.95)]
    client = _client_with_fake_model(raw)
    spans = client.detect(raw_text)

    assert spans[0].text_hash == expected_hash
    # The DetectedSpan dataclass exposes neither raw text nor a `text` attribute.
    assert not hasattr(spans[0], "text")


def test_hash_helper_is_deterministic():
    assert _hash_text("hello") == _hash_text("hello")
    assert _hash_text("hello") != _hash_text("world")


# ---------------------------------------------------------------------------
# 3. min_confidence filtering
# ---------------------------------------------------------------------------

def test_min_confidence_filters_low_score_spans():
    raw = [
        _make_raw_span("private_person", 0, 5, "Alice", score=0.5),
        _make_raw_span("private_email", 10, 27, "alice@example.com", score=0.95),
    ]
    client = _client_with_fake_model(raw, min_confidence=0.8)
    spans = client.detect("Alice alice@example.com")

    assert len(spans) == 1
    assert spans[0].entity_type == "private_email"


def test_min_confidence_zero_keeps_all_spans():
    raw = [_make_raw_span("private_person", 0, 5, "Alice", score=0.01)]
    client = _client_with_fake_model(raw, min_confidence=0.0)
    spans = client.detect("Alice")

    assert len(spans) == 1


# ---------------------------------------------------------------------------
# 4. Device resolution
# ---------------------------------------------------------------------------

def test_device_cpu_passed_through():
    client = OpenAIPrivacyFilterClient(device="cpu")
    assert client._device == "cpu"


def test_device_auto_falls_back_to_cpu_when_no_cuda(monkeypatch):
    """When torch is unimportable, _resolve_device('auto') must fall back to cpu."""
    import sys

    monkeypatch.setitem(sys.modules, "torch", None)

    from anonymizer.privacy_filter_client import _resolve_device
    assert _resolve_device("auto") == "cpu"


# ---------------------------------------------------------------------------
# 5. operating_point → decode_mode mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("op, expected", [
    ("precision", "viterbi"),
    ("recall", "argmax"),
    ("viterbi", "viterbi"),
    ("argmax", "argmax"),
    ("unknown", "viterbi"),  # unmapped → default
])
def test_operating_point_mapping(op, expected):
    client = OpenAIPrivacyFilterClient(device="cpu", operating_point=op)
    assert client._decode_mode == expected


# ---------------------------------------------------------------------------
# 6. Model loaded only once (lazy singleton)
# ---------------------------------------------------------------------------

def test_model_loaded_once(monkeypatch):
    call_count = 0

    def fake_load(self):
        nonlocal call_count
        call_count += 1
        return _make_fake_model([])

    monkeypatch.setattr(OpenAIPrivacyFilterClient, "_load_model", fake_load)

    client = OpenAIPrivacyFilterClient(device="cpu")
    client.detect("first call")
    client.detect("second call")

    assert call_count == 1


# ---------------------------------------------------------------------------
# 7. Missing opf raises a helpful RuntimeError
# ---------------------------------------------------------------------------

def test_missing_opf_raises_runtime_error(monkeypatch):
    """If `from opf import OPF` raises ImportError, we want a RuntimeError with
    a clear install hint."""
    import sys

    # Force the import of `opf` to fail.
    monkeypatch.setitem(sys.modules, "opf", None)

    client = OpenAIPrivacyFilterClient(device="cpu")
    with pytest.raises(RuntimeError, match="opf.*package is required"):
        client._load_model()


# ---------------------------------------------------------------------------
# 8. Empty result (no PII detected)
# ---------------------------------------------------------------------------

def test_no_pii_returns_empty_list():
    client = _client_with_fake_model([])
    spans = client.detect("No personal information here.")
    assert spans == []
