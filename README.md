# AutoTrade

台股記憶體產業的資訊監測與 AI 摘要系統：從多個管道收集新聞、公告與數據，
比對關鍵字、用 AI 整理重點，再用 Email 通知。

目前進度：**第 3 階段（數據線：現貨價、三大法人、融資融券、月營收）**

## 開發階段

| 階段 | 內容 | 狀態 |
|---|---|---|
| 0 | 專案骨架：設定檔、log、資料庫、命令列 | ✅ |
| 1 | 最小完整流程：Google News RSS → 關鍵字比對 → 去重 → Email | ✅ |
| 2 | 加入其他來源：公開資訊觀測站、TrendForce、PTT；來源異常提醒 | ✅ |
| 3 | 數據線：現貨價、三大法人、融資融券、月營收；異動提醒 | ✅ |
| 4 | AI 分析：同事件聚合、結構化摘要 | ⬜ |
| 5 | 通知分級：每日摘要、即時警示 | ⬜ |
| 6 | 部署與自動排程 | ⬜ |

## 安裝

需要 Python 3.10 以上，建議用 [uv](https://docs.astral.sh/uv/) 管理環境：

```bash
# 安裝 uv（只需一次）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 在專案資料夾內安裝相依套件（會自動建立 .venv）
uv sync

# 建立自己的 .env（密碼與 API key 放這裡，不會上傳到 GitHub）
cp .env.example .env
```

不用 uv 的話：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e . pytest
```

## 使用

```bash
uv run autotrade --help          # 查看所有指令
uv run autotrade check-config    # 檢查設定檔與 .env
uv run autotrade init-db         # 建立資料庫（data/autotrade.db）
uv run autotrade test-email      # 寄一封測試信，確認 Gmail 設定
uv run autotrade check-sources   # 實際抓取每個來源並顯示結果（不存檔、不寄信）
uv run autotrade check-sources --only mops ptt   # 只檢查指定來源
uv run autotrade run --dry-run   # 試跑：抓新聞並存檔，只在畫面顯示信件內容，不寄信
uv run autotrade run             # 正式執行：抓新聞並寄出彙整信
uv run autotrade backfill --days 30   # 回補過去 30 天的三大法人與融資融券（約 4 分鐘）
uv run autotrade data 2408       # 查個股最近 20 個交易日的法人、融資動向與最新月營收（也可輸入名稱）
uv run autotrade data prices     # 查記憶體現貨價
uv run pytest                    # 執行測試
```

### 每天自動執行（macOS／Linux）

```bash
uv run autotrade schedule install                        # 預設每天 07:30、21:30
uv run autotrade schedule install --times 08:00 21:30    # 自訂時間（重新安裝即可修改）
uv run autotrade schedule run-now                        # 立刻在背景執行一次，測試用
uv run autotrade schedule status                         # 查看目前排程
uv run autotrade schedule uninstall                      # 移除排程
```

- 指令會自動判斷系統：macOS 用 launchd，Linux 用 systemd timer，兩者都沒有時會印出 cron 設定範例。
- **macOS**：排定時間電腦在睡眠的話，醒來後會補跑一次；關機則會跳過。執行紀錄在 `logs/launchd.log`。
- **Linux**：關機錯過的排程，開機後會補跑一次。執行紀錄在 `logs/schedule.log`。
  使用者層級的 systemd 預設只在你登入時運作；主機平常沒人登入的話，
  需要執行 `sudo loginctl enable-linger <帳號>`（install 時會提醒）。
- 排程時間依電腦本身的時區，請確認是 Asia/Taipei（`timedatectl` 可查看）。
- 換電腦執行時，記得先移除舊電腦上的排程，避免兩邊都寄信。
- macOS 專案放在「文件」資料夾時，第一次背景執行可能會詢問是否允許存取，請按允許。
  如果紀錄裡出現 `Operation not permitted`，到「系統設定 → 隱私權與安全性 → 完整磁碟取用權限」
  加入 `.venv/bin/python` 指向的 Python，或把專案移到「文件」以外的資料夾。

### 資料來源

| 名稱 | 內容 | 說明 |
|---|---|---|
| `mops` | 公開資訊觀測站重大訊息 | 證交所與櫃買中心 OpenAPI，只保留觀察標的；信中附上說明摘要 |
| `trendforce` | TrendForce（集邦）報導 | 官方 RSS 自 2026/07 停止更新，改用 Google News 搜尋中文媒體轉載的集邦報導 |
| `google_news` | 各大媒體新聞 | Google News RSS 搜尋 |
| `ptt` | PTT Stock 板 | 每個查詞搜尋一次，只保留 3 天內的文章 |

每個來源都可以在 `config/settings.yaml` 的 `sources` 裡關閉或調整查詞。
同一篇報導被多個來源抓到時，只會記錄一次（記在順序較前的來源）。

某個來源**連續失敗 3 次**時會寄信提醒（只在剛達到時提醒一次），
通常代表網站改版或暫時無法連線，可以用 `check-sources` 檢查。

### 數據線

| 名稱 | 內容 | 來源 | 提醒規則（預設） |
|---|---|---|---|
| `prices` | 記憶體現貨價 | DRAMeXchange 首頁（DRAM 每日、NAND 晶圓等每週） | 與約 7 天前相比漲跌超過 5% |
| `flows` | 三大法人買賣超 | 證交所、櫃買中心官網 API | 外資連續 5 日買超或賣超（同一段只提醒一次） |
| `margin` | 融資融券餘額 | 證交所、櫃買中心官網 API | 融資餘額單日增加超過 10%（前日至少 1000 張） |
| `revenue` | 月營收 | 證交所、櫃買中心 OpenAPI | 新月份公布就通知，附上公司說明 |

- 規則門檻都在 `config/settings.yaml` 的 `data.rules` 調整。
- 只對最近 3 天內的數據提醒，回補歷史資料時不會一次冒出大量舊提醒。
- 每次執行會補抓最近 7 天缺少的交易日；假日會記錄為休市，不會一直重抓，也不算失敗。
- 三大法人約下午 3～4 點半公布，融資融券約晚上 9 點後公布，當天還沒公布的下次執行會再抓。
- 證交所對短時間大量請求會暫時封鎖，所以請求之間間隔 2.5 秒。
- 數字的明顯變化會出現在信件的「📊 數據異動」；現貨價下跌屬於風險訊號，會排在最前面。

### run 的運作方式

1. 依序抓取所有啟用的來源；某個來源失敗不會影響其他來源。
2. 標題或摘要命中產業關鍵字或台股觀察標的才保留；國際公司（如三星）
   單獨出現不算，要搭配產業關鍵字，避免收到手機新聞。
3. 同一個網址只會存一次，也只會通知一次。
4. 把最近 72 小時內還沒通知過的資料寄成一封信，分成：
   ⚠️ 風險訊號 → 📊 數據異動 → 📢 公司公告 → 📰 新聞 → 💬 PTT 討論。
5. 寄信失敗時資料會保留，下次執行會再寄。`--dry-run` 不會把資料標記為已通知。

## 專案結構

```
config/settings.yaml   觀察標的、關鍵字、路徑等設定（可進版控）
.env                   密碼與 API key（不進版控，從 .env.example 複製）
src/autotrade/
  cli.py               命令列入口
  pipeline.py          一次完整執行的流程
  matching.py          關鍵字與公司名稱比對
  config.py            設定讀取與驗證
  models.py            共用資料格式（Item）
  logging_setup.py     log 設定
  sources/             資料來源：google_news.py、mops.py、ptt.py
  data/                數據線：markets.py（法人、融資、營收）、prices.py（現貨價）、
                       store.py（存取）、rules.py（提醒規則）、collector.py（流程）
  storage/             SQLite 資料庫
  analysis/            AI 分析（第 4 階段）
  notify/              寄信（email.py）與信件排版（digest.py）
tests/                 測試
data/                  資料庫（不進版控）
logs/                  log 檔（不進版控）
```

## 修改觀察標的或關鍵字

直接編輯 `config/settings.yaml`，改完執行 `uv run autotrade check-config` 確認格式正確。
