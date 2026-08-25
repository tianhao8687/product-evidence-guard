from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "generate-pdfium-notices.py"
MANIFEST = REPO_ROOT / "docs" / "evidence" / "pypdfium2-notices.json"
BUNDLE = REPO_ROOT / "docs" / "evidence" / "licenses" / "pypdfium2"


def _bundle_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class PdfiumNoticeEvidenceTests(unittest.TestCase):
    @unittest.skipUnless(
        sys.platform == "win32",
        "the checked evidence pins the Windows pypdfium2 wheel",
    )
    def test_generator_check_and_fresh_generation_are_deterministic(self) -> None:
        check = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            generated_manifest = temp_root / "pypdfium2-notices.json"
            generated_bundle = temp_root / "licenses"
            run = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--output",
                    str(generated_manifest),
                    "--bundle-dir",
                    str(generated_bundle),
                ],
                cwd=temp_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(generated_manifest.read_bytes(), MANIFEST.read_bytes())
            self.assertEqual(_bundle_snapshot(generated_bundle), _bundle_snapshot(BUNDLE))

    def test_manifest_identifies_the_pinned_windows_pdfium_build(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        source = manifest["source"]
        summary = manifest["summary"]

        self.assertEqual(manifest["generator"], "scripts/generate-pdfium-notices.py")
        self.assertEqual(source["distribution"], "pypdfium2")
        self.assertEqual(source["distribution_version"], "5.12.1")
        self.assertEqual(source["wheel_tags"], ["py3-none-win_amd64"])
        self.assertEqual(
            source["pdfium_version"],
            {
                "major": 152,
                "minor": 0,
                "build": 7947,
                "patch": 0,
                "n_commits": 0,
                "hash": None,
                "origin": "pdfium-binaries",
                "flags": [],
            },
        )
        self.assertEqual(source["pdfium_binary"]["bytes"], 7_217_664)
        self.assertEqual(
            source["pdfium_binary"]["sha256"],
            "cc2058add54b9da299a4496d526b79c375c6f0eb3a9000552a45c60f83765d55",
        )
        self.assertTrue(source["pdfium_binary"]["record_sha256_verified"])
        self.assertEqual(
            summary,
            {
                "license_file_count": 19,
                "pdfium_build_license_file_count": 16,
                "distribution_license_file_count": 3,
                "total_bytes": 136_719,
                "record_sha256_verified_count": 19,
            },
        )

    def test_every_declared_notice_matches_the_bundled_original(self) -> None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        files = manifest["files"]
        bundled = _bundle_snapshot(BUNDLE)

        self.assertEqual(len(files), 19)
        self.assertEqual([row["bundle_path"] for row in files], sorted(bundled))
        self.assertEqual(sum(len(content) for content in bundled.values()), 136_719)
        for row in files:
            content = bundled[row["bundle_path"]]
            self.assertEqual(row["bytes"], len(content))
            self.assertEqual(row["sha256"], hashlib.sha256(content).hexdigest())
            self.assertTrue(row["record_sha256_verified"])

    def test_publishable_manifest_has_no_host_paths_credentials_or_timestamp(self) -> None:
        serialized = MANIFEST.read_text(encoding="utf-8")
        self.assertNotIn(str(REPO_ROOT), serialized)
        self.assertIsNone(re.search(r"(?i)[a-z]:[\\/]", serialized))
        self.assertIsNone(re.search(r"(?i)/(?:home|users)/", serialized))
        for marker in (
            "api_key",
            "access_token",
            "bearer ",
            "authorization",
            "private key",
            "timestamp",
            "generated_at",
        ):
            self.assertNotIn(marker, serialized.lower())


if __name__ == "__main__":
    unittest.main()
