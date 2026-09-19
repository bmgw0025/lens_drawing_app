# Lens Drawing 4.1 Agent 协议

协议版本：4.1.0  
Request Schema：1.2  
Task Schema：1.1

## 权威边界

1. Lens Drawing 只读打开一个 ZMX，保存已求值 surface、源文件 SHA-256、ZOS-API DLL 路径和 OpticStudio 版本。
2. Glass、T、R、AD、MD 和物理拓扑只能来自 Lens Drawing 冻结的 ZOS-API 候选。
3. Agent 只能向 resolve-geometry 提交 case_id、candidate_id 和理由，禁止提交或修改几何数值。
4. AD 只可来自显式圆孔径或 SemiDiameter；MD 只可来自 MechanicalSemiDiameter，且必须满足 MD >= max(AD_left, AD_right)。
5. 零厚度虚拟胶合面只有在面型、曲率一致且无 tilt/decenter 时才成为可选候选。任何虚拟面都必须由 Agent 选择候选后才能继续。
6. resolve-geometry 不能把中等置信候选变成自动放行。task_state.required_geometry_confirmations 中的每个 confirmation_id 都必须通过 DWS 取得 user_message 或 attachment 证据。
7. drawing_drafts[].lenses[] 是分侧 AD/MD 的权威结构；legacy row 仅为兼容视图。
8. 加工默认值来自任务内 deployment_policy.json 快照。未被用户明确覆盖的字段无需逐任务再次批准。
9. 正常生产视觉门槛由 deployment_policy 指定，默认 vision_agent。主 Agent 不拥有 review 工具；宿主验证报告后调用 review。

## 状态与命令

正常流程：

```text
create
  -> awaiting_geometry_resolution
  -> resolve-geometry
  -> needs_clarification（存在低置信确认项时）
  -> needs_input
  -> submit
  -> validate
  -> ready
  -> run
  -> awaiting_visual_review
  -> review
  -> completed
```

没有待选候选时，create 可直接进入 needs_input。失败或阻断状态包括 blocked_geometry、geometry_resolution_failed、needs_clarification、validation_failed、execution_failed、visual_review_failed 和 release_blocked。

```powershell
LensDrawing.exe --agent --output-json create.json create input.zmx task-dir --deployment-policy deployment_policy.json
LensDrawing.exe --agent --output-json resolve.json resolve-geometry task-dir geometry-decision.json
LensDrawing.exe --agent --output-json submit.json submit task-dir request.json
LensDrawing.exe --agent --output-json validate.json validate task-dir
LensDrawing.exe --agent --output-json run.json run task-dir
LensDrawing.exe --agent --output-json status.json status task-dir
LensDrawing.exe --agent --output-json review.json review task-dir --status passed --kind vision_agent --reviewer "minimax-m3@provider" --report vision-review.json --note "Automated page review passed."
```

## 几何 Decision

Decision 顶层只能包含 schema_version、task_id 和 cases。每个 case 只能包含 case_id、topology_candidate_id、field_selections 和 reason。field_selections 的值必须是 geometry_cases.json 中当前字段的 candidate_id。

未知候选、遗漏字段、额外字段、AD/MD 来源混用、未通过的虚拟面、修改数值或最终 MD 小于所选 AD 都会失败。失败不会覆盖原候选，可重新提交一份完整 decision。

resolve-geometry 成功后必须读取 task_state.required_geometry_confirmations。非固定 MEMA、非高置信 AD/MD 或多个仍可行拓扑不会由 Agent 自行消除；宿主应把其中 prompt 通过 DWS 发给内部技术人员。

## 需求与加工要求

生产证据只接受 user_message 和带 SHA-256 的 attachment；operator_record 仅用于 test。每条证据都必须有 evidence_disposition。命名和用户明确提出的加工覆盖必须引用证据；部署级默认值通过 policy_id 和 policy_sha256 绑定，不需要用户证据。

每个低置信确认项必须增加一条 requirement_analysis.decisions 记录：

~~~json
{
  "category": "geometry_confirmation",
  "confirmation_id": "group-1:MD2:md-g1-l2-c1",
  "statement": "用户确认采用该 ZOS-API MEMA 求值作为 MD2。",
  "evidence_ids": ["user-confirm-md2"]
}
~~~

对应 evidence_disposition target 必须是 geometry_confirmation.<confirmation_id>。缺少、重复或未知 confirmation_id 都会使 validate 失败。

提交请求必须使用 submit 保留版本，不得直接修改已提交的 agent_request.json。只有 validate 返回 valid=true 且状态为 ready 才能 run。

## 视觉报告

review --kind 必须与 execution.visual_review.mode 一致。--report 必须为 schema_version=1.0，并包含 status、review_kind、issues、uncertainties 和 artifacts。vision_agent 还必须包含 model、prompt_version 和 duration_ms。

artifacts.contact_sheets 与 artifacts.pdfs 必须完整列出当前全部产物及 SHA-256；Lens Drawing 会重新计算哈希。passed 报告不能含未解决 issues 或 uncertainties，且不能覆盖 PDF 自动校验失败。

只有 task_state.status=completed 才允许按 delivery_manifest.json 投递。失败任务、报告和输出不得覆盖。
