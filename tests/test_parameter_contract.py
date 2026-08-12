import importlib.util
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from batch_import import (
    CementedLensData,
    SingleLensData,
    export_batch_excel,
    read_excel,
)
from main import _build_lens_page_context
from settings import DEFAULT_SETTINGS
from web_app import app, batch_export_data_list


def single_item(name="Lens-N15", number="100.1"):
    lens = SingleLensData("G1", 4.7, -35, 30, 19, 15, 13)
    return CementedLensData(name, number, [lens])


class ParameterContractTests(unittest.TestCase):
    def test_row_override_outvotes_global_settings_at_batch_export_boundary(self):
        item = single_item()
        item.proc_overrides = {
            "N_mode": "manual",
            "N_manual": "1.5",
            "proc_vendor": "OVERRIDE-VENDOR",
            "proc_b": "20/10",
            "DN": "0.15",
            "signature": "tester",
            "coat_preset": "Custom",
            "coat_s1_wave1": "511-522",
            "CA_mode": "manual",
            "CA1": "14",
            "CA2": "12",
            "chamfer_mode": "manual",
            "chamfer_left": "0.11",
            "chamfer_right": "0.22",
            "t_tol": "0.013",
        }
        item.page_overrides = {"1": {"proc_N_manual": "1.4"}}
        settings = DEFAULT_SETTINGS.copy()
        settings["proc_N_mode"] = "auto"
        settings["proc_vendor"] = "GLOBAL-VENDOR"

        with tempfile.TemporaryDirectory() as output_dir:
            with patch("web_app.export_cemented_pdf") as exporter:
                result = batch_export_data_list([item], output_dir, settings)

        self.assertEqual(result["errors"], [])
        self.assertEqual(exporter.call_count, 2)
        effective = exporter.call_args_list[0].args[1]
        self.assertEqual(effective["proc_N_mode"], "manual")
        self.assertEqual(effective["proc_N_manual"], 1.5)
        self.assertEqual(effective["proc_vendor"], "OVERRIDE-VENDOR")
        self.assertEqual(effective["proc_surface_defect"], "20/10")
        self.assertEqual(effective["proc_DN"], "0.15")
        self.assertEqual(effective["proc_signature"], "tester")
        self.assertEqual(effective["coat_s1_wave1"], "511-522")
        self.assertEqual(effective["chamfer_left"], 0.11)
        self.assertEqual(effective["t_tol"], 0.013)
        self.assertNotIn("N_mode", effective)
        self.assertEqual(
            exporter.call_args_list[0].kwargs["page_overrides"],
            {"1": {"proc_N_manual": 1.4}},
        )

    def test_page_override_has_highest_priority_for_final_lens_page(self):
        item = single_item()
        settings = DEFAULT_SETTINGS.copy()
        settings.update({
            "proc_N_mode": "manual",
            "proc_N_manual": "1.5",
            "proc_vendor": "ROW-VENDOR",
            "coat_preset": "Custom",
            "coat_s1_wave1": "511-522",
        })
        context = _build_lens_page_context(
            item,
            settings,
            {
                "1": {
                    "proc_N_manual": 1.4,
                    "proc_vendor": "PAGE-VENDOR",
                    "coat_s1_wave1": "600-610",
                }
            },
            0,
        )
        self.assertEqual(context["proc_params"]["proc_N_manual"], 1.4)
        self.assertEqual(context["proc_params"]["proc_vendor"], "PAGE-VENDOR")
        self.assertEqual(context["proc_params"]["coat_s1_wave1"], "600-610")

    def test_single_preview_fields_match_rendered_custom_values(self):
        payload = {
            "T": 4.7,
            "R1": -35,
            "R2": 30,
            "MD": 19,
            "AD1": 15,
            "AD2": 13,
            "N_mode": "manual",
            "N_manual": "1.5",
            "proc_vendor": "PREVIEW-VENDOR",
            "proc_b": "20/10",
            "DN": "0.15",
            "signature": "tester",
        }
        with app.test_client() as client:
            data = client.post("/api/preview", json=payload).get_json()

        self.assertTrue(data["success"], data.get("error"))
        values = {field["id"]: field["value"] for field in data["fields"]}
        self.assertEqual(values["n_val"], "1.5")
        self.assertEqual(values["vendor"], "PREVIEW-VENDOR")
        self.assertEqual(values["b_val"], "20/10")
        self.assertEqual(values["dn_val"], "0.15")
        self.assertEqual(values["signature"], "tester")

    def test_excel_roundtrip_preserves_flat_radius_and_custom_proc(self):
        row = {
            "part_name": "flat",
            "part_no": "100.2",
            "glass1": "G1",
            "T1": 4.7,
            "R1": 0,
            "R2": 30,
            "MD1": 19,
            "AD1": 15,
            "AD2": 13,
            "save_pdf_folder": "Save PDF",
            "mfr_pdf_folder": "Mfr PDF",
            "custom_proc": {
                "proc_N_mode": "manual",
                "proc_N_manual": "1.5",
                "proc_vendor": "ROUNDTRIP",
                "page_overrides": {
                    "1": {"proc_surface_defect": "20/10"}
                },
            },
        }
        with tempfile.TemporaryDirectory() as output_dir:
            path = os.path.join(output_dir, "roundtrip.xlsx")
            export_batch_excel(path, [row])
            items, warnings = read_excel(path)

        self.assertEqual(warnings, [])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].lenses[0].R_left, 0)
        self.assertEqual(len(items[0].lenses), 1)
        self.assertEqual(items[0].proc_overrides["proc_N_manual"], "1.5")
        self.assertEqual(items[0].proc_overrides["proc_vendor"], "ROUNDTRIP")
        self.assertEqual(
            items[0].page_overrides["1"]["proc_surface_defect"], "20/10"
        )

    def test_batch_parse_api_keeps_zero_and_marks_missing_lenses_empty(self):
        row = {
            "part_name": "flat",
            "part_no": "100.2",
            "glass1": "G1",
            "T1": 4.7,
            "R1": 0,
            "R2": 30,
            "MD1": 19,
            "AD1": 15,
            "AD2": 13,
        }
        with tempfile.TemporaryDirectory() as output_dir:
            path = os.path.join(output_dir, "flat.xlsx")
            export_batch_excel(path, [row])
            with open(path, "rb") as workbook, app.test_client() as client:
                data = client.post(
                    "/api/batch/parse",
                    data={"file": (io.BytesIO(workbook.read()), "flat.xlsx")},
                    content_type="multipart/form-data",
                ).get_json()

        self.assertTrue(data["success"], data.get("error"))
        parsed = data["data"][0]
        self.assertEqual(parsed["R1"], 0)
        self.assertEqual(parsed["T2"], "")
        self.assertEqual(parsed["R3"], "")
        self.assertEqual(parsed["AD3"], "")

    @unittest.skipUnless(
        importlib.util.find_spec("pdfplumber"), "pdfplumber is not installed"
    )
    def test_actual_batch_pdf_contains_custom_n_vendor_and_coating(self):
        import pdfplumber

        item = single_item()
        item.proc_overrides = {
            "proc_N_mode": "manual",
            "proc_N_manual": "1.5",
            "proc_vendor": "OVERRIDE-VENDOR",
            "coat_preset": "Custom",
            "coat_s1_wave1": "511-522",
        }
        settings = DEFAULT_SETTINGS.copy()
        settings["proc_N_mode"] = "auto"

        with tempfile.TemporaryDirectory() as output_dir:
            result = batch_export_data_list([item], output_dir, settings)
            path = os.path.join(output_dir, "Save PDF", "Lens-N15.pdf")
            with pdfplumber.open(path) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)

        self.assertEqual(result["errors"], [])
        self.assertIn("N 1.5", text)
        self.assertIn("OVERRIDE-VENDOR", text)
        self.assertIn("511-522", text)


if __name__ == "__main__":
    unittest.main()
