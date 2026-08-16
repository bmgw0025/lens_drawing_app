from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageDraw


def _json_write(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("−", "-").split())


def _auto_ca(ad: float, ratio: float) -> float:
    return math.floor(ratio * ad * 10.0) / 10.0


def _resolved_ca(
    settings: dict[str, Any],
    lens_index: int,
    side: str,
    ad: float,
) -> float:
    indexed_mode = settings.get(f"ca_mode_{lens_index}", "auto")
    if indexed_mode == "manual":
        key = f"ca_{lens_index}_{side}"
        return float(settings[key])
    if settings.get("CA_mode", "auto") == "manual":
        return float(settings["CA1" if side == "left" else "CA2"])
    return _auto_ca(ad, float(settings.get("ca_ratio", 0.94)))


def _format_process_number(value: float) -> str:
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def _resolved_chamfers(
    settings: dict[str, Any],
    lens_index: int,
    lens: dict[str, Any],
    lens_count: int,
) -> tuple[float, float]:
    if settings.get(f"chamfer_mode_{lens_index}", "auto") == "manual":
        return (
            float(settings[f"chamfer_{lens_index}_left"]),
            float(settings[f"chamfer_{lens_index}_right"]),
        )
    if settings.get("chamfer_mode", "auto") == "manual":
        return float(settings["chamfer_left"]), float(settings["chamfer_right"])
    diameter = float(lens["MD"])
    base = 0.2 if diameter <= 30 else (0.3 if diameter <= 80 else 0.4)
    if lens_count > 1:
        return base, base
    left_radius = float(lens["R_left"])
    right_radius = float(lens["R_right"])
    if (left_radius > 0 > right_radius) or (left_radius < 0 < right_radius):
        left_abs = abs(left_radius)
        right_abs = abs(right_radius)
        if left_abs and right_abs and left_abs != right_abs:
            ratio = abs(left_abs - right_abs) / min(left_abs, right_abs)
            if ratio <= 0.70:
                return 0.2, 0.4
    return base, base


def _process_expectations(
    settings: dict[str, Any],
    *,
    lens_index: int,
    lens_count: int,
) -> dict[str, str]:
    expected = {
        "vendor": f"Vendor/Brand: {settings.get('proc_vendor', 'CDGM')}",
        "ranking": f"Ranking: {settings.get('proc_ranking', '01')}",
        "molding": f"Scribe&Break/Molding: {settings.get('proc_molding', 'Molding')}",
        "surface_defect": f"B {settings.get('proc_surface_defect', '60/40')}",
        "delta_n": f"ΔN {settings.get('proc_DN', '0.3')}",
        "signature": f"Drafting {settings.get('proc_signature', 'l.y.h')}",
        "c_single": f"C {settings.get('proc_c_single', '60″')}",
        "chipping": f"Chipping: {settings.get('proc_chipping', '0.2')}",
        "roughness": str(settings.get("proc_roughness", "0.01")),
    }
    if not (lens_count > 1):
        expected.update({
            "ink_brand": f"Ink Brand&Model: {settings.get('proc_ink_brand', 'GT-7II')}",
            "ink_proportion": f"Ink Proportion: {settings.get('proc_ink_proportion', '8: 1: 9(Paint: Curing agent: Diluent)')}",
            "ink_thickness": f"Thickness: {settings.get('proc_ink_thickness', '3~5um')}",
            "spraying_position": f"Spraying position: {settings.get('proc_spraying_position', 'Arrow indication The dashed line')}",
            "dimensions_rule": f"Dimensions: {settings.get('proc_dimensions_rule', 'According to the drawing')}",
            "ink_leakage": f"Ink over spray/Light Leakage: {settings.get('proc_ink_leakage', '0.1')}",
        })
    special_notes = str(settings.get("special_notes", "")).strip()
    if special_notes:
        expected["special_notes"] = special_notes
    if settings.get("proc_N_mode", "auto") == "manual":
        expected["manual_n"] = f"N {settings.get('proc_N_manual', '')}"
    coat = str(settings.get("coat_preset", "SQ-A1"))
    if coat != "Custom":
        if lens_index == 1:
            expected["coat_outer_s1"] = coat
        if lens_index == lens_count:
            expected["coat_outer_s2"] = coat
    else:
        if lens_index == 1:
            for key in ("coat_s1_wave1", "coat_s1_wave2", "coat_s1_ravg1", "coat_s1_ravg2"):
                value = str(settings.get(key, "")).strip()
                if value:
                    expected[key] = value
        if lens_index == lens_count:
            for key in ("coat_s2_wave1", "coat_s2_wave2", "coat_s2_ravg1", "coat_s2_ravg2"):
                value = str(settings.get(key, "")).strip()
                if value:
                    expected[key] = value
    return expected


