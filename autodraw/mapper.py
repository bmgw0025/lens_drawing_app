from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path

from .models import (
    DrawingDraft,
    ExtractedSystem,
    LensGeometry,
    Provenance,
    SurfaceRecord,
)


SUPPORTED_SURFACE_TYPE = "standard"
AIR_NAMES = {"", "AIR"}
BLOCKED_MATERIALS = {"MIRROR"}
SUSPICIOUS_MATERIAL_WORDS = ("GLUE", "CEMENT", "EPOXY", "ADHESIVE")


def _is_glass(material: str) -> bool:
    return material.strip().upper() not in AIR_NAMES | BLOCKED_MATERIALS


def _material_blocker(material: str) -> str | None:
    normalized = material.strip().upper()
    if normalized in BLOCKED_MATERIALS:
        return f"材料 {material!r} 不属于可加工普通玻璃"
    if "," in normalized and re.fullmatch(
        r"[+\-]?\d+(?:\.\d+)?\s*,\s*[+\-]?\d+(?:\.\d+)?", normalized
    ):
        return f"材料 {material!r} 是 Zemax 模型玻璃，不是可追溯玻璃牌号"
    if any(word in normalized for word in SUSPICIOUS_MATERIAL_WORDS) or normalized.startswith("NOA"):
        return f"材料 {material!r} 疑似胶水/胶层，当前模型不能自动判定其制造语义"
    return None


def _finite_positive(value: float) -> bool:
    return math.isfinite(value) and value > 0


def _radius_mm(surface: SurfaceRecord, factor: float) -> float:
    return 0.0 if not math.isfinite(surface.radius) else surface.radius * factor


def _ad_candidate(surface: SurfaceRecord, factor: float) -> tuple[float | None, str, str]:
    if surface.explicit_aperture_radius is not None and surface.explicit_aperture_radius > 0:
        return (
            2.0 * surface.explicit_aperture_radius * factor,
            f"surface {surface.index} explicit circular aperture MaximumRadius",
            "high",
        )
    if _finite_positive(surface.semi_diameter):
        solve = surface.solves.get("semi_diameter", "Unknown")
        return (
            2.0 * surface.semi_diameter * factor,
            f"surface {surface.index} evaluated SemiDiameter ({solve})",
            "high" if solve.lower() == "fixed" else "medium",
        )
    return None, f"surface {surface.index} missing usable aperture", "blocked"


def _diameters_match(first: float, second: float) -> bool:
    tolerance = max(1e-6, 1e-6 * max(abs(first), abs(second)))
    return abs(first - second) <= tolerance


def _radii_match(first: float, second: float) -> bool:
    if not math.isfinite(first) or not math.isfinite(second):
        return not math.isfinite(first) and not math.isfinite(second)
    tolerance = max(1e-9, 1e-9 * max(abs(first), abs(second)))
    return abs(first - second) <= tolerance


def _zero_thickness(value: float) -> bool:
    return math.isfinite(value) and abs(value) <= 1e-9


def _is_plane_radius(value: float) -> bool:
    return not math.isfinite(value) or abs(value) <= 1e-12


def _md_candidate_evidence(
    surface: SurfaceRecord,
    factor: float,
    *,
    side: str,
    boundary_role: str,
    association: str,
    required_diameter: float,
) -> dict[str, object]:
    raw_value = surface.mechanical_semi_diameter
    solve = surface.solves.get("mechanical_semi_diameter", "Unknown")
    value_mm = 2.0 * raw_value * factor if _finite_positive(raw_value) else None
    return {
        "side": side,
        "surface": surface.index,
        "boundary_role": boundary_role,
        "association": association,
        "raw_mechanical_semi_diameter": raw_value,
        "diameter_mm": value_mm,
        "solve": solve,
        "usable": value_mm is not None,
        "meets_ad_constraint": (
            value_mm is not None
            and value_mm + max(1e-6, 1e-6 * required_diameter) >= required_diameter
        ),
        "selected": False,
    }


