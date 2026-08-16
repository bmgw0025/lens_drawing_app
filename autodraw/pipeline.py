from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from settings import get_agent_default_settings

from .mapper import map_to_drafts
from .models import Provenance
from .naming import resolve_naming_policy
from .process_requirements import (
    ApprovedProcessPatch,
    PROCESS_FIELD_SPECS,
    build_ai_work_order,
    load_approved_patch,
    validate_patch_for_drafts,
)
from .renderer_adapter import DEFAULT_RENDERER_ROOT, render_draft, renderer_source_manifest
from .zosapi_provider import NativeZosApiProvider


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "NaN"
        return "Infinity" if value > 0 else "-Infinity"
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(
            _json_safe(payload),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
            default=str,
        ),
        encoding="utf-8",
    )


def _prepare_destination(output_dir: str | os.PathLike[str]) -> Path:
    destination = Path(output_dir).resolve()
    if destination.exists():
        if not destination.is_dir():
            raise FileExistsError(f"输出路径不是目录: {destination}")
        if any(destination.iterdir()):
            raise FileExistsError(
                f"输出目录非空，拒绝混入或覆盖旧审计/PDF: {destination}"
            )
    else:
        destination.mkdir(parents=True, exist_ok=False)
    return destination


def _build_effective_requirements(
    drafts: list[Any],
    defaults: dict[str, Any],
    patch: ApprovedProcessPatch | None,
) -> dict[str, Any]:
    groups: dict[str, Any] = {}
    for draft in drafts:
        if draft.status != "accepted":
            continue
        group_id = str(draft.group_index)
        lens_count = len(draft.lenses) or len(
            [key for key in draft.row if key.startswith("Glass")]
        )
        group_settings = dict(defaults)
        if patch is not None:
            group_settings.update(patch.global_overrides)
            group_settings.update(patch.group_overrides.get(group_id, {}))
        pages = {}
        for page_index in range(1, lens_count + 1):
            page_settings = dict(group_settings)
            if patch is not None:
                page_settings.update(
                    patch.page_overrides.get(group_id, {}).get(str(page_index), {})
                )
            pages[str(page_index)] = page_settings
        groups[group_id] = {
            "group_type": draft.topology.get("group_type"),
            "lens_count": lens_count,
            "group_settings": group_settings,
            "lens_page_settings": pages,
        }
    return {
        "schema_version": "1.0",
        "page_index_semantics": "lens position 1..N; assembly page is not addressable",
        "approved": patch is not None,
        "approval": (
            {
                "approved_by": patch.approved_by,
                "approved_at": patch.approved_at,
                "reason": patch.reason,
                "source": patch.source,
                "approval_evidence": patch.approval_evidence,
            }
            if patch is not None
            else None
        ),
        "renderer_defaults": defaults,
        "groups": groups,
    }


def _manufacturing_delivery_payload(
    defaults: dict[str, Any],
    patch: ApprovedProcessPatch | None,
    effective_requirements: dict[str, Any],
    excluded_drafts: list[Any],
) -> dict[str, Any]:
    baseline = {
        key: defaults[key]
        for key in sorted(PROCESS_FIELD_SPECS)
        if key in defaults
    }
    return {
        "schema_version": "1.0",
        "default_policy": {
            "source": "immutable Agent baseline bundled with this Lens Drawing version",
            "persisted_gui_settings_used": False,
            "unspecified_fields_use_baseline": True,
        },
        "default_manufacturing_requirements": baseline,
        "special_requirements": {
            "global_overrides": patch.global_overrides if patch else {},
            "group_overrides": patch.group_overrides if patch else {},
            "page_overrides": patch.page_overrides if patch else {},
        },
        "approval": (
            {
                "approved_by": patch.approved_by,
                "approved_at": patch.approved_at,
                "reason": patch.reason,
                "source": patch.source,
                "approval_evidence": patch.approval_evidence,
            }
            if patch
            else None
        ),
        "effective_requirements": effective_requirements,
        "excluded_components": [
            {
                "group_index": draft.group_index,
                "surface_range": draft.surface_range,
                "group_type": draft.topology.get("group_type"),
                "exclusion": draft.topology.get("exclusion", {}),
                "warnings": draft.warnings,
            }
            for draft in excluded_drafts
        ],
    }


