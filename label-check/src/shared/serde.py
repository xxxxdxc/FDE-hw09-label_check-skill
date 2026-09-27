"""JSON boundary. Numeric quantities are decimal strings in the wire format."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

from .models import (
    Basis, BoxPx, Claim, Evidence, Facts, FieldValue, ImageInfo,
    LabelDocument, NutritionRow, NutritionTable,
)


def to_dict(value):
    return asdict(value) if is_dataclass(value) else value


def _field(data):
    return None if data is None else FieldValue(**data)


def document_from_dict(data: dict) -> LabelDocument:
    facts_data = data["facts"]
    tables = []
    for item in facts_data.get("nutritionTables", []):
        basis_data = item["basis"].copy()
        basis_data["servingSize"] = _field(basis_data.get("servingSize"))
        rows = [NutritionRow(
            nutrient=_field(row["nutrient"]),
            amount=_field(row["amount"]),
            nrvPercent=_field(row.get("nrvPercent")),
        ) for row in item["rows"]]
        tables.append(NutritionTable(item["id"], Basis(**basis_data), rows, item.get("sourceRefs", [])))
    claims = [Claim(
        id=item["id"], category=item["category"],
        text=_field(item["text"]), quantity=_field(item.get("quantity")),
        basis=item["basis"],
    ) for item in facts_data.get("claims", [])]
    facts = Facts(
        productName=_field(facts_data["productName"]),
        netContent=_field(facts_data["netContent"]),
        ingredientsText=_field(facts_data["ingredientsText"]),
        allergenText=_field(facts_data["allergenText"]),
        nutritionTables=tables, claims=claims,
        barcodes=[_field(item) for item in facts_data.get("barcodes", [])],
    )
    evidence = [Evidence(
        id=item["id"], imageId=item["imageId"],
        sourceType=item["sourceType"], text=item["text"],
        boxPx=BoxPx(**item["boxPx"]) if item["boxPx"] is not None else None,
        ocrConfidence=item["ocrConfidence"],
    ) for item in data["evidence"]]
    return LabelDocument(
        schemaVersion=data["schemaVersion"], documentId=data["documentId"],
        productId=data["productId"],
        images=[ImageInfo(**item) for item in data["images"]],
        evidence=evidence, facts=facts,
        unassignedEvidenceIds=data["unassignedEvidenceIds"],
        productIdSource=data.get("productIdSource", "provided" if data.get("productId") else "unknown"),
    )
