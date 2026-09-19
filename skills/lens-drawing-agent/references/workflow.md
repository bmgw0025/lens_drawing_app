# Agent Workflow

## Create

Run spec, then:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/Invoke-LensDrawingAgent.ps1 create "C:\path\input.zmx" "C:\path\new-task" --deployment-policy "C:\ProgramData\LensBot\deployment_policy.json"
```

create snapshots the protocol, Schema, generated spec, deployment policy, runtime identity, renderer manifest, ZOS-API extraction and geometry candidate set.

Read in order:

1. task_state.json
2. AGENT_PROTOCOL.md
3. source_analysis/analysis_summary.json
4. source_analysis/geometry_cases.json
5. source_analysis/drawing_drafts.json
6. source_analysis/agent_work_order.json
7. agent_request.json

blocked_geometry means at least one hard_blocker cannot be removed by model selection. Explain the exact surface evidence and ask for a corrected/standardized ZMX when appropriate.

## Resolve Geometry

When status is awaiting_geometry_resolution, select every required topology and field candidate. The decision must not contain numeric geometry.

```json
{
  "schema_version": "1.0",
  "task_id": "LD-001",
  "cases": [
    {
      "case_id": "group-1",
      "topology_candidate_id": "topology-g1-fold-3-4",
      "field_selections": {
        "Lens1.AD_right": "ad-g1-l1-right-s3",
        "Lens2.AD_left": "ad-g1-l2-left-s4",
        "MD1": "md-g1-l1-c1",
        "MD2": "md-g1-l2-c1"
      },
      "reason": "GLAS intervals and the two coincident virtual surfaces map to the adjacent physical lens sides."
    }
  ]
}
```

If two different values remain equally feasible after surface order and boundary association are considered, do not guess. Ask the internal technician and wait.

After resolve-geometry, read task_state.required_geometry_confirmations. A medium-confidence candidate such as an Automatic MEMA remains unapproved even when it is the only candidate. Send every stored prompt through the host and wait for a user_message or attachment response.

## Build Request

Naming normally uses production_sequence. Deployment policy supplies every unspecified manufacturing default. Map only explicit user special requirements to global_overrides, group_overrides or page_overrides, with matching field_evidence.

The manufacturing_requirements policy_id, policy_sha256, approved_by and approved_at must remain identical to the generated task request. Do not add geometry_review; geometry was frozen by resolve-geometry.

For every required confirmation, add exactly one decision:

~~~json
{
  "category": "geometry_confirmation",
  "confirmation_id": "group-1:MD2:md-g1-l2-c1",
  "statement": "The internal technician confirmed this evaluated MEMA as MD2.",
  "evidence_ids": ["user-confirm-md2"]
}
~~~

Map the same evidence to geometry_confirmation.<confirmation_id> in evidence_disposition. Remove the matching unresolved question only after that evidence exists.

Submit and validate:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/Invoke-LensDrawingAgent.ps1 submit "C:\path\task" "C:\path\candidate.json"
powershell -ExecutionPolicy Bypass -File scripts/Invoke-LensDrawingAgent.ps1 validate "C:\path\task"
```

## Run And Delivery

Run only when ready. Lens Drawing reopens the same ZMX read-only, regenerates the candidate set, compares it with the task snapshot, reapplies the frozen selection and then renders.

After automated PDF validation, the task enters awaiting_visual_review. The host gives every contact sheet, PDF hash and fixed prompt to the configured reviewer. The host validates the structured response and invokes review. The main Agent never calls review.

Only completed permits delivery from delivery_manifest.json. Include both PDF variants, manufacturing summaries, geometry warnings, exclusions and visual_review.json.
