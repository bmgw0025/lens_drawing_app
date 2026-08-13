import unittest

import web_app
from settings import validate_settings_updates


class SettingsValidationTests(unittest.TestCase):
    def test_null_numeric_setting_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "J_multiplier 必须是数值"):
            validate_settings_updates({"J_multiplier": None})

    def test_drawing_scale_must_be_positive(self):
        with self.assertRaisesRegex(ValueError, "J_multiplier 必须大于 0"):
            validate_settings_updates({"J_multiplier": 0})

    def test_ca_ratio_cannot_exceed_one(self):
        with self.assertRaisesRegex(ValueError, "ca_ratio 不能大于 1"):
            validate_settings_updates({"ca_ratio": 1.01})

    def test_settings_api_does_not_persist_invalid_value(self):
        before = web_app._current_settings["J_multiplier"]
        with web_app.app.test_client() as client:
            data = client.post(
                "/api/settings", json={"J_multiplier": None}
            ).get_json()
        self.assertFalse(data["success"])
        self.assertEqual(web_app._current_settings["J_multiplier"], before)

    def test_legacy_sq_a3_setting_migrates_to_sq_a6(self):
        normalized = validate_settings_updates({"coat_preset": "SQ-A3"})
        self.assertEqual(normalized["coat_preset"], "SQ-A6")

    def test_custom_ranking_and_molding_are_accepted(self):
        normalized = validate_settings_updates({
            "proc_ranking": "CUSTOM GRADE",
            "proc_molding": "Scribe & Break",
        })
        self.assertEqual(normalized["proc_ranking"], "CUSTOM GRADE")
        self.assertEqual(normalized["proc_molding"], "Scribe & Break")


if __name__ == "__main__":
    unittest.main()
