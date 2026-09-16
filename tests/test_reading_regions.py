"""Layout/condition invariants using arbitrary fields, not supplier templates."""
from types import SimpleNamespace
import unittest

from product_evidence_guard.extractor import extract_rule_candidates, parameter_segments, block_segments
from product_evidence_guard.graph import build_graph
from product_evidence_guard.models import SourceBlock
from product_evidence_guard.pdf_conditions import attach_condition_notes, collect_condition_notes, raised_reference_words
from product_evidence_guard.pdf_layout import _horizontal_pairs, _lines, _orthogonal_edges, _text, read_layout_page, without_running_margins
from product_evidence_guard.structured_rows import bind_table


def word(text, x, y, width=45, size=10):
    return dict(text=text, x0=x, x1=x + width, top=y, bottom=y + size, size=size)


def page(words, edges=(), tables=()):
    result = SimpleNamespace(width=600, height=800, chars=[], edges=list(edges),
                             extract_words=lambda **kwargs: words, find_tables=lambda settings: tables)
    result.dedupe_chars = lambda: result
    return result


def block(text, **meta):
    return SourceBlock("b", "source.pdf", "pdf_text", "hash", {"page": 1}, text, provenance=meta)


class BoundValueTests(unittest.TestCase):
    def test_atomic_value_is_not_split_by_inner_colons(self):
        text = "新型指标: A: 10; Mounting: horizontal; Standard: XYZ:2025"
        self.assertEqual(parameter_segments(text, atomic=True), [text])
        rows = extract_rule_candidates(block(text, atomic_parameter=True))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].field_label, "新型指标")
        self.assertIn("mounting: horizontal", rows[0].normalized_value)
        self.assertIn("standard: xyz:2025", rows[0].normalized_value)

    def test_ordinary_text_still_supports_multiple_explicit_parameters(self):
        rows = extract_rule_candidates(block("净重: 10g; 新参数: 12 units"))
        self.assertEqual(len(rows), 2)

    def test_office_and_csv_reuse_atomic_segmentation(self):
        for kind in ("csv_row", "xlsx_row", "docx_table_row"):
            b = block("陌生参数 | Lab: A; Mounting: B")
            b.source_kind = kind
            self.assertEqual(block_segments(b), [b.text])
            rows = extract_rule_candidates(b)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].field_label, "陌生参数")
            self.assertIn("mounting: b", rows[0].normalized_value)

    def test_open_field_requires_binding_only_in_unbound_pdf_prose(self):
        text = "without interruption: our design is improved"
        self.assertFalse(extract_rule_candidates(block(text, layout_binding="unbound_text")))
        self.assertEqual(len(extract_rule_candidates(block(text, atomic_parameter=True))), 1)

    def test_raised_and_lowered_runs_keep_their_order(self):
        words = [word("XyO", 0, 10, 20), word("2", 20, 14, 4, 6), word(")", 24, 10, 4)]
        self.assertEqual(_text(_lines(words)[0]), "XyO2)")
        self.assertFalse(raised_reference_words(words))

    def test_paragraph_colon_does_not_make_a_parameter(self):
        words = [word("Previous prose " + "text " * 17, 20, 10, 550),
                 word("without", 20, 25, 40), word("restriction:", 62, 25, 55),
                 word("flowing prose " + "text " * 16, 120, 25, 440),
                 word("Continuing prose " + "text " * 17, 20, 40, 550)]
        records, _ = read_layout_page(page(words), 1)
        self.assertFalse(any(r["provenance"].get("atomic_parameter") for r in records))

    def test_unknown_single_source_parameter_is_still_auto_confirmed(self):
        rows = extract_rule_candidates(block("一种全新参数: 7 widgets", atomic_parameter=True))
        _, groups, _ = build_graph(rows)
        self.assertEqual(groups[0].fact_status, "verified")

    def test_unbound_model_heading_cannot_rename_the_product(self):
        self.assertFalse(extract_rule_candidates(block("Model Code", layout_binding="unbound_text")))
        self.assertTrue(extract_rule_candidates(block("Model: AX-77", atomic_parameter=True)))

    def test_repeated_margin_label_is_not_a_product_fact(self):
        blocks = [block("Document number: " + str(i), layout_binding="wrapped_aligned_pair") for i in (1, 2)]
        for i, b in enumerate(blocks):
            b.locator = {"page": i + 1, "bbox_1000": [50, 950, 200, 963]}
        body = block("New metric: 17 widgets", atomic_parameter=True)
        self.assertEqual(without_running_margins(blocks + [body]), [body])
        for b in blocks:
            b.text = "Model: AX-77"
        self.assertEqual(len(without_running_margins(blocks + [body])), 3)

    def test_merged_cell_is_not_its_own_parameter_value(self):
        shared = (10, 10, 300, 25)
        key, val = (10, 25, 100, 45), (100, 25, 300, 45)
        table = SimpleNamespace(bbox=(10, 10, 300, 45), cells=[shared, key, val],
            rows=[SimpleNamespace(cells=[shared, None], bbox=shared),
                  SimpleNamespace(cells=[key, val], bbox=(10, 25, 300, 45))],
            extract=lambda **kwargs: [["AX-77", None], ["New metric", "17 widgets"]])
        words = [word("AX-77", 20, 12), word("New metric", 20, 28), word("17 widgets", 110, 28)]
        edges = [dict(x0=10, x1=300, top=y, bottom=y) for y in (10, 25, 45)]
        edges += [dict(x0=x, x1=x, top=10, bottom=45) for x in (10, 100, 300)]
        records, _ = read_layout_page(page(words, edges, [table]), 1)
        self.assertEqual([r["text"] for r in records], ["New metric: 17 widgets"])

    def test_parallel_horizontal_rule_regions_never_cross_page_columns(self):
        words, edges = [], []
        for x, prefix in ((10, "Left"), (310, "Right")):
            for y in (10, 30, 50, 70):
                edges.extend([dict(x0=x, x1=x + 90, top=y, bottom=y),
                              dict(x0=x + 90, x1=x + 270, top=y, bottom=y)])
            for i, y in enumerate((14, 34, 54)):
                words.extend([word(prefix + str(i), x, y, 50), word(str(i + 1) + " units", x + 92, y, 70)])
        records, issues = read_layout_page(page(words, edges), 1)
        self.assertFalse(issues)
        self.assertEqual(len(records), 6)
        self.assertTrue(all(r["text"].count(":") == 1 for r in records))
        self.assertFalse(any("Left" in r["text"] and "Right" in r["text"] for r in records))

    def test_white_backgrounds_are_not_table_borders(self):
        edge = dict(x0=0, x1=80, top=10, bottom=10, object_type="rect_edge", stroke=False,
                    non_stroking_color=(1, 1, 1), pts=[(0, 10), (80, 10), (80, 40), (0, 40)])
        horizontal, vertical = _orthogonal_edges([edge])
        self.assertFalse(horizontal + vertical)
        # Thin filled bars still represent printed rules.
        edge.update(non_stroking_color=(0, 0, 0), pts=[(0, 10), (80, 10), (80, 10.2), (0, 10.2)])
        self.assertEqual(len(_orthogonal_edges([edge])[0]), 1)

    def test_three_repeated_columns_are_not_two_column_pairs(self):
        edges = [dict(x0=x, x1=x + 90, top=y, bottom=y)
                 for x in (10, 100, 190) for y in (10, 30, 50)]
        words = [word(text, x, y, 60) for y in (14, 34)
                 for x, text in ((10, "Metric"), (100, "5 units"), (190, "9 units"))]
        self.assertFalse(_horizontal_pairs(words, edges, []))

    def test_shaded_parameter_header_above_grid_is_bound_by_columns(self):
        columns = (10, 150, 250, 350, 450)
        boxes = [(l, 40, r, 60) for l, r in zip(columns, columns[1:])]
        second = [(l, 60, r, 80) for l, r in zip(columns, columns[1:])]
        table = SimpleNamespace(bbox=(10, 40, 450, 80), cells=boxes + second,
            rows=[SimpleNamespace(cells=boxes, bbox=(10, 40, 450, 60)),
                  SimpleNamespace(cells=second, bbox=(10, 60, 450, 80))],
            extract=lambda **kw: [["Novel metric", "1", "3", "µA"], ["Another metric", "2", "4", "mA"]])
        words = [word(text, x, 26, 70) for x, text in zip(columns, ("Parameter", "Min.", "Max.", "Unit"))]
        edges = [dict(x0=10, x1=450, top=y, bottom=y) for y in (40, 60, 80)]
        edges += [dict(x0=x, x1=x, top=40, bottom=80) for x in columns]
        records, issues = read_layout_page(page(words, edges, [table]), 1)
        self.assertFalse(issues)
        self.assertEqual([r["text"] for r in records], ["Novel metric: min: 1 µA; max: 3 µA", "Another metric: min: 2 mA; max: 4 mA"])

    def test_table_without_record_owner_is_one_reading_issue_not_many_conflicts(self):
        columns = (10, 150, 300)
        boxes = [[(l, y, r, y + 20) for l, r in zip(columns, columns[1:])] for y in (10, 30, 50)]
        table = SimpleNamespace(bbox=(10, 10, 300, 70), cells=sum(boxes, []),
            rows=[SimpleNamespace(cells=b, bbox=(10, y, 300, y + 20)) for b, y in zip(boxes, (10, 30, 50))],
            extract=lambda **kw: [["Substance", "Maximum limit"], ["A", "1000 ppm"], ["B", "100 ppm"]])
        edges = [dict(x0=10, x1=300, top=y, bottom=y) for y in (10, 30, 50, 70)]
        edges += [dict(x0=x, x1=x, top=10, bottom=70) for x in columns]
        records, issues = read_layout_page(page([], edges, [table]), 1)
        self.assertFalse(records)
        self.assertEqual(len(issues), 1)

    def test_list_bullet_is_not_part_of_parameter_identity(self):
        words = [word("•", 10, 10, 4), word("Weight:", 18, 10, 36), word("12g", 58, 10, 20)]
        records, _ = read_layout_page(page(words), 1)
        self.assertEqual(records[0]["text"], "Weight: 12g")

    def test_inline_value_does_not_swallow_neighboring_page_column(self):
        words = [word("• Bandwidth:", 10, 10, 65), word("10Gbps", 78, 10, 40),
                 word("- unrelated package contents", 310, 10, 180)]
        records, _ = read_layout_page(page(words), 1)
        bound = [r for r in records if r["provenance"].get("atomic_parameter")]
        self.assertEqual([r["text"] for r in bound], ["Bandwidth: 10Gbps"])

    def test_section_heading_does_not_borrow_a_distant_list_or_logo(self):
        for text in ("- network cables", "®"):
            records, _ = read_layout_page(page([word("Without clip:", 10, 10, 70), word(text, 310, 10, 120)]), 1)
            self.assertFalse(any(r["provenance"].get("atomic_parameter") for r in records))

    def test_numbered_bullet_value_is_still_a_legitimate_parameter_value(self):
        records, _ = read_layout_page(page([word("Peripherals:", 10, 10, 70),
                                            word("• 2 UART", 120, 10, 80), word("• 3 SPI", 120, 24, 80)]), 1)
        self.assertEqual(records[0]["text"], "Peripherals: • 2 UART • 3 SPI")
        self.assertTrue(records[0]["provenance"]["atomic_parameter"])


