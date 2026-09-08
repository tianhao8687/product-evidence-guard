from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "package-release.ps1"
GIT = shutil.which("git")
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")

REQUIRED_ROOT_FILES = {
    ".gitattributes": "* text=auto eol=lf\n*.png -text\n",
    ".gitignore": "release/\n*.log\n",
    "CHANGELOG.md": "# Changelog\n",
    "LICENSE": "Test fixture license\n",
    "PRIVACY.md": "# Privacy\n",
    "README.md": "# Release fixture\n",
    "SECURITY.md": "# Security\n",
    "SKILL.md": "# Skill\n",
    "THIRD_PARTY_NOTICES.md": "# Third-party notices\n",
    "info.json": '{"name":"fixture"}\n',
    "meta.json": '{"schema_version":1}\n',
    "pyproject.toml": "[project]\nname='fixture'\nversion='1.0.0'\n",
    "requirements.lock": "",
    "requirements.txt": "",
}
REQUIRED_DOCS = (
    "ARCHITECTURE.md",
    "ARTICLE_DRAFT.md",
    "BENCHMARK.md",
    "COMPETITION_COMPLIANCE.md",
    "CONTENT_WORKFLOW.md",
    "DEMO_SCRIPT.md",
    "LIMITATIONS.md",
    "MODEL_AND_RUNTIME.md",
    "NEXT_STEPS.md",
    "QODER_VALIDATION.md",
    "SUBMISSION_CHECKLIST.md",
    "USER_GUIDE.md",
)


