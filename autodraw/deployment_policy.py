from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app_version import AGENT_INTERFACE_VERSION
from settings import get_agent_default_settings

from .process_requirements import PROCESS_FIELD_SPECS, normalize_override_values
from .runtime import canonical_hash, sha256_file


class DeploymentPolicyError(ValueError):
    pass


def current_manufacturing_defaults() -> dict[str, Any]:
    defaults = get_agent_default_settings()
    selected = {
        key: defaults[key]
        for key in sorted(PROCESS_FIELD_SPECS)
        if key in defaults
    }
    return normalize_override_values(selected, "current_manufacturing_defaults")


def load_deployment_policy(path: str | Path) -> dict[str, Any]:
    policy_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(policy_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DeploymentPolicyError(f"部署策略文件不存在: {policy_path}") from exc
    except json.JSONDecodeError as exc:
        raise DeploymentPolicyError(f"部署策略 JSON 无效: {policy_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DeploymentPolicyError("部署策略必须是 JSON 对象")
    if payload.get("schema_version") != "1.0":
        raise DeploymentPolicyError("deployment_policy.schema_version 必须是 1.0")
    if payload.get("approval_status") != "approved":
        raise DeploymentPolicyError("部署策略尚未批准，approval_status 必须是 approved")
    if payload.get("agent_interface_version") != AGENT_INTERFACE_VERSION:
        raise DeploymentPolicyError(
            "部署策略 agent_interface_version 与当前 Lens Drawing 不一致"
        )
    for key in ("policy_id", "approved_by", "approved_at", "approval_scope"):
        if not str(payload.get(key, "")).strip():
            raise DeploymentPolicyError(f"deployment_policy.{key} 不能为空")

    defaults = payload.get("manufacturing_defaults")
    if not isinstance(defaults, dict):
        raise DeploymentPolicyError("deployment_policy.manufacturing_defaults 必须是对象")
    expected_keys = set(current_manufacturing_defaults())
    if set(defaults) != expected_keys:
        missing = sorted(expected_keys - set(defaults))
        extra = sorted(set(defaults) - expected_keys)
        details = []
        if missing:
            details.append("缺少: " + ", ".join(missing))
        if extra:
            details.append("多余: " + ", ".join(extra))
        raise DeploymentPolicyError("部署默认值字段集合不完整；" + "；".join(details))
    normalized_defaults = normalize_override_values(
        defaults, "deployment_policy.manufacturing_defaults"
    )
    defaults_hash = canonical_hash(normalized_defaults)
    declared_hash = str(payload.get("manufacturing_defaults_sha256", "")).lower()
    if declared_hash != defaults_hash:
        raise DeploymentPolicyError(
            "deployment_policy.manufacturing_defaults_sha256 与规范化默认值不一致"
        )

    review = payload.get("visual_review")
    if not isinstance(review, dict) or review.get("required") is not True:
        raise DeploymentPolicyError("deployment_policy.visual_review.required 必须是 true")
    if review.get("mode") not in {"vision_agent", "human_operator"}:
        raise DeploymentPolicyError(
            "deployment_policy.visual_review.mode 必须是 vision_agent 或 human_operator"
        )

    zosapi = payload.get("zosapi")
    if not isinstance(zosapi, dict):
        raise DeploymentPolicyError("deployment_policy.zosapi 必须是对象")
    for key in ("zemax_root", "opticstudio_install_dir"):
        if not str(zosapi.get(key, "")).strip():
            raise DeploymentPolicyError(f"deployment_policy.zosapi.{key} 不能为空")

    normalized = dict(payload)
    normalized["manufacturing_defaults"] = normalized_defaults
    normalized["manufacturing_defaults_sha256"] = defaults_hash
    normalized["policy_file"] = str(policy_path)
    normalized["policy_sha256"] = sha256_file(policy_path)
    return normalized
