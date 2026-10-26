#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "悬浮窗目前仅支持 macOS" >&2
  exit 1
fi

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIMI_HOME="${KIMI_CODE_HOME:-$HOME/.kimi-code}"
APP_DIR="$KIMI_HOME/kimi-code-token-meter/TokenMeter.app"
BINARY="$APP_DIR/Contents/MacOS/TokenMeter"
SWIFT_SOURCE="$PLUGIN_ROOT/native/macos/TokenMeter.swift"
MAIN_SOURCE="$PLUGIN_ROOT/native/macos/main.swift"
LAUNCH_AGENT_SOURCE="$PLUGIN_ROOT/native/macos/LaunchAgent.swift"
INFO_SOURCE="$PLUGIN_ROOT/native/macos/Info.plist"
BUILD_ONLY=0
APP_ARGS=()

for argument in "$@"; do
  case "$argument" in
    --build-only)
      BUILD_ONLY=1
      ;;
    --details)
      APP_ARGS+=("$argument")
      ;;
    *)
      echo "未知参数: $argument" >&2
      exit 2
      ;;
  esac
done

if [[ ! -x "$BINARY" || "$SWIFT_SOURCE" -nt "$BINARY" || "$MAIN_SOURCE" -nt "$BINARY" || "$LAUNCH_AGENT_SOURCE" -nt "$BINARY" || "$INFO_SOURCE" -nt "$APP_DIR/Contents/Info.plist" ]]; then
  if ! command -v swiftc >/dev/null 2>&1; then
    echo "未找到 swiftc，请先安装 Xcode Command Line Tools" >&2
    exit 1
  fi
  mkdir -p "$APP_DIR/Contents/MacOS"
  cp "$INFO_SOURCE" "$APP_DIR/Contents/Info.plist"
  swiftc -O -swift-version 5 \
    -target "$(uname -m)-apple-macos11.0" \
    -framework Cocoa -framework SwiftUI \
    -o "$BINARY" \
    "$MAIN_SOURCE" "$SWIFT_SOURCE" "$LAUNCH_AGENT_SOURCE"
  codesign --force --deep --sign - "$APP_DIR"
fi

if [[ "$BUILD_ONLY" == "1" ]]; then
  printf '%s\n' "$APP_DIR"
  exit 0
fi

if [[ "${#APP_ARGS[@]}" -gt 0 ]] && pgrep -x TokenMeter >/dev/null 2>&1; then
  osascript -e 'tell application "TokenMeter" to quit' >/dev/null 2>&1 || true
  sleep 1
fi

if [[ "${#APP_ARGS[@]}" -gt 0 ]]; then
  open "$APP_DIR" --args --home "$KIMI_HOME" --script "$PLUGIN_ROOT/scripts/token_meter.py" "${APP_ARGS[@]}"
else
  open "$APP_DIR" --args --home "$KIMI_HOME" --script "$PLUGIN_ROOT/scripts/token_meter.py"
fi
