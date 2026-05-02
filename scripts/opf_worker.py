"""OPF subprocess worker — runs OpenAIPrivacyFilterClient out-of-process.

Loaded by ``OPFManager`` when the user enables OPF in the UI. When the
user disables it, this process is asked to terminate (or killed if it
doesn't exit promptly) and the operating system reclaims the ~3 GB of
RAM the model holds — which the in-process model can't do reliably
because torch/glibc don't return CPU allocations to the OS.

Protocol (one JSON object per line on each side):

    parent → worker:
        {"action": "detect", "text": "..."}      run detection
        {"action": "ping"}                       readiness probe
        {"action": "shutdown"}                   exit cleanly

    worker → parent:
        {"event": "loading"}                     emitted at startup
        {"event": "ready"}                       model loaded, accepting work
        {"event": "error", "message": "..."}     fatal error during boot
        {"spans": [...]}                         response to detect
        {"event": "pong"}                        response to ping
        {"event": "bye"}                         response to shutdown

Each span dict carries: ``start, end, entity_type, confidence, text_hash, source``.

The ``--mock`` flag swaps the real OPF for ``MockPrivacyFilterClient`` —
exclusively for tests, so the subprocess plumbing can be exercised
without ``torch`` / ``opf`` installed.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import traceback

from anonymizer.client import MockPrivacyFilterClient, PrivacyFilterClient

logger = logging.getLogger("opf_worker")


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _build_client(use_mock: bool) -> PrivacyFilterClient:
    if use_mock:
        return MockPrivacyFilterClient()
    from anonymizer.privacy_filter_client import OpenAIPrivacyFilterClient

    client = OpenAIPrivacyFilterClient()
    # Force the model load up-front so "ready" actually means ready —
    # otherwise the parent would block on the first detect() call instead.
    _ = client.model
    return client


def main() -> int:
    parser = argparse.ArgumentParser(description="OPF subprocess worker")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use MockPrivacyFilterClient (regex) instead of real OPF — tests only.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s opf_worker %(levelname)s %(message)s",
    )

    _emit({"event": "loading"})
    try:
        client = _build_client(args.mock)
    except Exception as exc:
        logger.exception("worker boot failed")
        _emit({"event": "error", "message": f"{type(exc).__name__}: {exc}"})
        return 1
    _emit({"event": "ready"})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _emit({"event": "error", "message": "invalid_json"})
            continue

        action = msg.get("action")
        if action == "detect":
            text = msg.get("text", "")
            try:
                spans = client.detect(text)
            except Exception as exc:
                logger.exception("detect failed")
                _emit(
                    {
                        "event": "error",
                        "message": f"detect_failed: {type(exc).__name__}: {exc}",
                    }
                )
                continue
            _emit(
                {"spans": [dataclasses.asdict(s) for s in spans]}
            )
        elif action == "ping":
            _emit({"event": "pong"})
        elif action == "shutdown":
            _emit({"event": "bye"})
            return 0
        else:
            _emit({"event": "error", "message": f"unknown_action: {action!r}"})

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(2)
