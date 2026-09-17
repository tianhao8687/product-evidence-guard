"""Resource failure is not a source change or successful image reading."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from product_evidence_guard.confirmation import apply_review_action
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.readiness import coverage_summary
from product_evidence_guard.runtime_resources import GIB, check_model_memory, cpu_pipeline_options
from product_evidence_guard.qwen_vl_reader import QwenVlReader
from product_evidence_guard.openvino_adapter import OpenVinoDeviceSelection
from tests.test_semantic_review import FakeBackend


class RuntimeFallbackTests(unittest.TestCase):
    def setUp(self):
        resolver = patch("product_evidence_guard.engine.resolve_openvino_device", return_value=
                         OpenVinoDeviceSelection("CPU", "CPU", ("CPU",), {"CPU": "test"}, "test"))
        resolver.start()
        self.addCleanup(resolver.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root, self.out = self.base / "input", self.base / "output"
        self.root.mkdir()
        self.path = self.root / "a.txt"
        self.path.write_text("SKU: TEST-1\n净重：≤135g（运输状态）", encoding="utf8")

    def read(self):
        return json.loads((self.out / "product-facts.json").read_text("utf8"))

    def weight(self):
        return next(g for g in self.read()["facts"] if g["field"] == "net_weight")

    def test_checks_commit_not_only_free_ram_and_does_not_reject_unknown_metrics(self):
        with patch("product_evidence_guard.runtime_resources.available_memory", return_value={"physical": 20 * GIB, "commit": GIB}):
            with self.assertRaisesRegex(MemoryError, "提交空间不足"):
                check_model_memory(self.base)
        with patch("product_evidence_guard.runtime_resources.available_memory", return_value={}):
            self.assertEqual(check_model_memory(self.base)["available_bytes"], {})
        self.assertLessEqual(cpu_pipeline_options("CPU")["INFERENCE_NUM_THREADS"], 4)
        self.assertEqual(cpu_pipeline_options("GPU"), {})

    def test_load_failure_keeps_native_facts_and_marks_unread_images(self):
        self.path.write_text("SKU: TEST-1\n净重：135g", encoding="utf8")
        (self.root / "photo.png").write_bytes(b"not decoded without a reader")
        with patch("product_evidence_guard.engine.resolve_openvino_device", side_effect=MemoryError("low commit")) as resolver:
            summary = analyze_directory(self.root, self.out, openvino_vlm_model=str(self.base / "model"))
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(self.weight()["fact_status"], "verified")
        self.assertFalse(summary["local_ai"]["image_reader"])
        self.assertIn("low commit", summary["local_ai"]["unavailable_reason"])
        coverage = coverage_summary(summary)
        self.assertEqual(coverage["status"], "partial")
        self.assertTrue(coverage["can_retry"])

    def test_resource_fallback_preserves_unchanged_ai_human_adoption_then_recovers(self):
        reader = QwenVlReader(FakeBackend())
        model = str(self.base / "model")
        first = analyze_directory(self.root, self.out, openvino_vlm_model=model, preloaded_image_reader=reader, device="CPU", semantic_assist=True)
        group = self.weight()
        apply_review_action(self.out, session_id=first["session_id"], action="adopt", group_id=group["group_id"],
                            expected_candidate_ids=group["candidate_ids"], candidate_id=group["candidate_ids"][0])
        with patch("product_evidence_guard.engine.HybridImageReader.from_openvino", side_effect=AssertionError("must not reload")):
            fallback = analyze_directory(self.root, self.out, openvino_vlm_model=model, device="CPU", preloaded_model_error="low commit", semantic_assist=True)
        self.assertEqual(fallback["unchanged_files_reused"], ["a.txt"])
        self.assertTrue(self.weight()["human_approved"])
        self.assertEqual(self.weight()["selected_value"], "≤135")
        self.path.write_text("SKU: TEST-1\n净重：≤135g（运输状态）\n额定功率：12W", encoding="utf8")
        analyze_directory(self.root, self.out, openvino_vlm_model=model, device="CPU", preloaded_model_error="low commit", semantic_assist=True)
        self.assertFalse(self.weight()["human_approved"])
        self.assertEqual(self.weight()["fact_status"], "pending_confirmation")
        recovered = analyze_directory(self.root, self.out, openvino_vlm_model=model, device="CPU", preloaded_image_reader=reader, semantic_assist=True)
        self.assertEqual(self.weight()["fact_status"], "verified")
        self.assertFalse(self.weight()["human_approved"])
        self.assertIsNone(recovered["local_ai"]["unavailable_reason"])

    def test_explicit_disable_is_not_mistaken_for_temporary_failure(self):
        reader = QwenVlReader(FakeBackend())
        model = str(self.base / "model")
        analyze_directory(self.root, self.out, openvino_vlm_model=model, preloaded_image_reader=reader, device="CPU", semantic_assist=True)
        summary = analyze_directory(self.root, self.out, openvino_vlm_model=model, device="CPU",
                                    preloaded_model_error="low commit", semantic_assist=False)
        self.assertTrue(summary["engine_signature_changed"])
        self.assertEqual(self.weight()["fact_status"], "pending_confirmation")

    def test_resident_failed_model_is_neither_ready_nor_cached(self):
        from tests.test_device_and_resident_model import ResidentModelCache, FakeCore, resolve_openvino_device
        calls = []
        def load(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                raise MemoryError("low commit")
            return object()
        cache = ResidentModelCache(vlm_factory=load, device_resolver=lambda request: resolve_openvino_device(
            request, core=FakeCore({"CPU": "test CPU"})))
        options = {"openvino_model": None, "openvino_vlm_model": str(self.base), "requested_device": "CPU"}
        first = cache.prepare(**options)
        self.assertIsNone(first["vlm"])
        self.assertIn("low commit", first["error"])
        second = cache.prepare(**options)
        self.assertIsNotNone(second["vlm"])
        self.assertTrue(cache.prepare(**options)["reused"])
        self.assertEqual(len(calls), 2)

    def test_public_entry_exposes_semantic_rollback_without_disabling_images(self):
        from tests.test_device_and_resident_model import client
        args = client.build_parser().parse_args(["analyze", str(self.root), "--model", str(self.base), "--no-semantic-assist"])
        payload = client._command_payload(args)
        self.assertTrue(payload["no_semantic_assist"])
        self.assertFalse(payload["deterministic_only"])
        self.assertEqual(payload["openvino_vlm_model"], str(self.base.resolve()))
        default = client._command_payload(client.build_parser().parse_args(["analyze", str(self.root)]))
        enabled = client._command_payload(client.build_parser().parse_args(["analyze", str(self.root), "--semantic-assist"]))
        self.assertTrue(default["no_semantic_assist"])
        self.assertFalse(enabled["no_semantic_assist"])
