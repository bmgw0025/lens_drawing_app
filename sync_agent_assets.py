"""Generate and verify the V4 Agent spec shared by the app and bundled Skill."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app_version import AGENT_INTERFACE_VERSION, APP_VERSION, APP_VERSION_FULL
from autodraw.runtime import (
    CORE_SOURCE_FILES,
    RENDERER_SOURCE_FILES,
    SOURCE_ROOT,
    source_file_manifest,
)
from autodraw.spec import build_agent_spec, spec_json, spec_sha256


SPEC_TARGETS = (
    SOURCE_ROOT / "agent_resources" / "lens_drawing_agent_spec.json",
    SOURCE_ROOT / "skills" / "lens-drawing-agent" / "references"
    / "lens_drawing_agent_spec.json",
)
BUILD_MANIFEST = SOURCE_ROOT / "agent_resources" / "build_manifest.json"


def _json_text(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def expected_build_manifest(spec: dict) -> dict:
    return {
        "manifest_schema_version": "1.0",
        "app_version": APP_VERSION,
        "app_version_full": APP_VERSION_FULL,
        "agent_interface_version": AGENT_INTERFACE_VERSION,
        "agent_spec_sha256": spec_sha256(spec),
        "source_manifest": source_file_manifest(SOURCE_ROOT, CORE_SOURCE_FILES),
        "renderer_manifest": source_file_manifest(
            SOURCE_ROOT, RENDERER_SOURCE_FILES
        ),
    }


def write_assets() -> None:
    spec = build_agent_spec()
    text = spec_json(spec)
    for target in SPEC_TARGETS:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    BUILD_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    BUILD_MANIFEST.write_text(
        _json_text(expected_build_manifest(spec)), encoding="utf-8"
    )


def check_assets() -> list[str]:
    errors: list[str] = []
    spec = build_agent_spec()
    expected_spec = spec_json(spec)
    for target in SPEC_TARGETS:
        if not target.is_file():
            errors.append(f"缺少 Agent spec: {target}")
        elif target.read_text(encoding="utf-8") != expected_spec:
            errors.append(f"Agent spec 未同步: {target}")
    expected_manifest = _json_text(expected_build_manifest(spec))
    if not BUILD_MANIFEST.is_file():
        errors.append(f"缺少构建 manifest: {BUILD_MANIFEST}")
    elif BUILD_MANIFEST.read_text(encoding="utf-8") != expected_manifest:
        errors.append(f"构建 manifest 未同步: {BUILD_MANIFEST}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="写入最新 spec 和 manifest")
    parser.add_argument("--check", action="store_true", help="校验文件是否同步")
    args = parser.parse_args(argv)
    if not args.write and not args.check:
        args.check = True
    if args.write:
        write_assets()
    if args.check:
        errors = check_assets()
        if errors:
            print("\n".join(f"[ERROR] {item}" for item in errors))
            return 2
        print("Agent spec, Skill reference and build manifest are synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
