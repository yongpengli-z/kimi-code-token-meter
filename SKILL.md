---
name: token-meter
description: 当用户要求打开 Token Meter、查看 token 速度或缓存命中率，或说“打开看板”“打开悬浮窗”时，打开本地指标视图。
---

根据用户选择打开对应视图，不要在用户没有要求时自动打开。

## 打开原生悬浮窗

当用户说“打开悬浮窗”“显示浮窗”或要求常驻置顶指标时：

1. 用 Bash 执行：

   ```sh
   bash "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/open_floating.sh"
   ```

   如果用户要求直接展开请求明细，在命令末尾加 `--details`。脚本会在首次或源码更新时编译原生 macOS 应用，然后打开悬浮窗。
2. 成功后简短告诉用户悬浮窗已打开；它会自动跟随最近活跃会话。失败时报告实际编译或启动错误，不要改用内置浏览器冒充成功。

## 同时打开看板和悬浮窗（默认）

当用户只说“打开看板”、点击本 Skill，或没有指定只要某一种视图时，同时打开内置浏览器和原生悬浮窗：

1. 用 Bash 执行以下命令，启动或复用本地看板服务，并取得一个 `http://127.0.0.1` URL：

   ```sh
   python3 "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/token_meter.py" --launch
   ```

   如果系统没有 `python3` 但有 `python`，用 `python` 执行同一脚本。

2. 用 Bash 执行以下命令，启动原生悬浮窗：

   ```sh
   bash "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/open_floating.sh"
   ```

3. 加载并使用 Kimi Code Desktop 内置浏览器工具 `mcp__desktop_browser__run`。用 `browser.create_tab` 打开第一步返回的 URL，再用 `browser.switch_tab` 显示该标签页。不要改用系统浏览器。
4. 简短告诉用户浏览器看板和悬浮窗都已打开，关闭浏览器不会影响悬浮窗。速度是最近完成请求的平均输出 token/s，缓存命中率是当前会话累计值。
5. 如果任一步骤失败，报告实际错误和日志路径，不要假装两个视图都已打开。若用户明确要求只打开浏览器，才跳过第 2 步。
