# LensBot 1.0 / Lens Drawing 4.1 部署执行方案

版本：1.0  
日期：2026-08-18  
适用平台：Windows x64、OpticStudio 2022 R2.01

## 1. 输入与完成条件

从解压目录开始。目录中必须存在：

~~~text
00_README_FIRST.md
TARGET_MACHINE_DEPLOYMENT_EXECUTION.md
third_party.lock.json
release_manifest.json
SHA256.txt
config/
schemas/
LensDrawing_4.1.0_onedir/
LensDrawing_4.1.0_Setup.exe
~~~

最终完成必须同时满足：

1. 包内清单与全部 SHA-256 一致。
2. Lens Drawing spec 精确为 4.1.0 / Request 1.2 / Task 1.1。
3. DWS Gateway、Pi Worker 和 Python Host 的测试与构建全部通过。
4. 服务账号下 ZOS-API、主模型、副模型和 DWS 探针全部通过。
5. Windows 服务自动启动，三个子进程握手正常。
6. 真实钉钉测试群完成一条自动交付任务和一条澄清后续接任务。
7. ZIP 由配置的机器人身份发送，重启服务后不重复投递。
8. C:\ProgramData\LensBot\artifacts 中的完成证据齐全。

任何一个门槛未通过，都保留证据并停止在当前阶段，不得把服务标记为生产可用。

## 2. 固定边界

- Lens Drawing 只使用包内 onedir release，不从网络获取其他版本。
- DWS 固定提交 effde762277ad717a246e38b237a8297fa49aab7。
- Pi 固定 v0.84.2，提交 914cf1472e715297caa30db4b9535d534a9eb718。
- 主模型固定 glm-5.3；视觉模型固定 minimax-m3。
- 模型接口固定 https://ark.cn-beijing.volces.com/api/coding/v3，API 类型为 openai-responses。
- 一个任务一个 Agent 会话；不共享群级模型上下文。
- 全局同时最多运行一个 ZOS-API create 或 run。
- 只接受 ZMX 作为自动几何输入。
- AD 只能来自 aperture 或 SemiDiameter；MD 只能来自 MechanicalSemiDiameter。二者均按全直径、毫米处理。
- Agent 只能选择 geometry_cases.json 中的候选 ID，不能提交自定义几何数值。
- 正常任务在确定性校验和机器视觉通过后自动打包、发送，不逐单人工签核。
- 只有缺少必要输入、原始数据冲突或低置信几何通过 DWS 询问。
- 超出 renderer 范围的真实非球面、非零偏心/倾斜、非顺序、有效多配置或四片以上结构直接准确拒绝，不近似出图。
- 首期不自动删除任务和交付数据。

## 3. 建立部署记录

以管理员 PowerShell 运行。Codex先确定解压根目录，再执行：

~~~powershell
$ErrorActionPreference = "Stop"
$PackageRoot = (Resolve-Path ".").Path
$InstallRoot = "C:\Program Files\LensBot"
$DataRoot = "C:\ProgramData\LensBot"
$BuildRoot = "C:\ProgramData\LensBotBuild"
$Artifacts = Join-Path $DataRoot "artifacts"

New-Item -ItemType Directory -Force -Path $InstallRoot,$DataRoot,$BuildRoot,$Artifacts | Out-Null
~~~

在 artifacts\deployment-journal.jsonl 中逐阶段追加一行 JSON，至少记录阶段、开始/结束时间、结果、产物哈希和失败信息。不要记录 API Key、AppSecret、Token、Windows 密码、完整聊天正文或完整 ZMX 内容。

## 4. 验证转移包

SHA256.txt 的每行格式为：

~~~text
64位小写SHA256 *相对路径
~~~

逐项验证：

~~~powershell
$Failures = [System.Collections.Generic.List[string]]::new()

Get-Content -LiteralPath (Join-Path $PackageRoot "SHA256.txt") | ForEach-Object {
    if ($_ -notmatch '^([0-9a-f]{64}) \*(.+)$') {
        throw "SHA256.txt 行格式错误: $_"
    }
    $Expected = $Matches[1]
    $Relative = $Matches[2] -replace '/', '\'
    $Path = Join-Path $PackageRoot $Relative
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        $Failures.Add("缺失: $Relative")
    } else {
        $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
        if ($Actual -ne $Expected) {
            $Failures.Add("哈希不匹配: $Relative")
        }
    }
}

