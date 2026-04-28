#!/usr/bin/env python3
"""
藥品資料預處理腳本 build_data.py
=================================
【首次使用】先安裝依賴套件：
    pip install requests

執行方式：python build_data.py
輸出：./drugs_data.json（精簡合併版，供前端直接載入）
"""

import json, sys, os, time, gzip, zlib
from datetime import datetime

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("✗  缺少 requests 套件，請先執行：pip install requests", file=sys.stderr)
    sys.exit(1)

# ─── 設定區 ──────────────────────────────────────────────────────
API_37  = "https://data.fda.gov.tw/data/opendata/export/37/json"
API_39  = "https://data.fda.gov.tw/data/opendata/export/39/json"
API_42  = "https://data.fda.gov.tw/data/opendata/export/42/json"
API_NHI = "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-E41001-001"
OUTPUT_FILE = "drugs_data.json"
TIMEOUT_SEC = 180
# ─────────────────────────────────────────────────────────────────


def build_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[500,502,503,504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    session.headers.update({"User-Agent": "TFDA-DrugSearch/1.0", "Accept": "application/json, */*"})
    return session

SESSION = build_session()


def smart_decode(raw: bytes) -> tuple[str, str]:
    """BOM 優先偵測編碼，最後剝除殘餘 BOM 字元"""
    if raw[:2] == b'\xff\xfe':
        return raw.decode('utf-16', errors='replace').lstrip('\ufeff'), 'UTF-16 LE'
    if raw[:2] == b'\xfe\xff':
        return raw.decode('utf-16', errors='replace').lstrip('\ufeff'), 'UTF-16 BE'
    if raw[:3] == b'\xef\xbb\xbf':
        return raw[3:].decode('utf-8', errors='replace'), 'UTF-8 BOM'
    for enc in ('utf-8', 'big5', 'cp950'):
        try:
            return raw.decode(enc).lstrip('\ufeff'), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', errors='replace').lstrip('\ufeff'), 'UTF-8 (errors replaced)'


def try_decompress(raw: bytes) -> bytes:
    """偵測並解壓縮 gzip / zlib deflate"""
    if raw[:2] == b'\x1f\x8b':
        out = gzip.decompress(raw)
        print(f"     ℹ  gzip 解壓：{len(raw)/1e6:.1f} → {len(out)/1e6:.1f} MB")
        return out
    if raw[:1] == b'\x78':
        try:
            out = zlib.decompress(raw)
            print(f"     ℹ  zlib 解壓：{len(raw)/1e6:.1f} → {len(out)/1e6:.1f} MB")
            return out
        except zlib.error:
            pass
    return raw


def fetch_json(url: str, label: str) -> list | dict:
    print(f"  ⬇  下載中：{label}")
    is_nhi = "nhi.gov.tw" in url
    if is_nhi:
        print("     ℹ  健保署：停用 SSL 驗證（憑證缺少 Subject Key Identifier）")
    start = time.time()
    try:
        resp = SESSION.get(url, timeout=TIMEOUT_SEC, verify=(not is_nhi))
        resp.raise_for_status()

        raw = resp.content   # requests 已自動 gzip 解壓（若伺服器有設 Content-Encoding）
        elapsed = time.time() - start
        print(f"     ✓  下載完成（{len(raw)/1e6:.1f} MB，{elapsed:.1f} 秒）")
        print(f"     ℹ  Content-Encoding={resp.headers.get('Content-Encoding','—')}  "
              f"Content-Type={resp.headers.get('Content-Type','—')}")
        print(f"     ℹ  前16bytes (hex)：{raw[:16].hex(' ')}")

        # 防呆：若 requests 沒自動解壓，再手動試一次
        raw = try_decompress(raw)

        text, enc = smart_decode(raw)
        print(f"     ℹ  使用編碼：{enc}")

        result = json.loads(text)
        print(f"     ✓  JSON 解析成功，筆數：{len(result) if isinstance(result, list) else type(result).__name__}")
        return result

    except Exception as e:
        print(f"     ✗  失敗：{e}", file=sys.stderr)
        return []


def main():
    print("=" * 60)
    print("  藥品資料預處理腳本")
    print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    print("\n【Step 1】下載原始資料...")
    raw37   = fetch_json(API_37,  "未註銷藥品許可證資料集（API 37）")
    raw39   = fetch_json(API_39,  "藥品仿單資料集（API 39）")
    raw42   = fetch_json(API_42,  "藥品外觀圖檔資料集（API 42）")
    raw_nhi = fetch_json(API_NHI, "健保用藥品項查詢（NHI）")

    print(f"\n  API 37  筆數：{len(raw37):,}")
    print(f"  API 39  筆數：{len(raw39):,}")
    print(f"  API 42  筆數：{len(raw42):,}")
    print(f"  NHI     筆數：{len(raw_nhi):,}")

    if not raw37:
        print("\n⚠  API 37 無資料，請確認網路連線後重試。", file=sys.stderr)
        sys.exit(1)

    print("\n【Step 2】建立索引字典...")
    pkg_dict: dict[str, list] = {}
    for row in raw39:
        lic  = (row.get("許可證字號") or "").strip()
        link = (row.get("仿單連結")   or "").strip()
        if lic and link:
            pkg_dict.setdefault(lic, []).append(link)

    img_dict: dict[str, list] = {}
    for row in raw42:
        lic = (row.get("許可證字號") or "").strip()
        img = (row.get("圖檔名稱") or row.get("圖檔連結") or "").strip()
        if lic and img:
            img_dict.setdefault(lic, []).append(img)

    nhi_dict: dict[str, dict] = {}
    for row in raw_nhi:
        lic = (row.get("藥品許可證") or row.get("許可證字號") or "").strip()
        if not lic:
            continue
        nhi_dict[lic] = {
            "nhiChapter": (row.get("給付規定") or "").strip(),
            "nhiLink":    (row.get("連結") or row.get("給付規定連結") or "").strip(),
        }

    print(f"  仿單索引：{len(pkg_dict):,}  圖檔索引：{len(img_dict):,}  健保索引：{len(nhi_dict):,}")

    print("\n【Step 3】合併資料並精簡欄位...")
    output = []
    for drug in raw37:
        lic = (drug.get("許可證字號") or "").strip()
        if not lic:
            continue
        nhi = nhi_dict.get(lic, {})
        output.append({
            "licenseNumber": lic,
            "chName":        (drug.get("中文品名") or "").strip(),
            "enName":        (drug.get("英文品名") or "").strip(),
            "indication":    (drug.get("適應症")   or "").strip(),
            "ingredients":   (drug.get("成分")     or "").strip(),
            "usage":         (drug.get("用法用量") or "").strip(),
            "packageLinks":  pkg_dict.get(lic, []),
            "imageLinks":    img_dict.get(lic, []),
            "nhiChapter":    nhi.get("nhiChapter", ""),
            "nhiLink":       nhi.get("nhiLink", ""),
        })
    print(f"  合併完成：共 {len(output):,} 筆藥品記錄")

    print(f"\n【Step 4】寫入 {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"_meta": {"generatedAt": datetime.now().isoformat(),
                             "totalRecords": len(output)},
                   "data": output},
                  f, ensure_ascii=False, separators=(",", ":"))
    print(f"  ✓  完成：{OUTPUT_FILE}（{os.path.getsize(OUTPUT_FILE)/1e6:.2f} MB）")

    print("\n【Step 5】健全性檢查...")
    print(f"  有仿單連結：{sum(1 for d in output if d['packageLinks']):,} 筆")
    print(f"  有外觀圖檔：{sum(1 for d in output if d['imageLinks']):,} 筆")
    print(f"  有健保給付：{sum(1 for d in output if d['nhiChapter']):,} 筆")

    print("\n" + "=" * 60)
    print("  完成！請將 drugs_data.json 上傳至 GitHub 儲存庫根目錄。")
    print("=" * 60)


if __name__ == "__main__":
    main()
