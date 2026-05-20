#!/usr/bin/env python3
"""
jump-desktop-ime-bridge
=============
解決遠端桌面軟體 (Jump Desktop / Apple 螢幕共享) 全螢幕模式下,
本地 macOS 與遠端 Windows 的 IME 狀態脫鉤問題.

支援的遠端桌面應用程式:
    - Jump Desktop (com.p5sys.jump.mac.viewer)
    - Apple 螢幕共享 (com.apple.ScreenSharing)

機制:
    1. 使用者用 Karabiner 把 Caps Lock 改成 F19 (keycode 80).
    2. 本腳本透過 CGEventTap 攔截 F19 keydown.
    3. 若當下前景是支援的遠端桌面應用程式, 就:
        a. 放行 F19 (回傳 original event), 讓遠端 Windows 收到並切換遠端 IME.
        b. 同時非同步呼叫 Carbon TIS, 強制切換本地 macOS IME (ABC <-> 注音).
    4. 若前景不是支援的應用程式, 完全不干涉, 原樣放行.

關鍵技術:
    - Carbon TIS (TISSelectInputSource) 直接改 macOS 輸入源, 不走鍵盤事件鏈,
      所以 Jump Desktop 的 Secure Keyboard Entry 攔不住.
    - CGEventTap callback 必須極快, 否則 macOS 自動 disable 它.
      因此 IME 切換用 thread 異步, callback 本身只做最小判斷.
    - 真實 IME 狀態每次都從 TIS 讀, 不維護本地 bool, 避免和遠端不同步.

權限需求 (System Settings > Privacy & Security):
    - Accessibility (輔助使用)
    - Input Monitoring (輸入監控)

    兩個權限都要授權給「執行 python 的程式」(Terminal.app / iTerm / Python.app).
    *** 不要用 sudo 跑 ***. sudo 會把權限解析成 root, 與你個人帳號的授權無關.
    第一次跑時 macOS 會跳授權對話框; 若沒跳, 直接到設定面板把對應 app 拖進去並啟用.

執行:
    pip3 install -r requirements.txt
    python3 jump_desktop_ime_bridge.py

長期常駐:
    建議包成 LaunchAgent (~/Library/LaunchAgents/com.github.tzupingchenwork.jump-desktop-ime-bridge.plist)
    讓它隨登入啟動.
"""

import logging
import signal
import sys
import threading

import objc
from AppKit import NSWorkspace
from CoreFoundation import (
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRunInMode,
    kCFRunLoopCommonModes,
    kCFRunLoopDefaultMode,
)
from Foundation import NSBundle
from Quartz import (
    CFMachPortCreateRunLoopSource,
    CGEventGetIntegerValueField,
    CGEventMaskBit,
    CGEventTapCreate,
    CGEventTapEnable,
    kCGEventKeyDown,
    kCGEventTapDisabledByTimeout,
    kCGEventTapDisabledByUserInput,
    kCGEventTapOptionDefault,
    kCGHeadInsertEventTap,
    kCGKeyboardEventKeycode,
    kCGSessionEventTap,
)

# ===== 設定 =====
TARGET_BUNDLE_IDS = {
    "com.p5sys.jump.mac.viewer",   # Jump Desktop
    "com.apple.ScreenSharing",     # Apple 螢幕共享
}
F19_KEYCODE = 80
ABC_INPUT_SOURCE = "com.apple.keylayout.ABC"
ZHUYIN_INPUT_SOURCE = "com.apple.inputmethod.TCIM.Zhuyin"

# ===== Carbon TIS API binding =====
# pyobjc 預設不綁 Carbon 的 TIS API, 手動從 framework 載入.
# 簽名格式: '@' = id (pointer), 'i' = int32, 'B' = BOOL.
_carbon_bundle = NSBundle.bundleWithPath_(
    "/System/Library/Frameworks/Carbon.framework"
)
objc.loadBundleFunctions(
    _carbon_bundle,
    globals(),
    [
        ("TISCopyCurrentKeyboardInputSource", b"@"),
        ("TISCreateInputSourceList", b"@@B"),
        ("TISSelectInputSource", b"i@"),
        ("TISGetInputSourceProperty", b"@@@"),
    ],
)
objc.loadBundleVariables(
    _carbon_bundle,
    globals(),
    [
        ("kTISPropertyInputSourceID", b"@"),
    ],
)

# ===== Logging =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jump-desktop-ime-bridge")


# ===== IME 控制 =====
def get_current_ime_id():
    src = TISCopyCurrentKeyboardInputSource()
    if src is None:
        return None
    return TISGetInputSourceProperty(src, kTISPropertyInputSourceID)


