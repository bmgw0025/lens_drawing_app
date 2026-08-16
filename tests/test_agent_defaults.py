import unittest
from unittest.mock import patch

from autodraw.spec import build_agent_spec
from settings import DEFAULT_SETTINGS, get_agent_default_settings


class AgentDefaultSettingsTests(unittest.TestCase):
    def test_agent_defaults_are_fresh_and_not_mutable_across_tasks(self):
        first = get_agent_default_settings()
        first["proc_surface_defect"] = "changed"
        second = get_agent_default_settings()

        self.assertEqual(
            second["proc_surface_defect"], DEFAULT_SETTINGS["proc_surface_defect"]
        )

    def test_spec_does_not_read_persisted_gui_settings(self):
        with patch(
            "settings.load_settings",
            return_value={"proc_surface_defect": "persisted-special-value"},
        ):
            spec = build_agent_spec()

        self.assertEqual(spec["process_defaults"]["proc_surface_defect"], "60/40")
        self.assertFalse(
            spec["process_default_policy"]["reads_persisted_gui_settings"]
        )


if __name__ == "__main__":
    unittest.main()
