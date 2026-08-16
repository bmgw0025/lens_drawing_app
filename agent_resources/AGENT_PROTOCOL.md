# Lens Drawing V4 Agent 接管协议

协议版本：4.0.0

## 1. 权威数据

不同对话中的 Agent 不得依赖聊天记忆续接任务。每次接管必须按以下顺序读取任务目录：

1. `task_state.json`：唯一权威状态与下一步动作。
2. 任务目录内的 `AGENT_PROTOCOL.md`：创建任务时锁定的本协议快照。
3. `AGENT_HANDOFF.md`：便于阅读的状态摘要，不替代状态 JSON。
4. `source_analysis/analysis_summary.json`：组数、拓扑、阻断项和待确认几何字段。
5. `source_analysis/drawing_drafts.json`：ZMX 推导出的 Glass/T/R/MD/AD、来源和拓扑证据。
6. `source_analysis/agent_work_order.json`：加工字段目录、默认值和覆盖范围。
7. `agent_request.json` 与 `request_validation.json`：当前需求版本及校验结果。

任务创建时会复制协议、请求 Schema 和生成的 Agent spec，并在 `task_state.json` 中记录 SHA-256。任务还会锁定 Lens Drawing 运行时身份、绘图引擎 manifest、源 ZMX 哈希和 `source_analysis` 四个文件的 manifest。任一快照、运行时或分析文件发生变化时，程序必须拒绝执行旧任务；应使用当前版本重新创建任务。

## 2. 固定接管顺序

1. 对新 ZMX 运行 `create`。不得手工建立 `source_analysis`。
2. 若状态为 `blocked_geometry`，读取 blockers 并向用户解释；不得通过加工请求或聊天判断解除几何阻断。
3. 若状态为 `needs_input` 或 `needs_clarification`，只追问尚未得到明确证据的项目。
4. 将用户原话和附件登记到 `user_evidence`，附件必须记录当前本地路径和 SHA-256。
5. 在 `requirement_analysis` 中逐条形成决策，并为每条证据填写 `evidence_disposition`。
6. 填写命名、完整加工要求，以及确有需要时的中等置信几何原值确认。
7. 将候选请求保存到任务目录外的临时文件，运行 `submit`；不得直接改写已提交的 `agent_request.json`。
8. 运行 `validate`。只有状态为 `ready` 才可运行 `run`。
9. `run` 后读取 `result/pdf_validation_report.json`。自动检查不通过时不得视觉放行。
10. 状态为 `awaiting_human_review` 时停止 Agent 自动流程，由授权人工操作员逐页检查 `validation_render/contact_sheet_*.png` 并运行 `review`。
11. 只有 `task_state.status=completed` 才从 `delivery_manifest.json` 交付 PDF 清单。

## 3. ZMX 几何边界

Glass、T、R、MD、AD、单位换算、面映射和拓扑只来自只读 ZOS-API 与确定性 mapper，Agent 不得填写或修改。

准确全自动几何只接受 `.zmx`。完整截图可以作为型号、加工要求或人工识别证据，但不能替代 ZOS-API 数据进入生产自动接受路径；只有截图时必须说明精度限制并转入另行确认的人工路线。

Zemax 的 `GLAS` 表示该面之后的介质。相邻玻璃区间按以下规则分组：

- 直接相邻玻璃区间共享一个 LDE 面，属于 `direct_cemented_interface`。
- 若玻璃区间之间仅有非玻璃面，且这些间隔的 Thickness 全为 0，同时重复界面面型相同、曲率相同、无 tilt/decenter，则属于 `virtual_cemented_interface`。
- 虚拟界面的重复物理面只折叠为一个逻辑 R 边界；相邻镜片分别保留自己的 `AD_right`/`AD_left` 和 MEMA 证据。两侧 AD 不同不再因旧 Excel 行只能表达共享 AD 而阻断。
- 只要间隔非零，即使曲率相同也不是胶合连续界面。
- 零厚度但面型、曲率或坐标不一致时属于 `ambiguous_zero_thickness_compound`，必须阻断。

每个虚拟界面的 `coincidence_evidence` 必须明确记录零厚度面、重复面、曲率、面型、坐标检查和各布尔判据。测试文件2的 surface 3/4 因 Thickness=0、R=-58、Standard 且无 tilt/decenter 被折叠，随后 surface 5 是直接胶合界面，因此整个组是一组三胶合。

中等置信几何字段只允许在 `geometry_review.fields` 中按 `source_analysis` 原值确认；改值即失败。blocked 字段不能由确认绕过。

