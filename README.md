# Kimi Code Token Meter

在 **Kimi Code App 内置浏览器**中展示本地 token 用量分析。这是一个非官方社区插件，只提供浏览器看板，不包含原生悬浮窗，也不会修改 Kimi Code 客户端。

## 功能

- 六个汇总卡片：总消耗、输出、加权生成速度、缓存命中率、缓存读取、会话与模型数量。
- 近 100 天每日消耗热力图，悬停可查看当日输出和请求数。
- 时间筛选：今日、昨天、近 7 天、全部和自定义日期，结束日期包含全天。
- 按模型、会话或项目汇总输入、输出、缓存、请求数、平均速度和最近使用时间。
- 浅色与暗色主题，默认每 5 秒自动刷新，也可以手动重新扫描。
- 统计全部非归档本地会话，包含主 Agent 和子 Agent 的 turn 级用量。
- 只读取本地用量元数据，不上传对话、代码或凭据。
- Python 3.9+ 标准库实现，无第三方依赖；服务仅监听 `127.0.0.1`。

## 安装

在 Kimi Code App 中打开 **设置 → 插件 → 安装自定义插件**，粘贴以下地址并点击“安装”：

```text
https://github.com/yongpengli-z/kimi-code-token-meter
```

也可以在会话中执行：

```text
/plugins install https://github.com/yongpengli-z/kimi-code-token-meter
```

安装或更新后执行 `/reload`（也可以新建会话），然后直接对 Kimi 说“打开看板”。如果客户端支持插件斜杠命令，也可以运行 `/kimi-code-token-meter:show`。

当前在 macOS + Kimi Code Desktop 上开发和验证，需要 `python3`（或 `python`）可用。看板不会修改 `config.toml`、`tui.toml` 或客户端应用文件。

## 指标定义

### 总消耗

```text
inputCacheRead + inputCacheCreation + inputOther + output
```

缓存命中率：

```text
inputCacheRead / (inputCacheRead + inputCacheCreation + inputOther) × 100%
```

### 平均生成速度

```text
有请求耗时的记录 output 之和 / 这些记录的耗时之和
```

这是按请求总耗时加权的平均输出 `token/s`，包含从发起请求到请求完成的时间。Kimi Code 本地记录不提供逐 token 流式计数，因此看板不将其标为瞬时解码速度。没有可配对请求时间的记录不参与速度计算。

### 日期与汇总

- 日期按本机时区划分，自定义结束日期包含当天全部记录。
- 每日热力图展示当前筛选结束日期之前的 100 天；选择“全部”时以今天为结束日期。
- 模型、会话和项目表格按总消耗降序排列，默认显示前 20 条，可勾选“显示全部”。
- `state.json` 中 `archived: true` 的会话不参与统计。
- 本地日志没有可靠价格和中断计费信息，因此看板不展示估算花费或中断消耗。

## 数据与隐私

插件从 `$KIMI_CODE_HOME`（默认 `~/.kimi-code`）读取：

- 非归档会话 `agents/*/wire.jsonl` 中的 `llm.request` 和 turn 级 `usage.record`。
- `state.json` 中的会话标题、工作目录和归档标记。

API 只返回请求级聚合指标，不返回提示词、回复、工具输出或代码。运行状态保存在 `$KIMI_CODE_HOME/kimi-code-token-meter/`。连续 15 分钟没有访问后，本地服务自动停止。

## 调试

启动看板服务并输出 URL：

```sh
python3 "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/token_meter.py" --launch
```

直接查看当前会话聚合指标：

```sh
python3 "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/token_meter.py" --metrics
```

本地服务日志：`$KIMI_CODE_HOME/kimi-code-token-meter/server.log`。

## 卸载

```text
/plugins remove kimi-code-token-meter
/reload
```

已启动的看板服务会在闲置 15 分钟后自动退出。

## 声明

本项目与 Moonshot AI 无隶属或背书关系。Kimi 和 Kimi Code 是其各自权利人的商标。
