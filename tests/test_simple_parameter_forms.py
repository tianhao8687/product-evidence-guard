"""Literal answers for simple forms, plus controls against unsafe guessing."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from product_evidence_guard.engine import analyze_directory
from product_evidence_guard.extractor import extract_rule_candidates
from product_evidence_guard.field_registry import FieldRegistryError, _load_registry
from product_evidence_guard.graph import build_graph
from product_evidence_guard.models import SourceBlock
from product_evidence_guard.normalization import normalize_value


def extract(text, **provenance):
    return extract_rule_candidates(SourceBlock("b", "source.pdf", "pdf_text", "sha",
        {"page": 1, "bbox_1000": [30, 40, 200, 60]}, text, provenance=provenance))


class WrittenUnitTests(unittest.TestCase):
    def test_word_names_and_plurals_are_case_insensitive(self):
        cases = [("voltage", "3 Volts", 3, "V"), ("voltage", "1200 MILLIVOLTS", 1.2, "V"),
                 ("net_weight", "80 grams", 80, "g"), ("net_weight", "1.25 Kilograms", 1250, "g"),
                 ("power", "6 watts", 6, "W"), ("power", "2 Kilowatts", 2000, "W"),
                 ("current", "750 Milliamperes", .75, "A"), ("current", "2 AMPS", 2, "A"),
                 ("length", "2 Metres", 2000, "mm"), ("length", "3 Inches", 76.2, "mm"),
                 ("capacity_volume", "1.5 Liters", 1500, "mL"),
                 ("runtime", "2 Hours", 7200, "s"), ("charging_time", "30 Minutes", 1800, "s")]
        for field, raw, value, unit in cases:
            with self.subTest(raw=raw):
                result = normalize_value(field, raw)
                self.assertEqual((result.value, result.unit, result.notes), (value, unit, ()))

    def test_symbols_remain_case_sensitive(self):
        for field, small, large, values in (
            ("power", "2mW", "2MW", (.002, 2000000)),
            ("current", "2mA", "2MA", (.002, 2000000)),
            ("voltage", "2mV", "2MV", (.002, 2000000)),
            ("frequency", "2mHz", "2MHz", (.002, 2000000))):
            with self.subTest(field=field):
                self.assertEqual((normalize_value(field, small).value, normalize_value(field, large).value), values)
        self.assertIsNone(normalize_value("frequency", "2MHZ").unit)

    def test_word_boundaries_and_unknown_units_are_not_guessed(self):
        for field, raw in (("voltage", "3 Voltseconds"), ("net_weight", "80 gramophone"),
                           ("net_weight", "80 grams/m2"), ("net_weight", "80 grams per item"),
                           ("power", "6 watts / 9 watts"), ("net_weight", "80 grams ± 5 stones")):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_value(field, raw).unit)

    def test_ranges_bounds_and_tolerances_keep_their_meaning(self):
        for field, raw, value, unit in (
            ("voltage", "3 Volts to 5000 millivolts", [3, 5], "V"),
            ("net_weight", "0.3 Kilograms +/- 5 GRAMS", "300±5", "g"),
            ("net_weight", "at least 0.3 Kilograms", "≥300", "g"),
            ("power", "2 Milliwatts", .002, "W"), ("power", "2 Megawatts", 2000000, "W")):
            with self.subTest(raw=raw):
                result = normalize_value(field, raw)
                self.assertEqual((result.value, result.unit), (value, unit))
        self.assertEqual(normalize_value("dimensions", "2 x 3 Centimetres").value, [20, 30])

    def test_electrical_scope_and_header_paths_share_word_units(self):
        for text, expected in (
            ("Nominal Voltage: 3 Volts", [("voltage", 3, "V", "rating:nominal")]),
            ("Input: 5 Volts / 2 Amps", [("voltage", 5, "V", "input"), ("current", 2, "A", "input")]),
            ("Rated Power (Watts): 6", [("power", 6, "W", "rating:rated")]),
            ("Output: 3 Volts +/- 20 Millivolts", [("voltage", "3±0.02", "V", "output")])):
            with self.subTest(text=text):
                rows = extract(text, atomic_parameter=True)
                self.assertEqual([(c.field, c.normalized_value, c.normalized_unit, c.scope) for c in rows], expected)
                self.assertEqual({g.fact_status for g in build_graph(rows)[1]}, {"verified"})

    def test_registry_rejects_invalid_word_aliases(self):
        path = Path(__file__).resolve().parents[1] / "product_evidence_guard/field_registry.json"
        for aliases in ({"volt": "missing"}, {"V": "V"}, {"volts": "volt", "volt": "V"}, {"volts": 1}):
            with self.subTest(aliases=aliases):
                data = json.loads(path.read_text("utf-8"))
                data["unit_families"]["voltage"]["word_aliases"] = aliases
                with patch("product_evidence_guard.field_registry.files") as resource:
                    resource.return_value.joinpath.return_value.read_bytes.return_value = json.dumps(data).encode()
                    with self.assertRaises(FieldRegistryError):
                        _load_registry()


class ValueFirstParameterTests(unittest.TestCase):
    def test_acronyms_are_open_fields_not_a_three_field_allowlist(self):
        for text, label, value in (("384 kB ROM", "rom", "384kB"), ("512 kB SRAM", "sram", "512kB"),
                                  ("21 GPIO", "gpio", "21"), ("• 16 DMA_CHANNELS", "dma_channels", "16"),
                                  ("128 KiB CACHE", "cache", "128KiB"), ("4 UART", "uart", "4")):
            for unbound in (False, True):
                with self.subTest(text=text, unbound=unbound):
                    rows = extract(text, **({"layout_binding": "unbound_text"} if unbound else {}))
                    self.assertEqual([(c.field_label, c.normalized_value) for c in rows], [(label, value)])
                    row = rows[0]
                    self.assertEqual(row.raw_text, text)
                    self.assertEqual(row.provenance["parameter_order"], "value_first")
                    self.assertEqual(row.locator["page"], 1)
                    self.assertEqual(row.source_block_id, "b")
                    self.assertEqual(build_graph(rows)[1][0].fact_status, "verified")

    def test_known_labels_use_existing_normalization_and_scope(self):
        for text, expected in (("80 grams Net Weight", ("net_weight", 80, "g", "net")),
                               ("3 Volts Nominal Voltage", ("voltage", 3, "V", "rating:nominal")),
                               ("12 V 输出电压", ("voltage", 12, "V", "output"))):
            with self.subTest(text=text):
                rows = extract(text, layout_binding="unbound_text")
                self.assertEqual([(c.field, c.normalized_value, c.normalized_unit, c.scope) for c in rows], [expected])

    def test_prose_identity_units_and_multiple_values_are_not_parameters(self):
        for text in ("There are 21 GPIO available", "384 kB ROM is optional", "384 kB ROM (max)",
                     "3 Volts required", "21 errors", "2026 MODEL", "123 SKU", "21 GPIO: optional",
                     "384 kB ROM 512 kB SRAM", "21 GPIO / 14 UART", "21 MW", "21 WATTS", "384 kB",
                     "3 unknownunits ROM", "1.5 GPIO", "-2 GPIO", "without interruption: improved design"):
            with self.subTest(text=text):
                self.assertFalse(extract(text, layout_binding="unbound_text"))

    def test_opaque_unit_case_is_not_lost(self):
        for text, expected in (("384 kB ROM", "384kB"), ("384 kb ROM", "384kb"),
                               ("2 mW AUX", "2mW"), ("2 MW AUX", "2MW")):
            with self.subTest(text=text):
                self.assertEqual(extract(text)[0].normalized_value, expected)

    def test_explicit_parent_is_not_split_into_child_facts(self):
        rows = extract("Peripherals: 21 GPIO; 4 UART", atomic_parameter=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].field_label, "peripherals")
        self.assertIn("GPIO", rows[0].normalized_value)
        self.assertNotIn("parameter_order", rows[0].provenance)

    def test_source_conditions_remain_attached(self):
        note = {"marker": "1", "text": "Only in test mode", "locator": {"page": 2}}
        row = extract("21 GPIO", layout_binding="unbound_text", source_conditions=[note])[0]
        self.assertIn("Only in test mode", row.raw_text)
        self.assertIn("only in test mode", row.normalized_value)
        self.assertIn("context:only in test mode", row.scope)

    def test_full_pipeline_equivalent_and_conflicting_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            inputs, output = Path(temp) / "input", Path(temp) / "output"
            inputs.mkdir()
            (inputs / "a.txt").write_text("SKU: SIMPLE-21\nNet Weight: 80 GRAMS\n384 kB ROM\n21 GPIO", encoding="utf-8")
            (inputs / "b.txt").write_text("SKU: SIMPLE-21\n净重: 0.08kg\nROM: 384kB\nGPIO: 22", encoding="utf-8")
            summary = analyze_directory(inputs, output, preprocessing_workers=1)
            data = json.loads((output / "product-facts.json").read_text("utf-8"))
            self.assertFalse(summary["errors"])
            groups = [g for g in data["facts"] if g["field"] != "sku"]
            self.assertEqual(len(groups), 3)
            self.assertEqual({g["field_label"]: g["fact_status"] for g in groups},
                             {"净重": "verified", "rom": "verified", "gpio": "conflict"})
            self.assertTrue(all(g["independent_source_count"] == 2 for g in groups))


if __name__ == "__main__":
    unittest.main()