def find_input_source(source_id):
    sources = TISCreateInputSourceList(None, False)
    if sources is None:
        return None
    for src in sources:
        sid = TISGetInputSourceProperty(src, kTISPropertyInputSourceID)
        if sid == source_id:
            return src
    return None


def select_ime(source_id):
    src = find_input_source(source_id)
    if src is None:
        log.warning("找不到輸入法 (可能未在系統啟用): %s", source_id)
        return False
    status = TISSelectInputSource(src)
    if status != 0:
        log.warning("TISSelectInputSource 失敗: status=%s id=%s", status, source_id)
        return False
    log.info("本地 IME -> %s", source_id)
    return True


def toggle_local_ime():
    # 每次都讀真實狀態, 不維護本地 bool, 避免和遠端不同步.
    current = get_current_ime_id()
    log.debug("當前 IME = %s", current)
    target = ZHUYIN_INPUT_SOURCE if current == ABC_INPUT_SOURCE else ABC_INPUT_SOURCE
    select_ime(target)


def toggle_local_ime_async():
    threading.Thread(target=toggle_local_ime, daemon=True).start()


# ===== 前景 App 偵測 =====
def is_target_app_frontmost():
    front = NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is None:
        return False
    return front.bundleIdentifier() in TARGET_BUNDLE_IDS


# ===== Event Tap =====
_tap_ref = None  # global, 給 timeout 重啟用.


def event_tap_callback(proxy, event_type, event, refcon):
    global _tap_ref

    # macOS 偶爾因 callback 太慢或使用者介入暫停 tap, 自動重啟.
    if event_type in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
        log.warning("EventTap 被系統暫停 (type=%s), 重新啟用", event_type)
        if _tap_ref is not None:
            CGEventTapEnable(_tap_ref, True)
        return event

    if event_type != kCGEventKeyDown:
        return event

    keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
    if keycode != F19_KEYCODE:
        return event

    if not is_target_app_frontmost():
        return event

    toggle_local_ime_async()
    # 一定要回傳 event, Jump Desktop 才收得到 F19 並轉送給遠端 Windows.
    return event


def main():
    global _tap_ref

    log.info("jump-desktop-ime-bridge 啟動")
    log.info("Target Bundles: %s", ", ".join(sorted(TARGET_BUNDLE_IDS)))
    log.info("監聽 keycode: F19 (%d)", F19_KEYCODE)

    if find_input_source(ABC_INPUT_SOURCE) is None:
        log.error(
            "找不到 ABC 輸入源 (%s) — 請至 系統設定 > 鍵盤 > 輸入法 加入",
            ABC_INPUT_SOURCE,
        )
        sys.exit(1)
    if find_input_source(ZHUYIN_INPUT_SOURCE) is None:
        log.error(
            "找不到注音輸入源 (%s) — 請至 系統設定 > 鍵盤 > 輸入法 加入",
            ZHUYIN_INPUT_SOURCE,
        )
        sys.exit(1)

    event_mask = CGEventMaskBit(kCGEventKeyDown)
    tap = CGEventTapCreate(
        kCGSessionEventTap,
        kCGHeadInsertEventTap,
        kCGEventTapOptionDefault,
        event_mask,
        event_tap_callback,
        None,
    )

    if tap is None:
        log.error(
            "CGEventTapCreate 失敗.\n"
            "請至 系統設定 > 隱私權與安全性, 為「執行此 script 的 Terminal/iTerm/Python」啟用:\n"
            "  - 輔助使用 (Accessibility)\n"
            "  - 輸入監控 (Input Monitoring)\n"
            "*** 不要用 sudo. ***"
        )
        sys.exit(1)

    _tap_ref = tap

    rls = CFMachPortCreateRunLoopSource(None, tap, 0)
    CFRunLoopAddSource(CFRunLoopGetCurrent(), rls, kCFRunLoopCommonModes)
    CGEventTapEnable(tap, True)

    log.info(
        "EventTap 已啟用. 在 Jump Desktop 或 Apple 螢幕共享中按 F19 (Caps Lock) 測試. Ctrl-C 結束."
    )

    running = {"v": True}

    def _stop(*_):
        running["v"] = False
        log.info("收到中斷, 結束中...")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # 用 RunInMode + while 取代 CFRunLoopRun, 讓 Python signal handler 能在 1 秒內跑到.
    while running["v"]:
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 1.0, False)

    log.info("Bye")


if __name__ == "__main__":
    main()
