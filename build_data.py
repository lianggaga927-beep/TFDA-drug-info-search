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


def download(url: str, label: str, verify: bool = True) -> tuple[bytes, str]:
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
    print(f"     ✓  下載完成（{len(raw)/1e6:.1f} MB，{time.time()-start:.1f} 秒）")
    return raw, ct


def smart_decode(raw: bytes) -> str:
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
    """FDA：ZIP → 取出內部 JSON → 解析"""
    try:
        raw, _ = download(url, label)
        if raw[:4] != b'PK\x03\x04':
            return json.loads(smart_decode(raw))

        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            json_names = [n for n in zf.namelist() if n.lower().endswith('.json')] or zf.namelist()
            with zf.open(json_names[0]) as f:
                inner = f.read()
        print(f"     ℹ  解壓出：{json_names[0]}（{len(inner)/1e6:.1f} MB）")
        result = json.loads(smart_decode(inner))
        print(f"     ✓  解析成功，筆數：{len(result):,}")
        return result
    except Exception as e:
        print(f"     ✗  失敗：{e}", file=sys.stderr)
        return []


def fetch_nhi_csv(url: str, label: str) -> list:
    """NHI：CSV（UTF-8 BOM）→ DictReader"""
    try:
        raw, _ = download(url, label, verify=False)
        text = smart_decode(raw)
        records = list(csv.DictReader(io.StringIO(text)))
        print(f"     ✓  CSV 解析成功，筆數：{len(records):,}")
        return records
    except Exception as e:
        print(f"     ✗  失敗：{e}", file=sys.stderr)
        return []


def detect_field(rows: list, *patterns: str) -> str | None:
    """
    從第一筆資料找出第一個符合的欄位名。
    順序：先精確匹配 → 再 contains 匹配。
    """
    if not rows:
        return None
    keys = [str(k).strip() for k in rows[0].keys()]
    # 精確
    for p in patterns:
        if p in keys:
            return p
    # 模糊（contains）
    for p in patterns:
        for k in keys:
            if p in k:
                return k
    return None


def show_keys(label: str, rows: list):
    if rows:
        print(f"  📋 {label}（{len(rows[0])} 個欄位）：")
        keys = list(rows[0].keys())
        # 每行印 4 個欄位
        for i in range(0, len(keys), 4):
            print(f"       {' | '.join(keys[i:i+4])}")


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
        sys.exit("\n⚠  API 37 無資料，無法繼續")

    # ── 列出完整欄位 ─────────────────────────────────────────────
    print("\n  === 完整欄位清單 ===")
    show_keys("API 37", raw37)
    show_keys("API 39", raw39)
    show_keys("API 42", raw42)
    show_keys("NHI",    raw_nhi)

    # ── 自動偵測欄位映射 ────────────────────────────────────────
    print("\n  === 自動欄位映射 ===")

    # API 37（主表）
    K37_indication = detect_field(raw37, "適應症")
    K37_ingredient = detect_field(raw37, "主成分", "成分")
    K37_usage      = detect_field(raw37, "用法用量", "用法及用量", "用法")
    print(f"  API 37: 適應症={K37_indication}  成分={K37_ingredient}  用法={K37_usage}")

    # API 39（仿單）
    K39_lic     = detect_field(raw39, "許可證字號")
    K39_package = detect_field(raw39, "仿單圖檔連結", "仿單檔案連結", "仿單連結")
    K39_outer   = detect_field(raw39, "外盒圖檔連結", "外盒檔案連結", "外盒連結")
    print(f"  API 39: lic={K39_lic}  仿單={K39_package}  外盒={K39_outer}")

    # API 42（外觀）
    K42_lic   = detect_field(raw42, "許可證字號")
    K42_image = detect_field(raw42, "外觀圖檔連結", "外觀圖檔", "圖檔連結", "圖檔名稱", "圖檔")
    print(f"  API 42: lic={K42_lic}  圖檔={K42_image}")

    # NHI（健保）
    KN_lic     = detect_field(raw_nhi, "藥品許可證", "許可證字號", "許可證", "藥品代號")
    KN_chapter = detect_field(raw_nhi, "給付規定章節", "給付規定")
    KN_link    = detect_field(raw_nhi, "給付規定章節連結", "給付規定連結", "連結")
    print(f"  NHI: lic={KN_lic}  章節={KN_chapter}  連結={KN_link}")

    # ── Step 2：建立索引 ─────────────────────────────────────────
    print("\n【Step 2】建立索引字典...")

    # API 39：仿單（合併仿單圖檔 + 外盒圖檔到 packageLinks）
    pkg_dict: dict[str, list] = {}
    if K39_lic:
        for row in raw39:
            lic = (row.get(K39_lic) or "").strip()
            if not lic:
                continue
            for key in (K39_package, K39_outer):
                if key:
                    link = (row.get(key) or "").strip()
                    if link:
                        pkg_dict.setdefault(lic, []).append(link)

    # API 42：外觀圖檔
    img_dict: dict[str, list] = {}
    if K42_lic and K42_image:
        for row in raw42:
            lic = (row.get(K42_lic) or "").strip()
            img = (row.get(K42_image) or "").strip()
            if lic and img:
                img_dict.setdefault(lic, []).append(img)

    # NHI：健保給付
    nhi_dict: dict[str, dict] = {}
    if KN_lic:
        for row in raw_nhi:
            lic = (row.get(KN_lic) or "").strip()
            if not lic:
                continue
            chapter = (row.get(KN_chapter) or "").strip() if KN_chapter else ""
            link    = (row.get(KN_link)    or "").strip() if KN_link    else ""
            if chapter or link:
                # 同一個許可證可能多筆，保留第一筆有效記錄
                if lic not in nhi_dict:
                    nhi_dict[lic] = {"nhiChapter": chapter, "nhiLink": link}

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
            "indication":    (drug.get(K37_indication) or "").strip() if K37_indication else "",
            "ingredients":   (drug.get(K37_ingredient) or "").strip() if K37_ingredient else "",
            "usage":         (drug.get(K37_usage)      or "").strip() if K37_usage      else "",
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
    print(f"  有適應症　：{sum(1 for d in output if d['indication']):,} 筆")
    print(f"  有成分　　：{sum(1 for d in output if d['ingredients']):,} 筆")
    print(f"  有用法　　：{sum(1 for d in output if d['usage']):,} 筆")
    print(f"  有仿單連結：{sum(1 for d in output if d['packageLinks']):,} 筆")
    print(f"  有外觀圖檔：{sum(1 for d in output if d['imageLinks']):,} 筆")
    print(f"  有健保給付：{sum(1 for d in output if d['nhiChapter']):,} 筆")

    print("\n" + "=" * 60)
    print("  完成！drugs_data.json 已就緒。")
    print("=" * 60)


if __name__ == "__main__":
    main()