高置信结论可以继续出图，但其 warnings 必须保留到审计和交付并向用户说明；中等置信字段必须在运行前确认；明显低置信或 blocked 情况必须在任务执行前停止。

单片组若玻璃为 `H-K9L` 且两侧均为平面（Radius 为 Infinity/0），按业务规则记录为 `excluded_prism`，不消耗镜片命名/生产编码序号且不生成 PDF。任务必须保留曲率、材料、厚度和面号证据，并在最终交付中明确告知剔除结果。

## 4. 用户需求证据

`production` 模式只接受真实 `user_message` 或 `attachment`：

- `user_message.content` 保存足以复核决策的用户原话。
- `attachment.source_ref` 必须是当前可读的本地文件路径，`sha256` 必须与文件当前内容一致。
- `operator_record` 只允许 `test` 模式，不能作为生产授权。
- 每条证据必须在 `evidence_disposition` 标为 `mapped` 或 `no_action`，且说明理由。
- 每个实际覆盖字段必须通过 `field_evidence` 指向明确证据。

Agent可以把用户明确表达的自然语言转换为白名单字段并提交执行。Agent不得自行发明公差、CA、倒角、膜层、油墨、材料规格或命名。未提及的加工字段使用当前版本内置的固定 Agent 默认值，绝不读取 GUI 上一次持久化设置；仍需用户明确批准“未提及项使用固定默认值”，沉默不是批准。

## 5. 必要追问

只在以下信息无法从当前任务证据确定时追问：

- 业务命名：镜头型号写入 SavePdfFolder；镜片型号写入 MfrPdfFolder 并按有效镜片组顺序生成 PartName；首枚生产编码按数字尾缀等宽递增生成 PartNo。递增格式不明确时必须追问。
- 完整加工要求：明确覆盖项，或明确同意全部使用任务中锁定的 renderer defaults。
- `geometry_review.fields` 中列出的中等置信机械值是否按原值确认。
- 用户描述与附件冲突，或同一字段出现多个互斥值。

不得追问 ZMX 已高置信确定的 Glass/T/R/MD/AD，也不得让用户用一句“继续”替代缺失的生产授权。

## 6. 模式与完成条件

`production`：必须由真实用户证据支持命名、完整加工要求和必要的几何确认。自动验收、授权人工视觉验收和 `production_release_ready=true` 后才能完成。Agent 与视觉模型都不得代替人工签核。

`test`：可用 `operator_record` 验证接口和渲染能力。测试完成仅证明软件闭环，不构成任何真实图纸生产放行。

失败任务和输出目录必须保留，不得覆盖或改写审计。人工验收记录保存为 `human_visual_review.json`。修正需求或实现后创建新任务目录。

每个有效镜片组交付两份 PDF：存档版位于 `SavePdfFolder/PartName.pdf` 且显示 PartName；编码版位于 `MfrPdfFolder/PartNo.pdf` 且隐藏 PartName。最终交付还必须包含 `manufacturing_requirements_summary.md`、完整 JSON 清单、所有高置信警告和棱镜剔除记录。

## 7. 安装版命令

`LensDrawing.exe` 是 GUI 和 Agent 共用的唯一程序。Agent 模式必须带 `--agent`，安装版为 windowed EXE，必须带 `--output-json` 取得 UTF-8 JSON 结果。推荐使用随 Skill 提供的 `scripts/Invoke-LensDrawingAgent.ps1`，它会定位安装目录并在每次调用前比较 Skill spec 与 EXE spec。

```powershell
LensDrawing.exe --agent --output-json "C:\path\spec-result.json" spec
LensDrawing.exe --agent --output-json "C:\path\create-result.json" create "C:\path\lens.zmx" "C:\path\task"
LensDrawing.exe --agent --output-json "C:\path\status-result.json" status "C:\path\task"
LensDrawing.exe --agent --output-json "C:\path\submit-result.json" submit "C:\path\task" "C:\path\candidate-request.json"
LensDrawing.exe --agent --output-json "C:\path\validate-result.json" validate "C:\path\task"
LensDrawing.exe --agent --output-json "C:\path\run-result.json" run "C:\path\task"
LensDrawing.exe --agent --output-json "C:\path\review-result.json" review "C:\path\task" --status passed --reviewer "operator-id" --note "人工逐页检查说明"
```

所有命令返回统一 envelope：`ok`、`command`、`exit_code`、`interface_version` 以及 `result` 或 `error`。退出码 `0` 表示门槛通过，`1` 表示参数/资源/执行错误，`2` 表示命令已完成但几何、校验、视觉验收或生产放行门槛尚未通过。
