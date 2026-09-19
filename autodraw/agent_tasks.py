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

from .deployment_policy import DeploymentPolicyError, load_deployment_policy
from .geometry_resolution import (
    GeometryResolutionError,
    apply_geometry_decision,
    build_geometry_cases,
    system_from_payload,
)
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
DEPLOYMENT_POLICY_FILE = "deployment_policy.json"
GEOMETRY_CASES_FILE = "geometry_cases.json"
GEOMETRY_RESOLUTION_FILE = "geometry_resolution.json"
VISUAL_REVIEW_FILE = "visual_review.json"
ANALYSIS_FILES = (
    "extracted_system.json",
    GEOMETRY_CASES_FILE,
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
            "agent_selects_candidate_ids_only": True,
            "authoritative_geometry": "drawing_drafts[].lenses[]",
            "virtual_interface_ad": "preserve adjacent lens side-specific AD values",
        },
        "review_policy": {
            "required": True,
            "reviewer_kinds": ["vision_agent", "human_operator"],
            "production_default": "vision_agent",
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
        "deployment_policy_file": DEPLOYMENT_POLICY_FILE,
        "geometry_cases_file": f"{ANALYSIS_DIR}/{GEOMETRY_CASES_FILE}",
        "geometry_resolution_file": GEOMETRY_RESOLUTION_FILE,
        "visual_review_file": VISUAL_REVIEW_FILE,
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
        f"- Deployment policy: `{task_dir / DEPLOYMENT_POLICY_FILE}`",
        f"- Deployment policy SHA-256: `{state.get('deployment_policy_sha256', '')}`",
        f"- Geometry cases: `{task_dir / ANALYSIS_DIR / GEOMETRY_CASES_FILE}`",
        f"- Geometry resolution: `{task_dir / GEOMETRY_RESOLUTION_FILE}`",
        f"- Request: `{task_dir / REQUEST_FILE}`",
        f"- Request validation: `{task_dir / 'request_validation.json'}`",
        f"- Result: `{result_dir or ''}`",
        "",
        "## Resume Contract",
        "",
        "1. Read `task_state.json` first; do not infer status from chat history.",
        "2. Read task-local `AGENT_PROTOCOL.md`, spec and `source_analysis/agent_work_order.json`.",
        "3. Resolve geometry only by selecting IDs from geometry_cases.json.",
        "4. Link every naming and manufacturing decision to a user evidence ID.",
        "5. Do not run while `requirement_analysis.unresolved_questions` is non-empty.",
        "6. A completed task requires automated PDF checks and the configured visual review record.",
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
    system_payload, stored_drafts, _ = _load_analysis(task_dir)
    system = system_from_payload(system_payload)
    drafts = map_to_drafts(system)
    stored_cases = _read_json(task_dir / ANALYSIS_DIR / GEOMETRY_CASES_FILE)
    current_cases = build_geometry_cases(
        system, drafts, task_id=_load_state(task_dir)["task_id"]
    )
    if current_cases != stored_cases:
        raise AgentTaskError("geometry_cases.json 与已提取 ZOS-API 数据不一致")
    resolution_path = task_dir / GEOMETRY_RESOLUTION_FILE
    if current_cases.get("resolution_required"):
        if not resolution_path.is_file():
            raise AgentTaskError("任务尚未完成 resolve-geometry")
        try:
            drafts = apply_geometry_decision(
                drafts, current_cases, _read_json(resolution_path)
            )
        except GeometryResolutionError as exc:
            raise AgentTaskError(str(exc)) from exc
    if [draft.to_dict() for draft in drafts] != stored_drafts:
        raise AgentTaskError("drawing_drafts.json 与权威几何候选/选择不一致")
    return drafts


def _request_template(
    task_id: str,
    source: Path,
    source_hash: str,
    renderer_root: Path,
    policy: dict[str, Any],
) -> dict[str, Any]:
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
        "manufacturing_requirements": {
            "approval_status": "approved",
            "approve_effective_manufacturing_requirements": True,
            "approval_source": "deployment_policy",
            "policy_id": policy["policy_id"],
            "policy_sha256": policy["policy_sha256"],
            "approved_by": policy["approved_by"],
            "approved_at": policy["approved_at"],
            "source": "deployment_policy",
            "reason": "部署级加工默认值已批准；本任务仅记录用户明确提出的覆盖项。",
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
            "renderer_root": str(renderer_root),
            "automated_pdf_validation": True,
            "visual_review": {
                "required": True,
                "mode": policy["visual_review"]["mode"],
            },
        },
    }


