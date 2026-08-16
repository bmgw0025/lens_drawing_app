from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class NamingError(ValueError):
    pass


_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _validate_renderer_text(value: Any, label: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise NamingError(f"{label} 不能为空")
    if len(text) > maximum:
        raise NamingError(f"{label} 最多 {maximum} 个 ASCII 字符，当前为 {len(text)}")
    if not _SAFE_TEXT.fullmatch(text):
        raise NamingError(
            f"{label} 仅允许 ASCII 字母、数字、点、下划线和连字符，且必须以字母或数字开头"
        )
    if text.rstrip(". ").upper() in _WINDOWS_RESERVED:
        raise NamingError(f"{label} 不能使用 Windows 保留名 {text}")
    return text


def validate_naming_policy_shape(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict):
        raise NamingError("naming 必须是对象")
    mode = str(policy.get("mode", "")).strip()
    if mode not in {"generated", "production_sequence", "base_name", "per_group"}:
        raise NamingError(
            "naming.mode 必须是 generated、production_sequence、base_name 或 per_group"
        )
    evidence_ids = policy.get("evidence_ids", [])
    if (
        not isinstance(evidence_ids, list)
        or not evidence_ids
        or not all(str(item).strip() for item in evidence_ids)
    ):
        raise NamingError("naming.evidence_ids 必须是非空字符串数组")
    normalized = dict(policy)
    normalized["mode"] = mode
    normalized["evidence_ids"] = [str(item).strip() for item in evidence_ids]
    if mode == "generated":
        if policy.get("confirm_generated_names") is not True:
            raise NamingError("使用自动审计命名时必须设置 confirm_generated_names=true")
    elif mode == "production_sequence":
        normalized["lens_model"] = _validate_renderer_text(
            policy.get("lens_model"), "naming.lens_model", 64
        )
        normalized["lens_element_model"] = _validate_renderer_text(
            policy.get("lens_element_model"), "naming.lens_element_model", 64
        )
        normalized["first_production_code"] = _validate_renderer_text(
            policy.get("first_production_code"),
            "naming.first_production_code",
            20,
        )
        try:
            sequence_start = int(policy.get("element_sequence_start", 1))
        except (TypeError, ValueError) as exc:
            raise NamingError("naming.element_sequence_start 必须是正整数") from exc
        if sequence_start < 1:
            raise NamingError("naming.element_sequence_start 必须是正整数")
        normalized["element_sequence_start"] = sequence_start
    elif mode == "base_name":
        normalized["part_name_base"] = _validate_renderer_text(
            policy.get("part_name_base"), "naming.part_name_base", 15
        )
        if policy.get("part_no_base") not in (None, ""):
            normalized["part_no_base"] = _validate_renderer_text(
                policy.get("part_no_base"), "naming.part_no_base", 20
            )
        normalized["append_group_suffix"] = bool(
            policy.get("append_group_suffix", True)
        )
    else:
        groups = policy.get("groups")
        if not isinstance(groups, dict) or not groups:
            raise NamingError("naming.groups 必须是非空对象")
        normalized_groups: dict[str, dict[str, str]] = {}
        for group, values in groups.items():
            group_id = str(group)
            if not group_id.isdigit() or not isinstance(values, dict):
                raise NamingError("naming.groups 键必须是组号，值必须是对象")
            item = {
                "PartName": _validate_renderer_text(
                    values.get("PartName"), f"naming.groups.{group_id}.PartName", 15
                )
            }
            if values.get("PartNo") not in (None, ""):
                item["PartNo"] = _validate_renderer_text(
                    values.get("PartNo"), f"naming.groups.{group_id}.PartNo", 20
                )
            normalized_groups[group_id] = item
        normalized["groups"] = normalized_groups
    return normalized


def resolve_naming_policy(drafts: list[Any], policy: dict[str, Any]) -> dict[str, dict[str, str]]:
    policy = validate_naming_policy_shape(policy)
    if policy["mode"] == "generated":
        return {}

    group_ids = [str(draft.group_index) for draft in drafts]
    result: dict[str, dict[str, str]] = {}
    if policy["mode"] == "production_sequence":
        production_match = re.fullmatch(r"(.*?)(\d+)", policy["first_production_code"])
        if len(group_ids) > 1 and production_match is None:
            raise NamingError(
                "多组镜片的首枚生产编码必须以数字结尾，或改用 per_group 明确每组编码"
            )
        for offset, group_id in enumerate(group_ids):
            sequence = policy["element_sequence_start"] + offset
            part_name = _validate_renderer_text(
                f"{policy['lens_element_model']}-{sequence}",
                f"组 {group_id} PartName",
                15,
            )
            if production_match is None:
                part_no = policy["first_production_code"]
            else:
                prefix, digits = production_match.groups()
                part_no = _validate_renderer_text(
                    f"{prefix}{str(int(digits) + offset).zfill(len(digits))}",
                    f"组 {group_id} PartNo",
                    20,
                )
            result[group_id] = {
                "PartName": part_name,
                "PartNo": part_no,
                "SavePdfFolder": policy["lens_model"],
                "MfrPdfFolder": policy["lens_element_model"],
            }
    elif policy["mode"] == "per_group":
        missing = sorted(set(group_ids) - set(policy["groups"]))
        unknown = sorted(set(policy["groups"]) - set(group_ids))
        if missing:
            raise NamingError("缺少镜片组命名: " + ", ".join(missing))
        if unknown:
            raise NamingError("命名引用了不存在的镜片组: " + ", ".join(unknown))
        result = {group: dict(policy["groups"][group]) for group in group_ids}
    else:
        append_suffix = policy["append_group_suffix"]
        if len(group_ids) > 1 and not append_suffix:
            raise NamingError("一个 ZMX 含多个镜片组时必须追加组号或使用 per_group 命名")
        for group_id in group_ids:
            suffix = f"_G{int(group_id):02d}" if append_suffix else ""
            part_name = _validate_renderer_text(
                f"{policy['part_name_base']}{suffix}",
                f"组 {group_id} PartName",
                15,
            )
            item = {"PartName": part_name}
            if policy.get("part_no_base"):
                part_no_suffix = f"-G{int(group_id):02d}" if append_suffix else ""
                item["PartNo"] = _validate_renderer_text(
                    f"{policy['part_no_base']}{part_no_suffix}",
                    f"组 {group_id} PartNo",
                    20,
                )
            result[group_id] = item

    part_names = [item["PartName"].casefold() for item in result.values()]
    if len(part_names) != len(set(part_names)):
        raise NamingError("不同镜片组的 PartName 不能重复")
    for item in result.values():
        if Path(item["PartName"]).name != item["PartName"]:
            raise NamingError("PartName 不能包含目录路径")
    return result
