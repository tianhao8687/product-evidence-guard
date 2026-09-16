"""Corpus regression plus independent scoring/routing negative controls.

Metamorphic variants below are NOT counted as new business packages.
"""
from copy import deepcopy
import importlib.util
import hashlib
from itertools import permutations
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_corpus as corpus
from evaluate_scenarios import run_case, score
from evaluation_metrics import equal_value
from product_evidence_guard.extractor import parameter_segments
from product_evidence_guard.field_registry import split_label_unit, field_for_label
from product_evidence_guard.normalization import normalize_value, values_equal
from product_evidence_guard.parsers import parse_json

FIXTURE = ROOT / "tests/fixtures/cross_domain_100.json"
DATA = json.loads(FIXTURE.read_text("utf-8"))


class CorpusContractTests(unittest.TestCase):
    def test_hundred_distinct_packages_ten_domains_and_honest_provenance(self):
        cases = corpus.validate(DATA)
        self.assertEqual(len(cases), 100)
        self.assertEqual(len({c["domain"] for c in cases}), 10)
        self.assertEqual(sum(c["source_kind"] == "public_adapted" for c in cases), 20)
        self.assertEqual(sum(c["source_kind"] == "synthetic_stress" for c in cases), 80)
        self.assertEqual(sum(c["split"] == "holdout" for c in cases), 20)
        self.assertTrue(all(c["provenance"] for c in cases))

    def test_readable_curation_and_frozen_fixture_agree(self):
        spec = importlib.util.spec_from_file_location("corpus_gold", FIXTURE.with_suffix(".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.dataset(), DATA)

    def test_empty_duplicate_or_unsafe_corpus_is_not_a_passing_run(self):
        for data in ({"cases":[]}, {**DATA,"cases":[DATA["cases"][0]]*2},
                     {**DATA,"cases":[{**DATA["cases"][0],"id":"../escape"}]}):
            with self.assertRaises(ValueError):
                corpus.validate(data)

    def test_float_tolerance_never_hides_microamp_errors_or_type_errors(self):
        good = [{"model":"M", "variant":"A"}, "current", "input", "verified", [[.000020,"A"]]]
        noisy = deepcopy(good); noisy[4][0][0] += 1e-16
        self.assertTrue(score([good],[noisy])["passed"])
        for value in (.000021, .000019, 20, True, ".000020"):
            bad = deepcopy(good); bad[4][0][0] = value
            self.assertFalse(score([good],[bad])["passed"], value)
        self.assertFalse(equal_value(float("nan"),float("nan")))

    def test_product_equality_preserves_small_real_differences_and_range_noise(self):
        # Extra safety regressions, not additional members of the 100 packages.
        self.assertFalse(values_equal(.000020, .0000205))
        self.assertFalse(values_equal(True,1))
        self.assertFalse(values_equal(float("inf"),float("inf")))
        self.assertTrue(values_equal([0,40],[-8e-16,40.00000000000001]))
        self.assertFalse(values_equal([1,2],[2,1]))
        case = {"id":"micro-difference", "family":"numeric_equality",
                "files":{"a.txt":"SKU: MICRO\n额定电流: 20uA", "b.txt":"SKU: MICRO\n额定电流: 20.5uA"},
                "facts":[["MICRO","sku",None,"verified",[["micro",None]]],
                         ["MICRO","current","rating:rated","conflict",[[.000020,"A"],[.0000205,"A"]]]]}
        self.assertTrue(run_case(case)["passed"])

    def test_custom_expression_spacing_does_not_create_a_pdf_ocr_conflict(self):
        case = {"id":"custom-spacing", "family":"native_ocr_typography",
                "files":{"a.txt":"SKU: HUMIDITY\nOperating humidity: 10% ~ 90%, non-condensing",
                         "b.txt":"SKU: HUMIDITY\nOperating humidity: 10%~90%, non-condensing"},
                "facts":[["HUMIDITY","sku",None,"verified",[["humidity",None]]],
                         ["HUMIDITY","custom:operating humidity",None,"verified",[["10%~90%, non-condensing",None]]]]}
        self.assertTrue(run_case(case)["passed"])
        spec = field_for_label("工作湿度").name
        expected = normalize_value(spec,"10% ~ 90%, non-condensing").value
        for text in ("10%~91%, non-condensing","10%~90%, condensing","10%±90%, non-condensing"):
            self.assertNotEqual(expected,normalize_value(spec,text).value)

    def test_model_only_ownership_and_variant_are_graded(self):
        good = [{"model":"M", "variant":"red"}, "net_weight", "net", "verified", [[10,"g"]]]
        for who in ({"model":"N","variant":"red"},{"model":"M","variant":"blue"}):
            bad = deepcopy(good); bad[0]=who
            self.assertFalse(score([good],[bad])["passed"])

    def test_native_only_package_hash_and_path_are_checked(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "original.txt"
            source.write_text("SKU: NATIVE\n净重: 50g", encoding="utf8")
            case = {"id":"native", "family":"native_contract",
                    "native_files":[{"path":source.name, "sha256":hashlib.sha256(source.read_bytes()).hexdigest()}],
                    "facts":[["NATIVE","sku",None,"verified",[["native",None]]],
                             ["NATIVE","net_weight","net","verified",[[50,"g"]]]]}
            self.assertTrue(run_case(case,native_root=root)["passed"])
            source.write_text("SKU: NATIVE\n净重: 60g",encoding="utf8")
            with self.assertRaisesRegex(ValueError,"hash changed"):
                run_case(case,native_root=root)
            case["native_files"][0]["path"] = "../outside.txt"
            with self.assertRaisesRegex(ValueError,"escapes"):
                run_case(case,native_root=root)

    def test_corpus_checkpoint_reuses_shared_atomic_writer_with_bounded_retry(self):
        with patch.object(corpus,"atomic_write_json", side_effect=[PermissionError(),None]) as write, patch.object(corpus.time,"sleep"):
            corpus.atomic_json(Path("unused.json"),{})
            self.assertEqual(write.call_count,2)
        with patch.object(corpus,"atomic_write_json",side_effect=PermissionError()),patch.object(corpus.time,"sleep"):
            with self.assertRaises(PermissionError):
                corpus.atomic_json(Path("unused.json"),{})

    def test_timeout_and_invalid_worker_output_are_explicit_failures(self):
        c = DATA["cases"][0]
        for behavior in (subprocess.TimeoutExpired("test",1),subprocess.CompletedProcess([],0,"not-json","")):
            with patch.object(corpus.subprocess,"run", side_effect=behavior if isinstance(behavior,Exception) else None,
                              return_value=behavior if not isinstance(behavior,Exception) else None):
                result=corpus.run_isolated(c,FIXTURE,ROOT,timeout=1,native_root=None)
                self.assertFalse(result["passed"])
                self.assertTrue(result["error"])

    def test_resume_is_bound_to_gold_and_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); fixture=root/"fixture.json"
            c=DATA["cases"][0]
            fixture.write_text(json.dumps({**DATA,"cases":[c]}),encoding="utf8")
            with patch.object(corpus,"run_isolated",return_value={"id":c["id"],"passed":True}),patch.object(corpus,"code_hash",return_value="engine-one"):
                corpus.run(fixture,root/"out",workers=1)
                corpus.run(fixture,root/"out",workers=1,resume=True)
            with patch.object(corpus,"code_hash",return_value="engine-two"):
                with self.assertRaisesRegex(ValueError,"Cannot resume"):
                    corpus.run(fixture,root/"out",resume=True)

    def test_json_identity_does_not_depend_on_key_order(self):
        # Previously the flattened SKU heading assigned all later values;
        # a parent field after a child was silently bound to the child.
        case=deepcopy(next(c for c in DATA["cases"] if c["id"]=="08-03"))
        original=json.loads(case["files"]["spec.json"])
        for keys in permutations(original):
            case["files"]={"spec.json":json.dumps({k:original[k] for k in keys},ensure_ascii=False)}
            result=run_case(case)
            self.assertTrue(result["passed"],result)

    def test_json_rejects_nonfinite_and_duplicate_keys_instead_of_certifying_last(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"source.json"
            for content in ('{"SKU":"A","SKU":"B"}', '{"weight":NaN}', '{"weight":1e309}',
                            '{"weight":{"value":1e309,"unit":"g"}}','{"readings":[1e309]}'):
                path.write_text(content,encoding="utf8")
                with self.assertRaises(ValueError):
                    parse_json(path,"source.json","hash")

    def test_header_units_and_conditions_share_one_non_destructive_rule(self):
        for label,answer in (("重量(kg)",("重量","kg")),("输入电压[V]",("输入电压","V")),
                             ("噪声[dB(A)]",("噪声","dB(A)")),("功率(STC)",("功率(STC)",None)),
                             ("电池容量(25°C)",("电池容量(25°C)",None)),("重量(含电池)",("重量(含电池)",None))):
            self.assertEqual(split_label_unit(label),answer)

    def test_numeric_conditions_are_not_numbered_prose_or_document_metadata(self):
        self.assertIsNotNone(field_for_label("25°C容量"))
        for label in ("3. Tolerance","230VAC","file name","please refer to https","electrical data"):
            self.assertIsNone(field_for_label(label),label)

    def test_json_conflicting_identity_aliases_are_explicitly_ambiguous(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"source.json"
            path.write_text('{"SKU":"A","货号":"B","净重":"50g"}',encoding="utf8")
            blocks=parse_json(path,"source.json","hash")
            self.assertTrue(all(b.provenance["structured_row"]["identity"].get("_ambiguous_identity") for b in blocks))

    def test_segmentation_preserves_disclaimers_and_corrections_at_every_entry(self):
        self.assertEqual(parameter_segments("仅为模板，净重: 10g"),[])
        text="净重不是300g，正确为净重: 250g\n额定功率: 60W"
        segments=parameter_segments(text)
        self.assertEqual(len(segments),2)
        self.assertEqual([part for line in segments for part in parameter_segments(line)],segments)

    def test_scientific_notation_is_general_and_bounded(self):
        for field,raw,expected,unit in (("net_weight","1e3g",1000,"g"),("current","2e-3A",.002,"A"),
                                        ("voltage",".5V",.5,"V"),("pressure","2E2kPa",200000,"Pa"),
                                        ("current","20µA",.00002,"A"),("current","20μA",.00002,"A")):
            result=normalize_value(field,raw)
            self.assertEqual((result.value,result.unit),(expected,unit))
        for raw in ("1e999999g","1e-999999g","2e-3g/m2","1e3g / 2e3g"):
            self.assertIsNone(normalize_value("net_weight",raw).unit,raw)


class CrossDomainRegressionTests(unittest.TestCase):
    pass


for case in DATA["cases"]:
    def test(self,case=case):
        result=run_case(case)
        self.assertTrue(result["passed"],result)
    setattr(CrossDomainRegressionTests,"test_package_"+case["id"].replace("-","_"),test)
