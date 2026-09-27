"""Package explicit Codex image-reading observations, never human ground truth."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.ocr.vision import apply_vision_candidates
from src.shared import collect_review_items, document_from_dict, to_dict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--images", required=True, type=Path)
    parser.add_argument("--observations", nargs="+", type=Path)
    args = parser.parse_args()
    samples = {}
    for path in args.observations or [Path(__file__).parent / "fixtures" / "zero14_visual_observations.json"]:
        samples.update(json.loads(path.read_text(encoding="utf-8-sig"))["samples"])
    summary = []
    for code, sample in samples.items():
        if sample.get("classification") == "non_nutrition_table":
            summary.append({"code": code, "classification": sample["classification"], "note": sample["note"]})
            continue
        doc = document_from_dict(json.loads((args.results / "structured" / f"{code}.json").read_text(encoding="utf-8")))
        image = args.images / doc.images[0].fileName
        basis = {"kind": sample["basis"], "raw": sample["basisRaw"], "value": sample["basis"]}
        if sample.get("servingSize"):
            number, unit = sample["servingSize"]
            basis["servingSize"] = {"raw": number + unit, "value": number, "unit": unit}
        rows = []
        for name, nutrient, raw, number, unit, nrv in sample["rows"]:
            unit = unit.replace("μ", "µ") if unit else unit
            if unit in {"µg RE", "mg α-TE"}:
                # Preserve equivalence units in raw; the v1 contract cannot calculate them.
                number, unit = None, None
            rows.append({
                "nutrient": {"raw": name, "value": nutrient},
                "amount": {"raw": raw, "value": number, "unit": unit},
                "nrvPercent": None if nrv is None else {
                    "raw": "%" if nrv == "?" else nrv + "%", "value": None if nrv == "?" else nrv, "unit": "%"},
            })
        info = doc.images[0]
        candidate = {
            "schemaVersion": "1.0", "documentId": code, "imageName": image.name,
            "imageSha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "humanVerified": False, "notes": sample.get("note", ""),
            "replaceAllTables": True, "supersedesTableIds": [table.id for table in doc.facts.nutritionTables],
            "tables": [{"tableId": "table:vision:1", "basis": basis, "rows": rows,
                        "boxPx": {"left": 0, "top": 0, "width": info.widthPx, "height": info.heightPx}}],
        }
        updated = apply_vision_candidates(doc, image.read_bytes(), candidate)
        for diagnostic in updated.tableDiagnostics:
            diagnostic.message += "; " + sample.get("note", "") + "; location is whole-image, not word-level"
        for directory, value in (("vision_candidates", candidate), ("structured_vision", to_dict(updated)),
                                  ("review_vision", collect_review_items(updated))):
            destination = args.results / directory
            destination.mkdir(exist_ok=True)
            (destination / f"{code}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary.append({"code": code, "rows": len(rows), "status": "needs_review", "humanVerified": False,
                        "unknownFields": sum(row["amount"]["value"] is None for row in rows) +
                        sum(row["nrvPercent"] is not None and row["nrvPercent"]["value"] is None for row in rows),
                        "note": sample.get("note", "")})
    (args.results / "vision_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {sum('rows' in item for item in summary)} unverified visual proposals; no OCR API calls")


if __name__ == "__main__":
    main()