if ($Failures.Count -gt 0) {
    throw ($Failures -join [Environment]::NewLine)
}
~~~

再读取 release_manifest.json，验证每个文件的 path、size 和 sha256，并确认：

- 清单覆盖除 release_manifest.json 和 SHA256.txt 外的全部包内文件。
- 不存在未被清单记录的额外文件。
- Lens Drawing release 外部不存在 agent_cli.py、autodraw、main.py、web_app.py 等项目源码。
- LensDrawing_4.1.0_onedir\_internal 中第三方包自带的 .py 文件允许存在。

把验证结果写入 artifacts\package-verification.json。

## 5. 环境预检与缺失工具安装

记录以下结果到 artifacts\preflight.json：

- Windows 版本、Build、x64 状态和当前账号 SID。
- Python、Node、npm、Go、Git 版本。
- OpticStudio 安装目录。
- ZemaxRoot。
- 当前服务账号候选。

要求：

~~~text
Python 3.12.10
Node >= 22.19.0
Go 1.25.9
Git 可用
Windows x64
C:\Program Files\Ansys Zemax OpticStudio 2022 R2.01
~~~

先检测，不重复安装已有的正确版本。Go 不存在或版本不是 1.25.9 时，从 go.dev 官方下载索引定位 go1.25.9.windows-amd64.msi，校验索引给出的 SHA-256 后静默安装。不要使用其他 Go 版本继续。

Node 版本不足时，从 nodejs.org 官方目录下载 node-v24.15.0-x64.msi 和对应 SHASUMS256.txt，校验后静默安装。Python 缺失时安装官方 Python 3.12.10 x64。安装后重新打开 PowerShell 并再次生成 preflight.json。

如果 OpticStudio 或 ZemaxRoot 不存在，停止并询问实际绝对路径；不要切换为文本 ZMX 解析器。

## 6. 安装并验证 Lens Drawing release

目标目录：

~~~text
C:\Program Files\LensBot\lensdrawing\
~~~

如果该目录不存在，递归复制 LensDrawing_4.1.0_onedir 的内容。如果已存在，先比较当前 LensDrawing.exe 和 agent_resources\build_manifest.json 的 SHA-256；完全一致则复用，不一致则停止，不覆盖未知安装。

运行窗口版 EXE 时必须用 Start-Process -Wait，并只读取 --output-json 文件：

~~~powershell
$LensExe = Join-Path $InstallRoot "lensdrawing\LensDrawing.exe"
$SpecOut = Join-Path $Artifacts "lensdrawing-spec.json"

$Process = Start-Process -FilePath $LensExe -ArgumentList @(
    "--agent",
    "--output-json",
    $SpecOut,
    "spec"
) -Wait -PassThru

if ($Process.ExitCode -ne 0) {
    throw "Lens Drawing spec 失败，exit=$($Process.ExitCode)"
}

$Envelope = Get-Content -LiteralPath $SpecOut -Raw | ConvertFrom-Json
$Spec = $Envelope.result.spec

if ($Envelope.ok -ne $true) { throw "spec envelope.ok != true" }
if ($Envelope.interface_version -ne "4.1.0") { throw "Agent Interface 版本错误" }
if ($Spec.application.version_full -ne "4.1.0") { throw "Lens Drawing 版本错误" }
if ($Spec.agent_interface.request_schema_version -ne "1.2") { throw "Request Schema 版本错误" }
if ($Spec.agent_interface.task_schema_version -ne "1.1") { throw "Task Schema 版本错误" }

$RequiredCommands = @(
    "create",
    "resolve-geometry",
    "submit",
    "validate",
    "run",
    "status",
    "review"
)

foreach ($Command in $RequiredCommands) {
    if ($Spec.agent_interface.commands -notcontains $Command) {
        throw "缺少 Lens Drawing 命令: $Command"
    }
}
~~~