class ParameterTableTests(unittest.TestCase):
    @staticmethod
    def rows(values):
        return [(i, [(f"cell:{i}:{j}", value) for j, value in enumerate(row)]) for i, row in enumerate(values)]

    def test_min_typ_max_and_conditions_belong_to_one_parameter(self):
        cells = bind_table(self.rows([["Parameter", "Symbol", "Conditions", "Min.", "Typ.", "Max.", "Unit"],
                         ["Unfamiliar quantity", "X", "heater on at 1MW", "-", "6", "10", "mA"]]), table_id="t")
        self.assertEqual(len(cells), 1)
        self.assertEqual(cells[0].header, "Unfamiliar quantity")
        self.assertEqual(cells[0].value, "typ: 6 mA; max: 10 mA (heater on at 1MW)")
        self.assertEqual(cells[0].parent_labels, ("heater on at 1MW",))
        candidate = extract_rule_candidates(block(cells[0].header + ": " + cells[0].value,
            atomic_parameter=True, parent_labels=list(cells[0].parent_labels)))[0]
        self.assertIn("1MW", candidate.scope)

    def test_unbound_stacked_measurements_are_not_flattened(self):
        cells = bind_table(self.rows([["Parameter", "Max", "Unit", "Conditions"],
                         ["New quantity", "1.0\n3.4", "µA", "25°C\n125°C"]]), table_id="t")
        self.assertEqual(cells, [])

    def test_reference_tables_do_not_create_competing_product_facts(self):
        for header, values in ((["Date", "Revision", "Changes"], ["2025", "1", "Updated"]),
                               (["Pin", "Name", "Function"], ["1", "A", "Clock"]),
                               (["日期", "版本", "变更"], ["2025", "1", "更新"])):
            self.assertEqual(bind_table(self.rows([header, values]), table_id="t"), [])


