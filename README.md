# TFDA 藥品資訊查詢系統 (TFDA Drug Info Search)

[![Live Demo](https://img.shields.io/badge/Live%20Demo-Click%20Here-2563eb?style=flat-square)](https://lianggaga927-beep.github.io/TFDA-drug-info-search/)
[![License](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![HTML5](https://img.shields.io/badge/HTML5-E34F26.svg?style=flat-square&logo=html5&logoColor=white)]()
[![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E.svg?style=flat-square&logo=javascript&logoColor=black)]()
[![Python](https://img.shields.io/badge/Python-3.10+-3776ab.svg?style=flat-square&logo=python&logoColor=white)]()
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-2088FF.svg?style=flat-square&logo=github-actions&logoColor=white)]()

## 系統架構結論

本專案為無伺服器 (Serverless) 之純靜態網頁應用程式 (SPA)。核心機制為透過 GitHub Actions 定期觸發 Python 資料處理腳本，自食藥署 (TFDA) 介接原始開放資料，進行預先清洗 (Pre-fetch & Cleansing) 並生成輕量化靜態 JSON 快取檔。此架構徹底解決了直接於前端請求政府 API 所面臨的 CORS 限制、高網路延遲及瀏覽器記憶體溢出 (OOM) 等物理限制，實現毫秒級的客戶端檢索效能。

## 前端介面特色 (Frontend Features)

針對臨床醫療人員與一般民眾的查詢痛點，本專案前端具備以下工程與體驗優勢：

* 🚀 **毫秒級檢索效能 (In-Memory Search)：** 放棄傳統 API 往返查詢。當使用者載入網頁時，輕量化 JSON 已快取至本地，所有中英文模糊搜尋皆在記憶體內瞬間完成，達成「所打即所得」的零延遲體驗。
* ⚡ **極輕量化 (Zero Dependencies)：** 捨棄沈重的現代化框架 (如 React/Vue)，採用純 Vanilla JS 與原生 DOM 操作實作。核心渲染引擎極小化，顯著降低首次可互動時間 (Time to Interactive, TTI)。
* 📱 **響應式卡片設計 (Responsive UI)：** 採用 Mobile-first 策略開發，將龐雜的仿單資料轉化為結構化的「藥品資訊卡 (Drug Cards)」。無論於行動裝置或護理站桌機，皆能提供清晰的適應症、成分與用法用量閱讀體驗。
* 🔗 **跨資料集深度整合：** 介面層自動將「藥品許可證」、「原廠仿單連結 (PDF)」與「健保給付規定」等跨部會孤島資料進行視覺化綁定，大幅降低臨床藥師或醫師的資訊檢索成本。

## 客觀數據：系統技術棧與模組

| 模組屬性 | 技術實作 | 功能描述 |
| :--- | :--- | :--- |
| **前端展示層 (Frontend)** | HTML5, Vanilla JavaScript, CSS3 | 採事件驅動，依賴本地/CDN 快取之 `drugs_data.json` 進行記憶體內 (In-memory) 模糊搜尋與 DOM 渲染。 |
| **資料擷取層 (ETL)** | Python 3.x (`build_data.py`) | 負責串接 TFDA 開放資料 API (許可證、仿單等)，執行欄位過濾、合併與結構正規化。 |
| **自動化排程 (CI/CD)** | GitHub Actions | 透過 `.github/workflows` 內的 YAML 定義 Cron Job，定期執行 ETL 腳本並將變更自動 Commit 至儲存庫。 |
| **主機與網路 (Hosting)** | GitHub Pages | 負責靜態檔案派發 (CDN)，提供具備高可用性之 HTTPS 存取端點。 |

## 資料處理流程步驟 (Data Pipeline)

系統資料更新遵循以下自動化流程：
1. **排程觸發 (Trigger)：** GitHub Actions 依設定之 Cron 頻率（例如每月）啟動虛擬環境。
2. **依賴安裝 (Setup)：** 讀取 `requirements.txt` 安裝必要之 Python 模組。
3. **資料拉取 (Fetch)：** `build_data.py` 向 TFDA 伺服器發出 HTTP GET 請求，下載原始大型 JSON 資料集。
4. **資料清洗 (Cleanse)：** 移除前端展示無需之冗餘欄位，將資料體積極小化，並建立以「許可證字號」為關聯鍵之整合結構。
5. **靜態生成 (Build)：** 輸出精簡版之 `drugs_data.json` 覆寫原檔案。
6. **版控推播 (Deploy)：** GitHub Actions 自動將更新後的 JSON 檔 Commit 並 Push 至 Main 分支，觸發 GitHub Pages 更新。

## 本地開發與環境建置步驟

若需於本地環境進行除錯或開發，請依循以下步驟：

### 1. 取得專案原始碼

```bash
git clone [https://github.com/lianggaga927-beep/TFDA-drug-info-search.git](https://github.com/lianggaga927-beep/TFDA-drug-info-search.git)
cd TFDA-drug-info-search
```

### 2. 資料處理層開發 (Python)

建議使用虛擬環境隔離依賴套件：

```bash
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
```

### 3. 前端展示層開發 (UI)

因現代瀏覽器對於本地 `file://` 協定存在安全性限制（無法執行 `fetch()` 讀取本地 JSON），必須透過本地伺服器啟動：

```bash
# 使用 Python 內建 HTTP 伺服器
python -m http.server 8000
```

完成後，於瀏覽器造訪 `http://localhost:8000` 即可預覽介面與測試搜尋功能。

## 邏輯漏洞與維護注意事項

* **檔案體積監控：** 雖已進行資料清洗，仍需定期監控 `drugs_data.json` 的檔案大小。若隨時間膨脹超過 10MB，將影響行動裝置網路環境下的首次載入時間 (TTI)。
* **API 端點穩定性：** `build_data.py` 依賴 TFDA 開放資料平台的 URL 結構與 JSON Key 命名。若政府端無預警更動 Schema，將導致 GitHub Actions 構建失敗，需隨時檢視 Action 執行日誌。

