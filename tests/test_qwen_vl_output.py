from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest
from typing import Any

from product_evidence_guard.graph import build_fact_groups
from product_evidence_guard.model_output_schema import (
    MAX_RAW_TEXT_CHARS,
    MAX_TRANSCRIPTION_ITEMS,
    parse_field_mapping_output,
    parse_visual_transcription_output,
)
from product_evidence_guard.qwen_vl_reader import (
    QwenVlReader,
    _repair_complete_items_prefix,
)


def _visual_item(
    *,
    identifier: str = "visual-001",
    raw_text: str = "净重 0.32kg",
    bbox: list[int] | None = None,
    precision: str | None = None,
) -> dict[str, Any]:
    if bbox is None and precision is None:
        precision = "unavailable"
    elif precision is None:
        precision = "approximate"
    return {
        "id": identifier,
        "raw_text": raw_text,
        "bbox_1000": bbox,
        "position_precision": precision,
        "legibility": "clear",
        "confidence_estimate": 0.86,
        "confidence_source": "model_self_assessment",
    }


def _mapping_item(
    *,
    transcription_id: str = "visual-001",
    field: str = "net_weight",
    raw_value: str = "0.32kg",
) -> dict[str, Any]:
    return {
        "transcription_id": transcription_id,
        "field": field,
        "raw_value": raw_value,
        "mapping_confidence_estimate": 0.91,
        "confidence_source": "model_self_assessment",
    }


def _payload(items: list[dict[str, Any]]) -> str:
    return json.dumps({"schema_version": 1, "items": items}, ensure_ascii=False)


class FakeBackend:
    model_id = "fake/Qwen3-VL-8B"
    device = "CPU"

    def __init__(self, outputs: list[Any]) -> None:
        self.outputs = list(outputs)
        self.image_token = object()
        self.loaded_paths: list[Path] = []
        self.calls: list[dict[str, Any]] = []

    def load_image(self, image_path: Path) -> Any:
        self.loaded_paths.append(image_path)
        return self.image_token

    def generate(
        self,
        prompt: str,
        *,
        image: Any | None = None,
        max_new_tokens: int,
    ) -> str:
        self.calls.append(
            {
                "prompt": prompt,
                "image": image,
                "max_new_tokens": max_new_tokens,
            }
        )
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output


