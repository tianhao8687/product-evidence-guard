from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from math import isfinite
from pathlib import Path
import re
import time
from typing import Any, Protocol

from .extractor import FIELD_SPECS, infer_semantic_scope
from .model_output_schema import (
    FieldMappingItem,
    ModelOutputIssue,
    VisualTranscriptionItem,
)
from .models import FactCandidate, SourceBlock
from .normalization import normalize_value
from .parsers import sha256_file
from .qwen_vl_reader import (
    QwenVlReadResult,
    QwenVlReader,
    deterministic_field_mappings,
    OCR_REVIEW_PROMPT_REVISION,
    ocr_review_template,
)
from .recognition_cache import RecognitionCache, bind_image_result
from .review_focus import choose_review_region


RAPIDOCR_VERSION = "3.9.1"
RAPIDOCR_MODEL_ID = "RapidOCR/PP-OCRv6-small-OpenVINO"
FAST_RECOGNITION_THRESHOLD = 0.97
SPATIALLY_VERIFIED_RECOGNITION_THRESHOLD = 0.88
MAX_OCR_LINES = 128
MAX_OCR_LINE_CHARS = 500
MAX_IMAGE_PIXELS = 40_000_000

_RAPIDOCR_MODEL_HASHES = {
    "PP-OCRv6_det_small.onnx": (
        "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f"
    ),
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": (
        "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"
    ),
    "PP-OCRv6_rec_small.onnx": (
        "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"
    ),
}
_FIELD_SPECS_BY_NAME = {spec.name: spec for spec in FIELD_SPECS}
_TARGET_MARKERS = tuple(
    sorted(
        {
            alias.casefold()
            for spec in FIELD_SPECS
            for alias in spec.aliases
        }
        | {
            "input",
            "output",
            "输入",
            "输出",
            "entrada",
            "salida",
            "net wt",
        },
        key=len,
        reverse=True,
    )
)
_KNOWN_MEASUREMENT_RE = re.compile(
    r"(?<![A-Za-z0-9.\-])[-+]?\d+(?:[.,]\d+)?"
    r"(?:\s*(?:-|–|—|~|to|至|到)\s*[-+]?\d+(?:[.,]\d+)?)?\s*"
    r"(?:mAh|Ah|mV|V|mA|A|kW|mW|W|mg|kg|g|oz|lbs?|mL|L|mm|cm|in|inch|"
    r"毫安时|安时|伏特|伏|毫安|安培|安|千瓦|毫瓦|瓦|毫克|千克|公斤|克|"
    r"盎司|磅|毫升|升|毫米|厘米|英寸)(?![A-Za-z])",
    re.IGNORECASE,
)
_UNIT_LIKE_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9.\-])[-+]?\d+(?:[.,]\d+)?\s*([A-Za-z]{1,6})(?![A-Za-z])"
)
_ALLOWED_UNIT_SUFFIXES = {
    "a",
    "ac",
    "ah",
    "cm",
    "dc",
    "g",
    "hz",
    "in",
    "inch",
    "kg",
    "khz",
    "kw",
    "kwh",
    "l",
    "lb",
    "lbs",
    "m",
    "ma",
    "mah",
    "mg",
    "mhz",
    "ml",
    "mm",
    "mv",
    "mw",
    "oz",
    "pc",
    "pcs",
    "v",
    "vac",
    "vdc",
    "w",
    "wh",
}
_DIMENSION_CHAIN_RE = re.compile(
    r"[-+]?\d+(?:[.,]\d+)?"
    r"(?:\s*[x×]\s*[-+]?\d+(?:[.,]\d+)?){1,3}\s*"
    r"(?:mm|cm|m|in|inch|毫米|厘米|米|英寸)\b",
    re.IGNORECASE,
)
_BARE_LINEAR_DIMENSION_RE = re.compile(
    r"\s*[-+]?\d+(?:[.,]\d+)?\s*"
    r"(?:mm|cm|m|in|inch|毫米|厘米|米|英寸)\s*",
    re.IGNORECASE,
)
_SHORT_VALUE_FRAGMENT_RE = re.compile(
    r"\s*(?:"
    r"[-+]?\d+(?:[.,]\d+)?"
    r"|(?:mAh|Ah|mV|V|mA|A|kW|mW|W|mg|kg|g|oz|lbs?|mL|L|mm|cm|in|inch)"
    r")\s*",
    re.IGNORECASE,
)
_SERIES_CONTEXT_RE = re.compile(
    r"(?:\b(?:by|per)\s+(?:operating\s+)?"
    r"(?:mode|profile|variant)\b|"
    r"按(?:工作)?模式|(?:每种|不同)(?:工作)?模式)",
    re.IGNORECASE,
)
_SERIES_FIELD_VALUE_PATTERNS: dict[str, re.Pattern[str]] = {
    "voltage": re.compile(
        r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?\s*"
        r"(?:mV|V)(?:ac|dc)?(?![A-Za-z])",
        re.IGNORECASE,
    ),
    "current": re.compile(
        r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?\s*(?:mA|A)(?![A-Za-z])",
        re.IGNORECASE,
    ),
    "power": re.compile(
        r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)?\s*(?:mW|kW|W)(?![A-Za-z])",
        re.IGNORECASE,
    ),
}
_WEIGHT_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.\-])[-+]?\d+(?:[.,]\d+)?\s*"
    r"(?:mg|kg|g|oz|lbs?)(?![A-Za-z])",
    re.IGNORECASE,
)
_LINEAR_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.\-])[-+]?\d+(?:[.,]\d+)?\s*"
    r"(?:mm|cm|m|in|inch|毫米|厘米|米|英寸)(?![A-Za-z])",
    re.IGNORECASE,
)
_QUANTITY_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.\-])[-+]?\d+(?:[.,]\d+)?\s*"
    r"(?:pcs?|pieces?|件|个|套)?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CHARGE_CAPACITY_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.\-])[-+]?\d+(?:[.,]\d+)?\s*"
    r"(?:mAh|Ah)(?![A-Za-z])",
    re.IGNORECASE,
)
_VOLUME_CAPACITY_VALUE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9.\-])[-+]?\d+(?:[.,]\d+)?\s*"
    r"(?:mL|L|毫升|升)(?![A-Za-z])",
    re.IGNORECASE,
)
_NUMERIC_FIELD_VALUE_PATTERNS: dict[str, re.Pattern[str]] = {
    **_SERIES_FIELD_VALUE_PATTERNS,
    "net_weight": _WEIGHT_VALUE_PATTERN,
    "gross_weight": _WEIGHT_VALUE_PATTERN,
    "weight": _WEIGHT_VALUE_PATTERN,
    "dimensions": _DIMENSION_CHAIN_RE,
    "length": _LINEAR_VALUE_PATTERN,
    "width": _LINEAR_VALUE_PATTERN,
    "height": _LINEAR_VALUE_PATTERN,
    "quantity": _QUANTITY_VALUE_PATTERN,
    "capacity_charge": _CHARGE_CAPACITY_VALUE_PATTERN,
    "capacity_volume": _VOLUME_CAPACITY_VALUE_PATTERN,
    "capacity": re.compile(
        rf"(?:{_CHARGE_CAPACITY_VALUE_PATTERN.pattern}|"
        rf"{_VOLUME_CAPACITY_VALUE_PATTERN.pattern})",
        re.IGNORECASE,
    ),
}
_PLAIN_SERIES_LABEL_RE = re.compile(
    r"[A-Za-z\u3400-\u9fff][A-Za-z0-9\u3400-\u9fff]*"
    r"(?:[\s_/-][A-Za-z0-9\u3400-\u9fff]+){0,1}"
)
_SERIES_LABEL_STOPWORDS = {
    "input",
    "output",
    "power",
    "voltage",
    "current",
    "mode",
    "profile",
    "variant",
    "rated",
    "nominal",
    "minimum",
    "maximum",
    "min",
    "max",
}
_EXPLICIT_PRODUCT_FACT_LABEL_RE = re.compile(
    r"^\s*(?:[-*•]\s*)?(?:"
    r"(?:product\s+|商品|产品)?(?:net\s+(?:weight|wt)|gross\s+weight|"
    r"shipping\s+weight|weight|dimensions?|size|model(?:\s+no)?|sku|"
    r"material|colou?r|quantity|qty|count)"
    r"|(?:产品净重|商品净重|净含量|净重|包装重量|整箱重量|毛重|"
    r"单个重量|产品重量|商品重量|重量|产品尺寸|包装尺寸|规格尺寸|"
    r"外形尺寸|尺寸|产品型号|商品型号|型号|主要材质|产品材质|材料|"
    r"材质|产品颜色|颜色|套装数量|包装数量|件数|数量)\s*[:：=|]?"
    r")",
    re.IGNORECASE,
)
_OBSERVATION_OVERRIDE_FIELDS = {
    "net_weight",
    "gross_weight",
    "weight",
    "dimensions",
    "quantity",
    "model",
    "material",
    "color",
}
_IO_DIRECTION_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:input|output|entrada|salida)"
    r"(?![A-Za-z0-9_])|输入|输出|입력|출력",
    re.IGNORECASE,
)
OBSERVATION_FACT_OVERRIDE_REASON = (
    "observation_mode_cancelled_explicit_product_fact"
)


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    confidence: float
    bbox_1000: tuple[int, int, int, int] | None = None
    grouping_kind: str | None = None
    component_count: int = 1


