"""Canonical Product, Evidence, Candidate, Graph and cache identities."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .models import FactCandidate, ProductEntity
from .normalization import normalize_text
from .field_registry import field_definition


IDENTITY_VERSION = 2


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_id(prefix: str, value: Any, *, length: int = 24) -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:length]}"


def canonical_value(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, list):
        return [canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): canonical_value(item) for key, item in sorted(value.items())}
    return value


def product_id_for(*, sku: str | None = None, model: str | None = None,
                   variant: str | None = None) -> str | None:
    if sku and normalize_text(sku):
        return stable_id("product", {"v": IDENTITY_VERSION, "sku": normalize_text(sku)})
    if model and normalize_text(model):
        payload: dict[str, Any] = {"v": IDENTITY_VERSION, "model": normalize_text(model)}
        if variant and normalize_text(variant):
            payload["variant"] = normalize_text(variant)
        return stable_id("product", payload)
    return None


def graph_identity(candidate: FactCandidate) -> tuple[str, str, str]:
    # Public graph helpers still accept legacy v1 candidates. The engine's v2
    # resolver always supplies a product_id; unresolved direct callers retain
    # the former one-dataset behavior for compatibility.
    product = candidate.product_id or "legacy_dataset_product"
    return product, candidate.field, candidate.scope or ""


def identity_needs_review(candidate: FactCandidate) -> bool:
    # No product_id denotes the legacy direct graph API, not a v2 unresolved
    # entity. Engine-produced candidates always have an explicit status/id.
    return bool(candidate.product_id and candidate.product_identity_status in {"ambiguous", "unresolved"})


def product_label(sku: str | None, model: str | None, variant: str | None, fallback: str) -> str:
    return sku or " / ".join(part for part in (model, variant) if part) or fallback


def evidence_identity(candidate: FactCandidate) -> tuple[str, str, str, str, str, str]:
    """Semantic identity for duplicate evidence, including unit and scope."""
    return (
        candidate.source_block_id,
        candidate.product_id or "",
        candidate.field,
        candidate.scope or "",
        candidate.normalized_unit or "",
        canonical_json(canonical_value(candidate.normalized_value)),
    )


def candidate_identity(candidate: FactCandidate) -> str:
    return stable_id("candidate", {
        "v": IDENTITY_VERSION,
        "evidence": evidence_identity(candidate),
        "raw": normalize_text(candidate.raw_value),
        "method": candidate.extraction_method,
    }, length=20)


def conflict_group_id(product_id: str | None, field: str, scope: str | None) -> str:
    return stable_id("group", {
        "v": IDENTITY_VERSION,
        "product": product_id or "",
        "field": field,
        "scope": scope or "",
    }, length=20)


def _candidate_text(candidate: FactCandidate) -> str:
    # Product labels retain source casing for review/export; identity hashing
    # separately applies normalize_text.
    return str(candidate.raw_value if candidate.raw_value is not None else candidate.normalized_value)


def _row_key(candidate: FactCandidate) -> str:
    structured = candidate.provenance.get("structured_row")
    if isinstance(structured, dict) and structured.get("row_id"):
        return f"row:{structured['row_id']}"
    return f"block:{candidate.source_block_id}"


def _identity_values(items: Iterable[FactCandidate]) -> tuple[str | None, str | None, str | None]:
    fields: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if item.field in {"sku", "model", "variant"}:
            value = _candidate_text(item).strip()
            if value and normalize_text(value) not in {normalize_text(existing) for existing in fields[item.field]}:
                fields[item.field].append(value)
        structured = item.provenance.get("structured_row")
        values = structured.get("identity") if isinstance(structured, dict) else None
        if isinstance(values, dict):
            for field in ("sku", "model", "variant"):
                value = str(values.get(field) or "").strip()
                if value and normalize_text(value) not in {normalize_text(existing) for existing in fields[field]}:
                    fields[field].append(value)
    return tuple(values[0] if len(values) == 1 else None for values in (
        fields["sku"], fields["model"], fields["variant"]
    ))  # type: ignore[return-value]


def resolve_product_identities(
    candidates: Iterable[FactCandidate], *, dataset_root: str | Path
) -> list[ProductEntity]:
    """Assign conservative, deterministic identities and refresh candidate IDs.

    Explicit SKU wins.  A structured row is the next strongest boundary, then
    a unique model within one source.  If a dataset contains several products,
    unowned evidence stays source-local and ambiguous instead of being merged.
    """
    rows = list(candidates)
    by_row: dict[str, list[FactCandidate]] = defaultdict(list)
    by_file: dict[str, list[FactCandidate]] = defaultdict(list)
    for candidate in rows:
        by_row[_row_key(candidate)].append(candidate)
        by_file[candidate.source_file].append(candidate)

    resolved: dict[int, tuple[str, str | None, str | None, str | None, str]] = {}
    uncertain: set[int] = set()
    for items in by_row.values():
        sku, model, variant = _identity_values(items)
        structured_rows = [item.provenance.get("structured_row") for item in items]
        row_identities = [row["identity"] for row in structured_rows
                          if isinstance(row, dict) and isinstance(row.get("identity"), dict)]
        if any(identity.get("_ambiguous_identity") or
               (not sku and identity.get("_ambiguous_variant")) for identity in row_identities):
            uncertain.update(id(item) for item in items)
            continue
        product_id = product_id_for(sku=sku, model=model, variant=variant)
        if product_id:
            status = "explicit_sku" if sku else "explicit_model_variant" if variant else "explicit_model"
            for candidate in items:
                resolved[id(candidate)] = (product_id, sku, model, variant, status)

    # A file containing exactly one explicit product may safely lend that
    # identity to its other lines. Multiple row products never do.
    for items in by_file.values():
        identities = {resolved[id(item)][0] for item in items if id(item) in resolved}
        if len(identities) == 1:
            product_id = next(iter(identities))
            anchor = next(resolved[id(item)] for item in items if id(item) in resolved)
            for candidate in items:
                if id(candidate) not in uncertain:
                    resolved.setdefault(id(candidate), (*anchor[:4], "derived_from_unique_source_identity"))

    explicit_products = {value[0] for value in resolved.values()}
    dataset_token = stable_id("product", {
        "v": IDENTITY_VERSION,
        "dataset": str(Path(dataset_root).expanduser().resolve()).casefold(),
    })
    for candidate in rows:
        if id(candidate) in resolved:
            continue
        if id(candidate) in uncertain:
            ambiguous = stable_id("product", {"v": IDENTITY_VERSION, "ambiguous_row": _row_key(candidate)})
            resolved[id(candidate)] = (ambiguous, None, None, None, "ambiguous")
        elif len(explicit_products) == 1:
            only = next(iter(explicit_products))
            anchor = next(value for value in resolved.values() if value[0] == only)
            resolved[id(candidate)] = (*anchor[:4], "derived_from_single_dataset_product")
        elif not explicit_products:
            resolved[id(candidate)] = (dataset_token, None, None, None, "dataset_default")
        else:
            ambiguous = stable_id("product", {
                "v": IDENTITY_VERSION,
                "ambiguous_source": candidate.source_file,
            })
            resolved[id(candidate)] = (ambiguous, None, None, None, "ambiguous")

    # Without row boundaries, differing configured specifications are only
    # evidence of *possible* variants. Do not invent variants, but do not
    # present the broad model bucket as a proven same-product conflict either.
    possible_dimensions: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for candidate in rows:
        product_id, sku, model, variant, status = resolved[id(candidate)]
        spec = field_definition(candidate.field)
        if sku or not model or variant or not spec or not spec.review_unstructured_variants:
            continue
        if any(re.match(rf"^\s*(?:[-*•]\s*)?{re.escape(header)}(?:\s*[（(][^()（）]+[)）])?(?:\s*[:：=]|\s+)",
                        candidate.raw_text, re.IGNORECASE) for header in spec.variant_headers):
            possible_dimensions[(product_id, candidate.field, candidate.scope or "")].add(
                canonical_json([candidate.normalized_value, candidate.normalized_unit]))
    uncertain_models = {key[0] for key, values in possible_dimensions.items() if len(values) > 1}

    for candidate in rows:
        product_id, sku, model, variant, status = resolved[id(candidate)]
        if product_id in uncertain_models:
            status = "ambiguous"
        candidate.product_id = product_id
        candidate.product_sku = sku
        candidate.product_model = model
        candidate.product_variant = variant
        candidate.product_identity_status = status
        candidate.identity_version = IDENTITY_VERSION
        candidate.candidate_id = candidate_identity(candidate)
    return product_entities_from_candidates(rows, dataset_name=Path(dataset_root).name)


def product_entities_from_candidates(
    candidates: Iterable[FactCandidate], *, dataset_name: str = "Dataset"
) -> list[ProductEntity]:
    entities: dict[str, ProductEntity] = {}
    for candidate in candidates:
        if not candidate.product_id:
            continue
        label = product_label(candidate.product_sku, candidate.product_model, candidate.product_variant,
            dataset_name if candidate.product_identity_status == "dataset_default" else "Ambiguous product"
        )
        entity = entities.setdefault(candidate.product_id, ProductEntity(
            product_id=candidate.product_id,
            label=label,
            sku=candidate.product_sku,
            model=candidate.product_model,
            identity_status=candidate.product_identity_status,
        ))
        if candidate.product_variant and candidate.product_variant not in entity.variants:
            entity.variants.append(candidate.product_variant)
        entity.candidate_ids.append(candidate.candidate_id)
        if entity.sku is None and candidate.product_sku:
            entity.sku = candidate.product_sku
            entity.label = candidate.product_sku
        if entity.model is None and candidate.product_model:
            entity.model = candidate.product_model
    for entity in entities.values():
        entity.variants.sort(key=normalize_text)
        entity.candidate_ids.sort()
    return sorted(entities.values(), key=lambda item: (normalize_text(item.label), item.product_id))
