from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install-qoder-skill.ps1"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
GIT = shutil.which("git")
SKILL_NAME = "local-product-evidence-guard"

ROOT_REQUIRED = (
    "SKILL.md",
    "info.json",
    "meta.json",
    "requirements.txt",
    "requirements.lock",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "docs/ARCHITECTURE.md",
    "docs/CONTENT_WORKFLOW.md",
    "docs/NEXT_STEPS.md",
)
RUNTIME_SCRIPTS = (
    "scripts/benchmark-document-visuals.py",
    "scripts/benchmark-preprocessing.py",
    "scripts/benchmark.py",
    "scripts/client.py",
    "scripts/install-env.ps1",
    "scripts/install-qoder-skill.ps1",
    "scripts/model_download.py",
    "scripts/package-release.ps1",
    "scripts/protocol.py",
    "scripts/run.ps1",
    "scripts/run-demo.ps1",
    "scripts/run-demo.sh",
    "scripts/server.py",
    "scripts/workflow_cli.py",
    "scripts/workflow_service.py",
)
DEMO_FILES = (
    "samples/demo/说明书.txt",
    "samples/demo/参数表.csv",
    "samples/demo/包装正面.jpg.ocr.json",
)
PACKAGE_FILES = (
    "product_evidence_guard/ocr_review_prompt.txt",
    "product_evidence_guard/field_registry.json",
) + tuple(
    path.relative_to(REPO_ROOT).as_posix()
    for path in sorted((REPO_ROOT / "product_evidence_guard").glob("*.py"))
)
REQUIRED_SOURCE_FILES = ROOT_REQUIRED + PACKAGE_FILES + RUNTIME_SCRIPTS + DEMO_FILES


def _live(project: Path) -> Path:
    return project / ".qoder" / "skills" / SKILL_NAME


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    python_directory = str(Path(sys.executable).resolve().parent)
    environment["PATH"] = python_directory + os.pathsep + environment.get("PATH", "")
    environment["PYTHONUTF8"] = "1"
    return environment


def _run_installer(
    project: Path,
    *,
    installer: Path = INSTALLER,
    update: bool = False,
    runtime_root: Path | None = None,
    cwd: Path = REPO_ROOT,
) -> subprocess.CompletedProcess[str]:
    assert POWERSHELL is not None
    command = [
        POWERSHELL,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(installer),
        "-Scope",
        "Project",
        "-ProjectRoot",
        str(project),
    ]
    if update:
        command.append("-Update")
    if runtime_root is not None:
        command.extend(["-RuntimeRoot", str(runtime_root)])
    return subprocess.run(
        command,
        cwd=cwd,
        env=_subprocess_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )


def _assert_success(
    testcase: unittest.TestCase,
    result: subprocess.CompletedProcess[str],
) -> None:
    testcase.assertEqual(
        result.returncode,
        0,
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
    )


def _copy_source_fixture(parent: Path) -> Path:
    source = parent / "source"
    for relative in REQUIRED_SOURCE_FILES:
        origin = REPO_ROOT / Path(relative)
        target = source / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
    return source


def _create_directory_link(link: Path, target: Path) -> bool:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (NotImplementedError, OSError):
        pass

    if os.name != "nt":
        return False
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result.returncode == 0


