from __future__ import annotations

import json
from pathlib import Path
import re
import unittest
from urllib.parse import unquote


REPO_ROOT = Path(__file__).resolve().parents[1]
IGNORED_PARTS = {
    ".git",
    ".models",
    ".runtime",
    ".tools",
    ".venv",
    "benchmark-output",
    "demo-output",
    "release",
    "__pycache__",
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
PUBLIC_SYNTHETIC_BENCHMARK = (
    REPO_ROOT / "docs" / "evidence" / "synthetic-benchmark-final-06f8360.json"
)


def _repository_files(suffix: str) -> list[Path]:
    return sorted(
        path
        for path in REPO_ROOT.rglob(f"*{suffix}")
        if not any(part in IGNORED_PARTS for part in path.parts)
        and not any(part.startswith("test-real-model-") for part in path.parts)
    )


class SubmissionContractTests(unittest.TestCase):
    def test_required_competition_artifacts_exist(self) -> None:
        required = (
            "SKILL.md",
            "info.json",
            "meta.json",
            "requirements.txt",
            "requirements.lock",
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "PRIVACY.md",
            "SECURITY.md",
            "scripts/run.ps1",
            "scripts/install-env.ps1",
            "scripts/install-qoder-skill.ps1",
            "scripts/client.py",
            "scripts/server.py",
            "scripts/protocol.py",
            "scripts/model_download.py",
            "scripts/generate-sbom.py",
            "scripts/generate-pdfium-notices.py",
            "docs/ARCHITECTURE.md",
            "docs/USER_GUIDE.md",
            "docs/MODEL_AND_RUNTIME.md",
            "docs/BENCHMARK.md",
            "docs/QODER_VALIDATION.md",
            "docs/COMPETITION_COMPLIANCE.md",
            "docs/DEMO_SCRIPT.md",
            "docs/ARTICLE_DRAFT.md",
            "docs/SUBMISSION_CHECKLIST.md",
            "docs/LIMITATIONS.md",
            "docs/assets/qoder/01-skill-discovered.png",
            "docs/assets/qoder/04-analysis-summary-redacted.png",
            "docs/assets/qoder/2026-08-05-real-image-controlled-documents.md",
            "docs/assets/qoder/2026-08-05-auto-trigger-matrix.md",
            "docs/assets/competition/architecture.svg",
            "docs/assets/competition/architecture.png",
            "docs/assets/competition/performance.svg",
            "docs/assets/competition/performance.png",
            "docs/assets/competition/demo-3min.zh-CN.srt",
            "docs/assets/competition/demo-5min.zh-CN.srt",
            "docs/evidence/final-local-regression-20260805.json",
            "docs/evidence/sbom.json",
            "docs/evidence/license-inventory.json",
            "docs/evidence/pypdfium2-notices.json",
            "docs/evidence/cold-start-distribution-20260805.json",
            "docs/evidence/network-tcp-observation-20260805.json",
            "docs/evidence/performance-network-validation-20260805.md",
            "docs/evidence/synthetic-benchmark-final-06f8360.json",
        )
        missing = [item for item in required if not (REPO_ROOT / item).is_file()]
        self.assertEqual(missing, [])

        article = (REPO_ROOT / "docs" / "ARTICLE_DRAFT.md").read_text(
            encoding="utf-8"
        )
        cjk_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", article))
        self.assertLessEqual(cjk_count, 4000)

    def test_video_assets_are_publishable_and_timed(self) -> None:
        from PIL import Image

        image_contract = {
            "architecture.png": (2400, 1320),
            "performance.png": (2400, 1040),
        }
        asset_root = REPO_ROOT / "docs" / "assets" / "competition"
        for name, expected_size in image_contract.items():
            with self.subTest(image=name), Image.open(asset_root / name) as image:
                self.assertEqual(image.format, "PNG")
                self.assertEqual(image.size, expected_size)
                image.verify()

        cue_pattern = re.compile(
            r"(?ms)^(?P<index>\d+)\r?\n"
            r"(?P<start>\d{2}:\d{2}:\d{2},\d{3}) --> "
            r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})\r?\n"
            r"(?P<text>.+?)(?=\r?\n\r?\n\d+\r?\n|\Z)"
        )

        def milliseconds(value: str) -> int:
            hours, minutes, tail = value.split(":")
            seconds, millis = tail.split(",")
            return (
                int(hours) * 3_600_000
                + int(minutes) * 60_000
                + int(seconds) * 1_000
                + int(millis)
            )

        subtitle_contract = {
            "demo-3min.zh-CN.srt": (10, 180_000),
            "demo-5min.zh-CN.srt": (12, 300_000),
        }
        for name, (expected_cues, expected_end) in subtitle_contract.items():
            raw = (asset_root / name).read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), name)
            content = raw.decode("utf-8")
            cues = list(cue_pattern.finditer(content))
            self.assertEqual(len(cues), expected_cues, name)
            previous_end = 0
            for position, cue in enumerate(cues, start=1):
                start = milliseconds(cue.group("start"))
                end = milliseconds(cue.group("end"))
                self.assertEqual(int(cue.group("index")), position)
                self.assertGreater(end, start)
                self.assertGreaterEqual(start, previous_end)
                previous_end = end
            self.assertEqual(previous_end, expected_end)
            self.assertIn("已验证", content)
            self.assertIn("待完成", content)

        article = (REPO_ROOT / "docs" / "ARTICLE_DRAFT.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("assets/competition/architecture.png", article)
        self.assertIn("assets/competition/performance.png", article)

    def test_skill_frontmatter_and_trigger_contract(self) -> None:
        content = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", content, re.DOTALL)
        self.assertIsNotNone(match, "SKILL.md must start with YAML frontmatter")
        frontmatter: dict[str, str] = {}
        for line in match.group(1).splitlines():
            key, separator, value = line.partition(":")
            self.assertTrue(separator, f"invalid frontmatter line: {line}")
            frontmatter[key.strip()] = value.strip()
        self.assertEqual(frontmatter.get("name"), "local-product-evidence-guard")
        description = frontmatter.get("description", "")
        self.assertLessEqual(len(description), 1024)
        for trigger in (
            "商品资料",
            "包装图",
            "说明书",
            "冲突",
            "本地",
            "离线",
            "OpenVINO",
            "Intel",
            "AIPC",
            "product facts",
            "verify",
            "conflict",
            "local",
            "offline",
        ):
            self.assertIn(trigger.lower(), description.lower())
        self.assertIn("唯一公开入口是 `scripts\\run.ps1`", content)
        self.assertIn("--continue", content)
        self.assertIn("不能自动确认正式事实", content)
        self.assertIn("不使用云端 OCR/VLM 回退", content)

    def test_info_and_meta_identity_are_consistent(self) -> None:
        info = json.loads((REPO_ROOT / "info.json").read_text(encoding="utf-8"))
        meta = json.loads((REPO_ROOT / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(info["name"], "local-product-evidence-guard")
        self.assertEqual(meta["name"], info["name"])
        self.assertEqual(meta["version"], info["version"])
        self.assertEqual(info["python_version"], "3.11")
        self.assertEqual(info["exit_codes"]["3"], "model_download_in_progress")
        model = info["models"][0]
        self.assertEqual(
            model["model_id"],
            "OpenVINO/Qwen3-VL-8B-Instruct-int4-ov",
        )
        self.assertEqual(model["id"], model["model_id"])
        self.assertGreaterEqual(len(model["required_files"]), 20)
        self.assertGreaterEqual(len(meta["use_cases"]), 5)

    def test_repository_json_files_are_utf8_and_parseable(self) -> None:
        failures: list[str] = []
        for path in _repository_files(".json"):
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                failures.append(f"{path.relative_to(REPO_ROOT)}: {exc}")
        self.assertEqual(failures, [])

    def test_public_synthetic_benchmark_evidence_contract(self) -> None:
        raw = PUBLIC_SYNTHETIC_BENCHMARK.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "public JSON must not use a BOM")
        text = raw.decode("utf-8", errors="strict")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON number: {value}")

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate JSON key: {key}")
                result[key] = value
            return result

        evidence = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
        self.assertNotRegex(text, r"(?i)(?:[a-z]:[\\/]|\\\\[^\\/\r\n]+[\\/]|/(?:users|home)/)")
        self.assertNotRegex(
            text,
            r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization)",
        )
        self.assertNotIn("model_path", evidence)

        self.assertEqual(evidence["artifact_kind"], "public_synthetic_benchmark_summary")
        self.assertEqual(evidence["benchmark_commit"], "06f8360")
        self.assertEqual(evidence["run_id"], "benchmark-final-06f8360-20260730")
        self.assertEqual(evidence["status"], "completed")
        self.assertEqual(evidence["mode"], "real_openvino")
        self.assertEqual(evidence["model_id"], "OpenVINO/Qwen3-VL-8B-Instruct-int4-ov")
        self.assertEqual(evidence["requested_device"], "CPU")
        self.assertEqual(
            evidence["dataset"],
            {
                "document_count": 10,
                "image_count": 30,
                "partition": "synthetic",
                "seed": 20260730,
            },
        )
        self.assertEqual(evidence["image_success_count"], 30)
        self.assertEqual(evidence["image_failure_count"], 0)
        self.assertEqual(evidence["document_errors"], [])
        self.assertEqual(
            evidence["limitations"],
            [
                "synthetic dataset metrics are not real-business accuracy",
                "requested_device is not a claim about actual hardware execution",
                "model self-assessed confidence is not an accuracy probability",
            ],
        )
        self.assertEqual(
            evidence["publication"],
            {
                "privacy_review": "passed",
                "removed_fields": ["model_path"],
                "source_artifact": "benchmark-final-06f8360-20260730/benchmark-results.json",
                "source_sha256": "91fe74baed3863ca641d61b253fffd81dd22862143ea8036dfdee44c150f139b",
            },
        )

        metrics = evidence["metrics"]
        self.assertEqual(
            set(metrics),
            {
                "batch_total_seconds",
                "cold_start_total_seconds",
                "conflict_classification",
                "conflict_recall",
                "field_mapping",
                "field_recall",
                "hash_stabilization",
                "incremental_analysis",
                "model_load_seconds",
                "model_size_bytes",
                "nonexistent_content_hallucination_rate",
                "numeric_recognition_accuracy",
                "parameter_miss_rate",
                "peak_gpu_memory_bytes",
                "peak_memory_bytes",
                "per_image_median_seconds",
                "per_image_p90_seconds",
                "prompt_injection_fact_rate",
                "single_image_seconds",
                "unit_recognition_accuracy",
                "warm_call_median_seconds",
            },
        )
        self.assertEqual(metrics["model_size_bytes"], 5462526140)
        self.assertEqual(metrics["model_load_seconds"], 3.7012639999884414)
        self.assertEqual(metrics["single_image_seconds"], 75.96934589999728)
        self.assertEqual(metrics["cold_start_total_seconds"], 79.67060989998572)
        self.assertEqual(metrics["warm_call_median_seconds"], 76.01341300000786)
        self.assertEqual(metrics["batch_total_seconds"], 2221.6386169999896)
        self.assertEqual(metrics["per_image_median_seconds"], 75.99137945000257)
        self.assertEqual(metrics["per_image_p90_seconds"], 85.35025920000044)
        self.assertEqual(metrics["peak_memory_bytes"], 11663728640)
        self.assertIsNone(metrics["peak_gpu_memory_bytes"])
        self.assertEqual(metrics["field_recall"], {"expected": 25, "matched": 25, "value": 1.0})
        self.assertEqual(metrics["field_mapping"]["true_positive"], 25)
        self.assertEqual(metrics["numeric_recognition_accuracy"]["matched"], 27)
        self.assertEqual(metrics["unit_recognition_accuracy"]["matched"], 15)
        self.assertEqual(metrics["parameter_miss_rate"]["missed"], 0)
        self.assertEqual(metrics["nonexistent_content_hallucination_rate"]["hallucinated_images"], 0)
        self.assertEqual(metrics["prompt_injection_fact_rate"]["images_with_accepted_facts"], 0)
        self.assertEqual(
            metrics["conflict_classification"],
            {
                "accuracy": 1.0,
                "f1": 1.0,
                "false_negative": 0,
                "false_positive": 0,
                "field_universe_count": 12,
                "precision": 1.0,
                "recall": 1.0,
                "true_negative": 10,
                "true_positive": 2,
            },
        )
        self.assertEqual(metrics["incremental_analysis"]["second_pass_reused_files"], 10)
        self.assertTrue(metrics["hash_stabilization"]["stable"])

        benchmark = (REPO_ROOT / "docs" / "BENCHMARK.md").read_text(encoding="utf-8")
        article = (REPO_ROOT / "docs" / "ARTICLE_DRAFT.md").read_text(encoding="utf-8")
        evidence_index = (REPO_ROOT / "docs" / "evidence" / "README.md").read_text(
            encoding="utf-8"
        )
        link_name = "synthetic-benchmark-final-06f8360.json"
        self.assertIn(f"evidence/{link_name}", benchmark)
        self.assertIn(f"evidence/{link_name}", article)
        self.assertIn(f"]({link_name})", evidence_index)
        for value in (
            "3.701264",
            "75.969346",
            "79.670610",
            "76.013413",
            "85.350259",
            "2221.638617",
            "11,663,728,640",
        ):
            self.assertIn(value, benchmark)
            self.assertIn(value, article)

    def test_all_local_markdown_links_resolve(self) -> None:
        failures: list[str] = []
        for document in _repository_files(".md"):
            # Exact upstream license/notice files are copied byte-for-byte and
            # may reference files that were not shipped in the wheel.  Their
            # integrity is verified separately; rewriting them would destroy
            # the provenance evidence.
            if document.is_relative_to(REPO_ROOT / "docs" / "evidence" / "licenses"):
                continue
            content = document.read_text(encoding="utf-8")
            for raw_target in MARKDOWN_LINK.findall(content):
                target = raw_target.strip().strip("<>")
                if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                # Markdown titles are not used by this repository. Splitting
                # here still makes the validator fail closed if one is added.
                target = target.split("#", 1)[0]
                resolved = (document.parent / unquote(target)).resolve()
                if not resolved.exists():
                    failures.append(
                        f"{document.relative_to(REPO_ROOT)} -> {raw_target}"
                    )
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
