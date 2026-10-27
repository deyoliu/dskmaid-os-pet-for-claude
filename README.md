# dskmaid-os-pet-for-claude

陪你寫程式的鯨魚娘女僕桌寵。她跟著 **Claude Code**（也支援 Codex）的工作狀態動作，還會在頭上冒出一句 DeepSeek 風格的「內心 OS」吐槽，例如「這使用者說了個啥啊…」。

<p align="center">
  <img src="previews/demo.webp" width="438" alt="示範：收到指令、冒出吐槽、工作、做完、被點、被拖">
</p>

- 50 段透明背景、每秒 24 格的動畫，切換動作時淡入淡出。
- 你下指令時她變出書桌開始工作；跑很久會看錶，工具出錯會被打擾，做完伸懶腰比耶，被中斷會生氣。
- 沒事時哼歌、喝奶茶、掃地、吐泡泡；沒人理她會打瞌睡、抱枕頭睡著。可以點她、拖她、餵小魚乾。
- 在 Claude Code 裡輸入 `/dskmaid` 就能叫出來或收起來。

> 只支援 **Windows 10/11**。這是非官方同人作品，與 DeepSeek、Anthropic 無關，也未獲背書。

## 需要什麼

- Windows 10 或 11
- Python 3.10 以上，`python` 要在 PATH 裡
- 已登入的 [Claude Code](https://claude.com/claude-code) CLI（`claude`）
  - 吐槽是用 `claude -p --model haiku` 產生的，會用到你的 Claude 額度。
  - 每則訊息最多呼叫一次，右鍵選單可以關掉。

## 安裝

```powershell
git clone https://github.com/deyoliu/dskmaid-os-pet-for-claude.git
cd dskmaid-os-pet-for-claude

python -m pip install "PySide6>=6.7" numpy          # 視窗與影像（也可以先建 venv 再裝）
python -m pip install --target .deps "av>=19,<20"   # 影片解碼器 PyAV，裝在專案裡的 .deps/
python tools/fetch_anim.py                 # 下載 50 段動畫（約 26MB），逐一驗證檔案大小與雜湊值
python install_command.py                  # 安裝 Claude Code 的 /dskmaid 指令
```

- `fetch_anim.py` 會從上游固定版本下載動畫，詳見下面的「素材來源與授權」。
- `install_command.py` 只會在 `%USERPROFILE%\.claude\skills\dskmaid` 建一個指向 `claude-plugin\` 的目錄連結（junction），不需要系統管理員權限，也不會改 `settings.json`。

## 開啟與關閉

### 在 Claude Code 裡（推薦）

| 輸入 | 效果 |
|---|---|
| `/dskmaid` | 切換：她不在就叫出來，在就收起來 |
| `/dskmaid on` | 只開啟 |
| `/dskmaid off` | 只關閉 |
| `/dskmaid status` | 看她有沒有在執行 |

- 安裝後新開的 Claude Code 視窗就有這個指令。已經開著的視窗，先輸入 `/reload-plugins`。
- 這個指令由外掛直接執行，不經過模型，不花額度。
- 用指令叫出來的桌寵，關掉 Claude Code 後還會繼續執行；要關就再輸入一次 `/dskmaid`。

### 不透過 Claude Code

- **開啟**：雙擊 `start.bat`，不會有主控台視窗。
- **關閉**：在她身上按右鍵 →「結束」。她不會出現在工作列。
- **用指令列**：`python toggle.py`（切換），也接受 `on`、`off`、`status`。在 Codex 或任何終端機都能用。

### 移除

```powershell
python install_command.py --uninstall   # 只移除 /dskmaid 的連結，專案檔案都還在
```

接著刪掉整個專案資料夾即可。

> 不要手動用遞迴刪除（例如 `rm -r`、`rmdir /s`）去刪 `.claude\skills\dskmaid`。那是連結，遞迴刪除會順著它把專案裡的 `claude-plugin\` 也刪掉。

## 互動

- **單擊**：害羞、開心或生氣。工作中被點會被打擾，然後繼續工作。
- **雙擊**：再看一次上一句吐槽。
- **拖曳**：被拎起來懸空晃腳，放下時蹲一下。
- **右鍵選單**：
  - 餵小魚乾
  - 吐槽開關、毒舌模式開關
  - 顯示位置、漫遊開關
  - 開啟設定檔、開啟紀錄資料夾、重新載入設定
  - 結束

## 吐槽的樣子

只吐槽「使用者」這個人，不複述你在做的事，也不引用你的原話。每句 5～9 字，用詞像大陸年輕網友，但用繁體字。

| 你說 | 她頭上 |
|---|---|
| 為什麼又壞了，昨天明明還可以跑 | 這使用者手殘咋整… |
| 按鈕改回藍色，綠色不好看 | 使用者審美咋整… |
| ? | ??? |
| 好 謝謝 | （不吐槽） |

- **兩種風格**：
  - 溫和（預設）：傻眼、無奈。
  - 毒舌模式：嫌棄、挖苦，但不說髒話。
  - 右鍵選單可以切換。
- 做完、出錯、中斷、跑很久時，會說一句固定台詞，不呼叫模型。
- 吐槽呼叫是隔離的：不存對話紀錄、不載入你的 hooks、不開工具、不連 MCP。
- 送給 Haiku 的只有：你那則訊息的前 600 字、專案名稱，以及她自己最近 3 句吐槽（避免重複）。

## 設定

在專案根目錄建立 `config.json`，只寫想覆蓋的鍵（右鍵 →「開啟設定檔」會幫你建好）：

```json
{
  "generator": { "enabled": true, "style": "toxic" },
  "agents": { "claude": true, "codex": true },
  "pet": { "size": 240, "wander": false, "doze_after_s": 300, "sleep_after_s": 1200 }
}
```

- `style`：`"mild"`（溫和）或 `"toxic"`（毒舌）。
- `persona`：自己寫的整段系統提示。有填就會取代 `style`。
- `size`：動畫邊長（邏輯像素）。
- `wander`：偶爾橫著走一段，離原位最多 300px。
- 完整預設值在 `inner_os_pet/config.py` 的 `DEFAULTS`。

## 運作方式

- **讀取對話紀錄（唯讀）**：
  - Claude Code：`~/.claude/projects/*/*.jsonl`
  - Codex：依序找 `CODEX_HOME`、Orca 的 `%APPDATA%\orca\codex-accounts\*\home\sessions`、`~/.codex/sessions`
  - 啟動前的舊內容不會重播。
- **只跟一個 session**：她跟著最後收到指令的那個 session，泡泡上標示是 Claude 還是 Codex，以及專案名稱。
- **動畫**：執行時用 PyAV 解碼透明 WebM（VP9 alpha）。實測約占單核心 7% CPU、記憶體約 90MB。透明的地方可以直接點到後面的視窗。
- **紀錄**：`logs/` 自動輪替，上限約 1.5MB。

### 在 CLI 也顯示（選配）

把 `display` 設成 `"cli"` 或 `"both"`，並安裝 Claude Code 的 Stop hook。安裝後每輪結束時，Claude Code 會顯示一行「💭 已思考（用時 N 秒）：……」。

```powershell
python install_cli_hook.py              # 加進 ~/.claude/settings.json（會先備份）
python install_cli_hook.py --uninstall
```

## 測試

```powershell
python -m unittest discover -s tests    # 單元測試（播放器測試需要先下載動畫）
python tests/make_demo.py               # 重新產生 previews/demo.webp
python tests/bench_player.py            # 60 秒播放的 fps、CPU、記憶體
python tests/e2e_fake_session.py        # 假 session → 真的呼叫 Haiku（2 次）
```

`/dskmaid` 外掛使用 Claude Code 的 function hooks（`claude-plugin/`），這是搶先體驗中的 API，開發時用的是 Claude Code 2.1.31x。指令沒出現時，執行 `claude plugin list` 看 `dskmaid@skills-dir` 有沒有載入。

## 素材來源與授權

**程式碼**：MIT，見 [LICENSE](LICENSE)。

**動畫素材不在 MIT 範圍內**，也沒有放在這個 repo 裡。`tools/fetch_anim.py` 會從上游固定版本下載並驗證：

- 動畫：[yanzwzz/dsh-whale-girl-pet](https://github.com/yanzwzz/dsh-whale-girl-pet)
  - 下載的是 `assets/thumb/*.webm`，固定在 commit `b5ce53c`。
  - 上游 repo 以 MIT 授權。依其 README，動畫是 AI 生成的。
- 角色的出處鏈：
  - 原創角色「溟月」：上善无形（2025.06），以 CC BY-NC-SA 4.0 開放二創。
  - 女僕裝與 DeepSeek 元素二創：ZipZipPipe（2026.04）。
  - 桌寵動畫：yanzwzz/dsh-whale-girl-pet。
- 因此請把角色與動畫視為 [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hant)：
  - 保留上面的出處。
  - **不可商用**。
  - 二創以相同授權分享。
- `previews/demo.webp` 是用這些動畫錄成的示範，同樣採 CC BY-NC-SA 4.0。
- 檔名對照與下載驗證的細節見 [assets/anim/SOURCE.md](assets/anim/SOURCE.md)。

DeepSeek 及其標識屬於其權利人；Claude 屬於 Anthropic。本專案為非官方同人作品，與上述公司無關。
