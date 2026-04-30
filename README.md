# jump-desktop-ime-bridge

讓 Jump Desktop 全螢幕模式下,本地 macOS 與遠端 Windows 的輸入法狀態同步。

## 為什麼需要這個

用 [Jump Desktop](https://jumpdesktop.com/) 從 Mac 連 Windows 工作時,中英輸入法切換常常脫鉤:

- 在遠端按 Caps Lock,**只切換遠端 Windows IME**,本地 macOS IME 沒動。
- 結果離開 Jump Desktop 視窗回到本地 app,IME 狀態錯亂,要再手動切。
- Jump Desktop 為了避免按鍵被攔截,啟用了 macOS 的 Secure Keyboard Entry,
  一般的 NSEvent 監聽方法收不到鍵盤事件。

這個腳本用 CGEventTap (session 層) 攔截 F19,並用 Carbon TIS API 直接修改 macOS 輸入源 — 兩者都繞過 Secure Keyboard Entry,所以即使在 Jump Desktop 全螢幕中也能運作。

## 運作原理

```
Caps Lock
  └─► [Karabiner] 轉成 F19
        └─► [CGEventTap] 攔截 F19 keydown
              ├─► 若前景是 Jump Desktop:
              │     ├─► 放行 F19 給 Jump Desktop ─► 遠端 Windows 收到 ─► 切遠端 IME
              │     └─► Carbon TIS 強制切本地 macOS IME (ABC ↔ 注音)
              └─► 若前景不是 Jump Desktop:
                    └─► 完全放行, 不干涉
```

關鍵設計:

- **每次都讀真實 IME 狀態**才決定切去哪,不維護本地 bool — 否則遠端切了本地不知道,下次會反向。
- **EventTap callback 必須極快**,實際的 `TISSelectInputSource` 在另一個 thread 跑,callback 本身只判斷 keycode + 前景 app + 丟個 thread。
- **正確處理 EventTap timeout** (`kCGEventTapDisabledByTimeout` / `kCGEventTapDisabledByUserInput`),callback 會自動重啟自己。

## 前置需求

1. macOS (在 12+ 測試,理論上 10.15+ 都行)
2. [Karabiner-Elements](https://karabiner-elements.pqrs.org/) — 用來把 Caps Lock 改成 F19
3. Python 3.9+
4. macOS 系統設定 > 鍵盤 > 輸入法 已加入:
   - ABC (`com.apple.keylayout.ABC`)
   - 注音 (`com.apple.inputmethod.TCIM.Zhuyin`)

### Karabiner 設定

`~/.config/karabiner/karabiner.json` 的 `complex_modifications` 加一條:

```json
{
  "description": "Caps Lock → F19",
  "manipulators": [
    {
      "type": "basic",
      "from": { "key_code": "caps_lock" },
      "to": [{ "key_code": "f19" }]
    }
  ]
}
```

或直接從 [Karabiner Complex Modifications 庫](https://ke-complex-modifications.pqrs.org/) 找現成 rule。

## 安裝

```bash
git clone https://github.com/tzupingchenwork/jump-desktop-ime-bridge.git ~/jump-desktop-ime-bridge
cd ~/jump-desktop-ime-bridge
pip3 install -r requirements.txt
```

### 一次性測試

```bash
python3 jump_desktop_ime_bridge.py
```

第一次跑會被權限擋,看下一節。

### 設為登入啟動 (LaunchAgent)

```bash
chmod +x install.sh
./install.sh
tail -f ~/Library/Logs/jump-desktop-ime-bridge.log
```

其他指令:

```bash
./install.sh --status      查看狀態
./install.sh --uninstall   解除安裝
```

## 權限設定 ⚠️

腳本需要兩個權限,**不要用 sudo 跑**(sudo 會把權限解析成 root,跟你個人帳號無關):

- **輔助使用 (Accessibility)** — CGEventTap 監聽鍵盤事件
- **輸入監控 (Input Monitoring)** — 同上

到「系統設定 > 隱私權與安全性」,在這兩個清單裡把以下加入並啟用:

- 你跑 `python3 jump_desktop_ime_bridge.py` 的 Terminal/iTerm,**或者**
- LaunchAgent 模式下,實際的 `python3` binary
  (路徑會在 `install.sh` 輸出裡顯示,通常是 `/opt/homebrew/bin/python3` 或 `/usr/local/bin/python3`)

第一次跑 macOS 通常會跳對話框,沒跳就手動拖檔進去。

授權後重啟腳本:

```bash
./install.sh --uninstall && ./install.sh
```

## 設定

開啟 [jump_desktop_ime_bridge.py](jump_desktop_ime_bridge.py) 改最上面的常數:

```python
TARGET_BUNDLE_ID = "com.p5sys.jump.mac.viewer"   # 目標 app
F19_KEYCODE = 80                                  # 觸發鍵
ABC_INPUT_SOURCE = "com.apple.keylayout.ABC"
ZHUYIN_INPUT_SOURCE = "com.apple.inputmethod.TCIM.Zhuyin"
```

要切倉頡或別的 IME,改 `ZHUYIN_INPUT_SOURCE`:

| IME | Source ID |
|-----|-----------|
| 注音 | `com.apple.inputmethod.TCIM.Zhuyin` |
| 倉頡 | `com.apple.inputmethod.TCIM.Cangjie` |
| 簡體拼音 | `com.apple.inputmethod.SCIM.ITABC` |
| 日文羅馬字 | `com.apple.inputmethod.Kotoeri.RomajiTyping.Japanese` |

要查當前所有可用的 ID:

```bash
defaults read com.apple.HIToolbox AppleEnabledInputSources
```

## 疑難排解

### 按 F19 沒反應

按優先序檢查:

1. `tail -f ~/Library/Logs/jump-desktop-ime-bridge.log` 有看到 `EventTap 已啟用` 嗎?沒有就是權限問題,看下一條。
2. 在非 Jump Desktop 視窗按 F19,log 應該完全安靜。在 Jump Desktop 視窗按應該看到 `本地 IME -> ...`。如果兩者都沒反應 → Karabiner 沒把 Caps Lock 轉成 F19。用 [Key Codes app](https://github.com/manytypos/key-codes) 確認按下時收到的 keycode 是 80。
3. log 有印切換訊息但本地 IME 沒動 → 注音/ABC 沒在系統啟用,腳本啟動時會擋住才對,如果還是這樣請開 issue。

### `CGEventTapCreate 失敗`

權限沒給,看上面權限設定那節。LaunchAgent 模式下要授權給 binary 本身 (`python3`),不是 Terminal。

### EventTap 一直被 disable

通常是 callback 跑太慢觸發 timeout。本腳本已把 IME 切換丟到 thread,callback 主路徑只有 keycode + frontmost app 判斷,理論上不會 timeout。如果你的 Mac 有大量背景負載仍會偶發,腳本會自動重啟 tap (log 會印 `EventTap 被系統暫停, 重新啟用`),不影響使用。

### Jump Desktop 收不到 F19

理論上 CGEventTap 在 session 層,Secure Keyboard Entry 攔不住。如果你的 Jump Desktop 版本特別嚴格,fallback 方案是改用 Karabiner 直接觸發 shell command (跳過 EventTap)。歡迎 PR。

## 授權

MIT License — 自由使用、修改、再散布,作者免責。
