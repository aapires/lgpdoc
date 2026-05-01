#!/usr/bin/env python3
"""CLI: anonymize a document file and write redacted.txt / spans.json / job_metadata.json."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from anonymizer.extractors.base import UnsupportedFormatError
from anonymizer.pipeline import ALLOWED_EXTENSIONS, DEFAULT_MAX_BYTES, DocumentPipeline, FileTooLargeError
from anonymizer.policy import Policy

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("anonymizer.cli")

_DEFAULT_POLICY = Path(__file__).parent.parent / "policies" / "default.yaml"


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
        description=(
            "Anonymize PII in a document file. "
            f"Supported formats: {sorted(ALLOWED_EXTENSIONS)}"
        )
    )
    parser.add_argument("--input", required=True, metavar="PATH", help="Input document")
    parser.add_argument(
        "--output",
        default="out/",
        metavar="DIR",
        help="Output directory for artefacts (default: out/)",
    )
    parser.add_argument(
        "--policy",
        default=str(_DEFAULT_POLICY),
        help="Path to policy YAML (default: policies/default.yaml)",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        metavar="N",
        help=f"Reject files larger than N bytes (default: {DEFAULT_MAX_BYTES})",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=["auto", "cpu", "cuda"],
        help="Compute device (default: auto)",
    )
    parser.add_argument(
        "--operating-point",
        default="precision",
        choices=["precision", "recall"],
        dest="operating_point",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.0,
        dest="min_confidence",
        metavar="FLOAT",
    )
    parser.add_argument("--checkpoint", default=None, metavar="PATH")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use MockPrivacyFilterClient (no model download needed)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger("anonymizer").setLevel(logging.DEBUG)

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("File not found: %s", input_path)
        sys.exit(1)

    policy_path = Path(args.policy)
    policy = Policy.from_yaml(policy_path)
    client = _build_client(args)
    output_dir = Path(args.output)

    pipeline = DocumentPipeline(
        client=client,
        policy=policy,
        output_dir=output_dir,
        max_bytes=args.max_bytes,
    )

    try:
        result = pipeline.run(input_path, policy_path=str(policy_path))
    except FileTooLargeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    except UnsupportedFormatError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    print(f"job_id      : {result.job_id}")
    print(f"blocks      : {result.metadata.block_count}")
    print(f"spans found : {len(result.applied_spans)}")
    if result.verification is not None:
        risk = result.verification.risk_assessment
        print(f"risk score  : {risk.score:.1f}")
        print(f"risk level  : {risk.level}")
        print(f"decision    : {risk.decision}")
    print(f"output      : {output_dir.resolve()}/")
    for name in ("redacted.txt", "spans.json", "job_metadata.json", "verification_report.json"):
        print(f"  {name}")

    # Exit code 2 signals that the document was blocked — useful in shell
    # pipelines that gate exports on the decision.
    if result.verification and result.verification.risk_assessment.decision == "blocked":
        sys.exit(2)


if __name__ == "__main__":
    main()