同时验证安装目录中的：

~~~text
agent_resources\AGENT_PROTOCOL.md
agent_resources\agent_request.schema.json
agent_resources\build_manifest.json
agent_resources\deployment_policy.example.json
agent_resources\lens_drawing_agent_spec.json
skills\lens-drawing-agent\SKILL.md
skills\lens-drawing-agent\references\workflow.md
skills\lens-drawing-agent\references\persistence-and-judgment.md
~~~

## 7. 创建构建工作区

使用以下目录：

~~~text
C:\ProgramData\LensBotBuild\
  dws\
  agent-worker\
  host\
  artifacts\
~~~

所有新项目都初始化本地 Git 仓库并提交一个部署基线提交，便于 Codex 在中断后读取状态和续接。不得把任何密钥提交到仓库。

## 8. 构建 DWS Gateway

### 8.1 获取冻结源码

~~~powershell
Set-Location $BuildRoot
git clone https://github.com/DingTalk-Real-AI/dingtalk-workspace-cli.git dws
Set-Location (Join-Path $BuildRoot "dws")
git checkout --detach effde762277ad717a246e38b237a8297fa49aab7

if ((git rev-parse HEAD).Trim() -ne "effde762277ad717a246e38b237a8297fa49aab7") {
    throw "DWS 提交不匹配"
}

$GoSumHash = (Get-FileHash -Algorithm SHA256 -LiteralPath ".\go.sum").Hash.ToLowerInvariant()
if ($GoSumHash -ne "0c59b7bf9211fba5c2d63298a09f5f12c8ad1cbce4052a22a56b5d84b5ab36ca") {
    throw "DWS go.sum 哈希不匹配"
}

git switch -c codex/lensbot-gateway
~~~

### 8.2 最小修改范围

只在现有 DWS 生命周期内增加 dws lens connect：

1. 复用现有 DingTalk Stream 连接、快速 ACK、重连、附件发现和下载。
2. 复用 chat message send-by-bot 的 Markdown 与本地文件上传发送路径。
3. 增加 schemas\dws-lens-v1.schema.json 定义的 Named Pipe JSONL 适配层。
4. 不使用原版 --agent-cmd 作为生产协议。
5. 不改写现有认证、聊天命令或其他产品命令。

固定 Pipe：

~~~text
\\.\pipe\lensbot-gateway-v1
~~~

Python Host 是 Pipe Server；Gateway 是自动重连的 Pipe Client。每行一个 UTF-8 JSON，最大 4 MiB。连接后先发送 hello，只有收到 hello_ok 才处理业务帧。

入站幂等键固定为：

~~~text
SHA256(robotCode + "|" + conversationId + "|" + msgId)
~~~

不得依赖未验证的 event_id。Gateway 先快速 ACK 钉钉回调，再完成附件下载和本地事件落盘。

spool 规则：

1. 先把附件写入临时文件，关闭后计算 SHA-256，再原子改名。
2. 事件先写 .tmp，再原子改名为 .json。
3. 只有 Host 返回 accepted 后才能删除事件 JSON。
4. Pipe 断线或进程重启后，按 received_at 和 request_id 重放未确认事件。
5. Host 以 event_id 主键再次幂等，重复重放不能创建第二个任务。

出站只实现 send_text 和 send_file。send_file 只允许读取 C:\ProgramData\LensBot\deliveries 下的普通文件。发送成功必须返回 provider_key；失败返回结构化 code、message 和 retryable。

### 8.3 DWS 测试与构建

至少覆盖：

- hello 版本/提交/robot code 校验。
- Pipe 断线重连。
- ACK 后 Host 掉线，spool 重放。
- 重复 msgId。
- 群和用户白名单。
- 中文 ZMX 文件名。
- Markdown、引用消息和机器人本地 ZIP 投递。
- 发送失败的结构化结果。

执行：

~~~powershell
go test -count=1 ./...
New-Item -ItemType Directory -Force -Path ".\artifacts" | Out-Null
go build -trimpath -o ".\artifacts\dws-lens.exe" ".\cmd"
~~~

