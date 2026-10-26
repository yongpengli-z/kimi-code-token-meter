# Kimi Code Token Meter

在 **Kimi Code App 的内置浏览器面板**中显示当前会话的 token 输出速度和缓存命中率。这是一个非官方社区插件。

## 功能

- 显示最近完成请求的平均输出速度，单位为 `token/s`。
- 显示当前会话累计缓存命中率，包含主 Agent 和子 Agent。
- 每秒自动刷新；每个看板绑定发起命令的会话，并行窗口互不干扰。
- 只读取本地用量元数据，不上传对话、代码或凭据。
- 仅监听 `127.0.0.1`；连续 15 分钟没有访问后自动停止。
- Python 3.9+ 标准库实现，无第三方依赖。

## 安装

在 Kimi Code App 中打开 **设置 → 插件 → 安装自定义插件**，粘贴以下地址并点击“安装”：

```text
https://github.com/yongpengli-z/kimi-code-token-meter
```

也可以在会话中执行 `/plugins install https://github.com/yongpengli-z/kimi-code-token-meter`。

安装后执行 `/reload`，然后运行：

```text
/kimi-code-token-meter:show
```

插件命令会启动本地服务，并在 App 的内置浏览器面板中打开看板。如果安装后没有立即出现命令，请新建会话后再试。

当前在 macOS + Kimi Code Desktop 上开发和验证；需要 `python3`（或 `python`）可用。它不是 CLI 底部状态栏，也不会修改 `config.toml` 或 `tui.toml`。

## 指标定义

### Token 速度

```text
usage.output / (usage.record.time - 对应的 llm.request.time)
```

这是**最近一次已完成模型请求的平均输出 token/s**，包含从发起请求到请求完成的耗时。Kimi Code 本地记录不提供逐 token 流式计数，因此本插件不将其标为瞬时解码速度。新请求生成期间继续显示最近一次有效结果。

### 缓存命中率

```text
inputCacheRead / (inputCacheRead + inputCacheCreation + inputOther) × 100%
```

统计范围为当前会话中的 turn 级 `usage.record`，包含主 Agent 和全部子 Agent。没有输入数据时显示 `--`。

## 数据与隐私

插件从 `$KIMI_CODE_HOME`（默认 `~/.kimi-code`）读取以下信息：

- hook stdin 中的 `session_id`，用于定位当前会话。
- 当前会话 `agents/*/wire.jsonl` 中的 `llm.request` 和 turn 级 `usage.record`。

页面和 API 只返回聚合指标及 session id，不返回提示词、回复、工具输出或代码。运行状态保存在 `$KIMI_CODE_HOME/kimi-code-token-meter/`，看板服务不对局域网或公网开放。

## 调试

直接查看当前会话聚合指标：

```sh
python3 "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/token_meter.py" --metrics
```

本地服务日志：

```text
$KIMI_CODE_HOME/kimi-code-token-meter/server.log
```

## 卸载

```text
/plugins remove kimi-code-token-meter
/reload
```

已启动的看板服务会在闲置 15 分钟后自动退出。

## 声明

本项目与 Moonshot AI 无隶属或背书关系。Kimi 和 Kimi Code 是其各自权利人的商标。
