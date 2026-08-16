from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from app_version import (
    AGENT_INTERFACE_VERSION,
    APP_NAME,
    APP_VERSION,
    APP_VERSION_FULL,
)


SOURCE_ROOT = Path(__file__).resolve().parents[1]
AGENT_RESOURCE_DIR = "agent_resources"
SKILL_DIR = Path("skills") / "lens-drawing-agent"

CORE_SOURCE_FILES = (
    "app_version.py",
    "webview_main.py",
    "web_app.py",
    "agent_cli.py",
    "main.py",
    "settings.py",
    "config.py",
    "geometry.py",
    "batch_import.py",
    "autodraw/__init__.py",
    "autodraw/runtime.py",
    "autodraw/spec.py",
    "autodraw/models.py",
    "autodraw/mapper.py",
    "autodraw/naming.py",
    "autodraw/process_requirements.py",
    "autodraw/renderer_adapter.py",
    "autodraw/zosapi_provider.py",
    "autodraw/pipeline.py",
    "autodraw/output_validation.py",
    "autodraw/agent_tasks.py",
)

RENDERER_SOURCE_FILES = (
    "batch_import.py",
    "main.py",
    "settings.py",
    "config.py",
    "geometry.py",
)


class RuntimeResourceError(RuntimeError):
    pass


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_root() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


def bundle_root() -> Path:
    value = getattr(sys, "_MEIPASS", None)
    return Path(value).resolve() if value else SOURCE_ROOT


def _first_existing(candidates: list[Path], label: str) -> Path:
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    raise RuntimeResourceError(
        f"找不到 {label}: " + "; ".join(str(item) for item in candidates)
    )


def agent_resources_root() -> Path:
    return _first_existing(
        [
            install_root() / AGENT_RESOURCE_DIR,
            bundle_root() / AGENT_RESOURCE_DIR,
            SOURCE_ROOT / AGENT_RESOURCE_DIR,
        ],
        "Agent 资源目录",
    )


def agent_resource(name: str) -> Path:
    root = agent_resources_root()
    path = root / name
    if not path.is_file():
        raise RuntimeResourceError(f"Agent 资源缺失: {path}")
    return path.resolve()


def bundled_skill_root() -> Path:
    return _first_existing(
        [
            install_root() / SKILL_DIR,
            bundle_root() / SKILL_DIR,
            SOURCE_ROOT / SKILL_DIR,
        ],
        "Lens Drawing Agent Skill",
    )


def source_file_manifest(
    root: Path = SOURCE_ROOT,
    relative_paths: tuple[str, ...] = CORE_SOURCE_FILES,
) -> dict[str, str]:
    manifest: dict[str, str] = {}
    for relative in relative_paths:
        path = root / Path(relative)
        if not path.is_file():
            raise RuntimeResourceError(f"运行时源码 manifest 缺少文件: {path}")
        manifest[relative.replace("\\", "/")] = sha256_file(path)
    return manifest


def _read_build_manifest() -> dict[str, Any]:
    path = agent_resource("build_manifest.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeResourceError(f"构建 manifest 无效: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeResourceError(f"构建 manifest 必须是对象: {path}")
    return payload


def core_source_manifest() -> dict[str, str]:
    if not is_frozen():
        return source_file_manifest()
    payload = _read_build_manifest().get("source_manifest")
    if not isinstance(payload, dict) or not payload:
        raise RuntimeResourceError("安装版 build_manifest 缺少 source_manifest")
    return {str(key): str(value) for key, value in payload.items()}


def renderer_source_manifest() -> dict[str, str]:
    if not is_frozen():
        return source_file_manifest(SOURCE_ROOT, RENDERER_SOURCE_FILES)
    payload = _read_build_manifest().get("renderer_manifest")
    if not isinstance(payload, dict) or not payload:
        raise RuntimeResourceError("安装版 build_manifest 缺少 renderer_manifest")
    return {str(key): str(value) for key, value in payload.items()}


def runtime_identity() -> dict[str, Any]:
    spec_path = agent_resource("lens_drawing_agent_spec.json")
    try:
        spec_payload = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeResourceError(f"Agent spec 无效: {spec_path}") from exc
    core_manifest = core_source_manifest()
    renderer_manifest = renderer_source_manifest()
    identity: dict[str, Any] = {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "app_version_full": APP_VERSION_FULL,
        "agent_interface_version": AGENT_INTERFACE_VERSION,
        "runtime_mode": "installed" if is_frozen() else "source",
        "install_root": str(install_root()),
        "agent_spec_sha256": canonical_hash(spec_payload),
        "agent_spec_file_sha256": sha256_file(spec_path),
        "core_manifest_sha256": canonical_hash(core_manifest),
        "renderer_manifest_sha256": canonical_hash(renderer_manifest),
    }
    if is_frozen():
        identity["executable"] = str(Path(sys.executable).resolve())
        identity["executable_sha256"] = sha256_file(sys.executable)
    return identity
