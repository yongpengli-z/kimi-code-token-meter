---
name: token-meter
description: 当用户要求打开 Token Meter、查看 token 速度或缓存命中率，或说“打开看板”时，在 Kimi Code App 内置浏览器中打开本地实时看板。
---

当用户要求打开 token 速度或缓存命中率看板时，按以下步骤操作：

1. 用 Bash 执行以下命令。命令会启动或复用本地看板服务，并只输出一个 `http://127.0.0.1` URL：

   ```sh
   python3 "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/kimi-code-token-meter/scripts/token_meter.py" --launch
   ```

   如果系统没有 `python3` 但有 `python`，用 `python` 执行同一脚本。

2. 加载并使用 Kimi Code Desktop 内置浏览器工具 `mcp__desktop_browser__run`。用 `browser.create_tab` 打开上一步返回的 URL，再用 `browser.switch_tab` 显示该标签页。不要改用系统浏览器。
3. 简短告诉用户看板已打开。速度是最近完成请求的平均输出 token/s，缓存命中率是当前会话累计值。
4. 如果启动失败，报告命令的实际错误和日志路径，不要假装看板已经打开。

不要在用户没有要求时自动打开看板。
