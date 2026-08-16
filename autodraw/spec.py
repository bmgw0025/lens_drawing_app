from __future__ import annotations

import json
from typing import Any

from app_version import (
    AGENT_INTERFACE_VERSION,
    APP_DISPLAY_NAME,
    APP_NAME,
    APP_VERSION,
    APP_VERSION_FULL,
    REQUEST_SCHEMA_VERSION,
    TASK_SCHEMA_VERSION,
)
from settings import get_agent_default_settings

from .process_requirements import PROCESS_FIELD_SPECS
from .runtime import canonical_hash


def build_agent_spec() -> dict[str, Any]:
    agent_defaults = get_agent_default_settings()
    process_defaults = {
        key: agent_defaults[key]
        for key in sorted(PROCESS_FIELD_SPECS)
        if key in agent_defaults
    }
    return {
        "spec_schema_version": "1.0",
        "application": {
            "name": APP_NAME,
            "display_name": APP_DISPLAY_NAME,
            "version": APP_VERSION,
            "version_full": APP_VERSION_FULL,
        },
        "agent_interface": {
            "version": AGENT_INTERFACE_VERSION,
            "request_schema_version": REQUEST_SCHEMA_VERSION,
            "task_schema_version": TASK_SCHEMA_VERSION,
            "source_entrypoint": "python agent_cli.py <command>",
            "installed_entrypoint": (
                "LensDrawing.exe --agent --output-json <path> <command>"
            ),
            "commands": [
                "capabilities",
                "spec",
                "create",
                "submit",
                "validate",
                "run",
                "status",
                "review",
            ],
            "output_contract": {
                "format": "UTF-8 JSON envelope",
                "required_flag_for_windowed_install": "--output-json <path>",
                "top_level_fields": [
                    "ok",
                    "command",
                    "exit_code",
                    "interface_version",
                    "result or error",
                ],
                "exit_codes": {
                    "0": "command completed and its requested gate passed",
                    "1": "argument, resource or execution error",
                    "2": "command completed but geometry, validation, review or release gate is not passed",
                },
            },
        },
        "manual_application": {
            "preserved": True,
            "entrypoint": "LensDrawing.exe",
            "modules": ["draw", "batch", "settings"],
        },
        "geometry_policy": {
            "source": "read-only native ZOS-API extraction from one ZMX",
            "authoritative_input": ".zmx",
            "screenshot_policy": (
                "screenshots may support identification or user evidence but cannot enter the "
                "accurate automatic geometry path; require ZMX or a separately confirmed manual route"
            ),
            "agent_mutable": False,
            "fields": ["Glass", "T", "R", "MD", "AD_left", "AD_right"],
            "semi_diameter_rule": "AD and MD are full diameters; never map AD to MD",
            "virtual_interface_rule": (
                "zero-thickness non-glass gaps are cemented only when duplicated "
                "surface type/radius match and no tilt/decenter exists"
            ),
            "virtual_interface_effect": (
                "collapse the duplicated LDE surfaces into one logical R boundary, "
                "preserve side-specific AD and MEMA evidence for the adjacent lenses, "
                "and do not split the cemented lens group"
            ),
            "geometry_contract": (
                "lenses[] is authoritative for per-lens Glass/T/R/MD/AD_left/AD_right; "
                "row is a legacy compatibility view and may contain null at a virtual "
                "interface whose two AD values differ"
            ),
            "topology_and_dimension_review_are_separate": True,
            "prism_exclusion_rule": (
                "exclude a single H-K9L element with two plane surfaces from drawing, "
                "retain deterministic evidence, and disclose the exclusion at delivery"
            ),
            "medium_confidence_requires_exact_acknowledgement": True,
        },
        "supported_geometry": {
            "mode": "Sequential",
            "configuration_count": 1,
            "surface_types": ["Standard spherical", "plane"],
            "lens_count_per_group": [1, 2, 3],
        },
        "naming": {
            "modes": [
                "production_sequence",
                "generated",
                "base_name",
                "per_group",
            ],
            "production_sequence": {
                "lens_model": "SavePdfFolder for every drawable group",
                "lens_element_model": (
                    "MfrPdfFolder and PartName prefix; PartName uses -1, -2, ... "
                    "in drawable surface order"
                ),
                "first_production_code": (
                    "PartNo for the first drawable group; trailing digits increment "
                    "with preserved width"
                ),
                "excluded_groups_consume_sequence_numbers": False,
            },
            "renderer_text": "ASCII letters, digits, dot, underscore and hyphen",
        },
        "evidence": {
            "production_kinds": ["user_message", "attachment"],
            "test_only_kinds": ["operator_record"],
            "attachments_require_sha256": True,
            "every_evidence_item_requires_disposition": True,
            "every_override_requires_field_evidence": True,
        },
        "process_field_catalog": {
            key: PROCESS_FIELD_SPECS[key] for key in sorted(PROCESS_FIELD_SPECS)
        },
        "process_defaults": process_defaults,
        "process_default_policy": {
            "source": "immutable Agent baseline bundled with this app version",
            "reads_persisted_gui_settings": False,
            "unspecified_requirements_use_baseline": True,
        },
        "override_precedence": [
            "renderer_defaults",
            "global_overrides",
            "group_overrides",
            "page_overrides",
        ],
        "task_state": {
            "normal_flow": [
                "needs_input",
                "submitted",
                "ready",
                "running",
                "awaiting_human_review",
                "completed",
            ],
            "failure_or_block_states": [
                "blocked_geometry",
                "needs_clarification",
                "validation_failed",
                "execution_failed",
                "human_review_failed",
                "release_blocked",
            ],
        },
        "persistent_artifacts": [
            "task_state.json",
            "AGENT_PROTOCOL.md",
            "agent_request.schema.json",
            "lens_drawing_agent_spec.json",
            "AGENT_HANDOFF.md",
            "source_analysis/",
            "agent_request.json",
            "request_versions/",
            "request_validation.json",
            "result/",
            "result/manufacturing_requirements_delivery.json",
            "result/manufacturing_requirements_summary.md",
            "human_visual_review.json",
            "delivery_manifest.json",
        ],
        "agent_judgment_required": [
            "translate user language into evidence-backed naming and process fields",
            "ask only unresolved naming, complete manufacturing and exact geometry review questions",
            "explain blocked or ambiguous geometry without overriding it",
            "disclose high-confidence warnings and excluded prism groups at delivery",
            "prepare the task for an authorized human visual review without recording the decision",
        ],
        "human_operator_judgment_required": [
            "visually review every rendered PDF page",
            "record passed or failed through the review command",
        ],
        "installed_dependencies": {
            "bundled": [
                "Lens Drawing renderer",
                "Agent CLI",
                "PDF validation runtime",
                "Lens Drawing Agent Skill",
            ],
            "external": [
                "Windows x64",
                "Ansys Zemax OpticStudio 2022 R2.01 with valid license for ZMX import",
            ],
        },
        "renderer_contract": {
            "single_implementation": True,
            "alternate_renderer_root_allowed": False,
            "shared_by": ["manual GUI", "Agent CLI"],
            "pdf_variants_per_drawable_group": {
                "save": "SavePdfFolder/PartName.pdf with PartName visible",
                "mfr": "MfrPdfFolder/PartNo.pdf with PartName hidden",
            },
        },
    }


def spec_sha256(spec: dict[str, Any] | None = None) -> str:
    return canonical_hash(spec or build_agent_spec())


def spec_json(spec: dict[str, Any] | None = None) -> str:
    return json.dumps(
        spec or build_agent_spec(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
