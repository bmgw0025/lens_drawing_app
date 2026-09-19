# Persistence And Judgment

## Immutable Task Evidence

A task locks:

- Source ZMX path and SHA-256
- Evaluated ZOS-API surfaces, DLL paths and OpticStudio version
- AGENT_PROTOCOL.md, request Schema and generated spec
- Lens Drawing runtime and renderer manifests
- deployment_policy.json and its SHA-256
- geometry_cases.json and, when required, geometry_resolution.json
- required_geometry_confirmations derived from the immutable candidates
- Every submitted request revision, validation report, PDF audit and visual report

task_state.json is the status authority. Chat history is not.

## Deterministic Facts

Code determines GLAS-after-surface intervals, units, R/T/material values, supported surface classes, zero-thickness/type/radius/coordinate eligibility, candidate provenance, unit conversion and physical constraints.

AD is a full diameter derived only from explicit aperture or SemiDiameter. MD is a full diameter derived only from MechanicalSemiDiameter. Both Zemax radii are multiplied by two and converted to millimeters. AD must never be used as MD.

## Agent Judgment

The Agent may select only IDs in geometry_cases.json. It may use surface sequence, GLAS intervals, left/right boundary roles and virtual-interface evidence to map candidates to physical lenses. A virtual interface always requires a selection even if only one topology candidate remains.

The Agent must ask instead of guessing when different numeric candidates remain equally feasible, when data is missing, or when geometry is low-confidence or unsupported. Selecting a candidate does not promote its original confidence. Every required_geometry_confirmations item needs a matching evidence-backed geometry_confirmation decision. The Agent cannot turn a rejected topology candidate into an eligible one.

## Supported Boundary

The renderer supports Sequential, one configuration, Standard spherical/plane groups of one to three physical lenses. Coincident zero-thickness virtual interfaces may be folded when all hard checks pass, while preserving side-specific AD values.

Non-sequential systems, nonzero tilt/decenter, unsupported aspheres/freeforms, unresolved multi-configuration geometry, real nonzero glue gaps and four or more physical elements remain blocked.

## Review And Resume

The configured visual reviewer is an independent release gate. Production normally uses vision_agent; human_operator is reserved for configured troubleshooting. Reports must bind all current PDFs and contact sheets by hash. The main Agent has no review tool.

On resume, run spec and status, then follow next_action. Never overwrite a result, request history, geometry decision or review. visual_review_failed and release_blocked preserve all evidence; corrected work starts as a new task.
