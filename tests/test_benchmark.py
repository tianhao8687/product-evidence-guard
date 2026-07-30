from __future__ import annotations

from dataclasses import dataclass
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import benchmark
from product_evidence_guard.extractor import FIELD_SPECS
from product_evidence_guard.models import FactCandidate
from product_evidence_guard.normalization import normalize_value
from product_evidence_guard.parsers import parse_file, sha256_file


def module_is_importable(name: str) -> bool:
    if importlib.util.find_spec(name) is None:
        return False
    try:
        __import__(name)
    except (ImportError, OSError):
        return False
    return True


PIL_AVAILABLE = module_is_importable("PIL")
DOCUMENT_READERS_AVAILABLE = all(
    module_is_importable(name) for name in ("docx", "openpyxl", "pypdf")
)


def make_candidate(
    field: str,
    raw_value: str,
    *,
    source_file: str,
    candidate_id: str,
) -> FactCandidate:
    spec = next(item for item in FIELD_SPECS if item.name == field)
    normalized = normalize_value(field, raw_value)
    return FactCandidate(
        candidate_id=candidate_id,
        field=field,
        field_label=spec.label,
        raw_value=raw_value,
        normalized_value=normalized.value,
        normalized_unit=normalized.unit,
        source_block_id=f"block-{candidate_id}",
        source_file=source_file,
        source_kind="synthetic_test",
        file_hash="a" * 64,
        locator={"test": True},
        raw_text=f"{spec.label}: {raw_value}",
        recognition_confidence=1.0,
        mapping_confidence=1.0,
        extraction_method="injected_test_backend",
        scope=spec.scope,
        notes=list(normalized.notes),
    )


@dataclass
class FakeImageResult:
    sample_id: str
    fact_candidates: list[FactCandidate]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "image_file": self.sample_id,
            "ok": True,
            "raw_transcription_output": '{"schema_version":1,"items":[]}',
            "raw_mapping_output": '{"schema_version":1,"items":[]}',
            "fact_candidates": [
                candidate.to_dict() for candidate in self.fact_candidates
            ],
            "errors": [],
        }


class FakeReader:
    def __init__(self, expected_by_path: dict[str, list[dict[str, str]]]) -> None:
        self.expected_by_path = expected_by_path

    def analyze_image(
        self,
        image_path: Path,
        *,
        root: Path,
        deadline: float | None = None,
    ) -> FakeImageResult:
        self.assert_deadline = deadline
        relative = image_path.relative_to(root).as_posix()
        mappings = self.expected_by_path[relative]
        candidates = [
            make_candidate(
                mapping["field"],
                mapping["raw_value"],
                source_file=relative,
                candidate_id=f"{image_path.stem}-{index}",
            )
            for index, mapping in enumerate(mappings)
        ]
        return FakeImageResult(relative, candidates)


