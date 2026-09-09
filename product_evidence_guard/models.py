from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass(slots=True)
class SourceBlock:
    """One traceable fragment read from a source file."""

    block_id: str
    source_file: str
    source_kind: str
    file_hash: str
    locator: dict[str, Any]
    text: str
    recognition_confidence: float = 1.0
    extraction_method: str = "deterministic_parser"
    recognition_confidence_source: str = "deterministic"
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceBlock":
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass(slots=True)
class FactCandidate:
    """Traceable evidence. Its decision status records human actions only."""

    candidate_id: str
    field: str
    field_label: str
    raw_value: str
    normalized_value: Any
    normalized_unit: str | None
    source_block_id: str
    source_file: str
    source_kind: str
    file_hash: str
    locator: dict[str, Any]
    raw_text: str
    recognition_confidence: float
    mapping_confidence: float
    extraction_method: str
    scope: str | None = None
    notes: list[str] = field(default_factory=list)
    mapping_confidence_source: str = "deterministic"
    status: str = "pending"
    provenance: dict[str, Any] = field(default_factory=dict)
    product_id: str | None = None
    product_sku: str | None = None
    product_model: str | None = None
    product_variant: str | None = None
    product_identity_status: str = "unresolved"
    identity_version: int = 2
    source_current: bool = True

    @property
    def decision_status(self) -> str:
        return "undecided" if self.status == "pending" else self.status

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "decision_status": self.decision_status}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FactCandidate":
        data = dict(data)
        if "status" not in data and "decision_status" in data:
            data["status"] = "pending" if data["decision_status"] == "undecided" else data["decision_status"]
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass(slots=True)
class FactGroup:
    field: str
    field_label: str
    classification: str
    severity: str
    reason: str
    candidate_ids: list[str]
    normalized_values: list[Any]
    recognition_confidence: float
    mapping_confidence: float
    evidence_consistency: float
    recommendation: str
    product_id: str | None = None
    product_label: str | None = None
    scope: str | None = None
    group_id: str = ""
    fact_status: str = "pending_confirmation"
    verification_method: str | None = None
    selected_value: Any = None
    selected_unit: str | None = None
    independent_source_count: int = 0
    verified_candidate_ids: list[str] = field(default_factory=list)
    review_reason_code: str | None = None
    human_approved: bool = False
    current: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CrossFieldRelation:
    relation_type: str
    fields: list[str]
    severity: str
    reason: str
    candidate_ids: list[str]
    product_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ProductEntity:
    product_id: str
    label: str
    sku: str | None = None
    model: str | None = None
    variants: list[str] = field(default_factory=list)
    identity_status: str = "explicit"
    candidate_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ClaimCandidate:
    """A statement found in generated content; never a confirmed fact."""

    claim_id: str
    raw_text: str
    line: int
    field: str | None
    field_label: str | None
    normalized_value: Any = None
    normalized_unit: str | None = None
    scope: str | None = None
    product_id: str | None = None
    status: str = "needs_review"
    evidence_ids: list[str] = field(default_factory=list)
    reason: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
