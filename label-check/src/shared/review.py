"""Human-review queue and a conservative gate for future checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .models import Basis, CheckResult, FieldValue, LabelDocument


def _fields(doc: LabelDocument):
    facts = doc.facts
    for name in ("productName", "netContent", "ingredientsText", "allergenText"):
        yield f"facts.{name}", getattr(facts, name)
    for i, table in enumerate(facts.nutritionTables):
        base = f"facts.nutritionTables[{i}]"
        yield base + ".basis", table.basis
        if table.basis.servingSize is not None:
            yield base + ".basis.servingSize", table.basis.servingSize
        for j, row in enumerate(table.rows):
            prefix = f"{base}.rows[{j}]"
            yield prefix + ".nutrient", row.nutrient
            yield prefix + ".amount", row.amount
            if row.nrvPercent is not None:
                yield prefix + ".nrvPercent", row.nrvPercent
    for i, claim in enumerate(facts.claims):
        yield f"facts.claims[{i}].text", claim.text
        if claim.quantity is not None:
            yield f"facts.claims[{i}].quantity", claim.quantity
    for i, barcode in enumerate(facts.barcodes):
        yield f"facts.barcodes[{i}]", barcode


def collect_review_items(doc: LabelDocument) -> list[dict[str, Any]]:
    """Locate uncertain structured fields; no automatic correction is performed."""
    evidence = {item.id: item for item in doc.evidence}
    items = []
    for path, field in _fields(doc):
        if field.status != "needs_review":
            continue
        if isinstance(field, Basis):
            if field.kind == "per_serving" and field.servingSize and field.servingSize.status == "needs_review":
                continue  # The serving-size field already identifies the same OCR line.
            reasons = ["BASIS_UNKNOWN"] if field.kind == "unknown" else ["OCR_TABLE_HEADER_UNCERTAIN"]
        else:
            reasons = field.reviewReasons
        refs = [evidence[ref] for ref in field.sourceRefs]
        items.append({
            "path": path,
            "raw": field.raw,
            "value": field.kind if isinstance(field, Basis) else field.value,
            "unit": None if isinstance(field, Basis) else field.unit,
            "reviewReasons": reasons,
            "evidence": [{
                "id": entry.id,
                "imageId": entry.imageId,
                "boxPx": vars(entry.boxPx) if entry.boxPx else None,
                "ocrConfidence": entry.ocrConfidence,
            } for entry in refs],
        })
    return items


def gate_check(check_id: str, inputs: Mapping[str, FieldValue | Basis]) -> CheckResult | None:
    """Return a blocking result, or None when all inputs are ready for a future check."""
    blocked = [(path, field) for path, field in inputs.items() if field.status != "ready"]
    if not blocked:
        return None
    missing = any(field.status == "missing" for _, field in blocked)
    refs = list(dict.fromkeys(ref for _, field in inputs.items() for ref in field.sourceRefs))
    return CheckResult(
        checkId=check_id,
        status="insufficient_data" if missing else "needs_review",
        message="必要输入缺失" if missing else "输入证据待人工复核",
        inputPaths=[path for path, _ in blocked],
        evidenceRefs=refs,
    )
