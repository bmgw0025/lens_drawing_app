from __future__ import annotations

import copy
import math
import re
from typing import Any

from .mapper import _ad_candidate, _diameters_match, map_to_drafts
from .models import DrawingDraft, ExtractedSystem, SurfaceRecord


class GeometryResolutionError(ValueError):
    pass


_AD_FIELD = re.compile(r"^Lens(\d+)\.AD_(left|right)$")
_MD_FIELD = re.compile(r"^MD(\d+)$")


def _restore_number(value: Any) -> Any:
    if value == "Infinity":
        return math.inf
    if value == "-Infinity":
        return -math.inf
    if value == "NaN":
        return math.nan
    return value


def system_from_payload(payload: dict[str, Any]) -> ExtractedSystem:
    surfaces = []
    for raw_surface in payload["surfaces"]:
        item = dict(raw_surface)
        for key in (
            "radius",
            "thickness",
            "semi_diameter",
            "mechanical_semi_diameter",
            "explicit_aperture_radius",
        ):
            item[key] = _restore_number(item.get(key))
        surfaces.append(SurfaceRecord(**item))
    return ExtractedSystem(
        **{key: value for key, value in payload.items() if key != "surfaces"},
        surfaces=surfaces,
    )


def _provenance(draft: DrawingDraft, field: str):
    return next((item for item in draft.provenance if item.field == field), None)


def _boundary_for_field(draft: DrawingDraft, field: str) -> dict[str, Any] | None:
    return next(
        (
            boundary
            for boundary in draft.topology.get("boundary_surfaces", [])
            if field in boundary.get("ad_fields", [])
        ),
        None,
    )


def _ad_candidates(
    system: ExtractedSystem,
    draft: DrawingDraft,
    field: str,
    factor: float,
) -> tuple[list[dict[str, Any]], bool]:
    match = _AD_FIELD.fullmatch(field)
    if match is None:
        return [], False
    lens_position = int(match.group(1))
    side = match.group(2)
    lens = draft.lenses[lens_position - 1]
    physical_surface = lens.left_surface if side == "left" else lens.right_surface
    boundary = _boundary_for_field(draft, field)
    indexes = [physical_surface]
    if boundary and boundary.get("role") == "virtual_cemented_interface":
        indexes = list(boundary.get("surface_indexes", indexes))
    candidates = []
    for surface_index in dict.fromkeys(indexes):
        surface = system.surfaces[int(surface_index)]
        value, source, confidence = _ad_candidate(surface, factor)
        source_kind = (
            "explicit_aperture"
            if surface.explicit_aperture_radius is not None
            and surface.explicit_aperture_radius > 0
            else "semi_diameter"
        )
        candidates.append(
            {
                "candidate_id": (
                    f"ad-g{draft.group_index}-l{lens_position}-{side}-s{surface.index}"
                ),
                "field": field,
                "source_kind": source_kind,
                "surface": surface.index,
                "diameter_mm": value,
                "raw_radius": (
                    surface.explicit_aperture_radius
                    if source_kind == "explicit_aperture"
                    else surface.semi_diameter
                ),
                "aperture_type": surface.aperture_type,
                "solve": surface.solves.get("semi_diameter", "Unknown"),
                "confidence": confidence,
                "association": (
                    "current_lens_boundary"
                    if surface.index == physical_surface
                    else "adjacent_virtual_surface"
                ),
                "source": source,
                "eligible": value is not None,
                "excluded_reason": None if value is not None else "missing usable AD",
            }
        )
    provenance = _provenance(draft, field)
    required = bool(
        (boundary and boundary.get("role") == "virtual_cemented_interface")
        or (provenance is not None and provenance.confidence != "high")
        or len([item for item in candidates if item["eligible"]]) > 1
    )
    return candidates, required


