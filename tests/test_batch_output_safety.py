import tempfile
import unittest
from unittest.mock import patch

from batch_import import CementedLensData, SingleLensData
from settings import DEFAULT_SETTINGS
from web_app import app, batch_export_data_list


def single_item(name="Lens-A", number="100.1"):
    lens = SingleLensData("G1", 4.7, -35, 30, 19, 15, 13)
    return CementedLensData(name, number, [lens])


class BatchOutputSafetyTests(unittest.TestCase):
    def test_relative_folder_cannot_escape_output_directory(self):
        item = single_item()
        item.save_pdf_folder = "..\\outside"
        with tempfile.TemporaryDirectory() as output_dir:
            with patch("web_app.export_cemented_pdf") as exporter:
                result = batch_export_data_list([item], output_dir, DEFAULT_SETTINGS)
        exporter.assert_not_called()
        self.assertTrue(any("路径段" in error for error in result["errors"]))

    def test_windows_reserved_filename_is_rejected(self):
        item = single_item(name="CON")
        with tempfile.TemporaryDirectory() as output_dir:
            with patch("web_app.export_cemented_pdf") as exporter:
                result = batch_export_data_list([item], output_dir, DEFAULT_SETTINGS)
        exporter.assert_not_called()
        self.assertTrue(any("Windows 保留名称" in error for error in result["errors"]))

    def test_duplicate_output_names_do_not_overwrite(self):
        first = single_item()
        second = single_item()
        with tempfile.TemporaryDirectory() as output_dir:
            with patch("web_app.export_cemented_pdf") as exporter:
                result = batch_export_data_list(
                    [first, second], output_dir, DEFAULT_SETTINGS
                )
        self.assertEqual(exporter.call_count, 2)
        self.assertEqual(result["success_save"], 1)
        self.assertEqual(result["success_mfr"], 1)
        self.assertTrue(any("重复输出路径" in error for error in result["errors"]))

    def test_invalid_editor_row_does_not_abort_valid_rows(self):
        valid = {
            "part_name": "valid",
            "part_no": "100.1",
            "glass1": "G1",
            "T1": 4.7,
            "R1": -35,
            "R2": 30,
            "MD1": 19,
            "AD1": 15,
            "AD2": 13,
        }
        invalid = {**valid, "part_name": "invalid", "T1": ""}
        with tempfile.TemporaryDirectory() as output_dir:
            with patch("web_app.export_cemented_pdf") as exporter:
                with app.test_client() as client:
                    data = client.post(
                        "/api/batch/export",
                        json={"rows": [invalid, valid], "output_dir": output_dir},
                    ).get_json()

        self.assertTrue(data["success"])
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["failed_count"], 1)
        self.assertEqual(exporter.call_count, 2)
        self.assertTrue(any("第 1 行数据" in error for error in data["errors"]))


if __name__ == "__main__":
    unittest.main()
