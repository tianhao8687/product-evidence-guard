from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time
import unittest
from typing import Any

from product_evidence_guard.model_output_schema import (
    MAX_RAW_TEXT_CHARS,
    MAX_TRANSCRIPTION_ITEMS,
    parse_field_mapping_output,
    parse_visual_transcription_output,
)
from product_evidence_guard.qwen_vl_reader import QwenVlReader


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