def _md_candidates(
    draft: DrawingDraft,
    lens_position: int,
) -> tuple[list[dict[str, Any]], bool]:
    field = f"MD{lens_position}"
    provenance = _provenance(draft, field)
    evidence = provenance.raw_value if provenance is not None else {}
    raw_candidates = evidence.get("candidates", []) if isinstance(evidence, dict) else []
    clusters: list[dict[str, Any]] = []
    for raw in raw_candidates:
        value = raw.get("diameter_mm")
        if value is None:
            continue
        cluster = next(
            (
                item
                for item in clusters
                if _diameters_match(float(item["diameter_mm"]), float(value))
            ),
            None,
        )
        if cluster is None:
            cluster = {
                "candidate_id": f"md-g{draft.group_index}-l{lens_position}-c{len(clusters) + 1}",
                "field": field,
                "source_kind": "mechanical_semi_diameter",
                "diameter_mm": float(value),
                "confidence": (
                    provenance.confidence if provenance is not None else "blocked"
                ),
                "sources": [],
                "eligible": True,
            }
            clusters.append(cluster)
        cluster["sources"].append(
            {
                "surface": raw.get("surface"),
                "side": raw.get("side"),
                "boundary_role": raw.get("boundary_role"),
                "association": raw.get("association"),
                "raw_mechanical_semi_diameter": raw.get(
                    "raw_mechanical_semi_diameter"
                ),
                "solve": raw.get("solve"),
                "meets_current_ad_constraint": bool(raw.get("meets_ad_constraint")),
            }
        )
    interval = next(
        (
            item
            for item in draft.topology.get("glass_intervals", [])
            if item.get("lens_position") == lens_position
        ),
        {},
    )
    touches_virtual = any(
        "virtual_cemented_interface" in str(interval.get(key, ""))
        for key in ("left_boundary_role", "right_boundary_role")
    )
    required = bool(
        touches_virtual
        or provenance is None
        or provenance.confidence != "high"
        or len(clusters) > 1
    )
    return clusters, required


def build_geometry_cases(
    system: ExtractedSystem,
    drafts: list[DrawingDraft] | None = None,
    *,
    task_id: str,
) -> dict[str, Any]:
    drafts = drafts or map_to_drafts(system)
    factor = system.unit_to_mm or 1.0
    cases = []
    for draft in drafts:
        field_candidates: dict[str, list[dict[str, Any]]] = {}
        required_fields: list[str] = []
        topology_candidates: list[dict[str, Any]] = []
        topology_required = False

        virtual_connections = [
            connection
            for connection in draft.topology.get("connections", [])
            if connection.get("kind") == "virtual_cemented_interface"
        ]
        if virtual_connections:
            surfaces = "-".join(
                str(index)
                for connection in virtual_connections
                for index in connection.get("interface_surfaces", [])
            )
            topology_candidates.append(
                {
                    "candidate_id": f"topology-g{draft.group_index}-fold-{surfaces}",
                    "source_kind": "zosapi_virtual_interface",
                    "action": "fold_zero_thickness_virtual_interfaces",
                    "group_type": draft.topology.get("group_type"),
                    "connections": virtual_connections,
                    "eligible": True,
                }
            )
            topology_required = True

        for connection in draft.topology.get("connections", []):
            if connection.get("kind") == "ambiguous_zero_thickness_interface":
                topology_candidates.append(
                    {
                        "candidate_id": (
                            f"topology-g{draft.group_index}-rejected-"
                            + "-".join(
                                str(index)
                                for index in connection.get("interface_surfaces", [])
                            )
                        ),
                        "source_kind": "zosapi_virtual_interface",
                        "action": "fold_zero_thickness_virtual_interfaces",
                        "eligible": False,
                        "excluded_reason": connection.get("blocked_reason"),
                        "connection": connection,
                    }
                )

        for lens in draft.lenses:
            for side in ("left", "right"):
                field = f"Lens{lens.lens_position}.AD_{side}"
                candidates, required = _ad_candidates(system, draft, field, factor)
                field_candidates[field] = candidates
                if required:
                    required_fields.append(field)
            field = f"MD{lens.lens_position}"
            candidates, required = _md_candidates(draft, lens.lens_position)
            field_candidates[field] = candidates
            if required:
                required_fields.append(field)

        resolvable_fields = {
            field
            for field in required_fields
            if any(item.get("eligible") for item in field_candidates.get(field, []))
        }
        hard_blockers = [
            blocker
            for blocker in draft.blockers
            if str(blocker).split(":", 1)[0] not in resolvable_fields
        ]
        for field in required_fields:
            if not any(item.get("eligible") for item in field_candidates.get(field, [])):
                hard_blockers.append(f"{field}: 没有可选择的 ZOS-API 候选")
        cases.append(
            {
                "case_id": f"group-{draft.group_index}",
                "group_index": draft.group_index,
                "surface_range": draft.surface_range,
                "group_type": draft.topology.get("group_type"),
                "resolution_required": bool(topology_required or required_fields),
                "topology_selection_required": topology_required,
                "topology_candidates": topology_candidates,
                "required_field_selections": sorted(set(required_fields)),
                "field_candidates": field_candidates,
                "hard_blockers": sorted(set(hard_blockers)),
            }
        )

    return {
        "schema_version": "1.0",
        "task_id": task_id,
        "source_sha256": system.source_sha256,
        "rules": [
            "Select candidate IDs only; never submit Glass/T/R/AD/MD numeric values.",
            "AD candidates originate only from explicit aperture or evaluated SemiDiameter.",
            "MD candidates originate only from MechanicalSemiDiameter and must cover both selected AD values.",
            "A virtual interface is eligible only when zero-thickness/type/radius/coordinate checks passed.",
        ],
        "resolution_required": any(item["resolution_required"] for item in cases),
        "hard_blocked_groups": [
            item["group_index"] for item in cases if item["hard_blockers"]
        ],
        "cases": cases,
    }