运行 dws-lens.exe --help 和 dws-lens.exe lens connect --help，确认新命令存在。记录测试输出、Git HEAD、二进制 SHA-256 到 artifacts\build-manifest.json。

## 9. 构建 Pi Agent Worker

### 9.1 项目和依赖

创建 C:\ProgramData\LensBotBuild\agent-worker。生产依赖必须精确为：

~~~json
{
  "type": "module",
  "dependencies": {
    "@earendil-works/pi-agent-core": "0.84.2",
    "@earendil-works/pi-ai": "0.84.2",
    "typebox": "1.3.7"
  }
}
~~~

使用 third_party.lock.json 中的 npm integrity 校验 npm lockfile。构建工具也使用精确版本：esbuild 0.28.2、TypeScript 7.0.2、Vitest 4.1.10、@types/node 24.13.3。生成并提交 package-lock.json，后续只运行 npm ci。

### 9.2 Worker 边界

Worker 使用 stdin/stdout pi-worker/1 JSONL：

- stdout 只能写协议 JSON。
- 日志只写 stderr，且不写密钥和完整用户文件内容。
- 启动后先发送 hello，收到 hello_ok 后才接受请求。
- 同一进程一次只处理一个 run_agent 或 run_vision_review。
- Host 负责排队、持久化和超时。

Worker 固定读取以下已安装资源：

~~~text
C:\Program Files\LensBot\lensdrawing\skills\lens-drawing-agent\SKILL.md
C:\Program Files\LensBot\lensdrawing\skills\lens-drawing-agent\references\workflow.md
C:\Program Files\LensBot\lensdrawing\skills\lens-drawing-agent\references\persistence-and-judgment.md
C:\Program Files\LensBot\lensdrawing\skills\lens-drawing-agent\references\lens_drawing_agent_spec.json
~~~

启动时计算 SHA-256，与 agent-worker.manifest.json 对照。不要扫描用户目录、.pi、AGENTS.md、编码提示或其他 Skill。

### 9.3 模型和工具

使用 @earendil-works/pi-agent-core 创建主 Agent；使用 @earendil-works/pi-ai 的 openai-responses provider。模型参数从 models.json 读取，API Key 只从 Host 传入的进程环境读取。

主 Agent 只注册：

~~~text
lens_create
lens_resolve_geometry
lens_submit
lens_validate
lens_enqueue_run
lens_status
ask_customer
reject_task
emit_delivery_summary
~~~

所有参数使用 TypeBox 严格对象 Schema，不允许额外字段。lens_resolve_geometry 只接受 task_id、case_id、topology_candidate_id、field_selections 中的候选 ID 和 reason；禁止 Glass、T、R、AD、MD 数值字段。Worker 只发 tool_call，实际文件和 Lens Drawing 操作全部由 Host 完成。

每个 task_id 使用独立上下文。每次调用由 Host 传入 task_snapshot、当前任务的 conversation_history 和 user_evidence；Worker 不依靠隐藏思考或进程内记忆恢复任务。

### 9.4 视觉检查

run_vision_review 使用 minimax-m3，只检查：

- 空白页。
- 内容裁切。
- 标注明显重叠。
- 关键标注缺失或不可读。
- 页码和页面顺序异常。

不重新判断 ZOS 数值、单位、AD/MD 或几何映射。

输入为 Lens Drawing 已生成的 contact sheet，最长边最多 2000 px，每次最多 4 张。输出按 schemas\visual_review.schema.json。单次超时 90 秒；JSON 结构损坏时仅允许同模型进行一次 schema repair。仍失败则返回失败，不伪造通过。

最终 report 的 artifacts 必须由 Worker/Host使用 Host 提供的绝对路径和 SHA-256 原样构造，不允许模型改写路径或哈希。passed 时 issues 和 uncertainties 必须都为空。

### 9.5 Worker 构建与测试

至少覆盖：

- hello 和 JSONL 帧限制。
- 每任务会话隔离。
- 九个工具白名单和额外字段拒绝。
- 未知候选/自定义数值不能形成工具调用。
- tool result 后续轮。
- stderr/stdout 隔离。
- 视觉 Schema、图片输入、超时和一次 repair。
- 资源 manifest 篡改时拒绝启动。

