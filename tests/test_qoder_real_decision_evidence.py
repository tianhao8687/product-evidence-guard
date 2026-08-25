from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = REPO_ROOT / "docs" / "evidence" / "qoder-real-decision-closure-20260805.json"
FORBIDDEN_KEYS = {
    "session_id",
    "candidate_id",
    "candidate_ids",
    "request_id",
    "authkey",
    "raw_text",
    "raw_value",
    "reason",
    "reason_summary",
    "source_file",
    "file_hash",
    "model_path",
    "input_dir",
    "output_dir",
}


def _strict_json(raw: bytes) -> dict[str, object]:
    if raw.startswith(b"\xef\xbb\xbf"):
        raise AssertionError("evidence must be UTF-8 without BOM")

    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON value: {value}")

    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    return json.loads(
        raw.decode("utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _walk_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _walk_keys(child)}
    return set()


class QoderRealDecisionEvidenceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = EVIDENCE.read_bytes()
        self.serialized = self.raw.decode("utf-8")
        self.record = _strict_json(self.raw)

    def test_decision_export_and_stale_schema_is_exact(self) -> None:
        self.assertEqual(
            set(self.record),
            {
                "schema_version",
                "evidence_type",
                "observed_on",
                "scope",
                "operations",
                "source_change_test",
                "result",
                "claim_boundary",
            },
        )
        self.assertEqual(self.record["schema_version"], 1)
        self.assertEqual(self.record["evidence_type"], "qoder_real_image_decision_closure")
        scope = self.record["scope"]
        self.assertEqual(
            set(scope),
            {
                "real_image_count",
                "controlled_document_count",
                "public_entrypoint",
                "prior_analysis_reused",
                "decision_model_inference_requested",
                "network_observation_performed",
                "new_qoder_cloud_invocation",
            },
        )
        self.assertEqual(scope["public_entrypoint"], "scripts/run.ps1")
        self.assertTrue(scope["prior_analysis_reused"])
        self.assertFalse(scope["decision_model_inference_requested"])
        self.assertFalse(scope["network_observation_performed"])
        self.assertFalse(scope["new_qoder_cloud_invocation"])

        operations = self.record["operations"]
        self.assertEqual(
            set(operations),
            {"confirm", "reject", "export_before_change", "export_after_source_change"},
        )
        self.assertEqual(
            operations["confirm"],
            {
                "subject_role": "authorized_real_image_consistent_value",
                "evidence_relation": "unit_conversion_consistent",
                "exit_code": 0,
                "reason_recorded": True,
            },
        )
        self.assertEqual(
            operations["reject"],
            {
                "subject_role": "controlled_synthetic_conflict_value",
                "evidence_relation": "strong_conflict",
                "exit_code": 0,
                "reason_recorded": True,
            },
        )
        self.assertEqual(
            operations["export_before_change"],
            {"exit_code": 0, "confirmed_count": 1, "stale_count": 0},
        )
        self.assertEqual(
            operations["export_after_source_change"],
            {"exit_code": 0, "confirmed_count": 0, "stale_count": 1},
        )
        self.assertEqual(
            self.record["source_change_test"],
            {
                "disposable_copy": True,
                "confirmed_fact_became_stale": True,
                "original_bytes_restored": True,
                "temporary_backup_removed": True,
            },
        )
        self.assertEqual(self.record["result"], "passed")

    def test_public_record_is_strictly_sanitized_and_claims_are_bounded(self) -> None:
        lower = self.serialized.lower()
        self.assertFalse(_walk_keys(self.record) & FORBIDDEN_KEYS)
        for pattern in (
            r"(?i)[a-z]:[\\/]",
            r"(?i)\\\\[^\\\r\n]+\\[^\\\r\n]+",
            r"(?i)/(?:home|users)/",
            r"(?i)(?<![0-9a-f])[0-9a-f]{20}(?![0-9a-f])",
            r"(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])",
        ):
            self.assertIsNone(re.search(pattern, self.serialized))
        for marker in (
            "api_key",
            "access_token",
            "authorization",
            "private key",
            "bearer ",
        ):
            self.assertNotIn(marker, lower)
        boundary = self.record["claim_boundary"].lower()
        for phrase in (
            "not a broad real-image accuracy study",
            "not a new model inference run",
            "not a network-isolation proof",
            "not a complete qoder ide recording",
        ):
            self.assertIn(phrase, boundary)


if __name__ == "__main__":
    unittest.main()
