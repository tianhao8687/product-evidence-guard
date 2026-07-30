from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install-env.ps1"
REQUIREMENTS_LOCK = REPO_ROOT / "requirements.lock"
OFFICIAL_UV_084_WINDOWS_X64_SHA256 = (
    "817c50c80229f88de9699626ee3774c0cceed86099663e8fb00c5ffae7ea911c"
)
OFFICIAL_UV_084_WINDOWS_X64_EXE_SHA256 = (
    "658a2f7706f10eb424e7a13e8601aae5ed1f91d0ea53b5a313d238cfb10ffa9f"
)
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


class InstallEnvironmentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = INSTALL_SCRIPT.read_text(encoding="utf-8")

    def test_uv_primary_artifact_is_pinned_and_checked_before_expansion(self) -> None:
        self.assertIn("$uvVersion = '0.8.4'", self.script)
        self.assertIn(
            "$uvArchiveName = 'uv-x86_64-pc-windows-msvc.zip'",
            self.script,
        )
        self.assertIn(
            f"$uvArchiveSha256 = '{OFFICIAL_UV_084_WINDOWS_X64_SHA256}'",
            self.script,
        )
        self.assertIn(
            "https://github.com/astral-sh/uv/releases/download/0.8.4/"
            "uv-x86_64-pc-windows-msvc.zip.sha256",
            self.script,
        )
        self.assertRegex(
            OFFICIAL_UV_084_WINDOWS_X64_SHA256,
            r"\A[0-9a-f]{64}\Z",
        )
        hash_position = self.script.index(
            "Get-FileHash -LiteralPath $archive -Algorithm SHA256"
        )
        mismatch_position = self.script.index(
            "$actualArchiveSha256 -ne $uvArchiveSha256"
        )
        promote_position = self.script.index(
            "Move-Item -LiteralPath $archive -Destination $verifiedArchive"
        )
        expansion_position = self.script.index(
            "Expand-Archive -LiteralPath $verifiedArchive"
        )
        self.assertLess(hash_position, mismatch_position)
        self.assertLess(mismatch_position, promote_position)
        self.assertLess(promote_position, expansion_position)
        self.assertIn(".verified.zip", self.script)
        self.assertIn("uv_exe_sha256", self.script)
        self.assertIn("$currentUvExeSha256", self.script)

    def test_uv_cache_and_extracted_executable_are_bound_to_pinned_hash(
        self,
    ) -> None:
        expected_assignment = (
            "$expectedUvExeSha256 = "
            f"'{OFFICIAL_UV_084_WINDOWS_X64_EXE_SHA256}'"
        )
        self.assertEqual(self.script.count(expected_assignment), 1)
        self.assertIn(
            "$verification.uv_exe_sha256 -eq $expectedUvExeSha256",
            self.script,
        )
        self.assertIn(
            "$currentUvExeSha256 -eq $expectedUvExeSha256",
            self.script,
        )

        expansion_position = self.script.index(
            "Expand-Archive -LiteralPath $verifiedArchive"
        )
        extracted_hash_position = self.script.index(
            "$extractedUvExeSha256 =",
            expansion_position,
        )
        extracted_mismatch_position = self.script.index(
            "$extractedUvExeSha256 -ne $expectedUvExeSha256",
            extracted_hash_position,
        )
        remove_old_cache_position = self.script.index(
            "Remove-ExactManagedDirectory",
            extracted_mismatch_position,
        )
        copy_position = self.script.index(
            "Copy-Item -LiteralPath $downloadedUv.FullName",
            remove_old_cache_position,
        )
        installed_mismatch_position = self.script.index(
            "$installedUvExeSha256 -ne $expectedUvExeSha256",
            copy_position,
        )
        self.assertLess(expansion_position, extracted_hash_position)
        self.assertLess(extracted_hash_position, extracted_mismatch_position)
        self.assertLess(extracted_mismatch_position, remove_old_cache_position)
        self.assertLess(remove_old_cache_position, copy_position)
        self.assertLess(copy_position, installed_mismatch_position)

    def test_python_patch_version_is_exact_in_install_venv_and_stamp(self) -> None:
        self.assertIn("$pythonVersion = '3.11.13'", self.script)
        self.assertIn(
            "@('python', 'install', $pythonVersion)",
            self.script,
        )
        self.assertIn(
            "'venv', '--python', $pythonVersion, $venvDir",
            self.script,
        )
        self.assertIn("sys.version_info[:3]", self.script)
        self.assertIn("$stamp.python_version -eq $pythonVersion", self.script)
        self.assertIn("python_version = $pythonVersion", self.script)

    def test_force_rebuild_has_exact_target_and_reparse_point_guards(self) -> None:
        self.assertRegex(self.script, r"param\(\s*\[switch\]\$Force\s*\)")
        force_block = re.search(
            r"if \(\$Force -and .*?\) \{(?P<body>.*?)\n\}",
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(force_block)
        assert force_block is not None
        self.assertIn("Remove-ExactManagedDirectory", force_block.group("body"))
        self.assertIn("-ExpectedPath (Join-Path $repoRoot '.venv')", self.script)
        self.assertIn("[System.IO.Path]::GetFullPath", self.script)
        self.assertIn("[System.StringComparison]::OrdinalIgnoreCase", self.script)
        self.assertIn("[System.IO.FileAttributes]::ReparsePoint", self.script)
        self.assertIn("Resolve-Path -LiteralPath $targetFull", self.script)
        self.assertIn("现有 .venv 是 Python", self.script)
        self.assertIn("请使用 -Force 在安全校验后重新创建", self.script)
        python_ready_position = self.script.index(
            "Invoke-Checked -FilePath $uvExe -Arguments "
            "@('python', 'install', $pythonVersion)"
        )
        force_remove_position = self.script.index(
            "Remove-ExactManagedDirectory",
            force_block.start(),
        )
        wrong_version_position = self.script.index("现有 .venv 是 Python")
        self.assertLess(wrong_version_position, python_ready_position)
        self.assertLess(python_ready_position, force_remove_position)

    def test_managed_writes_and_recursive_removal_are_link_safe(self) -> None:
        self.assertIn("function Assert-SafeManagedFile", self.script)
        self.assertIn("function Assert-SingleHardLink", self.script)
        self.assertIn("hardlink list", self.script)
        self.assertIn("function Assert-NoReparseDescendants", self.script)
        self.assertIn(
            "New-Object 'System.Collections.Generic.Stack[string]'",
            self.script,
        )

        write_log = re.search(
            r"function Write-InstallLog \{(?P<body>.*?)\n\}",
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(write_log)
        assert write_log is not None
        self.assertLess(
            write_log.group("body").index("Assert-SafeManagedFile"),
            write_log.group("body").index("Add-Content"),
        )

        removal = re.search(
            r"function Remove-ExactManagedDirectory \{(?P<body>.*?)\n\}",
            self.script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(removal)
        assert removal is not None
        self.assertLess(
            removal.group("body").index("Assert-NoReparseDescendants"),
            removal.group("body").index("Remove-Item"),
        )
        for managed_path in (
            "$toolsDir",
            "$runtimeDir",
            "$logDir",
            "$pythonInstallDir",
        ):
            initialize_position = self.script.index(
                f"-TargetPath {managed_path}"
            )
            first_log_position = self.script.index(
                'Write-InstallLog "Install started.'
            )
            self.assertLess(initialize_position, first_log_position)

        verification_write = self.script.index(
            "-Context 'uv verification write'"
        )
        verification_set_content = self.script.index(
            "} | ConvertTo-Json | Set-Content",
            verification_write,
        )
        self.assertLess(verification_write, verification_set_content)
        stamp_write = self.script.index("-Context 'install stamp write'")
        stamp_set_content = self.script.index(
            "$stamp | ConvertTo-Json | Set-Content",
            stamp_write,
        )
        self.assertLess(stamp_write, stamp_set_content)
        self.assertIn(
            "$safeRequirementsPath = Assert-SafeManagedFile",
            self.script,
        )
        self.assertIn(
            "$safeRequirementsLockPath = Assert-SafeManagedFile",
            self.script,
        )
        self.assertIn(
            "Get-FileHash -LiteralPath $safeRequirementsPath",
            self.script,
        )
        self.assertIn(
            "Get-FileHash -LiteralPath $safeRequirementsLockPath",
            self.script,
        )

    def test_dependency_sync_precedes_no_deps_editable_install_and_check(self) -> None:
        sync_position = self.script.index("'pip', 'sync'")
        install_position = self.script.index("'pip', 'install'")
        check_position = self.script.rindex("'pip', 'check'")
        self.assertLess(sync_position, install_position)
        self.assertLess(install_position, check_position)
        self.assertIn("$requirementsLockPath", self.script)
        self.assertIn("requirements_lock_sha256", self.script)
        self.assertIn("'--require-hashes'", self.script)
        self.assertIn("'--strict'", self.script)
        self.assertIn("'--quiet'", self.script)
        self.assertIn("'--no-build-isolation'", self.script)
        self.assertIn("'--no-index'", self.script)
        self.assertIn("$safeRequirementsLockPath\n)", self.script)
        self.assertIn("function Invoke-QuietExitCode", self.script)
        self.assertIn("$ErrorActionPreference = 'Continue'", self.script)
        self.assertIn("$stampMatches = $checkExitCode -eq 0", self.script)
        self.assertNotIn(
            "'pip', 'install', '--python', $venvPython, '--upgrade'",
            self.script,
        )

    def test_windows_lock_is_complete_pinned_and_hash_checked(self) -> None:
        lock = REQUIREMENTS_LOCK.read_text(encoding="utf-8")
        requirements_input = (REPO_ROOT / "requirements.txt").read_text(
            encoding="utf-8"
        )
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("setuptools==80.9.0", requirements_input)
        self.assertIn('requires = ["setuptools==80.9.0"]', pyproject)
        packages = list(
            re.finditer(
                r"(?m)^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^\s\\]+)",
                lock,
            )
        )
        self.assertEqual(len(packages), 27)
        for expected in (
            "openvino==2026.2.1",
            "openvino-genai==2026.2.1.0",
            "openvino-tokenizers==2026.2.1.0",
            "huggingface-hub==0.34.4",
            "numpy==2.2.6",
            "pillow==11.3.0",
            "python-docx==1.2.0",
            "openpyxl==3.1.5",
            "pypdf==6.0.0",
            "pypdfium2==5.12.1",
            "setuptools==80.9.0",
        ):
            self.assertRegex(lock, rf"(?m)^{re.escape(expected)}\s*\\$")
        for index, package in enumerate(packages):
            end = packages[index + 1].start() if index + 1 < len(packages) else len(lock)
            block = lock[package.start() : end]
            self.assertIn(
                "--hash=sha256:",
                block,
                package.group("name"),
            )
        self.assertNotRegex(
            lock,
            r"(?im)^\s*(?:-e|--index-url|--extra-index-url)\b|https?://|file:",
        )

    def test_powershell_parser_accepts_script_without_executing_it(self) -> None:
        powershell = POWERSHELL
        if powershell is None:
            self.skipTest("PowerShell parser is unavailable on this runner")
        escaped_path = str(INSTALL_SCRIPT).replace("'", "''")
        command = (
            "$tokens = $null; $errors = $null; "
            "[System.Management.Automation.Language.Parser]::"
            f"ParseFile('{escaped_path}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { "
            "$errors | ForEach-Object { Write-Error $_.Message }; exit 1 }"
        )
        completed = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )
        self.assertTrue(INSTALL_SCRIPT.read_bytes().startswith(b"\xef\xbb\xbf"))


@unittest.skipUnless(
    os.name == "nt" and POWERSHELL,
    "managed-link integration tests require Windows PowerShell",
)
class InstallEnvironmentLinkSafetyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="install-env-link-test-"
        )
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_repository(self, name: str) -> Path:
        repository = self.base / name
        scripts = repository / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(INSTALL_SCRIPT, scripts / "install-env.ps1")
        (repository / "requirements.txt").write_text("", encoding="utf-8")
        (repository / "requirements.lock").write_text("", encoding="utf-8")
        return repository

    def make_junction(self, junction: Path, target: Path) -> None:
        assert POWERSHELL is not None
        junction.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir(parents=True)
        escaped_junction = str(junction).replace("'", "''")
        escaped_target = str(target).replace("'", "''")
        command = (
            "New-Item -ItemType Junction "
            f"-Path '{escaped_junction}' "
            f"-Target '{escaped_target}' | Out-Null"
        )
        result = subprocess.run(
            [
                POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            self.skipTest(
                "directory junctions are unavailable: "
                + result.stdout
                + result.stderr
            )

    def run_installer(self, repository: Path) -> subprocess.CompletedProcess[str]:
        assert POWERSHELL is not None
        return subprocess.run(
            [
                POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(repository / "scripts" / "install-env.ps1"),
            ],
            cwd=repository,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    def test_reparse_managed_directory_is_rejected_before_external_write(
        self,
    ) -> None:
        for managed_path in (".tools", ".runtime", "logs", ".runtime/python"):
            with self.subTest(managed_path=managed_path):
                fixture_name = managed_path.replace(".", "").replace("/", "-")
                repository = self.create_repository(
                    "junction-" + fixture_name
                )
                outside = self.base / (
                    "outside-" + fixture_name
                )
                self.make_junction(repository / managed_path, outside)

                result = self.run_installer(repository)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "reparse",
                    (result.stdout + result.stderr).lower(),
                )
                self.assertEqual(list(outside.iterdir()), [])

    def test_hardlinked_log_is_rejected_without_mutating_external_file(
        self,
    ) -> None:
        repository = self.create_repository("hardlinked-log")
        (repository / "logs").mkdir()
        outside_log = self.base / "outside-install.log"
        sentinel = b"do-not-change\n"
        outside_log.write_bytes(sentinel)
        try:
            os.link(outside_log, repository / "logs" / "install-env.log")
        except OSError as error:
            self.skipTest(f"hard links are unavailable: {error}")

        result = self.run_installer(repository)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(outside_log.read_bytes(), sentinel)


if __name__ == "__main__":
    unittest.main()
