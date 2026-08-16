from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROCESS_FIELD_SPECS: dict[str, dict[str, Any]] = {
    "chamfer_mode": {"type": "enum", "values": ["auto", "manual"], "label": "倒角模式"},
    "chamfer_left": {"type": "number", "minimum": 0, "unit": "mm", "label": "左倒角"},
    "chamfer_right": {"type": "number", "minimum": 0, "unit": "mm", "label": "右倒角"},
    "t_tol": {"type": "number", "minimum": 0, "unit": "mm", "label": "中心厚度公差"},
    "sag_tol": {"type": "number", "minimum": 0, "unit": "mm", "label": "矢高公差"},
    "dia_tol_pos_upper": {"type": "number", "minimum": 0, "unit": "mm", "label": "定位直径上偏差"},
    "dia_tol_pos_lower": {"type": "number", "minimum": 0, "unit": "mm", "label": "定位直径下偏差"},
    "dia_tol_nonpos_upper": {"type": "number", "minimum": 0, "unit": "mm", "label": "非定位直径上偏差"},
    "dia_tol_nonpos_lower": {"type": "number", "minimum": 0, "unit": "mm", "label": "非定位直径下偏差"},
    "cemented_ref_lens": {"type": "integer", "minimum": 1, "maximum": 3, "label": "胶合定位镜片序号"},
    "proc_c_single": {"type": "string", "label": "单片页偏心 C"},
    "proc_c_assembly": {"type": "string", "label": "胶合总图偏心 C"},
    "proc_surface_defect": {"type": "string", "label": "表面疵病等级 B"},
    "proc_N_mode": {"type": "enum", "values": ["auto", "manual"], "label": "光圈数 N 模式"},
    "proc_N_manual": {"type": "number", "exclusive_minimum": 0, "label": "手动光圈数 N"},
    "proc_DN": {"type": "string", "label": "局部光圈 ΔN"},
    "proc_signature": {"type": "string", "label": "制图签名"},
    "proc_vendor": {"type": "string", "label": "玻璃厂商"},
    "proc_ranking": {"type": "string", "label": "玻璃品级"},
    "proc_molding": {"type": "string", "label": "成型方式"},
    "proc_chipping": {"type": "string", "maximum_length": 32, "label": "崩边要求"},
    "proc_roughness": {"type": "string", "maximum_length": 32, "label": "其余表面粗糙度"},
    "proc_ink_brand": {"type": "string", "maximum_length": 64, "label": "油墨品牌及型号"},
    "proc_ink_proportion": {"type": "string", "maximum_length": 96, "label": "油墨配比"},
    "proc_ink_thickness": {"type": "string", "maximum_length": 32, "label": "油墨厚度"},
    "proc_spraying_position": {"type": "string", "maximum_length": 96, "label": "喷墨位置"},
    "proc_dimensions_rule": {"type": "string", "maximum_length": 96, "label": "喷墨尺寸依据"},
    "proc_ink_leakage": {"type": "string", "maximum_length": 32, "label": "溢墨漏光要求"},
    "special_notes": {"type": "string", "maximum_length": 320, "label": "自由特殊加工备注"},
    "coat_preset": {"type": "enum", "values": ["SQ-A1", "SQ-A5", "SQ-A6", "Custom"], "label": "镀膜预设"},
    "coat_s1_wave1": {"type": "string", "label": "S1 第一波段"},
    "coat_s1_wave2": {"type": "string", "label": "S1 第二波段"},
    "coat_s2_wave1": {"type": "string", "label": "S2 第一波段"},
    "coat_s2_wave2": {"type": "string", "label": "S2 第二波段"},
    "coat_s1_ravg1": {"type": "string", "label": "S1 第一波段平均反射率"},
    "coat_s1_ravg2": {"type": "string", "label": "S1 第二波段平均反射率"},
    "coat_s2_ravg1": {"type": "string", "label": "S2 第一波段平均反射率"},
    "coat_s2_ravg2": {"type": "string", "label": "S2 第二波段平均反射率"},
    "coat_s1_angle1": {"type": "string", "label": "S1 第一波段入射角"},
    "coat_s1_angle2": {"type": "string", "label": "S1 第二波段入射角"},
    "coat_s2_angle1": {"type": "string", "label": "S2 第一波段入射角"},
    "coat_s2_angle2": {"type": "string", "label": "S2 第二波段入射角"},
    "ca_ratio": {"type": "number", "exclusive_minimum": 0, "maximum": 1, "label": "CA 自动系数"},
    "CA_mode": {"type": "enum", "values": ["auto", "manual"], "label": "当前镜片 CA 模式"},
    "CA1": {"type": "number", "exclusive_minimum": 0, "unit": "mm", "label": "当前镜片左 CA"},
    "CA2": {"type": "number", "exclusive_minimum": 0, "unit": "mm", "label": "当前镜片右 CA"},
    "sapphire_surfaces": {"type": "string_array", "label": "蓝宝石膜胶合界面"},
}

