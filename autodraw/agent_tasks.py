from __future__ import annotations

import hashlib
import json
import math
import msvcrt
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app_version import (
    AGENT_INTERFACE_VERSION,
    REQUEST_SCHEMA_VERSION,
    TASK_SCHEMA_VERSION,
)
from settings import get_agent_default_settings

from .mapper import map_to_drafts
from .naming import NamingError, resolve_naming_policy, validate_naming_policy_shape
from .pipeline import run_pipeline
from .process_requirements import (
    PROCESS_FIELD_SPECS,
    ProcessPatchError,
    approved_patch_from_payload,
    build_ai_work_order,
    validate_patch_for_drafts,
)
from .renderer_adapter import DEFAULT_RENDERER_ROOT, renderer_source_manifest
from .renderer_adapter import preflight_draft
from .runtime import agent_resource, runtime_identity
from .spec import build_agent_spec, spec_sha256
from .zosapi_provider import NativeZosApiProvider, _sha256


REQUEST_FILE = "agent_request.json"
STATE_FILE = "task_state.json"
PROTOCOL_FILE = "AGENT_PROTOCOL.md"
SCHEMA_FILE = "agent_request.schema.json"
SPEC_FILE = "lens_drawing_agent_spec.json"
ANALYSIS_DIR = "source_analysis"
RESULT_DIR = "result"
RENDER_DIR = "validation_render"
HUMAN_REVIEW_FILE = "human_visual_review.json"
ANALYSIS_FILES = (
    "extracted_system.json",
    "drawing_drafts.json",
    "agent_work_order.json",
    "analysis_summary.json",
)
NON_BLOCKING_GEOMETRY_STATUSES = {"accepted", "excluded"}


class AgentTaskError(RuntimeError):
    pass