class BenchmarkDatasetTests(unittest.TestCase):
    dataset_context: tempfile.TemporaryDirectory[str] | None = None
    dataset: Path | None = None
    first_hashes: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        if not PIL_AVAILABLE:
            return
        cls.dataset_context = tempfile.TemporaryDirectory()
        cls.dataset = Path(cls.dataset_context.name) / "generated"
        benchmark.generate_dataset(
            cls.dataset,
            seed=benchmark.DEFAULT_SEED,
            image_count=benchmark.MINIMUM_IMAGE_COUNT,
        )
        manifest = benchmark.load_manifest(cls.dataset)
        cls.first_hashes = {
            str(row["relative_path"]): str(row["sha256"])
            for row in manifest["samples"]
        }

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.dataset_context is not None:
            cls.dataset_context.cleanup()

    def require_dataset(self) -> Path:
        if self.dataset is None:
            self.skipTest("Pillow is not installed in this CI environment")
        return self.dataset

    def test_minimum_image_count_cannot_be_reduced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                benchmark.generate_dataset(
                    Path(temporary) / "dataset",
                    image_count=benchmark.MINIMUM_IMAGE_COUNT - 1,
                )

    def test_generation_has_thirty_images_and_all_document_types(self) -> None:
        dataset = self.require_dataset()
        manifest = benchmark.load_manifest(dataset)
        image_rows = [
            row for row in manifest["samples"] if row["sample_type"] == "image"
        ]
        document_rows = [
            row
            for row in manifest["samples"]
            if row["sample_type"] == "document"
        ]
        self.assertGreaterEqual(len(image_rows), 30)
        self.assertGreaterEqual(len(document_rows), 4)
        self.assertEqual(
            {Path(row["relative_path"]).suffix for row in document_rows}
            & {".txt", ".docx", ".xlsx", ".pdf"},
            {".txt", ".docx", ".xlsx", ".pdf"},
        )
        categories = {str(row["category"]) for row in image_rows}
        self.assertEqual(
            categories,
            {"clear", "degraded", "mixed", "special"},
        )
        self.assertTrue(any(row["contains_prompt_injection_text"] for row in image_rows))
        self.assertTrue((dataset / benchmark.MANIFEST_NAME).is_file())

    def test_same_seed_regeneration_has_identical_expected_hashes(self) -> None:
        dataset = self.require_dataset()
        benchmark.generate_dataset(
            dataset,
            seed=benchmark.DEFAULT_SEED,
            image_count=benchmark.MINIMUM_IMAGE_COUNT,
        )
        regenerated = benchmark.load_manifest(dataset)
        hashes = {
            str(row["relative_path"]): str(row["sha256"])
            for row in regenerated["samples"]
        }
        self.assertEqual(hashes, self.first_hashes)
        first = benchmark.dataset_digest(dataset, regenerated)
        second = benchmark.dataset_digest(dataset, regenerated)
        self.assertEqual(first, second)

    @unittest.skipUnless(
        DOCUMENT_READERS_AVAILABLE,
        "optional document readers are not installed",
    )
    def test_generated_docx_xlsx_and_pdf_are_parseable(self) -> None:
        dataset = self.require_dataset()
        manifest = benchmark.load_manifest(dataset)
        rows = [
            row
            for row in manifest["samples"]
            if row["sample_type"] == "document"
            and Path(row["relative_path"]).suffix in {".docx", ".xlsx", ".pdf"}
        ]
        for row in rows:
            blocks = parse_file(dataset / row["relative_path"], dataset)
            self.assertTrue(blocks, row["relative_path"])

    def test_mock_run_emits_all_required_metrics_and_raw_results(self) -> None:
        dataset = self.require_dataset()
        manifest = benchmark.load_manifest(dataset)
        expected_by_path = {
            str(row["relative_path"]): [
                dict(mapping) for mapping in row["expected_mappings"]
            ]
            for row in manifest["samples"]
            if row["sample_type"] == "image"
        }

        def load_documents(_root, _rows):
            return (
                [
                    make_candidate(
                        "net_weight",
                        "320g",
                        source_file="documents/a.txt",
                        candidate_id="doc-weight-a",
                    ),
                    make_candidate(
                        "net_weight",
                        "350g",
                        source_file="documents/b.txt",
                        candidate_id="doc-weight-b",
                    ),
                    make_candidate(
                        "quantity",
                        "2件",
                        source_file="documents/a.txt",
                        candidate_id="doc-quantity-a",
                    ),
                    make_candidate(
                        "quantity",
                        "3件",
                        source_file="documents/b.txt",
                        candidate_id="doc-quantity-b",
                    ),
                ],
                [],
            )

        output = dataset.parent / "results"
        result = benchmark.run_benchmark(
            dataset,
            output,
            model_path=dataset.parent / "fake-model",
            reader_factory=lambda _path, _device: FakeReader(expected_by_path),
            document_loader=load_documents,
            memory_probe=lambda: 123456,
        )
        metrics = result["metrics"]
        self.assertEqual(result["mode"], "injected_test_backend")
        self.assertIn("model_load_seconds", metrics)
        self.assertIn("single_image_seconds", metrics)
        self.assertIn("batch_total_seconds", metrics)
        self.assertEqual(metrics["peak_memory_bytes"], 123456)
        self.assertEqual(metrics["field_recall"]["value"], 1.0)
        self.assertEqual(metrics["conflict_recall"]["value"], 1.0)
        self.assertEqual(metrics["numeric_recognition_accuracy"]["value"], 1.0)
        self.assertEqual(metrics["unit_recognition_accuracy"]["value"], 1.0)
        self.assertEqual(metrics["field_mapping"]["f1"], 1.0)
        self.assertEqual(metrics["parameter_miss_rate"]["value"], 0.0)
        self.assertEqual(
            metrics["nonexistent_content_hallucination_rate"]["value"],
            0.0,
        )
        self.assertEqual(metrics["conflict_classification"]["accuracy"], 1.0)
        self.assertEqual(metrics["incremental_analysis"]["status"], "completed")
        self.assertTrue(metrics["hash_stabilization"]["stable"])
        self.assertTrue((output / benchmark.RESULT_NAME).is_file())
        self.assertTrue((output / "per-sample-metrics.csv").is_file())
        self.assertEqual(
            len(list((output / "raw-model-output").glob("*.json"))),
            benchmark.MINIMUM_IMAGE_COUNT,
        )

    def test_expected_conflicts_match_generated_document_semantics_exactly(self) -> None:
        dataset = self.require_dataset()
        manifest = benchmark.load_manifest(dataset)
        document_rows = [
            row
            for row in manifest["samples"]
            if row["sample_type"] == "document"
        ]
        candidates, _errors = benchmark._document_candidates(
            dataset,
            document_rows,
        )
        _, groups, _ = benchmark.build_graph(candidates)
        actual = {
            (group.field, group.classification)
            for group in groups
            if group.classification == "strong_conflict"
        }
        expected = {
            (row["field"], row["classification"])
            for row in manifest["expected_conflicts"]
        }
        self.assertEqual(actual, expected)

    def test_manifest_hash_tampering_is_detected(self) -> None:
        dataset = self.require_dataset()
        manifest = benchmark.load_manifest(dataset)
        first = manifest["samples"][0]
        path = dataset / first["relative_path"]
        original = path.read_bytes()
        try:
            path.write_bytes(original + b"tampered")
            with self.assertRaises(ValueError):
                benchmark.dataset_digest(dataset, manifest)
        finally:
            path.write_bytes(original)
        self.assertEqual(sha256_file(path), first["sha256"])

    def test_expected_units_use_boundaries(self) -> None:
        dataset = self.require_dataset()
        manifest = benchmark.load_manifest(dataset)
        by_value = {
            row["expected_mappings"][0]["raw_value"]: row[
                "expected_transcriptions"
            ][0]["unit_tokens"]
            for row in manifest["samples"]
            if row.get("sample_type") == "image"
            and row.get("expected_mappings")
        }
        self.assertEqual(by_value["0.32kg"], ["kg"])
        self.assertEqual(by_value["PEG-320"], [])
        self.assertEqual(by_value["304不锈钢"], [])


