"""OCR-only entry point. Later checks can consume the emitted LabelDocument JSON."""

import argparse
import json
import sys
from pathlib import Path

from src.ocr.baidu import OcrServiceError, recognize_accurate
from src.ocr.crop import prepare_table_image
from src.ocr.dual import parse_dual
from src.ocr.parser import parse_accurate
from src.ocr.table_v2 import recognize_table_v2
from src.shared import collect_review_items, to_dict, validate_document
from src.shared.models import BoxPx, TableDiagnostic


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Extract structured packaging-label data from an image")
    parser.add_argument("--image", required=True, type=Path, help="Original PNG or JPEG image")
    parser.add_argument("--product-id", help="Confirmed product grouping ID")
    parser.add_argument("--document-id", help="Review document ID; defaults to image stem")
    parser.add_argument("--ocr-json", type=Path, help="Use saved Baidu accurate response instead of calling the API")
    parser.add_argument("--table-v2-json", type=Path, help="Use saved Table V2 response (or response+transform wrapper)")
    parser.add_argument("--accurate-raw-output", type=Path, help="Save the unmodified accurate OCR response")
    parser.add_argument("--table-v2-raw-output", type=Path, help="Save Table V2 response with its image transform")
    parser.add_argument("--output", type=Path, help="Write structured JSON; otherwise print to stdout")
    parser.add_argument("--review-output", type=Path, help="Write uncertain fields and original-image locations to JSON")
    parser.add_argument("--vision-task-output", type=Path, help="Write structural table issues for Codex Skill image review")
    parser.add_argument("--expect-nutrition-table", action="store_true",
                        help="Flag zero-table output even without OCR table clues (for known nutrition-label images)")
    args = parser.parse_args(argv)
    try:
        if not args.image.is_file():
            raise ValueError(f"Image does not exist: {args.image}")
        if args.ocr_json:
            raw = json.loads(args.ocr_json.read_text(encoding="utf-8"))
        else:
            raw = recognize_accurate(args.image, Path(__file__).resolve().parents[2])
        if args.accurate_raw_output:
            args.accurate_raw_output.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        table_raw = None
        origin, scale = (0, 0), (1, 1)
        table_error = None
        if args.table_v2_json:
            saved = json.loads(args.table_v2_json.read_text(encoding="utf-8"))
            if "response" in saved:
                table_raw = saved["response"]
                transform = saved.get("transform") or {}
                origin = tuple(transform.get("origin", origin))
                scale = tuple(transform.get("scale", scale))
            else:
                table_raw = saved
        elif not args.ocr_json:
            try:
                prepared = prepare_table_image(args.image, raw)
                origin, scale = prepared.origin, prepared.scale
                table_raw = recognize_table_v2(prepared.data, Path(__file__).resolve().parents[2])
            except (OcrServiceError, ValueError) as exc:
                table_error = str(exc)
                print(f"Table V2 unavailable; structural review required: {table_error}", file=sys.stderr)
                table_raw = {"table_num": 0, "tables_result": []}
        if table_raw is not None:
            if not isinstance(table_raw.get("tables_result"), list):
                raise ValueError("Table V2 JSON lacks tables_result")
            if args.table_v2_raw_output:
                args.table_v2_raw_output.write_text(json.dumps({
                    "response": table_raw,
                    "transform": {"origin": list(origin), "scale": list(scale)},
                }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            doc = parse_dual(args.image, raw, table_raw, args.document_id or args.image.stem,
                             args.product_id, table_origin=origin, table_scale=scale,
                             expect_nutrition_table=args.expect_nutrition_table)
            if table_error:
                if not doc.tableDiagnostics:
                    doc.tableDiagnostics.append(TableDiagnostic(
                        "TABLE_V2_API_ERROR", "needs_vision", table_error, doc.images[0].id,
                        BoxPx(0, 0, doc.images[0].widthPx, doc.images[0].heightPx), [],
                    ))
                for diagnostic in doc.tableDiagnostics:
                    if diagnostic.code in {"TABLE_V2_NOT_FOUND", "TABLE_NOT_DETECTED"}:
                        diagnostic.code = "TABLE_V2_API_ERROR"
                        diagnostic.message = table_error
        else:
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
        if args.vision_task_output:
            tasks = [{
                "code": item.code, "tableId": item.tableId, "imageId": item.imageId,
                "boxPx": vars(item.boxPx) if item.boxPx else None,
                "evidenceRefs": item.evidenceRefs, "message": item.message,
            } for item in doc.tableDiagnostics if item.status == "needs_vision"]
            args.vision_task_output.write_text(json.dumps({
                "schemaVersion": "1.0", "documentId": doc.documentId,
                "imageFile": str(args.image.resolve()), "tasks": tasks,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Vision tasks saved: {args.vision_task_output} ({len(tasks)})", file=sys.stderr)
        print(f"Fields requiring human review: {len(review_items)}", file=sys.stderr)
        return 0
    except (OSError, ValueError, OcrServiceError, KeyError, TypeError) as exc:
        print(f"OCR structuring failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