def _radius_token(radius: float) -> str:
    if abs(radius) <= 1e-12:
        return "PLANO"
    prefix = "-R" if radius < 0 else "R"
    return f"{prefix}{abs(radius):.3f}"


def _rotated_diameters(page: pdfplumber.page.Page) -> list[float]:
    values = []
    chars = page.chars
    for marker in chars:
        if marker.get("text") != "∅" or marker.get("upright") is not False:
            continue
        column = [
            char
            for char in chars
            if char.get("upright") is False
            and abs(float(char["x0"]) - float(marker["x0"])) <= 0.25
            and 0 < float(marker["top"]) - float(char["top"]) <= 36
            and str(char.get("text", "")) in "0123456789."
        ]
        text = "".join(
            str(char["text"])
            for char in sorted(column, key=lambda item: float(item["top"]), reverse=True)
        )
        match = re.fullmatch(r"\d+(?:\.\d+)?", text)
        if match:
            values.append(float(text))
    return values


def _upright_lines(page: pdfplumber.page.Page, tolerance: float = 1.0) -> list[str]:
    clusters: list[dict[str, Any]] = []
    chars = [
        char
        for char in page.chars
        if char.get("upright") is not False and str(char.get("text", "")).strip()
    ]
    for char in sorted(chars, key=lambda item: (float(item["top"]), float(item["x0"]))):
        top = float(char["top"])
        cluster = next(
            (item for item in clusters if abs(float(item["top"]) - top) <= tolerance),
            None,
        )
        if cluster is None:
            cluster = {"top": top, "chars": []}
            clusters.append(cluster)
        cluster["chars"].append(char)
    return [
        "".join(
            str(char["text"])
            for char in sorted(cluster["chars"], key=lambda item: float(item["x0"]))
        ).replace(" ", "")
        for cluster in clusters
    ]


def _contains_number(values: list[float], expected: float) -> bool:
    return any(abs(value - expected) <= 0.005 for value in values)


def _render_page(
    page: pdfium.PdfPage,
    output_path: Path,
) -> dict[str, Any]:
    image = page.render(scale=1.5).to_pil().convert("RGB")
    image.save(output_path, optimize=True)
    white = Image.new("RGB", image.size, "white")
    difference = ImageChops.difference(image, white).convert("L")
    mask = difference.point(lambda value: 255 if value > 8 else 0)
    bbox = mask.getbbox()
    histogram = difference.histogram()
    nonwhite_pixels = sum(histogram[9:])
    nonwhite_ratio = nonwhite_pixels / (image.width * image.height)
    margins = None
    if bbox is not None:
        margins = {
            "left": bbox[0],
            "top": bbox[1],
            "right": image.width - bbox[2],
            "bottom": image.height - bbox[3],
        }
    checks = {
        "nonblank": bbox is not None and nonwhite_ratio > 0.005,
        "content_inside_page": margins is not None and min(margins.values()) >= 10,
    }
    return {
        "image": str(output_path),
        "pixel_size": [image.width, image.height],
        "content_bbox": list(bbox) if bbox else None,
        "content_margins_px": margins,
        "nonwhite_ratio": round(nonwhite_ratio, 6),
        "checks": checks,
        "passed": all(checks.values()),
    }


