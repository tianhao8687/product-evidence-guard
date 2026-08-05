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
            "docs/assets/qoder/04-analysis-summary-redacted.png",
            "docs/assets/qoder/2026-08-05-real-image-controlled-documents.md",
            "docs/assets/qoder/2026-08-05-auto-trigger-matrix.md",
            "docs/assets/competition/architecture.svg",
            "docs/assets/competition/performance.svg",
            "docs/evidence/final-local-regression-20260805.json",
            "docs/evidence/sbom.json",
            "docs/evidence/license-inventory.json",
            "docs/evidence/pypdfium2-notices.json",
            "docs/evidence/cold-start-distribution-20260805.json",
            "docs/evidence/network-tcp-observation-20260805.json",
            "docs/evidence/performance-network-validation-20260805.md",
        )
        missing = [item for item in required if not (REPO_ROOT / item).is_file()]
        self.assertEqual(missing, [])

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