class OcrBackend(Protocol):
    model_id: str
    device: str

    def read(self, image_path: Path) -> list[OcrLine]:
        ...


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RapidOcrOpenVinoBackend:
    """RapidOCR with explicit, wheel-bundled model files and no auto-download."""

    model_id = RAPIDOCR_MODEL_ID
    device = "CPU"

    def __init__(self) -> None:
        try:
            import rapidocr
            from PIL import Image
            from rapidocr import EngineType, RapidOCR
        except ImportError as exc:  # pragma: no cover - installation contract
            raise RuntimeError(
                "本地 OCR 依赖未安装；请重新运行 scripts\\install-env.ps1。"
            ) from exc

        if str(getattr(rapidocr, "__version__", RAPIDOCR_VERSION)) != RAPIDOCR_VERSION:
            raise RuntimeError("RapidOCR 版本与锁定环境不一致。")
        package_root = Path(rapidocr.__file__).resolve().parent
        model_root = package_root / "models"
        model_paths = {
            name: model_root / name for name in _RAPIDOCR_MODEL_HASHES
        }
        for name, expected_hash in _RAPIDOCR_MODEL_HASHES.items():
            model_path = model_paths[name]
            if not model_path.is_file():
                raise RuntimeError(f"RapidOCR 内置模型缺失：{name}")
            if _sha256_file(model_path) != expected_hash:
                raise RuntimeError(f"RapidOCR 内置模型完整性校验失败：{name}")

        params = {
            "Global.log_level": "critical",
            "Global.model_root_dir": str(model_root),
            "Det.engine_type": EngineType.OPENVINO,
            "Det.model_path": str(model_paths["PP-OCRv6_det_small.onnx"]),
            "Cls.engine_type": EngineType.OPENVINO,
            "Cls.model_path": str(
                model_paths["ch_ppocr_mobile_v2.0_cls_mobile.onnx"]
            ),
            "Rec.engine_type": EngineType.OPENVINO,
            "Rec.model_path": str(model_paths["PP-OCRv6_rec_small.onnx"]),
        }
        self._Image = Image
        self._engine = RapidOCR(params=params)

    def read(self, image_path: Path) -> list[OcrLine]:
        with self._Image.open(image_path) as image:
            width, height = image.size
        if (
            width <= 0
            or height <= 0
            or width * height > MAX_IMAGE_PIXELS
        ):
            raise ValueError(
                f"Image pixel limit exceeded: {width}x{height} "
                f"(limit {MAX_IMAGE_PIXELS})"
            )

        output = self._engine(image_path)
        raw_texts = getattr(output, "txts", None)
        raw_scores = getattr(output, "scores", None)
        raw_boxes = getattr(output, "boxes", None)
        texts = list(raw_texts) if raw_texts is not None else []
        scores = list(raw_scores) if raw_scores is not None else []
        boxes = list(raw_boxes) if raw_boxes is not None else []
        line_count = min(len(texts), len(scores), len(boxes), MAX_OCR_LINES)
        lines: list[OcrLine] = []
        for index in range(line_count):
            text = str(texts[index]).strip()
            try:
                confidence = float(scores[index])
            except (TypeError, ValueError):
                continue
            if (
                not text
                or len(text) > MAX_OCR_LINE_CHARS
                or not isfinite(confidence)
                or not 0.0 <= confidence <= 1.0
            ):
                continue
            bbox = _normalize_bbox(boxes[index], width=width, height=height)
            lines.append(
                OcrLine(
                    text=text,
                    confidence=confidence,
                    bbox_1000=bbox,
                )
            )
        return lines


def _normalize_bbox(
    raw_box: Any,
    *,
    width: int,
    height: int,
) -> tuple[int, int, int, int] | None:
    try:
        points = list(raw_box)
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs or not ys or any(not isfinite(value) for value in (*xs, *ys)):
        return None
    left = max(0, min(1000, round(min(xs) * 1000 / width)))
    top = max(0, min(1000, round(min(ys) * 1000 / height)))
    right = max(0, min(1000, round(max(xs) * 1000 / width)))
    bottom = max(0, min(1000, round(max(ys) * 1000 / height)))
    if left >= right or top >= bottom:
        return None
    return left, top, right, bottom