执行 npm ci、npm test、npm run build、npm run bundle。bundle 产物固定为单文件 agent-worker.mjs。用一个不含 node_modules 的临时目录，仅复制 node.exe、agent-worker.mjs、manifest 和测试资源，运行协议测试。

## 10. 构建确定性 Python Host

### 10.1 依赖与入口

在 C:\ProgramData\LensBotBuild\host 创建 Python 3.12.10 venv。运行依赖只保留 pywin32 312 和 jsonschema 4.26.0；构建使用 PyInstaller 6.20.0。其余功能使用标准库。

Host 产物为 PyInstaller onedir，入口 LensBotHost.exe，提供：

~~~text
LensBotHost.exe service --config <path>
LensBotHost.exe configure-secret --config <path>
LensBotHost.exe preflight --config <path> --output <path>
LensBotHost.exe health --config <path> --output <path>
LensBotHost.exe migrate --config <path>
~~~

### 10.2 目录和 SQLite

使用：

~~~text
C:\ProgramData\LensBot\
  config\
  db\lensbot.db
  gateway-spool\
  tasks\<task_id>\
  deliveries\<task_id>_<lens_model>\
  logs\
  health\status.json
  artifacts\
~~~

SQLite 使用 WAL、busy_timeout=5000、单 Host 写入。至少实现：

~~~text
inbound_messages
files
tasks
task_events
agent_turns
deliveries
settings
schema_migrations
~~~

event_id、delivery_id 和 schema migration version 必须有唯一约束。

### 10.3 进程监督

Host 启动：

1. 一个 dws-lens.exe lens connect。
2. 一个 node.exe agent-worker.mjs。
3. 每条 Lens Drawing 命令启动一个一次性 LensDrawing.exe --agent 子进程。

Gateway 和 Worker 异常退出后由 Host 记录一次状态并重启。Lens Drawing 子进程放入 Windows Job Object；超时时只终止该任务进程树，不按进程名结束用户手工打开的 OpticStudio。

Lens Drawing 是窗口版 EXE，Host 必须等待进程退出并读取 --output-json 文件，不能依赖 stdout。退出码解释：

~~~text
0 = 命令完成且当前门槛通过
1 = 参数、资源或执行错误
2 = 命令完成，但当前几何/校验/审图/放行门槛未通过
~~~

退出码 2 不是进程崩溃，必须结合 JSON envelope 和 task_state.json 路由下一步。

### 10.4 文件和任务

1. 只接受扩展名大小写不敏感的 .zmx。
2. 一个消息包含多个 ZMX 时，每个文件建立一个任务。
3. ZMX 与文字同消息时直接绑定；单独上传也立即建立任务并返回任务号。
4. 文件移入 tasks\<task_id>\input\ 的随机文件名，保留原名、大小和 SHA-256 元数据。
5. Host 重算 SHA-256 并与 Gateway 事件一致。
6. 不覆盖同名文件，不执行上传文件，不增加 Defender 扫描流程。
7. 任务号格式为 LD-YYYYMMDD-HHMMSS-XXXX。

### 10.5 Host 状态机

使用以下主状态：

~~~text
received
analyzing
waiting_customer
ready
queued
running
visual_review
packaging
delivering
completed
cancelled
rejected
system_wait
execution_failed
review_failed
delivery_failed
~~~

每次变化写 task_events。聊天内容不能作为权威状态。

ZOS 命令队列全局并发固定为 1。首期可以把全部 Lens Drawing 命令串行，优先降低状态竞争。许可证暂不可用时当前任务进入 system_wait；每 60 秒执行一次轻量 probe，恢复后自动排队。命令超时自动重试一次，第二次失败进入 execution_failed。

### 10.6 任务流水线

每个任务严格按下列顺序：

