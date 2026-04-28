#!/usr/bin/env python3
"""
藥品資料預處理腳本 build_data.py
=================================
從食藥署與健保署下載四個 API 資料，合併輸出為精簡 JSON。

實際格式（經實測）：
  - FDA 37/39/42：回應為 ZIP 檔，內含一個 JSON 檔
  - NHI：回應為 UTF-8 BOM 開頭的 CSV 檔（不是 JSON！）

依賴：pip install requests
"""

import json
import sys
import os
import time
import io
import csv
import zipfile
from datetime import datetime

try:
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("✗  缺少 requests 套件，請執行：pip install requests", file=sys.stderr)
    sys.exit(1)

# ─── 設定 ───────────────────────────────────────────────────────
API_37  = "https://data.fda.gov.tw/data/opendata/export/37/json"
API_39  = "https://data.fda.gov.tw/data/opendata/export/39/json"
API_42  = "https://data.fda.gov.tw/data/opendata/export/42/json"
API_NHI = "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-E41001-001"
OUTPUT_FILE = "drugs_data.json"
TIMEOUT_SEC = 180
# ────────────────────────────────────────────────────────────────


def download(url: str, label: str, verify: bool = True) -> bytes:
    """單純下載原始 bytes，回傳 (bytes, content_type)"""
    print(f"  ⬇  下載中：{label}")
    if not verify:
        print("     ℹ  停用 SSL 驗證")
    start = time.time()
    resp = requests.get(url, timeout=TIMEOUT_SEC, verify=verify, headers={
        "User-Agent": "TFDA-DrugSearch/1.0",
        "Accept": "*/*",
    })
    resp.raise_for_status()
    raw = resp.content
    ct  = resp.headers.get("Content-Type", "")
    print(f"     ✓  下載完成（{len(raw)/1e6:.1f} MB，{time.time()-start:.1f} 秒，Content-Type={ct}）")
    return raw, ct


def smart_decode(raw: bytes) -> str:
    """BOM 偵測 + 多編碼嘗試，最後剝除殘餘 BOM"""
    if raw[:2] == b'\xff\xfe':
        return raw.decode('utf-16', errors='replace').lstrip('\ufeff')
    if raw[:2] == b'\xfe\xff':
        return raw.decode('utf-16', errors='replace').lstrip('\ufeff')
    if raw[:3] == b'\xef\xbb\xbf':
        return raw[3:].decode('utf-8', errors='replace')
    for enc in ('utf-8', 'big5', 'cp950'):
        try:
            return raw.decode(enc).lstrip('\ufeff')
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode('utf-8', errors='replace').lstrip('\ufeff')


def fetch_fda_json(url: str, label: str) -> list:
    """
    食藥署 API 實際回傳 ZIP 檔，內含一個 JSON 檔。
    解 ZIP → 取第一個 .json → 解析。
    """
    try:
        raw, ct = download(url, label)

        # ZIP magic：50 4b 03 04
        if raw[:4] != b'PK\x03\x04':
            print(f"     ⚠  非 ZIP 格式（前4 bytes={raw[:4].hex()}），改嘗試直接解析 JSON")
            text = smart_decode(raw)
            return json.loads(text)

        # 解 ZIP
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = zf.namelist()
            print(f"     ℹ  ZIP 內含檔案：{names}")
            # 找出 JSON 檔（通常只有一個）
            json_names = [n for n in names if n.lower().endswith('.json')]
            if not json_names:
                # 沒有 .json 副檔名就拿第一個檔案
                json_names = names
            with zf.open(json_names[0]) as f:
                inner = f.read()

        print(f"     ℹ  解壓出檔案：{json_names[0]}（{len(inner)/1e6:.1f} MB）")
        text = smart_decode(inner)
        result = json.loads(text)
        print(f"     ✓  JSON 解析成功，筆數：{len(result):,}")
        return result

    except Exception as e:
        print(f"     ✗  失敗：{e}", file=sys.stderr)
        return []


def fetch_nhi_csv(url: str, label: str) -> list:
    """
    健保署 API 實際回傳 CSV 檔（UTF-8 BOM）。
    解析 CSV → 轉成 list of dict。
    """
    try:
        raw, ct = download(url, label, verify=False)

        text = smart_decode(raw)
        print(f"     ℹ  CSV 文字長度：{len(text):,} 字元")

        # 用 csv.DictReader 解析
        reader = csv.DictReader(io.StringIO(text))
        records = list(reader)
        print(f"     ✓  CSV 解析成功，筆數：{len(records):,}")
        if records:
            print(f"     ℹ  欄位：{list(records[0].keys())[:8]}")
        return records

    except Exception as e:
        print(f"     ✗  失敗：{e}", file=sys.stderr)
        return []


