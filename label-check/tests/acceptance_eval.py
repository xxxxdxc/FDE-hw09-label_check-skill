"""Evidence-based OCR acceptance evaluation. Run: python -m tests.acceptance_eval."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from src.ocr.parser import parse_accurate
from src.shared.confidence import LOW_OCR_CONFIDENCE
from src.shared.validation import validate_document

DEFAULT_ANNOTATIONS = Path(__file__).parent / "fixtures" / "acceptance_annotations.json"
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _numeric_field(document, role):
    table = document.facts.nutritionTables[0] if document.facts.nutritionTables else None
    if table is None:
        return None
    if role == "serving_size":
        return table.basis.servingSize
    for suffix, attr in (("_amount", "amount"), ("_nrv", "nrvPercent")):
        if role.endswith(suffix):
            nutrient = role[: -len(suffix)]
            return next((getattr(row, attr) for row in table.rows if row.nutrient.value == nutrient), None)
    raise ValueError(f"Unknown numeric role: {role}")


def _numeric_observed(document, role):
    field = _numeric_field(document, role)
    if field is None or not field.sourceRefs:
        return None
    evidence = {entry.id: entry for entry in document.evidence}
    parts = [evidence[ref].text for ref in field.sourceRefs]
    numbers = NUMBER.findall(" ".join(parts))
    return numbers[0] if len(numbers) == 1 else None


def _table_matches(document, truth):
    if len(document.facts.nutritionTables) != 1:
        return False, "Expected exactly one nutrition table"
    table = document.facts.nutritionTables[0]
    basis = table.basis
    if (basis.kind, basis.servingSize.value if basis.servingSize else None,
        basis.servingSize.unit if basis.servingSize else None) != (
        truth["basisKind"], truth["servingSize"], truth["servingUnit"]
    ):
        return False, "Serving basis mismatch"
    if len(table.rows) != len(truth["rows"]):
        return False, "Nutrition row count mismatch"
    actual = {row.nutrient.value: row for row in table.rows}
    if len(actual) != len(table.rows):
        return False, "Duplicate nutrient rows"
    for expected in truth["rows"]:
        row = actual.get(expected["nutrient"])
        if row is None:
            return False, f"Missing nutrient: {expected['nutrient']}"
        observed = (row.amount.value, row.amount.unit,
                    row.nrvPercent.value if row.nrvPercent else None)
        wanted = (expected["amount"], expected["unit"], expected["nrvPercent"])
        if observed != wanted:
            return False, f"Nutrient mismatch: {expected['nutrient']}"
    return True, None


def _resolved_images(data, base_path):
    paths = [(base_path / case["image"]).resolve() for case in data["cases"]]
    if len(set(paths)) != len(paths):
        raise ValueError("Repeated copies of one image cannot count as separate label images")
    fingerprints = [hashlib.sha256(path.read_bytes()).digest() for path in paths if path.is_file()]
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("Identical image content cannot count as separate label images")
    return paths


def evaluate(manifest_path: Path = DEFAULT_ANNOTATIONS):
    manifest_path = manifest_path.resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("annotationVersion") != "1.0" or not data.get("cases"):
        raise ValueError("Acceptance annotations are empty or unsupported")
    image_paths = _resolved_images(data, manifest_path.parent)

    numeric_details, table_details, confidence_details = [], [], []
    for case, image_path in zip(data["cases"], image_paths):
        raw_path = (manifest_path.parent / case["ocrResponse"]).resolve()
        if not image_path.is_file() or not raw_path.is_file():
            raise ValueError(f"Image or OCR response missing: {image_path}, {raw_path}")
        response = json.loads(raw_path.read_text(encoding="utf-8-sig"))
        document = parse_accurate(image_path, response, image_path.stem, case.get("productId"))
        validate_document(document)
        evidence = {entry.id: entry for entry in document.evidence}
        roles = [cell["role"] for cell in case["numericCells"]]
        if len(roles) != len(set(roles)):
            raise ValueError(f"Duplicate numeric roles in {image_path}")
        for cell in case["numericCells"]:
            observed = _numeric_observed(document, cell["role"])
            numeric_details.append({
                "image": image_path.name, "role": cell["role"],
                "expected": cell["digits"], "observed": observed,
                "correct": observed == cell["digits"],
            })
        passed, reason = _table_matches(document, case["nutritionTable"])
        table_details.append({"image": image_path.name, "correct": passed, "reason": reason})
        audit_ids = [item["evidenceId"] for item in case["confidenceAudit"]]
        if len(audit_ids) != len(set(audit_ids)):
            raise ValueError(f"Duplicate confidence audit IDs in {image_path}")
        for item in case["confidenceAudit"]:
            entry = evidence.get(item["evidenceId"])
            flagged = (entry is None or entry.ocrConfidence is None or
                       entry.ocrConfidence < LOW_OCR_CONFIDENCE)
            human = item["humanNeedsReview"]
            confidence_details.append({
                "image": image_path.name, "evidenceId": item["evidenceId"],
                "score": entry.ocrConfidence if entry else None,
                "thresholdFlag": flagged, "humanNeedsReview": human,
                "agrees": flagged == human,
            })

    n_ok = sum(item["correct"] for item in numeric_details)
    t_ok = sum(item["correct"] for item in table_details)
    c_ok = sum(item["agrees"] for item in confidence_details)
    n_total, t_total, c_total = len(numeric_details), len(table_details), len(confidence_details)
    count = len(image_paths)
    return {
        "source": str(manifest_path),
        "threshold": LOW_OCR_CONFIDENCE,
        "distinctImages": count,
        "numericRecognition": {
            "correct": n_ok, "total": n_total, "accuracy": n_ok / n_total if n_total else None,
            "target": 0.98, "requiredImages": 50,
            "status": "insufficient_samples" if count < 50 else
                      ("pass" if n_total and n_ok / n_total >= 0.98 else "fail"),
            "details": numeric_details,
        },
        "nutritionStructure": {
            "correct": t_ok, "total": t_total, "successRate": t_ok / t_total if t_total else None,
            "target": 0.95,
            "observedStatus": "pass" if t_total and t_ok / t_total >= 0.95 else "fail",
            "details": table_details,
        },
        "confidenceAudit": {
            "agree": c_ok, "total": c_total, "agreement": c_ok / c_total if c_total else None,
            "falseReview": sum(x["thresholdFlag"] and not x["humanNeedsReview"] for x in confidence_details),
            "missedReview": sum(not x["thresholdFlag"] and x["humanNeedsReview"] for x in confidence_details),
            "status": "pass" if c_total and c_ok == c_total else "fail",
            "details": confidence_details,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate OCR against manually transcribed label annotations")
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    args = parser.parse_args()
    report = evaluate(args.annotations)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
