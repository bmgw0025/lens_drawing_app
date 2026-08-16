import unittest

from sync_agent_assets import check_assets


class AgentAssetSyncTests(unittest.TestCase):
    def test_generated_spec_skill_reference_and_manifest_are_current(self):
        self.assertEqual(check_assets(), [])


if __name__ == "__main__":
    unittest.main()
