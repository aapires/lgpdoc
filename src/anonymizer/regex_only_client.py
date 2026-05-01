"""Pure-regex detector client used by the diagnostic comparison mode.

Implements the ``PrivacyFilterClient`` interface but only fires the
deterministic Brazilian regex detectors — never the OPF model. It is the
"regex" side of the OPF-vs-regex comparison: feeding the same text to the
real OPF client and to a ``RegexOnlyClient`` lets us measure where each
detector is contributing and where they disagree.

Design notes
------------

* Mirrors the augmented client's regex layer: ``REGEX_DETECTORS`` plus the
  light auxiliary detectors from ``augmentations`` (CPF/CNPJ check-digit,
  BR labelled names, BR addresses). This keeps the comparison meaningful —
  the comparison mode reflects what the real pipeline would see if OPF
  were silent.
* No heavy imports. The module only depends on standard library + sibling
  pure-Python modules. The OPF model is *never* loaded from here.
* Optional ``get_enabled_kinds`` callback lets the same runtime settings
  toggle individual entity kinds in the diagnostic output, just like in
  the augmented client.
"""
from __future__ import annotations

import logging
from typing import Callable

from .augmentations import (
    detect_br_labeled_names,
    detect_cnpjs,
    detect_cpfs,
    detect_endereco_logradouro,
    detect_endereco_unidade,
)
from .client import PrivacyFilterClient
from .models import DetectedSpan
from .regex_detectors import REGEX_DETECTORS

logger = logging.getLogger(__name__)

AuxDetector = Callable[[str], list[DetectedSpan]]

# Detectors that are part of the augmented stack but live outside
# REGEX_DETECTORS (validated CPF/CNPJ, BR labelled names, BR addresses).
_BR_AUX_DETECTORS: tuple[AuxDetector, ...] = (
    detect_br_labeled_names,
    detect_cpfs,
    detect_cnpjs,
    detect_endereco_logradouro,
    detect_endereco_unidade,
)


class RegexOnlyClient(PrivacyFilterClient):
    """Detector that runs every deterministic regex rule and nothing else.

    Parameters
    ----------
    get_enabled_kinds:
        Optional callable returning the currently enabled entity kinds.
        Spans whose ``entity_type`` is not in the returned set are dropped,
        matching the behaviour of the augmented client.
    include_br_aux:
        When True (default), also runs the BR auxiliary detectors
        (CPF/CNPJ/labelled-name/address) on top of ``REGEX_DETECTORS``.
        Set to False to restrict the output to ``REGEX_DETECTORS`` only.
    """

    def __init__(
        self,
        *,
        get_enabled_kinds: Callable[[], set[str]] | None = None,
        include_br_aux: bool = True,
    ) -> None:
        self._get_enabled_kinds = get_enabled_kinds
        detectors: list[AuxDetector] = []
        if include_br_aux:
            detectors.extend(_BR_AUX_DETECTORS)
        detectors.extend(REGEX_DETECTORS.values())
        self._detectors: tuple[AuxDetector, ...] = tuple(detectors)

    def detect(self, text: str) -> list[DetectedSpan]:
        spans: list[DetectedSpan] = []
        for detector in self._detectors:
            spans.extend(detector(text))

        if self._get_enabled_kinds is not None:
            enabled = self._get_enabled_kinds()
            spans = [s for s in spans if s.entity_type in enabled]

        # Metadata only — never log the text or hash contents of the spans.
        logger.debug(
            "RegexOnlyClient produced spans count=%d detectors=%d",
            len(spans),
            len(self._detectors),
        )
        return spans