def find_field(row: dict, *candidates: str) -> str:
    """從多個可能的欄位名取值（CSV 欄位名可能含空白或變體）"""
    for key in row.keys():
        key_clean = key.strip()
        for cand in candidates:
            if cand in key_clean:
                return (row[key] or "").strip()
    return ""


def main():
    print("=" * 60)
    print("  藥品資料預處理腳本")
    print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── Step 1：下載 ─────────────────────────────────────────────
    print("\n【Step 1】下載原始資料...")
    raw37   = fetch_fda_json(API_37,  "未註銷藥品許可證資料集（API 37）")
    raw39   = fetch_fda_json(API_39,  "藥品仿單資料集（API 39）")
    raw42   = fetch_fda_json(API_42,  "藥品外觀圖檔資料集（API 42）")
    raw_nhi = fetch_nhi_csv (API_NHI, "健保用藥品項查詢（NHI CSV）")

    print(f"\n  API 37  筆數：{len(raw37):,}")
    print(f"  API 39  筆數：{len(raw39):,}")
    print(f"  API 42  筆數：{len(raw42):,}")
    print(f"  NHI     筆數：{len(raw_nhi):,}")

    if not raw37:
        print("\n⚠  API 37 無資料，無法繼續", file=sys.stderr)
        sys.exit(1)

    # ── 列印第一筆資料以協助對欄位 ─────────────────────────────
    if raw37:
        print(f"\n  📋 API 37 第一筆樣本鍵：{list(raw37[0].keys())[:8]}")
    if raw39:
        print(f"  📋 API 39 第一筆樣本鍵：{list(raw39[0].keys())[:8]}")
    if raw42:
        print(f"  📋 API 42 第一筆樣本鍵：{list(raw42[0].keys())[:8]}")
    if raw_nhi:
        print(f"  📋 NHI    第一筆樣本鍵：{list(raw_nhi[0].keys())[:8]}")

    # ── Step 2：建立索引 ─────────────────────────────────────────
    print("\n【Step 2】建立索引字典...")

    # API 39：仿單
    pkg_dict: dict[str, list] = {}
    for row in raw39:
        lic  = (row.get("許可證字號") or "").strip()
        link = (row.get("仿單檔案連結") or row.get("仿單連結") or row.get("外盒檔案連結") or "").strip()
        if lic and link:
            pkg_dict.setdefault(lic, []).append(link)

    # API 42：外觀圖
    img_dict: dict[str, list] = {}
    for row in raw42:
        lic = (row.get("許可證字號") or "").strip()
        img = (row.get("外觀圖檔連結") or row.get("圖檔名稱") or row.get("圖檔連結") or "").strip()
        if lic and img:
            img_dict.setdefault(lic, []).append(img)

    # NHI：健保給付（CSV 欄位）
    nhi_dict: dict[str, dict] = {}
    for row in raw_nhi:
        lic = find_field(row, "藥品許可證", "許可證")
        if not lic:
            continue
        nhi_dict[lic] = {
            "nhiChapter": find_field(row, "給付規定章節", "給付規定"),
            "nhiLink":    find_field(row, "給付規定連結", "連結"),
        }

    print(f"  仿單索引：{len(pkg_dict):,}  圖檔索引：{len(img_dict):,}  健保索引：{len(nhi_dict):,}")

    # ── Step 3：合併 ─────────────────────────────────────────────
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
            "ingredients":   (drug.get("主成分名稱") or drug.get("成分") or "").strip(),
            "usage":         (drug.get("用法用量") or "").strip(),
            "packageLinks":  pkg_dict.get(lic, []),
            "imageLinks":    img_dict.get(lic, []),
            "nhiChapter":    nhi.get("nhiChapter", ""),
            "nhiLink":       nhi.get("nhiLink", ""),
        })
    print(f"  合併完成：{len(output):,} 筆")

    # ── Step 4：輸出 ────────────────────────────────────────────
    print(f"\n【Step 4】寫入 {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "_meta": {
                "generatedAt":  datetime.now().isoformat(),
                "totalRecords": len(output),
            },
            "data": output,
        }, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  ✓  完成：{OUTPUT_FILE}（{os.path.getsize(OUTPUT_FILE)/1e6:.2f} MB）")

    # ── Step 5：健全性檢查 ──────────────────────────────────────
    print("\n【Step 5】健全性檢查...")
    print(f"  有仿單連結：{sum(1 for d in output if d['packageLinks']):,} 筆")
    print(f"  有外觀圖檔：{sum(1 for d in output if d['imageLinks']):,} 筆")
    print(f"  有健保給付：{sum(1 for d in output if d['nhiChapter']):,} 筆")

    print("\n" + "=" * 60)
    print("  完成！drugs_data.json 已就緒。")
    print("=" * 60)


if __name__ == "__main__":
    main()
