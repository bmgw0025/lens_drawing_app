import unittest
from types import SimpleNamespace

from autodraw.naming import NamingError, resolve_naming_policy


def _draft(group_index):
    return SimpleNamespace(group_index=group_index)


class AutodrawNamingTests(unittest.TestCase):
    def test_production_sequence_maps_business_fields_and_preserves_width(self):
        resolved = resolve_naming_policy(
            [_draft(1), _draft(3)],
            {
                "mode": "production_sequence",
                "lens_model": "DTCA110-36",
                "lens_element_model": "A11036",
                "first_production_code": "105.2.00599",
                "element_sequence_start": 1,
                "evidence_ids": ["user-1"],
            },
        )

        self.assertEqual(resolved["1"]["SavePdfFolder"], "DTCA110-36")
        self.assertEqual(resolved["1"]["MfrPdfFolder"], "A11036")
        self.assertEqual(resolved["1"]["PartName"], "A11036-1")
        self.assertEqual(resolved["3"]["PartName"], "A11036-2")
        self.assertEqual(resolved["1"]["PartNo"], "105.2.00599")
        self.assertEqual(resolved["3"]["PartNo"], "105.2.00600")

    def test_multiple_groups_require_incrementable_production_code(self):
        with self.assertRaisesRegex(NamingError, "必须以数字结尾"):
            resolve_naming_policy(
                [_draft(1), _draft(2)],
                {
                    "mode": "production_sequence",
                    "lens_model": "LENS-1",
                    "lens_element_model": "ELEMENT",
                    "first_production_code": "NO-NUMBER",
                    "element_sequence_start": 1,
                    "evidence_ids": ["user-1"],
                },
            )


if __name__ == "__main__":
    unittest.main()
