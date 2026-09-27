"""OCR-only entry point. Later checks can consume the emitted LabelDocument JSON."""

import argparse
import json
import sys
from pathlib import Path

from src.ocr.baidu import OcrServiceError, recognize_accurate
from src.ocr.parser import parse_accurate
from src.shared import collect_review_items, to_dict, validate_document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Extract structured packaging-label data from an image")
    parser.add_argument("--image", required=True, type=Path, help="Original PNG or JPEG image")
    parser.add_argument("--product-id", help="Confirmed product grouping ID")
    parser.add_argument("--document-id", help="Review document ID; defaults to image stem")
    parser.add_argument("--ocr-json", type=Path, help="Use saved Baidu accurate response instead of calling the API")
    parser.add_argument("--output", type=Path, help="Write structured JSON; otherwise print to stdout")
    parser.add_argument("--review-output", type=Path, help="Write uncertain fields and original-image locations to JSON")
    args = parser.parse_args(argv)
    try:
        if not args.image.is_file():
            raise ValueError(f"Image does not exist: {args.image}")
        if args.ocr_json:
            raw = json.loads(args.ocr_json.read_text(encoding="utf-8"))
        else:
            raw = recognize_accurate(args.image, Path(__file__).resolve().parents[2])
        doc = parse_accurate(args.image, raw, args.document_id or args.image.stem, args.product_id)
        validate_document(doc)
        output = json.dumps(to_dict(doc), ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            print(f"Structured label saved: {args.output}", file=sys.stderr)
        else:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            print(output, end="")
        review_items = collect_review_items(doc)
        if args.review_output:
            args.review_output.write_text(json.dumps(review_items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Review queue saved: {args.review_output}", file=sys.stderr)
        print(f"Fields requiring human review: {len(review_items)}", file=sys.stderr)
        return 0
    except (OSError, ValueError, OcrServiceError, KeyError, TypeError) as exc:
        print(f"OCR structuring failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