def _selected_candidate(
    case: dict[str, Any], field: str, candidate_id: str
) -> dict[str, Any]:
    candidates = case.get("field_candidates", {}).get(field, [])
    candidate = next(
        (item for item in candidates if item.get("candidate_id") == candidate_id),
        None,
    )
    if candidate is None:
        raise GeometryResolutionError(
            f"{case['case_id']}.{field} 引用了未知候选 {candidate_id}"
        )
    if candidate.get("eligible") is not True:
        raise GeometryResolutionError(
            f"{case['case_id']}.{field} 候选 {candidate_id} 已被确定性规则排除"
        )
    return candidate


def _lens_ad_value(draft: DrawingDraft, field: str) -> float | None:
    match = _AD_FIELD.fullmatch(field)
    if match is None:
        return None
    lens = draft.lenses[int(match.group(1)) - 1]
    return lens.AD_left if match.group(2) == "left" else lens.AD_right


def _rebuild_legacy_ad(draft: DrawingDraft) -> None:
    draft.legacy_row_compatible = True
    for boundary in draft.topology.get("boundary_surfaces", []):
        fields = boundary.get("ad_fields", [])
        values = [_lens_ad_value(draft, field) for field in fields]
        compatible = bool(values) and all(value is not None for value in values)
        if compatible:
            compatible = all(
                _diameters_match(float(values[0]), float(value))
                for value in values[1:]
            )
        boundary["ad_values_mm"] = values
        boundary["legacy_ad_compatible"] = compatible
        legacy_field = boundary.get("legacy_ad_field")
        if legacy_field:
            draft.row[legacy_field] = float(values[0]) if compatible else None
        if not compatible:
            draft.legacy_row_compatible = False