def _manufacturing_delivery_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# 本次出图加工要求与特殊处理",
        "",
        "- Agent 默认值来源：当前 Lens Drawing 版本内置固定基准",
        "- 使用 GUI 上次持久化设置：否",
        "- 未提及的加工字段：使用下表默认值",
        "",
        "## 默认加工要求",
        "",
        "| 字段 | 含义 | 默认值 |",
        "|---|---|---|",
    ]
    for key, value in payload["default_manufacturing_requirements"].items():
        label = PROCESS_FIELD_SPECS.get(key, {}).get("label", key)
        rendered = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        lines.append(f"| `{key}` | {label} | {rendered} |")

    lines.extend(["", "## 特殊覆盖", ""])
    special = payload["special_requirements"]
    special_rows = []
    for field, value in special["global_overrides"].items():
        special_rows.append(("全局", field, value))
    for group, values in special["group_overrides"].items():
        for field, value in values.items():
            special_rows.append((f"镜片组 {group}", field, value))
    for group, pages in special["page_overrides"].items():
        for page, values in pages.items():
            for field, value in values.items():
                special_rows.append((f"镜片组 {group} / 镜片 {page}", field, value))
    if special_rows:
        lines.extend(["| 范围 | 字段 | 特殊值 |", "|---|---|---|"])
        for scope, field, value in special_rows:
            rendered = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
            lines.append(f"| {scope} | `{field}` | {rendered} |")
    else:
        lines.append("无特殊覆盖，全部采用固定 Agent 默认值。")

    lines.extend(["", "## 已剔除组件", ""])
    exclusions = payload["excluded_components"]
    if exclusions:
        for item in exclusions:
            evidence = item.get("exclusion", {})
            lines.append(
                f"- 镜片组 {item['group_index']}（surface {item['surface_range']}）："
                f"{evidence.get('material', '')} 双平面棱镜，未出图。"
            )
    else:
        lines.append("无。")
    return "\n".join(lines) + "\n"


