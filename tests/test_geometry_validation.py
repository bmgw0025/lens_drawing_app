import math
import os
import tempfile
import unittest

from batch_import import CementedLensData, SingleLensData, parse_row
from config import validate, validate_cemented_lenses
from main import export_cemented_pdf
from settings import DEFAULT_SETTINGS
from web_app import app, _cemented_data_from_row_dict


def split_triplet():
    return [
        SingleLensData("G1", 4, -30, 25, 30, 24, 22),
        SingleLensData("G2", 5, 25, -40, 24, 20, 18),
        SingleLensData("G3", 2, -40, 50, 18, 16, 14),
    ]


class GeometryValidationTests(unittest.TestCase):
    def test_split_interface_apertures_are_valid(self):
        self.assertEqual(validate_cemented_lenses(split_triplet()), [])

    def test_non_finite_values_are_rejected(self):
        errors = validate(4.7, -35, 30, math.nan, 15, 13)
        self.assertTrue(any("有限数值" in error for error in errors))

    def test_reversed_edge_thickness_is_rejected(self):
        errors = validate(1, 10, -10, 20, 18, 18)
        self.assertTrue(any("边缘厚度" in error for error in errors))

    def test_cemented_radius_discontinuity_is_rejected(self):
        lenses = split_triplet()[:2]
        lenses[1].R_left = 26
        errors = validate_cemented_lenses(lenses)
        self.assertTrue(any("曲率不连续" in error for error in errors))

    def test_batch_rows_cannot_skip_lens_two(self):
        row = {
            "PartName": "bad-order", "PartNo": "1", "Glass1": "G1",
            "T1": 4, "R1": -30, "R2": 25, "MD1": 30,
            "AD1": 24, "AD2": 22,
            "Glass3": "G3", "T3": 2, "R4": 50, "MD3": 18, "AD4": 14,
        }
        with self.assertRaisesRegex(ValueError, "镜片3已有数据"):
            parse_row(row)

    def test_editor_row_cannot_silently_drop_partial_lens_two(self):
        row = {
            "part_name": "partial", "part_no": "1", "glass1": "G1",
            "T1": 4, "R1": -30, "R2": 25, "MD1": 30,
            "AD1": 24, "AD2": 22,
            "glass2": "G2", "R3": -40, "MD2": 24, "AD3": 18,
        }
        with self.assertRaisesRegex(ValueError, "镜片2 T2不能为空"):
            _cemented_data_from_row_dict(row)

    def test_cemented_preview_reports_engineering_errors(self):
        payload = {
            "lenses": [
                {"T": 4, "R_left": -30, "R_right": 25,
                 "MD": 20, "AD_left": 24, "AD_right": 18},
                {"T": 5, "R_left": 26, "R_right": -40,
                 "MD": 20, "AD_left": 18, "AD_right": 16},
            ]
        }
        with app.test_client() as client:
            data = client.post("/api/preview/cemented", json=payload).get_json()
        self.assertFalse(data["success"])
        self.assertIn("镜片1: 左侧 AD 不能大于 MD", data["error"])
        self.assertIn("曲率不连续", data["error"])

    def test_valid_split_triplet_builds_all_preview_pages(self):
        payload = {
            "lenses": [
                {
                    "glass": lens.glass,
                    "T": lens.T,
                    "R_left": lens.R_left,
                    "R_right": lens.R_right,
                    "MD": lens.MD,
                    "AD_left": lens.AD_left,
                    "AD_right": lens.AD_right,
                }
                for lens in split_triplet()
            ],
            "part_name": "split-triplet",
            "part_no": "TEST",
            "cemented_ref_lens": 2,
            "ca_ratio": 0.94,
        }
        with app.test_client() as client:
            data = client.post("/api/preview/cemented", json=payload).get_json()
        self.assertTrue(data["success"], data.get("error"))
        self.assertEqual(data["labels"], ["整体", "镜片1", "镜片2", "镜片3"])
        self.assertEqual(len(data["images"]), 4)

    def test_invalid_page_override_fails_before_pdf_is_created(self):
        data = CementedLensData("preflight", "TEST", split_triplet())
        settings = DEFAULT_SETTINGS.copy()
        with tempfile.TemporaryDirectory() as output_dir:
            output_path = os.path.join(output_dir, "should-not-exist.pdf")
            with self.assertRaisesRegex(ValueError, "必须填写镜片1右侧 CA"):
                export_cemented_pdf(
                    data,
                    settings,
                    output_path,
                    page_overrides={
                        1: {
                            "ca_mode_1": "manual",
                            "ca_1_left": "10",
                            "ca_1_right": "",
                        }
                    },
                )
            self.assertFalse(os.path.exists(output_path))


if __name__ == "__main__":
    unittest.main()
