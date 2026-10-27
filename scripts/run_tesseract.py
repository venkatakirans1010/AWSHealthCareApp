"""Run local Tesseract OCR for a claim PDF already stored in Amazon S3."""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402
from pytesseract import TesseractError, TesseractNotFoundError  # noqa: E402

from app.services.s3_service import S3ConfigurationError  # noqa: E402
from app.services.tesseract_service import analyze_claim_document  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim-id", required=True, help="Claim ID, for example CLM10001")
    parser.add_argument("--s3-key", required=True, help="Existing PDF object key")
    parser.add_argument("--no-s3-result", action="store_true", help="Do not upload the raw OCR JSON")
    args = parser.parse_args()

    try:
        result = analyze_claim_document(
            args.claim_id,
            args.s3_key,
            upload_to_s3=not args.no_s3_result,
        )
    except (BotoCoreError, ClientError, OSError, S3ConfigurationError, TesseractError, TesseractNotFoundError, ValueError) as error:
        print(f"OCR failed: {error}", file=sys.stderr)
        return 1

    print(f"Claim ID: {result['claim_id']}")
    print(f"OCR status: {result['extraction_status']}")
    print(f"Pages: {result['page_count']}")
    confidence = result["average_word_confidence"]
    print(f"Average word confidence: {confidence if confidence is not None else 'unavailable'}")
    print(f"Local JSON: {result['local_output_path']}")
    if result["s3_result_key"]:
        print(f"S3 result key: {result['s3_result_key']}")

    print("Candidate fields and review flags:")
    for field_name, review in result["field_review"].items():
        reasons = "; ".join(review["review_reasons"]) or "none"
        print(
            f"  {field_name}: value={review['value']!r}, "
            f"outcome={review['validation_outcome']}, review={review['review_required']}, "
            f"reasons={reasons}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