for _index in range(1, 4):
    PROCESS_FIELD_SPECS.update({
        f"ca_mode_{_index}": {
            "type": "enum", "values": ["auto", "manual"],
            "label": f"镜片{_index} CA 模式",
        },
        f"ca_{_index}_left": {
            "type": "number", "exclusive_minimum": 0, "unit": "mm",
            "label": f"镜片{_index}左 CA",
        },
        f"ca_{_index}_right": {
            "type": "number", "exclusive_minimum": 0, "unit": "mm",
            "label": f"镜片{_index}右 CA",
        },
        f"chamfer_mode_{_index}": {
            "type": "enum", "values": ["auto", "manual"],
            "label": f"镜片{_index}倒角模式",
        },
        f"chamfer_{_index}_left": {
            "type": "number", "minimum": 0, "unit": "mm",
            "label": f"镜片{_index}左倒角",
        },
        f"chamfer_{_index}_right": {
            "type": "number", "minimum": 0, "unit": "mm",
            "label": f"镜片{_index}右倒角",
        },
    })


ALLOWED_GLOBAL_KEYS = set(PROCESS_FIELD_SPECS)
ALLOWED_PAGE_KEYS = ALLOWED_GLOBAL_KEYS - {
    "proc_c_assembly",
    "cemented_ref_lens",
    "sapphire_surfaces",
}
_LENS_INDEXED_KEY = re.compile(r"^(?:ca_mode|ca|chamfer_mode|chamfer)_(\d)(?:_|$)")


class ProcessPatchError(ValueError):
    pass


