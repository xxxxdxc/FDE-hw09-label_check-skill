"""Apply explicit human decisions to a copy of a structured label document."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from src.shared import collect_review_items, document_from_dict, to_dict, validate_document
from src.shared.confidence import LOW_OCR_CONFIDENCE
from src.shared.models import Basis, Evidence, FieldValue, LabelDocument
from src.shared.validation import BASIS_KINDS


PATH_PART = re.compile(r"^([A-Za-z]+)(?:\[(\d+)\])?$")


def _field_at_path(doc: LabelDocument, path: str) -> FieldValue | Basis:
    current: object = doc
    for part in path.split("."):
        match = PATH_PART.fullmatch(part)
        if match is None or not hasattr(current, match.group(1)):
            raise ValueError(f"Invalid review path: {path}")
        current = getattr(current, match.group(1))
        if match.group(2) is not None:
            if not isinstance(current, list) or int(match.group(2)) >= len(current):
                raise ValueError(f"Invalid review path: {path}")
            current = current[int(match.group(2))]
    if not isinstance(current, (FieldValue, Basis)):
        raise ValueError(f"Review path is not a field: {path}")
    return current


def apply_decisions(doc: LabelDocument, image_bytes: bytes, decisions: dict) -> LabelDocument:
    """Return a reviewed copy; reject stale, mismatched or fabricated decisions."""
    reviewed = document_from_dict(to_dict(doc))
    validate_document(reviewed)
    if decisions.get("reviewSchemaVersion") != "1.0":
        raise ValueError("Unsupported review decision schema")
    if decisions.get("documentId") != reviewed.documentId or decisions.get("productId") != reviewed.productId:
        raise ValueError("Review decisions do not match this document/product")
    if len(reviewed.images) != 1 or decisions.get("imageName") != Path(reviewed.images[0].fileName).name:
        raise ValueError("Review decisions do not match this image")
    if decisions.get("imageSha256") != hashlib.sha256(image_bytes).hexdigest():
        raise ValueError("Review decisions do not match the original image bytes")
    if decisions.get("threshold") != LOW_OCR_CONFIDENCE:
        raise ValueError("Review threshold changed; generate a fresh review page")
    if not isinstance(decisions.get("reviewer"), str) or not decisions["reviewer"].strip():
        raise ValueError("A named reviewer is required")
    queue = {item["path"]: item for item in collect_review_items(reviewed)}
    rows = decisions.get("decisions")
    table_rows = decisions.get("tableDecisions", [])
    if not isinstance(rows, list) or not isinstance(table_rows, list) or not (rows or table_rows):
        raise ValueError("There are no completed review decisions")
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("Invalid review decision")
        path = row["path"]
        if path in seen or path not in queue:
            raise ValueError(f"Duplicate or no-longer-pending review path: {path}")
        seen.add(path)
        item = queue[path]
        refs = [entry["id"] for entry in item["evidence"]]
        if (row.get("originalRaw") != item["raw"] or row.get("originalValue") != item["value"]
                or row.get("originalUnit") != item["unit"] or row.get("sourceEvidenceRefs") != refs):
            raise ValueError(f"Stale review decision at {path}")
        action = row.get("action")
        manual_text = row.get("manualText")
        value, unit = row.get("correctedValue"), row.get("correctedUnit")
        if (action not in {"confirm", "correct"} or not isinstance(manual_text, str)
                or not manual_text.strip() or not isinstance(value, str) or not value.strip()):
            raise ValueError(f"Invalid action or missing manual text/value at {path}")
        if unit is not None and not isinstance(unit, str):
            raise ValueError(f"Invalid manual unit at {path}")
        if action == "confirm" and (manual_text != item["raw"] or value != item["value"] or unit != item["unit"]):
            raise ValueError(f"Confirmed value does not match the original parse at {path}")
        field = _field_at_path(reviewed, path)
        if isinstance(field, Basis):
            if unit is not None or value not in BASIS_KINDS or value == "unknown":
                raise ValueError(f"Select a known basis for {path}")
            if value == "per_serving" and field.servingSize is None:
                raise ValueError("per_serving requires a servingSize field")
        elif value == "unknown" and path.endswith(".nutrient"):
            raise ValueError("Nutrient name must be reviewed from the image")
        next_id = len(reviewed.evidence) + 1
        existing_ids = {entry.id for entry in reviewed.evidence}
        while f"manual-review:{next_id}" in existing_ids:
            next_id += 1
        evidence_id = f"manual-review:{next_id}"
        reviewed.evidence.append(Evidence(
            id=evidence_id, imageId=None, sourceType="manual", text=manual_text,
            boxPx=None, ocrConfidence=None,
        ))
        if isinstance(field, Basis):
            field.kind = value
            field.raw = manual_text
        else:
            field.raw = manual_text
            field.value = value
            field.unit = unit
            field.reviewReasons = []
        field.sourceRefs = list(dict.fromkeys([*field.sourceRefs, evidence_id]))
        field.status = "ready"
        if path.startswith("facts.claims[") and path.endswith("].text"):
            claim_index = int(path.split("[")[1].split("]")[0])
            if reviewed.facts.claims[claim_index].category == "front_title" and reviewed.productIdSource == "unknown":
                reviewed.productId = value
                reviewed.productIdSource = "human_reviewed_title"
    seen_tables: set[str] = set()
    for decision in table_rows:
        if not isinstance(decision, dict) or decision.get("action") != "confirm_complete":
            raise ValueError("Invalid table-completeness decision")
        table_id = decision.get("tableId")
        if not isinstance(table_id, str) or table_id in seen_tables:
            raise ValueError("Duplicate or missing table decision")
        seen_tables.add(table_id)
        diagnostic = next((item for item in reviewed.tableDiagnostics
                           if item.tableId == table_id and item.status == "needs_review"), None)
        table = next((item for item in reviewed.facts.nutritionTables if item.id == table_id), None)
        if (diagnostic is None or table is None or decision.get("originalCode") != diagnostic.code
                or decision.get("sourceEvidenceRefs") != diagnostic.evidenceRefs):
            raise ValueError(f"Stale table decision: {table_id}")
        if not table.rows or table.basis.status != "ready" or any(
            field.status != "ready" for row in table.rows
            for field in (row.nutrient, row.amount, row.nrvPercent) if field is not None
        ):
            raise ValueError(f"Confirm all table fields before table completeness: {table_id}")
        evidence_id = f"manual-review:{len(reviewed.evidence) + 1}"
        while any(item.id == evidence_id for item in reviewed.evidence):
            evidence_id = f"manual-review:{int(evidence_id.split(':')[1]) + 1}"
        reviewed.evidence.append(Evidence(evidence_id, None, "manual",
                                          f"{decisions['reviewer']} confirmed complete table {table_id}", None, None))
        diagnostic.status = "ok"
        diagnostic.code = "HUMAN_REVIEWED_TABLE"
        diagnostic.message = "Human reviewer confirmed every row and column against the original image"
        diagnostic.evidenceRefs.append(evidence_id)
    validate_document(reviewed)
    return reviewed


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply human review decisions to a new structured JSON")
    parser.add_argument("--document", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.suffix.lower() != ".json" or args.output.resolve() in {
        args.document.resolve(), args.decisions.resolve(), args.image.resolve()
    }:
        parser.error("Output must be a new .json file and cannot overwrite an input")
    if args.output.exists():
        parser.error("Output already exists; choose a new path to preserve the review audit")
    doc = document_from_dict(json.loads(args.document.read_text(encoding="utf-8")))
    decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
    reviewed = apply_decisions(doc, args.image.read_bytes(), decisions)
    args.output.write_text(json.dumps(to_dict(reviewed), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending = len(collect_review_items(reviewed))
    print(f"Reviewed document saved: {args.output} ({pending} fields still pending)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
