from __future__ import annotations

import json
from pathlib import Path
import statistics
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
COLD = REPO_ROOT / "docs" / "evidence" / "cold-start-distribution-20260805.json"
NETWORK = REPO_ROOT / "docs" / "evidence" / "network-tcp-observation-20260805.json"


class PerformanceNetworkEvidenceTests(unittest.TestCase):
    def test_cold_start_distribution_is_internally_consistent(self) -> None:
        record = json.loads(COLD.read_text(encoding="utf-8"))
        runs = record["runs"]
        self.assertEqual(len(runs), 3)
        self.assertEqual({run["run"] for run in runs}, {1, 2, 3})
        self.assertTrue(all(run["candidate_count"] == 2 for run in runs))
        self.assertTrue(all(run["error_count"] == 0 for run in runs))
        self.assertFalse(record["runtime"]["model_reused"])
        analysis = [run["analysis_seconds"] for run in runs]
        loads = [run["model_load_seconds"] for run in runs]
        self.assertAlmostEqual(
            record["statistics"]["analysis_seconds"]["mean"],
            statistics.mean(analysis),
            places=4,
        )
        self.assertEqual(record["statistics"]["analysis_seconds"]["min"], min(analysis))
        self.assertEqual(record["statistics"]["analysis_seconds"]["max"], max(analysis))
        self.assertAlmostEqual(
            record["statistics"]["model_load_seconds"]["mean"],
            statistics.mean(loads),
            places=4,
        )

    def test_network_record_preserves_the_sampled_claim_boundary(self) -> None:
        record = json.loads(NETWORK.read_text(encoding="utf-8"))
        self.assertEqual(record["method"]["requested_interval_ms"], 100)
        self.assertGreaterEqual(record["method"]["observation_cycles"], 30)
        self.assertEqual(record["result"]["external_tcp_observation_count"], 0)
        self.assertEqual(record["result"]["client_exit_code"], 0)
        self.assertEqual(record["result"]["stderr_bytes"], 0)
        boundary = record["claim_boundary"].lower()
        for required_limit in ("not firewall", "packet capture", "dns", "udp", "air-gapped"):
            self.assertIn(required_limit, boundary)

    def test_public_evidence_contains_no_host_absolute_path(self) -> None:
        serialized = COLD.read_text(encoding="utf-8") + NETWORK.read_text(encoding="utf-8")
        for forbidden in ("C:\\\\Users\\\\", "D:\\\\", "/home/", "/Users/"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
