"""Stable, provider-neutral data contract for label checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BoxPx:
    left: int
    top: int
    width: int
    height: int


@dataclass
class ImageInfo:
    id: str
    fileName: str
    widthPx: int
    heightPx: int


@dataclass
class Evidence:
    id: str
    imageId: str | None
    sourceType: str
    text: str
    boxPx: BoxPx | None
    ocrConfidence: float | None


@dataclass
class FieldValue:
    raw: str | None
    value: str | None
    unit: str | None
    sourceRefs: list[str]
    status: str
    reviewReasons: list[str] = field(default_factory=list)

    @classmethod
    def missing(cls) -> FieldValue:
        return cls(None, None, None, [], "missing")


@dataclass
class Basis:
    kind: str
    raw: str | None
    sourceRefs: list[str]
    status: str
    servingSize: FieldValue | None = None


@dataclass
class NutritionRow:
    nutrient: FieldValue
    amount: FieldValue
    nrvPercent: FieldValue | None


@dataclass
class NutritionTable:
    id: str
    basis: Basis
    rows: list[NutritionRow]
    sourceRefs: list[str] = field(default_factory=list)


@dataclass
class TableDiagnostic:
    code: str
    status: str
    message: str
    imageId: str
    boxPx: BoxPx | None
    evidenceRefs: list[str] = field(default_factory=list)
    tableId: str | None = None


@dataclass
class Claim:
    id: str
    category: str
    text: FieldValue
    quantity: FieldValue | None
    basis: str


@dataclass
class Facts:
    productName: FieldValue = field(default_factory=FieldValue.missing)
    netContent: FieldValue = field(default_factory=FieldValue.missing)
    ingredientsText: FieldValue = field(default_factory=FieldValue.missing)
    allergenText: FieldValue = field(default_factory=FieldValue.missing)
    nutritionTables: list[NutritionTable] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    barcodes: list[FieldValue] = field(default_factory=list)


@dataclass
class LabelDocument:
    schemaVersion: str
    documentId: str
    productId: str | None
    images: list[ImageInfo]
    evidence: list[Evidence]
    facts: Facts
    unassignedEvidenceIds: list[str]
    productIdSource: str = "unknown"
    tableDiagnostics: list[TableDiagnostic] = field(default_factory=list)


@dataclass
class RuleRef:
    id: str
    version: str


@dataclass
class CheckResult:
    checkId: str
    status: str
    message: str
    inputPaths: list[str]
    evidenceRefs: list[str]
    ruleRef: RuleRef | None = None
    observed: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None


@dataclass
class DerivedValue:
    kind: str
    value: str
    unit: str
    basis: str
    inputPaths: list[str]
    evidenceRefs: list[str]
    ruleRef: RuleRef | None = None


@dataclass
class CheckReport:
    schemaVersion: str
    documentId: str
    productId: str | None
    status: str
    results: list[CheckResult]
    derived: list[DerivedValue]
    ruleNotice: str