def _lens_fields(draft: dict[str, Any]) -> list[dict[str, Any]]:
    authoritative = draft.get("lenses", [])
    if authoritative:
        return [
            {
                "glass": lens["glass"],
                "T": float(lens["T"]),
                "R_left": float(lens["R_left"]),
                "R_right": float(lens["R_right"]),
                "MD": float(lens["MD"]),
                "AD_left": float(lens["AD_left"]),
                "AD_right": float(lens["AD_right"]),
            }
            for lens in authoritative
        ]
    row = draft["row"]
    count = len([key for key in row if key.startswith("Glass")])
    return [
        {
            "glass": row[f"Glass{index}"],
            "T": float(row[f"T{index}"]),
            "R_left": float(row[f"R{index}"]),
            "R_right": float(row[f"R{index + 1}"]),
            "MD": float(row[f"MD{index}"]),
            "AD_left": float(row[f"AD{index}"]),
            "AD_right": float(row[f"AD{index + 1}"]),
        }
        for index in range(1, count + 1)
    ]


def _validate_pdf_variant(
    draft: dict[str, Any],
    pdf_path: Path,
    render_dir: Path,
    defaults: dict[str, Any],
    effective_group: dict[str, Any] | None = None,
    *,
    variant: str,
    expect_part_name: bool,
) -> tuple[dict[str, Any], list[tuple[str, int, Path]]]:
    row = draft["row"]
    lenses = _lens_fields(draft)
    expected_pages = 1 if len(lenses) == 1 else len(lenses) + 1
    effective_group = effective_group or {}
    group_settings = dict(defaults)
    group_settings.update(effective_group.get("group_settings", {}))
    lens_page_settings = effective_group.get("lens_page_settings", {})

    checks: dict[str, bool] = {
        "draft_accepted": draft.get("status") == "accepted",
        "pdf_exists": pdf_path.is_file(),
    }
    failures: list[str] = []
    pages: list[dict[str, Any]] = []
    rendered: list[tuple[str, int, Path]] = []
    if not pdf_path.is_file():
        return {
            "group_index": draft["group_index"],
            "pdf": str(pdf_path),
            "checks": checks,
            "failures": ["PDF missing"],
            "passed": False,
        }, rendered

    with pdfplumber.open(pdf_path) as parsed:
        page_texts = [_normalize_text(page.extract_text() or "") for page in parsed.pages]
        page_diameters = [_rotated_diameters(page) for page in parsed.pages]
        page_upright_lines = [_upright_lines(page) for page in parsed.pages]
    document = pdfium.PdfDocument(str(pdf_path))
    checks["page_count"] = len(document) == expected_pages
    all_text = "\n".join(page_texts)
    checks["part_name_visibility"] = (
        str(row["PartName"]) in all_text
        if expect_part_name
        else str(row["PartName"]) not in all_text
    )
    if not checks["page_count"]:
        failures.append(f"page count {len(document)} != {expected_pages}")

    for page_index in range(len(document)):
        image_path = render_dir / (
            f"g{int(draft['group_index']):02d}_{variant}_{pdf_path.stem}_p{page_index + 1}.png"
        )
        render_result = _render_page(document[page_index], image_path)
        rendered.append((f"{variant}/{pdf_path.name}", page_index + 1, image_path))
        pages.append(
            {
                "page": page_index + 1,
                "text": page_texts[page_index],
                "rotated_diameters_mm": page_diameters[page_index],
                "render": render_result,
            }
        )
    checks["all_pages_render_nonblank_and_inside_frame"] = all(
        page["render"]["passed"] for page in pages
    )

    lens_results = []
    for index, lens in enumerate(lenses):
        page_index = index if len(lenses) == 1 else index + 1
        text = page_texts[page_index]
        lens_position = index + 1
        settings = dict(group_settings)
        settings.update(lens_page_settings.get(str(lens_position), {}))
        t_tol = float(settings.get("t_tol", 0.02))
        ca_left = _resolved_ca(settings, lens_position, "left", lens["AD_left"])
        ca_right = _resolved_ca(settings, lens_position, "right", lens["AD_right"])
        chamfer_left, chamfer_right = _resolved_chamfers(
            settings, lens_position, lens, len(lenses)
        )
        expected_tokens = {
            "glass": str(lens["glass"]),
            "thickness": f"{lens['T']:.2f}±{t_tol:.2f}",
            "left_radius": _radius_token(lens["R_left"]),
            "right_radius": _radius_token(lens["R_right"]),
            "left_clear_aperture": f"S1 φ{ca_left:.2f}",
            "right_clear_aperture": f"S2 φ{ca_right:.2f}",
            "chamfer_summary": f"Chamfer: {_format_process_number(chamfer_left)}",
            "chamfer_left": f"C{_format_process_number(chamfer_left)}",
            "chamfer_right": f"C{_format_process_number(chamfer_right)}",
        }
        token_checks = {
            name: token in text for name, token in expected_tokens.items()
        }
        md_check = _contains_number(page_diameters[page_index], lens["MD"])
        process_expected = _process_expectations(
            settings,
            lens_index=lens_position,
            lens_count=len(lenses),
        )
        process_checks = {
            name: token in text for name, token in process_expected.items()
        }
        passed = all(token_checks.values()) and all(process_checks.values()) and md_check
        if not passed:
            missing = [name for name, ok in token_checks.items() if not ok]
            if not md_check:
                missing.append("mechanical_diameter")
            missing.extend(
                f"process:{name}" for name, ok in process_checks.items() if not ok
            )
            failures.append(f"lens {index + 1} missing/incorrect: {', '.join(missing)}")
        lens_results.append(
            {
                "lens": index + 1,
                "page": page_index + 1,
                "expected": {
                    **expected_tokens,
                    "mechanical_diameter_mm": lens["MD"],
                    "process": process_expected,
                },
                "rotated_diameters_mm": page_diameters[page_index],
                "checks": {
                    **token_checks,
                    "mechanical_diameter": md_check,
                    "process": process_checks,
                },
                "passed": passed,
            }
        )
    checks["all_lens_geometry_present"] = all(item["passed"] for item in lens_results)

    assembly = None
    if len(lenses) > 1:
        text = page_texts[0]
        first_settings = dict(group_settings)
        first_settings.update(lens_page_settings.get("1", {}))
        last_settings = dict(group_settings)
        last_settings.update(lens_page_settings.get(str(len(lenses)), {}))
        assembly_t_tol = sum(
            float(
                {
                    **group_settings,
                    **lens_page_settings.get(str(index), {}),
                }.get("t_tol", 0.02)
            )
            for index in range(1, len(lenses) + 1)
        )
        assembly_ca_left = _resolved_ca(
            first_settings, 1, "left", lenses[0]["AD_left"]
        )
        assembly_ca_right = _resolved_ca(
            last_settings, len(lenses), "right", lenses[-1]["AD_right"]
        )
        assembly_chamfer_left, _ = _resolved_chamfers(
            first_settings, 1, lenses[0], len(lenses)
        )
        _, assembly_chamfer_right = _resolved_chamfers(
            last_settings, len(lenses), lenses[-1], len(lenses)
        )
        assembly_chamfer = _format_process_number(assembly_chamfer_left)
        if abs(assembly_chamfer_left - assembly_chamfer_right) > 1e-12:
            assembly_chamfer += "/" + _format_process_number(assembly_chamfer_right)
        assembly_expected = {
            "part_no": str(row["PartNo"]),
            "total_thickness": f"{sum(lens['T'] for lens in lenses):.2f}±{assembly_t_tol:.2f}",
            "left_clear_aperture": f"S1 φ{assembly_ca_left:.2f}",
            "right_clear_aperture": f"S2 φ{assembly_ca_right:.2f}",
            "chamfer": f"Chamfer: {assembly_chamfer}",
            "c_assembly": f"C {group_settings.get('proc_c_assembly', '60″')}",
            "chipping": f"Chipping: {group_settings.get('proc_chipping', '0.2')}",
            "roughness": str(group_settings.get("proc_roughness", "0.01")),
            "ink_brand": f"Ink Brand&Model: {group_settings.get('proc_ink_brand', 'GT-7II')}",
            "ink_thickness": f"Thickness: {group_settings.get('proc_ink_thickness', '3~5um')}",
        }
        if expect_part_name:
            assembly_expected["part_name"] = str(row["PartName"])
        assembly_note = str(group_settings.get("special_notes", "")).strip()
        if assembly_note:
            assembly_expected["special_notes"] = assembly_note
        assembly_checks = {
            name: token in text for name, token in assembly_expected.items()
        }
        material_checks = {
            str(lens["glass"]): any(
                str(lens["glass"]).replace(" ", "") in line
                for line in page_upright_lines[0]
            )
            for lens in lenses
        }
        expected_mds = sorted({lens["MD"] for lens in lenses})
        diameter_checks = {
            f"{value:.2f}": _contains_number(page_diameters[0], value)
            for value in expected_mds
        }
        assembly_passed = (
            all(assembly_checks.values())
            and all(material_checks.values())
            and all(diameter_checks.values())
        )
        if not assembly_passed:
            failures.append("assembly page geometry/table validation failed")
        assembly = {
            "page": 1,
            "expected": assembly_expected,
            "checks": assembly_checks,
            "materials": material_checks,
            "mechanical_diameters": diameter_checks,
            "rotated_diameters_mm": page_diameters[0],
            "passed": assembly_passed,
        }
        checks["assembly_geometry_present"] = assembly_passed

    passed = all(checks.values()) and not failures
    return {
        "variant": variant,
        "group_index": draft["group_index"],
        "group_type": draft.get("topology", {}).get("group_type"),
        "surface_range": draft.get("surface_range"),
        "topology_connections": draft.get("topology", {}).get("connections", []),
        "pdf": str(pdf_path),
        "expected_page_count": expected_pages,
        "actual_page_count": len(document),
        "checks": checks,
        "lens_pages": lens_results,
        "assembly_page": assembly,
        "pages": pages,
        "failures": failures,
        "passed": passed,
    }, rendered