class ConditionTests(unittest.TestCase):
    def test_raised_reference_not_normal_number_or_squared_unit(self):
        words = [word("Service", 0, 10, 40), word("3", 40, 9.8, 3, 6)]
        self.assertEqual([w["text"] for w in raised_reference_words(words)], ["3"])
        words[0]["text"], words[1]["text"] = "cm", "2"
        self.assertFalse(raised_reference_words(words))
        words[1].update(top=10, bottom=20)
        self.assertFalse(raised_reference_words(words))

    def test_definition_continuation_stops_at_next_note(self):
        p = page([word("1. Applies only at 3V", 20, 30, 230, 8),
                  word("and 25 degrees", 20, 41, 200, 8),
                  word("2. Other conditions", 20, 52, 230, 8),
                  word("Different section", 20, 100, 230, 12)])
        notes = collect_condition_notes(p, 4)
        self.assertEqual(len(notes), 2)
        self.assertEqual(notes[0]["text"], "Applies only at 3V and 25 degrees")
        self.assertEqual(notes[1]["text"], "Other conditions")
        self.assertEqual(notes[0]["locator"]["page"], 4)

    def test_plain_number_definitions_do_not_swallow_next_note(self):
        notes = collect_condition_notes(page([word("6 Measured at ambient temperature", 20, 700, 300, 8),
                                               word("7 Measured with the heater active", 20, 711, 300, 8)]), 9)
        self.assertEqual(len(notes), 2)
        self.assertEqual(notes[0]["text"], "Measured at ambient temperature")

    def test_reused_marker_on_multiple_pages_needs_local_definition(self):
        blocks = [block("New metric: 5 units", atomic_parameter=True, condition_refs=["6"]) for _ in range(2)]
        blocks[1].locator = {"page": 2}
        attach_condition_notes(blocks, [{"marker": "6", "text": "Unrelated definition", "locator": {"page": 8}}])
        self.assertTrue(all(b.provenance["unresolved_condition_refs"] == ["6"] for b in blocks))

    def test_placeholder_with_footnote_is_not_a_verified_measurement(self):
        b = block("Novel metric: -", atomic_parameter=True,
                  source_conditions=[{"marker": "1", "text": "Only under PROFILE-Z", "locator": {"page": 2}}])
        _, groups, _ = build_graph(extract_rule_candidates(b))
        self.assertEqual(groups[0].fact_status, "pending_confirmation")

    def test_cross_page_condition_is_part_of_adopted_value(self):
        b = block("Endurance: 300TB", atomic_parameter=True, condition_refs=["4"])
        attach_condition_notes([b], [{"marker": "4", "text": "Measured with PROFILE-Z", "locator": {"page": 3}}])
        row = extract_rule_candidates(b)[0]
        self.assertIn("profile-z", row.normalized_value)
        self.assertIn("PROFILE-Z", row.raw_text)
        self.assertEqual(row.provenance["source_conditions"][0]["locator"]["page"], 3)
        _, groups, _ = build_graph([row])
        self.assertEqual(groups[0].fact_status, "verified")
        self.assertIn("profile-z", groups[0].selected_value)

    def test_ambiguous_or_missing_condition_is_never_silently_dropped(self):
        for notes in ([], [{"marker": "1", "text": "A", "locator": {"page": 2}},
                          {"marker": "1", "text": "B", "locator": {"page": 3}}]):
            b = block("Endurance: 300TB", atomic_parameter=True, condition_refs=["1"])
            attach_condition_notes([b], notes)
            _, groups, _ = build_graph(extract_rule_candidates(b))
            self.assertEqual(groups[0].fact_status, "pending_confirmation")

    def test_page_local_definition_wins_without_guessing_other_pages(self):
        b = block("Endurance: 300TB", atomic_parameter=True, condition_refs=["1"])
        attach_condition_notes([b], [{"marker": "1", "text": "Local", "locator": {"page": 1}},
                                   {"marker": "1", "text": "Other", "locator": {"page": 3}}])
        self.assertEqual(b.provenance["source_conditions"][0]["text"], "Local")

    def test_numeric_value_keeps_condition_in_scope_and_case(self):
        b = block("净重: 30g", atomic_parameter=True, source_conditions=[
            {"marker": "1", "text": "At 1MW load", "locator": {"page": 2}}])
        row = extract_rule_candidates(b)[0]
        self.assertEqual(row.normalized_value, 30)
        self.assertIn("1MW", row.scope)

    def test_different_text_conditions_are_not_a_same_scope_conflict(self):
        rows = []
        for i, condition in enumerate(("Humidity response", "Temperature response")):
            b = block(f"Response time: {i + 2}s", atomic_parameter=True,
                source_conditions=[{"marker": "1", "text": condition, "locator": {"page": i + 1}}])
            b.block_id = str(i)
            rows.extend(extract_rule_candidates(b))
        _, groups, _ = build_graph(rows)
        self.assertEqual(len(groups), 2)
        self.assertTrue(all(g.fact_status == "verified" for g in groups))


if __name__ == "__main__":
    unittest.main()