1. 调用 create，显式传 deployment policy、ZemaxRoot 和 OpticStudio 安装目录。
2. 读取 task_state.json、source_analysis\analysis_summary.json 和 geometry_cases.json。
3. 唯一高置信普通几何直接继续；存在虚拟界面或多个合法归属时，让主 Agent 选择候选 ID。
4. Host 调用 resolve-geometry；Lens Drawing 再次验证候选来源、候选未变、MD 来源和 MD >= AD。
5. 读取 task_state.required_geometry_confirmations。任何确认项存在时，状态必须为 needs_clarification，Host 逐项通过 DWS 发送其 prompt。
6. 每个 confirmation_id 必须取得 user_message 或 attachment 证据；主 Agent 在 requirement_analysis.decisions 增加 geometry_confirmation，并把证据映射到 geometry_confirmation.<confirmation_id>。
7. 主 Agent 依据用户证据和部署策略生成 agent_request.json。
8. Host 调用 submit，再调用 validate；缺失、重复或未知 confirmation_id 必须失败。
9. 只有 Lens Drawing 状态为 ready 才进入 queued。
10. 调用 run。run 会重新读取 ZMX 并重新生成候选，候选发生变化必须失败。
11. 自动 PDF 校验通过并进入 awaiting_visual_review 后，调用 run_vision_review。
12. Host 校验视觉 JSON Schema、全部 PDF/contact sheet 路径和 SHA-256，再调用 review --kind vision_agent。
13. 只有 Lens Drawing task_state.json 为 completed 且 delivery_manifest.json 的 completed=true 才允许打包。
14. 先发送 ZIP，再发送摘要；两步成功后 Host 才写 completed。

需要向用户询问时，ask_customer 只列出当前缺失字段、冲突的 surface/值或低置信候选，并包含任务号。用户回复中含任务号时直接绑定；不含任务号且同一发送者在该群只有一个 waiting_customer 任务时自动绑定；仍有歧义时仅询问任务号。

超出 renderer 范围时使用 reject_task，发送准确限制并终止任务，不请求模型近似。

### 10.7 确定性聊天命令

以下命令不得调用 LLM：

~~~text
#状态 [任务号]
#取消 <任务号>
#重试 <任务号>
~~~

#取消 对 waiting_customer/ready/queued 立即取消；running 时结束该任务 Job Object。#重试 只允许 system_wait、execution_failed、review_failed 和 delivery_failed。

### 10.8 视觉失败

视觉检查发现问题、模型超时或 repair 后仍不满足 Schema 时：

- 不交付文件。
- 状态进入 review_failed。
- 通过 DWS 返回任务号、失败摘要和 contact sheet。
- 不把视觉失败转成客户参数澄清。
- 等待 #重试 或代码修复，不伪造 passed。

### 10.9 交付与幂等

交付目录至少包含：

~~~text
archive-pdf\
production-pdf\
manufacturing_requirements_summary.md
manufacturing_requirements_delivery.json
geometry_summary.json
visual_review.json
delivery_manifest.json
<task_id>_<lens_model>.zip
~~~

ZIP 内的 delivery_manifest.json 记录每个成员的相对路径、大小和 SHA-256，但不记录 ZIP 自身哈希。ZIP 自身哈希写入 ZIP 外部的 delivery_archive.json 和 SQLite delivery ledger。这样避免自引用哈希。

固定：

~~~text
delivery_id = SHA256(task_id + "|" + conversation_id + "|" + zip_sha256)
~~~

投递顺序：

1. 生成 ZIP 和 zip_sha256。
2. ledger 写 sending_file。
3. Gateway 发送 ZIP。
4. 成功后记录 provider_key 并写 file_sent。
5. 发送包含任务号、镜片型号、PDF 数量、警告和 ZIP SHA-256 前 12 位的摘要。
6. 摘要成功后写 completed。

服务重启后，file_sent 的 ZIP 不得再次发送，只补发未成功摘要。

### 10.10 Host 测试和构建

至少覆盖：

- SQLite migration、WAL 和重启恢复。
- event_id 幂等。
- 多 ZMX 建立多个任务。
- Agent 会话隔离。
- Lens Drawing 退出码 0/1/2。
- 合法候选、未知候选、候选值变化和 MD < AD。
- 单 ZOS 队列、超时、取消和许可证 system_wait。
- 视觉 passed/failed/Schema 错误。
- ZIP manifest、delivery_id 和 file_sent 后重启不重复。
- #状态、#取消、#重试 不触发 Worker。