@dataclass
class ApprovedProcessPatch:
    approve_effective_manufacturing_requirements: bool
    approved_by: str
    approved_at: str
    reason: str
    source: str
    global_overrides: dict[str, Any]
    group_overrides: dict[str, dict[str, Any]]
    page_overrides: dict[str, dict[str, dict[str, Any]]]
    approval_evidence: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ProcessPatchError(f"{label} 必须是数值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ProcessPatchError(f"{label} 必须是数值") from exc
    if not math.isfinite(number):
        raise ProcessPatchError(f"{label} 必须是有限数值")
    return number


def normalize_override_values(
    values: dict[str, Any],
    label: str,
    *,
    allowed_keys: set[str] = ALLOWED_GLOBAL_KEYS,
) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ProcessPatchError(f"{label} 必须是对象")
    unknown = sorted(set(values) - allowed_keys)
    if unknown:
        raise ProcessPatchError(f"{label} 包含未知或禁止字段: {', '.join(unknown)}")

    normalized: dict[str, Any] = {}
    for key, raw in values.items():
        spec = PROCESS_FIELD_SPECS[key]
        value_type = spec["type"]
        field_label = f"{label}.{key}"
        if key == "coat_preset" and raw == "SQ-A3":
            raw = "SQ-A6"
        if value_type == "number":
            value = _finite_number(raw, field_label)
            if "minimum" in spec and value < spec["minimum"]:
                raise ProcessPatchError(f"{field_label} 不能小于 {spec['minimum']}")
            if "exclusive_minimum" in spec and value <= spec["exclusive_minimum"]:
                raise ProcessPatchError(f"{field_label} 必须大于 {spec['exclusive_minimum']}")
            if "maximum" in spec and value > spec["maximum"]:
                raise ProcessPatchError(f"{field_label} 不能大于 {spec['maximum']}")
            normalized[key] = value
        elif value_type == "integer":
            value = _finite_number(raw, field_label)
            if not value.is_integer():
                raise ProcessPatchError(f"{field_label} 必须是整数")
            integer = int(value)
            if integer < spec["minimum"] or integer > spec["maximum"]:
                raise ProcessPatchError(
                    f"{field_label} 必须在 {spec['minimum']}~{spec['maximum']} 之间"
                )
            normalized[key] = integer
        elif value_type == "enum":
            value = str(raw)
            if value not in spec["values"]:
                raise ProcessPatchError(
                    f"{field_label} 无效，允许值: {', '.join(spec['values'])}"
                )
            normalized[key] = value
        elif value_type == "string_array":
            if raw in (None, ""):
                normalized[key] = []
            elif not isinstance(raw, list):
                raise ProcessPatchError(f"{field_label} 必须是数组")
            else:
                cleaned: list[str] = []
                for item in raw:
                    text = str(item).strip()
                    if text and text not in cleaned:
                        cleaned.append(text)
                normalized[key] = cleaned
        else:
            if raw is None:
                raise ProcessPatchError(f"{field_label} 不能为空")
            value = str(raw).strip()
            if len(value) > spec.get("maximum_length", 1000):
                raise ProcessPatchError(
                    f"{field_label} 最多 {spec['maximum_length']} 个字符"
                )
            normalized[key] = value
    return normalized


def _normalize_group_overrides(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProcessPatchError("group_overrides 必须是对象")
    result: dict[str, dict[str, Any]] = {}
    for group, values in payload.items():
        group_id = str(group)
        if not group_id.isdigit():
            raise ProcessPatchError("group_overrides 键必须是组号")
        result[group_id] = normalize_override_values(
            values, f"group_overrides.{group_id}"
        )
    return result


def _normalize_page_overrides(payload: Any) -> dict[str, dict[str, dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise ProcessPatchError("page_overrides 必须是对象")
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for group, pages in payload.items():
        group_id = str(group)
        if not group_id.isdigit() or not isinstance(pages, dict):
            raise ProcessPatchError("page_overrides 键必须是组号，值必须是页面对象")
        normalized_pages: dict[str, dict[str, Any]] = {}
        for page, values in pages.items():
            page_id = str(page)
            if not page_id.isdigit():
                raise ProcessPatchError(f"page_overrides.{group_id} 键必须是镜片页序号")
            normalized_pages[page_id] = normalize_override_values(
                values,
                f"page_overrides.{group_id}.{page_id}",
                allowed_keys=ALLOWED_PAGE_KEYS,
            )
        result[group_id] = normalized_pages
    return result


def load_approved_patch(path: str | Path | None) -> ApprovedProcessPatch | None:
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return approved_patch_from_payload(payload)


def approved_patch_from_payload(payload: dict[str, Any]) -> ApprovedProcessPatch:
    if not isinstance(payload, dict):
        raise ProcessPatchError("加工要求补丁必须是 JSON 对象")
    if payload.get("approval_status") != "approved":
        raise ProcessPatchError("加工要求补丁必须明确设置 approval_status=approved")
    if payload.get("approve_effective_manufacturing_requirements") is not True:
        raise ProcessPatchError(
            "必须明确设置 approve_effective_manufacturing_requirements=true，"
            "表示已审核默认值和覆盖值组成的完整加工要求"
        )
    approved_by = str(payload.get("approved_by", "")).strip()
    approved_at = str(payload.get("approved_at", "")).strip()
    reason = str(payload.get("reason", "")).strip()
    if not approved_by or not approved_at or not reason:
        raise ProcessPatchError("加工要求补丁缺少 approved_by、approved_at 或 reason")

    global_overrides = normalize_override_values(
        payload.get("global_overrides", {}), "global_overrides"
    )
    return ApprovedProcessPatch(
        approve_effective_manufacturing_requirements=True,
        approved_by=approved_by,
        approved_at=approved_at,
        reason=reason,
        source=str(payload.get("source", "human")),
        global_overrides=global_overrides,
        group_overrides=_normalize_group_overrides(payload.get("group_overrides", {})),
        page_overrides=_normalize_page_overrides(payload.get("page_overrides", {})),
        approval_evidence=payload.get("approval_evidence"),
    )


def validate_patch_for_drafts(
    patch: ApprovedProcessPatch,
    drafts: list[Any],
) -> None:
    draft_by_id = {
        str(draft.group_index): draft
        for draft in drafts
        if draft.status == "accepted"
    }
    referenced = set(patch.group_overrides) | set(patch.page_overrides)
    unknown = sorted(referenced - set(draft_by_id))
    if unknown:
        raise ProcessPatchError(
            "加工要求补丁引用了不存在的镜片组: " + ", ".join(unknown)
        )

    for group_id, draft in draft_by_id.items():
        lens_count = len(draft.lenses) or len(
            [key for key in draft.row if key.startswith("Glass")]
        )
        effective_group = dict(patch.global_overrides)
        effective_group.update(patch.group_overrides.get(group_id, {}))
        ref_lens = effective_group.get("cemented_ref_lens")
        if ref_lens is not None and not 1 <= int(ref_lens) <= lens_count:
            raise ProcessPatchError(
                f"组 {group_id} 的 cemented_ref_lens 必须在 1~{lens_count} 之间"
            )

        valid_sapphire = {
            surface
            for interface_index in range(1, lens_count)
            for surface in (f"{interface_index}:S2", f"{interface_index + 1}:S1")
        }
        invalid_sapphire = sorted(
            set(effective_group.get("sapphire_surfaces", [])) - valid_sapphire
        )
        if invalid_sapphire:
            raise ProcessPatchError(
                f"组 {group_id} 蓝宝石膜表面无效: {', '.join(invalid_sapphire)}"
            )

        for key in effective_group:
            match = _LENS_INDEXED_KEY.match(key)
            if match and int(match.group(1)) > lens_count:
                raise ProcessPatchError(
                    f"组 {group_id} 只有 {lens_count} 片镜片，字段 {key} 无效"
                )

        for page_id, values in patch.page_overrides.get(group_id, {}).items():
            page_index = int(page_id)
            if not 1 <= page_index <= lens_count:
                raise ProcessPatchError(
                    f"组 {group_id} 的镜片页序号 {page_id} 必须在 1~{lens_count} 之间"
                )
            for key in values:
                match = _LENS_INDEXED_KEY.match(key)
                if match and int(match.group(1)) != page_index:
                    raise ProcessPatchError(
                        f"组 {group_id} 镜片页 {page_id} 不能设置其他镜片字段 {key}"
                    )


def build_ai_work_order(drafts: list[dict[str, Any]], defaults: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "4.0",
        "purpose": (
            "Agent may explain topology evidence and translate explicit user requirements into "
            "approved manufacturing assignments; geometry and acceptance status are immutable"
        ),
        "rules": [
            "Do not change Glass/T/R/MD/AD, surface mapping, or source provenance.",
            "Do not turn a blocked or ambiguous topology into accepted geometry.",
            "Do not invent missing optical, mechanical, naming, or manufacturing values.",
            "Every naming/manufacturing decision must retain evidence from a user message or hashed attachment.",
            "Only use fields listed in process_field_catalog.",
            "Page overrides use lens position 1..N, not the physical PDF page number.",
            "Execution requires explicit approval of the complete effective manufacturing requirements.",
            "Unspecified fields always use the immutable Agent baseline, never persisted GUI settings.",
        ],
        "process_field_catalog": PROCESS_FIELD_SPECS,
        "allowed_global_and_group_keys": sorted(ALLOWED_GLOBAL_KEYS),
        "allowed_page_keys": sorted(ALLOWED_PAGE_KEYS),
        "current_defaults": defaults,
        "drawing_groups": [
            {
                "group_index": draft["group_index"],
                "surface_range": draft["surface_range"],
                "topology": draft.get("topology", {}),
                "geometry": draft["row"],
                "lens_geometry": draft.get("lenses", []),
                "legacy_row_compatible": draft.get(
                    "legacy_row_compatible", True
                ),
                "warnings": draft["warnings"],
                "blockers": draft["blockers"],
            }
            for draft in drafts
            if draft.get("status") == "accepted"
        ],
        "excluded_components": [
            {
                "group_index": draft["group_index"],
                "surface_range": draft["surface_range"],
                "topology": draft.get("topology", {}),
                "geometry": draft["row"],
                "lens_geometry": draft.get("lenses", []),
                "legacy_row_compatible": draft.get(
                    "legacy_row_compatible", True
                ),
                "warnings": draft["warnings"],
            }
            for draft in drafts
            if draft.get("status") == "excluded"
        ],
        "legacy_patch_template": {
            "approval_status": "proposed",
            "approve_effective_manufacturing_requirements": False,
            "approved_by": "",
            "approved_at": "",
            "source": "ai-assisted",
            "reason": "",
            "global_overrides": {},
            "group_overrides": {},
            "page_overrides": {},
        },
    }