def _bbox_union(lines: list[OcrLine]) -> tuple[int, int, int, int] | None:
    boxes = [line.bbox_1000 for line in lines if line.bbox_1000 is not None]
    if len(boxes) != len(lines) or not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def _vertical_overlap_ratio(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> float:
    overlap = max(0, min(first[3], second[3]) - max(first[1], second[1]))
    shorter = max(1, min(first[3] - first[1], second[3] - second[1]))
    return overlap / shorter


def _field_alias_in_text(text: str) -> str | None:
    lowered = text.casefold()
    for spec in FIELD_SPECS:
        if spec.name not in _SERIES_FIELD_VALUE_PATTERNS:
            continue
        if any(alias.casefold() in lowered for alias in spec.aliases):
            return spec.name
    return None


def _has_any_field_alias(text: str) -> bool:
    lowered = text.casefold()
    return any(
        alias.casefold() in lowered
        for spec in FIELD_SPECS
        for alias in spec.aliases
    )


def _mapping_exists_for_text(text: str, confidence: float) -> bool:
    transcription = VisualTranscriptionItem(
        id="spatial-probe",
        raw_text=text,
        bbox_1000=None,
        position_precision="unavailable",
        legibility="clear",
        confidence_estimate=confidence,
        confidence_source="rapidocr_recognizer_score",
    )
    return bool(deterministic_field_mappings([transcription]))


def _numeric_field_alias_in_text(text: str) -> str | None:
    for alias, field in sorted(
        (
            (alias, spec.name)
            for spec in FIELD_SPECS
            if spec.name in _NUMERIC_FIELD_VALUE_PATTERNS
            for alias in spec.aliases
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        prefix = (
            r"(?<![A-Za-z0-9_])"
            if alias and alias[0].isascii() and alias[0].isalnum()
            else ""
        )
        suffix = (
            r"(?![A-Za-z0-9_])"
            if alias and alias[-1].isascii() and alias[-1].isalnum()
            else ""
        )
        if re.search(
            prefix + re.escape(alias) + suffix,
            text,
            re.IGNORECASE,
        ):
            return field
    return None


def _has_ambiguous_same_field_measurements(text: str) -> bool:
    field = _numeric_field_alias_in_text(text)
    pattern = (
        _NUMERIC_FIELD_VALUE_PATTERNS.get(field)
        if field is not None
        else None
    )
    if pattern is None:
        return False
    values: set[str] = set()
    for match in pattern.finditer(text):
        raw_value = match.group(0).strip()
        normalized = normalize_value(field, raw_value)
        values.add(
            json.dumps(
                [normalized.value, normalized.unit],
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return len(values) > 1


def _has_multiple_io_directions(text: str) -> bool:
    directions = {
        (
            "input"
            if match.group(0).casefold()
            in {"input", "entrada", "输入", "입력"}
            else "output"
        )
        for match in _IO_DIRECTION_TOKEN_RE.finditer(text)
    }
    return len(directions) > 1


def _is_short_value_fragment(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 48:
        return False
    return (
        _SHORT_VALUE_FRAGMENT_RE.fullmatch(stripped) is not None
        or _KNOWN_MEASUREMENT_RE.search(stripped) is not None
        or _DIMENSION_CHAIN_RE.search(stripped) is not None
    )


def _coalesce_label_value_rows(lines: list[OcrLine]) -> list[OcrLine]:
    positioned = [
        (index, line)
        for index, line in enumerate(lines)
        if line.bbox_1000 is not None
    ]
    row_clusters: list[list[tuple[int, OcrLine]]] = []
    for index, line in sorted(
        positioned,
        key=lambda item: (
            (item[1].bbox_1000[1] + item[1].bbox_1000[3]) / 2,
            item[1].bbox_1000[0],
        ),
    ):
        box = line.bbox_1000
        assert box is not None
        matching: list[tuple[int, OcrLine]] | None = None
        best_overlap = 0.0
        for cluster in row_clusters:
            cluster_box = _bbox_union([item[1] for item in cluster])
            overlap = (
                _vertical_overlap_ratio(cluster_box, box)
                if cluster_box is not None
                else 0.0
            )
            if overlap >= 0.4 and overlap > best_overlap:
                matching = cluster
                best_overlap = overlap
        if matching is None:
            row_clusters.append([(index, line)])
        else:
            matching.append((index, line))

    replacements: dict[int, OcrLine] = {}
    consumed: set[int] = set()
    for cluster in row_clusters:
        ordered = sorted(cluster, key=lambda item: item[1].bbox_1000[0])
        segments: list[list[tuple[int, OcrLine]]] = []
        for item in ordered:
            if not segments:
                segments.append([item])
                continue
            previous_box = segments[-1][-1][1].bbox_1000
            current_box = item[1].bbox_1000
            assert previous_box is not None and current_box is not None
            if current_box[0] - previous_box[2] <= 180:
                segments[-1].append(item)
            else:
                segments.append([item])
        for segment in segments:
            if len(segment) < 2:
                continue
            parts = [item[1] for item in segment]
            combined_text = " ".join(part.text.strip() for part in parts)
            if not _has_any_field_alias(combined_text):
                continue
            if not any(
                _is_short_value_fragment(part.text)
                and not _has_any_field_alias(part.text)
                for part in parts
            ):
                continue
            if _has_ambiguous_same_field_measurements(combined_text):
                for index, part in segment:
                    replacements[index] = OcrLine(
                        text=part.text,
                        confidence=part.confidence,
                        bbox_1000=part.bbox_1000,
                        grouping_kind="ambiguous_label_value_row",
                        component_count=part.component_count,
                    )
                continue
            combined_confidence = min(part.confidence for part in parts)
            if not _mapping_exists_for_text(
                combined_text,
                combined_confidence,
            ):
                continue
            first_index = min(item[0] for item in segment)
            replacements[first_index] = OcrLine(
                text=combined_text,
                confidence=combined_confidence,
                bbox_1000=_bbox_union(parts),
                grouping_kind="label_value_row",
                component_count=sum(part.component_count for part in parts),
            )
            consumed.update(item[0] for item in segment)

    return [
        replacements[index]
        if index in replacements
        else line
        for index, line in enumerate(lines)
        if index not in consumed or index in replacements
    ]


def _coalesce_mode_series(lines: list[OcrLine]) -> list[OcrLine]:
    consumed: set[int] = set()
    replacements: list[OcrLine] = []
    for header_index, header in enumerate(lines):
        if header_index in consumed:
            continue
        header_box = header.bbox_1000
        field = _field_alias_in_text(header.text)
        if (
            header_box is None
            or field is None
            or _SERIES_CONTEXT_RE.search(header.text) is None
        ):
            continue
        value_pattern = _SERIES_FIELD_VALUE_PATTERNS[field]
        value_rows: list[tuple[int, OcrLine]] = []
        for value_index, value_line in enumerate(lines):
            value_box = value_line.bbox_1000
            if (
                value_index == header_index
                or value_index in consumed
                or value_box is None
                or value_pattern.search(value_line.text) is None
                or value_box[1] < header_box[1]
                or value_box[1] - header_box[3] > 180
            ):
                continue
            value_center = (value_box[0] + value_box[2]) / 2
            header_width = header_box[2] - header_box[0]
            if not (
                header_box[0] - 80
                <= value_center
                <= header_box[2] + max(160, header_width)
            ):
                continue
            value_rows.append((value_index, value_line))

        pairs: list[tuple[int, OcrLine, int, OcrLine]] = []
        used_labels: set[int] = set()
        for value_index, value_line in sorted(
            value_rows,
            key=lambda item: item[1].bbox_1000[0],
        ):
            value_box = value_line.bbox_1000
            assert value_box is not None
            value_center = (value_box[0] + value_box[2]) / 2
            label_choices: list[tuple[float, int, OcrLine]] = []
            for label_index, label_line in enumerate(lines):
                label_box = label_line.bbox_1000
                normalized_label = label_line.text.strip().casefold()
                if (
                    label_index in used_labels
                    or label_index == header_index
                    or label_index == value_index
                    or label_box is None
                    or _PLAIN_SERIES_LABEL_RE.fullmatch(
                        label_line.text.strip()
                    )
                    is None
                    or normalized_label in _SERIES_LABEL_STOPWORDS
                    or label_box[1] < value_box[3] - 5
                    or label_box[1] - value_box[3] > 120
                ):
                    continue
                label_center = (label_box[0] + label_box[2]) / 2
                horizontal_distance = abs(label_center - value_center)
                if horizontal_distance <= 80:
                    label_choices.append(
                        (
                            horizontal_distance
                            + max(0, label_box[1] - value_box[3]),
                            label_index,
                            label_line,
                        )
                    )
            if label_choices:
                _, label_index, label_line = min(
                    label_choices,
                    key=lambda item: item[0],
                )
                used_labels.add(label_index)
                pairs.append(
                    (
                        value_index,
                        value_line,
                        label_index,
                        label_line,
                    )
                )

        unique_labels = {
            label_line.text.strip().casefold()
            for _, _, _, label_line in pairs
        }
        if (
            len(pairs) < 2
            or len(pairs) != len(value_rows)
            or len(unique_labels) != len(pairs)
        ):
            continue
        ordered_pairs = sorted(
            pairs,
            key=lambda item: item[1].bbox_1000[0],
        )
        value_centers = [
            (value_line.bbox_1000[0] + value_line.bbox_1000[2]) / 2
            for _, value_line, _, _ in ordered_pairs
        ]
        label_centers = [
            (label_line.bbox_1000[0] + label_line.bbox_1000[2]) / 2
            for _, _, _, label_line in ordered_pairs
        ]
        value_y_centers = [
            (value_line.bbox_1000[1] + value_line.bbox_1000[3]) / 2
            for _, value_line, _, _ in ordered_pairs
        ]
        label_y_centers = [
            (label_line.bbox_1000[1] + label_line.bbox_1000[3]) / 2
            for _, _, _, label_line in ordered_pairs
        ]
        if (
            any(
                current <= previous
                for previous, current in zip(
                    label_centers,
                    label_centers[1:],
                )
            )
            or max(value_y_centers) - min(value_y_centers) > 80
            or max(label_y_centers) - min(label_y_centers) > 80
        ):
            continue

        plausible_label_indices: set[int] = set()
        for label_index, label_line in enumerate(lines):
            label_box = label_line.bbox_1000
            normalized_label = label_line.text.strip().casefold()
            if (
                label_box is None
                or _PLAIN_SERIES_LABEL_RE.fullmatch(
                    label_line.text.strip()
                )
                is None
                or normalized_label in _SERIES_LABEL_STOPWORDS
            ):
                continue
            label_center = (label_box[0] + label_box[2]) / 2
            for _, value_line in value_rows:
                value_box = value_line.bbox_1000
                assert value_box is not None
                value_center = (value_box[0] + value_box[2]) / 2
                if (
                    label_box[1] >= value_box[3] - 5
                    and label_box[1] - value_box[3] <= 120
                    and abs(label_center - value_center) <= 80
                ):
                    plausible_label_indices.add(label_index)
                    break
        if plausible_label_indices != used_labels:
            continue

        ambiguous_nearest = False
        for _, value_line, label_index, _ in ordered_pairs:
            value_box = value_line.bbox_1000
            assert value_box is not None
            value_center = (value_box[0] + value_box[2]) / 2
            distances = sorted(
                abs(
                    (
                        lines[index].bbox_1000[0]
                        + lines[index].bbox_1000[2]
                    )
                    / 2
                    - value_center
                )
                for index in plausible_label_indices
            )
            chosen_box = lines[label_index].bbox_1000
            assert chosen_box is not None
            chosen_distance = abs(
                (chosen_box[0] + chosen_box[2]) / 2 - value_center
            )
            if (
                not distances
                or chosen_distance != distances[0]
                or (
                    len(distances) > 1
                    and distances[1] - distances[0] < 15
                )
            ):
                ambiguous_nearest = True
                break
        if ambiguous_nearest:
            continue
        consumed.add(header_index)
        for (
            value_index,
            value_line,
            label_index,
            label_line,
        ) in ordered_pairs:
            components = [header, value_line, label_line]
            replacements.append(
                OcrLine(
                    text=(
                        f"{header.text.strip()} {value_line.text.strip()} "
                        f"{label_line.text.strip()}"
                    ),
                    confidence=min(item.confidence for item in components),
                    bbox_1000=_bbox_union(components),
                    grouping_kind="mode_series",
                    component_count=sum(
                        item.component_count for item in components
                    ),
                )
            )
            consumed.update({value_index, label_index})

    remaining = [
        line for index, line in enumerate(lines) if index not in consumed
    ]
    return sorted(
        [*remaining, *replacements],
        key=lambda line: (
            line.bbox_1000[1] if line.bbox_1000 else 1001,
            line.bbox_1000[0] if line.bbox_1000 else 1001,
            line.text,
        ),
    )


def _coalesce_spatial_ocr_lines(lines: list[OcrLine]) -> list[OcrLine]:
    return _coalesce_mode_series(_coalesce_label_value_rows(lines))


def _line_requires_review(line: OcrLine) -> bool:
    threshold = (
        SPATIALLY_VERIFIED_RECOGNITION_THRESHOLD
        if line.grouping_kind in {"label_value_row", "mode_series"}
        and line.component_count >= 2
        else FAST_RECOGNITION_THRESHOLD
    )
    return line.confidence < threshold


def _looks_target_like(text: str) -> bool:
    lowered = text.casefold()
    return (
        any(marker in lowered for marker in _TARGET_MARKERS)
        or _KNOWN_MEASUREMENT_RE.search(text) is not None
    )


def _has_unknown_unit_like_token(text: str) -> bool:
    dimension_spans = [
        match.span() for match in _DIMENSION_CHAIN_RE.finditer(text)
    ]
    for match in _UNIT_LIKE_TOKEN_RE.finditer(text):
        suffix = match.group(1).casefold()
        if suffix in _ALLOWED_UNIT_SUFFIXES:
            continue
        if suffix == "x" and any(
            start <= match.start() and match.end() <= end
            for start, end in dimension_spans
        ):
            continue
        return True
    return False


def _to_transcriptions(lines: list[OcrLine]) -> list[VisualTranscriptionItem]:
    return [
        VisualTranscriptionItem(
            id=f"ocr-{index:03d}",
            raw_text=line.text,
            bbox_1000=line.bbox_1000,
            position_precision=(
                "approximate" if line.bbox_1000 is not None else "unavailable"
            ),
            legibility=(
                "clear"
                if line.confidence >= FAST_RECOGNITION_THRESHOLD
                else "uncertain"
            ),
            confidence_estimate=line.confidence,
            confidence_source=(
                "rapidocr_spatial_group_min_score"
                if line.grouping_kind is not None
                else "rapidocr_recognizer_score"
            ),
        )
        for index, line in enumerate(lines, start=1)
    ]


def _explicit_product_fact_subset(
    target_lines: list[OcrLine],
    transcriptions: list[VisualTranscriptionItem],
    mappings: list[FieldMappingItem],
) -> tuple[
    list[OcrLine],
    list[VisualTranscriptionItem],
    list[FieldMappingItem],
]:
    allowed_mappings = [
        mapping
        for mapping in mappings
        if mapping.field in _OBSERVATION_OVERRIDE_FIELDS
    ]
    mapped_ids = {
        mapping.transcription_id for mapping in allowed_mappings
    }
    explicit_ids = {
        transcription.id
        for transcription in transcriptions
        if transcription.id in mapped_ids
        and _EXPLICIT_PRODUCT_FACT_LABEL_RE.match(
            transcription.raw_text
        )
        is not None
    }
    if not explicit_ids:
        return [], [], []
    pairs = [
        (line, transcription)
        for line, transcription in zip(target_lines, transcriptions)
        if transcription.id in explicit_ids
    ]
    return (
        [line for line, _ in pairs],
        [transcription for _, transcription in pairs],
        [
            mapping
            for mapping in allowed_mappings
            if mapping.transcription_id in explicit_ids
        ],
    )


def _has_distinct_line_mapping_conflict(
    transcriptions: list[VisualTranscriptionItem],
    mappings: list[FieldMappingItem],
) -> bool:
    transcription_by_id = {item.id: item for item in transcriptions}
    values_by_scope: dict[
        tuple[str, str | None],
        dict[str, set[str]],
    ] = {}
    for mapping in mappings:
        transcription = transcription_by_id.get(mapping.transcription_id)
        if transcription is None:
            continue
        normalized = normalize_value(mapping.field, mapping.raw_value)
        scope = (
            infer_semantic_scope(mapping.field, transcription.raw_text)
            or _FIELD_SPECS_BY_NAME[mapping.field].scope
        )
        normalized_key = json.dumps(
            [normalized.value, normalized.unit],
            ensure_ascii=False,
            sort_keys=True,
        )
        values_by_scope.setdefault((mapping.field, scope), {}).setdefault(
            normalized_key,
            set(),
        ).add(mapping.transcription_id)
    return any(
        len(values) > 1
        and len(
            {
                transcription_id
                for transcription_ids in values.values()
                for transcription_id in transcription_ids
            }
        )
        > 1
        for values in values_by_scope.values()
    )


def _routing_reasons(
    *,
    all_lines: list[OcrLine],
    target_lines: list[OcrLine],
    transcriptions: list[VisualTranscriptionItem],
    mappings: list[FieldMappingItem],
) -> list[str]:
    reasons: list[str] = []
    if not target_lines:
        if len(all_lines) > 2:
            reasons.append("dense_text_without_safe_target")
        return reasons
    if not mappings:
        reasons.append("target_text_without_safe_mapping")
    mapped_transcription_ids = {
        mapping.transcription_id for mapping in mappings
    }
    low_confidence_requires_review = any(
        _line_requires_review(line)
        and not (
            transcription.id not in mapped_transcription_ids
            and _BARE_LINEAR_DIMENSION_RE.fullmatch(line.text) is not None
        )
        for line, transcription in zip(target_lines, transcriptions)
    )
    if low_confidence_requires_review:
        reasons.append("low_ocr_confidence_on_target")
    if any(_has_unknown_unit_like_token(line.text) for line in target_lines):
        reasons.append("unknown_unit_like_token")
    if any(
        line.grouping_kind == "ambiguous_label_value_row"
        or _has_ambiguous_same_field_measurements(line.text)
        for line in target_lines
    ):
        reasons.append("conflicting_ocr_values")
    if any(_has_multiple_io_directions(line.text) for line in target_lines):
        reasons.append("multiple_io_scopes_in_one_line")

    transcription_by_id = {item.id: item for item in transcriptions}
    values_by_scope: dict[tuple[str, str | None], set[str]] = {}
    for mapping in mappings:
        transcription = transcription_by_id.get(mapping.transcription_id)
        if transcription is None:
            continue
        normalized = normalize_value(mapping.field, mapping.raw_value)
        scope = (
            infer_semantic_scope(mapping.field, transcription.raw_text)
            or _FIELD_SPECS_BY_NAME[mapping.field].scope
        )
        values_by_scope.setdefault((mapping.field, scope), set()).add(
            json.dumps(
                [normalized.value, normalized.unit],
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    if any(len(values) > 1 for values in values_by_scope.values()):
        reasons.append("conflicting_ocr_values")
    return list(dict.fromkeys(reasons))


def _block_id(
    *,
    source_file: str,
    file_hash: str,
    transcription: VisualTranscriptionItem,
) -> str:
    payload = json.dumps(
        {
            "source_file": source_file,
            "file_hash": file_hash,
            "transcription_id": transcription.id,
            "raw_text": transcription.raw_text,
            "bbox_1000": transcription.bbox_1000,
            "reader": RAPIDOCR_MODEL_ID,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _candidate_id(block: SourceBlock, mapping: FieldMappingItem) -> str:
    payload = (
        f"{block.block_id}\0{mapping.field}\0{mapping.raw_value}\0"
        f"{mapping.transcription_id}\0rapidocr"
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:20]


def _serialize_ocr_observations(
    lines: list[OcrLine],
) -> list[dict[str, Any]]:
    return [
        {
            "raw_text": line.text,
            "recognition_confidence": round(line.confidence, 6),
            "confidence_source": "rapidocr_recognizer_score",
            "bbox_1000": (
                list(line.bbox_1000)
                if line.bbox_1000 is not None
                else None
            ),
        }
        for line in lines[:MAX_OCR_LINES]
    ]


def _build_fast_result(
    *,
    image_path: Path,
    root_path: Path,
    transcriptions: list[VisualTranscriptionItem],
    mappings: list[FieldMappingItem],
    route: str,
    ocr_seconds: float,
    ocr_line_count: int,
    all_lines: list[OcrLine],
) -> QwenVlReadResult:
    relative = image_path.relative_to(root_path).as_posix()
    file_hash = sha256_file(image_path)
    result = QwenVlReadResult(
        image_file=relative,
        file_hash=file_hash,
        image_route=route,
        stage_timings={"ocr_seconds": round(ocr_seconds, 4)},
        ocr_line_count=ocr_line_count,
        ocr_observations=_serialize_ocr_observations(all_lines),
    )
    result.transcriptions.extend(transcriptions)
    result.mappings.extend(mappings)
    transcription_payload = {
        "schema_version": 1,
        "items": [item.to_dict() for item in transcriptions],
    }
    mapping_payload = {
        "schema_version": 1,
        "items": [item.to_dict() for item in mappings],
    }
    result.raw_transcription_output = json.dumps(
        transcription_payload,
        ensure_ascii=False,
    )
    result.transcription_json_payload = result.raw_transcription_output
    result.raw_mapping_output = json.dumps(mapping_payload, ensure_ascii=False)
    result.mapping_json_payload = result.raw_mapping_output

    for transcription in transcriptions:
        locator = {
            "image": relative,
            "transcription_id": transcription.id,
            "bbox_1000": (
                list(transcription.bbox_1000)
                if transcription.bbox_1000 is not None
                else None
            ),
            "position_precision": transcription.position_precision,
            "legibility": transcription.legibility,
            "recognition_confidence_source": transcription.confidence_source,
            "coordinate_source": (
                "rapidocr_spatial_group_union"
                if transcription.confidence_source
                == "rapidocr_spatial_group_min_score"
                else "rapidocr_detected_box"
            ),
            "model_id": RAPIDOCR_MODEL_ID,
            "inference_device": "CPU",
        }
        result.source_blocks.append(
            SourceBlock(
                block_id=_block_id(
                    source_file=relative,
                    file_hash=file_hash,
                    transcription=transcription,
                ),
                source_file=relative,
                source_kind="image_openvino_rapidocr",
                file_hash=file_hash,
                locator=locator,
                text=transcription.raw_text,
                recognition_confidence=transcription.confidence_estimate,
                extraction_method="rapidocr_openvino_transcription",
                recognition_confidence_source=transcription.confidence_source,
                provenance={
                    "model_id": RAPIDOCR_MODEL_ID,
                    "inference_device": "CPU",
                    "position_precision": transcription.position_precision,
                    "coordinate_source": locator["coordinate_source"],
                },
            )
        )

    blocks_by_transcription = {
        str(block.locator["transcription_id"]): block
        for block in result.source_blocks
    }
    for mapping in mappings:
        block = blocks_by_transcription.get(mapping.transcription_id)
        if block is None:
            continue
        spec = _FIELD_SPECS_BY_NAME[mapping.field]
        normalized = normalize_value(mapping.field, mapping.raw_value)
        notes = list(normalized.notes)
        notes.extend(
            [
                (
                    "recognition_confidence_source:"
                    f"{block.recognition_confidence_source}"
                ),
                "mapping_confidence_source:deterministic",
            ]
        )
        result.fact_candidates.append(
            FactCandidate(
                candidate_id=_candidate_id(block, mapping),
                field=mapping.field,
                field_label=spec.label,
                raw_value=mapping.raw_value,
                normalized_value=normalized.value,
                normalized_unit=normalized.unit,
                source_block_id=block.block_id,
                source_file=relative,
                source_kind=block.source_kind,
                file_hash=file_hash,
                locator=block.locator,
                raw_text=block.text,
                recognition_confidence=block.recognition_confidence,
                mapping_confidence=mapping.mapping_confidence_estimate,
                extraction_method="rapidocr_deterministic_mapping",
                scope=(
                    infer_semantic_scope(mapping.field, block.text)
                    or spec.scope
                ),
                notes=notes,
                mapping_confidence_source="deterministic",
                status="pending",
                provenance={
                    "model_id": RAPIDOCR_MODEL_ID,
                    "inference_device": "CPU",
                    "recognition_confidence_source": (
                        block.recognition_confidence_source
                    ),
                    "mapping_confidence_source": "deterministic",
                    "position_precision": block.locator.get(
                        "position_precision"
                    ),
                },
            )
        )
    return result


def _observation_unavailable_result(
    *,
    image_path: Path,
    root_path: Path,
    code: str,
    message: str,
) -> QwenVlReadResult:
    """Fail closed when OCR-only observation cannot be completed.

    Observation mode is a hard safety boundary: it must never fall through to
    the visual-language model or emit field mappings/fact candidates.
    """

    try:
        image_file = image_path.relative_to(root_path).as_posix()
    except ValueError:
        image_file = image_path.name
    try:
        file_hash = sha256_file(image_path)
    except OSError:
        file_hash = ""
    result = QwenVlReadResult(
        image_file=image_file,
        file_hash=file_hash,
        image_route="observations_unavailable",
        errors=[
            ModelOutputIssue(
                stage="visual_observation",
                code=code,
                message=message,
            )
        ],
        fallback_reasons=[code],
    )
    return result


class HybridImageReader:
    """OpenVINO OCR fast path with a local Qwen visual safety fallback."""

    def __init__(
        self,
        qwen_reader: QwenVlReader,
        *,
        ocr_backend: OcrBackend | None,
        ocr_unavailable_reason: str | None = None,
        enable_recognition_cache: bool = False,
        enable_review_region: bool = False,
    ) -> None:
        self._qwen_reader = qwen_reader
        self._ocr_backend = ocr_backend
        self._ocr_unavailable_reason = ocr_unavailable_reason
        self._cache_capable = enable_recognition_cache
        self.use_recognition_cache = enable_recognition_cache
        self.enable_review_region = enable_review_region
        self._recognition_cache = RecognitionCache()

    @property
    def optimization_revision(self):
        prompt_hash = hashlib.sha256(ocr_review_template().encode("utf-8")).hexdigest()[:16]
        region_mode = "context-band-v2" if self.enable_review_region else "full-image"
        return f"{OCR_REVIEW_PROMPT_REVISION}:{prompt_hash}:{region_mode}:resident-cache-v1"

    def warmup(self) -> None:
        """Compile the vision and generation path using a generated blank fixture."""
        from tempfile import TemporaryDirectory
        from PIL import Image
        with TemporaryDirectory(prefix="peg-warmup-") as temporary:
            path = Path(temporary) / "warmup.png"
            Image.new("RGB", (224, 224), "white").save(path)
            backend = self._qwen_reader._backend
            backend.generate("Describe this image in one word.", image=backend.load_image(path), max_new_tokens=4)

    @classmethod
    def from_openvino(
        cls,
        model_path: str | Path,
        device: str = "CPU",
        *,
        model_id: str | None = None,
    ) -> "HybridImageReader":
        qwen_reader = QwenVlReader.from_openvino(
            model_path,
            device=device,
            model_id=model_id,
        )
        try:
            ocr_backend: OcrBackend | None = RapidOcrOpenVinoBackend()
            unavailable_reason = None
        except Exception as exc:
            ocr_backend = None
            unavailable_reason = f"ocr_unavailable:{type(exc).__name__}"
        return cls(
            qwen_reader,
            ocr_backend=ocr_backend,
            ocr_unavailable_reason=unavailable_reason,
            enable_recognition_cache=True,
        )

    def analyze_image(
        self,
        image_path: str | Path,
        root: str | Path | None = None,
        *,
        deadline: float | None = None,
        observation_only: bool = False,
    ) -> QwenVlReadResult:
        started = time.perf_counter()
        path = Path(image_path).expanduser().resolve()
        root_path = Path(root).expanduser().resolve() if root is not None else path.parent
        if deadline is not None and started >= deadline:
            return QwenVlReadResult(image_file=path.name, file_hash="", errors=[
                ModelOutputIssue(stage="image_read", code="deadline_exceeded", message="图片核验已超过截止时间。")
            ])
        cache_ready = self._cache_capable and path.is_file() and path.is_relative_to(root_path)
        content_hash = sha256_file(path) if cache_ready else None
        backend = getattr(self._qwen_reader, "_backend", None)
        revision = self.optimization_revision
        key = (content_hash, observation_only, revision, self.enable_review_region,
               id(self._ocr_backend), id(backend), getattr(self._qwen_reader, "_visual_max_new_tokens", None),
               getattr(self._qwen_reader, "_mapping_max_new_tokens", None))
        cached = self._recognition_cache.get(key) if cache_ready and self.use_recognition_cache else None
        if cached is not None and (deadline is None or time.perf_counter() < deadline):
            result = bind_image_result(cached, path, root_path, content_hash)
            result.recognition_cache = {"hit": True, "scope": "resident_model", "original_route": cached.image_route}
            # Keep the recognizer identity for PDF/embedded-image provenance.
            # Cache reuse is execution metadata, not a different evidence source.
            result.stage_timings = {"cache_lookup_seconds": round(time.perf_counter() - started, 4),
                                    "ocr_seconds": 0.0, "visual_seconds": 0.0}
            unchanged = sha256_file(path) == content_hash
            if unchanged and (deadline is None or time.perf_counter() < deadline):
                return result
            if unchanged:
                return QwenVlReadResult(image_file=result.image_file, file_hash=content_hash, errors=[
                    ModelOutputIssue(stage="recognition_cache", code="deadline_exceeded", message="图片核验已超过截止时间。")
                ])
            return QwenVlReadResult(image_file=path.relative_to(root_path).as_posix(), file_hash="",
                errors=[ModelOutputIssue(stage="recognition_cache", code="source_changed", message="图片在缓存读取期间发生变化，请重试。")])
        result = self._analyze_uncached(path, root_path, deadline=deadline, observation_only=observation_only)
        if cache_ready and result.ok and result.file_hash == content_hash and (
            deadline is None or time.perf_counter() < deadline
        ) and sha256_file(path) == content_hash and self.optimization_revision == revision:
            result = bind_image_result(result, path, root_path, content_hash)
            result.recognition_cache = {"hit": False, "scope": "resident_model", "enabled": self.use_recognition_cache}
            if self.use_recognition_cache:
                self._recognition_cache.put(key, result)
        return result

    def _analyze_uncached(
        self, image_path: str | Path, root: str | Path | None = None, *,
        deadline: float | None = None, observation_only: bool = False,
    ) -> QwenVlReadResult:
        started = time.perf_counter()
        path = Path(image_path).expanduser().resolve()
        root_path = (
            Path(root).expanduser().resolve() if root is not None else path.parent
        )
        if self._ocr_backend is None:
            if observation_only:
                return _observation_unavailable_result(
                    image_path=path,
                    root_path=root_path,
                    code="ocr_unavailable",
                    message=(
                        "仅观察模式要求本地 OCR；OCR 不可用时不会回退到 "
                        "Qwen，也不会生成事实候选。"
                    ),
                )
            result = self._qwen_reader.analyze_image(
                path,
                root_path,
                deadline=deadline,
            )
            result.image_route = "qwen_deep_fallback"
            result.fallback_reasons.append(
                self._ocr_unavailable_reason or "ocr_unavailable"
            )
            return result
        try:
            path.relative_to(root_path)
        except ValueError:
            if observation_only:
                return _observation_unavailable_result(
                    image_path=path,
                    root_path=root_path,
                    code="image_outside_root",
                    message=(
                        "图片不在证据根目录内，仅观察模式已安全停止。"
                    ),
                )
            return self._qwen_reader.analyze_image(
                path,
                root_path,
                deadline=deadline,
            )

        ocr_started = time.perf_counter()
        try:
            all_lines = self._ocr_backend.read(path)
        except Exception as exc:
            if observation_only:
                return _observation_unavailable_result(
                    image_path=path,
                    root_path=root_path,
                    code="ocr_inference_failed",
                    message=(
                        "仅观察模式的 OCR 推理失败；未调用 Qwen，"
                        "也未生成事实候选。"
                    ),
                )
            result = self._qwen_reader.analyze_image(
                path,
                root_path,
                deadline=deadline,
            )
            result.image_route = "qwen_deep_fallback"
            result.fallback_reasons.append(
                f"ocr_inference_failed:{type(exc).__name__}"
            )
            return result
        ocr_seconds = time.perf_counter() - ocr_started
        semantic_lines = _coalesce_spatial_ocr_lines(all_lines)
        target_lines = [
            line for line in semantic_lines if _looks_target_like(line.text)
        ]
        transcriptions = _to_transcriptions(target_lines)
        mappings = deterministic_field_mappings(transcriptions)
        if observation_only:
            observation_transcriptions = transcriptions
            (
                explicit_lines,
                explicit_transcriptions,
                explicit_mappings,
            ) = _explicit_product_fact_subset(
                target_lines,
                transcriptions,
                mappings,
            )
            if not explicit_mappings:
                result = _build_fast_result(
                    image_path=path,
                    root_path=root_path,
                    transcriptions=transcriptions,
                    mappings=[],
                    route="ocr_observations",
                    ocr_seconds=ocr_seconds,
                    ocr_line_count=len(all_lines),
                    all_lines=all_lines,
                )
                result.fallback_reasons.extend(
                    _routing_reasons(
                        all_lines=all_lines,
                        target_lines=target_lines,
                        transcriptions=transcriptions,
                        mappings=mappings,
                    )
                )
                return result
            explicit_reasons = _routing_reasons(
                all_lines=all_lines,
                target_lines=explicit_lines,
                transcriptions=explicit_transcriptions,
                mappings=explicit_mappings,
            )
            safe_distinct_conflict = (
                explicit_reasons == ["conflicting_ocr_values"]
                and _has_distinct_line_mapping_conflict(
                    explicit_transcriptions,
                    explicit_mappings,
                )
                and not any(
                    _has_ambiguous_same_field_measurements(line.text)
                    or _has_multiple_io_directions(line.text)
                    for line in explicit_lines
                )
            )
            if not explicit_reasons or safe_distinct_conflict:
                result = _build_fast_result(
                    image_path=path,
                    root_path=root_path,
                    transcriptions=explicit_transcriptions,
                    mappings=explicit_mappings,
                    route=(
                        "ocr_fast_conflict"
                        if safe_distinct_conflict
                        else "ocr_fast"
                    ),
                    ocr_seconds=ocr_seconds,
                    ocr_line_count=len(all_lines),
                    all_lines=all_lines,
                )
                result.fallback_reasons.extend(explicit_reasons)
                result.fallback_reasons.append(
                    OBSERVATION_FACT_OVERRIDE_REASON
                )
                return result

            result = _build_fast_result(
                image_path=path,
                root_path=root_path,
                transcriptions=observation_transcriptions,
                mappings=[],
                route="ocr_observations",
                ocr_seconds=ocr_seconds,
                ocr_line_count=len(all_lines),
                all_lines=all_lines,
            )
            result.fallback_reasons.extend(explicit_reasons)
            result.fallback_reasons.append(
                "explicit_product_fact_not_promoted:"
                + ",".join(explicit_reasons)
            )
            return result
        reasons = _routing_reasons(
            all_lines=all_lines,
            target_lines=target_lines,
            transcriptions=transcriptions,
            mappings=mappings,
        )
        if not reasons:
            result = _build_fast_result(
                image_path=path,
                root_path=root_path,
                transcriptions=transcriptions,
                mappings=mappings,
                route=(
                    "ocr_fast"
                    if target_lines
                    else "ocr_fast_negative"
                ),
                ocr_seconds=ocr_seconds,
                ocr_line_count=len(all_lines),
                all_lines=all_lines,
            )
            return result

        hint_lines = target_lines or all_lines[:24]
        hints = [
            {"text": line.text, "score": round(line.confidence, 6)}
            for line in hint_lines[:24]
        ]
        review_started = time.perf_counter()
        region = (choose_review_region(path, all_lines, target_lines)
                  if self.enable_review_region and getattr(self._qwen_reader, "supports_review_region", False)
                  and "unknown_unit_like_token" not in reasons
                  and not any(_has_ambiguous_same_field_measurements(line.text)
                              or _has_multiple_io_directions(line.text) for line in target_lines)
                  else None)
        region_options = {"review_region": region} if region is not None else {}
        reviewed = self._qwen_reader.analyze_image(
            path,
            root_path,
            deadline=deadline,
            ocr_hints=hints,
            **region_options,
        )
        if region is not None:
            text_by_id = {line.id: line.raw_text for line in transcriptions}
            expected_fields = Counter(
                (mapping.field, infer_semantic_scope(mapping.field, text_by_id[mapping.transcription_id])
                 or _FIELD_SPECS_BY_NAME[mapping.field].scope)
                for mapping in mappings)
            actual_fields = Counter((candidate.field, candidate.scope) for candidate in reviewed.fact_candidates)
            expected_conflict = _has_distinct_line_mapping_conflict(transcriptions, mappings)
            actual_conflict = _has_distinct_line_mapping_conflict(reviewed.transcriptions, reviewed.mappings)
            if (not reviewed.ok or bool(expected_fields - actual_fields)
                    or len(reviewed.transcriptions) < len(target_lines)
                    or (expected_conflict and not actual_conflict)):
                # A crop must not silently remove a known field or conflict.
                region_seconds = time.perf_counter() - review_started
                reviewed = self._qwen_reader.analyze_image(path, root_path, deadline=deadline, ocr_hints=hints)
                reviewed.stage_timings["region_attempt_seconds"] = round(region_seconds, 4)
                reviewed.fallback_reasons.append("review_region_coverage_full_image_retry")
            else:
                reviewed.review_region["area_ratio"] = round((region[3] - region[1]) / 1000, 4)
        review_seconds = time.perf_counter() - review_started
        reviewed.image_route = "qwen_ocr_review"
        reviewed.ocr_line_count = len(all_lines)
        reviewed.ocr_observations = _serialize_ocr_observations(all_lines)
        reviewed.fallback_reasons.extend(reasons)
        reviewed.stage_timings["ocr_seconds"] = round(ocr_seconds, 4)
        reviewed.stage_timings["review_total_seconds"] = round(
            review_seconds,
            4,
        )
        reviewed.stage_timings["hybrid_total_seconds"] = round(
            time.perf_counter() - started,
            4,
        )
        if reviewed.ok:
            if not reviewed.fact_candidates:
                reviewed.fallback_reasons.append(
                    "guided_review_no_safe_candidate_no_deep_retry"
                )
            return reviewed

        deep = self._qwen_reader.analyze_image(
            path,
            root_path,
            deadline=deadline,
        )
        deep.image_route = "qwen_deep_fallback"
        deep.ocr_line_count = len(all_lines)
        deep.ocr_observations = _serialize_ocr_observations(all_lines)
        deep.fallback_reasons.extend(
            [*reasons, "guided_review_no_safe_candidate"]
        )
        deep.stage_timings["ocr_seconds"] = round(ocr_seconds, 4)
        deep.stage_timings["guided_review_seconds"] = round(
            review_seconds,
            4,
        )
        deep.stage_timings["hybrid_total_seconds"] = round(
            time.perf_counter() - started,
            4,
        )
        return deep