class QoderInstallerContractTests(unittest.TestCase):
    def test_installer_contract_is_fail_closed_and_transactional(self) -> None:
        script = INSTALLER.read_text(encoding="utf-8")
        for contract in (
            "$allowedFiles",
            "$requiredRuntimeFiles",
            "$demoSampleFiles",
            "requirements.lock",
            "samples/demo/说明书.txt",
            "samples/demo/参数表.csv",
            "samples/demo/包装正面.jpg.ocr.json",
            "$deniedSegments",
            "$deniedLeafPatterns",
            "__pycache__",
            "*.pyc",
            "*.log",
            "benchmark-output",
            "demo-output",
            "*.safetensors",
            "ls-files --cached",
            "ReparsePoint",
            "hardlink list",
            "$maximumFileBytes",
            "skill-staging",
            "skill-backups",
            "$preservedRuntimeDirectories",
            "Assert-SafePathChain",
            "Assert-SameVolume",
            "Invoke-InstalledValidation",
            "importlib.import_module",
            'runpy.run_module("product_evidence_guard"',
            "-File $entryPoint",
        ):
            self.assertIn(contract, script)

        self.assertNotIn(
            'Join-Path $skillsRoot "$skillName.backup',
            script,
        )
        self.assertNotIn(
            "Copy-Item -LiteralPath $oldRuntime",
            script,
        )

        skill = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
        for routing_rule in (
            "必须把这些参数原样传给 `scripts\\run.ps1`",
            "必须保留用户指定的 `--deterministic-only`",
            "只执行用户点名的 candidate ID 和原始理由",
            "不要再次要求处理所有剩余候选",
            "Skill 自己不得修改来源文件",
        ):
            self.assertIn(routing_rule, skill)

    @unittest.skipUnless(POWERSHELL, "Qoder integration test requires PowerShell")
    def test_initial_install_has_complete_runtime_and_only_tracked_demo_inputs(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="qoder-initial-") as temporary:
            root = Path(temporary)
            source = _copy_source_fixture(root)
            project = root / "project"
            project.mkdir()
            result = _run_installer(
                project,
                installer=source / "scripts" / "install-qoder-skill.ps1",
                cwd=source,
            )
            _assert_success(self, result)

            installed = _live(project)
            for relative in REQUIRED_SOURCE_FILES:
                self.assertTrue(
                    (installed / Path(relative)).is_file(),
                    f"required file was not installed: {relative}",
                )

            demo_files = {
                path.relative_to(installed).as_posix()
                for path in (installed / "samples" / "demo").iterdir()
                if path.is_file()
            }
            self.assertEqual(demo_files, set(DEMO_FILES))
            self.assertFalse((installed / "tests").exists())
            self.assertFalse((installed / "samples" / "generated-benchmark").exists())
            self.assertFalse((installed / ".models").exists())
            self.assertFalse((installed / ".venv").exists())
            self.assertFalse((installed / ".tools").exists())
            self.assertFalse((installed / ".runtime").exists())

            installed_files = [
                path for path in installed.rglob("*") if path.is_file()
            ]
            denied_segments = {
                "__pycache__",
                ".pytest_cache",
                "logs",
                "output",
                "outputs",
                "benchmark-output",
                "demo-output",
                ".peg-output",
                "release",
            }
            denied_suffixes = {
                ".pyc",
                ".pyo",
                ".log",
                ".trace",
                ".tmp",
                ".partial",
                ".safetensors",
                ".onnx",
                ".gguf",
            }
            for path in installed_files:
                relative = path.relative_to(installed)
                self.assertTrue(denied_segments.isdisjoint(relative.parts))
                self.assertNotIn(path.suffix.casefold(), denied_suffixes)

            discoverable = list(
                (project / ".qoder" / "skills").rglob("SKILL.md")
            )
            self.assertEqual(discoverable, [installed / "SKILL.md"])

    @unittest.skipUnless(
        sys.platform == "win32" and POWERSHELL,
        "Qoder runtime bridge execution requires the supported Windows runtime",
    )
    def test_explicit_runtime_root_creates_local_bridge_and_executes_entry(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="qoder-runtime-bridge-") as temporary:
            root = Path(temporary)
            source = _copy_source_fixture(root)
            project = root / "project"
            project.mkdir()

            result = _run_installer(
                project,
                installer=source / "scripts" / "install-qoder-skill.ps1",
                runtime_root=source,
                cwd=source,
            )
            _assert_success(self, result)
            installed = _live(project)
            runtime_config = installed / ".runtime" / "source-root.txt"
            self.assertTrue(runtime_config.is_file())
            self.assertEqual(
                Path(runtime_config.read_text(encoding="utf-8").strip()).resolve(),
                source.resolve(),
            )
            self.assertIn("已复用本地运行时", result.stdout)

            entry_result = subprocess.run(
                [
                    POWERSHELL,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(installed / "scripts" / "run.ps1"),
                    "--help",
                ],
                cwd=installed,
                env=_subprocess_environment(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            self.assertEqual(entry_result.returncode, 0, entry_result.stderr)
            self.assertIn("--continue", entry_result.stdout)

    @unittest.skipUnless(
        POWERSHELL and GIT,
        "tracked-only integration test requires PowerShell and Git",
    )
    def test_untracked_allowlisted_and_arbitrary_files_are_not_installed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="qoder-tracked-") as temporary:
            root = Path(temporary)
            source = _copy_source_fixture(root)
            project = root / "project"
            project.mkdir()

            subprocess.run(
                [GIT, "init", "--quiet"],
                cwd=source,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                [GIT, "-c", "core.excludesFile=", "add", "--", "."],
                cwd=source,
                capture_output=True,
                check=True,
            )
            # One optional allowlisted file plus several arbitrary files remain
            # untracked and must not cross the install boundary.
            (source / "PRIVACY.md").write_text("untracked notice\n", encoding="utf-8")
            (source / "scripts" / "evil.py").write_text(
                "raise RuntimeError('must not install')\n",
                encoding="utf-8",
            )
            (source / "logs").mkdir()
            (source / "logs" / "secret.log").write_text(
                "untracked log\n",
                encoding="utf-8",
            )
            (source / "demo-output").mkdir()
            (source / "demo-output" / "result.json").write_text(
                "{}\n",
                encoding="utf-8",
            )

            result = _run_installer(
                project,
                installer=source / "scripts" / "install-qoder-skill.ps1",
                cwd=source,
            )
            _assert_success(self, result)
            installed = _live(project)
            self.assertFalse((installed / "PRIVACY.md").exists())
            self.assertFalse((installed / "scripts" / "evil.py").exists())
            self.assertFalse((installed / "logs").exists())
            self.assertFalse((installed / "demo-output").exists())
            for relative in DEMO_FILES:
                self.assertTrue((installed / Path(relative)).is_file())

    @unittest.skipUnless(POWERSHELL, "Qoder integration test requires PowerShell")
    def test_update_moves_runtime_to_live_and_keeps_logs_in_external_backup(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="qoder-update-") as temporary:
            root = Path(temporary)
            source = _copy_source_fixture(root)
            project = root / "project"
            project.mkdir()
            fixture_installer = source / "scripts" / "install-qoder-skill.ps1"
            initial = _run_installer(
                project,
                installer=fixture_installer,
                cwd=source,
            )
            _assert_success(self, initial)
            installed = _live(project)

            runtime_names = (".models", ".venv", ".tools", ".runtime")
            original_file_ids: dict[str, int] = {}
            for name in runtime_names:
                runtime_file = installed / name / "sentinel.dat"
                runtime_file.parent.mkdir(parents=True)
                runtime_file.write_text(f"preserved:{name}\n", encoding="utf-8")
                original_file_ids[name] = runtime_file.stat().st_ino

            excluded_names = (
                "logs",
                "output",
                "demo-output",
                "benchmark-output",
                ".peg-output",
            )
            for name in excluded_names:
                excluded_file = installed / name / "old-output.dat"
                excluded_file.parent.mkdir(parents=True)
                excluded_file.write_text(f"old:{name}\n", encoding="utf-8")
            (installed / "old-only.txt").write_text("old\n", encoding="utf-8")

            updated = _run_installer(
                project,
                installer=fixture_installer,
                cwd=source,
                update=True,
            )
            _assert_success(self, updated)
            self.assertIn("skills discovery root", updated.stdout)

            for name in runtime_names:
                moved_file = installed / name / "sentinel.dat"
                self.assertEqual(
                    moved_file.read_text(encoding="utf-8"),
                    f"preserved:{name}\n",
                )
                if original_file_ids[name]:
                    self.assertEqual(
                        moved_file.stat().st_ino,
                        original_file_ids[name],
                        f"{name} was copied instead of moved",
                    )

            backups = list(
                (project / ".qoder" / "skill-backups").iterdir()
            )
            self.assertEqual(len(backups), 1)
            backup = backups[0]
            self.assertTrue((backup / "SKILL.md").is_file())
            self.assertTrue((backup / "old-only.txt").is_file())
            self.assertFalse((installed / "old-only.txt").exists())
            for name in runtime_names:
                self.assertFalse((backup / name).exists())
            for name in excluded_names:
                self.assertFalse((installed / name).exists())
                self.assertTrue((backup / name / "old-output.dat").is_file())

            skills_root = project / ".qoder" / "skills"
            discoverable = list(skills_root.rglob("SKILL.md"))
            self.assertEqual(discoverable, [installed / "SKILL.md"])
            self.assertFalse(str(backup).startswith(str(skills_root) + os.sep))
            staging_skills = list(
                (project / ".qoder" / "skill-staging").rglob("SKILL.md")
            )
            self.assertEqual(staging_skills, [])

    @unittest.skipUnless(POWERSHELL, "Qoder integration test requires PowerShell")
    def test_missing_required_module_fails_before_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qoder-missing-") as temporary:
            root = Path(temporary)
            source = _copy_source_fixture(root)
            project = root / "project"
            project.mkdir()
            (source / "product_evidence_guard" / "models.py").unlink()

            result = _run_installer(
                project,
                installer=source / "scripts" / "install-qoder-skill.ps1",
                cwd=source,
            )
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertIn("models.py", combined)
            self.assertFalse(_live(project).exists())

    @unittest.skipUnless(POWERSHELL, "Qoder integration test requires PowerShell")
    def test_import_failure_is_rejected_and_existing_live_is_unchanged(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="qoder-import-") as temporary:
            root = Path(temporary)
            source = _copy_source_fixture(root)
            project = root / "project"
            project.mkdir()
            initial = _run_installer(
                project,
                installer=source / "scripts" / "install-qoder-skill.ps1",
                cwd=source,
            )
            _assert_success(self, initial)
            installed = _live(project)
            marker = installed / "old-live-marker.txt"
            marker.write_text("must survive failed update\n", encoding="utf-8")

            (source / "product_evidence_guard" / "normalization.py").write_text(
                "raise RuntimeError('BROKEN_IMPORT_SENTINEL')\n",
                encoding="utf-8",
            )
            failed = _run_installer(
                project,
                installer=source / "scripts" / "install-qoder-skill.ps1",
                cwd=source,
                update=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            combined = failed.stdout + failed.stderr
            self.assertIn("BROKEN_IMPORT_SENTINEL", combined)
            self.assertEqual(
                marker.read_text(encoding="utf-8"),
                "must survive failed update\n",
            )
            self.assertEqual(
                list((project / ".qoder" / "skills").rglob("SKILL.md")),
                [installed / "SKILL.md"],
            )
            backup_root = project / ".qoder" / "skill-backups"
            self.assertEqual(
                list(backup_root.iterdir()) if backup_root.exists() else [],
                [],
            )
            staging_root = project / ".qoder" / "skill-staging"
            self.assertEqual(
                list(staging_root.iterdir()) if staging_root.exists() else [],
                [],
            )

    @unittest.skipUnless(POWERSHELL, "Qoder integration test requires PowerShell")
    def test_reparse_point_in_each_destination_ancestor_is_rejected(self) -> None:
        scenarios = (".qoder", "skills", "target")
        link_capability_confirmed = False
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory(
                prefix=f"qoder-link-{scenario}-"
            ) as temporary:
                root = Path(temporary)
                source = _copy_source_fixture(root)
                project = root / "project"
                outside = root / "outside"
                project.mkdir()
                outside.mkdir()

                if scenario == ".qoder":
                    link = project / ".qoder"
                    expected_untouched = outside / "skills"
                elif scenario == "skills":
                    (project / ".qoder").mkdir()
                    link = project / ".qoder" / "skills"
                    expected_untouched = outside / SKILL_NAME
                else:
                    (project / ".qoder" / "skills").mkdir(parents=True)
                    link = project / ".qoder" / "skills" / SKILL_NAME
                    expected_untouched = outside / "SKILL.md"

                if not _create_directory_link(link, outside):
                    if not link_capability_confirmed:
                        self.skipTest(
                            "cannot create a directory symlink/junction "
                            "with current permissions"
                        )
                    self.fail(f"link creation unexpectedly failed for {scenario}")
                link_capability_confirmed = True

                result = _run_installer(
                    project,
                    installer=source / "scripts" / "install-qoder-skill.ps1",
                    cwd=source,
                )
                self.assertNotEqual(
                    result.returncode,
                    0,
                    f"{scenario} reparse point was accepted",
                )
                combined = (result.stdout + result.stderr).casefold()
                self.assertTrue(
                    "reparse" in combined
                    or "junction" in combined
                    or "链接" in combined,
                    combined,
                )
                self.assertFalse(expected_untouched.exists())


if __name__ == "__main__":
    unittest.main()
