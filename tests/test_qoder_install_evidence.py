from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = (
    REPO_ROOT / "docs" / "evidence" / "qoder-install-integrity-20260805.json"
)
CRITICAL_FILES = (
    "SKILL.md",
    "info.json",
    "meta.json",
    "scripts/run.ps1",
    "scripts/client.py",
    "scripts/server.py",
)


class QoderInstallEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.serialized = EVIDENCE.read_text(encoding="utf-8")
        self.record = json.loads(self.serialized)

    def test_schema_records_discovery_scope_bridge_and_status(self) -> None:
        self.assertEqual(self.record["schema_version"], 1)
        self.assertEqual(self.record["evidence_type"], "qoder_install_integrity")
        self.assertRegex(self.record["observed_on"], r"^\d{4}-\d{2}-\d{2}$")

        cli = self.record["qoder_cli"]
        self.assertEqual(cli["version"], "1.1.8")
        self.assertEqual(cli["skills_list_command"], "qodercli skills list")
        self.assertEqual(cli["skills_list_exit_code"], 0)

        skill = self.record["skill"]
        self.assertEqual(skill["name"], "local-product-evidence-guard")
        self.assertEqual(skill["discovery_status"], "Enabled")
        self.assertEqual(skill["install_scope"], "user")
        self.assertEqual(
            skill["entry_path"],
            "%USERPROFILE%/.qoder/skills/local-product-evidence-guard/SKILL.md",
        )
        self.assertEqual(
            skill["matching_install_counts"],
            {"user": 1, "project": 0, "workspace": 0},
        )
        self.assertEqual(skill["duplicate_install_count"], 0)

        bridge = self.record["runtime_bridge"]
        self.assertEqual(
            bridge["bridge_file"], "%SKILL_ROOT%/.runtime/source-root.txt"
        )
        self.assertEqual(bridge["configured_target"], "<prepared-project-root>")
        self.assertTrue(bridge["configured_target_exists"])
        self.assertTrue(bridge["configured_target_matches_source_root"])
        self.assertEqual(bridge["source_identity"], "product-evidence-guard")

        probe = self.record["status_probe"]
        self.assertEqual(probe["entry"], "%SKILL_ROOT%/scripts/run.ps1 status")
        self.assertEqual(probe["process_exit_code"], 0)
        self.assertEqual(
            probe["stable_response"],
            {
                "protocol_version": 1,
                "ok": True,
                "operation": "status",
                "exit_code": 0,
                "status": "stopped",
                "error": None,
            },
        )
        self.assertEqual(probe["product_files_read"], 0)
        self.assertFalse(probe["model_load_requested"])
        self.assertFalse(probe["qoder_cloud_message_sent"])

    def test_six_installed_hashes_match_each_other_and_current_source(self) -> None:
        rows = self.record["critical_files"]
        self.assertEqual(len(rows), 6)
        self.assertEqual(tuple(row["path"] for row in rows), CRITICAL_FILES)

        for row in rows:
            source = REPO_ROOT / Path(row["path"])
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertRegex(row["source_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(row["source_sha256"], digest)
            self.assertEqual(row["installed_sha256"], digest)
            self.assertTrue(row["matches"])

    def test_publishable_evidence_has_no_private_path_or_credential(self) -> None:
        self.assertIsNone(re.search(r"(?i)[a-z]:[\\/]", self.serialized))
        self.assertIsNone(re.search(r"(?i)/(?:home|users)/", self.serialized))
        self.assertNotIn("Admin", self.serialized)
        for marker in (
            "api_key",
            "access_token",
            "bearer ",
            "authorization",
            "private key",
            "session_id",
            "request_id",
        ):
            self.assertNotIn(marker, self.serialized.lower())


if __name__ == "__main__":
    unittest.main()
