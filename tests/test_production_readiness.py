from __future__ import annotations
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from product_evidence_guard import workflow
from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.extractor import extract_rule_candidates
from product_evidence_guard.input_safety import validate_office_archive
from product_evidence_guard.models import SourceBlock
from product_evidence_guard.parsers import parse_xlsx_document, sha256_file
from product_evidence_guard.readiness import assess_delivery, reading_issue
from product_evidence_guard.state import (begin_publication, finish_publication, recover_publication,
                                          atomic_write_json, load_state)
from product_evidence_guard.structured_rows import bind_table
from product_evidence_guard.task_jobs import TaskJobs
from scripts.production_acceptance import evaluate


def table(values):
    return [(i, list(enumerate(row))) for i, row in enumerate(values)]


class StructureTests(unittest.TestCase):
    def test_matrix_explicit_model_columns_bind_without_vendor_template(self):
        cells = bind_table(table([["型号", "AB-1", "CD-2"], ["输入电压", "12V", "24V"],
                                  ["新参数", "6", "9"]]), table_id="x")
        self.assertEqual(len(cells), 4)
        self.assertEqual([(c.identity["model"], c.value) for c in cells],
                         [("AB-1", "12V"), ("CD-2", "24V"), ("AB-1", "6"), ("CD-2", "9")])

    def test_row_oriented_header_is_not_mistaken_for_models(self):
        cells = bind_table(table([["型号", "电压", "功率"], ["AB-1", "12V", "15W"]]), table_id="x")
        self.assertTrue(all(c.identity == {"model": "AB-1"} for c in cells))

    def test_blank_matrix_value_is_never_forward_filled(self):
        cells = bind_table(table([["MODEL", "A1", "B2"], ["voltage", "12V", None]]), table_id="x")
        self.assertEqual([(c.identity["model"], c.value) for c in cells], [("A1", "12V")])

    def test_same_physical_merged_header_is_one_model_column(self):
        rows = [(0, [((0, 0, 10, 10), "MODEL"), ((10, 0, 30, 10), "A1"),
                     ((10, 0, 30, 10), "A1"), ((30, 0, 40, 10), "B2")]),
                (1, [((0, 10, 10, 20), "新参数"), ((10, 10, 30, 20), "6"),
                     ((10, 10, 30, 20), "6"), ((30, 10, 40, 20), "9")])]
        cells = bind_table(rows, table_id="geometry")
        self.assertEqual([(c.identity["model"], c.value) for c in cells], [("A1", "6"), ("B2", "9")])
        self.assertEqual(cells[0].location, (10, 10, 30, 20))

    def test_repeated_model_text_without_same_geometry_stays_ambiguous(self):
        for locations in ((1, 2, 3), ((10, 0, 20, 10), (20, 0, 30, 10), (30, 0, 40, 10))):
            with self.subTest(locations=locations):
                rows = [(0, [(0, "MODEL"), *zip(locations, ("A1", "A1", "B2"))]),
                        (1, [(0, "新参数"), *zip(locations, ("6", "7", "9"))])]
                self.assertEqual(bind_table(rows, table_id="ambiguous"), [])

    def test_merged_model_header_does_not_swallow_distinct_subrow_values(self):
        for second_value in ("6", "7", ""):
            with self.subTest(second_value=second_value):
                rows = [(0, [((0, 0, 10, 10), "MODEL"), ((10, 0, 30, 10), "A1"),
                             ((10, 0, 30, 10), "A1"), ((30, 0, 40, 10), "B2")]),
                        (1, [((0, 10, 10, 20), "新参数"), ((10, 10, 20, 20), "6"),
                             ((20, 10, 30, 20), second_value), ((30, 10, 40, 20), "9")])]
                self.assertEqual(bind_table(rows, table_id="split"), [])

    def test_vertical_merged_label_is_not_split_into_competing_facts(self):
        rows = [(0, [((0, 0, 10, 10), "MODEL"), ((10, 0, 20, 10), "A1"), ((20, 0, 30, 10), "B2")]),
                (1, [((0, 10, 10, 30), "Protection"), ((10, 10, 20, 20), "120%"), ((20, 10, 30, 20), "120%")]),
                (2, [((0, 10, 10, 30), "Protection"), ((10, 20, 20, 30), "Auto recovery"), ((20, 20, 30, 30), "Auto recovery")]),
                (3, [((0, 30, 10, 40), "新参数"), ((10, 30, 20, 40), "6"), ((20, 30, 30, 40), "9")])]
        cells = bind_table(rows, table_id="vertical")
        self.assertEqual([(c.header, c.value) for c in cells], [("新参数", "6"), ("新参数", "9")])

    def test_pdf_merged_side_label_never_moves_values_into_another_row(self):
        from product_evidence_guard.pdf_layout import read_layout_page
        boxes = [[(0, 0, 20, 10), None, (20, 0, 30, 10), (30, 0, 40, 10), None],
                 [(0, 10, 10, 40), (10, 10, 20, 20), (20, 10, 40, 20), None, None],
                 [None, (10, 20, 20, 30), (20, 20, 30, 30), (30, 20, 40, 30), None],
                 [None, (10, 30, 20, 40), (20, 30, 30, 40), (30, 30, 35, 40), (35, 30, 40, 40)]]
        raw = [["MODEL", None, "A1", "B2", None], ["INPUT", "Frequency", "47~63Hz", None, None],
               [None, "Efficiency", "80%", "90%", None], [None, "Mode", "common", "3", "4"]]
        rows = [SimpleNamespace(cells=cells, bbox=(0, i * 10, 40, 40 if i == 1 else (i + 1) * 10))
                for i, cells in enumerate(boxes)]
        words = []
        for cells, values in zip(boxes, raw):
            for box, value in zip(cells, values):
                if box and value:
                    words.append(dict(text=value, x0=box[0] + 1, x1=box[0] + 3,
                                      top=box[1] + 1, bottom=box[1] + 3))
        table_obj = SimpleNamespace(bbox=(0, 0, 40, 40), rows=rows,
                                    cells=[b for row in boxes for b in row if b],
                                    extract=lambda **kwargs: raw)
        edges = [dict(x0=0, x1=40, top=y, bottom=y) for y in (0, 10, 20, 30, 40)]
        edges += [dict(x0=x, x1=x, top=0, bottom=40) for x in (0, 10, 20, 30, 35, 40)]
        page = SimpleNamespace(width=40, height=40, chars=[], edges=edges,
                               extract_words=lambda **kwargs: words, find_tables=lambda settings: [table_obj])
        page.dedupe_chars = lambda: page
        records, issues = read_layout_page(page, 1)
        frequency = [r for r in records if r["text"].startswith("Frequency:")]
        self.assertEqual(len(frequency), 2)
        self.assertEqual({r["text"] for r in frequency}, {"Frequency: 47~63Hz"})
        self.assertEqual({r["provenance"]["structured_row"]["identity"]["model"] for r in frequency}, {"A1", "B2"})
        self.assertEqual({tuple(r["locator"]["bbox_1000"]) for r in frequency}, {(500.0, 250.0, 1000.0, 500.0)})
        self.assertTrue(any(3 in issue.get("unbound_rows", []) for issue in issues))

    def test_repeated_row_header_is_not_a_product(self):
        cells = bind_table(table([["型号", "电压"], ["A1", "12V"], ["型号", "电压"], ["B2", "24V"]]), table_id="x")
        self.assertEqual(len(cells), 4)

    def test_parent_context_separates_conditions(self):
        block = SourceBlock("id", "spec.pdf", "pdf_text", "hash", {"page": 1}, "rated power: 35W",
                            provenance={"parent_labels": ["OUTPUT"]})
        facts = extract_rule_candidates(block)
        self.assertEqual(facts[0].scope, "output|rating:rated")

    def test_two_matrix_sections_keep_their_conditions(self):
        cells = bind_table(table([["MODEL", "A1", "B2"], ["STC", "", ""], ["power", "400W", "410W"],
                                 ["NMOT", "", ""], ["power", "300W", "310W"]]), table_id="x")
        self.assertEqual([c.parent_labels for c in cells], [("STC",), ("STC",), ("NMOT",), ("NMOT",)])

    def test_merged_label_columns_keep_parent_scope(self):
        cells = bind_table(table([["MODEL", "MODEL", "A1", "B2"], ["OUTPUT", "Rated current", "1A", "2A"]]), table_id="x")
        self.assertEqual([c.parent_labels for c in cells], [("OUTPUT",), ("OUTPUT",)])
        self.assertEqual([c.header for c in cells], ["Rated current", "Rated current"])

    def test_unknown_column_headers_do_not_become_product_names(self):
        cells = bind_table(table([["SKU", "光波长(nm)", "传输距离(km)"], ["OPTICS", "1310", "10"]]), table_id="x")
        self.assertTrue(all(c.identity == {"sku": "OPTICS"} for c in cells))

    def test_small_print_skew_is_horizontal_not_a_vertical_border(self):
        from product_evidence_guard.pdf_layout import _orthogonal_edges
        h, v = _orthogonal_edges([{"x0": 40, "x1": 500, "top": 120, "bottom": 120.13, "orientation": "v"}])
        self.assertEqual(len(h), 1)
        self.assertFalse(v)
        self.assertEqual(h[0]["top"], h[0]["bottom"])

    def test_explicit_qualified_field_does_not_lose_its_meaning(self):
        block = SourceBlock("id", "x.txt", "text", "hash", {}, "leakage current: 0.75mA")
        facts = extract_rule_candidates(block)
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0].field.startswith("custom_"))
        self.assertEqual(facts[0].normalized_value, "0.75mA")

    def test_compound_io_label_still_uses_existing_electrical_binder(self):
        block = SourceBlock("id", "x.txt", "text", "hash", {}, "输入：5V/3A")
        facts = extract_rule_candidates(block)
        self.assertEqual({(f.field, f.scope) for f in facts}, {("voltage", "input"), ("current", "input")})

    def test_repeated_aligned_pairs_ignore_neighbor_body_column(self):
        from product_evidence_guard.pdf_layout import _aligned_pairs
        def word(text, x, top):
            return {"text": text, "x0": x, "x1": x + 45, "top": top, "bottom": top + 8}
        lines = [[word("Long body paragraph unrelated to the product value row", 20, y), word(label, 200, y), word(value, 400, y)]
                 for y, label, value in ((20,"Dimensions","80x80x25mm"),(35,"Bearing","SSO2"),(50,"MTTF",">150000h"))]
        pairs = _aligned_pairs(lines)
        self.assertEqual(len(pairs), 3)
        self.assertEqual(pairs[0][0][0]["text"], "Dimensions")

    def test_parallel_subtables_are_not_flattened_into_conflicts(self):
        cells = bind_table(table([["Pin No.", "Assignment", "Pin No.", "Assignment"],
                                 ["1", "AC/L", "2", "AC/N"]]), table_id="x")
        self.assertEqual(cells, [])


class LocalFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="peg-production-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "input"
        self.output = self.root / "output"
        self.source.mkdir()
        (self.source / "spec.txt").write_text("型号：A1\n净重：320g", encoding="utf-8")

    def analyze(self):
        return analyze_directory(self.source, self.output)


class DeliveryTests(LocalFixture):
    def test_clear_single_source_still_exports_without_extra_confirmation(self):
        self.analyze()
        result = workflow.export_table(self.output)
        self.assertTrue(result["delivery_scope"]["complete"])

    def test_incomplete_input_blocks_default_and_partial_is_explicit(self):
        (self.source / "legacy.xls").write_bytes(b"not-a-supported-workbook")
        self.analyze()
        with self.assertRaisesRegex(ValueError, "仅导出已确认部分"):
            workflow.export_table(self.output)
        result = workflow.export_table(self.output, allow_partial=True)
        self.assertFalse(result["delivery_scope"]["complete"])
        self.assertIn("仅已确认部分", Path(result["path"]).read_text(encoding="utf-8-sig"))
        brief = workflow.export_local(self.output, allow_partial=True)
        self.assertEqual(brief["status"], "partial_delivery_ready")
        self.assertIn("部分交付", Path(brief["artifacts"]["brief"]).read_text(encoding="utf-8"))
        self.assertEqual(workflow.task_snapshot(self.output)["local_delivery"]["status"], "partial")

    def test_file_with_zero_facts_still_invalidates_complete_delivery(self):
        extra = self.source / "note.txt"
        extra.write_text("Supplementary document", encoding="utf-8")
        self.analyze()
        extra.write_text("Supplementary document changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "原资料已改变"):
            workflow.export_table(self.output)

    def test_false_string_is_not_partial_authorization(self):
        self.analyze()
        with self.assertRaises(ValueError):
            workflow.export_table(self.output, allow_partial="false")

    def test_new_unread_file_cannot_leave_delivery_marked_complete(self):
        self.analyze()
        (self.source / "new-batch.txt").write_text("净重：999g", encoding="utf-8")
        self.assertFalse(workflow.task_snapshot(self.output)["delivery_readiness"]["complete"])
        with self.assertRaises(ValueError):
            workflow.export_table(self.output)

    def test_legacy_result_needs_reading_upgrade_not_new_human_approval(self):
        self.analyze()
        path = self.output / "product-facts.json"
        product = json.loads(path.read_text(encoding="utf-8"))
        product["run_summary"].pop("source_inventory")
        atomic_write_json(path, product)
        snapshot = workflow.task_snapshot(self.output)
        self.assertFalse(snapshot["delivery_readiness"]["complete"])
        self.assertGreater(snapshot["counts"]["verified_facts"], 0)
        with self.assertRaises(ValueError):
            workflow.export_table(self.output)

    def test_known_other_product_problem_does_not_block_selection(self):
        product = {"facts": [{"product_id": "a", "fact_status": "verified"},
                             {"product_id": "b", "fact_status": "conflict"}], "run_summary": {}}
        self.assertTrue(assess_delivery(product, product_ids=["a"])["complete"])
        product["run_summary"]["reading_issues"] = [{"file": "unclear.pdf"}]
        self.assertFalse(assess_delivery(product, product_ids=["a"])["complete"])

    def test_issue_fingerprint_changes_when_original_changes(self):
        first = reading_issue(file="x", file_hash="a", code="table", locator={"page": 2})
        second = reading_issue(file="x", file_hash="b", code="table", locator={"page": 2})
        self.assertNotEqual(first["issue_id"], second["issue_id"])


class SafetyAndRecoveryTests(LocalFixture):
    def test_hidden_sheets_and_formula_cells_are_reported_not_confirmed(self):
        from openpyxl import Workbook
        path = self.source / "table.xlsx"
        book = Workbook()
        book.active.append(["型号", "净重"])
        book.active.append(["A1", "=100+200"])
        sheet = book.create_sheet("hidden")
        sheet.append(["净重", "999g"])
        sheet.sheet_state = "hidden"
        book.save(path)
        book.close()
        parsed = parse_xlsx_document(path, path.name, sha256_file(path))
        self.assertEqual({i["code"] for i in parsed.reading_issues}, {"hidden_sheet", "formula_unverified"})
        self.assertFalse(any("999" in b.text or "100+200" in b.text for b in parsed.blocks))

    def test_archive_dtd_and_path_traversal_rejected(self):
        for name, value in (("../a.xml", b"<x/>"), ("word/document.xml", b'<!DOCTYPE x [<!ENTITY a "x">]><x/>')):
            path = self.root / "hostile.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(name, value)
            with self.assertRaises(ValueError):
                validate_office_archive(path)

    def test_source_changed_during_parse_does_not_publish_mixed_facts(self):
        from product_evidence_guard.parsers import parse_file
        def changing(path, root, **kwargs):
            result = parse_file(path, root, **kwargs)
            path.write_text("型号：A1\n净重：999g", encoding="utf-8")
            return result
        with patch("product_evidence_guard.engine.parse_file", side_effect=changing):
            summary = self.analyze()
        self.assertEqual(summary["candidate_count"], 0)
        self.assertTrue(summary["errors"])

    def test_journal_restores_a_whole_previous_result(self):
        self.analyze()
        before = (self.output / "product-facts.json").read_bytes()
        begin_publication(self.output)
        atomic_write_json(self.output / "product-facts.json", {"broken_generation": True})
        self.assertTrue(recover_publication(self.output))
        self.assertEqual((self.output / "product-facts.json").read_bytes(), before)
        self.assertFalse(recover_publication(self.output))

    def test_corrupt_journal_never_restores_untrusted_data(self):
        self.analyze()
        begin_publication(self.output)
        atomic_write_json(self.output / ".previous-complete-result.json", {"../outside": "evil"})
        with self.assertRaises(ValueError):
            recover_publication(self.output)

    def test_non_object_state_is_safe_empty_state(self):
        path = self.root / "state.json"
        path.write_text("[]", encoding="utf-8")
        self.assertEqual(load_state(path)["files"], {})

    def test_checkpoint_survives_interruption_and_is_reused(self):
        (self.source / "z.txt").write_text("功率：20W", encoding="utf-8")
        def stop(stage, details):
            if stage == "file_started" and details.get("index") == 2:
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            analyze_directory(self.source, self.output, progress_callback=stop)
        self.assertTrue((self.output / "analysis-checkpoint.json").exists())
        summary = self.analyze()
        self.assertIn("spec.txt", summary["unchanged_files_reused"])


class JobsAndAcceptanceTests(LocalFixture):
    def test_cancellation_terminates_only_the_active_worker(self):
        from tests.test_device_and_resident_model import ResidentAnalysisWorker
        from types import SimpleNamespace
        calls = []
        proc = SimpleNamespace(is_alive=lambda: True, terminate=lambda: calls.append("terminate"),
                               join=lambda timeout: None)
        conn = SimpleNamespace(send=lambda data: None, close=lambda: calls.append("close"))
        worker = ResidentAnalysisWorker()
        worker._process, worker._connection = proc, conn
        with self.assertRaisesRegex(RuntimeError, "任务已取消"):
            worker.analyze({}, on_model_ready=lambda: None, cancel_check=lambda: True)
        self.assertEqual(calls, ["close", "terminate"])
        self.assertIsNone(worker._process)
    def test_corrupt_job_does_not_stop_startup(self):
        directory = self.root / "jobs"
        directory.mkdir()
        (directory / "broken.json").write_text("{", encoding="utf-8")
        jobs = TaskJobs(self.root, lambda p, progress: {})
        self.assertEqual(jobs.unreadable_records, 1)
        self.assertTrue((directory / "broken.json").exists())

    def test_different_payload_cannot_silently_reuse_job(self):
        release = threading.Event()
        self.addCleanup(release.set)
        def execute(payload, progress):
            release.wait(5)
            return {"ok": True}
        jobs = TaskJobs(self.root, execute)
        payload = {"input_dir": str(self.source), "output_dir": str(self.output)}
        first = jobs.submit(payload)
        self.assertEqual(jobs.submit(payload)["job_id"], first["job_id"])
        with self.assertRaises(ValueError):
            jobs.submit({**payload, "device": "GPU"})
        jobs.cancel(first["job_id"])
        release.set()
        for thread in jobs.threads:
            thread.join(2)
        self.assertEqual(jobs.snapshot(first["job_id"])["phase"], "cancelled")

    def test_missing_independent_evidence_is_a_blocker_not_a_pass(self):
        report = evaluate({"packages": [], "release_evidence": {}}, self.root)
        self.assertEqual(report["status"], "blocked")
        self.assertGreaterEqual(len(report["failures"]), 10)


if __name__ == "__main__":
    unittest.main()