class ModelOutputSchemaTests(unittest.TestCase):
    def test_deterministic_truncation_repair_keeps_only_complete_items(self) -> None:
        first = _visual_item()
        second = _visual_item(identifier="visual-002", raw_text="电压 19V")
        complete = _payload([first, second])
        truncated = complete[: complete.find('"visual-002"') + 8]

        repaired = _repair_complete_items_prefix(truncated)

        self.assertIsNotNone(repaired)
        parsed = parse_visual_transcription_output(repaired or "")
        self.assertEqual([item.id for item in parsed.items], ["visual-001"])
        self.assertIsNone(
            _repair_complete_items_prefix(
                '{"schema_version":999,"items":[{"id":"unsafe"}'
            )
        )

    def test_visual_output_accepts_fence_and_surrounding_explanation(self) -> None:
        raw = (
            "模型说明文字\n```json\n"
            + _payload(
                [
                    _visual_item(
                        bbox=[120, 240, 560, 330],
                        precision="approximate",
                    )
                ]
            )
            + "\n```\n结束"
        )

        parsed = parse_visual_transcription_output(raw)

        self.assertTrue(parsed.ok)
        self.assertEqual(len(parsed.items), 1)
        self.assertEqual(parsed.items[0].raw_text, "净重 0.32kg")
        self.assertEqual(parsed.items[0].bbox_1000, (120, 240, 560, 330))
        self.assertEqual(parsed.items[0].position_precision, "approximate")

    def test_visual_output_rejects_exact_position_and_extra_keys(self) -> None:
        exact = _visual_item(
            bbox=[1, 2, 30, 40],
            precision="exact",
        )
        extra = _visual_item(identifier="visual-002")
        extra["explanation"] = "not allowed"

        parsed = parse_visual_transcription_output(_payload([exact, extra]))

        self.assertEqual(parsed.items, [])
        self.assertIn("invalid_position_precision", {error.code for error in parsed.errors})
        self.assertIn("invalid_item_schema", {error.code for error in parsed.errors})

    def test_visual_output_enforces_item_and_text_limits(self) -> None:
        too_many = [
            _visual_item(identifier=f"visual-{index:03d}")
            for index in range(MAX_TRANSCRIPTION_ITEMS + 1)
        ]
        count_result = parse_visual_transcription_output(_payload(too_many))
        self.assertEqual(count_result.items, [])
        self.assertEqual(count_result.errors[0].code, "too_many_items")

        long_text = _visual_item(raw_text="x" * (MAX_RAW_TEXT_CHARS + 1))
        length_result = parse_visual_transcription_output(_payload([long_text]))
        self.assertEqual(length_result.items, [])
        self.assertIn("string_too_long", {error.code for error in length_result.errors})

    def test_mapping_rejects_unknown_field_and_unrelated_value(self) -> None:
        transcriptions = parse_visual_transcription_output(
            _payload([_visual_item(raw_text="净重 0.32kg")])
        ).items
        rows = [
            _mapping_item(),
            _mapping_item(field="secret_instruction"),
            _mapping_item(field="net_weight", raw_value="999g"),
        ]

        parsed = parse_field_mapping_output(_payload(rows), transcriptions=transcriptions)

        self.assertEqual(len(parsed.items), 1)
        self.assertEqual(parsed.items[0].field, "net_weight")
        codes = {error.code for error in parsed.errors}
        self.assertIn("field_not_allowed", codes)
        self.assertIn("raw_value_not_in_source", codes)

    def test_mapping_rejects_unknown_transcription_and_missing_fields(self) -> None:
        transcriptions = parse_visual_transcription_output(
            _payload([_visual_item()])
        ).items
        missing = _mapping_item()
        missing.pop("confidence_source")
        rows = [
            _mapping_item(transcription_id="visual-does-not-exist"),
            missing,
        ]

        parsed = parse_field_mapping_output(_payload(rows), transcriptions=transcriptions)

        self.assertEqual(parsed.items, [])
        codes = {error.code for error in parsed.errors}
        self.assertIn("unknown_transcription_id", codes)
        self.assertIn("invalid_item_schema", codes)

    def test_non_json_and_invalid_utf8_return_safe_errors(self) -> None:
        non_json = parse_visual_transcription_output("这不是 JSON \ufffd\ufffd")
        invalid_utf8 = parse_visual_transcription_output(b"\xff\xfe")

        self.assertEqual(non_json.items, [])
        self.assertEqual(non_json.errors[0].code, "invalid_json")
        self.assertEqual(invalid_utf8.items, [])
        self.assertEqual(invalid_utf8.errors[0].code, "invalid_utf8")

    def test_json_repair_is_injected_and_called_at_most_once(self) -> None:
        repair_calls: list[str] = []

        def repair(raw: str) -> str:
            repair_calls.append(raw)
            return _payload([_visual_item()])

        parsed = parse_visual_transcription_output("broken json", repair_callback=repair)

        self.assertEqual(len(parsed.items), 1)
        self.assertTrue(parsed.repair_attempted)
        self.assertEqual(len(repair_calls), 1)

        failed_calls: list[str] = []

        def still_broken(raw: str) -> str:
            failed_calls.append(raw)
            return "still broken"

        failed = parse_visual_transcription_output("broken json", repair_callback=still_broken)
        self.assertEqual(failed.items, [])
        self.assertEqual(len(failed_calls), 1)
        self.assertEqual(failed.errors[0].code, "invalid_json_after_repair")

    def test_truncated_wrapper_does_not_treat_inner_bbox_as_top_level(self) -> None:
        repair_calls: list[str] = []
        truncated = (
            '{"schema_version":1,"items":['
            '{"id":"visual-001","raw_text":"净重 300g",'
            '"bbox_1000":[10,20,300,80],'
        )

        def repair(raw: str) -> str:
            repair_calls.append(raw)
            return _payload(
                [
                    _visual_item(
                        raw_text="净重 300g",
                        bbox=[10, 20, 300, 80],
                        precision="approximate",
                    )
                ]
            )

        parsed = parse_visual_transcription_output(
            truncated,
            repair_callback=repair,
        )

        self.assertEqual(len(repair_calls), 1)
        self.assertTrue(parsed.repair_attempted)
        self.assertEqual([item.raw_text for item in parsed.items], ["净重 300g"])

    def test_invalid_wrapper_cannot_fall_back_to_nested_items_array(self) -> None:
        item = _visual_item()
        cases = (
            (
                {"schema_version": 999, "items": [item]},
                "unsupported_schema_version",
            ),
            (
                {"schema_version": 1, "items": [item], "unexpected": True},
                "invalid_wrapper_schema",
            ),
            (
                {"items": [item]},
                "invalid_wrapper_schema",
            ),
        )

        for wrapper, expected_code in cases:
            with self.subTest(wrapper=wrapper):
                parsed = parse_visual_transcription_output(
                    json.dumps(wrapper, ensure_ascii=False)
                )
                self.assertEqual(parsed.items, [])
                self.assertIn(expected_code, {error.code for error in parsed.errors})

    def test_truncated_wrapper_with_complete_items_requires_json_repair(self) -> None:
        complete = _payload([_visual_item()])
        truncated = complete[:-1]

        without_repair = parse_visual_transcription_output(truncated)
        self.assertEqual(without_repair.items, [])
        self.assertEqual(without_repair.errors[0].code, "invalid_json")

        repair_calls: list[str] = []

        def repair(raw: str) -> str:
            repair_calls.append(raw)
            return complete

        repaired = parse_visual_transcription_output(
            truncated,
            repair_callback=repair,
        )
        self.assertEqual(len(repair_calls), 1)
        self.assertTrue(repaired.repair_attempted)
        self.assertEqual(len(repaired.items), 1)

    def test_malformed_wrapper_cannot_promote_fenced_or_post_mismatch_items(self) -> None:
        items = json.dumps([_visual_item()], ensure_ascii=False)
        malformed_outputs = (
            (
                '{"schema_version":999,"items":\n'
                f"```json\n{items}\n```\n"
                "}"
            ),
            f'{{"schema_version":1] "items": {items}}}',
        )

        for raw in malformed_outputs:
            with self.subTest(raw=raw):
                parsed = parse_visual_transcription_output(raw)
                self.assertEqual(parsed.items, [])
                self.assertEqual(parsed.errors[0].code, "invalid_json")

    def test_valid_json_with_bad_schema_is_not_repaired(self) -> None:
        repair_calls: list[str] = []

        def repair(raw: str) -> str:
            repair_calls.append(raw)
            return _payload([_visual_item()])

        bad_schema = json.dumps(
            {
                "schema_version": 1,
                "items": [{"id": "visual-001"}],
            }
        )
        parsed = parse_visual_transcription_output(bad_schema, repair_callback=repair)

        self.assertEqual(parsed.items, [])
        self.assertEqual(repair_calls, [])
        self.assertIn("invalid_item_schema", {error.code for error in parsed.errors})