执行：

~~~powershell
python -m unittest discover -s tests -v
python -m PyInstaller --clean --noconfirm LensBotHost.spec
~~~

在不含 Python 和源码的临时目录运行冻结版 preflight、migrate 和 health。

## 11. 配置与一次性输入

将包内模板复制为：

~~~text
C:\ProgramData\LensBot\config\lensbot.toml
C:\ProgramData\LensBot\config\models.json
C:\ProgramData\LensBot\config\deployment_policy.json
~~~

一次集中询问并取得：

1. robot code 和 DWS 所需应用信息。
2. allowed_groups 和 allowed_users。
3. 火山方舟 API Key。
4. 运行 LensBotHost 的 Windows 账号凭据。
5. 固定加工默认值的批准人标识和是否批准。

先向用户展示 deployment_policy.json 中全部 manufacturing_defaults。只有明确批准后才修改：

~~~text
approval_status = approved
approved_by = 用户确认的标识
approved_at = 当前 RFC3339 时间
zosapi.zemax_root = 实际绝对路径
zosapi.opticstudio_install_dir = 实际绝对路径
~~~

如果默认值被修改，使用 Python 标准库按 ensure_ascii=false、sort_keys=true、separators=(",",":") 序列化 manufacturing_defaults 后计算 SHA-256，更新 manufacturing_defaults_sha256。不要修改字段集合。

API Key 使用 Windows DPAPI CurrentUser 加密为 C:\ProgramData\LensBot\config\secrets.dpapi。明文不写入 TOML、JSON、命令行、日志或 artifacts。

DWS 授权必须在同一个服务账号和固定 DWS_CONFIG_DIR 下完成：

~~~text
C:\ProgramData\LensBot\dws-config
~~~

执行 dws-lens.exe auth login 或 auth login --device，并用 auth status --format json 确认 refresh token 有效。授权需要浏览器/设备码时暂停等待用户完成，完成后自动续接。

## 12. 模型探针

在安装服务前调用冻结 Worker，生成 artifacts\model-probe.json。必须实测：

1. glm-5.3 短文本。
2. glm-5.3 指定单工具调用。
3. tool_result 后续轮。
4. glm-5.3 严格 JSON Schema。
5. minimax-m3 读取带页码测试图。
6. minimax-m3 输出 visual_review Schema。
7. thinking_level=max。

先发送 max。若端点仅明确拒绝 reasoning effort 参数，允许只移除 effort 再试一次，并在 probe 中记录 requested=max、effective=provider_default 和原始错误代码；不得换模型。文本、工具、续轮、JSON 或图片任一能力失败都禁止启动生产服务。

## 13. ZOS-API 探针

使用未来服务账号执行，不使用 LocalSystem。生成 artifacts\zosapi-probe.json，记录：

- 服务账号 SID。
- ZemaxRoot、OpticStudio 目录和实际 DLL 路径。
- CreateNewApplication 结果。
- IsValidLicenseForAPI 和 LicenseStatus。
- 样本文件相对路径和 SHA-256。
- 模式、单位、配置数和 surface 数。
- 关闭时未保存源文件。

优先使用：

~~~text
Documents\Zemax\Samples\Design Applications\Lasers and Fibers\Laser Lenses\2- Singlet.zmx
~~~

用已批准 deployment_policy.json 调用一次 Lens Drawing create。create 返回退出码 2 且任务进入等待几何/输入状态可以证明业务门槛；只有进程错误、无有效 ZOS 许可证、DLL 路径错误或无法读取样本才算 probe 失败。探针结束后关闭 OpticStudio，不修改样本。

## 14. 安装组合产物

最终程序目录：

~~~text
C:\Program Files\LensBot\
  host\
  gateway\dws-lens.exe
  agent\node.exe
  agent\agent-worker.mjs
  agent\agent-worker.manifest.json
  lensdrawing\
  schemas\
  scripts\
~~~