def _infer_md(
    left: SurfaceRecord,
    right: SurfaceRecord,
    ad_left: float | None,
    ad_right: float | None,
    factor: float,
    *,
    left_role: str,
    right_role: str,
    left_association: str,
    right_association: str,
) -> tuple[float | None, str, str, list[str], dict[str, object]]:
    """Infer one lens diameter from interval topology and physical constraints."""
    warnings: list[str] = []
    evidence: dict[str, object] = {
        "method": "glass_interval_topology_and_ad_constraint_v3",
        "glass_interval": {
            "material_surface": left.index,
            "left_surface": left.index,
            "right_surface": right.index,
        },
        "rule": (
            "GLAS defines the medium after its surface and therefore the glass intervals. "
            "A cemented interface is shared evidence for both adjacent lenses and is not assigned "
            "to either lens merely from its surface number. MD must cover both boundary AD values. "
            "Equal candidates are merged; AD-impossible candidates are rejected; if different "
            "viable values remain, output is blocked instead of guessing. Fixed affects confidence "
            "but never overrides an unresolved numeric conflict."
        ),
        "required_minimum_diameter_mm": None,
        "candidates": [],
        "decision": "blocked",
    }
    if ad_left is None or ad_right is None:
        source = (
            f"surfaces {left.index}/{right.index}: 无法在两侧 AD 不完整时判断机械直径"
        )
        return None, source, "blocked", warnings, evidence

    required_diameter = max(ad_left, ad_right)
    evidence["required_minimum_diameter_mm"] = required_diameter
    candidates = [
        _md_candidate_evidence(
            left,
            factor,
            side="left",
            boundary_role=left_role,
            association=left_association,
            required_diameter=required_diameter,
        ),
        _md_candidate_evidence(
            right,
            factor,
            side="right",
            boundary_role=right_role,
            association=right_association,
            required_diameter=required_diameter,
        ),
    ]
    evidence["candidates"] = candidates
    viable = [candidate for candidate in candidates if candidate["meets_ad_constraint"]]
    if not viable:
        candidate_text = ", ".join(
            f"surface {candidate['surface']}={candidate['diameter_mm']} mm"
            for candidate in candidates
        )
        source = (
            f"surfaces {left.index}/{right.index}: 没有 MEMA 候选满足 "
            f"MD >= max(AD_left, AD_right) = {required_diameter:.9g} mm; {candidate_text}"
        )
        return None, source, "blocked", warnings, evidence

    dedicated_associations = {
        "exclusive_to_current_lens",
        "virtual_interface_side_for_current_lens",
    }
    dedicated_candidates = [
        candidate
        for candidate in candidates
        if candidate["association"] in dedicated_associations
    ]
    invalid_dedicated = [
        candidate
        for candidate in dedicated_candidates
        if not candidate["meets_ad_constraint"]
    ]
    if invalid_dedicated:
        dedicated_text = ", ".join(
            f"surface {candidate['surface']}={candidate['diameter_mm']} mm"
            for candidate in invalid_dedicated
        )
        source = (
            f"surfaces {left.index}/{right.index}: 本片独占外表面或虚拟界面侧的 MEMA "
            f"不满足 MD >= {required_diameter:.9g} mm，不能借用共享面值: {dedicated_text}"
        )
        evidence["decision"] = "blocked_invalid_dedicated_candidate"
        return None, source, "blocked", warnings, evidence

    clusters: list[dict[str, object]] = []
    for candidate in viable:
        value = float(candidate["diameter_mm"])
        cluster = next(
            (
                item
                for item in clusters
                if _diameters_match(float(item["diameter_mm"]), value)
            ),
            None,
        )
        if cluster is None:
            cluster = {"diameter_mm": value, "candidates": []}
            clusters.append(cluster)
        cluster["candidates"].append(candidate)

    selected_by_association = False
    if len(clusters) == 1:
        selected_cluster = clusters[0]
    else:
        dedicated_clusters = [
            cluster
            for cluster in clusters
            if any(
                candidate["association"] in dedicated_associations
                for candidate in cluster["candidates"]
            )
        ]
        selected_cluster = dedicated_clusters[0] if len(dedicated_clusters) == 1 else None

    if selected_cluster is None:
        viable_text = ", ".join(
            f"surface {candidate['surface']}={float(candidate['diameter_mm']):.9g} mm "
            f"({candidate['solve']}, {candidate['association']})"
            for candidate in viable
        )
        source = (
            f"surfaces {left.index}/{right.index}: MEMA 候选冲突且无法唯一归属；"
            f"它们均满足 MD >= {required_diameter:.9g} mm: {viable_text}"
        )
        evidence["decision"] = "blocked_ambiguous_viable_candidates"
        return None, source, "blocked", warnings, evidence

    if len(clusters) > 1:
        selected_by_association = True
    selected_value = float(selected_cluster["diameter_mm"])
    selected_candidates = selected_cluster["candidates"]
    for candidate in selected_candidates:
        candidate["selected"] = True

    selected_fixed = any(
        str(candidate["solve"]).lower() == "fixed" for candidate in selected_candidates
    )
    confidence = "high" if selected_fixed else "medium"
    if not selected_fixed:
        selected_surfaces = "/".join(
            str(candidate["surface"]) for candidate in selected_candidates
        )
        warnings.append(
            f"surface {selected_surfaces} 的候选 MEMA 未固定；使用 ZOS-API 已求值结果 "
            f"{selected_value:.9g} mm 可复现当前 ZMX，但需确认最终机械规格"
        )

    invalid_candidates = [
        candidate
        for candidate in candidates
        if candidate["usable"] and not candidate["meets_ad_constraint"]
    ]
    if invalid_candidates:
        confidence = "medium"
        invalid_surfaces = "/".join(
            str(candidate["surface"]) for candidate in invalid_candidates
        )
        warnings.append(
            f"surface {invalid_surfaces} 的 MEMA 小于当前镜片两侧 AD 下限，"
            "已作为物理不可能候选排除；需复核源 ZMX 的机械口径设置"
        )

    if selected_by_association:
        confidence = "medium"
        rejected_text = ", ".join(
            f"surface {candidate['surface']}={float(candidate['diameter_mm']):.9g} mm"
            for cluster in clusters
            if cluster is not selected_cluster
            for candidate in cluster["candidates"]
        )
        warnings.append(
            "共享胶合面存在不同的可行 MEMA；已采用本片独占外表面或虚拟界面侧的 "
            f"{selected_value:.9g} mm，未采用 {rejected_text}，需复核机械规格"
        )

    missing_candidates = [candidate for candidate in candidates if not candidate["usable"]]
    if missing_candidates:
        confidence = "medium"
        missing_surfaces = "/".join(
            str(candidate["surface"]) for candidate in missing_candidates
        )
        warnings.append(
            f"surface {missing_surfaces} 缺少可用 MEMA；当前 MD 仅由另一边界确定，需复核"
        )

    selected_text = ", ".join(
        f"surface {candidate['surface']} ({candidate['solve']}, {candidate['boundary_role']})"
        for candidate in selected_candidates
    )
    evidence["decision"] = {
        "diameter_mm": selected_value,
        "selected_surfaces": [candidate["surface"] for candidate in selected_candidates],
        "confidence": confidence,
    }
    source = (
        f"拓扑/约束判定选取 {selected_value:.9g} mm: {selected_text}; "
        f"满足 MD >= max(AD_left, AD_right) = {required_diameter:.9g} mm"
    )
    return selected_value, source, confidence, warnings, evidence


