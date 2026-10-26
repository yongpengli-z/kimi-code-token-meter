# Kimi Code Token Meter

在 **Kimi Code App 内置浏览器或 macOS 原生悬浮窗**中显示当前会话的 token 输出速度和缓存命中率。这是一个非官方社区插件。

## 功能

- 显示最近完成请求的平均输出速度，单位为 `token/s`。
- 显示当前会话累计缓存命中率，包含主 Agent 和子 Agent。
- 以表格展示最近 100 次请求的时间、Agent、模型、输入输出、缓存读取、速度和总耗时。
- macOS 原生悬浮窗支持置顶、拖动、紧凑/展开视图和自动跟随最近活跃会话。
- 汇总当前与全部非归档本地会话的消耗，并按会话列出请求数、输出、缓存命中率和最近速度。
- 每秒自动刷新；每个内置浏览器看板绑定发起命令的会话，并行窗口互不干扰。
- 只读取本地用量元数据，不上传对话、代码或凭据。
- 仅监听 `127.0.0.1`；连续 15 分钟没有访问后自动停止。
- Python 3.9+ 标准库实现，无第三方依赖。

## 安装

在 Kimi Code App 中打开 **设置 → 插件 → 安装自定义插件**，粘贴以下地址并点击“安装”：

```text
https://github.com/yongpengli-z/kimi-code-token-meter
```

也可以在会话中执行 `/plugins install https://github.com/yongpengli-z/kimi-code-token-meter`。

安装或更新后执行 `/reload`（也可以新建会话），然后直接对 Kimi 说：

```text
打开看板
```

插件的 sessionStart Skill 会在每个正式会话中提供打开指令，并在 App 的内置浏览器面板中显示看板，不依赖斜杠命令补全列表。

如果客户端支持插件斜杠命令，也可以运行 `/kimi-code-token-meter:show`。部分 Kimi Code App 版本不显示该命令，此时使用上面的自然语言方式。

当前在 macOS + Kimi Code Desktop 上开发和验证；需要 `python3`（或 `python`）可用。它不是 CLI 底部状态栏，也不会修改 `config.toml` 或 `tui.toml`。

## 原生悬浮窗（macOS）

在正式会话中直接对 Kimi 说：

```text
打开悬浮窗
```

首次或源码更新时，插件会用本机 Swift 工具链编译应用，构建位置为 `$KIMI_CODE_HOME/kimi-code-token-meter/TokenMeter.app`，使用 ad-hoc 签名。需要 macOS 11+ 和 Xcode Command Line Tools；缺少 `swiftc` 时可运行 `xcode-select --install` 安装。

- 紧凑视图同时显示当前会话与全部非归档会话的输出消耗、速度和缓存命中率，可拖动。
- 展开视图提供“会话汇总”和“请求明细”两个页签：前者列出全部非归档会话及各自消耗，后者保留当前会话最近 100 条请求。
- 关闭悬浮窗只退出原生应用，不影响本地看板服务或内置浏览器。
- 展开视图提供“登录时启动”开关，默认关闭。开启时写入 `~/Library/LaunchAgents/com.github.yongpengli-z.kimi-code-token-meter.plist`，关闭时删除。

也可以在终端手动运行：

```sh
bash "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/open_floating.sh"
# 直接展开明细
bash "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/open_floating.sh" --details
# 只编译，不打开窗口
bash "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/open_floating.sh" --build-only
```

## 全部会话汇总

“全部会话”统计 `$KIMI_CODE_HOME/sessions` 中所有**非归档**且有 turn 级用量记录的本地会话，包含未归档的历史会话。`state.json` 中 `archived: true` 的会话不参与总计，也不出现在会话明细中。

总计包含会话数、请求数、输入/输出 token 和整体缓存命中率；每个会话的明细包含标题、请求数、输出 token、缓存命中率、最近速度和最近用量时间，按最近用量倒序排列。全局数据增量读取并缓存约 3 秒，不为所有会话保留请求明细。

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

### 请求明细

表格按完成时间倒序展示最近 100 次请求，并区分 `main` 和 `agent-N`：

- 未缓存输入：`inputOther + inputCacheCreation`。
- 缓存读取：`inputCacheRead`。
- 总耗时：`usage.record.time - 对应的 llm.request.time`。
- 速度：输出 token 除以总耗时。

无法配对请求时间的记录，速度和总耗时显示 `--`。现有日志没有可靠计费信息和逐 token 流式时间戳，因此不展示成本或首字时间。

## 数据与隐私

插件从 `$KIMI_CODE_HOME`（默认 `~/.kimi-code`）读取以下信息：

- 最近活跃会话及全部非归档会话 `agents/*/wire.jsonl` 中的 `llm.request` 和 turn 级 `usage.record`，用于计算指标并在打开看板时绑定会话。
- `state.json` 中的会话标题、工作目录和归档标记，用于会话明细展示及排除归档会话。

页面和 API 只返回请求级用量元数据、会话与全局聚合指标、session id、标题和工作目录，不返回提示词、回复、工具输出或代码。运行状态保存在 `$KIMI_CODE_HOME/kimi-code-token-meter/`，看板服务不对局域网或公网开放。

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
