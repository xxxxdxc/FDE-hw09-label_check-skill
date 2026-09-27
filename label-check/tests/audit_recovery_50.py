"""Offline audit: schema, provenance, and model observations; NOT human acceptance."""
import argparse
import json
from pathlib import Path
from src.shared import document_from_dict, validate_document, collect_review_items


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    root = args.results
    direct = read(Path(__file__).parent / "fixtures/parsed9_visual_audit.json")["samples"]
    vision = {item["code"]: item for item in read(root / "vision_summary.json")}
    output = root / "final_candidates"
    output.mkdir(exist_ok=True)
    records = []
    for path in sorted((root / "structured").glob("*.json")):
        code = path.stem
        candidate = root / "structured_vision" / path.name
        data = read(candidate if candidate.exists() else path)
        doc = document_from_dict(data)
        validate_document(doc)
        rows = [r for t in data["facts"]["nutritionTables"] for r in t["rows"]]
        differences = []
        if code in direct:
            kind, serving, expected = direct[code]
            tables = data["facts"]["nutritionTables"]
            got = [[r["nutrient"]["value"], r["amount"]["value"], r["amount"]["unit"],
                    r["nrvPercent"]["value"] if r["nrvPercent"] else None] for r in rows]
            if got != expected:
                differences.append({"expected": expected, "actual": got})
            if len(tables) != 1 or tables[0]["basis"]["kind"] != kind:
                differences.append("basis/table count mismatch")
            if serving and tables[0]["basis"]["servingSize"]["value"] != serving:
                differences.append("serving size mismatch")
        if candidate.exists():
            assert all(r["amount"]["status"] == "needs_review" for r in rows)
            assert any(e["sourceType"] == "codex_vision" for e in data["evidence"])
        record = {"code": code, "rows": len(rows), "schemaValid": True,
                  "source": "codex_vision_candidate" if candidate.exists() else "ocr_parser",
                  "humanVerified": False, "reviewItems": len(collect_review_items(doc)),
                  "nullAmounts": sum(r["amount"]["value"] is None for r in rows),
                  "modelAuditDifferences": differences, "note": vision.get(code, {}).get("note", ""),
                  "classification": vision.get(code, {}).get("classification", "nutrition_table")}
        records.append(record)
        (output / path.name).write_text(json.dumps(data, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    assert len(records) == 50, f"Expected 50 documents, got {len(records)}"
    summary = {"documents": len(records), "schemaValid": len(records),
               "nutritionCandidates": sum(r["rows"] > 0 for r in records),
               "visionCandidates": sum(r["source"] == "codex_vision_candidate" for r in records),
               "candidateRows": sum(r["rows"] for r in records),
               "modelAuditDifferences": sum(bool(r["modelAuditDifferences"]) for r in records),
               "humanVerified": 0, "acceptancePassed": False,
               "warning": "Model observations and schema checks cannot establish OCR or structure accuracy",
               "records": records}
    (root / "final_audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({k:v for k,v in summary.items() if k != "records"}, ensure_ascii=True))
    assert not summary["modelAuditDifferences"], "Model visual audit found mismatches"


if __name__ == "__main__":
    main()