def _safe_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().strip(".")
    return cleaned or "ZemaxLens"


def _renderer_safe_stem(value: str, source_sha256: str) -> tuple[str, str]:
    cleaned = _safe_name(value)
    if all(ord(char) < 128 for char in cleaned):
        return cleaned, "source title/file stem is renderer-safe ASCII"
    return (
        f"ZMX{source_sha256[:8].upper()}",
        "source title/file stem contains glyphs unsupported by the renderer font; "
        "used deterministic ASCII source-hash alias",
    )


def _display_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in value)


def _truncate_display_width(value: str, maximum: int) -> str:
    result = []
    width = 0
    for char in value:
        char_width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if width + char_width > maximum:
            break
        result.append(char)
        width += char_width
    return "".join(result).rstrip("_.- ") or "Lens"


def _interval_connection(
    system: ExtractedSystem,
    left_start: int,
    right_start: int,
) -> dict[str, object] | None:
    left_boundary = left_start + 1
    if right_start == left_boundary:
        return {
            "kind": "direct_cemented_interface",
            "left_surface": left_boundary,
            "right_surface": right_start,
            "interface_surfaces": [left_boundary],
            "evidence": "adjacent GLAS intervals share one LDE surface",
        }
    if right_start <= left_boundary:
        return None

    gap_indexes = list(range(left_boundary, right_start))
    gap_surfaces = [system.surfaces[index] for index in gap_indexes]
    if any(_is_glass(surface.material) for surface in gap_surfaces):
        return None
    if any(not _zero_thickness(surface.thickness) for surface in gap_surfaces):
        return None

    interface_indexes = list(range(left_boundary, right_start + 1))
    interface_surfaces = [system.surfaces[index] for index in interface_indexes]
    reference = interface_surfaces[0]
    mismatches = []
    pair_checks = []
    for surface in interface_surfaces[1:]:
        type_matches = (
            reference.type_name.strip().lower() == surface.type_name.strip().lower()
        )
        radius_matches = _radii_match(reference.radius, surface.radius)
        no_tilt_decenter = not reference.tilt_decenter and not surface.tilt_decenter
        pair_checks.append(
            {
                "reference_surface": reference.index,
                "compared_surface": surface.index,
                "type_matches": type_matches,
                "radius_matches": radius_matches,
                "no_tilt_decenter": no_tilt_decenter,
            }
        )
        if not type_matches:
            mismatches.append(
                f"surface {reference.index}/{surface.index} 面型不同: "
                f"{reference.type_name!r} vs {surface.type_name!r}"
            )
        if not radius_matches:
            mismatches.append(
                f"surface {reference.index}/{surface.index} 曲率半径不同: "
                f"{reference.radius!r} vs {surface.radius!r}"
            )
        if not no_tilt_decenter:
            mismatches.append(
                f"surface {reference.index}/{surface.index} 存在 tilt/decenter"
            )
    coincidence_evidence = {
        "rule": "zero_thickness_coincident_virtual_interface_v1",
        "zero_axial_separation": sum(surface.thickness for surface in gap_surfaces),
        "all_gap_surfaces_non_glass": all(
            not _is_glass(surface.material) for surface in gap_surfaces
        ),
        "all_gap_thickness_zero": all(
            _zero_thickness(surface.thickness) for surface in gap_surfaces
        ),
        "all_surface_types_match": all(item["type_matches"] for item in pair_checks),
        "all_radii_match": all(item["radius_matches"] for item in pair_checks),
        "no_tilt_decenter": all(item["no_tilt_decenter"] for item in pair_checks),
        "zero_thickness_surfaces": [
            {
                "surface": surface.index,
                "thickness": surface.thickness,
                "material": surface.material,
            }
            for surface in gap_surfaces
        ],
        "duplicated_interface_surfaces": [
            {
                "surface": surface.index,
                "type": surface.type_name,
                "radius": surface.radius,
                "material": surface.material,
                "tilt_decenter": surface.tilt_decenter,
            }
            for surface in interface_surfaces
        ],
        "pair_checks": pair_checks,
    }
    if mismatches:
        return {
            "kind": "ambiguous_zero_thickness_interface",
            "left_surface": left_boundary,
            "right_surface": right_start,
            "interface_surfaces": interface_indexes,
            "zero_thickness_air_surfaces": gap_indexes,
            "coincidence_evidence": coincidence_evidence,
            "evidence": (
                "non-glass intervals have zero axial thickness, but duplicated interface "
                "geometry is not coincident"
            ),
            "blocked_reason": (
                "检测到零厚度非玻璃虚拟面候选，但无法证明为同一胶合界面: "
                + "; ".join(mismatches)
            ),
        }
    return {
        "kind": "virtual_cemented_interface",
        "left_surface": left_boundary,
        "right_surface": right_start,
        "interface_surfaces": interface_indexes,
        "zero_thickness_air_surfaces": gap_indexes,
        "coincidence_evidence": coincidence_evidence,
        "evidence": (
            "non-glass intervals have zero axial thickness and all duplicated surfaces have "
            "identical type/radius with no tilt or decenter"
        ),
    }


