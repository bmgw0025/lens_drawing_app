import unittest

from matplotlib.backends.backend_agg import FigureCanvasAgg

from batch_import import CementedLensData, SingleLensData
from main import (
    SideAnnotationManager,
    _build_assembly_page_figure,
    _build_lens_page_context,
    _build_single_page_figure,
)
from settings import DEFAULT_SETTINGS
from tests.test_geometry_validation import split_triplet


class AnnotationLayoutTests(unittest.TestCase):
    def assert_dimension_leader_clears_text(self, fig, leader_id, direction="right"):
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        renderer = canvas.get_renderer()
        text = next(
            item for axis in fig.axes for item in axis.texts
            if item.get_gid() == f"dimension-text:{leader_id}"
        )
        line = next(
            item for axis in fig.axes for item in axis.lines
            if item.get_gid() == f"dimension-leader:{leader_id}"
        )
        bbox = text.get_window_extent(renderer)
        line_end_px = text.axes.transData.transform(
            (line.get_xdata()[-1], line.get_ydata()[-1])
        )[0]
        if direction == "right":
            self.assertGreater(line_end_px, bbox.x1)
        else:
            self.assertLess(line_end_px, bbox.x0)

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

    def assert_edit_hotspots_cover_rendered_text(self, fig, field_ids):
        canvas = FigureCanvasAgg(fig)
        canvas.draw()
        renderer = canvas.get_renderer()
        for field_id in field_ids:
            text = next(
                item for axis in fig.axes for item in axis.texts
                if getattr(item, "_field_id", None) == field_id
            )
            region = next(
                item for axis in fig.axes for item in axis.patches
                if getattr(item, "_field_id", None) == field_id
                and getattr(item, "_field_region", False)
            )
            text_box = text.get_window_extent(renderer)
            region_box = region.get_window_extent(renderer)
            self.assertLessEqual(region_box.x0, text_box.x0, field_id)
            self.assertLessEqual(region_box.y0, text_box.y0, field_id)
            self.assertGreaterEqual(region_box.x1, text_box.x1, field_id)
            self.assertGreaterEqual(region_box.y1, text_box.y1, field_id)

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

    def test_single_dimension_leaders_clear_rendered_text(self):
        data = CementedLensData("leaders", "TEST", split_triplet()[:2])
        settings = DEFAULT_SETTINGS.copy()
        context = _build_lens_page_context(data, settings, {}, 0)
        lens = data.lenses[0]
        s = context["settings"]
        fig = _build_single_page_figure(
            lens.T, lens.R_left, lens.R_right, lens.MD,
            lens.AD_left, lens.AD_right,
            s["J_multiplier"], s["ct_offset_J"], s["et_offset_J"],
            s["sag_offset_J"], s["dia_offset_J"], s["ad_offset_J"],
            s["spray_gap_J"], context["chamfer_left"], context["chamfer_right"],
            s["t_tol"], s["sag_tol"], s["font_size"], s["arrow_scale"],
            s["r_offset_J"], context["dia_upper"], context["dia_lower"],
            context["proc_params"], s, context["ca1"], context["ca2"],
        )
        try:
            self.assert_dimension_leader_clears_text(fig, "ct")
            self.assert_dimension_leader_clears_text(fig, "et")
            self.assert_dimension_leader_clears_text(fig, "sag1", "left")
            self.assert_dimension_leader_clears_text(fig, "sag2")
            right_ends = []
            for leader_id in ("ct", "et", "sag2"):
                line = next(
                    item for axis in fig.axes for item in axis.lines
                    if item.get_gid() == f"dimension-leader:{leader_id}"
                )
                right_ends.append(line.get_xdata()[-1])
            self.assertLess(max(right_ends) - min(right_ends), 1e-9)
        finally:
            fig.clear()

    def test_assembly_total_thickness_leader_clears_rendered_text(self):
        data = CementedLensData("assembly-leader", "TEST", split_triplet()[:2])
        fig = _build_assembly_page_figure(data, DEFAULT_SETTINGS.copy(), 1, 3)
        try:
            self.assert_dimension_leader_clears_text(fig, "assembly-ct")
        finally:
            fig.clear()

    def test_assembly_title_block_is_compact_without_coating_or_empty_rows(self):
        data = CementedLensData("compact-assembly", "TEST", split_triplet()[:2])
        fig = _build_assembly_page_figure(data, DEFAULT_SETTINGS.copy(), 1, 3)
        try:
            page_axis = fig.axes[0]
            labels = {text.get_text() for text in page_axis.texts}
            self.assertNotIn("Coating Position⊕", labels)
            self.assertNotIn("Surface", labels)
            self.assertNotIn("Wavelength(nm)", labels)
            self.assertNotIn("N", labels)
            self.assertNotIn("ΔN", labels)
            self.assertNotIn("B", labels)
            self.assertIn("Special technical requirement", labels)
            self.assertIn("C", labels)
            self.assertIn("Project", labels)
            self.assertIn("Material", labels)

            bottom_lines = [
                line for line in page_axis.lines
                if max(line.get_ydata()) <= 45 and min(line.get_ydata()) >= 10
            ]
            self.assertFalse(any(
                min(line.get_xdata()) < 100 < max(line.get_xdata())
                for line in bottom_lines
            ))
        finally:
            fig.clear()

    def test_selected_internal_surfaces_render_sapphire_coating(self):
        data = CementedLensData("sapphire", "TEST", split_triplet())
        settings = DEFAULT_SETTINGS.copy()
        settings["sapphire_surfaces"] = ["1:S2", "2:S1", "2:S2"]
        expected = [(False, True), (True, True), (False, False)]
        for index, expected_sides in enumerate(expected):
            context = _build_lens_page_context(data, settings, {}, index)
            self.assertEqual(
                (context["proc_params"]["sapphire_s1"],
                 context["proc_params"]["sapphire_s2"]),
                expected_sides,
            )
            lens = data.lenses[index]
            s = context["settings"]
            fig = _build_single_page_figure(
                lens.T, lens.R_left, lens.R_right, lens.MD,
                lens.AD_left, lens.AD_right,
                s["J_multiplier"], s["ct_offset_J"], s["et_offset_J"],
                s["sag_offset_J"], s["dia_offset_J"], s["ad_offset_J"],
                s["spray_gap_J"], context["chamfer_left"], context["chamfer_right"],
                s["t_tol"], s["sag_tol"], s["font_size"], s["arrow_scale"],
                s["r_offset_J"], context["dia_upper"], context["dia_lower"],
                context["proc_params"], s, context["ca1"], context["ca2"],
            )
            try:
                sapphire_count = sum(
                    text.get_text() == "蓝宝石膜"
                    for axis in fig.axes for text in axis.texts
                )
                self.assertEqual(sapphire_count, sum(expected_sides))
            finally:
                fig.clear()

    def test_edit_hotspots_follow_layout_slots_and_cover_text(self):
        data = CementedLensData("editor-hotspots", "TEST", split_triplet()[:2])
        settings = DEFAULT_SETTINGS.copy()
        settings["proc_ranking"] = "CUSTOM-GRADE-LONG"
        context = _build_lens_page_context(data, settings, {}, 1)
        lens = data.lenses[1]
        s = context["settings"]
        fig = _build_single_page_figure(
            lens.T, lens.R_left, lens.R_right, lens.MD,
            lens.AD_left, lens.AD_right,
            s["J_multiplier"], s["ct_offset_J"], s["et_offset_J"],
            s["sag_offset_J"], s["dia_offset_J"], s["ad_offset_J"],
            s["spray_gap_J"], context["chamfer_left"], context["chamfer_right"],
            s["t_tol"], s["sag_tol"], s["font_size"], s["arrow_scale"],
            s["r_offset_J"], context["dia_upper"], context["dia_lower"],
            context["proc_params"], s, context["ca1"], context["ca2"],
        )
        try:
            self.assert_edit_hotspots_cover_rendered_text(
                fig,
                (
                    "vendor", "ranking", "molding", "chamfer",
                    "ca1", "ca2", "c_val", "n_val", "dn_val",
                    "b_val", "signature",
                ),
            )
        finally:
            fig.clear()


if __name__ == "__main__":
    unittest.main()
