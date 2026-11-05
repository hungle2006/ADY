import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

from silver_v3.safe_tools.dav_normalizer_v3 import (
    ingredient_search_fields, normalize, normalize_component_strength, split_top_level,
)


class DAVIngredientNormalizationTests(unittest.TestCase):
    def strength(self, value):
        return normalize_component_strength(value, value, "A", 1, 0, "Viên nén", "TEST")

    def test_spaced_plus_preserves_parentheses_decimal_and_ionic_charge(self):
        self.assertEqual(split_top_level("A 0,5 mg + B (muối X + Y) 125 mg"),
                         ["A 0,5 mg", "B (muối X + Y) 125 mg"])
        self.assertEqual(split_top_level("Na+"), ["Na+"])

    def test_newline_split_requires_strength_on_both_lines(self):
        self.assertEqual(split_top_level("A 500mg\nB 125mg"), ["A 500mg", "B 125mg"])
        self.assertEqual(split_top_level("A\n(dưới dạng muối A)"), ["A (dưới dạng muối A)"])

    def test_mass_unit_equivalence_for_each(self):
        self.assertEqual(self.strength("0,5 g")["strength_normalized"], "500 MG/EACH")
        self.assertEqual(self.strength("500 mg")["strength_normalized"], "500 MG/EACH")

    def test_concentration_unit_conversion(self):
        self.assertEqual(self.strength("1 g/l")["strength_normalized"], "1 MG/ML")
        self.assertEqual(self.strength("500 mcg/5ml")["strength_normalized"], "0.1 MG/ML")

    def test_multiple_strengths_never_choose_last(self):
        result = self.strength("A 500mg B 125mg")
        self.assertEqual(result["strength_parse_status"], "REVIEW_MULTIPLE_STRENGTHS")
        self.assertEqual(result["strength_compare_value"], "")

    def test_zero_denominator_is_review(self):
        self.assertEqual(self.strength("5mg/0ml")["strength_parse_status"],
                         "REVIEW_NONPOSITIVE_DENOMINATOR")

    def test_parenthetical_salt_strength_is_not_assumed_active_strength(self):
        self.assertTrue(self.strength("A (dưới dạng muối A 550mg)")["strength_parse_status"].startswith("REVIEW"))

    def test_search_key_is_only_lexical(self):
        result = ingredient_search_fields("Celecoxib 200mg")
        self.assertEqual(result["ingredient_key_for_search"], "celecoxib")
        self.assertEqual(result["ingredient_name_parse_status"], "LEXICAL_ONLY")
        salt = ingredient_search_fields("A (dưới dạng muối A) 500mg")
        self.assertEqual(salt["ingredient_name_parse_status"], "COMPLEX_SOURCE_TEXT_REVIEW")

    def test_end_to_end_preserves_price_and_aligns_two_components(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = dict(dav_row_id="1", dav_drug_name="Example", dav_active_ingredient_raw="A 500mg + B 125mg",
                       dav_strength="500mg + 125mg", dav_dosage_form="Viên nén", dav_price_vnd="10000")
            with (root / "input.csv").open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=list(row)); writer.writeheader(); writer.writerow(row)
            with contextlib.redirect_stdout(io.StringIO()):
                normalize(root / "input.csv", root / "out")
            with (root / "out/dav_component_normalized_v2.csv").open(encoding="utf-8-sig", newline="") as f:
                components = list(csv.DictReader(f))
            self.assertEqual([r["strength_normalized"] for r in components], ["500 MG/EACH", "125 MG/EACH"])
            self.assertTrue(all(r["mapping_readiness_v2"] == "REVIEW_BEFORE_RXNORM_MAPPING" for r in components))
            with (root / "out/dav_product_normalized_v2.csv").open(encoding="utf-8-sig", newline="") as f:
                product = next(csv.DictReader(f))
            self.assertEqual(product["dav_price_vnd"], "10000")
            self.assertEqual(product["component_count"], "2")

    def test_local_utf16_tab_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (root / "dav.csv").open("w", encoding="utf-16", newline="") as f:
                writer = csv.writer(f, delimiter="\t")
                writer.writerow(["dav_row_id", "dav_active_ingredient_raw", "dav_strength", "dav_dosage_form"])
                writer.writerow(["1", "A", "0,5 g", "Viên nén"])
            with contextlib.redirect_stdout(io.StringIO()):
                normalize(root / "dav.csv", root / "out")
            with (root / "out/dav_component_normalized_v2.csv").open(encoding="utf-8-sig", newline="") as f:
                self.assertEqual(next(csv.DictReader(f))["strength_normalized"], "500 MG/EACH")

    def test_invalid_schema_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "bad.csv").write_text("wrong\nvalue\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                normalize(root / "bad.csv", root / "out")


if __name__ == "__main__":
    unittest.main()
