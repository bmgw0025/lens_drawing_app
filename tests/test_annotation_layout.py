import unittest

from matplotlib.backends.backend_agg import FigureCanvasAgg

from batch_import import CementedLensData, SingleLensData
from main import SideAnnotationManager, _build_assembly_page_figure
from settings import DEFAULT_SETTINGS
from tests.test_geometry_validation import split_triplet


class AnnotationLayoutTests(unittest.TestCase):
    def assert_vertical_annotations_do_not_overlap(self, fig, expected_count):
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        renderer = canvas.get_renderer()
        boxes = []
        for axis in fig.axes:
            for text in axis.texts:
                if round(float(text.get_rotation())) % 180 == 90:
                    boxes.append((text.get_text(), text.get_window_extent(renderer)))

        self.assertEqual(len(boxes), expected_count)
        for index, (label_a, box_a) in enumerate(boxes):
            for label_b, box_b in boxes[index + 1:]:
                self.assertFalse(
                    box_a.overlaps(box_b),
                    f"vertical annotations overlap: {label_a} / {label_b}",
                )

    def test_mixed_scales_share_global_physical_lanes(self):
        manager = SideAnnotationManager(
            J=3.0, base_offset_J=8.0, lane_spacing_J=3.0, boundary_x=10.0
        )
        slots = [
            manager.register("right", 0, 7, 0, lambda _: None,
                             slot_id="ad", attach_x=8, offset_scale=3,
                             preferred_offset_J=6),
            manager.register("right", 0, 9, 1, lambda _: None,
                             slot_id="small-md", attach_x=13, offset_scale=1.8,
                             preferred_offset_J=8),
            manager.register("right", 0, 12, 1, lambda _: None,
                             slot_id="large-md", attach_x=5, offset_scale=2.4,
                             preferred_offset_J=8),
        ]
        manager.layout()

        positions = sorted(slot.assigned_x for slot in slots)
        self.assertEqual(positions, [28.0, 37.0, 46.0])
        self.assertTrue(all(
            abs((slot.attach_x + slot.assigned_offset * slot.offset_scale)
                - slot.assigned_x) < 1e-9
            for slot in slots
        ))

    def test_cemented_md_and_ad_text_boxes_do_not_overlap(self):
        data = CementedLensData("layout", "TEST", split_triplet())
        settings = DEFAULT_SETTINGS.copy()
        settings.update({
            "cemented_ref_lens": 2,
            "dia_offset_J": 8,
            "ad_offset_J": 6,
            "arrow_scale": 0.3,
        })
        fig = _build_assembly_page_figure(data, settings, 1, 4)
        try:
            self.assert_vertical_annotations_do_not_overlap(fig, 5)
        finally:
            fig.clear()

    def test_near_equal_md_and_split_ad_values_use_distinct_lanes(self):
        data = CementedLensData(
            "severe-layout",
            "TEST",
            [
                SingleLensData("G1", 4, -30, 25, 30, 24, 22),
                SingleLensData("G2", 5, 25, -40, 29, 20, 18),
                SingleLensData("G3", 2, -40, 50, 28, 16, 14),
            ],
        )
        settings = DEFAULT_SETTINGS.copy()
        settings.update({
            "cemented_ref_lens": 2,
            "dia_offset_J": 8,
            "ad_offset_J": 6,
            "arrow_scale": 0.3,
        })
        fig = _build_assembly_page_figure(data, settings, 1, 4)
        try:
            self.assert_vertical_annotations_do_not_overlap(fig, 5)
        finally:
            fig.clear()


if __name__ == "__main__":
    unittest.main()
