from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from product_evidence_guard.confirmation import (
    ConfirmationError,
    apply_decision,
    export_confirmed,
)
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.state import atomic_write_json


class ConfirmationTests(unittest.TestCase):
    def test_confirm_requires_reason_and_creates_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "a.txt").write_text("净重：320g\n", encoding="utf-8")
            summary = analyze_directory(root, output)
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            candidate = facts["candidates"][0]

            with self.assertRaises(ConfirmationError):
                apply_decision(
                    output,
                    session_id=summary["session_id"],
                    candidate_id=candidate["candidate_id"],
                    reason="",
                    action="confirm",
                )

            result = apply_decision(
                output,
                session_id=summary["session_id"],
                candidate_id=candidate["candidate_id"],
                reason="已与供应商书面确认",
                action="confirm",
            )
            self.assertEqual(result.status, "confirmed")
            exported = export_confirmed(output, session_id=summary["session_id"])
            self.assertEqual(exported["confirmed_count"], 1)
            audit = (output / "confirmation-audit.jsonl").read_text(encoding="utf-8")
            self.assertIn('"action": "confirm"', audit)

    def test_source_change_marks_confirmation_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            source = root / "a.txt"
            source.write_text("净重：320g\n", encoding="utf-8")
            first = analyze_directory(root, output)
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            candidate = facts["candidates"][0]
            apply_decision(
                output,
                session_id=first["session_id"],
                candidate_id=candidate["candidate_id"],
                reason="人工确认",
                action="confirm",
            )

            source.write_text("净重：350g\n", encoding="utf-8")
            second = analyze_directory(root, output)
            self.assertEqual(second["session_id"], first["session_id"])
            state = json.loads((output / "confirmation-state.json").read_text(encoding="utf-8"))
            self.assertEqual(
                state["decisions"][candidate["candidate_id"]]["status"],
                "stale",
            )
            exported = export_confirmed(output, session_id=second["session_id"])
            self.assertEqual(exported["confirmed_count"], 0)

    def test_decision_immediately_updates_all_user_facing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "a.txt").write_text("净重：320g\n", encoding="utf-8")
            summary = analyze_directory(root, output)
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            candidate_id = facts["candidates"][0]["candidate_id"]

            apply_decision(
                output,
                session_id=summary["session_id"],
                candidate_id=candidate_id,
                reason="供应商书面确认",
                action="confirm",
            )
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            run_summary = json.loads((output / "run-summary.json").read_text(encoding="utf-8"))
            markdown = (output / "conflicts.md").read_text(encoding="utf-8")
            html = (output / "evidence-report.html").read_text(encoding="utf-8")
            self.assertEqual(facts["candidates"][0]["status"], "confirmed")
            self.assertEqual(
                facts["run_summary"]["confirmation_status_counts"]["confirmed"],
                1,
            )
            self.assertEqual(run_summary["confirmation_status_counts"]["confirmed"], 1)
            self.assertIn("(`confirmed`)", markdown)
            self.assertIn("(confirmed)", html)

            apply_decision(
                output,
                session_id=summary["session_id"],
                candidate_id=candidate_id,
                reason="供应商撤回确认",
                action="reject",
            )
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            confirmed = json.loads(
                (output / "confirmed-product-facts.json").read_text(encoding="utf-8")
            )
            self.assertEqual(facts["candidates"][0]["status"], "rejected")
            self.assertEqual(confirmed["facts"], [])

    def test_export_marks_confirmation_stale_when_source_changes_without_reanalysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            source = root / "a.txt"
            source.write_text("净重：320g\n", encoding="utf-8")
            summary = analyze_directory(root, output)
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            candidate_id = facts["candidates"][0]["candidate_id"]
            apply_decision(
                output,
                session_id=summary["session_id"],
                candidate_id=candidate_id,
                reason="人工确认",
                action="confirm",
            )

            source.write_text("净重：350g\n", encoding="utf-8")
            exported = export_confirmed(output, session_id=summary["session_id"])
            state = json.loads(
                (output / "confirmation-state.json").read_text(encoding="utf-8")
            )
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            self.assertEqual(exported["confirmed_count"], 0)
            self.assertEqual(state["decisions"][candidate_id]["status"], "stale")
            self.assertEqual(facts["candidates"][0]["status"], "pending")
            self.assertEqual(
                facts["run_summary"]["confirmation_status_counts"]["stale"],
                1,
            )

    def test_export_rejects_modified_product_facts_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "a.txt").write_text("净重：320g\n", encoding="utf-8")
            summary = analyze_directory(root, output)
            facts_path = output / "product-facts.json"
            facts = json.loads(facts_path.read_text(encoding="utf-8"))
            candidate_id = facts["candidates"][0]["candidate_id"]
            apply_decision(
                output,
                session_id=summary["session_id"],
                candidate_id=candidate_id,
                reason="人工确认",
                action="confirm",
            )

            facts = json.loads(facts_path.read_text(encoding="utf-8"))
            facts["candidates"][0]["raw_value"] = "999g"
            facts["candidates"][0]["normalized_value"] = 999
            facts_path.write_text(
                json.dumps(facts, ensure_ascii=False),
                encoding="utf-8",
            )
            exported = export_confirmed(output, session_id=summary["session_id"])
            confirmed = json.loads(
                (output / "confirmed-product-facts.json").read_text(encoding="utf-8")
            )
            self.assertEqual(exported["confirmed_count"], 0)
            self.assertEqual(confirmed["facts"], [])

    def test_output_reuse_with_different_input_root_rotates_session_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root_one = Path(tmp) / "input-one"
            root_two = Path(tmp) / "input-two"
            output = Path(tmp) / "output"
            root_one.mkdir()
            root_two.mkdir()
            for root in (root_one, root_two):
                (root / "a.txt").write_text("净重：320g\n", encoding="utf-8")

            first = analyze_directory(root_one, output)
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            candidate_id = facts["candidates"][0]["candidate_id"]
            apply_decision(
                output,
                session_id=first["session_id"],
                candidate_id=candidate_id,
                reason="仅确认第一个商品目录",
                action="confirm",
            )

            second = analyze_directory(root_two, output)
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            confirmed = json.loads(
                (output / "confirmed-product-facts.json").read_text(encoding="utf-8")
            )
            state = json.loads((output / "analysis-state.json").read_text(encoding="utf-8"))
            self.assertNotEqual(second["session_id"], first["session_id"])
            self.assertEqual(second["changed_or_new_files"], ["a.txt"])
            self.assertEqual(second["unchanged_files_reused"], [])
            self.assertEqual(facts["candidates"][0]["status"], "pending")
            self.assertEqual(confirmed["facts"], [])
            self.assertEqual(Path(state["input_root"]), root_two.resolve())

    def test_missing_analysis_state_recovers_without_manual_confirmation_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "input"
            output = Path(tmp) / "output"
            root.mkdir()
            (root / "a.txt").write_text("净重：320g\n", encoding="utf-8")
            first = analyze_directory(root, output)
            facts = json.loads((output / "product-facts.json").read_text(encoding="utf-8"))
            candidate_id = facts["candidates"][0]["candidate_id"]
            apply_decision(
                output,
                session_id=first["session_id"],
                candidate_id=candidate_id,
                reason="旧会话确认",
                action="confirm",
            )
            (output / "analysis-state.json").unlink()

            recovered = analyze_directory(root, output)
            confirmation_state = json.loads(
                (output / "confirmation-state.json").read_text(encoding="utf-8")
            )
            self.assertNotEqual(recovered["session_id"], first["session_id"])
            self.assertEqual(
                confirmation_state["session_id"],
                recovered["session_id"],
            )
            self.assertEqual(
                confirmation_state["decisions"][candidate_id]["status"],
                "stale",
            )

    def test_atomic_json_ignores_precreated_legacy_temp_hardlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "state.json"
            victim = root / "victim.txt"
            legacy_temp = target.with_suffix(target.suffix + ".tmp")
            victim.write_text("do-not-change", encoding="utf-8")
            try:
                os.link(victim, legacy_temp)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            atomic_write_json(target, {"safe": True})
            self.assertEqual(victim.read_text(encoding="utf-8"), "do-not-change")
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"safe": True},
            )


if __name__ == "__main__":
    unittest.main()