class QwenVlReaderTests(unittest.TestCase):
    def test_expired_deadline_creates_no_candidate_or_model_call(self) -> None:
        backend = FakeBackend([_payload([_visual_item()])])
        reader = QwenVlReader(backend)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "label.jpg"
            image.write_bytes(b"fake-image")

            result = reader.analyze_image(
                image,
                root,
                deadline=time.perf_counter() - 1,
            )

        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(result.errors[0].code, "file_timeout")
        self.assertEqual(backend.calls, [])

    def test_deadline_expiring_during_json_repair_discards_repaired_items(self) -> None:
        backend = FakeBackend(["broken json"])

        def slow_repair(_raw: str) -> str:
            time.sleep(0.2)
            return _payload([_visual_item()])

        reader = QwenVlReader(backend, json_repair=slow_repair)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "label.jpg"
            image.write_bytes(b"fake-image")
            result = reader.analyze_image(
                image,
                root,
                deadline=time.perf_counter() + 0.1,
            )

        self.assertTrue(result.transcription_repair_attempted)
        self.assertEqual(result.source_blocks, [])
        self.assertEqual(result.fact_candidates, [])
        self.assertIn("file_timeout", {error.code for error in result.errors})

    def test_same_backend_runs_two_stages_and_builds_existing_models(self) -> None:
        visual = _payload(
            [
                _visual_item(
                    bbox=[90, 130, 350, 180],
                    precision="approximate",
                )
            ]
        )
        mapping = _payload([_mapping_item()])
        backend = FakeBackend([visual, mapping])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "package-front.jpg"
            image.write_bytes(b"fake image bytes")

            result = QwenVlReader(backend).analyze_image(image, root)

        self.assertTrue(result.ok)
        self.assertEqual(len(backend.calls), 2)
        self.assertIs(backend.calls[0]["image"], backend.image_token)
        self.assertIsNone(backend.calls[1]["image"])
        self.assertIn("视觉原文抄录器", backend.calls[0]["prompt"])
        self.assertIn("字段映射器", backend.calls[1]["prompt"])

        self.assertEqual(len(result.source_blocks), 1)
        block = result.source_blocks[0]
        self.assertEqual(block.source_file, "package-front.jpg")
        self.assertEqual(block.locator["position_precision"], "approximate")
        self.assertEqual(block.locator["bbox_1000"], [90, 130, 350, 180])
        self.assertEqual(block.locator["model_id"], backend.model_id)
        self.assertAlmostEqual(block.recognition_confidence, 0.86)

        self.assertEqual(len(result.fact_candidates), 1)
        candidate = result.fact_candidates[0]
        self.assertEqual(candidate.field, "net_weight")
        self.assertEqual(candidate.raw_value, "0.32kg")
        self.assertEqual(candidate.normalized_value, 320)
        self.assertEqual(candidate.normalized_unit, "g")
        self.assertEqual(candidate.source_block_id, block.block_id)
        self.assertAlmostEqual(candidate.mapping_confidence, 0.91)

    def test_unit_mapping_fills_voltage_and_current_omitted_by_model(self) -> None:
        visual = _payload(
            [
                _visual_item(
                    raw_text="INPUT 100-240V~50-60Hz, 1.5A",
                )
            ]
        )
        mapping = _payload(
            [
                _mapping_item(
                    field="voltage",
                    raw_value="100-240V~50-60Hz",
                )
            ]
        )
        backend = FakeBackend([visual, mapping])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "adapter.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(image, root)

        by_field = {candidate.field: candidate for candidate in result.fact_candidates}
        self.assertEqual(by_field["voltage"].normalized_value, [100, 240])
        self.assertEqual(by_field["current"].raw_value, "1.5A")
        self.assertEqual(by_field["current"].normalized_value, 1.5)
        self.assertEqual(by_field["voltage"].scope, "input")
        self.assertEqual(by_field["current"].scope, "input")
        self.assertEqual(
            by_field["current"].extraction_method,
            "deterministic_unit_mapping",
        )
        self.assertEqual(
            by_field["current"].mapping_confidence_source,
            "deterministic",
        )

    def test_unit_mapping_fills_voltage_on_battery_capacity_line(self) -> None:
        visual = _payload(
            [_visual_item(raw_text="GSP 063450 1000mAh 3.7V")]
        )
        mapping = _payload(
            [
                _mapping_item(
                    field="capacity_charge",
                    raw_value="1000mAh",
                )
            ]
        )
        backend = FakeBackend([visual, mapping])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "battery.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(image, root)

        by_field = {candidate.field: candidate for candidate in result.fact_candidates}
        self.assertEqual(by_field["capacity_charge"].normalized_value, 1000)
        self.assertEqual(by_field["voltage"].normalized_value, 3.7)

    def test_ocr_guided_review_uses_one_visual_call_and_deterministic_mapping(
        self,
    ) -> None:
        backend = FakeBackend(
            [
                json.dumps(
                    {
                        "schema_version": 1,
                        "lines": ["GSP 063450 1000mAh 3.7V"],
                    },
                    ensure_ascii=False,
                )
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "battery.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(
                image,
                root,
                ocr_hints=[{"text": "11000mAh 3.7U", "score": 0.955}],
            )

        self.assertEqual(result.image_route, "qwen_ocr_review")
        self.assertEqual(len(backend.calls), 1)
        self.assertIs(backend.calls[0]["image"], backend.image_token)
        self.assertLessEqual(
            backend.calls[0]["max_new_tokens"],
            96,
        )
        self.assertIn("OCR_HINTS_BEGIN", backend.calls[0]["prompt"])
        by_field = {item.field: item for item in result.fact_candidates}
        self.assertEqual(by_field["capacity_charge"].normalized_value, 1000)
        self.assertEqual(by_field["voltage"].normalized_value, 3.7)
        self.assertTrue(
            all(
                item.mapping_confidence_source == "deterministic"
                for item in result.fact_candidates
            )
        )
        self.assertTrue(
            all(
                item.provenance["recognition_confidence_source"]
                == "qwen_visual_review_unscored"
                for item in result.fact_candidates
            )
        )

    def test_ocr_guided_review_rejects_non_strict_wrapper(self) -> None:
        backend = FakeBackend(
            [
                'explanation {"schema_version":1,'
                '"lines":["GSP 063450 1000mAh 3.7V"]}'
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "battery.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(
                image,
                root,
                ocr_hints=[{"text": "11000mAh 3.7U", "score": 0.955}],
            )

        self.assertEqual(result.fact_candidates, [])
        self.assertIn("invalid_json", {error.code for error in result.errors})

    def test_ocr_guided_review_does_not_treat_model_suffix_as_current(
        self,
    ) -> None:
        backend = FakeBackend(
            [
                json.dumps(
                    {"schema_version": 1, "lines": ["MOD0L/04A"]},
                    ensure_ascii=False,
                )
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "adapter.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(
                image,
                root,
                ocr_hints=[{"text": "MOD0L/04A", "score": 0.99}],
            )

        self.assertEqual(result.fact_candidates, [])

    def test_model_suffix_is_not_misread_as_current(self) -> None:
        visual = _payload(
            [_visual_item(raw_text="MODEL CPA009-004A")]
        )
        mapping = _payload(
            [_mapping_item(field="model", raw_value="CPA009-004A")]
        )
        backend = FakeBackend([visual, mapping])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "adapter.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(image, root)

        self.assertEqual(
            [(candidate.field, candidate.raw_value) for candidate in result.fact_candidates],
            [("model", "CPA009-004A")],
        )

    def test_input_and_output_values_are_reviewed_without_false_conflict(self) -> None:
        visual = _payload(
            [
                _visual_item(
                    identifier="visual-001",
                    raw_text="INPUT 100-240V 1.5A",
                ),
                _visual_item(
                    identifier="visual-002",
                    raw_text="OUTPUT 19V 3.16A",
                ),
            ]
        )
        mapping = _payload(
            [
                _mapping_item(
                    transcription_id="visual-001",
                    field="voltage",
                    raw_value="100-240V",
                ),
                _mapping_item(
                    transcription_id="visual-002",
                    field="voltage",
                    raw_value="19V",
                ),
            ]
        )
        backend = FakeBackend([visual, mapping])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "adapter.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(image, root)

        groups = {
            group.field: group
            for group in build_fact_groups(result.fact_candidates)
        }
        self.assertEqual(
            groups["voltage"].classification,
            "semantic_scope_split",
        )
        self.assertEqual(groups["voltage"].severity, "review")
        self.assertEqual(
            groups["current"].classification,
            "semantic_scope_split",
        )

    def test_explicit_mode_profiles_are_not_treated_as_power_conflicts(self) -> None:
        visual = _payload(
            [
                _visual_item(
                    identifier="visual-eco",
                    raw_text="POWER BY MODE (W) 30 W ECO",
                ),
                _visual_item(
                    identifier="visual-balanced",
                    raw_text="POWER BY MODE (W) 45 W BALANCED",
                ),
                _visual_item(
                    identifier="visual-turbo",
                    raw_text="POWER BY MODE (W) 65 W TURBO",
                ),
            ]
        )
        mapping = _payload(
            [
                _mapping_item(
                    transcription_id="visual-eco",
                    field="power",
                    raw_value="BY MODE (W) 30 W ECO",
                ),
                _mapping_item(
                    transcription_id="visual-balanced",
                    field="power",
                    raw_value="BY MODE (W) 45 W BALANCED",
                ),
                _mapping_item(
                    transcription_id="visual-turbo",
                    field="power",
                    raw_value="BY MODE (W) 65 W TURBO",
                ),
            ]
        )
        backend = FakeBackend([visual, mapping])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "power-profiles.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(image, root)

        self.assertEqual(
            {
                (candidate.normalized_value, candidate.scope)
                for candidate in result.fact_candidates
                if candidate.field == "power"
            },
            {
                (30, "profile:eco"),
                (45, "profile:balanced"),
                (65, "profile:turbo"),
            },
        )
        group = next(
            item
            for item in build_fact_groups(result.fact_candidates)
            if item.field == "power"
        )
        self.assertEqual(group.classification, "semantic_scope_split")
        self.assertEqual(group.severity, "review")

    def test_rating_and_profile_scopes_do_not_create_false_conflicts(self) -> None:
        visual = _payload(
            [
                _visual_item(
                    identifier="visual-rated",
                    raw_text="RATED POWER 65 W",
                ),
                _visual_item(
                    identifier="visual-max",
                    raw_text="MAX POWER 80 W",
                ),
                _visual_item(
                    identifier="visual-eco",
                    raw_text="POWER BY MODE (W) 30 W ECO",
                ),
            ]
        )
        backend = FakeBackend([visual, _payload([])])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "power-ratings.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(image, root)

        power_candidates = [
            candidate
            for candidate in result.fact_candidates
            if candidate.field == "power"
        ]
        self.assertEqual(
            {
                (candidate.normalized_value, candidate.scope)
                for candidate in power_candidates
            },
            {
                (30, "profile:eco"),
                (65, "rating:rated"),
                (80, "rating:max"),
            },
        )
        self.assertTrue(
            all(
                candidate.mapping_confidence_source == "deterministic"
                for candidate in power_candidates
            )
        )
        group = next(
            item
            for item in build_fact_groups(power_candidates)
            if item.field == "power"
        )
        self.assertEqual(group.classification, "semantic_scope_split")
        self.assertEqual(group.severity, "review")

    def test_unlabeled_power_values_remain_a_strong_conflict(self) -> None:
        visual = _payload(
            [
                _visual_item(
                    identifier="visual-low",
                    raw_text="POWER 30 W",
                ),
                _visual_item(
                    identifier="visual-high",
                    raw_text="POWER 65 W",
                ),
            ]
        )
        mapping = _payload(
            [
                _mapping_item(
                    transcription_id="visual-low",
                    field="power",
                    raw_value="30 W",
                ),
                _mapping_item(
                    transcription_id="visual-high",
                    field="power",
                    raw_value="65 W",
                ),
            ]
        )
        backend = FakeBackend([visual, mapping])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "unlabeled-power.jpg"
            image.write_bytes(b"fake image bytes")
            result = QwenVlReader(backend).analyze_image(image, root)

        power_candidates = [
            candidate
            for candidate in result.fact_candidates
            if candidate.field == "power"
        ]
        self.assertEqual(
            [candidate.scope for candidate in power_candidates],
            [None, None],
        )
        group = next(
            item
            for item in build_fact_groups(power_candidates)
            if item.field == "power"
        )
        self.assertEqual(group.classification, "strong_conflict")
        self.assertEqual(group.severity, "block")

    def test_invalid_mapping_keeps_source_block_but_creates_no_candidate(self) -> None:
        backend = FakeBackend(
            [
                _payload([_visual_item()]),
                _payload([_mapping_item(field="not_allowed")]),
            ]
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "package.jpg"
            image.write_bytes(b"fake")
            result = QwenVlReader(backend).analyze_image(image, root)

        self.assertEqual(len(result.source_blocks), 1)
        self.assertEqual(result.fact_candidates, [])
        self.assertIn("field_not_allowed", {error.code for error in result.errors})

    def test_invalid_visual_output_stops_before_mapping_without_raising(self) -> None:
        backend = FakeBackend(["\ufffd not json"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "package.jpg"
            image.write_bytes(b"fake")
            result = QwenVlReader(backend).analyze_image(image, root)

        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(result.source_blocks, [])
        self.assertEqual(result.fact_candidates, [])
        self.assertIn("invalid_json", {error.code for error in result.errors})

    def test_backend_error_returns_structured_error(self) -> None:
        backend = FakeBackend([RuntimeError("secret raw failure")])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "package.jpg"
            image.write_bytes(b"fake")
            result = QwenVlReader(backend).analyze_image(image, root)

        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0].code, "backend_generation_failed")
        self.assertNotIn("secret raw failure", result.errors[0].message)

    def test_empty_visual_result_is_valid_and_does_not_invent_facts(self) -> None:
        backend = FakeBackend([_payload([])])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "blank.jpg"
            image.write_bytes(b"fake")
            result = QwenVlReader(backend).analyze_image(image, root)

        self.assertTrue(result.ok)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(result.source_blocks, [])
        self.assertEqual(result.fact_candidates, [])


if __name__ == "__main__":
    unittest.main()
