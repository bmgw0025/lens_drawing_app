import unittest

from autodraw.mapper import map_to_drafts
from autodraw.renderer_adapter import DEFAULT_RENDERER_ROOT, _build_render_inputs
from tests.test_autodraw_mapper import _virtual_surface_triplet


class RendererAdapterGeometryTests(unittest.TestCase):
    def test_renderer_uses_authoritative_side_specific_ad_values(self):
        system = _virtual_surface_triplet()
        system.surfaces[3].semi_diameter = 9.0
        system.surfaces[4].semi_diameter = 8.5
        draft = map_to_drafts(system)[0]

        _, drawing, _, _ = _build_render_inputs(
            draft,
            DEFAULT_RENDERER_ROOT,
            None,
        )

        self.assertIsNone(draft.row["AD2"])
        self.assertEqual(drawing.lenses[0].AD_right, 18.0)
        self.assertEqual(drawing.lenses[1].AD_left, 17.0)


if __name__ == "__main__":
    unittest.main()
