"""Runtime validation of the shared contract; TypedDict alone would not do this."""

from decimal import Decimal, InvalidOperation
import re

from .models import FieldValue, LabelDocument
from .confidence import LOW_OCR_CONFIDENCE

FIELD_STATES = {"ready", "needs_review", "missing"}
CHECK_STATES = {"pass", "fail", "needs_review", "insufficient_data", "not_applicable"}
BASIS_KINDS = {"per_serving", "per_100g", "per_100ml", "unknown"}
UNITS = {"g", "mg", "µg", "kg", "mL", "L", "kJ", "kcal", "%", "piece", "bag"}
DECIMAL_PATTERN = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d+)?$")


def decimal_value(value: str) -> Decimal:
    if not isinstance(value, str) or not DECIMAL_PATTERN.fullmatch(value):
        raise ValueError(f"Expected a non-negative decimal string, got {value!r}")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal string: {value!r}") from exc


def validate_document(doc: LabelDocument) -> None:
    if doc.schemaVersion != "1.0" or not doc.documentId:
        raise ValueError("Unsupported schemaVersion or missing documentId")
    if doc.productIdSource not in {"provided", "ocr_front_title", "human_reviewed_title", "unknown"}:
        raise ValueError("Invalid productIdSource")
    if doc.productIdSource == "unknown" and doc.productId is not None:
        raise ValueError("Unknown productId source cannot have a productId")
    image_ids = {image.id for image in doc.images}
    if len(image_ids) != len(doc.images):
        raise ValueError("Duplicate image IDs")
    for image in doc.images:
        if image.widthPx <= 0 or image.heightPx <= 0:
            raise ValueError("Invalid image dimensions")
    images = {image.id: image for image in doc.images}
    ids = {entry.id for entry in doc.evidence}
    if len(ids) != len(doc.evidence):
        raise ValueError("Duplicate evidence IDs")
    for entry in doc.evidence:
        if entry.sourceType not in {"baidu_accurate", "baidu_table_v2", "codex_vision", "manual"}:
            raise ValueError(f"Unknown evidence source: {entry.sourceType}")
        if entry.sourceType == "manual":
            if entry.imageId is not None or entry.boxPx is not None or entry.ocrConfidence is not None:
                raise ValueError("Manual evidence cannot claim image coordinates or OCR confidence")
        elif entry.imageId not in images:
            raise ValueError(f"Unknown imageId: {entry.imageId}")
        if entry.sourceType in {"baidu_table_v2", "codex_vision"} and entry.ocrConfidence is not None:
            raise ValueError("Table V2 and vision candidates do not provide OCR confidence")
        if entry.ocrConfidence is not None and not 0 <= entry.ocrConfidence <= 1:
            raise ValueError("ocrConfidence must lie in [0, 1]")
        if entry.boxPx is not None:
            box, image = entry.boxPx, images[entry.imageId]
            if (min(box.left, box.top) < 0 or min(box.width, box.height) <= 0 or
                box.left + box.width > image.widthPx or box.top + box.height > image.heightPx):
                raise ValueError(f"Evidence box outside original image: {entry.id}")
    evidence_by_id = {entry.id: entry for entry in doc.evidence}

    def uncertain_ocr(refs):
        has_manual_review = any(evidence_by_id[ref].sourceType == "manual" for ref in refs)
        if has_manual_review:
            return False
        accurate = [evidence_by_id[ref] for ref in refs if evidence_by_id[ref].sourceType == "baidu_accurate"]
        if any(e.ocrConfidence is None or e.ocrConfidence < LOW_OCR_CONFIDENCE for e in accurate):
            return True
        # A vision candidate is never verified without a separate human record.
        if any(evidence_by_id[ref].sourceType == "codex_vision" for ref in refs):
            return True
        # V2 has no confidence score; it needs a high-confidence accurate line.
        return (any(evidence_by_id[ref].sourceType == "baidu_table_v2" for ref in refs)
                and not accurate)

    def check_field(value: FieldValue, path: str, numeric=False, unit=False):
        if value.status not in FIELD_STATES:
            raise ValueError(f"Invalid field status at {path}")
        if any(ref not in ids for ref in value.sourceRefs):
            raise ValueError(f"Unresolved sourceRefs at {path}")
        if value.unit is not None and value.unit not in UNITS:
            raise ValueError(f"Unknown unit at {path}: {value.unit}")
        if value.status == "missing":
            if value.raw is not None or value.value is not None:
                raise ValueError(f"Missing field has data at {path}")
        elif value.status == "ready":
            if value.raw is None or value.value is None or not value.sourceRefs:
                raise ValueError(f"Ready field lacks value or source at {path}")
            if unit and value.unit is None:
                raise ValueError(f"Ready field lacks unit at {path}")
            if uncertain_ocr(value.sourceRefs):
                raise ValueError(f"Ready field has uncertain OCR evidence at {path}")
        if numeric and value.value is not None:
            decimal_value(value.value)

    facts = doc.facts
    check_field(facts.productName, "facts.productName")
    check_field(facts.netContent, "facts.netContent", numeric=True, unit=True)
    check_field(facts.ingredientsText, "facts.ingredientsText")
    check_field(facts.allergenText, "facts.allergenText")
    for i, table in enumerate(facts.nutritionTables):
        if any(ref not in ids for ref in table.sourceRefs):
            raise ValueError(f"Unresolved table sourceRefs in table {i}")
        if table.basis.kind not in BASIS_KINDS or table.basis.status not in FIELD_STATES:
            raise ValueError(f"Invalid nutrition basis in table {i}")
        if any(ref not in ids for ref in table.basis.sourceRefs):
            raise ValueError(f"Unresolved nutrition basis refs in table {i}")
        if table.basis.status == "ready" and uncertain_ocr(table.basis.sourceRefs):
            raise ValueError(f"Ready nutrition basis has uncertain OCR evidence in table {i}")
        if table.basis.kind == "per_serving":
            if table.basis.servingSize is None:
                raise ValueError("per_serving requires servingSize")
            check_field(table.basis.servingSize, f"table[{i}].servingSize", numeric=True, unit=True)
        for j, row in enumerate(table.rows):
            prefix = f"table[{i}].rows[{j}]"
            check_field(row.nutrient, prefix + ".nutrient")
            check_field(row.amount, prefix + ".amount", numeric=True, unit=True)
            if row.nrvPercent is not None:
                check_field(row.nrvPercent, prefix + ".nrvPercent", numeric=True, unit=True)
                if row.nrvPercent.unit not in {"%", None}:
                    raise ValueError("NRV percent unit must be %")
    for i, claim in enumerate(facts.claims):
        if claim.basis not in {"per_piece", "per_bag", "per_serving", "per_100g", "unknown", "none"}:
            raise ValueError("Invalid claim basis")
        check_field(claim.text, f"claims[{i}].text")
        if claim.quantity is not None:
            check_field(claim.quantity, f"claims[{i}].quantity", numeric=True, unit=True)
    for i, barcode in enumerate(facts.barcodes):
        check_field(barcode, f"barcodes[{i}]")
        if barcode.status == "ready" and not re.fullmatch(r"\d{13}", barcode.value):
            raise ValueError(f"Invalid ready barcode at {i}")
    if doc.productIdSource in {"ocr_front_title", "human_reviewed_title"}:
        titles = [claim.text for claim in facts.claims if claim.category == "front_title"]
        if not any(title.status == "ready" and title.value == doc.productId for title in titles):
            raise ValueError("Title-derived productId must match a ready front-title claim")
    if any(ref not in ids for ref in doc.unassignedEvidenceIds):
        raise ValueError("Unknown unassignedEvidenceId")
    for diagnostic in doc.tableDiagnostics:
        if diagnostic.status not in {"ok", "needs_vision", "needs_review"}:
            raise ValueError("Invalid table diagnostic status")
        if diagnostic.imageId not in images or any(ref not in ids for ref in diagnostic.evidenceRefs):
            raise ValueError("Invalid table diagnostic reference")
        if diagnostic.boxPx is not None:
            box, image = diagnostic.boxPx, images[diagnostic.imageId]
            if (min(box.left, box.top) < 0 or min(box.width, box.height) <= 0 or
                box.left + box.width > image.widthPx or box.top + box.height > image.heightPx):
                raise ValueError("Table diagnostic box outside original image")
