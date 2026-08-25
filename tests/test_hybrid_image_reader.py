from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.hybrid_image_reader import (
    HybridImageReader,
    OcrLine,
)
from product_evidence_guard.model_output_schema import ModelOutputIssue
from product_evidence_guard.models import FactCandidate
from product_evidence_guard.qwen_vl_reader import QwenVlReadResult


class FakeOcrBackend:
    model_id = "fake-ocr"
    device = "CPU"

    def __init__(
        self,
        lines: list[OcrLine] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.lines = lines or []
        self.error = error
        self.calls: list[Path] = []

    def read(self, image_path: Path) -> list[OcrLine]:
        self.calls.append(image_path)
        if self.error is not None:
            raise self.error
        return list(self.lines)


def _review_candidate() -> FactCandidate:
    return FactCandidate(
        candidate_id="reviewed",
        field="capacity_charge",
        field_label="电池容量",
        raw_value="1000mAh",
        normalized_value=1000,
        normalized_unit="mAh",
        source_block_id="review-block",
        source_file="label.jpg",
        source_kind="image_openvino_qwen_vl",
        file_hash="hash",
        locator={"position_precision": "unavailable"},
        raw_text="GSP 063450 1000mAh 3.7V",
        recognition_confidence=0.9,
        mapping_confidence=0.99,
        extraction_method="deterministic_unit_mapping",
        status="pending",
    )


class FakeQwenReader:
    def __init__(
        self,
        *,
        review_has_candidate: bool = True,
        review_has_error: bool = False,
    ) -> None:
        self.review_has_candidate = review_has_candidate
        self.review_has_error = review_has_error
        self.calls: list[dict[str, object]] = []

    def analyze_image(
        self,
        image_path: Path,
        root: Path,
        *,
        deadline: float | None = None,
        ocr_hints: list[dict[str, object]] | None = None,
    ) -> QwenVlReadResult:
        self.calls.append(
            {
                "image_path": image_path,
                "root": root,
                "deadline": deadline,
                "ocr_hints": ocr_hints,
            }
        )
        result = QwenVlReadResult(
            image_file=image_path.name,
            file_hash="hash",
        )
        if ocr_hints is not None and self.review_has_candidate:
            result.fact_candidates.append(_review_candidate())
        if ocr_hints is not None and self.review_has_error:
            result.errors.append(
                ModelOutputIssue(
                    stage="visual_review",
                    code="invalid_json",
                    message="invalid",
                )
            )
        return result


class HybridImageReaderTests(unittest.TestCase):
    def _image(self, root: Path) -> Path:
        image = root / "label.jpg"
        image.write_bytes(b"test-image")
        return image

    def test_clear_electrical_lines_use_ocr_fast_path(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine(
                    "INPUT:120V 60Hz 9W",
                    0.992,
                    (10, 20, 500, 80),
                ),
                OcrLine(
                    "OUTPUT:9V DC 500mA",
                    0.995,
                    (10, 100, 500, 160),
                ),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "ocr_fast")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(len(result.ocr_observations), 2)
        self.assertEqual(
            result.ocr_observations[0]["raw_text"],
            "INPUT:120V 60Hz 9W",
        )
        self.assertEqual(
            result.ocr_observations[0]["bbox_1000"],
            [10, 20, 500, 80],
        )
        self.assertEqual(
            {(item.field, item.scope) for item in result.fact_candidates},
            {
                ("voltage", "input"),
                ("power", "input"),
                ("voltage", "output"),
                ("current", "output"),
            },
        )
        self.assertTrue(
            all(item.status == "pending" for item in result.fact_candidates)
        )
        self.assertTrue(
            all(
                item.source_kind == "image_openvino_rapidocr"
                for item in result.fact_candidates
            )
        )

    def test_spatial_label_values_and_mode_series_avoid_qwen(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine("NET WEIGHT", 0.98, (70, 220, 180, 250)),
                OcrLine("320", 0.999, (290, 216, 338, 261)),
                OcrLine("g", 0.999, (333, 224, 352, 259)),
                OcrLine("INPUT VOLTAGE", 0.98, (70, 347, 205, 377)),
                OcrLine("12 V", 0.96, (290, 341, 345, 384)),
                OcrLine("80 mm", 0.92, (846, 441, 902, 476)),
                OcrLine("RATED POWER", 0.99, (70, 472, 200, 502)),
                OcrLine("65 W", 0.99, (289, 466, 351, 509)),
                OcrLine("DIMENSIONS", 0.999, (70, 597, 180, 627)),
                OcrLine(
                    "120 x 80 x 28 mm",
                    0.96,
                    (292, 593, 480, 632),
                ),
                OcrLine("POWER BY MODE (W)", 0.99, (580, 773, 755, 806)),
                OcrLine("30 W", 0.89, (599, 847, 644, 880)),
                OcrLine("45 W", 0.95, (711, 831, 758, 867)),
                OcrLine("65 W", 0.97, (824, 810, 871, 847)),
                OcrLine("ECO", 0.999, (592, 921, 636, 958)),
                OcrLine("BALANCED", 0.999, (707, 923, 798, 955)),
                OcrLine("TURBO", 0.999, (819, 923, 881, 955)),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "ocr_fast")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(len(result.ocr_observations), 17)
        self.assertTrue(
            any(
                item.raw_text == "NET WEIGHT 320 g"
                and item.confidence_source
                == "rapidocr_spatial_group_min_score"
                for item in result.transcriptions
            )
        )
        power_scopes = {
            item.scope
            for item in result.fact_candidates
            if item.field == "power"
        }
        self.assertTrue(
            {
                "profile:eco",
                "profile:balanced",
                "profile:turbo",
            }.issubset(power_scopes)
        )
        self.assertFalse(result.fallback_reasons)

    def test_spatial_row_with_two_power_values_requires_review(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine("POWER", 0.99, (70, 200, 180, 240)),
                OcrLine("30 W", 0.99, (270, 198, 330, 242)),
                OcrLine("65 W", 0.99, (350, 198, 410, 242)),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_ocr_review")
        self.assertEqual(len(qwen.calls), 1)
        self.assertIn("conflicting_ocr_values", result.fallback_reasons)

    def test_raw_line_with_two_power_values_requires_review(self) -> None:
        ocr = FakeOcrBackend(
            [OcrLine("POWER 30 W 65 W", 0.999, (70, 200, 410, 242))]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_ocr_review")
        self.assertEqual(len(qwen.calls), 1)
        self.assertIn("conflicting_ocr_values", result.fallback_reasons)

    def test_spatial_weight_row_with_two_values_requires_review(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine("NET WEIGHT", 0.999, (70, 200, 210, 242)),
                OcrLine("320 g", 0.999, (270, 198, 330, 242)),
                OcrLine("350 g", 0.999, (350, 198, 410, 242)),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_ocr_review")
        self.assertEqual(len(qwen.calls), 1)
        self.assertIn("conflicting_ocr_values", result.fallback_reasons)

    def test_line_with_input_and_output_requires_review(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine(
                    "INPUT: 12 V; OUTPUT: 5 V 2 A",
                    0.999,
                    (70, 200, 510, 242),
                )
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_ocr_review")
        self.assertEqual(len(qwen.calls), 1)
        self.assertIn(
            "multiple_io_scopes_in_one_line",
            result.fallback_reasons,
        )

    def test_mode_series_with_competing_label_requires_review(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine(
                    "POWER BY MODE (W)",
                    0.99,
                    (580, 773, 755, 806),
                ),
                OcrLine("30 W", 0.99, (599, 847, 644, 880)),
                OcrLine("45 W", 0.99, (711, 831, 758, 867)),
                OcrLine("65 W", 0.99, (824, 810, 871, 847)),
                OcrLine("ECO", 0.99, (592, 921, 636, 958)),
                OcrLine("PLUS", 0.99, (650, 920, 702, 958)),
                OcrLine(
                    "BALANCED",
                    0.99,
                    (696, 907, 774, 944),
                ),
                OcrLine("TURBO", 0.99, (824, 885, 879, 922)),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_ocr_review")
        self.assertEqual(len(qwen.calls), 1)
        self.assertIn(
            "target_text_without_safe_mapping",
            result.fallback_reasons,
        )

    def test_sparse_non_parameter_image_returns_fast_empty_result(self) -> None:
        ocr = FakeOcrBackend([OcrLine("Coca-Cola", 0.99)])
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "ocr_fast_negative")
        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(qwen.calls, [])

    def test_observation_only_mode_never_promotes_chart_values_or_calls_qwen(
        self,
    ) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine("Efficiency", 0.999, (10, 10, 200, 50)),
                OcrLine("Vin = 7 V", 0.90, (220, 80, 400, 120)),
                OcrLine("Output Current (mA)", 0.999, (10, 200, 400, 240)),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(
                self._image(root),
                root,
                observation_only=True,
            )

        self.assertEqual(result.image_route, "ocr_observations")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(len(result.ocr_observations), 3)

    def test_observation_selection_is_cancelled_for_explicit_product_label(
        self,
    ) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine("Efficiency", 0.999, (10, 10, 200, 50)),
                OcrLine("Vin = 7 V", 0.99, (220, 80, 400, 120)),
                OcrLine("NET WEIGHT: 320 g", 0.999, (20, 300, 400, 350)),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(
                self._image(root),
                root,
                observation_only=True,
            )

        self.assertEqual(result.image_route, "ocr_fast")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(
            [(item.field, item.raw_value) for item in result.fact_candidates],
            [("net_weight", "320 g")],
        )
        self.assertNotIn(
            "voltage",
            {item.field for item in result.fact_candidates},
        )
        self.assertIn(
            "observation_mode_cancelled_explicit_product_fact",
            result.fallback_reasons,
        )

    def test_observation_only_electrical_labels_remain_observations(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine("Input Voltage: 7 V", 0.999, (20, 80, 400, 120)),
                OcrLine("Rated Power: 65 W", 0.999, (20, 140, 400, 180)),
                OcrLine("Output: 5 V 2 A", 0.999, (20, 200, 400, 240)),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(
                self._image(root),
                root,
                observation_only=True,
            )

        self.assertEqual(result.image_route, "ocr_observations")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(result.mappings, [])
        self.assertNotIn(
            "observation_mode_cancelled_explicit_product_fact",
            result.fallback_reasons,
        )

    def test_observation_low_confidence_product_fact_fails_closed(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine("Vin = 7 V", 0.999, (20, 80, 400, 120)),
                OcrLine("NET WEIGHT: 320 g", 0.70, (20, 200, 400, 240)),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(
                self._image(root),
                root,
                observation_only=True,
            )

        self.assertEqual(result.image_route, "ocr_observations")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(result.mappings, [])
        self.assertIn(
            "low_ocr_confidence_on_target",
            result.fallback_reasons,
        )
        self.assertNotIn(
            "observation_mode_cancelled_explicit_product_fact",
            result.fallback_reasons,
        )

    def test_observation_same_line_product_values_fail_closed(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine(
                    "NET WEIGHT: 320 g | 350 g",
                    0.999,
                    (20, 200, 500, 240),
                )
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(
                self._image(root),
                root,
                observation_only=True,
            )

        self.assertEqual(result.image_route, "ocr_observations")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(result.mappings, [])
        self.assertIn("conflicting_ocr_values", result.fallback_reasons)
        self.assertNotIn(
            "observation_mode_cancelled_explicit_product_fact",
            result.fallback_reasons,
        )

    def test_observation_distinct_product_facts_preserve_conflict(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine("NET WEIGHT: 320 g", 0.999, (20, 80, 400, 120)),
                OcrLine("NET WEIGHT: 350 g", 0.999, (20, 200, 400, 240)),
            ]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(
                self._image(root),
                root,
                observation_only=True,
            )

        self.assertEqual(result.image_route, "ocr_fast_conflict")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(
            {item.raw_value for item in result.fact_candidates},
            {"320 g", "350 g"},
        )
        self.assertIn("conflicting_ocr_values", result.fallback_reasons)
        self.assertIn(
            "observation_mode_cancelled_explicit_product_fact",
            result.fallback_reasons,
        )

    def test_observation_only_with_unavailable_ocr_fails_closed(self) -> None:
        qwen = FakeQwenReader()
        reader = HybridImageReader(
            qwen,
            ocr_backend=None,
            ocr_unavailable_reason="controlled_unavailable",
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(
                self._image(root),
                root,
                observation_only=True,
            )

        self.assertEqual(result.image_route, "observations_unavailable")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(result.mappings, [])
        self.assertEqual(result.errors[0].code, "ocr_unavailable")

    def test_observation_only_with_ocr_failure_fails_closed(self) -> None:
        ocr = FakeOcrBackend(error=RuntimeError("local failure"))
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(
                self._image(root),
                root,
                observation_only=True,
            )

        self.assertEqual(result.image_route, "observations_unavailable")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(result.mappings, [])
        self.assertEqual(result.errors[0].code, "ocr_inference_failed")

    def test_observation_only_outside_evidence_root_fails_closed(self) -> None:
        ocr = FakeOcrBackend([OcrLine("Vin = 7 V", 0.99)])
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            evidence_root = workspace / "evidence"
            evidence_root.mkdir()
            outside_image = workspace / "outside.jpg"
            outside_image.write_bytes(b"test-image")
            result = reader.analyze_image(
                outside_image,
                evidence_root,
                observation_only=True,
            )

        self.assertEqual(result.image_route, "observations_unavailable")
        self.assertEqual(qwen.calls, [])
        self.assertEqual(ocr.calls, [])
        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(result.errors[0].code, "image_outside_root")

    def test_unknown_unit_like_token_forces_qwen_visual_review(self) -> None:
        ocr = FakeOcrBackend(
            [OcrLine("11000mAh 3.7U", 0.995, (10, 20, 700, 100))]
        )
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_ocr_review")
        self.assertIn("unknown_unit_like_token", result.fallback_reasons)
        self.assertEqual(len(qwen.calls), 1)
        self.assertIsNotNone(qwen.calls[0]["ocr_hints"])

    def test_low_target_confidence_forces_review(self) -> None:
        ocr = FakeOcrBackend([OcrLine("NET WT 8.0oz", 0.95)])
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_ocr_review")
        self.assertIn(
            "low_ocr_confidence_on_target",
            result.fallback_reasons,
        )

    def test_ocr_failure_falls_back_to_deep_qwen(self) -> None:
        ocr = FakeOcrBackend(error=RuntimeError("local failure"))
        qwen = FakeQwenReader()
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_deep_fallback")
        self.assertEqual(len(qwen.calls), 1)
        self.assertIsNone(qwen.calls[0]["ocr_hints"])
        self.assertIn(
            "ocr_inference_failed:RuntimeError",
            result.fallback_reasons,
        )

    def test_valid_empty_review_does_not_repeat_expensive_deep_qwen(self) -> None:
        ocr = FakeOcrBackend([OcrLine("NET WT 8.0oz", 0.95)])
        qwen = FakeQwenReader(review_has_candidate=False)
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_ocr_review")
        self.assertEqual(len(qwen.calls), 1)
        self.assertIsNotNone(qwen.calls[0]["ocr_hints"])
        self.assertIn(
            "guided_review_no_safe_candidate_no_deep_retry",
            result.fallback_reasons,
        )
        self.assertEqual(
            result.ocr_observations[0]["raw_text"],
            "NET WT 8.0oz",
        )

    def test_invalid_dense_negative_review_escalates_to_deep_qwen(self) -> None:
        ocr = FakeOcrBackend(
            [
                OcrLine("brand", 0.99),
                OcrLine("marketing", 0.99),
                OcrLine("ingredients", 0.99),
            ]
        )
        qwen = FakeQwenReader(
            review_has_candidate=False,
            review_has_error=True,
        )
        reader = HybridImageReader(qwen, ocr_backend=ocr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = reader.analyze_image(self._image(root), root)

        self.assertEqual(result.image_route, "qwen_deep_fallback")
        self.assertEqual(len(qwen.calls), 2)


if __name__ == "__main__":
    unittest.main()
