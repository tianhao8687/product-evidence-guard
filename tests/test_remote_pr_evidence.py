from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "docs" / "evidence" / "remote-pr-snapshot-20260805.json"


class RemotePullRequestEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        raw = EVIDENCE.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        self.text = raw.decode("utf-8", errors="strict")
        self.data = json.loads(self.text)

    def test_snapshot_is_publishable_and_explicitly_stale(self) -> None:
        self.assertNotRegex(
            self.text,
            r"(?i)(?:(?<![a-z])[a-z]:[\\/]|/(?:users|home)/|authorization|bearer\s+|gh[opsu]_[a-z0-9])",
        )
        pull_request = self.data["pull_request"]
        comparison = self.data["local_comparison"]
        self.assertEqual(pull_request["number"], 1)
        self.assertTrue(pull_request["draft"])
        self.assertEqual(pull_request["state"], "open")
        self.assertRegex(pull_request["head_sha"], r"\A[0-9a-f]{40}\Z")
        self.assertRegex(comparison["local_head_at_observation"], r"\A[0-9a-f]{40}\Z")
        self.assertFalse(comparison["matches_remote_pr_head"])
        self.assertNotEqual(
            pull_request["head_sha"], comparison["local_head_at_observation"]
        )
        self.assertIn("does not prove CI", self.data["claim_boundary"])
        self.assertIn("authorize a push", self.data["claim_boundary"])

    def test_both_events_have_successful_linux_and_windows_jobs(self) -> None:
        runs = self.data["runs"]
        self.assertEqual({run["event"] for run in runs}, {"push", "pull_request"})
        remote_sha = self.data["pull_request"]["head_sha"]
        for run in runs:
            with self.subTest(event=run["event"]):
                self.assertEqual(run["status"], "completed")
                self.assertEqual(run["conclusion"], "success")
                self.assertEqual(run["head_sha"], remote_sha)
                jobs = {job["runner"]: job for job in run["jobs"]}
                self.assertEqual(set(jobs), {"ubuntu-latest", "windows-latest"})
                self.assertTrue(
                    all(job["conclusion"] == "success" for job in jobs.values())
                )

    def test_no_review_work_is_hidden(self) -> None:
        self.assertEqual(
            self.data["review_summary"],
            {
                "review_count": 0,
                "review_comment_count": 0,
                "issue_comment_count": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