def _validate_group(
    draft: dict[str, Any],
    output_dir: Path,
    render_dir: Path,
    defaults: dict[str, Any],
    effective_group: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[tuple[str, int, Path]]]:
    row = draft["row"]
    variants = (
        (
            "save",
            output_dir
            / "drawings"
            / str(row.get("SavePdfFolder", "Save PDF"))
            / f"{row['PartName']}.pdf",
            True,
        ),
        (
            "mfr",
            output_dir
            / "drawings"
            / str(row.get("MfrPdfFolder", "Mfr PDF"))
            / f"{row['PartNo']}.pdf",
            False,
        ),
    )
    results = []
    rendered: list[tuple[str, int, Path]] = []
    for variant, pdf_path, expect_part_name in variants:
        result, images = _validate_pdf_variant(
            draft,
            pdf_path,
            render_dir,
            defaults,
            effective_group,
            variant=variant,
            expect_part_name=expect_part_name,
        )
        results.append(result)
        rendered.extend(images)
    return {
        "group_index": draft["group_index"],
        "group_type": draft.get("topology", {}).get("group_type"),
        "surface_range": draft.get("surface_range"),
        "variants": results,
        "passed": all(item["passed"] for item in results),
    }, rendered


def _contact_sheets(
    rendered: list[tuple[str, int, Path]],
    render_dir: Path,
) -> list[str]:
    sheet_paths = []
    thumb_width = 900
    thumb_height = 640
    cell_width = thumb_width + 60
    cell_height = thumb_height + 90
    for start in range(0, len(rendered), 4):
        chunk = rendered[start : start + 4]
        sheet = Image.new("RGB", (cell_width * 2, cell_height * 2), "#d8d8d8")
        draw = ImageDraw.Draw(sheet)
        for offset, (pdf_name, page_number, path) in enumerate(chunk):
            page_image = Image.open(path).convert("RGB")
            page_image.thumbnail((thumb_width, thumb_height), Image.Resampling.LANCZOS)
            column = offset % 2
            row = offset // 2
            x = column * cell_width + (cell_width - page_image.width) // 2
            y = row * cell_height + 38 + (thumb_height - page_image.height) // 2
            sheet.paste(page_image, (x, y))
            draw.text(
                (column * cell_width + 12, row * cell_height + 10),
                f"{pdf_name} page {page_number}",
                fill="black",
            )
        sheet_path = render_dir / f"contact_sheet_{start // 4 + 1}.png"
        sheet.save(sheet_path, optimize=True)
        sheet_paths.append(str(sheet_path))
    return sheet_paths


