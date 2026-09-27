"""Run the dual-OCR pipeline on the existing 50-image Open Food Facts corpus.

Accurate OCR is reused from the corpus. Table V2 responses are cached by image hash
so interrupted batches can resume without paying for the same call twice.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
from pathlib import Path

from src.ocr.baidu import OcrServiceError
from src.ocr.crop import prepare_table_image
from src.ocr.dual import parse_dual
from src.ocr.table_v2 import recognize_table_v2
from src.shared import collect_review_items, to_dict, validate_document


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--offline", action="store_true",
                        help="Require all 50 saved Table V2 responses; never call OCR APIs")
    args = parser.parse_args()
    entries = json.loads((args.dataset / "manifest.json").read_text(encoding="utf-8"))
    if len(entries) != 50:
        raise ValueError(f"Expected 50 samples, got {len(entries)}")
    if args.offline:
        missing = [entry["code"] for entry in entries
                   if not (args.output / "table_v2_raw" / f"{entry['code']}.json").is_file()]
        if missing:
            raise ValueError(f"Offline replay requires cached Table V2 responses: {missing}")
    args.output.mkdir(parents=True, exist_ok=True)
    fixture_dir = Path(__file__).parent / "fixtures"
    rows = []
    consecutive_api_errors = 0
    for index, entry in enumerate(entries, 1):
        code = entry["code"]
        image = args.dataset / entry["imageFile"]
        accurate_raw = json.loads((args.dataset / entry["rawFile"]).read_text(encoding="utf-8"))
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        row = {"code": code, "productName": entry.get("productName", ""),
               "imageFile": str(image), "oldRows": entry.get("nutritionRows", 0),
               "oldTables": entry.get("nutritionTables", 0),
               "accurateLines": len(accurate_raw.get("words_result", [])),
               "accurateLowConfidenceLines": sum(
                   isinstance(item.get("probability"), dict)
                   and item["probability"].get("average", 1) < 0.96
                   for item in accurate_raw.get("words_result", [])),
               "tableV2Source": "", "tableV2CacheHit": False,
               "tableV2Count": 0, "newRows": 0,
               "readyRows": 0, "reviewItems": 0, "visionTasks": 0,
               "diagnostics": "", "error": ""}
        try:
            cache = args.output / "table_v2_raw" / f"{code}.json"
            if cache.exists():
                saved = json.loads(cache.read_text(encoding="utf-8"))
                if saved.get("imageSha256") != digest:
                    raise ValueError("Cached Table V2 response belongs to a different image")
                row["tableV2Source"] = saved.get("source", "cached")
                row["tableV2CacheHit"] = True
            else:
                if args.offline:
                    raise ValueError(f"Offline replay cannot call OCR for {code}")
                fixture = fixture_dir / f"table_v2_{code}.json"
                if fixture.exists():
                    response = json.loads(fixture.read_text(encoding="utf-8"))
                    transform = {"origin": [0, 0], "scale": [1, 1]}
                    source = "prior_v2_fixture"
                else:
                    prepared = prepare_table_image(image, accurate_raw)
                    response = recognize_table_v2(prepared.data, args.project_root)
                    transform = {"origin": list(prepared.origin), "scale": list(prepared.scale),
                                 "inputSize": list(prepared.inputSize), "cropBox": list(prepared.cropBox)}
                    source = "live_baidu_v2"
                saved = {"imageSha256": digest, "source": source,
                         "response": response, "transform": transform}
                write_json(cache, saved)
                row["tableV2Source"] = source
                consecutive_api_errors = 0
            response = saved["response"]
            row["tableV2Count"] = response.get("table_num", 0)
            transform = saved["transform"]
            doc = parse_dual(image, accurate_raw, response, code, product_id=code,
                             table_origin=tuple(transform["origin"]),
                             table_scale=tuple(transform["scale"]),
                             expect_nutrition_table=True)
            validate_document(doc)
            reviews = collect_review_items(doc)
            tasks = [{"code": item.code, "message": item.message,
                      "imageId": item.imageId, "boxPx": to_dict(item.boxPx),
                      "evidenceRefs": item.evidenceRefs, "tableId": item.tableId}
                     for item in doc.tableDiagnostics if item.status == "needs_vision"]
            write_json(args.output / "structured" / f"{code}.json", to_dict(doc))
            write_json(args.output / "review" / f"{code}.json", reviews)
            write_json(args.output / "vision_tasks" / f"{code}.json",
                       {"documentId": code, "imageFile": str(image), "tasks": tasks})
            row["newRows"] = sum(len(table.rows) for table in doc.facts.nutritionTables)
            row["readyRows"] = sum(
                nutrient.status == amount.status == "ready"
                for table in doc.facts.nutritionTables
                for nutrient, amount in ((item.nutrient, item.amount) for item in table.rows))
            row["reviewItems"] = len(reviews)
            row["visionTasks"] = len(tasks)
            row["diagnostics"] = ";".join(item.code + ":" + item.status for item in doc.tableDiagnostics)
        except (OcrServiceError, ValueError, OSError, KeyError, TypeError) as exc:
            row["error"] = str(exc)
            if isinstance(exc, OcrServiceError):
                consecutive_api_errors += 1
        rows.append(row)
        write_json(args.output / "summary.json", rows)
        with (args.output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerows(rows)
        print(f"[{index:02d}/50] {code}: V2={row['tableV2Count']} rows={row['newRows']} "
              f"ready={row['readyRows']} vision={row['visionTasks']} error={row['error'][:90]}",
              flush=True)
        if consecutive_api_errors >= 3:
            print("Stopped after three consecutive Table V2 API errors", file=sys.stderr)
            break
        if row["tableV2Source"] == "live_baidu_v2" and not row["tableV2CacheHit"]:
            time.sleep(0.4)
    return 0 if len(rows) == 50 else 2


if __name__ == "__main__":
    raise SystemExit(main())
