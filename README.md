# TFDA 藥品資訊查詢系統 (TFDA Drug Info Search)

## 系統架構結論
本專案為無伺服器 (Serverless) 之純靜態網頁應用程式 (SPA)。核心機制為透過 GitHub Actions 定期觸發 Python 資料處理腳本，自食藥署 (TFDA) 介接原始開放資料，進行預先清洗 (Pre-fetch & Cleansing) 並生成輕量化靜態 JSON 快取檔。此架構徹底解決了直接於前端請求政府 API 所面臨的 CORS 限制、高網路延遲及瀏覽器記憶體溢出 (OOM) 等物理限制，實現毫秒級的客戶端檢索效能。

## 客觀數據：系統技術棧與模組

| 模組屬性 | 技術實作 | 功能描述 |
| :--- | :--- | :--- |
| **前端展示層 (Frontend)** | HTML5, Vanilla JavaScript, CSS3 | 採事件驅動，依賴本地/CDN 快取之 `drugs_data.json` 進行記憶體內 (In-memory) 模糊搜尋與 DOM 渲染。 |
| **資料擷取層 (ETL)** | Python 3.x (`build_data.py`) | 負責串接 TFDA 開放資料 API (許可證、仿單等)，執行欄位過濾、合併與結構正規化。 |
| **自動化排程 (CI/CD)** | GitHub Actions | 透過 `.github/workflows` 內的 YAML 定義 Cron Job，定期執行 ETL 腳本並將變更自動 Commit 至儲存庫。 |
| **主機與網路 (Hosting)** | GitHub Pages | 負責靜態檔案派發 (CDN)，提供具備高可用性之 HTTPS 存取端點。 |

## 資料處理流程步驟 (Data Pipeline)

系統資料更新遵循以下自動化流程：
1. **排程觸發 (Trigger)：** GitHub Actions 依設定之 Cron 頻率（例如每月/每週）啟動虛擬環境。
2. **依賴安裝 (Setup)：** 讀取 `requirements.txt` 安裝必要之 Python 模組。
3. **資料拉取 (Fetch)：** `build_data.py` 向 TFDA 伺服器發出 GET 請求，下載原始大型 JSON 資料集。
4. **資料清洗 (Cleanse)：** 移除前端展示無需之冗餘欄位，將資料體積極小化，並建立以「許可證字號」為關聯鍵之整合結構。
5. **靜態生成 (Build)：** 輸出精簡版之 `drugs_data.json` 覆寫原檔案。
6. **版控推播 (Deploy)：** GitHub Actions 自動將更新後的 JSON 檔 Commit 並 Push 至 Main 分支，觸發 GitHub Pages 更新。

## 本地開發與環境建置步驟

若需於本地環境進行除錯或開發，請依循以下步驟：

### 1. 取得專案原始碼
git clone [https://github.com/lianggaga927-beep/TFDA-drug-info-search.git](https://github.com/lianggaga927-beep/TFDA-drug-info-search.git)

### 2. 資料處理層開發 (Python)
建議使用虛擬環境隔離依賴套件：

# 建立並啟動虛擬環境 (Windows)
python -m venv venv
venv\Scripts\activate

# 建立並啟動虛擬環境 (macOS/Linux)
python3 -m venv venv
source venv/bin/activate

# 安裝依賴套件
pip install -r requirements.txt

# 執行資料更新腳本，生成最新 drugs_data.json
python build_data.py

### 3. 前端展示層開發 (UI)
因瀏覽器對於本地 file:// 協定存在安全性限制（無法執行 fetch() 讀取本地 JSON），必須透過本地伺服器啟動：

# 使用 Python 內建 HTTP 伺服器
python -m http.server 8000
完成後，於瀏覽器造訪 http://localhost:8000 即可預覽介面。