def apply_geometry_decision(
    drafts: list[DrawingDraft],
    geometry_cases: dict[str, Any],
    decision: dict[str, Any],
) -> list[DrawingDraft]:
    if geometry_cases.get("hard_blocked_groups"):
        raise GeometryResolutionError(
            "几何包含确定性阻断组，不能通过候选选择解除: "
            + ", ".join(str(item) for item in geometry_cases["hard_blocked_groups"])
        )
    if not isinstance(decision, dict):
        raise GeometryResolutionError("几何 decision 必须是 JSON 对象")
    allowed_top = {"schema_version", "task_id", "cases"}
    if set(decision) - allowed_top:
        raise GeometryResolutionError(
            "几何 decision 含禁止字段: "
            + ", ".join(sorted(set(decision) - allowed_top))
        )
    if decision.get("schema_version") != "1.0":
        raise GeometryResolutionError("几何 decision.schema_version 必须是 1.0")
    if decision.get("task_id") != geometry_cases.get("task_id"):
        raise GeometryResolutionError("几何 decision.task_id 与任务不一致")
    submitted_cases = decision.get("cases")
    if not isinstance(submitted_cases, list):
        raise GeometryResolutionError("几何 decision.cases 必须是数组")

    required_cases = {
        item["case_id"]: item
        for item in geometry_cases.get("cases", [])
        if item.get("resolution_required")
    }
    submitted_by_id: dict[str, dict[str, Any]] = {}
    for item in submitted_cases:
        if not isinstance(item, dict):
            raise GeometryResolutionError("几何 decision.cases 项必须是对象")
        allowed_case = {
            "case_id",
            "topology_candidate_id",
            "field_selections",
            "reason",
        }
        if set(item) - allowed_case:
            raise GeometryResolutionError(
                f"几何 case {item.get('case_id', '')} 含禁止字段: "
                + ", ".join(sorted(set(item) - allowed_case))
            )
        case_id = str(item.get("case_id", ""))
        if not case_id or case_id in submitted_by_id:
            raise GeometryResolutionError("几何 decision case_id 缺失或重复")
        submitted_by_id[case_id] = item
    if set(submitted_by_id) != set(required_cases):
        missing = sorted(set(required_cases) - set(submitted_by_id))
        extra = sorted(set(submitted_by_id) - set(required_cases))
        raise GeometryResolutionError(
            "几何 decision cases 不完整"
            + (f"；缺少 {', '.join(missing)}" if missing else "")
            + (f"；多余 {', '.join(extra)}" if extra else "")
        )

    resolved = copy.deepcopy(drafts)
    draft_by_group = {draft.group_index: draft for draft in resolved}
    for case_id, case in required_cases.items():
        submitted = submitted_by_id[case_id]
        if not str(submitted.get("reason", "")).strip():
            raise GeometryResolutionError(f"{case_id}.reason 不能为空")
        draft = draft_by_group[int(case["group_index"])]
        customer_confirmations: list[dict[str, Any]] = []

        if case.get("topology_selection_required"):
            candidate_id = str(submitted.get("topology_candidate_id", ""))
            topology = next(
                (
                    item
                    for item in case.get("topology_candidates", [])
                    if item.get("candidate_id") == candidate_id
                ),
                None,
            )
            if topology is None or topology.get("eligible") is not True:
                raise GeometryResolutionError(
                    f"{case_id}.topology_candidate_id 不是可用候选"
                )
            draft.topology["agent_resolution"] = {
                "candidate_id": candidate_id,
                "reason": submitted["reason"],
            }
            eligible_topologies = [
                item
                for item in case.get("topology_candidates", [])
                if item.get("eligible") is True
            ]
            if len(eligible_topologies) > 1:
                confirmation_id = f"{case_id}:topology:{candidate_id}"
                customer_confirmations.append(
                    {
                        "confirmation_id": confirmation_id,
                        "case_id": case_id,
                        "group_index": int(case["group_index"]),
                        "field": "topology",
                        "candidate_id": candidate_id,
                        "reason_code": "multiple_eligible_topologies",
                        "prompt": (
                            f"请确认 {case_id} 采用拓扑候选 {candidate_id}；"
                            "源 ZMX 同时存在多个满足硬约束的拓扑候选。"
                        ),
                    }
                )
        elif submitted.get("topology_candidate_id") not in (None, ""):
            raise GeometryResolutionError(f"{case_id} 不需要 topology_candidate_id")

        selections = submitted.get("field_selections")
        if not isinstance(selections, dict):
            raise GeometryResolutionError(f"{case_id}.field_selections 必须是对象")
        required_fields = set(case.get("required_field_selections", []))
        if set(selections) != required_fields:
            raise GeometryResolutionError(
                f"{case_id}.field_selections 必须恰好包含: "
                + ", ".join(sorted(required_fields))
            )
        selected_payloads: dict[str, dict[str, Any]] = {}
        for field, candidate_id in selections.items():
            candidate = _selected_candidate(case, field, str(candidate_id))
            selected_payloads[field] = candidate
            provenance = _provenance(draft, field)
            if provenance is None:
                raise GeometryResolutionError(f"{field} 缺少原始 provenance")
            candidate_confidence = str(
                candidate.get("confidence", provenance.confidence)
            ).lower()
            if candidate_confidence != "high" or provenance.confidence != "high":
                confirmation_id = (
                    f"{case_id}:{field}:{candidate['candidate_id']}"
                )
                value = candidate.get("diameter_mm")
                value_text = (
                    f"{float(value):.9g} mm" if value is not None else "无数值"
                )
                customer_confirmations.append(
                    {
                        "confirmation_id": confirmation_id,
                        "case_id": case_id,
                        "group_index": int(case["group_index"]),
                        "field": field,
                        "candidate_id": candidate["candidate_id"],
                        "diameter_mm": value,
                        "original_confidence": provenance.confidence,
                        "candidate_confidence": candidate_confidence,
                        "reason_code": "selected_candidate_not_high_confidence",
                        "prompt": (
                            f"请确认 {case_id} 的 {field} 使用候选 "
                            f"{candidate['candidate_id']}（{value_text}）；"
                            "该 ZOS-API 候选不是固定高置信机械/口径数据。"
                        ),
                    }
                )
            ad_match = _AD_FIELD.fullmatch(field)
            md_match = _MD_FIELD.fullmatch(field)
            if ad_match:
                if candidate.get("source_kind") not in {
                    "explicit_aperture",
                    "semi_diameter",
                }:
                    raise GeometryResolutionError(f"{field} 选择了非 AD 来源候选")
                lens = draft.lenses[int(ad_match.group(1)) - 1]
                value = candidate.get("diameter_mm")
                if value is None:
                    raise GeometryResolutionError(f"{field} 候选缺少直径值")
                if ad_match.group(2) == "left":
                    lens.AD_left = float(value)
                else:
                    lens.AD_right = float(value)
            elif md_match:
                if candidate.get("source_kind") != "mechanical_semi_diameter":
                    raise GeometryResolutionError(f"{field} 选择了非 MEMA 来源候选")
                lens = draft.lenses[int(md_match.group(1)) - 1]
                lens.MD = float(candidate["diameter_mm"])
                draft.row[field] = lens.MD
            else:
                raise GeometryResolutionError(f"不支持的候选字段: {field}")

            provenance.source = (
                f"Agent selected candidate {candidate['candidate_id']} from immutable "
                "ZOS-API candidate set"
            )
            provenance.raw_value = {
                "candidate_id": candidate["candidate_id"],
                "candidate": candidate,
            }
            provenance.converted_value = candidate["diameter_mm"]
            provenance.confidence = "agent-selected"
            draft.blockers = [
                blocker
                for blocker in draft.blockers
                if str(blocker).split(":", 1)[0] != field
            ]

        _rebuild_legacy_ad(draft)
        for lens in draft.lenses:
            if lens.AD_left is None or lens.AD_right is None or lens.MD is None:
                raise GeometryResolutionError(
                    f"group {draft.group_index} lens {lens.lens_position} 几何仍不完整"
                )
            minimum = max(lens.AD_left, lens.AD_right)
            if lens.MD + max(1e-6, 1e-6 * minimum) < minimum:
                raise GeometryResolutionError(
                    f"MD{lens.lens_position}={lens.MD:.9g} mm 小于所选两侧 AD "
                    f"{minimum:.9g} mm"
                )
        if draft.blockers:
            raise GeometryResolutionError(
                f"group {draft.group_index} 仍有不可解除阻断: "
                + "; ".join(draft.blockers)
            )
        if draft.status != "excluded":
            draft.status = "accepted"
            draft.confidence = "agent-selected"
        draft.topology["selected_field_candidates"] = {
            field: candidate["candidate_id"]
            for field, candidate in selected_payloads.items()
        }
        draft.topology["required_customer_confirmations"] = sorted(
            customer_confirmations,
            key=lambda item: item["confirmation_id"],
        )
    return resolved
