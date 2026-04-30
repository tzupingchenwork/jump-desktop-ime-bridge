#!/bin/bash
# 安裝 / 解除安裝 jump-desktop-ime-bridge LaunchAgent.
#
# 用法:
#   ./install.sh              安裝並啟動 (登入時自動跑)
#   ./install.sh --uninstall  停止並解除安裝
#   ./install.sh --status     顯示狀態
#
# 注意:
#   - 在 Mac 上跑, 不要 sudo.
#   - LaunchAgent 載入後第一次跑會被系統擋, 需手動到 系統設定 > 隱私權與安全性 ,
#     幫「python3」(會被列為 Python.app 或具體路徑) 開「輔助使用」「輸入監控」.

set -euo pipefail

LABEL="com.github.tzupingchenwork.jump-desktop-ime-bridge"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SCRIPT_PATH="$SCRIPT_DIR/jump_desktop_ime_bridge.py"
TEMPLATE="$SCRIPT_DIR/$LABEL.plist.template"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_PATH="$HOME/Library/Logs/jump-desktop-ime-bridge.log"

cmd_status() {
    echo "Label:    $LABEL"
    echo "Plist:    $PLIST_DEST"
    echo "Script:   $SCRIPT_PATH"
    echo "Log:      $LOG_PATH"
    echo
    if [[ -f "$PLIST_DEST" ]]; then
        echo "Plist installed: yes"
    else
        echo "Plist installed: no"
    fi
    if launchctl list 2>/dev/null | grep -q "$LABEL"; then
        echo "Loaded:          yes"
        launchctl list "$LABEL" || true
    else
        echo "Loaded:          no"
    fi
}

cmd_uninstall() {
    if launchctl list 2>/dev/null | grep -q "$LABEL"; then
        echo "Unloading $LABEL ..."
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
    fi
    if [[ -f "$PLIST_DEST" ]]; then
        rm "$PLIST_DEST"
        echo "Removed $PLIST_DEST"
    else
        echo "No plist at $PLIST_DEST (nothing to remove)"
    fi
}

cmd_install() {
    PYTHON3=$(command -v python3 || true)
    if [[ -z "$PYTHON3" ]]; then
        echo "Error: python3 not found in PATH" >&2
        exit 1
    fi

    if [[ ! -f "$SCRIPT_PATH" ]]; then
        echo "Error: $SCRIPT_PATH not found" >&2
        exit 1
    fi

    if [[ ! -f "$TEMPLATE" ]]; then
        echo "Error: $TEMPLATE not found" >&2
        exit 1
    fi

    mkdir -p "$HOME/Library/LaunchAgents"
    mkdir -p "$(dirname "$LOG_PATH")"

    if launchctl list 2>/dev/null | grep -q "$LABEL"; then
        echo "Already loaded, unloading first..."
        launchctl unload "$PLIST_DEST" 2>/dev/null || true
    fi

    sed \
        -e "s|__PYTHON3__|$PYTHON3|g" \
        -e "s|__SCRIPT__|$SCRIPT_PATH|g" \
        -e "s|__LOG__|$LOG_PATH|g" \
        "$TEMPLATE" > "$PLIST_DEST"

    echo "Installed: $PLIST_DEST"
    echo "  python3: $PYTHON3"
    echo "  script:  $SCRIPT_PATH"
    echo "  log:     $LOG_PATH"

    launchctl load "$PLIST_DEST"
    echo
    echo "Loaded. 觀察 log:"
    echo "  tail -f $LOG_PATH"
    echo
    echo "若 log 出現 CGEventTapCreate 失敗, 到 系統設定 > 隱私權與安全性 ,"
    echo "幫 $PYTHON3 開「輔助使用」與「輸入監控」, 然後:"
    echo "  ./install.sh --uninstall && ./install.sh"
}

case "${1:-install}" in
    --uninstall|uninstall)
        cmd_uninstall
        ;;
    --status|status)
        cmd_status
        ;;
    --install|install|"")
        cmd_install
        ;;
    *)
        echo "Usage: $0 [--install | --uninstall | --status]" >&2
        exit 1
        ;;
esac