class BenchmarkMetricTests(unittest.TestCase):
    def test_saved_raw_run_is_rescored_with_current_strict_schema(self) -> None:
        visual_item = {
            "id": "visual-001",
            "raw_text": "净重 320g",
            "bbox_1000": None,
            "position_precision": "unavailable",
            "legibility": "clear",
            "confidence_estimate": 0.9,
            "confidence_source": "model_self_assessment",
        }
        mapping_item = {
            "transcription_id": "visual-001",
            "field": "net_weight",
            "raw_value": "320g",
            "mapping_confidence_estimate": 0.9,
            "confidence_source": "model_self_assessment",
        }
        serialized = {
            "raw_transcription_output": json.dumps(
                {"schema_version": 999, "items": [visual_item]},
                ensure_ascii=False,
            ),
            "raw_mapping_output": json.dumps(
                {"schema_version": 1, "items": [mapping_item]},
                ensure_ascii=False,
            ),
            "transcription_repair_attempted": False,
            "mapping_repair_attempted": False,
            "fact_candidates": [
                {
                    "field": "net_weight",
                    "raw_value": "320g",
                    "locator": {"transcription_id": "visual-001"},
                }
            ],
        }

        rescored = benchmark._rescore_serialized_with_current_schema(serialized)

        self.assertEqual(rescored["fact_candidates"], [])
        self.assertIn(
            "unsupported_schema_version",
            rescored["schema_rescore"]["transcription_issue_codes"],
        )

    def test_field_recall_reports_raw_counts(self) -> None:
        rows = [
            {
                "sample_id": "a",
                "expected_mappings": [
                    {"field": "net_weight"},
                    {"field": "quantity"},
                ],
            }
        ]
        metric = benchmark._field_recall(rows, {"a": {"net_weight"}})
        self.assertEqual(metric, {"value": 0.5, "matched": 1, "expected": 2})

    def test_conflict_recall_reports_missing_groups(self) -> None:
        @dataclass
        class Group:
            field: str
            classification: str

        metric = benchmark._conflict_recall(
            [
                {"field": "net_weight", "classification": "strong_conflict"},
                {"field": "quantity", "classification": "strong_conflict"},
            ],
            [Group("net_weight", "strong_conflict")],
        )
        self.assertEqual(metric["value"], 0.5)
        self.assertEqual(metric["matched"], 1)
        self.assertEqual(metric["expected"], 2)
        self.assertEqual(metric["missing"][0]["field"], "quantity")

    def test_quality_metrics_penalize_missing_and_hallucinated_facts(self) -> None:
        rows = [
            {
                "sample_id": "expected",
                "expected_empty": False,
                "contains_prompt_injection_text": False,
                "expected_mappings": [{"field": "net_weight", "raw_value": "320g"}],
                "expected_transcriptions": [
                    {
                        "raw_text": "净重 320g",
                        "numeric_tokens": ["320"],
                        "unit_tokens": ["g"],
                    }
                ],
            },
            {
                "sample_id": "empty",
                "expected_empty": True,
                "contains_prompt_injection_text": True,
                "expected_mappings": [],
                "expected_transcriptions": [],
            },
        ]
        serialized = {
            "expected": {"fact_candidates": [], "transcriptions": []},
            "empty": {
                "fact_candidates": [
                    {"field": "power", "raw_value": "999W", "raw_text": "999W"}
                ]
            },
        }
        metrics, samples = benchmark._quality_metrics(rows, serialized)
        self.assertEqual(metrics["field_mapping"]["recall"], 0.0)
        self.assertEqual(metrics["parameter_miss_rate"]["value"], 1.0)
        self.assertEqual(
            metrics["nonexistent_content_hallucination_rate"]["value"],
            1.0,
        )
        self.assertTrue(samples["empty"]["hallucinated_fact"])

    def test_release_and_real_model_scripts_keep_safety_gates(self) -> None:
        package_script = (SCRIPTS_DIR / "package-release.ps1").read_text(
            encoding="utf-8"
        )
        for excluded in (".models", ".runtime", ".git", "samples\\real"):
            self.assertIn(excluded, package_script)
        self.assertIn("SHA256", package_script)

        real_script = (REPO_ROOT / "tests" / "test-real-model.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("mock", real_script.casefold())
        self.assertIn("visual-transcription.json", real_script)
        self.assertIn("run.ps1 status", real_script)
        self.assertIn("1GB", real_script)

        powershell_tests = (REPO_ROOT / "tests" / "test.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("deterministic-only", powershell_tests)
        self.assertIn("中文 空格", powershell_tests)


if __name__ == "__main__":
    unittest.main()