class TaskDirectoryLock:
    def __init__(self, task_dir: Path, timeout_seconds: float = 30.0):
        self.path = task_dir / ".agent_task.lock"
        self.timeout_seconds = timeout_seconds
        self.stream = None

    def __enter__(self) -> "TaskDirectoryLock":
        self.stream = self.path.open("a+b")
        self.stream.seek(0, os.SEEK_END)
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
                return self
            except OSError as exc:
                if time.monotonic() >= deadline:
                    self.stream.close()
                    self.stream = None
                    raise AgentTaskError("等待其他 Agent 释放任务目录锁超时") from exc
                time.sleep(0.1)

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.stream.close()
            self.stream = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AgentTaskError(f"缺少文件: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AgentTaskError(f"JSON 无效: {path}: {exc}") from exc


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_manifest(root: Path, relative_paths: tuple[str, ...]) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for relative in relative_paths:
        path = root / Path(relative)
        if not path.is_file():
            raise AgentTaskError(f"manifest 缺少文件: {path}")
        manifest[relative.replace("\\", "/")] = _sha256(path)
    return manifest


def analysis_source_manifest(task_dir: Path) -> dict[str, str]:
    return _file_manifest(task_dir / ANALYSIS_DIR, ANALYSIS_FILES)


def _state_path(task_dir: Path) -> Path:
    return task_dir / STATE_FILE


def _load_state(task_dir: Path) -> dict[str, Any]:
    return _read_json(_state_path(task_dir))


def get_capabilities() -> dict[str, Any]:
    spec = build_agent_spec()
    return {
        "interface": "lens-drawing-agent-task",
        "interface_version": AGENT_INTERFACE_VERSION,
        "request_schema_version": REQUEST_SCHEMA_VERSION,
        "task_schema_version": TASK_SCHEMA_VERSION,
        "commands": spec["agent_interface"]["commands"],
        "geometry_policy": {
            "source": "read-only ZOS-API extraction from one ZMX",
            "agent_mutable": False,
            "medium_confidence_fields_require_exact_user_acknowledgement": True,
            "authoritative_geometry": "drawing_drafts[].lenses[]",
            "virtual_interface_ad": "preserve adjacent lens side-specific AD values",
        },
        "review_policy": {
            "required": True,
            "reviewer_kind": "human_operator",
            "agent_may_record_review": False,
        },
        "analysis_manifest_files": list(ANALYSIS_FILES),
        "process_field_catalog": PROCESS_FIELD_SPECS,
        "naming_modes": [
            "production_sequence",
            "generated",
            "base_name",
            "per_group",
        ],
        "agent_spec": spec,
        "agent_spec_sha256": spec_sha256(spec),
        "runtime_identity": runtime_identity(),
        "state_file": STATE_FILE,
        "request_file": REQUEST_FILE,
        "handoff_file": "AGENT_HANDOFF.md",
        "protocol_file": PROTOCOL_FILE,
        "request_schema_file": SCHEMA_FILE,
        "agent_spec_file": SPEC_FILE,
        "human_review_file": HUMAN_REVIEW_FILE,
        "delivery_file": "delivery_manifest.json",
    }


def _update_state(task_dir: Path, **updates: Any) -> dict[str, Any]:
    state = _load_state(task_dir)
    previous_status = state.get("status")
    previous_note = state.get("status_note", "")
    state.update(updates)
    state["updated_at"] = _now()
    history = list(state.get("history", []))
    if (
        "status" in updates
        and (
            updates["status"] != previous_status
            or updates.get("status_note", "") != previous_note
        )
    ):
        history.append({
            "at": state["updated_at"],
            "status": updates["status"],
            "note": updates.get("status_note", ""),
        })
    state["history"] = history
    _write_json(_state_path(task_dir), state)
    _write_handoff(task_dir, state)
    return state


def _write_handoff(task_dir: Path, state: dict[str, Any]) -> None:
    status = state.get("status", "unknown")
    questions = state.get("unresolved_questions", [])
    result_dir = state.get("result_dir")
    lines = [
        "# Agent Task Handoff",
        "",
        f"- Task ID: `{state.get('task_id', task_dir.name)}`",
        f"- Status: `{status}`",
        f"- Interface version: `{state.get('agent_interface_version', '')}`",
        f"- Source ZMX: `{state.get('source_file', '')}`",
        f"- Source SHA-256: `{state.get('source_sha256', '')}`",
        f"- Protocol snapshot: `{task_dir / PROTOCOL_FILE}`",
        f"- Protocol SHA-256: `{state.get('agent_protocol_sha256', '')}`",
        f"- Request schema snapshot: `{task_dir / SCHEMA_FILE}`",
        f"- Agent spec snapshot: `{task_dir / SPEC_FILE}`",
        f"- Agent spec SHA-256: `{state.get('agent_spec_sha256', '')}`",
        f"- Runtime identity: `{state.get('agent_runtime_identity', {})}`",
        f"- Source analysis manifest entries: `{len(state.get('source_analysis_manifest_sha256', {}))}`",
        f"- Request: `{task_dir / REQUEST_FILE}`",
        f"- Request validation: `{task_dir / 'request_validation.json'}`",
        f"- Result: `{result_dir or ''}`",
        "",
        "## Resume Contract",
        "",
        "1. Read `task_state.json` first; do not infer status from chat history.",
        "2. Read task-local `AGENT_PROTOCOL.md`, spec and `source_analysis/agent_work_order.json`.",
        "3. Never edit Glass/T/R/MD/AD or topology acceptance in the request.",
        "4. Link every naming and manufacturing decision to a user evidence ID.",
        "5. Do not run while `requirement_analysis.unresolved_questions` is non-empty.",
        "6. A completed task requires automated PDF checks and a human operator visual review record.",
    ]
    if questions:
        lines.extend(["", "## Unresolved Questions", ""])
        lines.extend(f"- {item}" for item in questions)
    if state.get("next_action"):
        lines.extend(["", "## Next Action", "", state["next_action"]])
    (task_dir / "AGENT_HANDOFF.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_analysis(task_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    analysis = task_dir / ANALYSIS_DIR
    system = _read_json(analysis / "extracted_system.json")
    drafts = _read_json(analysis / "drawing_drafts.json")
    work_order = _read_json(analysis / "agent_work_order.json")
    return system, drafts, work_order


def _draft_objects_from_analysis(task_dir: Path) -> list[Any]:
    # Re-run the mapper over the stored extracted values without opening OpticStudio.
    from .models import ExtractedSystem, SurfaceRecord

    system_payload, _, _ = _load_analysis(task_dir)
    def restored(value: Any) -> Any:
        if value == "Infinity":
            return math.inf
        if value == "-Infinity":
            return -math.inf
        if value == "NaN":
            return math.nan
        return value

    surfaces = []
    for payload in system_payload["surfaces"]:
        item = dict(payload)
        for key in (
            "radius", "thickness", "semi_diameter", "mechanical_semi_diameter",
            "explicit_aperture_radius",
        ):
            item[key] = restored(item.get(key))
        surfaces.append(SurfaceRecord(**item))
    system = ExtractedSystem(
        **{key: value for key, value in system_payload.items() if key != "surfaces"},
        surfaces=surfaces,
    )
    return map_to_drafts(system)


def _request_template(task_id: str, source: Path, source_hash: str) -> dict[str, Any]:
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "task_id": task_id,
        "source": {
            "zmx_path": str(source),
            "sha256": source_hash,
        },
        "user_evidence": [],
        "requirement_analysis": {
            "user_goal_summary": "",
            "decisions": [],
            "evidence_disposition": {},
            "assumptions": [],
            "unresolved_questions": [
                "请提供镜头型号、镜片型号和首枚生产编码，并确认顺序递增规则。",
                "请确认特殊加工要求；未提及字段将使用当前版本固定的 Agent 默认值。",
            ],
        },
        "naming": {
            "mode": "production_sequence",
            "lens_model": "",
            "lens_element_model": "",
            "first_production_code": "",
            "element_sequence_start": 1,
            "evidence_ids": [],
        },
        "geometry_review": {
            "approval_status": "proposed",
            "approved_by": "",
            "approved_at": "",
            "reason": "",
            "evidence_ids": [],
            "fields": {},
        },
        "manufacturing_requirements": {
            "approval_status": "proposed",
            "approve_effective_manufacturing_requirements": False,
            "approved_by": "",
            "approved_at": "",
            "source": "user-explicit-via-agent",
            "reason": "",
            "evidence_ids": [],
            "field_evidence": {
                "global_overrides": {},
                "group_overrides": {},
                "page_overrides": {},
            },
            "global_overrides": {},
            "group_overrides": {},
            "page_overrides": {},
        },
        "execution": {
            "mode": "production",
            "renderer_root": str(DEFAULT_RENDERER_ROOT),
            "automated_pdf_validation": True,
            "human_visual_review_required": True,
        },
    }


def create_agent_task(
    source_file: str | os.PathLike[str],
    task_dir: str | os.PathLike[str],
    *,
    renderer_root: str | os.PathLike[str] = DEFAULT_RENDERER_ROOT,
) -> dict[str, Any]:
    source = Path(source_file).resolve()
    destination = Path(task_dir).resolve()
    if not source.is_file() or source.suffix.lower() != ".zmx":
        raise AgentTaskError(f"有效的 .zmx 文件不存在: {source}")
    if destination.exists():
        if not destination.is_dir() or any(destination.iterdir()):
            raise AgentTaskError(f"任务目录必须不存在或为空: {destination}")
    else:
        destination.mkdir(parents=True, exist_ok=False)

    task_id = destination.name
    created_at = _now()
    try:
        protocol_source = agent_resource(PROTOCOL_FILE)
        schema_source = agent_resource(SCHEMA_FILE)
        spec_source = agent_resource(SPEC_FILE)
        shutil.copy2(protocol_source, destination / PROTOCOL_FILE)
        shutil.copy2(schema_source, destination / SCHEMA_FILE)
        shutil.copy2(spec_source, destination / SPEC_FILE)
        protocol_hash = _sha256(destination / PROTOCOL_FILE)
        schema_hash = _sha256(destination / SCHEMA_FILE)
        agent_spec_hash = _sha256(destination / SPEC_FILE)
        locked_runtime_identity = runtime_identity()
        with NativeZosApiProvider() as provider:
            system = provider.extract(source)
        drafts = map_to_drafts(system)
        analysis_dir = destination / ANALYSIS_DIR
        analysis_dir.mkdir()
        _write_json(analysis_dir / "extracted_system.json", system.to_dict())
        draft_payload = [draft.to_dict() for draft in drafts]
        _write_json(analysis_dir / "drawing_drafts.json", draft_payload)

        renderer_path = Path(renderer_root).resolve()
        defaults: dict[str, Any] = get_agent_default_settings()
        work_order = build_ai_work_order(draft_payload, defaults)
        work_order["source"] = {
            "zmx_path": str(source),
            "sha256": system.source_sha256,
        }
        work_order["renderer_source_manifest_sha256"] = renderer_source_manifest(
            renderer_path
        )
        work_order["agent_runtime_identity"] = locked_runtime_identity
        work_order["agent_spec_sha256"] = agent_spec_hash
        _write_json(analysis_dir / "agent_work_order.json", work_order)
        geometry_review_fields: dict[str, dict[str, Any]] = {}
        for draft in drafts:
            if draft.status != "accepted":
                continue
            fields = {
                item.field: item.converted_value
                for item in draft.provenance
                if item.field.startswith(
                    ("Glass", "T", "R", "MD", "AD", "Lens")
                )
                and item.confidence != "high"
            }
            if fields:
                geometry_review_fields[str(draft.group_index)] = fields
        _write_json(
            analysis_dir / "analysis_summary.json",
            {
                "accepted_groups": [d.group_index for d in drafts if d.status == "accepted"],
                "excluded_groups": [d.group_index for d in drafts if d.status == "excluded"],
                "blocked_groups": [
                    d.group_index
                    for d in drafts
                    if d.status not in NON_BLOCKING_GEOMETRY_STATUSES
                ],
                "group_count": len(drafts),
                "group_topologies": [
                    {
                        "group_index": d.group_index,
                        "group_type": d.topology.get("group_type"),
                        "surface_range": d.surface_range,
                        "status": d.status,
                        "warnings": d.warnings,
                        "blockers": d.blockers,
                    }
                    for d in drafts
                ],
                "geometry_review_fields": geometry_review_fields,
            },
        )
        analysis_manifest = analysis_source_manifest(destination)
        request = _request_template(task_id, source, system.source_sha256)
        request["geometry_review"]["fields"] = geometry_review_fields
        if geometry_review_fields:
            request["requirement_analysis"]["unresolved_questions"].append(
                "请确认 geometry_review.fields 中列出的中等置信几何值与最终机械规格一致。"
            )
        _write_json(destination / REQUEST_FILE, request)
        initial_hash = _canonical_hash(request)
        _write_json(
            destination / "request_versions" / f"000_initial_{initial_hash[:12]}.json",
            request,
        )
        state = {
            "schema_version": TASK_SCHEMA_VERSION,
            "task_id": task_id,
            "created_at": created_at,
            "updated_at": created_at,
            "status": (
                "blocked_geometry"
                if any(d.status not in NON_BLOCKING_GEOMETRY_STATUSES for d in drafts)
                else "needs_input"
            ),
            "status_note": (
                "ZMX 几何存在阻断项，禁止进入加工要求和出图执行。"
                if any(d.status not in NON_BLOCKING_GEOMETRY_STATUSES for d in drafts)
                else "ZMX 分析完成，等待用户命名与完整加工要求。"
            ),
            "agent_interface_version": AGENT_INTERFACE_VERSION,
            "agent_protocol_file": PROTOCOL_FILE,
            "agent_protocol_sha256": protocol_hash,
            "request_schema_file": SCHEMA_FILE,
            "request_schema_sha256": schema_hash,
            "agent_spec_file": SPEC_FILE,
            "agent_spec_sha256": agent_spec_hash,
            "agent_runtime_identity": locked_runtime_identity,
            "source_analysis_manifest_sha256": analysis_manifest,
            "source_file": str(source),
            "source_sha256": system.source_sha256,
            "renderer_root": str(renderer_path),
            "renderer_source_manifest_sha256": renderer_source_manifest(renderer_path),
            "request_hash": None,
            "request_revision": 0,
            "result_dir": None,
            "unresolved_questions": request["requirement_analysis"]["unresolved_questions"],
            "next_action": (
                "读取 source_analysis/drawing_drafts.json 中的 blockers。"
                if any(d.status not in NON_BLOCKING_GEOMETRY_STATUSES for d in drafts)
                else "Agent 与用户确认命名和完整加工要求后填写 agent_request.json，再运行 validate。"
            ),
            "history": [
                {
                    "at": created_at,
                    "status": (
                        "blocked_geometry"
                        if any(
                            d.status not in NON_BLOCKING_GEOMETRY_STATUSES
                            for d in drafts
                        )
                        else "needs_input"
                    ),
                    "note": "Agent task created and ZMX analyzed.",
                }
            ],
        }
        _write_json(destination / STATE_FILE, state)
        _write_handoff(destination, state)
        return state
    except Exception:
        if destination.exists():
            shutil.rmtree(destination, ignore_errors=True)
        raise


def submit_agent_request(
    task_dir: str | os.PathLike[str],
    request_file: str | os.PathLike[str],
    *,
    _lock: bool = True,
) -> dict[str, Any]:
    task = Path(task_dir).resolve()
    if _lock:
        with TaskDirectoryLock(task):
            return submit_agent_request(task, request_file, _lock=False)
    state = _load_state(task)
    if state.get("status") in {
        "running", "awaiting_human_review", "completed", "human_review_failed",
        "validation_failed", "execution_failed", "release_blocked",
    }:
        raise AgentTaskError(
            f"当前状态 {state.get('status')} 不允许替换请求；请创建新任务目录"
        )
    candidate = _read_json(Path(request_file).resolve())
    if not isinstance(candidate, dict):
        raise AgentTaskError("提交的 Agent 请求必须是 JSON 对象")
    if candidate.get("task_id") != state.get("task_id"):
        raise AgentTaskError("提交请求的 task_id 与任务不一致")
    source = candidate.get("source", {})
    if not isinstance(source, dict):
        raise AgentTaskError("提交请求缺少 source 对象")
    if str(Path(str(source.get("zmx_path", ""))).resolve()) != state.get("source_file"):
        raise AgentTaskError("提交请求的 ZMX 路径与任务不一致")
    if str(source.get("sha256", "")).lower() != state.get("source_sha256"):
        raise AgentTaskError("提交请求的 ZMX 哈希与任务不一致")
    request_hash = _canonical_hash(candidate)
    revision = int(state.get("request_revision", 0)) + 1
    version_path = (
        task / "request_versions" /
        f"{revision:03d}_{request_hash[:12]}.json"
    )
    _write_json(version_path, candidate)
    _write_json(task / REQUEST_FILE, candidate)
    return _update_state(
        task,
        status="submitted",
        status_note=f"Agent 请求第 {revision} 版已原子提交，尚未校验。",
        request_revision=revision,
        request_hash=request_hash,
        active_request_version=str(version_path),
        unresolved_questions=candidate.get("requirement_analysis", {}).get(
            "unresolved_questions", []
        ),
        next_action="运行 validate；不要依据聊天记忆跳过校验。",
    )


def _evidence_map(request: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    evidence = request.get("user_evidence", [])
    if not isinstance(evidence, list):
        errors.append("user_evidence 必须是数组")
        return {}
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, dict):
            errors.append(f"user_evidence[{index}] 必须是对象")
            continue
        evidence_id = str(item.get("id", "")).strip()
        kind = str(item.get("kind", "")).strip()
        content = str(item.get("content", "")).strip()
        if not evidence_id or evidence_id in result:
            errors.append(f"user_evidence[{index}].id 缺失或重复")
            continue
        if kind not in {"user_message", "attachment", "operator_record"}:
            errors.append(f"user_evidence[{index}].kind 无效")
        if not content:
            errors.append(f"user_evidence[{index}].content 不能为空")
        result[evidence_id] = item
    return result


def _validate_evidence_refs(
    ids: Any,
    label: str,
    evidence: dict[str, dict[str, Any]],
    errors: list[str],
) -> list[str]:
    if not isinstance(ids, list) or not ids:
        errors.append(f"{label} 必须至少引用一条用户证据")
        return []
    normalized = [str(item).strip() for item in ids]
    unknown = sorted(set(normalized) - set(evidence))
    if unknown:
        errors.append(f"{label} 引用了不存在的证据: {', '.join(unknown)}")
    return normalized


def _validate_attachment_evidence(
    evidence: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    for evidence_id, item in evidence.items():
        if item.get("kind") != "attachment":
            continue
        source_ref = str(item.get("source_ref", "")).strip()
        expected_hash = str(item.get("sha256", "")).strip().lower()
        if not source_ref:
            errors.append(f"附件证据 {evidence_id} 缺少 source_ref 本地路径")
            continue
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            errors.append(f"附件证据 {evidence_id} 缺少有效 sha256")
            continue
        path = Path(source_ref).resolve()
        if not path.is_file():
            errors.append(f"附件证据 {evidence_id} 当前不存在: {path}")
        elif _sha256(path) != expected_hash:
            errors.append(f"附件证据 {evidence_id} 当前哈希与请求记录不一致")


def _validate_manufacturing_field_evidence(
    manufacturing: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    field_evidence = manufacturing.get("field_evidence", {})
    if not isinstance(field_evidence, dict):
        errors.append("manufacturing_requirements.field_evidence 必须是对象")
        return

    global_evidence = field_evidence.get("global_overrides", {})
    if not isinstance(global_evidence, dict):
        errors.append("field_evidence.global_overrides 必须是对象")
        global_evidence = {}
    for key in manufacturing.get("global_overrides", {}):
        _validate_evidence_refs(
            global_evidence.get(key),
            f"field_evidence.global_overrides.{key}",
            evidence,
            errors,
        )

    group_evidence = field_evidence.get("group_overrides", {})
    if not isinstance(group_evidence, dict):
        errors.append("field_evidence.group_overrides 必须是对象")
        group_evidence = {}
    for group, values in manufacturing.get("group_overrides", {}).items():
        group_map = group_evidence.get(str(group), {})
        if not isinstance(group_map, dict):
            errors.append(f"field_evidence.group_overrides.{group} 必须是对象")
            group_map = {}
        for key in values:
            _validate_evidence_refs(
                group_map.get(key),
                f"field_evidence.group_overrides.{group}.{key}",
                evidence,
                errors,
            )

    page_evidence = field_evidence.get("page_overrides", {})
    if not isinstance(page_evidence, dict):
        errors.append("field_evidence.page_overrides 必须是对象")
        page_evidence = {}
    for group, pages in manufacturing.get("page_overrides", {}).items():
        group_map = page_evidence.get(str(group), {})
        if not isinstance(group_map, dict):
            errors.append(f"field_evidence.page_overrides.{group} 必须是对象")
            group_map = {}
        for page, values in pages.items():
            page_map = group_map.get(str(page), {})
            if not isinstance(page_map, dict):
                errors.append(f"field_evidence.page_overrides.{group}.{page} 必须是对象")
                page_map = {}
            for key in values:
                _validate_evidence_refs(
                    page_map.get(key),
                    f"field_evidence.page_overrides.{group}.{page}.{key}",
                    evidence,
                    errors,
                )


def _valid_requirement_targets(
    request: dict[str, Any],
    expected_geometry_review: dict[str, dict[str, Any]],
) -> set[str]:
    targets = {"naming", "manufacturing.defaults"}
    for group, fields in expected_geometry_review.items():
        targets.update(f"geometry_review.{group}.{field}" for field in fields)
    manufacturing = request.get("manufacturing_requirements", {})
    if not isinstance(manufacturing, dict):
        return targets
    targets.update(
        f"manufacturing.global_overrides.{key}"
        for key in manufacturing.get("global_overrides", {})
    )
    for group, values in manufacturing.get("group_overrides", {}).items():
        targets.update(
            f"manufacturing.group_overrides.{group}.{key}" for key in values
        )
    for group, pages in manufacturing.get("page_overrides", {}).items():
        for page, values in pages.items():
            targets.update(
                f"manufacturing.page_overrides.{group}.{page}.{key}"
                for key in values
            )
    return targets


def _validate_evidence_disposition(
    analysis: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    valid_targets: set[str],
    errors: list[str],
) -> None:
    disposition = analysis.get("evidence_disposition", {})
    if not isinstance(disposition, dict):
        errors.append("requirement_analysis.evidence_disposition 必须是对象")
        return
    missing = sorted(set(evidence) - set(disposition))
    unknown = sorted(set(disposition) - set(evidence))
    if missing:
        errors.append("以下用户证据尚未分析: " + ", ".join(missing))
    if unknown:
        errors.append("evidence_disposition 引用了不存在的证据: " + ", ".join(unknown))
    for evidence_id, item in disposition.items():
        if not isinstance(item, dict):
            errors.append(f"evidence_disposition.{evidence_id} 必须是对象")
            continue
        status = item.get("status")
        if status not in {"mapped", "no_action"}:
            errors.append(
                f"evidence_disposition.{evidence_id}.status 必须是 mapped 或 no_action"
            )
            continue
        explanation = str(item.get("explanation", "")).strip()
        if not explanation:
            errors.append(f"evidence_disposition.{evidence_id}.explanation 不能为空")
        targets = item.get("targets", [])
        if status == "mapped":
            if not isinstance(targets, list) or not targets:
                errors.append(f"evidence_disposition.{evidence_id}.targets 不能为空")
                continue
            invalid = sorted(set(str(target) for target in targets) - valid_targets)
            if invalid:
                errors.append(
                    f"evidence_disposition.{evidence_id} 含无效目标: {', '.join(invalid)}"
                )
        elif targets not in ([], None):
            errors.append(f"no_action 证据 {evidence_id} 不能设置 targets")


def validate_agent_request(
    task_dir: str | os.PathLike[str],
    *,
    _lock: bool = True,
) -> dict[str, Any]:
    task = Path(task_dir).resolve()
    if _lock:
        with TaskDirectoryLock(task):
            return validate_agent_request(task, _lock=False)
    state = _load_state(task)
    request = _read_json(task / REQUEST_FILE)
    errors: list[str] = []
    warnings: list[str] = []
    questions: list[str] = []
    active_request_hash = _canonical_hash(request)
    if int(state.get("request_revision", 0)) < 1:
        errors.append("请求尚未通过 submit 原子提交")
    if state.get("request_hash") != active_request_hash:
        errors.append("agent_request.json 与已提交版本哈希不一致，禁止未版本化修改")
    if request.get("schema_version") != REQUEST_SCHEMA_VERSION:
        errors.append(f"schema_version 必须是 {REQUEST_SCHEMA_VERSION}")
    if request.get("task_id") != state.get("task_id"):
        errors.append("task_id 与任务目录状态不一致")

    protocol_path = task / str(state.get("agent_protocol_file", PROTOCOL_FILE))
    schema_path = task / str(state.get("request_schema_file", SCHEMA_FILE))
    spec_path = task / str(state.get("agent_spec_file", SPEC_FILE))
    if not state.get("agent_protocol_sha256") or not protocol_path.is_file():
        errors.append("任务缺少创建时锁定的 AGENT_PROTOCOL.md 快照")
    elif _sha256(protocol_path) != state.get("agent_protocol_sha256"):
        errors.append("任务内 AGENT_PROTOCOL.md 已被修改，禁止执行")
    if not state.get("request_schema_sha256") or not schema_path.is_file():
        errors.append("任务缺少创建时锁定的 agent_request.schema.json 快照")
    elif _sha256(schema_path) != state.get("request_schema_sha256"):
        errors.append("任务内 agent_request.schema.json 已被修改，禁止执行")
    if not state.get("agent_spec_sha256") or not spec_path.is_file():
        errors.append("任务缺少创建时锁定的 Agent spec 快照")
    elif _sha256(spec_path) != state.get("agent_spec_sha256"):
        errors.append("任务内 Agent spec 已被修改，禁止执行")
    try:
        current_runtime_identity = runtime_identity()
    except Exception as exc:
        errors.append(str(exc))
    else:
        if current_runtime_identity != state.get("agent_runtime_identity"):
            errors.append("Lens Drawing V4 运行时身份与任务创建时不一致，禁止执行")
    try:
        current_analysis_manifest = analysis_source_manifest(task)
    except AgentTaskError as exc:
        errors.append(str(exc))
    else:
        if current_analysis_manifest != state.get("source_analysis_manifest_sha256"):
            errors.append("source_analysis 与任务创建时不一致，禁止执行")

    source = request.get("source", {})
    if not isinstance(source, dict):
        errors.append("source 必须是对象")
    else:
        source_path = Path(str(source.get("zmx_path", ""))).resolve()
        if str(source_path) != state.get("source_file"):
            errors.append("source.zmx_path 与已分析的源文件不一致")
        if str(source.get("sha256", "")).lower() != state.get("source_sha256"):
            errors.append("source.sha256 与已分析的源文件不一致")
        if not source_path.is_file():
            errors.append(f"源 ZMX 不存在: {source_path}")
        elif _sha256(source_path) != state.get("source_sha256"):
            errors.append("源 ZMX 当前哈希与任务创建时不一致")

    evidence = _evidence_map(request, errors)
    execution = request.get("execution", {})
    execution_mode = (
        str(execution.get("mode", "production"))
        if isinstance(execution, dict)
        else "production"
    )
    if execution_mode not in {"production", "test"}:
        errors.append("execution.mode 必须是 production 或 test")
    if execution_mode == "production":
        operator_evidence = sorted(
            evidence_id
            for evidence_id, item in evidence.items()
            if item.get("kind") == "operator_record"
        )
        if operator_evidence:
            errors.append(
                "production 任务不能使用 operator_record 作为需求证据: "
                + ", ".join(operator_evidence)
            )
    _validate_attachment_evidence(evidence, errors)
    analysis = request.get("requirement_analysis", {})
    if not isinstance(analysis, dict):
        errors.append("requirement_analysis 必须是对象")
        analysis = {}
    if not str(analysis.get("user_goal_summary", "")).strip():
        errors.append("requirement_analysis.user_goal_summary 不能为空")
    unresolved = analysis.get("unresolved_questions", [])
    if not isinstance(unresolved, list):
        errors.append("requirement_analysis.unresolved_questions 必须是数组")
        unresolved = []
    questions.extend(str(item).strip() for item in unresolved if str(item).strip())
    if questions:
        errors.append("仍有未决问题，禁止执行")
    assumptions = analysis.get("assumptions", [])
    if assumptions not in ([], None):
        errors.append("执行请求不能包含未经用户确认的 assumptions")

    decisions = analysis.get("decisions", [])
    categories: set[str] = set()
    if not isinstance(decisions, list):
        errors.append("requirement_analysis.decisions 必须是数组")
    else:
        for index, decision in enumerate(decisions, start=1):
            if not isinstance(decision, dict):
                errors.append(f"requirement_analysis.decisions[{index}] 必须是对象")
                continue
            category = str(decision.get("category", "")).strip()
            if category not in {"naming", "manufacturing_complete", "geometry_review"}:
                errors.append(
                    f"决策 {index} category 必须是 naming、manufacturing_complete 或 geometry_review"
                )
            else:
                categories.add(category)
            if not str(decision.get("statement", "")).strip():
                errors.append(f"决策 {index} statement 不能为空")
            _validate_evidence_refs(
                decision.get("evidence_ids"),
                f"决策 {index}.evidence_ids",
                evidence,
                errors,
            )
    for required in ("naming", "manufacturing_complete"):
        if required not in categories:
            errors.append(f"缺少 {required} 决策记录")

    try:
        naming = validate_naming_policy_shape(request.get("naming"))
        _validate_evidence_refs(
            naming.get("evidence_ids"), "naming.evidence_ids", evidence, errors
        )
    except NamingError as exc:
        errors.append(str(exc))
        naming = None

    manufacturing = request.get("manufacturing_requirements")
    if not isinstance(manufacturing, dict):
        errors.append("manufacturing_requirements 必须是对象")
        patch = None
    else:
        evidence_ids = _validate_evidence_refs(
            manufacturing.get("evidence_ids"),
            "manufacturing_requirements.evidence_ids",
            evidence,
            errors,
        )
        payload = dict(manufacturing)
        payload.pop("field_evidence", None)
        payload["approval_evidence"] = {"evidence_ids": evidence_ids}
        _validate_manufacturing_field_evidence(manufacturing, evidence, errors)
        try:
            patch = approved_patch_from_payload(payload)
        except ProcessPatchError as exc:
            errors.append(str(exc))
            patch = None

    drafts = _draft_objects_from_analysis(task)
    expected_geometry_review: dict[str, dict[str, Any]] = {}
    for draft in drafts:
        if draft.status != "accepted":
            continue
        fields = {
            item.field: item.converted_value
            for item in draft.provenance
            if item.field.startswith(
                ("Glass", "T", "R", "MD", "AD", "Lens")
            )
            and item.confidence != "high"
        }
        if fields:
            expected_geometry_review[str(draft.group_index)] = fields
    geometry_review = request.get("geometry_review", {})
    if expected_geometry_review:
        if "geometry_review" not in categories:
            errors.append("存在中等置信几何字段，缺少 geometry_review 决策记录")
        if not isinstance(geometry_review, dict):
            errors.append("geometry_review 必须是对象")
        else:
            if geometry_review.get("approval_status") != "approved":
                errors.append("geometry_review.approval_status 必须是 approved")
            for key in ("approved_by", "approved_at", "reason"):
                if not str(geometry_review.get(key, "")).strip():
                    errors.append(f"geometry_review.{key} 不能为空")
            _validate_evidence_refs(
                geometry_review.get("evidence_ids"),
                "geometry_review.evidence_ids",
                evidence,
                errors,
            )
            if geometry_review.get("fields") != expected_geometry_review:
                errors.append(
                    "geometry_review.fields 必须与 source_analysis 中待确认字段和值完全一致，禁止修改几何"
                )
    elif geometry_review not in ({}, None):
        if not isinstance(geometry_review, dict):
            errors.append("geometry_review 必须是对象")
        elif geometry_review.get("fields") not in ({}, None):
            errors.append("当前任务没有需要确认的几何字段")
    _validate_evidence_disposition(
        analysis,
        evidence,
        _valid_requirement_targets(request, expected_geometry_review),
        errors,
    )
    if any(
        draft.status not in NON_BLOCKING_GEOMETRY_STATUSES
        for draft in drafts
    ):
        errors.append("ZMX 几何包含 blocked 镜片组，不能执行出图")
    if naming is not None:
        try:
            resolve_naming_policy(
                [draft for draft in drafts if draft.status == "accepted"], naming
            )
        except NamingError as exc:
            errors.append(str(exc))
    if patch is not None:
        try:
            validate_patch_for_drafts(patch, drafts)
            renderer_root = request.get("execution", {}).get(
                "renderer_root", state.get("renderer_root")
            )
            for draft in drafts:
                if draft.status == "accepted":
                    if naming is not None:
                        resolved = resolve_naming_policy(
                            [item for item in drafts if item.status == "accepted"],
                            naming,
                        )
                        for field, value in resolved.get(str(draft.group_index), {}).items():
                            draft.row[field] = value
                    preflight_draft(draft, renderer_root, patch)
        except ProcessPatchError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(f"绘图引擎加工要求预检失败: {type(exc).__name__}: {exc}")

    if not isinstance(execution, dict):
        errors.append("execution 必须是对象")
    else:
        if execution.get("automated_pdf_validation") is not True:
            errors.append("必须启用 automated_pdf_validation")
        if execution.get("human_visual_review_required") is not True:
            errors.append("必须启用 human_visual_review_required")
        renderer_root = Path(
            str(execution.get("renderer_root", state.get("renderer_root", "")))
        ).resolve()
        if str(renderer_root) != state.get("renderer_root"):
            errors.append("execution.renderer_root 与任务创建时锁定的绘图引擎不一致")
        current_manifest = renderer_source_manifest(renderer_root)
        if current_manifest != state.get("renderer_source_manifest_sha256"):
            errors.append("绘图引擎源码哈希与任务创建时不一致")

    validation = {
        "schema_version": "1.0",
        "validated_at": _now(),
        "request_hash": active_request_hash,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "unresolved_questions": questions,
        "next_action": (
            "修正 agent_request.json 中的错误并重新 validate。"
            if errors
            else "请求已就绪，可以运行 run。"
        ),
    }
    _write_json(task / "request_validation.json", validation)
    current_status = state.get("status")
    terminal_statuses = {
        "running",
        "awaiting_human_review",
        "completed",
        "release_blocked",
        "human_review_failed",
        "validation_failed",
        "execution_failed",
    }
    if current_status in terminal_statuses and state.get("request_hash") == validation["request_hash"]:
        next_status = current_status
    elif any(
        draft.status not in NON_BLOCKING_GEOMETRY_STATUSES
        for draft in drafts
    ):
        next_status = "blocked_geometry"
    else:
        next_status = "needs_clarification" if errors else "ready"
    _update_state(
        task,
        status=next_status,
        status_note=("Agent 请求尚未满足执行契约。" if errors else "Agent 请求已通过执行前校验。"),
        unresolved_questions=questions,
        next_action=validation["next_action"],
    )
    return validation


def run_agent_task(
    task_dir: str | os.PathLike[str],
    *,
    _lock: bool = True,
) -> dict[str, Any]:
    task = Path(task_dir).resolve()
    if _lock:
        with TaskDirectoryLock(task):
            return run_agent_task(task, _lock=False)
    state = _load_state(task)
    request = _read_json(task / REQUEST_FILE)
    current_hash = _canonical_hash(request)
    result_dir = task / RESULT_DIR
    if state.get("status") in {"awaiting_human_review", "completed", "release_blocked"}:
        if state.get("request_hash") == current_hash and result_dir.is_dir():
            return _read_json(result_dir / "audit.json")
        raise AgentTaskError("任务已有不同请求或结果，拒绝覆盖；请创建新任务目录")
    validation = validate_agent_request(task, _lock=False)
    if not validation["valid"]:
        raise AgentTaskError("Agent 请求未通过校验，请读取 request_validation.json")
    state = _load_state(task)
    request = _read_json(task / REQUEST_FILE)
    request_hash = validation["request_hash"]
    if result_dir.exists() and any(result_dir.iterdir()):
        raise AgentTaskError("result 目录非空，拒绝覆盖旧结果")

    source = Path(request["source"]["zmx_path"]).resolve()
    manufacturing = dict(request["manufacturing_requirements"])
    manufacturing.pop("field_evidence", None)
    manufacturing["approval_evidence"] = {
        "evidence_ids": manufacturing.pop("evidence_ids")
    }
    patch = approved_patch_from_payload(manufacturing)
    _update_state(
        task,
        status="running",
        status_note="正在重新核对 ZMX 并执行绘图。",
        request_hash=request_hash,
        unresolved_questions=[],
        next_action="等待流水线完成。",
    )
    try:
        audit = run_pipeline(
            source,
            result_dir,
            renderer_root=request["execution"]["renderer_root"],
            process_patch=patch,
            naming_policy=request["naming"],
            task_context={
                "task_id": request["task_id"],
                "request_hash": request_hash,
                "request_file": str(task / REQUEST_FILE),
                "user_evidence": request["user_evidence"],
                "requirement_analysis": request["requirement_analysis"],
                "execution_mode": request["execution"].get("mode", "production"),
                "agent_interface_version": state.get("agent_interface_version"),
                "agent_runtime_identity": state.get("agent_runtime_identity"),
                "agent_spec_sha256": state.get("agent_spec_sha256"),
                "source_analysis_manifest_sha256": state.get("source_analysis_manifest_sha256"),
            },
            geometry_acknowledgements=request.get("geometry_review"),
        )
        if not audit.get("drawings_generated"):
            _update_state(
                task,
                status="execution_failed",
                status_note="几何阻断或 PDF 生成失败。",
                result_dir=str(result_dir),
                next_action="读取 result/audit.json 的 blocked_groups 和 render_errors。",
            )
            return audit

        from .output_validation import validate

        report = validate(
            result_dir,
            task / RENDER_DIR,
            "pending",
            "等待人工操作员逐页目视检查。",
        )
        if not report["automated_checks_passed"]:
            status = "validation_failed"
            note = "PDF 自动字段或渲染检查失败。"
            action = "读取 result/pdf_validation_report.json 并修正后创建新任务。"
        else:
            status = "awaiting_human_review"
            note = "自动检查通过，等待人工操作员逐页目视验收。"
            action = "由授权操作员检查 validation_render/contact_sheet_*.png 后运行 review。"
        _update_state(
            task,
            status=status,
            status_note=note,
            result_dir=str(result_dir),
            automated_checks_passed=report["automated_checks_passed"],
            next_action=action,
        )
        return audit
    except Exception as exc:
        _update_state(
            task,
            status="execution_failed",
            status_note=f"{type(exc).__name__}: {exc}",
            result_dir=str(result_dir) if result_dir.exists() else None,
            next_action="读取错误与现有审计文件；不要覆盖目录，修正请求后创建新任务。",
        )
        raise


def record_human_visual_review(
    task_dir: str | os.PathLike[str],
    *,
    status: str,
    reviewer: str,
    note: str,
    _lock: bool = True,
) -> dict[str, Any]:
    task = Path(task_dir).resolve()
    if _lock:
        with TaskDirectoryLock(task):
            return record_human_visual_review(
                task,
                status=status,
                reviewer=reviewer,
                note=note,
                _lock=False,
            )
    state = _load_state(task)
    if state.get("status") not in {"awaiting_human_review", "human_review_failed"}:
        raise AgentTaskError(
            f"当前状态 {state.get('status')} 不允许提交人工视觉验收"
        )
    if status not in {"passed", "failed"}:
        raise AgentTaskError("人工视觉验收状态必须是 passed 或 failed")
    reviewer = reviewer.strip()
    note = note.strip()
    if not reviewer or not note:
        raise AgentTaskError("人工视觉验收必须填写 reviewer 和 note")

    result_dir = Path(state["result_dir"])
    from .output_validation import validate

    report = validate(result_dir, task / RENDER_DIR, status, note)
    audit = _read_json(result_dir / "audit.json")
    request = _read_json(task / REQUEST_FILE)
    execution_mode = request.get("execution", {}).get("mode", "production")
    review = {
        "schema_version": "1.0",
        "review_kind": "human_operator",
        "reviewed_at": _now(),
        "status": status,
        "reviewer": reviewer,
        "note": note,
        "contact_sheets": [
            {"path": path, "sha256": _sha256(Path(path))}
            for path in report["human_visual_review"]["contact_sheets"]
        ],
        "pdfs": [
            {"path": path, "sha256": _sha256(Path(path))}
            for path in audit.get("rendered_pdfs", [])
        ],
        "request_hash": state.get("request_hash"),
    }
    _write_json(task / HUMAN_REVIEW_FILE, review)
    release_gate_passed = (
        bool(audit.get("production_release_ready"))
        if execution_mode == "production"
        else True
    )
    completed = bool(report["all_checks_passed"] and release_gate_passed)
    delivery = {
        "schema_version": "1.0",
        "task_id": state["task_id"],
        "request_hash": state.get("request_hash"),
        "completed": completed,
        "execution_mode": execution_mode,
        "production_release_ready": bool(audit.get("production_release_ready")),
        "source_file": state["source_file"],
        "source_sha256": state["source_sha256"],
        "audit": str(result_dir / "audit.json"),
        "pdf_validation_report": str(result_dir / "pdf_validation_report.json"),
        "human_visual_review": str(task / HUMAN_REVIEW_FILE),
        "manufacturing_requirements": str(
            result_dir / "manufacturing_requirements_delivery.json"
        ),
        "manufacturing_requirements_summary": str(
            result_dir / "manufacturing_requirements_summary.md"
        ),
        "excluded_components": audit.get("excluded_components", []),
        "geometry_warnings": audit.get("geometry_warnings", []),
        "pdfs": [
            str(path)
            for path in sorted((result_dir / "drawings").rglob("*.pdf"))
        ],
    }
    _write_json(task / "delivery_manifest.json", delivery)
    _update_state(
        task,
        status=(
            "completed"
            if completed
            else ("release_blocked" if status == "passed" else "human_review_failed")
        ),
        status_note=(
            "自动校验与人工视觉验收均通过。"
            if completed
            else (
                "人工视觉验收通过，但生产放行门槛未满足。"
                if status == "passed"
                else "人工视觉验收未通过。"
            )
        ),
        next_action=(
            "读取 delivery_manifest.json 向用户交付结果。"
            if completed
            else "根据视觉问题修正后创建新任务，不得覆盖当前审计。"
        ),
    )
    return delivery


def record_visual_review(
    task_dir: str | os.PathLike[str],
    *,
    status: str,
    reviewer: str,
    note: str,
    _lock: bool = True,
) -> dict[str, Any]:
    """Compatibility alias for callers using the original Python symbol."""
    return record_human_visual_review(
        task_dir,
        status=status,
        reviewer=reviewer,
        note=note,
        _lock=_lock,
    )


def get_task_status(task_dir: str | os.PathLike[str]) -> dict[str, Any]:
    return _load_state(Path(task_dir).resolve())
