import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autodraw.models import ExtractedSystem, SurfaceRecord
from autodraw.output_validation import validate
from autodraw.pipeline import run_pipeline


def _surface(index, *, material="", radius=math.inf, thickness=0.0, is_image=False):
    return SurfaceRecord(
        index=index,
        type_name="Standard",
        comment="",
        radius=radius,
        thickness=thickness,
        material=material,
        semi_diameter=8.0,
        mechanical_semi_diameter=8.0,
        aperture_type="None",
        explicit_aperture_radius=None,
        coating="",
        is_stop=False,
        is_object=index == 0,
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


class FakePrismProvider:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def extract(self, source):
        path = Path(source).resolve()
        return ExtractedSystem(
            source_file=str(path),
            source_sha256="a" * 64,
            source_size=path.stat().st_size,
            provider="test",
            opticstudio_version="test",
            license_status="test",
            mode="Sequential",
            lens_units="Millimeters",
            unit_to_mm=1.0,
            title="PrismOnly",
            configuration_count=1,
            current_configuration=1,
            surfaces=[
                _surface(0),
                _surface(1, material="H-K9L", thickness=12.5),
                _surface(2),
                _surface(3, is_image=True),
            ],
        )


class PrismExclusionPipelineTests(unittest.TestCase):
    def test_excluded_prism_generates_delivery_reports_without_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "prism.zmx"
            source.write_text("synthetic", encoding="ascii")
            output = root / "result"
            with (
                patch("autodraw.pipeline.NativeZosApiProvider", FakePrismProvider),
                patch("autodraw.pipeline.render_draft") as renderer,
            ):
                audit = run_pipeline(
                    source,
                    output,
                    naming_policy={
                        "mode": "generated",
                        "confirm_generated_names": True,
                        "evidence_ids": ["test"],
                    },
                    task_context={"execution_mode": "test"},
                )
            report = validate(
                output,
                output / "validation_render",
                "pending",
                "prism exclusion check",
            )
            summary = (output / "manufacturing_requirements_summary.md").read_text(
                encoding="utf-8"
            )

        renderer.assert_not_called()
        self.assertEqual(audit["excluded_groups"], [1])
        self.assertEqual(audit["rendered_pdfs"], [])
        self.assertTrue(audit["drawings_generated"])
        self.assertTrue(report["automated_checks_passed"])
        self.assertEqual(report["excluded_count"], 1)
        self.assertIn("双平面棱镜", summary)


if __name__ == "__main__":
    unittest.main()
