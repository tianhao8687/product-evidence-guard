from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "generate-sbom.py"
SBOM = REPO_ROOT / "docs" / "evidence" / "sbom.json"
LICENSES = REPO_ROOT / "docs" / "evidence" / "license-inventory.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_sbom", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load supply-chain evidence generator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GENERATOR = _load_generator()


class SupplyChainEvidenceTests(unittest.TestCase):
    def test_lock_parser_collects_and_sorts_artifact_hashes(self) -> None:
        first = "a" * 64
        second = "b" * 64
        locked = GENERATOR.parse_lock(
            "# generated offline\n"
            "Demo_Package==2.0 \\\n"
            f"    --hash=sha256:{second} \\\n"
            f"    --hash=sha256:{first}\n"
            "another==1.0 \\\n"
            f"    --hash=sha256:{first}\n"
        )
        self.assertEqual([item.name for item in locked], ["another", "Demo_Package"])
        self.assertEqual(locked[1].sha256, (first, second))

    def test_checked_evidence_is_current_and_deterministic(self) -> None:
        first = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            temp_root = Path(temporary)
            generated_sbom = temp_root / "sbom.json"
            generated_licenses = temp_root / "licenses.json"
            command = [
                sys.executable,
                str(SCRIPT),
                "--output",
                str(generated_sbom),
                "--license-output",
                str(generated_licenses),
            ]
            run = subprocess.run(
                command,
                cwd=temp_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(generated_sbom.read_bytes(), SBOM.read_bytes())
            self.assertEqual(generated_licenses.read_bytes(), LICENSES.read_bytes())

    def test_sbom_covers_every_locked_package_and_contains_no_host_paths(self) -> None:
        sbom = json.loads(SBOM.read_text(encoding="utf-8"))
        inventory = json.loads(LICENSES.read_text(encoding="utf-8"))
        locked = GENERATOR.parse_lock(
            (REPO_ROOT / "requirements.lock").read_text(encoding="utf-8")
        )

        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["specVersion"], "1.5")
        self.assertEqual(len(sbom["components"]), len(locked))
        self.assertEqual(
            inventory["summary"]["installed_version_match_count"], len(locked)
        )
        self.assertEqual(
            {(row["name"].lower(), row["version"]) for row in inventory["packages"]},
            {(item.name.lower(), item.version) for item in locked},
        )

        serialized = SBOM.read_text(encoding="utf-8") + LICENSES.read_text(encoding="utf-8")
        self.assertNotIn(str(REPO_ROOT), serialized)
        self.assertNotIn("C" + ":\\\\Users\\", serialized)
        self.assertNotIn("D" + ":\\\\", serialized)
        for secret_marker in ("api_key", "access_token", "bearer ", "authorization"):
            self.assertNotIn(secret_marker, serialized.lower())


if __name__ == "__main__":
    unittest.main()