def _group_intervals(system: ExtractedSystem) -> list[dict[str, object]]:
    interval_starts = [
        index
        for index, surface in enumerate(system.surfaces[:-1])
        if not surface.is_object and not surface.is_image and _is_glass(surface.material)
    ]
    if not interval_starts:
        return []

    groups: list[dict[str, object]] = []
    current_intervals = [interval_starts[0]]
    current_connections: list[dict[str, object]] = []
    for start in interval_starts[1:]:
        connection = _interval_connection(system, current_intervals[-1], start)
        if connection is None:
            groups.append(
                {"intervals": current_intervals, "connections": current_connections}
            )
            current_intervals = [start]
            current_connections = []
            continue
        current_intervals.append(start)
        current_connections.append(connection)
    groups.append({"intervals": current_intervals, "connections": current_connections})
    return groups


def map_to_drafts(system: ExtractedSystem) -> list[DrawingDraft]:
    common_blockers = []
    if system.mode.lower() != "sequential":
        common_blockers.append(f"仅支持 Sequential，当前为 {system.mode}")
    if system.unit_to_mm is None:
        common_blockers.append(f"无法识别或换算 LensUnits: {system.lens_units}")
    if system.configuration_count != 1:
        common_blockers.append(
            f"检测到 {system.configuration_count} 个配置；当前原型要求单一配置"
        )

    groups = _group_intervals(system)
    if not groups:
        return [
            DrawingDraft(
                group_index=1,
                surface_range=[],
                row={},
                provenance=[],
                status="blocked",
                confidence="blocked",
                blockers=common_blockers + ["未识别到普通玻璃区间"],
            )
        ]

    factor = system.unit_to_mm or 1.0
    source_stem = _safe_name(Path(system.source_file).stem)
    original_title_stem = _safe_name(system.title) if system.title else source_stem
    title_stem, part_name_rule = _renderer_safe_stem(
        original_title_stem,
        system.source_sha256,
    )
    drafts = []
    for group_index, group in enumerate(groups, start=1):
        intervals = list(group["intervals"])
        connections = list(group["connections"])
        group_suffix = f"_G{group_index:02d}"
        part_name_base = _truncate_display_width(title_stem, 15 - _display_width(group_suffix))
        row = {
            "PartName": f"{part_name_base}{group_suffix}",
            "PartNo": f"AUTO-{system.source_sha256[:8].upper()}-G{group_index:02d}",
            "SavePdfFolder": "Save PDF",
            "MfrPdfFolder": "Mfr PDF",
        }
        provenance = [
            Provenance(
                "PartName",
                f"{part_name_rule} + deterministic group index",
                system.title or Path(system.source_file).stem,
                row["PartName"],
                "medium",
            ),
            Provenance(
                "PartNo",
                "generated from source SHA-256; not a manufacturing part number",
                system.source_sha256,
                row["PartNo"],
                "generated",
            ),
        ]
        warnings = ["PartNo 为自动审计编号，不等同于正式物料编码"]
        blockers = list(common_blockers)
        if len(intervals) > 3:
            blockers.append(f"连续胶合组含 {len(intervals)} 片，当前绘图模型最多支持 3 片")

        final_boundary_index = intervals[-1] + 1
        if final_boundary_index >= len(system.surfaces):
            blockers.append("玻璃区间缺少右边界面")
            final_boundary_index = len(system.surfaces) - 1

        boundary_specs: list[dict[str, object]] = [
            {
                "kind": "group_outer_left",
                "surface_indexes": [intervals[0]],
                "previous_lens_surface": None,
                "next_lens_surface": intervals[0],
                "evidence": "first glass interval starts at the group outer surface",
            }
        ]
        for connection in connections:
            boundary_specs.append(
                {
                    "kind": connection["kind"],
                    "surface_indexes": list(connection["interface_surfaces"]),
                    "previous_lens_surface": connection["left_surface"],
                    "next_lens_surface": connection["right_surface"],
                    "evidence": connection["evidence"],
                    "zero_thickness_air_surfaces": list(
                        connection.get("zero_thickness_air_surfaces", [])
                    ),
                }
            )
        boundary_specs.append(
            {
                "kind": "group_outer_right",
                "surface_indexes": [final_boundary_index],
                "previous_lens_surface": final_boundary_index,
                "next_lens_surface": None,
                "evidence": "the final glass interval ends at the group outer surface",
            }
        )
        group_type = {
            1: "singlet",
            2: "cemented_doublet",
            3: "cemented_triplet",
        }.get(len(intervals), f"unsupported_{len(intervals)}_element_group")
        ambiguous_connections = [
            connection
            for connection in connections
            if connection["kind"] == "ambiguous_zero_thickness_interface"
        ]
        if ambiguous_connections:
            group_type = "ambiguous_zero_thickness_compound"
            blockers.extend(
                str(connection["blocked_reason"])
                for connection in ambiguous_connections
            )
        topology = {
            "model": "glass_interval_topology_v3",
            "group_type": group_type,
            "grouping_rule": (
                "Zemax GLAS applies to the medium after a surface. Directly adjacent glass "
                "intervals are cemented. Glass intervals separated only by zero-thickness "
                "non-glass surfaces are also cemented when the duplicated interface surfaces "
                "have identical type/radius and no tilt/decenter."
            ),
            "boundary_surfaces": [
                {
                    "surface_indexes": list(boundary["surface_indexes"]),
                    "role": boundary["kind"],
                    "previous_lens_surface": boundary["previous_lens_surface"],
                    "next_lens_surface": boundary["next_lens_surface"],
                    "evidence": boundary["evidence"],
                    "ad_fields": (
                        ["Lens1.AD_left"]
                        if position == 0
                        else [f"Lens{len(intervals)}.AD_right"]
                        if position == len(intervals)
                        else [
                            f"Lens{position}.AD_right",
                            f"Lens{position + 1}.AD_left",
                        ]
                    ),
                    "legacy_ad_field": f"AD{position + 1}",
                    "radius_field": f"R{position + 1}",
                }
                for position, boundary in enumerate(boundary_specs)
            ],
            "connections": connections,
            "glass_intervals": [],
        }

        physical_boundary_indexes = sorted(
            {
                index
                for boundary in boundary_specs
                for index in boundary["surface_indexes"]
            }
        )
        for boundary_index in physical_boundary_indexes:
            boundary = system.surfaces[boundary_index]
            if boundary.type_name.strip().lower() != SUPPORTED_SURFACE_TYPE:
                blockers.append(
                    f"surface {boundary.index} 类型 {boundary.type_name!r} 不受球面绘图模型支持"
                )
            if boundary.tilt_decenter:
                blockers.append(
                    f"surface {boundary.index} 存在 tilt/decenter: {boundary.tilt_decenter}"
                )
            if boundary.material.strip().upper() == "MIRROR":
                blockers.append(f"surface {boundary.index} 为 MIRROR")

        prism_signature = (
            len(intervals) == 1
            and system.surfaces[intervals[0]].material.strip().upper() == "H-K9L"
            and not common_blockers
            and all(
                system.surfaces[index].type_name.strip().lower()
                == SUPPORTED_SURFACE_TYPE
                and not system.surfaces[index].tilt_decenter
                and _is_plane_radius(system.surfaces[index].radius)
                for index in physical_boundary_indexes
            )
        )

        lens_geometries: list[LensGeometry] = []
        for lens_position, surface_index in enumerate(intervals, start=1):
            left = system.surfaces[surface_index]
            left_boundary = boundary_specs[lens_position - 1]
            right_boundary = boundary_specs[lens_position]
            left_surface_index = int(left_boundary["next_lens_surface"])
            right_surface_index = int(right_boundary["previous_lens_surface"])
            left_md_surface = system.surfaces[left_surface_index]
            right_md_surface = system.surfaces[right_surface_index]
            glass_field = f"Glass{lens_position}"
            thickness_field = f"T{lens_position}"
            md_field = f"MD{lens_position}"
            row[glass_field] = left.material
            material_blocker = _material_blocker(left.material)
            if material_blocker:
                blockers.append(f"{glass_field}: {material_blocker}")
            provenance.append(
                Provenance(
                    glass_field,
                    f"surface {left.index} Material",
                    left.material,
                    left.material,
                    "blocked" if material_blocker else "high",
                )
            )
            thickness = left.thickness * factor
            if not _finite_positive(thickness):
                blockers.append(
                    f"{thickness_field}: surface {left.index} Thickness 不是有限正数"
                )
            else:
                row[thickness_field] = thickness
            provenance.append(
                Provenance(
                    thickness_field,
                    f"surface {left.index} evaluated Thickness x {factor:g}",
                    left.thickness,
                    thickness,
                    "high" if _finite_positive(thickness) else "blocked",
                )
            )

            side_ads: dict[str, float | None] = {}
            for side, surface in (
                ("left", left_md_surface),
                ("right", right_md_surface),
            ):
                ad, source, ad_confidence = _ad_candidate(surface, factor)
                field = f"Lens{lens_position}.AD_{side}"
                side_ads[side] = ad
                raw_ad = {
                    "surface": surface.index,
                    "semi_diameter": surface.semi_diameter,
                    "explicit_aperture_radius": surface.explicit_aperture_radius,
                    "aperture_type": surface.aperture_type,
                    "solve": surface.solves.get("semi_diameter", "Unknown"),
                }
                if ad is None:
                    blockers.append(f"{field}: {source}")
                elif ad_confidence != "high":
                    blockers.append(
                        f"{field}: {source} 不是显式圆孔径或 Fixed SemiDiameter，"
                        "仅可作为预览候选"
                    )
                provenance.append(
                    Provenance(field, source, raw_ad, ad, ad_confidence)
                )

            lens_count = len(intervals)
            left_role = (
                "group_outer_left"
                if lens_position == 1
                else str(left_boundary["kind"]) + "_from_previous"
            )
            right_role = (
                str(right_boundary["kind"]) + "_to_next"
                if lens_position < lens_count
                else "group_outer_right"
            )
            left_association = (
                "exclusive_to_current_lens"
                if lens_position == 1
                else "virtual_interface_side_for_current_lens"
                if left_boundary["kind"] == "virtual_cemented_interface"
                else "shared_with_adjacent_lens"
            )
            right_association = (
                "exclusive_to_current_lens"
                if lens_position == lens_count
                else "virtual_interface_side_for_current_lens"
                if right_boundary["kind"] == "virtual_cemented_interface"
                else "shared_with_adjacent_lens"
            )
            md, source, confidence, md_warnings, md_evidence = _infer_md(
                left_md_surface,
                right_md_surface,
                side_ads["left"],
                side_ads["right"],
                factor,
                left_role=left_role,
                right_role=right_role,
                left_association=left_association,
                right_association=right_association,
            )
            warnings.extend(md_warnings)
            if md is None:
                blockers.append(f"{md_field}: {source}")
            else:
                row[md_field] = md
            provenance.append(
                Provenance(
                    md_field,
                    source,
                    md_evidence,
                    md,
                    confidence,
                )
            )
            lens_geometries.append(
                LensGeometry(
                    lens_position=lens_position,
                    glass=left.material,
                    T=thickness if _finite_positive(thickness) else None,
                    R_left=_radius_mm(left_md_surface, factor),
                    R_right=_radius_mm(right_md_surface, factor),
                    MD=md,
                    AD_left=side_ads["left"],
                    AD_right=side_ads["right"],
                    left_surface=left_surface_index,
                    right_surface=right_surface_index,
                )
            )
            topology["glass_intervals"].append(
                {
                    "lens_position": lens_position,
                    "material": left.material,
                    "material_surface": left.index,
                    "left_surface": left_surface_index,
                    "right_surface": right_surface_index,
                    "left_boundary_role": left_role,
                    "right_boundary_role": right_role,
                    "left_boundary_association": left_association,
                    "right_boundary_association": right_association,
                    "ad_fields": [
                        f"Lens{lens_position}.AD_left",
                        f"Lens{lens_position}.AD_right",
                    ],
                    "md_field": md_field,
                    "md_inference": md_evidence,
                }
            )

        legacy_row_compatible = True
        for boundary_position, boundary in enumerate(boundary_specs, start=1):
            if boundary_position == 1:
                side_values = [lens_geometries[0].AD_left]
            elif boundary_position == len(boundary_specs):
                side_values = [lens_geometries[-1].AD_right]
            else:
                side_values = [
                    lens_geometries[boundary_position - 2].AD_right,
                    lens_geometries[boundary_position - 1].AD_left,
                ]
            field = f"AD{boundary_position}"
            compatible = (
                all(value is not None for value in side_values)
                and all(
                    _diameters_match(float(side_values[0]), float(value))
                    for value in side_values[1:]
                )
            )
            topology["boundary_surfaces"][boundary_position - 1][
                "ad_values_mm"
            ] = side_values
            topology["boundary_surfaces"][boundary_position - 1][
                "legacy_ad_compatible"
            ] = compatible
            if compatible:
                row[field] = float(side_values[0])
            else:
                row[field] = None
                legacy_row_compatible = False
                if all(value is not None for value in side_values):
                    warnings.append(
                        f"{field} 对应界面两侧 AD 不同（"
                        + ", ".join(f"{float(value):.9g} mm" for value in side_values)
                        + "）；旧批量 row 不能表达该几何，已保留 lenses[].AD_left/AD_right 分侧值"
                    )

        for radius_position, boundary in enumerate(boundary_specs, start=1):
            surfaces = [
                system.surfaces[index] for index in boundary["surface_indexes"]
            ]
            reference = surfaces[0]
            if any(not _radii_match(reference.radius, surface.radius) for surface in surfaces[1:]):
                value = None
                confidence = "blocked"
                source = (
                    "重合虚拟面曲率不一致: "
                    + ", ".join(
                        f"surface {surface.index}={_radius_mm(surface, factor):.9g} mm"
                        for surface in surfaces
                    )
                )
                blockers.append(f"R{radius_position}: {source}")
            else:
                value = _radius_mm(reference, factor)
                confidence = "high"
                source = (
                    ", ".join(f"surface {surface.index}" for surface in surfaces)
                    + f" evaluated Radius x {factor:g}; infinity maps to 0"
                )
                row[f"R{radius_position}"] = value
            provenance.append(
                Provenance(
                    f"R{radius_position}",
                    source,
                    [
                        {"surface": surface.index, "radius": surface.radius}
                        for surface in surfaces
                    ],
                    value,
                    confidence,
                )
            )

        for lens in lens_geometries:
            lens.R_left = row.get(f"R{lens.lens_position}")
            lens.R_right = row.get(f"R{lens.lens_position + 1}")

        for lens in lens_geometries:
            for side, ad in (("left", lens.AD_left), ("right", lens.AD_right)):
                if lens.MD is not None and ad is not None and ad > lens.MD + 1e-9:
                    blockers.append(
                        f"Lens{lens.lens_position}.AD_{side}={ad:.9g} mm 大于 "
                        f"MD{lens.lens_position}={lens.MD:.9g} mm"
                    )

        geometry_provenance = [
            item
            for item in provenance
            if item.field.startswith(("Glass", "T", "R", "MD", "AD", "Lens"))
        ]
        confidence = (
            "medium"
            if any(item.confidence != "high" for item in geometry_provenance)
            else "high"
        )
        if prism_signature:
            material_surface = system.surfaces[intervals[0]]
            topology["group_type"] = "excluded_prism"
            topology["exclusion"] = {
                "rule": "h-k9l_plane_plane_prism_exclusion_v1",
                "reason": "H-K9L single element with two plane surfaces does not require a lens drawing",
                "material": material_surface.material,
                "thickness_mm": material_surface.thickness * factor,
                "boundary_surfaces": physical_boundary_indexes,
                "boundary_radii": [
                    system.surfaces[index].radius
                    for index in physical_boundary_indexes
                ],
                "delivery_notification_required": True,
            }
            warnings.append(
                "检测到 H-K9L 双平面棱镜，已按业务规则剔除出图；交付时必须明确告知"
            )
            blockers = []
            confidence = "high"
            status = "excluded"
        else:
            status = "blocked" if blockers else "accepted"
        if blockers:
            confidence = "blocked"
        drafts.append(
            DrawingDraft(
                group_index=group_index,
                surface_range=[intervals[0], final_boundary_index],
                row=row,
                provenance=provenance,
                status=status,
                confidence=confidence,
                warnings=sorted(set(warnings)),
                blockers=sorted(set(blockers)),
                topology=topology,
                lenses=lens_geometries,
                legacy_row_compatible=legacy_row_compatible,
            )
        )
    return drafts
