#!/usr/bin/env python3
"""CLI entry-point: anonymize text and print a structured JSON result."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

# Make the src package importable when run directly from the repo root.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from anonymizer.client import MockPrivacyFilterClient
from anonymizer.policy import Policy
from anonymizer.redactor import Redactor

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Anonymize PII in text")
    parser.add_argument("--text", required=True, help="Input text to anonymize")
    parser.add_argument(
        "--policy",
        default=str(Path(__file__).parent.parent / "policies" / "default.yaml"),
        help="Path to policy YAML file",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger("anonymizer").setLevel(logging.DEBUG)

    policy = Policy.from_yaml(Path(args.policy))
    client = MockPrivacyFilterClient()
    redactor = Redactor(policy)

    spans = client.detect(args.text)
    result = redactor.redact(args.text, spans)

    output = {
        "redacted_text": result.redacted_text,
        "stats": result.stats,
        "applied_spans": [asdict(s) for s in result.applied_spans],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