def _analysis_summary_payload(
    drafts: list[Any],
    geometry_cases: dict[str, Any],
    *,
    resolved: bool,
) -> dict[str, Any]:
    return {
        "accepted_groups": [d.group_index for d in drafts if d.status == "accepted"],
        "excluded_groups": [d.group_index for d in drafts if d.status == "excluded"],
        "blocked_groups": [
            d.group_index
            for d in drafts
            if d.status not in NON_BLOCKING_GEOMETRY_STATUSES
        ],
        "hard_blocked_groups": geometry_cases.get("hard_blocked_groups", []),
        "geometry_resolution_required": bool(
            geometry_cases.get("resolution_required")
        ),
        "geometry_resolved": resolved,
        "required_customer_confirmations": (
            _geometry_confirmation_requirements(drafts) if resolved else []
        ),
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
        "geometry_cases": [
            {
                "case_id": case["case_id"],
                "group_index": case["group_index"],
                "resolution_required": case["resolution_required"],
                "required_field_selections": case["required_field_selections"],
                "hard_blockers": case["hard_blockers"],
            }
            for case in geometry_cases.get("cases", [])
        ],
    }


def _geometry_confirmation_requirements(
    drafts: list[Any],
) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    seen: set[str] = set()
    for draft in drafts:
        for item in draft.topology.get("required_customer_confirmations", []):
            if not isinstance(item, dict):
                continue
            confirmation_id = str(item.get("confirmation_id", "")).strip()
            if not confirmation_id or confirmation_id in seen:
                continue
            seen.add(confirmation_id)
            requirements.append(dict(item))
    return sorted(requirements, key=lambda item: item["confirmation_id"])


