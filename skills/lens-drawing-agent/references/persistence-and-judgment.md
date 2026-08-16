# Persistence and Agent Judgment

## Contents

- Authoritative artifacts
- Persistent versus judgment-based data
- Virtual cemented interfaces
- Resume and anti-drift rules
- Failure handling

## Authoritative Artifacts

`task_state.json` is the only authority for current status and next action. `AGENT_HANDOFF.md` is a readable derivative. Chat history is never authoritative.

Task creation locks hashes for:

- Task-local `AGENT_PROTOCOL.md`
- Task-local request schema
- Task-local generated Agent spec
- Lens Drawing runtime identity and renderer manifest
- All four `source_analysis` files
- Source ZMX path and SHA-256

Any mismatch blocks execution. Recreate the task with the current application instead of modifying snapshots.

## Persistent Versus Judgment-Based Data

Persisted, deterministic data:

- Evaluated ZOS-API surface records and source hash
- Glass intervals, cemented grouping, virtual-interface evidence
- Authoritative per-lens Glass/T/R/MD/AD_left/AD_right values and provenance; legacy rows are compatibility views only
- Blockers, confidence, warnings, and exact geometry-review values
- Renderer defaults and supported process-field catalog
- Submitted request versions, validation results, audit, PDF validation, human visual review, and delivery manifest

Agent judgment required each task:

- Translate user language into naming and whitelisted manufacturing fields
- Decide whether evidence is sufficient or conflicting
- Ask concise unresolved questions
- Explain geometry blockers and medium-confidence evidence
- Prepare concise evidence and status for the human reviewer without deciding the review result

Agent judgment must never mutate deterministic geometry or invent missing manufacturing requirements.

Human operator judgment is a separate release gate. Only an authorized operator may inspect every rendered page and submit `review`; neither the Agent nor a vision-model advisory may record `passed`.

Confidence gate: high-confidence geometry may proceed while every warning is disclosed at delivery; medium-confidence fields require exact acknowledgement before `run`; blocked or clearly low-confidence geometry stops the task.

The Agent baseline is versioned application data. GUI users may persist personal settings, but those settings are outside the Agent task contract and must never become the next task's defaults. Deliver `manufacturing_requirements_summary.md` and its JSON companion with every completed task.

## Virtual Cemented Interfaces

Zemax `GLAS` applies to the medium after a surface. Therefore glass names do not need to appear on adjacent LDE rows for the physical elements to be cemented.

Treat separated glass intervals as one cemented connection only when all intervening media are non-glass, every gap thickness is zero, duplicated interface surface types and radii match, and neither surface has tilt/decenter. Record the connection as `virtual_cemented_interface` and collapse the duplicate surfaces into one logical R boundary.

Preserve the previous lens's `AD_right` and the next lens's `AD_left` independently. Different values are valid when both come from their respective physical ZOS-API surfaces; set the legacy shared `row.ADn` to null and use `drawing_drafts[].lenses[]` for rendering and validation.

Keep each physical side's MEMA evidence for the adjacent lens MD. A virtual interface can prove a triplet topology while a conflicting or non-fixed MEMA still produces a medium-confidence MD requiring exact acknowledgement. Topology approval is not permission to edit the MD.

If thickness is nonzero, split the groups. If type, radius, or coordinate checks differ at zero thickness, classify as an ambiguous compound and block.

Classify a single H-K9L element with two plane boundaries as an excluded prism. Preserve material, thickness, surface numbers, and radii; skip both PDFs and disclose it at delivery. Do not consume naming or production-code sequence numbers.

## Resume and Anti-Drift Rules

On every resume:

1. Run `spec`; let the wrapper compare Skill and EXE hashes.
2. Run `status <task-dir>`.
3. Read the task-local protocol and state-directed artifacts.
4. Verify unresolved questions and the latest submitted request revision.
5. Continue only with the command allowed by the current state.

Never replace a request after running has started. Never overwrite a nonempty result directory. Never copy results into another task as if they were newly validated.

## Failure Handling

- `blocked_geometry`: explain blockers; a new implementation or corrected ZMX is required.
- `needs_clarification`: obtain missing evidence and submit a new request revision.
- `validation_failed`: inspect the validation report; do not visually pass.
- `execution_failed`: preserve partial artifacts and create a new task after correction.
- `human_review_failed`: preserve the human review and create a new task after correction.
- `release_blocked`: visual checks passed but production authorization is incomplete; do not deliver as released output.

Only `completed` permits delivery from `delivery_manifest.json`.
