---
name: lens-drawing-agent
description: Control an installed Lens Drawing V4 application to analyze one Zemax .zmx file, infer singlet/doublet/triplet geometry including zero-thickness virtual cemented interfaces, collect evidence-backed naming and manufacturing requirements, generate and validate PDF drawings, and resume audited tasks across conversations. Use for automatic ZMX-to-drawing work, Lens Drawing Agent task continuation/status, special manufacturing edits mediated by AI, or installation and use of the bundled Lens Drawing Agent interface.
---

# Lens Drawing Agent

Use the bundled PowerShell wrapper for every installed-app call. It locates the app, verifies that this Skill and the executable expose the same generated spec, invokes the windowed EXE through `--output-json`, and returns the JSON envelope.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/Invoke-LensDrawingAgent.ps1 spec
```

Treat `references/lens_drawing_agent_spec.json` as the field and command authority. Read [workflow.md](references/workflow.md) before creating or editing a task request. Read [persistence-and-judgment.md](references/persistence-and-judgment.md) when resuming a task, interpreting virtual surfaces, handling failures, or deciding what the Agent may infer.

## Required Workflow

1. Run `spec`, then `create <zmx> <new-empty-task-dir>`.
2. Read `task_state.json` first. Never reconstruct task status from chat history.
3. Read the task-local protocol, analysis summary, drawing drafts, work order, and unresolved questions.
4. Never edit or override Glass/T/R/MD/AD, units, surface mapping, topology, or provenance.
5. Ask only for unresolved lens model, lens-element model, first production code/increment rule, complete manufacturing approval, conflicting evidence, and exact acknowledgement of listed medium-confidence geometry values.
6. Record real user messages or hashed attachments as evidence. Use `operator_record` only in test mode.
7. Build the candidate request outside the task directory, then run `submit`, `validate`, and only when ready, `run`.
8. After `run`, stop when the status is `awaiting_human_review`. An authorized human operator must inspect every generated contact sheet/PDF page and record `review`; the Agent must not submit the review decision.
9. Deliver only when `task_state.status` is `completed`. Use `delivery_manifest.json` for both PDF variants, geometry warnings, prism exclusions, the human review record, and the manufacturing-requirements summary.

## Geometry Boundary

Interpret Zemax `GLAS` as the medium after a surface. A zero-thickness non-glass separator does not break a cemented group when the duplicated surfaces have matching type and radius and no tilt/decenter. Collapse those surfaces into one logical R boundary while retaining each adjacent lens side's independent AD and MEMA evidence. `drawing_drafts[].lenses[]` is authoritative; the legacy `row` may contain a null shared AD when the two physical sides differ.

This structural conclusion is separate from mechanical-size confidence. For example, a system may be definitively a cemented triplet while an inferred `MD2` remains medium-confidence and requires exact user acknowledgement. Never change the value during acknowledgement.

Exclude a single `H-K9L` element with two plane surfaces as a prism. Do not assign it a sequence number or PDF; retain and disclose the exclusion evidence. Treat screenshots only as supporting evidence. Accurate automatic geometry requires a ZMX.

## Invocation

Pass command arguments after the command. Use `-OutputJson` when a stable result path is required.

```powershell
$invoke = "scripts/Invoke-LensDrawingAgent.ps1"
powershell -ExecutionPolicy Bypass -File $invoke create "C:\work\lens.zmx" "C:\work\lens-task"
powershell -ExecutionPolicy Bypass -File $invoke status "C:\work\lens-task"
powershell -ExecutionPolicy Bypass -File $invoke submit "C:\work\lens-task" "C:\work\candidate.json"
powershell -ExecutionPolicy Bypass -File $invoke validate "C:\work\lens-task"
powershell -ExecutionPolicy Bypass -File $invoke run "C:\work\lens-task"
# Operator-only after manual page inspection:
powershell -ExecutionPolicy Bypass -File $invoke review "C:\work\lens-task" --status passed --reviewer "operator-id" --note "All pages manually inspected."
```

Do not reuse a nonempty output/task directory, alter frozen task snapshots, bypass a blocked geometry group, or call a different renderer implementation.
