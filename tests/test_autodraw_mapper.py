import unittest
import math

from autodraw.mapper import map_to_drafts
from autodraw.models import ExtractedSystem, SurfaceRecord


def _surface(
    index,
    *,
    radius=0.0,
    thickness=0.0,
    material="",
    semi_diameter=9.0,
    mechanical_semi_diameter=9.0,
    is_object=False,
    is_image=False,
):
    return SurfaceRecord(
        index=index,
        type_name="Standard",
        comment="",
        radius=radius,
        thickness=thickness,
        material=material,
        semi_diameter=semi_diameter,
        mechanical_semi_diameter=mechanical_semi_diameter,
        aperture_type="None",
        explicit_aperture_radius=None,
        coating="",
        is_stop=False,
        is_object=is_object,
        is_image=is_image,
        solves={
            "radius": "Fixed",
            "thickness": "Fixed",
            "material": "Fixed",
            "semi_diameter": "Fixed",
            "mechanical_semi_diameter": "Fixed",
        },
        tilt_decenter={},
    )


def _virtual_surface_triplet():
    surfaces = [
        _surface(0, is_object=True, semi_diameter=0.0, mechanical_semi_diameter=0.0),
        _surface(1, semi_diameter=10.0, mechanical_semi_diameter=10.0),
        _surface(2, radius=66.0, thickness=5.0, material="H-K9L"),
        _surface(3, radius=-58.0, thickness=0.0),
        _surface(
            4,
            radius=-58.0,
            thickness=6.0,
            material="H-FK61B",
            mechanical_semi_diameter=10.0,
        ),
        _surface(
            5,
            radius=35.0,
            thickness=7.0,
            material="H-ZF11",
            semi_diameter=8.0,
            mechanical_semi_diameter=8.0,
        ),
        _surface(
            6,
            radius=-45.0,
            semi_diameter=8.0,
            mechanical_semi_diameter=8.0,
        ),
        _surface(
            7,
            is_image=True,
            semi_diameter=8.0,
            mechanical_semi_diameter=8.0,
        ),
    ]
    return ExtractedSystem(
        source_file=r"C:\test\test2.zmx",
        source_sha256="f0" * 32,
        source_size=1,
        provider="test",
        opticstudio_version="OpticStudio 2022 R2.01",
        license_status="PremiumEdition",
        mode="Sequential",
        lens_units="Millimeters",
        unit_to_mm=1.0,
        title="Test2",
        configuration_count=1,
        current_configuration=1,
        surfaces=surfaces,
    )


class AutodrawMapperTests(unittest.TestCase):
    def test_h_k9l_plane_plane_element_is_excluded_as_prism(self):
        system = _virtual_surface_triplet()
        system.surfaces = [
            _surface(
                0,
                radius=math.inf,
                is_object=True,
                semi_diameter=0.0,
                mechanical_semi_diameter=0.0,
            ),
            _surface(
                1,
                radius=math.inf,
                thickness=12.5,
                material="H-K9L",
            ),
            _surface(2, radius=math.inf),
            _surface(3, radius=math.inf, is_image=True),
        ]

        draft = map_to_drafts(system)[0]

        self.assertEqual(draft.status, "excluded")
        self.assertEqual(draft.topology["group_type"], "excluded_prism")
        self.assertEqual(
            draft.topology["exclusion"]["rule"],
            "h-k9l_plane_plane_prism_exclusion_v1",
        )
        self.assertEqual(draft.topology["exclusion"]["thickness_mm"], 12.5)
        self.assertEqual(draft.blockers, [])

    def test_zero_thickness_matching_virtual_surface_forms_triplet(self):
        drafts = map_to_drafts(_virtual_surface_triplet())

        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        self.assertEqual(draft.status, "accepted")
        self.assertEqual(draft.topology["group_type"], "cemented_triplet")
        self.assertEqual(
            [item["kind"] for item in draft.topology["connections"]],
            ["virtual_cemented_interface", "direct_cemented_interface"],
        )
        virtual = draft.topology["connections"][0]
        self.assertEqual(virtual["interface_surfaces"], [3, 4])
        self.assertEqual(virtual["zero_thickness_air_surfaces"], [3])
        self.assertTrue(virtual["coincidence_evidence"]["all_gap_thickness_zero"])
        self.assertTrue(virtual["coincidence_evidence"]["all_radii_match"])
        self.assertEqual(
            [draft.row[f"Glass{index}"] for index in range(1, 4)],
            ["H-K9L", "H-FK61B", "H-ZF11"],
        )
        self.assertEqual([draft.row[f"T{index}"] for index in range(1, 4)], [5.0, 6.0, 7.0])
        self.assertEqual(draft.row["R2"], -58.0)
        self.assertEqual(draft.row["MD2"], 20.0)
        md2 = next(item for item in draft.provenance if item.field == "MD2")
        self.assertEqual(md2.confidence, "medium")

    def test_virtual_interface_preserves_different_side_specific_ad_values(self):
        system = _virtual_surface_triplet()
        system.surfaces[3].semi_diameter = 9.0
        system.surfaces[4].semi_diameter = 8.5

        draft = map_to_drafts(system)[0]

        self.assertEqual(draft.status, "accepted", draft.blockers)
        self.assertEqual(draft.lenses[0].AD_right, 18.0)
        self.assertEqual(draft.lenses[1].AD_left, 17.0)
        self.assertIsNone(draft.row["AD2"])
        self.assertFalse(draft.legacy_row_compatible)
        boundary = draft.topology["boundary_surfaces"][1]
        self.assertEqual(
            boundary["ad_fields"],
            ["Lens1.AD_right", "Lens2.AD_left"],
        )
        self.assertEqual(boundary["ad_values_mm"], [18.0, 17.0])
        self.assertFalse(boundary["legacy_ad_compatible"])
        self.assertTrue(any("分侧值" in warning for warning in draft.warnings))

    def test_virtual_interface_uses_one_logical_shared_radius(self):
        system = _virtual_surface_triplet()
        system.surfaces[4].radius = -58.00000001

        draft = map_to_drafts(system)[0]

        self.assertEqual(draft.status, "accepted", draft.blockers)
        self.assertEqual(draft.row["R2"], -58.0)
        self.assertEqual(draft.lenses[0].R_right, -58.0)
        self.assertEqual(draft.lenses[1].R_left, -58.0)

    def test_zero_thickness_mismatched_curvature_is_blocked(self):
        system = _virtual_surface_triplet()
        system.surfaces[4].radius = -57.0

        draft = map_to_drafts(system)[0]

        self.assertEqual(draft.status, "blocked")
        self.assertEqual(
            draft.topology["group_type"], "ambiguous_zero_thickness_compound"
        )
        self.assertTrue(any("曲率半径不同" in item for item in draft.blockers))

    def test_nonzero_air_gap_splits_the_physical_groups(self):
        system = _virtual_surface_triplet()
        system.surfaces[3].thickness = 0.01

        drafts = map_to_drafts(system)

        self.assertEqual(
            [draft.topology["group_type"] for draft in drafts],
            ["singlet", "cemented_doublet"],
        )


if __name__ == "__main__":
    unittest.main()
