# Agent Workflow

## Contents

- Discover and create
- Analyze user requirements
- Build and submit the request
- Validate, run, and review
- Production and test evidence

## Discover and Create

Run `spec` at the start of every new task or resumed task after an application update. The wrapper rejects a Skill/EXE spec mismatch.

Run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/Invoke-LensDrawingAgent.ps1 create "C:\path\input.zmx" "C:\path\new-task"
```

The task directory must not exist or must be empty. `create` opens the ZMX through a serialized, read-only ZOS-API session, stores evaluated values, closes without saving, maps geometry deterministically, and snapshots the protocol/schema/spec/runtime identity.

If `task_state.status` is `blocked_geometry`, report the exact blockers from `source_analysis/drawing_drafts.json`. Do not continue to manufacturing approval or try to repair geometry through the request.

## Analyze User Requirements

Read these files in order:

1. `task_state.json`
2. `AGENT_PROTOCOL.md`
3. `AGENT_HANDOFF.md`
4. `source_analysis/analysis_summary.json`
5. `source_analysis/drawing_drafts.json`
6. `source_analysis/agent_work_order.json`
7. `agent_request.json`

Resolve only these decision classes:

- `naming`: normally collect `lens_model` for SavePdfFolder, `lens_element_model` for MfrPdfFolder and sequential PartName, and `first_production_code` for sequential PartNo. Excluded prism groups do not consume a sequence number.
- `manufacturing_complete`: explicit overrides plus approval of all effective defaults.
- `geometry_review`: exact acknowledgement of only the medium-confidence fields already listed by the task.

When the user says there are no special manufacturing requirements, use the immutable Agent baseline for every unmentioned field and retain an explicit approval statement. Never use persisted GUI settings. Silence is not approval.

Never infer a manufacturing tolerance, CA, chamfer, coating, ink, roughness, chipping rule, vendor, glass rank, molding method, signature, or special note without user evidence. Natural-language specialization is allowed only by mapping evidence to fields present in the current generated spec.

## Build and Submit the Request

Preserve the generated `task_id`, source path, source SHA-256, and geometry-review field values. Build a candidate JSON outside the task directory.

For production evidence, use:

```json
{
  "id": "user-001",
  "kind": "user_message",
  "content": "Use the automatic audit name and all current default manufacturing requirements.",
  "captured_at": "2026-08-14T12:00:00+08:00",
  "source_ref": "current-user-message"
}
```

For an attachment, add `source_ref` as the current readable local path and `sha256` as the current file hash. Every evidence item needs an `evidence_disposition` entry with `mapped` or `no_action`, targets, and an explanation.

Every actual override needs `field_evidence` pointing to one or more evidence IDs at the same scope:

- `global_overrides.<field>`
- `group_overrides.<group>.<field>`
- `page_overrides.<group>.<page>.<field>`

Submit and validate:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/Invoke-LensDrawingAgent.ps1 submit "C:\path\task" "C:\path\candidate.json"
powershell -ExecutionPolicy Bypass -File scripts/Invoke-LensDrawingAgent.ps1 validate "C:\path\task"
```

Do not directly edit the submitted `agent_request.json`. Submit a new version while the task state still allows it.

## Validate, Run, and Human Review

Run only when validation reports `valid: true` and task status is `ready`:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/Invoke-LensDrawingAgent.ps1 run "C:\path\task"
```

Read `result/pdf_validation_report.json`. Automated checks validate page counts, expected fields, text, rendered pixels, crop safety, and output integrity. They do not replace human visual review.

When `task_state.status` becomes `awaiting_human_review`, the Agent must stop. An authorized human operator inspects every `validation_render/contact_sheet_*.png` and, when needed, individual PDF pages. The operator confirms geometry, title blocks, dimensions, tolerances, CA/chamfers, notes, coating/spraying tables, page order, clipping, overlap, and readable text.

The operator records the result:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/Invoke-LensDrawingAgent.ps1 review "C:\path\task" --status passed --reviewer "operator-id" --note "Manually reviewed every page; no overlap, clipping, or field mismatch."
```

If review fails, record `failed`; do not overwrite the task. Create a new task after correcting the implementation or requirements.

## Production and Test Evidence

`production` accepts only real `user_message` and hash-verified `attachment` evidence. It completes only when manufacturing approval, any geometry acknowledgement, automated PDF checks, human visual review, and the production release gate all pass.

`test` may use `operator_record` to exercise the interface. A completed test task proves the software loop only and must not be represented as user-authorized manufacturing output.

At delivery, include both PDF variants for every drawable group, `manufacturing_requirements_summary.md`, its JSON companion, all geometry warnings, and every excluded-prism record from `delivery_manifest.json`.