def create_agent_task(
    source_file: str | os.PathLike[str],
    task_dir: str | os.PathLike[str],
    *,
    renderer_root: str | os.PathLike[str] = DEFAULT_RENDERER_ROOT,
    deployment_policy: str | os.PathLike[str],
    zemax_root: str | os.PathLike[str] | None = None,
    opticstudio_install_dir: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    source = Path(source_file).resolve()
    destination = Path(task_dir).resolve()
    if not source.is_file() or source.suffix.lower() != ".zmx":
        raise AgentTaskError(f"有效的 .zmx 文件不存在: {source}")
    policy_source = Path(deployment_policy).expanduser().resolve()
    try:
        load_deployment_policy(policy_source)
    except DeploymentPolicyError as exc:
        raise AgentTaskError(str(exc)) from exc
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
        shutil.copy2(policy_source, destination / DEPLOYMENT_POLICY_FILE)
        policy = load_deployment_policy(destination / DEPLOYMENT_POLICY_FILE)
        protocol_hash = _sha256(destination / PROTOCOL_FILE)
        schema_hash = _sha256(destination / SCHEMA_FILE)
        agent_spec_hash = _sha256(destination / SPEC_FILE)
        locked_runtime_identity = runtime_identity()
        zosapi_config = {
            "zemax_root": str(
                Path(zemax_root or policy["zosapi"]["zemax_root"])
                .expanduser()
                .resolve()
            ),
            "opticstudio_install_dir": str(
                Path(
                    opticstudio_install_dir
                    or policy["zosapi"]["opticstudio_install_dir"]
                )
                .expanduser()
                .resolve()
            ),
        }
        with NativeZosApiProvider(
            install_dir=zosapi_config["opticstudio_install_dir"],
            zemax_root=zosapi_config["zemax_root"],
        ) as provider:
            system = provider.extract(source)
        drafts = map_to_drafts(system)
        geometry_cases = build_geometry_cases(system, drafts, task_id=task_id)
        analysis_dir = destination / ANALYSIS_DIR
        analysis_dir.mkdir()
        _write_json(analysis_dir / "extracted_system.json", system.to_dict())
        _write_json(analysis_dir / GEOMETRY_CASES_FILE, geometry_cases)
        draft_payload = [draft.to_dict() for draft in drafts]
        _write_json(analysis_dir / "drawing_drafts.json", draft_payload)

        renderer_path = Path(renderer_root).resolve()
        defaults: dict[str, Any] = get_agent_default_settings()
        defaults.update(policy["manufacturing_defaults"])
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
        work_order["deployment_policy"] = {
            "policy_id": policy["policy_id"],
            "policy_sha256": policy["policy_sha256"],
            "manufacturing_defaults_sha256": policy[
                "manufacturing_defaults_sha256"
            ],
        }
        _write_json(analysis_dir / "agent_work_order.json", work_order)
        _write_json(
            analysis_dir / "analysis_summary.json",
            _analysis_summary_payload(drafts, geometry_cases, resolved=False),
        )
        analysis_manifest = analysis_source_manifest(destination)
        request = _request_template(
            task_id, source, system.source_sha256, renderer_path, policy
        )
        _write_json(destination / REQUEST_FILE, request)
        initial_hash = _canonical_hash(request)
        _write_json(
            destination / "request_versions" / f"000_initial_{initial_hash[:12]}.json",
            request,
        )
        hard_blocked = bool(geometry_cases["hard_blocked_groups"])
        resolution_required = bool(geometry_cases["resolution_required"])
        initial_status = (
            "blocked_geometry"
            if hard_blocked
            else "awaiting_geometry_resolution"
            if resolution_required
            else "needs_input"
        )
        state = {
            "schema_version": TASK_SCHEMA_VERSION,
            "task_id": task_id,
            "created_at": created_at,
            "updated_at": created_at,
            "status": initial_status,
            "status_note": (
                "ZMX 几何存在不可由候选选择解除的阻断项。"
                if hard_blocked
                else "ZMX 候选已冻结，等待 Agent 提交候选 ID 选择。"
                if resolution_required
                else "ZMX 分析完成，等待用户命名与特殊加工要求。"
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
            "geometry_cases_sha256": _canonical_hash(geometry_cases),
            "geometry_resolution_sha256": None,
            "deployment_policy_file": DEPLOYMENT_POLICY_FILE,
            "deployment_policy_sha256": policy["policy_sha256"],
            "deployment_policy_id": policy["policy_id"],
            "manufacturing_defaults_sha256": policy[
                "manufacturing_defaults_sha256"
            ],
            "visual_review_mode": policy["visual_review"]["mode"],
            "zosapi_config": zosapi_config,
            "source_file": str(source),
            "source_sha256": system.source_sha256,
            "renderer_root": str(renderer_path),
            "renderer_source_manifest_sha256": renderer_source_manifest(renderer_path),
            "request_hash": None,
            "request_revision": 0,
            "result_dir": None,
            "unresolved_questions": request["requirement_analysis"]["unresolved_questions"],
            "next_action": (
                "读取 source_analysis/geometry_cases.json 中的 hard_blockers。"
                if hard_blocked
                else "读取 geometry_cases.json，仅提交候选 ID 到 resolve-geometry。"
                if resolution_required
                else "Agent 整理命名和用户明确提出的加工覆盖后提交请求。"
            ),
            "history": [
                {
                    "at": created_at,
                    "status": initial_status,
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


def resolve_agent_geometry(
    task_dir: str | os.PathLike[str],
    decision_file: str | os.PathLike[str],
    *,
    _lock: bool = True,
) -> dict[str, Any]:
    task = Path(task_dir).resolve()
    if _lock:
        with TaskDirectoryLock(task):
            return resolve_agent_geometry(task, decision_file, _lock=False)
    state = _load_state(task)
    decision = _read_json(Path(decision_file).resolve())
    decision_hash = _canonical_hash(decision)
    resolution_path = task / GEOMETRY_RESOLUTION_FILE
    if resolution_path.is_file():
        existing = _read_json(resolution_path)
        if _canonical_hash(existing) == decision_hash:
            return state
        raise AgentTaskError("任务已存在不同的 geometry_resolution.json，拒绝覆盖")
    if state.get("status") not in {
        "awaiting_geometry_resolution",
        "geometry_resolution_failed",
    }:
        raise AgentTaskError(
            f"当前状态 {state.get('status')} 不允许 resolve-geometry"
        )

    analysis_dir = task / ANALYSIS_DIR
    system = system_from_payload(_read_json(analysis_dir / "extracted_system.json"))
    drafts = map_to_drafts(system)
    stored_cases = _read_json(analysis_dir / GEOMETRY_CASES_FILE)
    current_cases = build_geometry_cases(system, drafts, task_id=state["task_id"])
    if current_cases != stored_cases:
        raise AgentTaskError("geometry_cases.json 与已提取 ZOS-API 数据不一致")
    if _canonical_hash(stored_cases) != state.get("geometry_cases_sha256"):
        raise AgentTaskError("geometry_cases.json 哈希与任务状态不一致")
    if not stored_cases.get("resolution_required"):
        raise AgentTaskError("当前任务没有需要 Agent 选择的几何候选")
    try:
        resolved = apply_geometry_decision(drafts, stored_cases, decision)
    except GeometryResolutionError as exc:
        _write_json(
            task / "geometry_resolution_error.json",
            {
                "schema_version": "1.0",
                "failed_at": _now(),
                "decision_sha256": decision_hash,
                "error": str(exc),
            },
        )
        _update_state(
            task,
            status="geometry_resolution_failed",
            status_note=str(exc),
            next_action=(
                "重新读取 geometry_cases.json，仅修正候选 ID 选择后再次运行 "
                "resolve-geometry。"
            ),
        )
        raise AgentTaskError(str(exc)) from exc

    _write_json(resolution_path, decision)
    draft_payload = [draft.to_dict() for draft in resolved]
    _write_json(analysis_dir / "drawing_drafts.json", draft_payload)
    policy = load_deployment_policy(task / DEPLOYMENT_POLICY_FILE)
    defaults = get_agent_default_settings()
    defaults.update(policy["manufacturing_defaults"])
    work_order = build_ai_work_order(draft_payload, defaults)
    work_order["source"] = {
        "zmx_path": state["source_file"],
        "sha256": state["source_sha256"],
    }
    work_order["renderer_source_manifest_sha256"] = state[
        "renderer_source_manifest_sha256"
    ]
    work_order["agent_runtime_identity"] = state["agent_runtime_identity"]
    work_order["agent_spec_sha256"] = state["agent_spec_sha256"]
    work_order["deployment_policy"] = {
        "policy_id": policy["policy_id"],
        "policy_sha256": policy["policy_sha256"],
        "manufacturing_defaults_sha256": policy[
            "manufacturing_defaults_sha256"
        ],
    }
    _write_json(analysis_dir / "agent_work_order.json", work_order)
    _write_json(
        analysis_dir / "analysis_summary.json",
        _analysis_summary_payload(resolved, stored_cases, resolved=True),
    )
    analysis_manifest = analysis_source_manifest(task)
    request = _read_json(task / REQUEST_FILE)
    confirmations = _geometry_confirmation_requirements(resolved)
    existing_questions = [
        str(item).strip()
        for item in request.get("requirement_analysis", {}).get(
            "unresolved_questions", []
        )
        if str(item).strip()
    ]
    confirmation_questions = [
        str(item["prompt"]).strip()
        for item in confirmations
        if str(item.get("prompt", "")).strip()
    ]
    unresolved_questions = list(
        dict.fromkeys(existing_questions + confirmation_questions)
    )
    request["requirement_analysis"]["unresolved_questions"] = unresolved_questions
    request_hash = _canonical_hash(request)
    _write_json(task / REQUEST_FILE, request)
    _write_json(
        task
        / "request_versions"
        / f"000_after_geometry_{request_hash[:12]}.json",
        request,
    )
    next_status = "needs_clarification" if confirmations else "needs_input"
    return _update_state(
        task,
        status=next_status,
        status_note=(
            "几何候选选择已冻结，但低置信字段必须取得用户确认。"
            if confirmations
            else "几何候选选择已通过确定性校验并冻结。"
        ),
        geometry_resolution_sha256=decision_hash,
        source_analysis_manifest_sha256=analysis_manifest,
        required_geometry_confirmations=confirmations,
        unresolved_questions=unresolved_questions,
        next_action=(
            "通过 DWS 逐项取得 required_geometry_confirmations 的用户确认，"
            "再连同命名和加工要求提交请求。"
            if confirmations
            else "整理命名和用户明确提出的加工覆盖，随后 submit 并 validate。"
        ),
    )


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
        "awaiting_geometry_resolution", "geometry_resolution_failed", "running",
        "awaiting_visual_review", "completed", "visual_review_failed",
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
    geometry_confirmations: list[dict[str, Any]] | None = None,
) -> set[str]:
    targets = {"naming", "manufacturing.deployment_policy"}
    targets.update(
        f"geometry_confirmation.{item['confirmation_id']}"
        for item in (geometry_confirmations or [])
        if str(item.get("confirmation_id", "")).strip()
    )
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
    policy = None
    policy_path = task / str(
        state.get("deployment_policy_file", DEPLOYMENT_POLICY_FILE)
    )
    if not policy_path.is_file():
        errors.append("任务缺少创建时锁定的 deployment_policy.json 快照")
    elif _sha256(policy_path) != state.get("deployment_policy_sha256"):
        errors.append("任务内 deployment_policy.json 已被修改，禁止执行")
    else:
        try:
            policy = load_deployment_policy(policy_path)
        except DeploymentPolicyError as exc:
            errors.append(str(exc))
        else:
            if policy.get("policy_id") != state.get("deployment_policy_id"):
                errors.append("部署策略 policy_id 与任务创建时不一致")
            if policy.get("manufacturing_defaults_sha256") != state.get(
                "manufacturing_defaults_sha256"
            ):
                errors.append("部署加工默认值哈希与任务创建时不一致")
    try:
        current_runtime_identity = runtime_identity()
    except Exception as exc:
        errors.append(str(exc))
    else:
        if current_runtime_identity != state.get("agent_runtime_identity"):
            errors.append("Lens Drawing 运行时身份与任务创建时不一致，禁止执行")
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

    drafts = _draft_objects_from_analysis(task)
    required_geometry_confirmations = _geometry_confirmation_requirements(drafts)
    if state.get("required_geometry_confirmations", []) != required_geometry_confirmations:
        errors.append("task_state.required_geometry_confirmations 与权威几何分析不一致")

    decisions = analysis.get("decisions", [])
    categories: set[str] = set()
    submitted_geometry_confirmations: set[str] = set()
    if not isinstance(decisions, list):
        errors.append("requirement_analysis.decisions 必须是数组")
    else:
        for index, decision in enumerate(decisions, start=1):
            if not isinstance(decision, dict):
                errors.append(f"requirement_analysis.decisions[{index}] 必须是对象")
                continue
            category = str(decision.get("category", "")).strip()
            if category not in {
                "naming",
                "manufacturing_complete",
                "geometry_confirmation",
            }:
                errors.append(
                    f"决策 {index} category 必须是 naming、manufacturing_complete "
                    "或 geometry_confirmation"
                )
            else:
                categories.add(category)
            if category == "geometry_confirmation":
                confirmation_id = str(
                    decision.get("confirmation_id", "")
                ).strip()
                if not confirmation_id:
                    errors.append(
                        f"决策 {index}.confirmation_id 不能为空"
                    )
                elif confirmation_id in submitted_geometry_confirmations:
                    errors.append(
                        f"几何确认重复: {confirmation_id}"
                    )
                else:
                    submitted_geometry_confirmations.add(confirmation_id)
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
    required_confirmation_ids = {
        str(item.get("confirmation_id", "")).strip()
        for item in required_geometry_confirmations
        if isinstance(item, dict)
        and str(item.get("confirmation_id", "")).strip()
    }
    missing_confirmations = sorted(
        required_confirmation_ids - submitted_geometry_confirmations
    )
    unknown_confirmations = sorted(
        submitted_geometry_confirmations - required_confirmation_ids
    )
    if missing_confirmations:
        errors.append(
            "以下低置信几何尚未取得用户确认: "
            + ", ".join(missing_confirmations)
        )
    if unknown_confirmations:
        errors.append(
            "geometry_confirmation 引用了未知确认项: "
            + ", ".join(unknown_confirmations)
        )

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
        evidence_ids = manufacturing.get("evidence_ids", [])
        if evidence_ids not in ([], None):
            evidence_ids = _validate_evidence_refs(
                evidence_ids,
                "manufacturing_requirements.evidence_ids",
                evidence,
                errors,
            )
        else:
            evidence_ids = []
        if manufacturing.get("approval_source") != "deployment_policy":
            errors.append(
                "manufacturing_requirements.approval_source 必须是 deployment_policy"
            )
        if policy is not None:
            expected_policy_fields = {
                "policy_id": policy["policy_id"],
                "policy_sha256": policy["policy_sha256"],
                "approved_by": policy["approved_by"],
                "approved_at": policy["approved_at"],
            }
            for key, expected in expected_policy_fields.items():
                if manufacturing.get(key) != expected:
                    errors.append(
                        f"manufacturing_requirements.{key} 与任务部署策略不一致"
                    )
        payload = dict(manufacturing)
        payload.pop("field_evidence", None)
        payload["approval_evidence"] = {
            "deployment_policy_id": state.get("deployment_policy_id"),
            "deployment_policy_sha256": state.get("deployment_policy_sha256"),
            "user_override_evidence_ids": evidence_ids,
        }
        _validate_manufacturing_field_evidence(manufacturing, evidence, errors)
        try:
            patch = approved_patch_from_payload(payload)
        except ProcessPatchError as exc:
            errors.append(str(exc))
            patch = None

    _validate_evidence_disposition(
        analysis,
        evidence,
        _valid_requirement_targets(request, required_geometry_confirmations),
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
                    base_settings = get_agent_default_settings()
                    if policy is not None:
                        base_settings.update(policy["manufacturing_defaults"])
                    preflight_draft(
                        draft,
                        renderer_root,
                        patch,
                        base_settings=base_settings,
                    )
        except ProcessPatchError as exc:
            errors.append(str(exc))
        except Exception as exc:
            errors.append(f"绘图引擎加工要求预检失败: {type(exc).__name__}: {exc}")

    if not isinstance(execution, dict):
        errors.append("execution 必须是对象")
    else:
        if execution.get("automated_pdf_validation") is not True:
            errors.append("必须启用 automated_pdf_validation")
        visual_review = execution.get("visual_review")
        if not isinstance(visual_review, dict):
            errors.append("execution.visual_review 必须是对象")
        else:
            if visual_review.get("required") is not True:
                errors.append("execution.visual_review.required 必须是 true")
            if visual_review.get("mode") != state.get("visual_review_mode"):
                errors.append(
                    "execution.visual_review.mode 与任务部署策略不一致"
                )
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
        "awaiting_visual_review",
        "completed",
        "release_blocked",
        "visual_review_failed",
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
    if state.get("status") in {"awaiting_visual_review", "completed", "release_blocked"}:
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
        "deployment_policy_id": state.get("deployment_policy_id"),
        "deployment_policy_sha256": state.get("deployment_policy_sha256"),
        "user_override_evidence_ids": manufacturing.pop("evidence_ids", []),
    }
    patch = approved_patch_from_payload(manufacturing)
    policy = load_deployment_policy(task / DEPLOYMENT_POLICY_FILE)
    geometry_cases = _read_json(task / ANALYSIS_DIR / GEOMETRY_CASES_FILE)
    resolution_path = task / GEOMETRY_RESOLUTION_FILE
    geometry_decision = (
        _read_json(resolution_path) if resolution_path.is_file() else None
    )
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
                "deployment_policy_id": state.get("deployment_policy_id"),
                "deployment_policy_sha256": state.get("deployment_policy_sha256"),
                "geometry_cases_sha256": state.get("geometry_cases_sha256"),
                "geometry_resolution_sha256": state.get(
                    "geometry_resolution_sha256"
                ),
            },
            deployment_policy=policy,
            geometry_cases=geometry_cases,
            geometry_decision=geometry_decision,
            zosapi_config=state.get("zosapi_config", {}),
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
            "等待部署策略指定的视觉检查。",
        )
        if not report["automated_checks_passed"]:
            status = "validation_failed"
            note = "PDF 自动字段或渲染检查失败。"
            action = "读取 result/pdf_validation_report.json 并修正后创建新任务。"
        else:
            status = "awaiting_visual_review"
            note = (
                "自动检查通过，等待 "
                f"{state.get('visual_review_mode')} 提交结构化视觉报告。"
            )
            action = (
                "检查 validation_render/contact_sheet_*.png，并以配置的 kind 和 "
                "--report 运行 review。"
            )
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


def _validated_external_review_report(
    report_file: str | os.PathLike[str],
    *,
    kind: str,
    status: str,
    expected_contact_sheets: list[str],
    expected_pdfs: list[str],
) -> dict[str, Any]:
    payload = _read_json(Path(report_file).resolve())
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise AgentTaskError("视觉报告必须是 schema_version=1.0 的 JSON 对象")
    if payload.get("status") != status:
        raise AgentTaskError("视觉报告 status 与 review --status 不一致")
    if payload.get("review_kind") != kind:
        raise AgentTaskError("视觉报告 review_kind 与 review --kind 不一致")
    for key in ("issues", "uncertainties"):
        if not isinstance(payload.get(key), list):
            raise AgentTaskError(f"视觉报告 {key} 必须是数组")
    if status == "passed" and (payload["issues"] or payload["uncertainties"]):
        raise AgentTaskError("passed 视觉报告不能包含未解决问题或不确定项")
    if kind == "vision_agent":
        for key in ("model", "prompt_version"):
            if not str(payload.get(key, "")).strip():
                raise AgentTaskError(f"机器视觉报告 {key} 不能为空")
        duration = payload.get("duration_ms")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise AgentTaskError("机器视觉报告 duration_ms 必须是非负数值")
        if duration < 0:
            raise AgentTaskError("机器视觉报告 duration_ms 不能为负数")

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        raise AgentTaskError("视觉报告 artifacts 必须是对象")

    def validated_artifacts(label: str, expected_paths: list[str]) -> list[dict[str, str]]:
        items = artifacts.get(label)
        if not isinstance(items, list):
            raise AgentTaskError(f"视觉报告 artifacts.{label} 必须是数组")
        expected = {str(Path(path).resolve()) for path in expected_paths}
        submitted: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict):
                raise AgentTaskError(f"视觉报告 artifacts.{label} 项必须是对象")
            path = str(Path(str(item.get("path", ""))).resolve())
            declared_hash = str(item.get("sha256", "")).lower()
            if path in submitted:
                raise AgentTaskError(f"视觉报告 artifacts.{label} 路径重复: {path}")
            if not re.fullmatch(r"[0-9a-f]{64}", declared_hash):
                raise AgentTaskError(f"视觉报告 artifacts.{label} 缺少有效 sha256")
            current_hash = _sha256(Path(path)) if Path(path).is_file() else None
            if current_hash != declared_hash:
                raise AgentTaskError(f"视觉报告引用的产物哈希不匹配: {path}")
            submitted[path] = declared_hash
        if set(submitted) != expected:
            raise AgentTaskError(
                f"视觉报告 artifacts.{label} 必须完整引用当前全部产物"
            )
        return [
            {"path": path, "sha256": submitted[path]}
            for path in sorted(submitted)
        ]

    payload["artifacts"] = {
        "contact_sheets": validated_artifacts(
            "contact_sheets", expected_contact_sheets
        ),
        "pdfs": validated_artifacts("pdfs", expected_pdfs),
    }
    return payload


def record_visual_review(
    task_dir: str | os.PathLike[str],
    *,
    status: str,
    kind: str,
    reviewer: str,
    report_file: str | os.PathLike[str],
    note: str,
    _lock: bool = True,
) -> dict[str, Any]:
    task = Path(task_dir).resolve()
    if _lock:
        with TaskDirectoryLock(task):
            return record_visual_review(
                task,
                status=status,
                kind=kind,
                reviewer=reviewer,
                report_file=report_file,
                note=note,
                _lock=False,
            )
    state = _load_state(task)
    if state.get("status") not in {"awaiting_visual_review", "visual_review_failed"}:
        raise AgentTaskError(
            f"当前状态 {state.get('status')} 不允许提交视觉验收"
        )
    if status not in {"passed", "failed"}:
        raise AgentTaskError("视觉验收状态必须是 passed 或 failed")
    if kind not in {"vision_agent", "human_operator"}:
        raise AgentTaskError("review kind 必须是 vision_agent 或 human_operator")
    reviewer = reviewer.strip()
    note = note.strip()
    if not reviewer or not note:
        raise AgentTaskError("视觉验收必须填写 reviewer 和 note")

    result_dir = Path(state["result_dir"])
    from .output_validation import validate

    audit = _read_json(result_dir / "audit.json")
    request = _read_json(task / REQUEST_FILE)
    configured_kind = request.get("execution", {}).get("visual_review", {}).get(
        "mode"
    )
    if kind != configured_kind:
        raise AgentTaskError(
            f"review kind {kind} 与任务配置 {configured_kind} 不一致"
        )
    preliminary = _read_json(result_dir / "pdf_validation_report.json")
    if preliminary.get("automated_checks_passed") is not True:
        raise AgentTaskError("PDF 自动校验未通过，禁止提交视觉验收")
    contact_sheets = preliminary.get("visual_review", {}).get(
        "contact_sheets", []
    )
    external_report = _validated_external_review_report(
        report_file,
        kind=kind,
        status=status,
        expected_contact_sheets=contact_sheets,
        expected_pdfs=audit.get("rendered_pdfs", []),
    )
    report = validate(result_dir, task / RENDER_DIR, status, note)
    execution_mode = request.get("execution", {}).get("mode", "production")
    review = {
        "schema_version": "1.0",
        "review_kind": kind,
        "reviewed_at": _now(),
        "status": status,
        "reviewer": reviewer,
        "note": note,
        "external_report_file": str(Path(report_file).resolve()),
        "external_report_sha256": _sha256(Path(report_file).resolve()),
        "report": external_report,
        "contact_sheets": external_report["artifacts"]["contact_sheets"],
        "pdfs": external_report["artifacts"]["pdfs"],
        "request_hash": state.get("request_hash"),
    }
    _write_json(task / VISUAL_REVIEW_FILE, review)
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
        "visual_review": str(task / VISUAL_REVIEW_FILE),
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
            else ("release_blocked" if status == "passed" else "visual_review_failed")
        ),
        status_note=(
            "自动校验与配置的视觉验收均通过。"
            if completed
            else (
                "视觉验收通过，但生产放行门槛未满足。"
                if status == "passed"
                else "视觉验收未通过。"
            )
        ),
        next_action=(
            "读取 delivery_manifest.json 向用户交付结果。"
            if completed
            else "根据视觉问题修正后创建新任务，不得覆盖当前审计。"
        ),
    )
    return delivery


def record_human_visual_review(
    task_dir: str | os.PathLike[str],
    *,
    status: str,
    reviewer: str,
    report_file: str | os.PathLike[str],
    note: str,
    _lock: bool = True,
) -> dict[str, Any]:
    return record_visual_review(
        task_dir,
        status=status,
        kind="human_operator",
        reviewer=reviewer,
        report_file=report_file,
        note=note,
        _lock=_lock,
    )


def get_task_status(task_dir: str | os.PathLike[str]) -> dict[str, Any]:
    return _load_state(Path(task_dir).resolve())