def run_process(
    arguments: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {arguments!r}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


class ReleasePackageStaticContractTests(unittest.TestCase):
    def test_script_has_fail_closed_release_contract(self) -> None:
        script = PACKAGE_SCRIPT.read_text(encoding="utf-8")
        for required_gate in (
            "diff',\n        '--cached'",
            "'ls-files',\n            '--others'",
            "'ls-tree',",
            "HEAD^{commit}",
            "samples/real/.gitkeep",
            "samples/real/README.md",
            "ReparsePoint",
            "hardlink list",
            "MaximumFileBytes",
            "MaximumArchiveInputBytes",
            "*.log",
            ".safetensors",
            ".gitattributes",
            "requirements.lock",
            "StringComparer]::Ordinal",
            "0x04034b50",
            "0x5021",
            "Get-FileSha256Hex",
            "[System.Security.Cryptography.SHA256]::Create()",
            "[System.IO.FileShare]::Read",
            "Assert-SafeReleaseContent",
            "Windows 绝对路径",
            "Bearer 凭据",
            "docs/assets/qoder/04-analysis-summary.png",
            "docs/evidence/licenses/",
            "GetEncoding(28591)",
        ):
            self.assertIn(required_gate, script)
        self.assertNotIn("Get-FileHash", script)

    def test_repository_attributes_and_ignores_cover_release_artifacts(
        self,
    ) -> None:
        attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("* text=auto eol=lf", attributes)
        for binary_pattern in ("*.png -text", "*.pdf -text", "*.zip -text"):
            self.assertIn(binary_pattern, attributes)
        self.assertIn("docs/evidence/licenses/** -text -diff", attributes)

        ignores = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        for ignored in (
            "*.log",
            ".coverage",
            "htmlcov/",
            ".pytest_cache/",
            ".mypy_cache/",
            ".ruff_cache/",
        ):
            self.assertIn(ignored, ignores)


@unittest.skipUnless(
    GIT and POWERSHELL,
    "release integration tests require Git and PowerShell",
)
class ReleasePackageIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="release-package-test-"
        )
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, repository: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        assert GIT is not None
        return run_process(
            [
                GIT,
                "-c",
                "core.excludesFile=",
                "-c",
                "core.quotepath=false",
                *arguments,
            ],
            cwd=repository,
        )

    def create_repository(
        self,
        name: str,
        *,
        omit: set[str] | None = None,
        extra_files: dict[str, bytes] | None = None,
        hardlink: bool = False,
        symlink: bool = False,
    ) -> Path:
        repository = self.base / name
        repository.mkdir()
        omitted = omit or set()

        for relative_path, content in REQUIRED_ROOT_FILES.items():
            if relative_path in omitted:
                continue
            target = repository / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

        for document_name in REQUIRED_DOCS:
            relative_path = f"docs/{document_name}"
            if relative_path in omitted:
                continue
            target = repository / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f"# {document_name}\n",
                encoding="utf-8",
                newline="\n",
            )

        fixture_files: dict[str, bytes] = {
            ".github/workflows/tests.yml": b"name: tests\n",
            "product_evidence_guard/__init__.py": b'"""fixture"""\n',
            "product_evidence_guard/field_registry.json": (
                REPO_ROOT / "product_evidence_guard" / "field_registry.json"
            ).read_bytes(),
            "samples/generated-benchmark/documents/\u4e2d\u6587 \u8bf4\u660e.txt": (
                "\u56fa\u5b9a\u5b57\u8282\n".encode("utf-8")
            ),
            "samples/real/.gitkeep": b"",
            "samples/real/README.md": b"# Real samples stay local\n",
            "scripts/run-demo.sh": b"#!/usr/bin/env sh\nexit 0\n",
            "scripts/run.ps1": b"Write-Output 'fixture'\n",
            "tests/smoke.txt": b"fixture\n",
        }
        fixture_files.update(extra_files or {})
        for relative_path, content in fixture_files.items():
            if relative_path in omitted:
                continue
            target = repository / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

        package_target = repository / "scripts" / "package-release.ps1"
        package_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PACKAGE_SCRIPT, package_target)

        if hardlink:
            os.link(
                repository / "README.md",
                repository / "scripts" / "hardlinked-readme.md",
            )
        if symlink:
            os.symlink(
                "../README.md",
                repository / "scripts" / "symlinked-readme.md",
            )

        self.git(repository, "init", "-q")
        self.git(repository, "config", "user.email", "release-test@example.invalid")
        self.git(repository, "config", "user.name", "Release Test")
        run_demo = repository / "scripts" / "run-demo.sh"
        if os.name != "nt":
            run_demo.chmod(
                run_demo.stat().st_mode
                | stat.S_IXUSR
                | stat.S_IXGRP
                | stat.S_IXOTH
            )
        self.git(repository, "add", "--all")
        self.git(
            repository,
            "update-index",
            "--chmod=+x",
            "scripts/run-demo.sh",
        )
        self.git(repository, "commit", "-qm", "fixture")
        return repository

    def test_fixture_repository_is_clean_after_commit(self) -> None:
        repository = self.create_repository("clean-fixture")
        self.git(
            repository,
            "diff",
            "--cached",
            "--quiet",
            "--exit-code",
            "HEAD",
            "--",
        )
        self.git(
            repository,
            "diff",
            "--quiet",
            "--exit-code",
            "--",
        )
        status = self.git(repository, "status", "--short")
        self.assertEqual(status.stdout, "")

    def package(
        self,
        repository: Path,
        output_directory: Path,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        assert POWERSHELL is not None
        result = run_process(
            [
                POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(repository / "scripts" / "package-release.ps1"),
                "-Version",
                "9.8.7",
                "-OutputDirectory",
                str(output_directory),
            ],
            cwd=repository,
            check=False,
        )
        output_lines = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
        self.assertTrue(
            output_lines,
            f"packager returned no JSON\nstderr:\n{result.stderr}",
        )
        try:
            payload = json.loads(output_lines[-1])
        except json.JSONDecodeError as error:
            raise AssertionError(
                f"last output line is not JSON: {output_lines[-1]!r}\n"
                f"complete stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            ) from error
        return result, payload

    def assert_package_failure(
        self,
        repository: Path,
        output_directory: Path,
        expected_error_fragment: str,
    ) -> None:
        result, payload = self.package(repository, output_directory)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(payload.get("status"), "error")
        self.assertIn(expected_error_fragment, str(payload.get("error", "")))
        self.assertFalse(output_directory.exists())

    def test_two_clean_head_packages_are_byte_identical_stored_zips(
        self,
    ) -> None:
        separator_path = "docs/line\u2028separator.txt"
        extra_files = {separator_path: b"unicode separator path\n"}
        repository = self.create_repository(
            "repeatable",
            extra_files=extra_files,
        )
        first_output = self.base / "output-one"
        second_output = self.base / "output-two"

        first_result, first_payload = self.package(repository, first_output)
        second_result, second_payload = self.package(repository, second_output)
        self.assertEqual(first_result.returncode, 0, first_result.stdout)
        self.assertEqual(second_result.returncode, 0, second_result.stdout)
        self.assertEqual(first_payload["status"], "ready")
        self.assertEqual(second_payload["status"], "ready")
        self.assertEqual(first_payload["commit"], second_payload["commit"])

        first_zip = Path(str(first_payload["zip"]))
        second_zip = Path(str(second_payload["zip"]))
        first_bytes = first_zip.read_bytes()
        second_bytes = second_zip.read_bytes()
        self.assertEqual(first_bytes, second_bytes)
        digest = hashlib.sha256(first_bytes).hexdigest()
        self.assertEqual(first_payload["sha256"], digest)
        self.assertEqual(second_payload["sha256"], digest)
        self.assertEqual(
            Path(str(first_payload["sha256_file"])).read_bytes(),
            f"{digest}  {first_zip.name}\n".encode("ascii"),
        )

        with zipfile.ZipFile(first_zip) as archive:
            self.assertIsNone(archive.testzip())
            infos = archive.infolist()
            names = [item.filename for item in infos]
            self.assertEqual(names, sorted(names))
            self.assertEqual(len(names), len(set(names)))
            self.assertIn(".gitattributes", names)
            self.assertIn("manifest.json", names)
            self.assertIn("requirements.lock", names)
            self.assertIn("docs/BENCHMARK.md", names)
            self.assertIn(separator_path, names)
            self.assertIn(
                "samples/generated-benchmark/documents/\u4e2d\u6587 \u8bf4\u660e.txt",
                names,
            )
            self.assertIn("samples/real/.gitkeep", names)
            self.assertIn("samples/real/README.md", names)
            self.assertTrue(all(item.compress_type == zipfile.ZIP_STORED for item in infos))
            self.assertTrue(
                all(item.date_time == (2020, 1, 1, 0, 0, 0) for item in infos)
            )
            by_name = {item.filename: item for item in infos}
            self.assertEqual(
                (by_name["scripts/run-demo.sh"].external_attr >> 16) & 0o777,
                0o755,
            )
            self.assertEqual(
                (by_name["README.md"].external_attr >> 16) & 0o777,
                0o644,
            )
            manifest_bytes = archive.read("manifest.json")
            self.assertFalse(manifest_bytes.startswith(b"\xef\xbb\xbf"))
            self.assertTrue(manifest_bytes.endswith(b"\n"))
            manifest = json.loads(manifest_bytes)
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["name"], "local-product-evidence-guard")
            self.assertEqual(manifest["version"], "9.8.7")
            self.assertEqual(manifest["commit"], first_payload["commit"])
            self.assertEqual(manifest["source"], "clean HEAD tracked files")
            self.assertEqual(
                manifest["archive_format"],
                "deterministic ZIP/store",
            )
            source_names = [name for name in names if name != "manifest.json"]
            manifest_files = manifest["files"]
            manifest_paths = [item["path"] for item in manifest_files]
            self.assertEqual(manifest_paths, source_names)
            self.assertEqual(
                manifest["source_file_count"],
                len(source_names),
            )
            self.assertEqual(manifest["archive_file_count"], len(names))
            self.assertNotIn("manifest.json", manifest_paths)
            self.assertNotIn("sha256", manifest)
            tree = self.git(
                repository,
                "ls-tree",
                "-r",
                "--full-tree",
                "HEAD",
                "--",
            ).stdout.split("\n")
            git_entries: dict[str, tuple[str, str]] = {}
            for line in tree:
                if not line:
                    continue
                metadata, relative_path = line.split("\t", 1)
                mode, object_type, object_id = metadata.split()
                if relative_path in source_names:
                    self.assertEqual(object_type, "blob")
                    git_entries[relative_path] = (mode, object_id)
            self.assertEqual(set(git_entries), set(source_names))
            for item in manifest_files:
                info = by_name[item["path"]]
                mode, object_id = git_entries[item["path"]]
                self.assertEqual(item["mode"], mode)
                self.assertEqual(item["git_object"], object_id)
                self.assertEqual(item["size"], info.file_size)
            manifest_info = by_name["manifest.json"]
            self.assertEqual(manifest_info.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(manifest_info.date_time, (2020, 1, 1, 0, 0, 0))
            self.assertEqual(
                (manifest_info.external_attr >> 16) & 0o777,
                0o644,
            )
            self.assertEqual(first_payload["manifest"], "manifest.json")
            self.assertEqual(
                first_payload["source_file_count"],
                len(source_names),
            )
            self.assertEqual(first_payload["file_count"], len(names))
            self.assertEqual(
                first_payload["source_input_bytes"],
                sum(by_name[name].file_size for name in source_names),
            )
            self.assertEqual(
                first_payload["input_bytes"],
                sum(item.file_size for item in infos),
            )

    def test_rejects_dirty_tracked_file(self) -> None:
        repository = self.create_repository("dirty")
        with (repository / "README.md").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("dirty\n")
        self.assert_package_failure(
            repository,
            self.base / "dirty-output",
            "tracked",
        )

    def test_rejects_untracked_file(self) -> None:
        repository = self.create_repository("untracked")
        (repository / "docs" / "injected.txt").write_text(
            "must not enter release\n",
            encoding="utf-8",
            newline="\n",
        )
        self.assert_package_failure(
            repository,
            self.base / "untracked-output",
            "未跟踪",
        )

    def test_rejects_staged_index_change(self) -> None:
        repository = self.create_repository("staged")
        staged = repository / "docs" / "staged.txt"
        staged.write_text("staged\n", encoding="utf-8", newline="\n")
        self.git(repository, "add", "docs/staged.txt")
        self.assert_package_failure(
            repository,
            self.base / "staged-output",
            "索引与 HEAD",
        )

    def test_rejects_missing_required_document_in_head(self) -> None:
        repository = self.create_repository(
            "missing-doc",
            omit={"docs/BENCHMARK.md"},
        )
        self.assert_package_failure(
            repository,
            self.base / "missing-output",
            "HEAD 缺少必备发布文件：docs/BENCHMARK.md",
        )

    def test_rejects_tracked_root_manifest_collision(self) -> None:
        repository = self.create_repository(
            "tracked-manifest",
            extra_files={"manifest.json": b'{"not":"generated"}\n'},
        )
        self.assert_package_failure(
            repository,
            self.base / "tracked-manifest-output",
            "manifest.json 是打包器保留的生成条目",
        )

    def test_rejects_tracked_denylisted_file(self) -> None:
        repository = self.create_repository(
            "denylist",
            extra_files={"scripts/model.safetensors": b"not-a-model"},
        )
        self.assert_package_failure(
            repository,
            self.base / "denylist-output",
            "模型、可执行或归档扩展名",
        )

    def test_rejects_real_sample_payload(self) -> None:
        repository = self.create_repository(
            "real-sample",
            extra_files={"samples/real/customer.jpg": b"private"},
        )
        self.assert_package_failure(
            repository,
            self.base / "real-output",
            "samples/real 仅允许",
        )

    def test_rejects_private_absolute_path_in_text_content(self) -> None:
        private_path = "C" + ":\\Users\\Alice\\private\\report.json\n"
        repository = self.create_repository(
            "private-path-content",
            extra_files={"docs/private-path.txt": private_path.encode("utf-8")},
        )
        self.assert_package_failure(
            repository,
            self.base / "private-path-output",
            "Windows 绝对路径",
        )

    def test_rejects_bearer_credential_in_text_content(self) -> None:
        credential = "Bear" + "er abcdefghijklmnopqrstuvwxyz123456\n"
        repository = self.create_repository(
            "bearer-content",
            extra_files={"docs/credential.txt": credential.encode("utf-8")},
        )
        self.assert_package_failure(
            repository,
            self.base / "bearer-output",
            "Bearer 凭据",
        )

    def test_preserves_non_utf8_exact_upstream_notice_bytes(self) -> None:
        relative_path = "docs/evidence/licenses/upstream/legacy-notice.txt"
        notice = b"legacy notice byte: \xff\n"
        repository = self.create_repository(
            "legacy-notice",
            extra_files={relative_path: notice},
        )
        result, payload = self.package(repository, self.base / "legacy-output")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with zipfile.ZipFile(Path(str(payload["zip"]))) as archive:
            self.assertEqual(archive.read(relative_path), notice)

    def test_rejects_credential_inside_non_utf8_upstream_notice(self) -> None:
        credential = b"\xffBear" + b"er abcdefghijklmnopqrstuvwxyz123456\n"
        repository = self.create_repository(
            "legacy-notice-credential",
            extra_files={
                "docs/evidence/licenses/upstream/credential.txt": credential,
            },
        )
        self.assert_package_failure(
            repository,
            self.base / "legacy-notice-credential-output",
            "Bearer 凭据",
        )

    def test_rejects_output_inside_allowed_tree(self) -> None:
        repository = self.create_repository("nested-output")
        self.assert_package_failure(
            repository,
            repository / "docs" / "release-output",
            "输出目录不能位于",
        )

    def test_rejects_hardlinked_release_input(self) -> None:
        try:
            repository = self.create_repository("hardlink", hardlink=True)
        except OSError as error:
            self.skipTest(f"hard links are unavailable: {error}")
        self.assert_package_failure(
            repository,
            self.base / "hardlink-output",
            "硬链接",
        )

    def test_rejects_git_symlink_when_platform_supports_it(self) -> None:
        try:
            repository = self.create_repository("symlink", symlink=True)
        except OSError as error:
            self.skipTest(f"symbolic links are unavailable: {error}")
        self.assert_package_failure(
            repository,
            self.base / "symlink-output",
            "符号链接",
        )


if __name__ == "__main__":
    unittest.main()
