#!/usr/bin/env python3
"""CLI entry-point: anonymize PII in a text file and write structured JSON output."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

# Make the src package importable when run directly from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from anonymizer.policy import Policy
from anonymizer.redactor import Redactor

_DEFAULT_POLICY = Path(__file__).parent.parent / "policies" / "default.yaml"
_DEFAULT_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("anonymizer.cli")


def _build_client(args: argparse.Namespace):
    if args.mock:
        from anonymizer.client import MockPrivacyFilterClient
        return MockPrivacyFilterClient()

    from anonymizer.privacy_filter_client import OpenAIPrivacyFilterClient
    return OpenAIPrivacyFilterClient(
        checkpoint_path=args.checkpoint or None,
        device=args.device,
        operating_point=args.operating_point,
        min_confidence=args.min_confidence,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Anonymize PII in a text file using OpenAI Privacy Filter"
    )
    parser.add_argument("--input", required=True, metavar="PATH", help="Input .txt file")
    parser.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Output JSON file (default: stdout)",
    )
    parser.add_argument(
        "--policy",
        default=str(_DEFAULT_POLICY),
        help="Path to policy YAML (default: policies/default.yaml)",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=_DEFAULT_MAX_BYTES,
        metavar="N",
        help=f"Reject files larger than N bytes (default: {_DEFAULT_MAX_BYTES})",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Compute device for the model (default: auto)",
    )
    parser.add_argument(
        "--operating-point",
        default="precision",
        choices=["precision", "recall"],
        dest="operating_point",
        help="Detection operating point (default: precision)",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        dest="min_confidence",
        metavar="FLOAT",
        help="Discard spans below this confidence score (default: 0.0)",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        metavar="PATH",
        help="Path to OPF model checkpoint (default: ~/.opf/privacy_filter)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use MockPrivacyFilterClient instead of the real model (for testing)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger("anonymizer").setLevel(logging.DEBUG)

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        sys.exit(1)

    file_size = input_path.stat().st_size
    if file_size > args.max_bytes:
        logger.error(
            "Input file size %d bytes exceeds limit of %d bytes",
            file_size,
            args.max_bytes,
        )
        sys.exit(1)

    # Never log file content — log only path and byte count.
    logger.info("Processing file=%s size=%d bytes", input_path, file_size)

    text = input_path.read_text(encoding="utf-8")

    policy = Policy.from_yaml(Path(args.policy))
    client = _build_client(args)
    redactor = Redactor(policy)

    spans = client.detect(text)
    result = redactor.redact(text, spans)

    output = {
        "source_file": str(input_path),
        "source_bytes": file_size,
        "redacted_text": result.redacted_text,
        "stats": result.stats,
        "applied_spans": [asdict(s) for s in result.applied_spans],
    }
    payload = json.dumps(output, ensure_ascii=False, indent=2)

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(payload, encoding="utf-8")
        logger.info("Output written to %s", out_path)
    else:
        print(payload)


if __name__ == "__main__":
    main()