复制构建产物时生成 artifacts\install-manifest.json，记录每个文件的相对路径、大小和 SHA-256。node.exe 使用已验证的 Node 24.15.0 x64 可执行文件。

首期直接安装以上目录并注册服务，不额外构建第二层安装器。这样减少一次打包和路径变换；install-manifest.json 是安装权威清单。

## 15. 注册并启动 Windows 服务

服务名固定 LensBotHost，启动类型 Automatic Delayed Start。服务账号使用已激活 OpticStudio、通过 ZOS probe 且已完成 DWS 授权的固定 Windows 账号，不使用 LocalSystem。

服务二进制：

~~~text
"C:\Program Files\LensBot\host\LensBotHost.exe" service --config "C:\ProgramData\LensBot\config\lensbot.toml"
~~~

服务注册后配置失败恢复：60 秒后重启，连续三次。启动前运行 migrate 和 preflight。只有以下全部通过才 Start-Service：

- deployment policy 已批准且 Lens Drawing 接受。
- DWS auth status 有效。
- model-probe.json 全部通过。
- zosapi-probe.json 全部通过。
- install-manifest.json 自检通过。

启动后运行 health，生成 artifacts\service-status.json。必须包含：

~~~text
service = running
gateway_process = running
gateway_pipe = connected
worker_process = running
worker_handshake = ok
sqlite = writable
lensdrawing_spec = matched
zosapi = ready
main_model = ready
vision_model = ready
dws_stream = connected
production_ready = true
~~~

## 16. 真实端到端验收

在允许列表中的测试群完成：

1. 上传标准 ZMX，同时给出完整镜片命名。确认自动 create、submit、validate、run、视觉检查、ZIP 和摘要。
2. 上传缺少必要命名的 ZMX。确认 Agent 只询问缺失项，回复后继续同一个 task_id。
3. 使用一份含零厚度重复胶合界面的代表 ZMX。确认 geometry_cases.json 出现候选，Agent 只选择 candidate ID，并正确保留两侧 AD 和各镜片 MD。
4. 使用一份含 Automatic MEMA 或其他中等置信口径的 ZMX。确认 resolve 后进入 needs_clarification；无 confirmation_id 证据时 validate 失败，DWS 回复后同一任务继续。
5. 发送 #状态、#取消、#重试，确认不产生模型调用。
6. ZIP 发送成功后重启 LensBotHost，确认不重复发送 ZIP。
7. 重启电脑，确认服务、Stream 和队列自动恢复，再完成一个新任务。

数值验收：

- 所有 Zemax 半径型 AD/MD 值乘 2 后换算为毫米。
- MD 只来自 MEMA/MechanicalSemiDiameter。
- 每片 MD >= 自己的 AD_left 和 AD_right。
- 虚拟胶合面仅在零厚度、面型/曲率一致且无 tilt/decenter 时折叠。
- 标准和虚拟界面任务的 Glass/T/R/MD/AD 与 ZOS-API 证据一致，误差不超过 1e-6 mm。

视觉只负责版面和可读性。确定性 PDF 校验失败时不得调用视觉结果覆盖失败。

把两条群消息 ID、任务号、输出 ZIP SHA-256、provider_key、重启前后 ledger 状态和关键断言写入 artifacts\e2e-success.json，不写完整聊天正文。

## 17. 完成证据

C:\ProgramData\LensBot\artifacts 最终至少包含：

~~~text
package-verification.json
preflight.json
lensdrawing-spec.json
build-manifest.json
model-probe.json
zosapi-probe.json
test-summary.txt
install-manifest.json
service-status.json
e2e-success.json
deployment-journal.jsonl
~~~

最后执行一次完成审计：

1. 重新校验 install-manifest.json。
2. 重新运行 Lens Drawing spec。
3. 重新运行 Host health。
4. 查询 SQLite，确认没有未知 running/delivering 状态。
5. 确认 e2e-success.json 同时证明自动交付、澄清续接、机器人文件身份和防重复投递。
6. 确认 production_ready=true。

只有全部成立才报告部署完成。报告只给出安装版本、服务状态、探针结论、E2E 任务号、证据目录和仍存在的明确产品范围，不输出任何密钥。
