# LensBot 4.1 目标机部署包

先让目标 Windows 电脑上的 Codex 完整读取：

1. TARGET_MACHINE_DEPLOYMENT_EXECUTION.md
2. third_party.lock.json
3. config/ 与 schemas/
4. 根目录 release_manifest.json 和 SHA256.txt

Codex 必须先验证清单和 SHA-256，再开始构建或安装。任何校验失败都应停止，不得用同名文件覆盖或自行补齐。

需要用户提供的输入集中到部署配置阶段一次询问：

- 钉钉机器人/应用标识及 DWS 授权。
- 允许使用机器人的群和内部用户。
- 火山方舟 API Key。
- Windows 服务账号凭据。
- 对 config/deployment_policy.json 中固定加工默认值的一次性批准。

除上述授权和密钥输入外，构建、测试、安装、服务注册、探针和验收均由 Codex 执行。

