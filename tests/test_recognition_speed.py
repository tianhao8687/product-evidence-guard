from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image

from product_evidence_guard.hybrid_image_reader import HybridImageReader, OcrLine
from product_evidence_guard.qwen_vl_reader import QwenVlReader
from product_evidence_guard.recognition_cache import RecognitionCache
from product_evidence_guard.review_focus import choose_review_region
from test_hybrid_image_reader import FakeOcrBackend
from test_qwen_vl_output import FakeBackend


def compact(*lines):
    return json.dumps({"schema_version": 1, "lines": list(lines)})


class RegionBackend(FakeBackend):
    def __init__(self, outputs):
        super().__init__(outputs)
        self.regions = []

    def load_image_region(self, image_path, region):
        self.regions.append(region)
        return self.load_image(image_path)


class RecognitionSpeedTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "label.png"
        Image.new("RGB", (800, 1000), "white").save(self.path)

    def reader(self, *, outputs=None, lines=None, region=False):
        backend = (RegionBackend if region else FakeBackend)(outputs or [compact("NET WEIGHT 320g")])
        ocr = FakeOcrBackend(lines or [OcrLine("NET WEIGHT 320g", .9, (100, 400, 700, 440))])
        reader = HybridImageReader(QwenVlReader(backend), ocr_backend=ocr,
                                   enable_recognition_cache=True, enable_review_region=region)
        return reader, ocr, backend

    def test_renamed_cross_directory_cache_rebinds_evidence_and_never_decisions(self):
        reader, ocr, backend = self.reader()
        first = reader.analyze_image(self.path, self.root)
        original_id = first.fact_candidates[0].candidate_id
        first.fact_candidates[0].status = "confirmed"
        first.fact_candidates[0].locator["image"] = "poisoned"
        first.source_blocks[0].text = "poisoned"
        new_root = self.root / "another-task"
        new_path = new_root / "subdir" / "renamed.png"
        new_path.parent.mkdir(parents=True)
        new_path.write_bytes(self.path.read_bytes())

        reused = reader.analyze_image(new_path, new_root)
        self.assertEqual(len(ocr.calls), 1)
        self.assertEqual(len(backend.calls), 1)
        self.assertTrue(reused.recognition_cache["hit"])
        self.assertEqual(reused.image_route, "qwen_ocr_review")
        self.assertEqual(reused.stage_timings["visual_seconds"], 0)
        self.assertEqual(reused.image_file, "subdir/renamed.png")
        self.assertNotEqual(reused.fact_candidates[0].candidate_id, original_id)
        for item in [*reused.source_blocks, *reused.fact_candidates]:
            self.assertEqual(item.source_file, "subdir/renamed.png")
            self.assertEqual(item.locator["image"], "subdir/renamed.png")
            self.assertEqual(item.file_hash, first.file_hash)
        self.assertEqual(reused.fact_candidates[0].source_block_id, reused.source_blocks[0].block_id)
        self.assertEqual(reused.fact_candidates[0].status, "pending")
        self.assertEqual(reused.source_blocks[0].text, "NET WEIGHT 320g")
        again = reader.analyze_image(self.path, self.root)
        self.assertEqual(again.fact_candidates[0].candidate_id, original_id)

    def test_diagnostic_cache_bypass_keeps_candidate_identity_stable(self):
        reader, ocr, backend = self.reader(outputs=[compact("NET WEIGHT 320g")] * 2)
        reader.use_recognition_cache = False
        first = reader.analyze_image(self.path, self.root)
        self.assertEqual(reader._recognition_cache.stats()["entries"], 0)
        reader.use_recognition_cache = True
        second = reader.analyze_image(self.path, self.root)
        third = reader.analyze_image(self.path, self.root)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(first.fact_candidates[0].candidate_id, second.fact_candidates[0].candidate_id)
        self.assertEqual(second.fact_candidates[0].candidate_id, third.fact_candidates[0].candidate_id)

    def test_prompt_change_invalidates_caches_without_replacing_backend(self):
        from product_evidence_guard.engine import _image_reader_identity
        from product_evidence_guard.qwen_vl_reader import ocr_review_template
        template = self.root / "prompt.txt"
        template.write_text(ocr_review_template(), "utf-8")
        reader, ocr, backend = self.reader(outputs=[compact("NET WEIGHT 320g")] * 2)
        with patch("product_evidence_guard.qwen_vl_reader.OCR_REVIEW_TEMPLATE_PATH", template):
            before_identity = _image_reader_identity(reader)
            reader.analyze_image(self.path, self.root)
            template.write_text(template.read_text("utf-8") + "\n再次核对所有位置。", "utf-8")
            result = reader.analyze_image(self.path, self.root)
            self.assertNotEqual(before_identity, _image_reader_identity(reader))
            self.assertFalse(result.recognition_cache["hit"])
            self.assertEqual(len(backend.calls), 2)
            self.assertIn("再次核对所有位置", backend.calls[-1]["prompt"])

    def test_invalid_prompt_resource_fails_closed(self):
        from product_evidence_guard.qwen_vl_reader import _ocr_review_prompt
        template = self.root / "prompt.txt"
        template.write_text("missing required data placeholder", "utf-8")
        with patch("product_evidence_guard.qwen_vl_reader.OCR_REVIEW_TEMPLATE_PATH", template):
            with self.assertRaises(ValueError):
                _ocr_review_prompt([])

    def test_changed_image_bytes_force_new_recognition(self):
        reader, ocr, backend = self.reader(outputs=[compact("NET WEIGHT 320g")] * 2)
        first = reader.analyze_image(self.path, self.root)
        Image.new("RGB", (800, 1000), "black").save(self.path)
        changed = reader.analyze_image(self.path, self.root)
        self.assertFalse(changed.recognition_cache["hit"])
        self.assertNotEqual(first.file_hash, changed.file_hash)
        self.assertEqual(len(backend.calls), 2)

    def test_observation_only_never_uses_normal_recognition_cache(self):
        reader, ocr, backend = self.reader()
        reader.analyze_image(self.path, self.root)
        observation = reader.analyze_image(self.path, self.root, observation_only=True)
        self.assertEqual(observation.fact_candidates, [])
        self.assertEqual(len(ocr.calls), 2)
        self.assertEqual(len(backend.calls), 1)

    def test_cached_ocr_observations_keep_their_reader_identity(self):
        from product_evidence_guard.engine import _visual_reader_suffix
        reader, ocr, backend = self.reader(
            lines=[OcrLine("GRAPH TREND", .99, (100, 400, 700, 440))])
        reader.analyze_image(self.path, self.root, observation_only=True)
        cached = reader.analyze_image(self.path, self.root, observation_only=True)
        self.assertTrue(cached.recognition_cache["hit"])
        self.assertEqual(cached.image_route, "ocr_observations")
        self.assertEqual(_visual_reader_suffix(cached), "openvino_rapidocr")
        self.assertEqual(cached.fact_candidates, [])
        self.assertEqual(len(ocr.calls), 1)
        self.assertEqual(len(backend.calls), 0)

    def test_expired_deadline_never_returns_cached_success(self):
        reader, ocr, backend = self.reader()
        reader.analyze_image(self.path, self.root)
        result = reader.analyze_image(self.path, self.root, deadline=time.perf_counter() - 1)
        self.assertFalse(result.ok)
        self.assertEqual(result.fact_candidates, [])
        self.assertEqual(len(ocr.calls), 1)

    def test_failed_recognition_is_not_cached(self):
        reader, ocr, backend = self.reader(outputs=[RuntimeError("unavailable")] * 4)
        self.assertFalse(reader.analyze_image(self.path, self.root).ok)
        self.assertFalse(reader.analyze_image(self.path, self.root).ok)
        self.assertEqual(reader._recognition_cache.stats()["entries"], 0)
        self.assertEqual(len(ocr.calls), 2)

    def test_lru_limits_and_nonpending_results(self):
        reader, _, _ = self.reader()
        result = reader.analyze_image(self.path, self.root)
        cache = RecognitionCache(max_entries=2)
        cache.put("a", result)
        cache.put("b", result)
        cache.get("a")
        cache.put("c", result)
        self.assertIsNone(cache.get("b"))
        self.assertEqual(cache.stats()["entries"], 2)
        confirmed = deepcopy(result)
        confirmed.fact_candidates[0].status = "confirmed"
        cache.put("d", confirmed)
        self.assertIsNone(cache.get("d"))
        tiny = RecognitionCache(max_bytes=1)
        tiny.put("a", result)
        self.assertEqual(tiny.stats()["entries"], 0)

    def test_context_region_keeps_numeric_and_uncertain_lines(self):
        target = OcrLine("NET WEIGHT 320g", .9, (100, 400, 700, 440))
        lines = [target, OcrLine("batch 2", .999, (100, 260, 500, 300)),
                 OcrLine("uncertain label", .94, (100, 620, 700, 660)),
                 OcrLine("BRAND", .999, (100, 20, 700, 60))]
        self.assertEqual(choose_review_region(self.path, lines, [target]), (0, 180, 1000, 740))
        lines.append(OcrLine("5V", .999, (100, 940, 700, 980)))
        self.assertIsNone(choose_review_region(self.path, lines, [target]))

    def test_no_crop_for_missing_coordinates_or_rotated_exif(self):
        target = OcrLine("NET WEIGHT 320g", .9, None)
        self.assertIsNone(choose_review_region(self.path, [target], [target]))
        target = OcrLine("NET WEIGHT 320g", .9, (100, 400, 700, 440))
        exif = Image.Exif()
        exif[274] = 6
        Image.new("RGB", (800, 1000)).save(self.path, exif=exif)
        self.assertIsNone(choose_review_region(self.path, [target], [target]))

    def test_successful_region_does_not_claim_exact_fact_coordinates(self):
        reader, _, backend = self.reader(region=True)
        result = reader.analyze_image(self.path, self.root)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.regions, [(0, 320, 1000, 520)])
        self.assertEqual(result.review_region["area_ratio"], .2)
        for item in [*result.source_blocks, *result.fact_candidates]:
            self.assertIsNone(item.locator["bbox_1000"])
            self.assertEqual(item.locator["position_precision"], "unavailable")
            self.assertEqual(item.locator["review_region_1000"], [0, 320, 1000, 520])

    def test_missing_field_in_crop_retries_whole_image(self):
        reader, _, backend = self.reader(region=True, outputs=[compact(), compact("NET WEIGHT 320g")])
        result = reader.analyze_image(self.path, self.root)
        self.assertTrue(result.ok)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(len(backend.regions), 1)
        self.assertEqual(result.review_region, {})
        self.assertIn("review_region_coverage_full_image_retry", result.fallback_reasons)

    def test_unknown_unit_requires_whole_image_even_with_tight_coordinates(self):
        reader, _, backend = self.reader(region=True,
            lines=[OcrLine("NET WEIGHT 8OZ", .9, (100, 400, 700, 440)),
                   OcrLine("NET WT 8.0oz (0.501b)", .99, (100, 560, 700, 600))],
            outputs=[compact("NET WEIGHT 8OZ", "NET WT 8.0oz (0.501b)")])
        result = reader.analyze_image(self.path, self.root)
        self.assertIn("unknown_unit_like_token", result.fallback_reasons)
        self.assertEqual(backend.regions, [])
        self.assertEqual(len(result.fact_candidates), 2)

    def test_duplicate_value_at_two_positions_is_not_silently_dropped_by_crop(self):
        reader, _, backend = self.reader(region=True,
            lines=[OcrLine("NET WEIGHT 320g", .9, (100, 400, 700, 440)),
                   OcrLine("NET WEIGHT 320g", .9, (100, 560, 700, 600))],
            outputs=[compact("NET WEIGHT 320g"), compact("NET WEIGHT 320g", "NET WEIGHT 320g")])
        result = reader.analyze_image(self.path, self.root)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual(len(result.fact_candidates), 2)
        self.assertEqual(len({c.source_block_id for c in result.fact_candidates}), 2)

    def test_lost_conflict_in_crop_retries_whole_image(self):
        reader, _, backend = self.reader(region=True,
            lines=[OcrLine("NET WEIGHT 320g", .99, (100, 400, 700, 440)),
                   OcrLine("NET WEIGHT 350g", .99, (100, 560, 700, 600))],
            outputs=[compact("NET WEIGHT 320g", "NET WEIGHT 320g"),
                     compact("NET WEIGHT 320g", "NET WEIGHT 350g")])
        result = reader.analyze_image(self.path, self.root)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual({c.normalized_value for c in result.fact_candidates}, {320, 350})
        self.assertIn("review_region_coverage_full_image_retry", result.fallback_reasons)

    def test_lost_input_output_scope_retries_whole_image(self):
        reader, _, backend = self.reader(region=True,
            lines=[OcrLine("INPUT: 12V", .9, (100, 400, 700, 440)),
                   OcrLine("OUTPUT: 5V", .9, (100, 560, 700, 600))],
            outputs=[compact("INPUT: 12V", "INPUT: 5V"), compact("INPUT: 12V", "OUTPUT: 5V")])
        result = reader.analyze_image(self.path, self.root)
        self.assertEqual(len(backend.calls), 2)
        self.assertEqual({c.scope for c in result.fact_candidates}, {"input", "output"})


if __name__ == "__main__":
    unittest.main()
