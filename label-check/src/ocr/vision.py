"""Import Codex image-reading proposals as *unverified* table candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from src.shared import document_from_dict, to_dict, validate_document
from src.shared.models import Basis, BoxPx, Evidence, FieldValue, LabelDocument, NutritionRow, NutritionTable
from src.shared.validation import BASIS_KINDS


def apply_vision_candidates(doc: LabelDocument, image_bytes: bytes, candidates: dict) -> LabelDocument:
    updated = document_from_dict(to_dict(doc))
    validate_document(updated)
    if candidates.get("schemaVersion") != "1.0" or candidates.get("documentId") != updated.documentId:
        raise ValueError("Vision candidates do not match this document")
    if len(updated.images) != 1 or candidates.get("imageName") != updated.images[0].fileName:
        raise ValueError("Vision candidates do not match the image name")
    if candidates.get("imageSha256") != hashlib.sha256(image_bytes).hexdigest():
        raise ValueError("Vision candidates do not match the original image bytes")
    proposals = candidates.get("tables")
    if not isinstance(proposals, list) or not proposals:
        raise ValueError("Vision candidates need at least one proposed table")
    supersedes = candidates.get("supersedesTableIds", [])
    old_ids = {table.id for table in updated.facts.nutritionTables}
    if (not isinstance(supersedes, list) or any(not isinstance(item, str) for item in supersedes)
            or not set(supersedes) <= old_ids):
        raise ValueError("Vision candidates name unknown superseded tables")
    replace_all = candidates.get("replaceAllTables", False)
    if replace_all and (len(proposals) != 1 or set(supersedes) != old_ids):
        raise ValueError("Whole-image replacement needs one proposal and every previous table ID")
    existing_ids = {entry.id for entry in updated.evidence}
    seen_tables = set()

    def candidate_field(value: dict, diagnostic, role: str) -> FieldValue:
        if not isinstance(value, dict) or not isinstance(value.get("raw"), str) or not value["raw"].strip():
            raise ValueError(f"Missing vision {role} raw text")
        text = value["raw"].strip()
        normalized = value.get("value")
        unit = value.get("unit")
        if normalized is not None and not isinstance(normalized, str):
            raise ValueError(f"Invalid vision {role} value")
        if unit is not None and not isinstance(unit, str):
            raise ValueError(f"Invalid vision {role} unit")
        support = value.get("supportingEvidenceRefs", [])
        if not isinstance(support, list) or any(ref not in existing_ids for ref in support):
            raise ValueError(f"Unknown supporting evidence for vision {role}")
        evidence_id = f"vision:{len(updated.evidence) + 1}"
        while evidence_id in existing_ids:
            evidence_id = f"vision:{int(evidence_id.split(':')[1]) + 1}"
        # boxPx describes the *table region*, never a fabricated word-level location.
        updated.evidence.append(Evidence(evidence_id, diagnostic.imageId, "codex_vision",
                                         text, diagnostic.boxPx, None))
        existing_ids.add(evidence_id)
        return FieldValue(text, normalized, unit, [*support, evidence_id], "needs_review",
                          ["VISION_CANDIDATE_UNVERIFIED"])

    for proposal in proposals:
        table_id = proposal.get("tableId")
        if not isinstance(table_id, str) or table_id in seen_tables:
            raise ValueError("Duplicate or missing vision tableId")
        seen_tables.add(table_id)
        diagnostic = next((item for item in updated.tableDiagnostics
                           if item.tableId == table_id and item.status == "needs_vision"), None)
        if diagnostic is None:
            # TABLE_V2_NOT_FOUND has no tableId; permit a new table only for that diagnostic.
            diagnostic = next((item for item in updated.tableDiagnostics
                               if item.tableId is None and item.status == "needs_vision"), None)
        if diagnostic is None:
            diagnostic = next((item for item in updated.tableDiagnostics
                               if item.tableId in supersedes and item.status == "needs_vision"), None)
        if diagnostic is None:
            raise ValueError(f"No unresolved structural task for table {table_id}")
        if proposal.get("boxPx") is not None:
            try:
                box = BoxPx(**proposal["boxPx"])
                image = updated.images[0]
                if (min(box.left, box.top) < 0 or min(box.width, box.height) <= 0 or
                        box.left + box.width > image.widthPx or box.top + box.height > image.heightPx):
                    raise ValueError("Vision table region outside original image")
                diagnostic.boxPx = box
            except (TypeError, KeyError) as exc:
                raise ValueError("Invalid vision table region") from exc
        basis_info = proposal.get("basis")
        if not isinstance(basis_info, dict) or basis_info.get("kind") not in BASIS_KINDS:
            raise ValueError("Invalid vision table basis")
        basis_field = candidate_field(basis_info, diagnostic, "basis")
        serving = None
        if basis_info["kind"] == "per_serving":
            serving = candidate_field(basis_info.get("servingSize"), diagnostic, "servingSize")
        basis = Basis(basis_info["kind"], basis_field.raw, basis_field.sourceRefs, "needs_review", serving)
        rows_info = proposal.get("rows")
        if not isinstance(rows_info, list) or not rows_info:
            raise ValueError("Vision proposal must contain at least one row")
        rows = []
        for row in rows_info:
            if not isinstance(row, dict):
                raise ValueError("Invalid vision row")
            nutrient = candidate_field(row.get("nutrient"), diagnostic, "nutrient")
            amount = candidate_field(row.get("amount"), diagnostic, "amount")
            nrv = candidate_field(row["nrvPercent"], diagnostic, "nrvPercent") if row.get("nrvPercent") else None
            rows.append(NutritionRow(nutrient, amount, nrv))
        table = NutritionTable(table_id, basis, rows, list(dict.fromkeys([
            *diagnostic.evidenceRefs, *basis.sourceRefs,
        ])))
        old = next((i for i, item in enumerate(updated.facts.nutritionTables) if item.id == table_id), None)
        if old is None:
            updated.facts.nutritionTables.append(table)
        else:
            updated.facts.nutritionTables[old] = table
        diagnostic.code = "VISION_CANDIDATE_UNVERIFIED"
        diagnostic.status = "needs_review"
        diagnostic.message = "Codex vision proposed table rows; human must verify every field and row completeness"
        diagnostic.tableId = table_id
        diagnostic.evidenceRefs = list(dict.fromkeys([*diagnostic.evidenceRefs,
            *(ref for row in rows for field in (row.nutrient, row.amount, row.nrvPercent)
              if field for ref in field.sourceRefs)]))
    updated.facts.nutritionTables = [table for table in updated.facts.nutritionTables
                                    if table.id not in supersedes or table.id in seen_tables]
    for diagnostic in updated.tableDiagnostics:
        if diagnostic.tableId in supersedes and diagnostic.tableId not in seen_tables:
            diagnostic.tableId = None
            diagnostic.code = "VISION_CANDIDATE_UNVERIFIED"
            diagnostic.status = "needs_review"
            diagnostic.message = "Previous table replaced by unverified visual candidates; human review required"
    if replace_all:
        updated.tableDiagnostics = [item for item in updated.tableDiagnostics if item.tableId in seen_tables]
    used = set()
    for field in (updated.facts.productName, updated.facts.netContent,
                  updated.facts.ingredientsText, updated.facts.allergenText):
        used.update(field.sourceRefs)
    for table in updated.facts.nutritionTables:
        used.update(table.sourceRefs)
        used.update(table.basis.sourceRefs)
        if table.basis.servingSize:
            used.update(table.basis.servingSize.sourceRefs)
        for row in table.rows:
            for field in (row.nutrient, row.amount, row.nrvPercent):
                if field:
                    used.update(field.sourceRefs)
    for claim in updated.facts.claims:
        used.update(claim.text.sourceRefs)
        if claim.quantity:
            used.update(claim.quantity.sourceRefs)
    for barcode in updated.facts.barcodes:
        used.update(barcode.sourceRefs)
    updated.unassignedEvidenceIds = [entry.id for entry in updated.evidence if entry.id not in used]
    validate_document(updated)
    return updated


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import Codex visual table proposals for human review")
    parser.add_argument("--document", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--candidates", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != ".json" or args.output.exists() or args.output.resolve() in {
        args.document.resolve(), args.candidates.resolve(), args.image.resolve()
    }:
        parser.error("Output must be a new .json file, separate from all inputs")
    doc = document_from_dict(json.loads(args.document.read_text(encoding="utf-8")))
    proposed = json.loads(args.candidates.read_text(encoding="utf-8"))
    updated = apply_vision_candidates(doc, args.image.read_bytes(), proposed)
    args.output.write_text(json.dumps(to_dict(updated), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Unverified vision candidates saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
