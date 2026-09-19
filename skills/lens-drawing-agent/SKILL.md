---
name: lens-drawing-agent
description: Control an installed Lens Drawing 4.1 application to analyze one Zemax ZMX, select only frozen topology and AD/MD candidate IDs for virtual cemented interfaces, apply deployment-approved manufacturing defaults, generate and validate PDF drawings, and resume audited tasks.
---

# Lens Drawing Agent

Use the bundled PowerShell wrapper for every installed call. It verifies that this Skill and the executable expose the same generated spec.

```powershell
$invoke = "scripts/Invoke-LensDrawingAgent.ps1"
powershell -ExecutionPolicy Bypass -File $invoke spec
```

Read [workflow.md](references/workflow.md) for the command sequence and [persistence-and-judgment.md](references/persistence-and-judgment.md) for geometry limits.

## Required Workflow

1. Run spec, then create with the approved deployment policy.
2. Read task_state.json before all other artifacts.
3. If status is awaiting_geometry_resolution, read source_analysis/geometry_cases.json and submit candidate IDs only through resolve-geometry.
4. Never place Glass, T, R, AD or MD values in the geometry decision.
5. After resolve-geometry, read task_state.required_geometry_confirmations. Ask each prompt through the host and wait; candidate selection alone never confirms low-confidence geometry.
6. For every confirmation_id, add one geometry_confirmation decision backed by user_message or attachment evidence and map that evidence to geometry_confirmation.<confirmation_id>.
7. Ask through the host only for missing naming, real numeric conflicts, low-confidence or unsupported geometry.
8. Build the requirement request outside the task directory. Deployment defaults need no per-task approval; every user override still needs evidence.
9. Run submit, validate and run only in the allowed state.
10. Stop at awaiting_visual_review. The host, not this main Agent, invokes the configured reviewer and review command.
11. Deliver only when task_state.status is completed.

## Invocation

```powershell
powershell -ExecutionPolicy Bypass -File $invoke create "C:\work\lens.zmx" "C:\work\task" --deployment-policy "C:\ProgramData\LensBot\deployment_policy.json"
powershell -ExecutionPolicy Bypass -File $invoke resolve-geometry "C:\work\task" "C:\work\geometry-decision.json"
powershell -ExecutionPolicy Bypass -File $invoke submit "C:\work\task" "C:\work\request.json"
powershell -ExecutionPolicy Bypass -File $invoke validate "C:\work\task"
powershell -ExecutionPolicy Bypass -File $invoke run "C:\work\task"
powershell -ExecutionPolicy Bypass -File $invoke status "C:\work\task"
```

Do not reuse a nonempty task/result directory, alter frozen snapshots, bypass a hard blocker, call a different renderer, or submit review from the main Agent.
