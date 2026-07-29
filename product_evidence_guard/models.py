from __future__ import annotations

from dataclasses import asdict, dataclass, field
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceBlock":
        return cls(**data)


@dataclass(slots=True)
class FactCandidate:
    """A proposed product fact. Candidates never become confirmed facts automatically."""

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FactCandidate":
        return cls(**data)


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CrossFieldRelation:
    relation_type: str
    fields: list[str]
    severity: str
    reason: str
    candidate_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