def run_pipeline(
    source_file: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    renderer_root: str | os.PathLike[str] = DEFAULT_RENDERER_ROOT,
    process_patch_path: str | os.PathLike[str] | None = None,
    process_patch: ApprovedProcessPatch | None = None,
    naming_overrides: dict[str, dict[str, str]] | None = None,
    naming_policy: dict[str, Any] | None = None,
    task_context: dict[str, Any] | None = None,
    geometry_acknowledgements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # The bridge is a one-shot isolated process; never write bytecode into the renderer checkout.
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    destination = _prepare_destination(output_dir)
    started_at = datetime.now(timezone.utc).isoformat()
    with NativeZosApiProvider() as provider:
        system = provider.extract(source_file)
    _write_json(destination / "extracted_system.json", system.to_dict())

    drafts = map_to_drafts(system)
    drawable_drafts = [draft for draft in drafts if draft.status == "accepted"]
    excluded_drafts = [draft for draft in drafts if draft.status == "excluded"]
    if naming_policy is not None:
        if naming_overrides is not None:
            raise ValueError("naming_policy 与 naming_overrides 不能同时提供")
        naming_overrides = resolve_naming_policy(drawable_drafts, naming_policy)
    if naming_overrides:
        valid_group_ids = {str(draft.group_index) for draft in drawable_drafts}
        unknown_group_ids = sorted(set(naming_overrides) - valid_group_ids)
        if unknown_group_ids:
            raise ValueError(
                "命名覆盖引用了不存在的镜片组: " + ", ".join(unknown_group_ids)
            )
        for draft in drafts:
            values = naming_overrides.get(str(draft.group_index), {})
            for field in ("PartName", "PartNo", "SavePdfFolder", "MfrPdfFolder"):
                if field in values:
                    previous = draft.row.get(field)
                    draft.row[field] = values[field]
                    provenance = next(
                        (item for item in draft.provenance if item.field == field), None
                    )
                    if provenance is not None:
                        provenance.source = (
                            "agent task naming policy backed by user evidence IDs: "
                            + ", ".join(naming_policy.get("evidence_ids", []))
                            if naming_policy is not None
                            else "validated external naming override"
                        )
                        provenance.raw_value = previous
                        provenance.converted_value = values[field]
                        provenance.confidence = "user-specified"
                    else:
                        draft.provenance.append(
                            Provenance(
                                field,
                                "agent task naming policy backed by user evidence IDs: "
                                + ", ".join(naming_policy.get("evidence_ids", [])),
                                previous,
                                values[field],
                                "user-specified",
                            )
                        )
                    if field == "PartNo":
                        draft.warnings = [
                            warning
                            for warning in draft.warnings
                            if "PartNo 为自动审计编号" not in warning
                        ]
    draft_payload = [draft.to_dict() for draft in drafts]
    _write_json(destination / "drawing_drafts.json", draft_payload)

    if process_patch is not None and process_patch_path is not None:
        raise ValueError("process_patch 与 process_patch_path 不能同时提供")
    patch = process_patch or load_approved_patch(process_patch_path)
    if patch is not None:
        validate_patch_for_drafts(patch, drafts)
    renderer_root_path = Path(renderer_root).resolve()
    renderer_manifest_before = renderer_source_manifest(renderer_root_path)
    defaults: dict[str, Any] = get_agent_default_settings()
    _write_json(destination / "ai_work_order.json", build_ai_work_order(draft_payload, defaults))
    effective_requirements = _build_effective_requirements(drafts, defaults, patch)
    effective_requirements_path = destination / "effective_manufacturing_requirements.json"
    _write_json(effective_requirements_path, effective_requirements)
    manufacturing_delivery = _manufacturing_delivery_payload(
        defaults,
        patch,
        effective_requirements,
        excluded_drafts,
    )
    manufacturing_delivery_path = destination / "manufacturing_requirements_delivery.json"
    manufacturing_summary_path = destination / "manufacturing_requirements_summary.md"
    _write_json(manufacturing_delivery_path, manufacturing_delivery)
    manufacturing_summary_path.write_text(
        _manufacturing_delivery_markdown(manufacturing_delivery), encoding="utf-8"
    )

    rendered = []
    render_errors = []
    for draft in drafts:
        if draft.status != "accepted":
            continue
        try:
            rendered.append(
                render_draft(draft, destination, renderer_root_path, patch)
            )
        except Exception as exc:
            render_errors.append(
                {"group_index": draft.group_index, "error": f"{type(exc).__name__}: {exc}"}
            )

    blocked = [
        draft.group_index
        for draft in drafts
        if draft.status not in {"accepted", "excluded"}
    ]
    renderer_manifest_after = renderer_source_manifest(renderer_root_path)
    if renderer_manifest_after != renderer_manifest_before:
        raise RuntimeError("只读绘图引擎源码在运行期间发生变化，已拒绝生成完成审计")
    geometry_ready = bool(drafts) and not blocked and not render_errors
    drawings_generated = geometry_ready and len(rendered) == len(drawable_drafts)
    geometry_review_fields = []
    acknowledged_fields = (
        geometry_acknowledgements.get("fields", {})
        if isinstance(geometry_acknowledgements, dict)
        else {}
    )
    for draft in drafts:
        if draft.status != "accepted":
            continue
        for provenance in draft.provenance:
            if provenance.field.startswith(
                ("Glass", "T", "R", "MD", "AD", "Lens")
            ):
                if provenance.confidence != "high":
                    expected_value = provenance.converted_value
                    supplied = acknowledged_fields.get(
                        str(draft.group_index), {}
                    ).get(provenance.field, object())
                    acknowledged = supplied == expected_value
                    geometry_review_fields.append(
                        {
                            "group_index": draft.group_index,
                            "field": provenance.field,
                            "value": expected_value,
                            "source": provenance.source,
                            "confidence": provenance.confidence,
                            "acknowledged": acknowledged,
                        }
                    )
    outstanding_geometry_review_fields = [
        item for item in geometry_review_fields if not item["acknowledged"]
    ]
    geometry_review_required = bool(outstanding_geometry_review_fields)
    manufacturing_review_status = (
        "approved_patch" if patch is not None else "renderer_defaults_require_explicit_approval"
    )
    execution_mode = (
        task_context.get("execution_mode", "production")
        if isinstance(task_context, dict)
        else "production"
    )
    audit = {
        "schema_version": "3.0",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "source_file": system.source_file,
        "source_sha256": system.source_sha256,
        "provider": system.provider,
        "opticstudio_version": system.opticstudio_version,
        "license_status": system.license_status,
        "configuration": {
            "count": system.configuration_count,
            "current": system.current_configuration,
        },
        "geometry_inference_method": (
            "GLAS-after-surface intervals + direct or coincident zero-thickness virtual cemented "
            "interfaces + side-specific/shared MEMA evidence + MD >= adjacent AD constraints; "
            "unresolved different viable candidates are blocked"
        ),
        "accepted_groups": [draft.group_index for draft in drafts if draft.status == "accepted"],
        "excluded_groups": [draft.group_index for draft in drafts if draft.status == "excluded"],
        "excluded_components": [
            {
                "group_index": draft.group_index,
                "surface_range": draft.surface_range,
                "exclusion": draft.topology.get("exclusion", {}),
                "warnings": draft.warnings,
            }
            for draft in excluded_drafts
        ],
        "blocked_groups": blocked,
        "rendered_outputs": rendered,
        "rendered_pdfs": [
            path
            for output in rendered
            for path in (output.get("save_pdf"), output.get("mfr_pdf"))
            if path
        ],
        "render_errors": render_errors,
        "renderer_source_manifest_sha256": renderer_manifest_after,
        "group_topologies": [
            {
                "group_index": draft.group_index,
                "surface_range": draft.surface_range,
                "group_type": draft.topology.get("group_type"),
                "connections": draft.topology.get("connections", []),
            }
            for draft in drafts
        ],
        "geometry_warnings": [
            {
                "group_index": draft.group_index,
                "status": draft.status,
                "confidence": draft.confidence,
                "warnings": draft.warnings,
            }
            for draft in drafts
            if draft.warnings
        ],
        "process_patch": asdict(patch) if patch is not None else None,
        "naming_overrides": naming_overrides or {},
        "agent_task": task_context,
        "execution_mode": execution_mode,
        "effective_manufacturing_requirements_file": str(effective_requirements_path),
        "manufacturing_requirements_delivery_file": str(manufacturing_delivery_path),
        "manufacturing_requirements_summary_file": str(manufacturing_summary_path),
        "automatic_geometry_ready": geometry_ready,
        "drawings_generated": drawings_generated,
        "geometry_review_required": geometry_review_required,
        "geometry_review_fields": geometry_review_fields,
        "outstanding_geometry_review_fields": outstanding_geometry_review_fields,
        "geometry_acknowledgements": geometry_acknowledgements,
        "manufacturing_review_status": manufacturing_review_status,
        "production_release_ready": (
            execution_mode == "production"
            and drawings_generated
            and patch is not None
            and not geometry_review_required
        ),
        "notes": [
            "Geometry came from evaluated ZOS-API values; the source ZMX was closed without saving.",
            "Manufacturing requirements use renderer defaults unless an explicitly approved patch is supplied.",
            "A generated PDF is not production-release-ready until manufacturing requirements have explicit evidence-backed approval.",
            "PartNo is an audit identifier only when generated naming is explicitly selected; production_sequence uses the approved production-code sequence.",
            "Excluded H-K9L plane-plane prism groups are listed in the manufacturing delivery files and receive no PDF.",
        ],
    }
    _write_json(destination / "audit.json", audit)
    return audit
