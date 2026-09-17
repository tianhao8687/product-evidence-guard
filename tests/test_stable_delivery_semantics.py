"""Generic release regressions, not product-name or fixture-specific patches."""
import unittest
import json
from pathlib import Path
from product_evidence_guard.models import SourceBlock
from product_evidence_guard.extractor import extract_rule_candidates
from product_evidence_guard.identity import resolve_product_identities
from product_evidence_guard.graph import build_graph
from product_evidence_guard.normalization import normalize_value
from product_evidence_guard.structured_rows import bind_table


class StableDeliverySemanticsTests(unittest.TestCase):
    def groups(self, lines):
        candidates = []
        for i, line in enumerate(lines):
            candidates += extract_rule_candidates(SourceBlock(str(i), f"{i}.txt", "text", str(i), {"line": 1}, line))
        resolve_product_identities(candidates, dataset_root=Path("test-materials"))
        return build_graph(candidates)[1]

    def test_opaque_description_differences_require_one_choice_not_a_blocking_conflict(self):
        for values in (("Nickel-plated", "Metallic"),
                       ("Braided with shielding", "Braided with outer jacket"),
                       ("USB 3.0 at 5Gbps", "Data transfer with USB 3.0 at 5Gbps")):
            groups = self.groups(["结构描述: " + value for value in values])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].fact_status, "pending_confirmation")
            self.assertEqual(groups[0].review_reason_code, "compatible_expression")

    def test_open_numeric_parameters_still_detect_real_conflicts(self):
        self.assertEqual(self.groups(["噪声: 41dB", "噪声: 58dB"])[0].fact_status, "conflict")

    def test_open_scalar_unit_comparison_reuses_unit_registry(self):
        for left, right in (("1mW", "1MW"), ("1mV", "1MV"), ("2MHz", "2mHz"), ("3Pa", "3MPa")):
            self.assertEqual(self.groups(["新参数: " + left, "新参数: " + right])[0].fact_status, "conflict")
        for left, right in (("1000mW", "1W"), ("1000mV", "1V"), ("32°F", "0°C")):
            self.assertNotEqual(self.groups(["新参数: " + left, "新参数: " + right])[0].fact_status, "conflict")

    def test_signal_suffix_spacing_does_not_change_measurement(self):
        for a, b in (("20VDC", "20 V DC"), ("12-48VDC", "12-48 V DC"), ("0.3AAC", "0.3 A AC")):
            field = "current" if "A" in a and "V" not in a else "voltage"
            self.assertEqual(normalize_value(field, a), normalize_value(field, b))
        self.assertIn("unparsed_unit", normalize_value("voltage", "20 V DC except outdoor").notes)

    def test_structural_headers_do_not_become_product_facts(self):
        for label, value in (("Feature", "Specification"), ("Functionality", "Support")):
            cells = bind_table([(0,[(1,label),(2,value)]),(1,[(1,"颜色"),(2,"black")]),
                                (2,[(1,"净重"),(2,"37 g")])], table_id="table")
            self.assertIsNotNone(cells)
            self.assertFalse(any(c.header in {label, value} for c in cells))
            self.assertEqual([c.value for c in cells], ["black", "37 g"])
            self.assertEqual(cells[1].parameter_value, "37 g")
            self.assertEqual(normalize_value("net_weight", cells[1].parameter_value).value, 37)

    def test_qualified_value_headers_keep_their_measurement_meaning(self):
        cells = bind_table([(0, [(1, "Parameter"), (2, "Max")]),
                            (1, [(1, "净重"), (2, "37 g")])], table_id="limits")
        self.assertEqual(cells[0].parameter_value, "max: 37 g")
        normalized = normalize_value("net_weight", cells[0].parameter_value)
        # This compound syntax remains pending unless the common normalizer
        # understands it; it must never silently become an exact 37 g.
        self.assertNotEqual(normalized.value, 37)

    def test_complete_identity_headers_are_not_values_in_text_or_model_mapping(self):
        from product_evidence_guard.qwen_vl_reader import deterministic_field_mappings
        from product_evidence_guard.model_output_schema import VisualTranscriptionItem, parse_field_mapping_output
        for heading in ("Model Name", "Model Number", "Model Code"):
            block = SourceBlock("header", "a.txt", "text", "hash", {}, heading)
            self.assertEqual(extract_rule_candidates(block), [])
            item = VisualTranscriptionItem("h1", heading, None, "unavailable", "clear", .9)
            self.assertEqual(deterministic_field_mappings([item]), [])
            mapping = json.dumps({"schema_version": 1, "items": [{"transcription_id": "h1", "field": "model",
                "raw_value": heading.split()[1], "mapping_confidence_estimate": .99, "confidence_source": "model_self_assessment"}]})
            parsed = parse_field_mapping_output(mapping, transcriptions=[item])
            self.assertEqual(parsed.items, [])
            self.assertIn("heading_without_value", [e.code for e in parsed.errors])
        # Do not blacklist the word 'Name' or ban descriptive product models.
        for text, value in (("Model: Name", "Name"), ("Model Name: TX-82", "TX-82")):
            values = extract_rule_candidates(SourceBlock("value", "a.txt", "text", "hash", {}, text))
            self.assertEqual([c.raw_value for c in values], [value])

    def test_pdf_word_boundaries_scale_with_font_size(self):
        try:
            from pdfplumber.utils import extract_words
        except ImportError:
            self.skipTest("optional PDF layout backend unavailable")
        from product_evidence_guard.pdf_layout import read_layout_page
        for size in (5, 8, 11):
            class Page:
                width, height, edges = 300, 200, []
                chars = []
                def dedupe_chars(self):
                    return self
                def extract_words(self, **options):
                    return extract_words(self.chars, **options)
            page = Page()
            x = 10
            for word in ("Input", "Current"):
                for char in word:
                    page.chars.append({"text":char,"x0":x,"x1":x+size*.5,"top":10,"bottom":10+size,
                                       "doctop":10,"size":size,"upright":True})
                    x += size*.5
                x += size*.24
            x = 140
            for char in "120mA":
                page.chars.append({"text":char,"x0":x,"x1":x+size*.5,"top":10,"bottom":10+size,
                                   "doctop":10,"size":size,"upright":True})
                x += size*.5
            records, _ = read_layout_page(page, 1)
            self.assertIn("Input Current: 120mA", [r["text"] for r in records])
