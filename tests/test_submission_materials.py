from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = REPO_ROOT / "docs" / "ARCHITECTURE.md"
AUDIT = REPO_ROOT / "docs" / "evidence" / "mermaid-static-audit-20260805.json"
PR_DRAFT = REPO_ROOT / "docs" / "PR_DESCRIPTION_DRAFT.md"
SUPPORTED_MERMAID_DECLARATION = re.compile(
    r"(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)|stateDiagram-v2"
)


def _public_markdown_files() -> list[Path]:
    return sorted(
        [*REPO_ROOT.glob("*.md"), *(REPO_ROOT / "docs").rglob("*.md")]
        + list((REPO_ROOT / "samples" / "real").rglob("*.md"))
    )


def _mermaid_blocks(path: Path) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    start_line: int | None = None
    body: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if start_line is None:
            if re.fullmatch(r"```mermaid\s*", line):
                start_line = line_number
                body = []
            continue
        if re.fullmatch(r"```\s*", line):
            blocks.append((start_line, body))
            start_line = None
            body = []
        else:
            body.append(line)
    if start_line is not None:
        raise AssertionError(f"unclosed Mermaid fence in {path}:{start_line}")
    return blocks


class SubmissionMaterialsContractTests(unittest.TestCase):
    def test_all_mermaid_blocks_pass_recorded_static_contract(self) -> None:
        documents = _public_markdown_files()
        found: list[dict[str, object]] = []
        for document in documents:
            for start_line, body in _mermaid_blocks(document):
                self.assertTrue(body, f"empty Mermaid block: {document}:{start_line}")
                declaration = body[0].strip()
                self.assertRegex(
                    declaration,
                    rf"\A{SUPPORTED_MERMAID_DECLARATION.pattern}\Z",
                    f"unsupported Mermaid declaration: {document}:{start_line}",
                )
                source = "\n".join(body)
                self.assertEqual(source.count('"') % 2, 0)
                for opening, closing in (("[", "]"), ("{", "}"), ("(", ")")):
                    self.assertEqual(
                        source.count(opening),
                        source.count(closing),
                        f"unbalanced {opening}{closing}: {document}:{start_line}",
                    )
                found.append(
                    {
                        "file": document.relative_to(REPO_ROOT).as_posix(),
                        "start_line": start_line,
                        "declaration": declaration,
                    }
                )

        audit = json.loads(AUDIT.read_text(encoding="utf-8"))
        self.assertEqual(audit["repository_commit"], "618036973caa923047ab6be4023dc6acb6b5afa1")
        self.assertEqual(audit["scope"]["markdown_files_scanned"], len(documents))
        self.assertEqual(audit["scope"]["mermaid_block_count"], len(found))
        self.assertEqual(audit["scope"]["mermaid_file_count"], len({item["file"] for item in found}))
        self.assertEqual(audit["blocks"], found)
        self.assertEqual(len(found), 5)
        self.assertTrue(all(item["file"] == "docs/ARCHITECTURE.md" for item in found))
        self.assertTrue(all(audit["static_checks"].values()))
        self.assertFalse(audit["local_tooling"]["node_on_path"])
        self.assertFalse(audit["local_tooling"]["mermaid_cli_on_path"])
        self.assertFalse(audit["local_tooling"]["local_mermaid_javascript_found"])
        self.assertIsNone(audit["local_tooling"]["renderer_used"])
        self.assertEqual(audit["status"], "passed_static_checks_only")
        self.assertIn("not proof", audit["claim_boundary"])

    def test_pr_description_separates_evidence_and_external_actions(self) -> None:
        content = PR_DRAFT.read_text(encoding="utf-8")
        for heading in (
            "## 功能与文件变化",
            "## 测试命令与冻结结果",
            "## 真实模型",
            "## Qoder",
            "## 离线与网络边界",
            "## Benchmark",
            "## CI",
            "## Mermaid 审计",
            "## 已知风险与失败边界",
            "## 合并/投稿前的用户操作",
        ):
            self.assertIn(heading, content)

        for exact_evidence in (
            "618036973caa923047ab6be4023dc6acb6b5afa1",
            "263 项通过，84.891 s，0 跳过",
            "246 项通过，87.329 s，0 跳过",
            "246 项通过，89.646 s",
            "099454e83c154d6f50456780442871308632fa0c878379238dee298aa2125776",
            "3.6064/57.1824/61.895 s",
            "26.7046 s",
            "35 个约 100 ms TCP 状态观察周期",
            "30 张图片、10 份文档",
            "30/30 成功",
            "旧 head `4295ab8`",
        ):
            self.assertIn(exact_evidence, content)

        for command in (
            "powershell -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\\tests\\test.ps1",
            ".\\.venv\\Scripts\\python.exe -m unittest tests.test_submission_materials -v",
            ".\\.venv\\Scripts\\python.exe -m unittest tests.test_submission_contract -v",
            "git diff --check",
        ):
            self.assertIn(command, content)

        for evidence_path in (
            "docs/evidence/final-local-regression-20260805.json",
            "release/local-product-evidence-guard-v1.0.0.verification.json",
            "docs/evidence/synthetic-benchmark-final-06f8360.json",
            "docs/evidence/qoder-install-integrity-20260805.json",
            "docs/evidence/mermaid-static-audit-20260805.json",
            "docs/evidence/log-privacy-contract-20260805.json",
            "docs/evidence/named-pipe-security-boundaries-20260805.json",
            "docs/evidence/remote-pr-snapshot-20260805.json",
        ):
            self.assertIn(evidence_path, content)
            self.assertTrue((REPO_ROOT / evidence_path).is_file())

        self.assertIn("当前本地脱敏分支更新尚未推送", content)
        self.assertIn("真实渲染待验证", content)
        self.assertIn("用户本人登录 ModelScope", content)
        self.assertNotIn("当前提交的最新远端 Actions 已通过", content)
        self.assertNotIn("已证明零外连", content)


if __name__ == "__main__":
    unittest.main()