def validate(
    root: Path,
    render_dir: Path,
    human_review: str,
    human_note: str,
) -> dict[str, Any]:
    root = root.resolve()
    render_dir = render_dir.resolve()
    render_dir.mkdir(parents=True, exist_ok=True)
    groups = []
    exclusions = []
    blocked_drafts = []
    rendered = []
    audits = []
    for output_dir in sorted(path.parent for path in root.rglob("drawing_drafts.json")):
        drafts = json.loads((output_dir / "drawing_drafts.json").read_text(encoding="utf-8"))
        work_order = json.loads((output_dir / "ai_work_order.json").read_text(encoding="utf-8"))
        audit = json.loads((output_dir / "audit.json").read_text(encoding="utf-8"))
        effective_path = output_dir / "effective_manufacturing_requirements.json"
        effective = (
            json.loads(effective_path.read_text(encoding="utf-8"))
            if effective_path.is_file()
            else {"groups": {}}
        )
        audits.append(
            {
                "path": str(output_dir / "audit.json"),
                "schema_version": audit.get("schema_version"),
                "source_file": audit.get("source_file"),
                "source_sha256": audit.get("source_sha256"),
                "automatic_geometry_ready": audit.get("automatic_geometry_ready"),
                "drawings_generated": audit.get("drawings_generated"),
                "production_release_ready": audit.get("production_release_ready"),
                "renderer_source_manifest_sha256": audit.get(
                    "renderer_source_manifest_sha256", {}
                ),
            }
        )
        for draft in drafts:
            if draft.get("status") == "excluded":
                exclusions.append(
                    {
                        "group_index": draft.get("group_index"),
                        "surface_range": draft.get("surface_range"),
                        "group_type": draft.get("topology", {}).get("group_type"),
                        "exclusion": draft.get("topology", {}).get("exclusion", {}),
                        "warnings": draft.get("warnings", []),
                    }
                )
                continue
            if draft.get("status") != "accepted":
                blocked_drafts.append(
                    {
                        "group_index": draft.get("group_index"),
                        "status": draft.get("status"),
                        "blockers": draft.get("blockers", []),
                    }
                )
                continue
            result, images = _validate_group(
                draft,
                output_dir,
                render_dir,
                work_order.get("current_defaults", {}),
                effective.get("groups", {}).get(str(draft["group_index"])),
            )
            groups.append(result)
            rendered.extend(images)

    automated_passed = (
        bool(groups or exclusions)
        and not blocked_drafts
        and all(group["passed"] for group in groups)
    )
    contact_sheets = _contact_sheets(rendered, render_dir)
    report = {
        "schema_version": "2.0",
        "validation_root": str(root),
        "automated_checks_passed": automated_passed,
        "human_visual_review": {
            "status": human_review,
            "note": human_note,
            "contact_sheets": contact_sheets,
        },
        "all_checks_passed": automated_passed and human_review == "passed",
        "audit_files": audits,
        "group_count": len(groups),
        "excluded_count": len(exclusions),
        "pdf_count": sum(len(group.get("variants", [])) for group in groups),
        "total_pages": sum(
            variant.get("actual_page_count", 0)
            for group in groups
            for variant in group.get("variants", [])
        ),
        "groups": groups,
        "excluded_components": exclusions,
        "blocked_drafts": blocked_drafts,
    }
    _json_write(root / "pdf_validation_report.json", report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate Lens Drawing PDFs against drawing_drafts.json and render every page."
    )
    parser.add_argument("root", help="Result root containing one or more drawing_drafts.json files")
    parser.add_argument("--render-dir", required=True, help="Directory for page PNGs/contact sheets")
    parser.add_argument(
        "--human-review",
        "--visual-review",
        dest="human_review",
        choices=("pending", "passed", "failed"),
        default="pending",
    )
    parser.add_argument("--human-note", "--visual-note", dest="human_note", default="")
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    args = build_parser().parse_args(argv)
    report = validate(
        Path(args.root),
        Path(args.render_dir),
        args.human_review,
        args.human_note,
    )
    print(
        json.dumps(
            {
                "all_checks_passed": report["all_checks_passed"],
                "automated_checks_passed": report["automated_checks_passed"],
                "group_count": report["group_count"],
                "pdf_count": report["pdf_count"],
                "total_pages": report["total_pages"],
                "report": str(Path(args.root).resolve() / "pdf_validation_report.json"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["all_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
