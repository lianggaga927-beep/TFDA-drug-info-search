#!/usr/bin/env python3
"""
藥品資料預處理腳本 build_data.py
=================================
從食藥署與健保署下載四個 API 資料，合併輸出為精簡 JSON。

格式（經實測）：
  - FDA 37/39/42：ZIP 檔，內含一個 JSON 檔
  - NHI：UTF-8 BOM 開頭的 CSV 檔（不是 JSON）

NHI 對應策略：
  健保「藥品代號」結構通常為「[字母前綴][許可證數字][包裝後綴]」，
  例如「BC42374100」對應「衛署藥製字第042374號」。
  本腳本對 NHI 端生成多種可能的子字串鍵，
  FDA 端用許可證數字（保留與去除前導零兩種）查找匹配。

依賴：pip install requests
"""

import json
import sys
import os
import re
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
    print("✗  缺少 requests 套件", file=sys.stderr)
    sys.exit(1)

# ─── 設定 ───────────────────────────────────────────────────────
API_37  = "https://data.fda.gov.tw/data/opendata/export/37/json"
API_39  = "https://data.fda.gov.tw/data/opendata/export/39/json"
API_42  = "https://data.fda.gov.tw/data/opendata/export/42/json"
API_NHI = "https://info.nhi.gov.tw/api/iode0000s01/Dataset?rId=A21030000I-E41001-001"
OUTPUT_FILE = "drugs_data.json"
TIMEOUT_SEC = 180
# ────────────────────────────────────────────────────────────────


# ── 下載與解碼 ──────────────────────────────────────────────────
def download(url, label, verify=True):
    print(f"  ⬇  下載中：{label}")
    if not verify:
        print("     ℹ  停用 SSL 驗證")
    start = time.time()
    resp = requests.get(url, timeout=TIMEOUT_SEC, verify=verify, headers={
        "User-Agent": "TFDA-DrugSearch/1.0", "Accept": "*/*",
    })
    resp.raise_for_status()
    raw = resp.content
    print(f"     ✓  下載完成（{len(raw)/1e6:.1f} MB，{time.time()-start:.1f} 秒）")
    return raw


def smart_decode(raw):
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


def fetch_fda_json(url, label):
    try:
        raw = download(url, label)
        if raw[:4] != b'PK\x03\x04':
            return json.loads(smart_decode(raw))
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            json_names = [n for n in zf.namelist() if n.lower().endswith('.json')] or zf.namelist()
            inner = zf.open(json_names[0]).read()
        print(f"     ℹ  解壓出：{json_names[0]}（{len(inner)/1e6:.1f} MB）")
        result = json.loads(smart_decode(inner))
        print(f"     ✓  解析成功，筆數：{len(result):,}")
        return result
    except Exception as e:
        print(f"     ✗  失敗：{e}", file=sys.stderr)
        return []


def fetch_nhi_csv(url, label):
    try:
        raw = download(url, label, verify=False)
        text = smart_decode(raw)
        records = list(csv.DictReader(io.StringIO(text)))
        print(f"     ✓  CSV 解析成功，筆數：{len(records):,}")
        return records
    except Exception as e:
        print(f"     ✗  失敗：{e}", file=sys.stderr)
        return []


# ── 欄位輔助 ────────────────────────────────────────────────────
def detect_field(rows, *patterns):
    if not rows:
        return None
    keys = [str(k).strip() for k in rows[0].keys()]
    for p in patterns:
        if p in keys:
            return p
    for p in patterns:
        for k in keys:
            if p in k:
                return k
    return None


def show_keys(label, rows):
    if rows:
        keys = list(rows[0].keys())
        print(f"  📋 {label}（{len(keys)} 欄）：")
        for i in range(0, len(keys), 4):
            print(f"       {' | '.join(keys[i:i+4])}")


def show_samples(label, rows, n=3):
    print(f"\n  🔬 {label} 前 {n} 筆樣本：")
    for i, row in enumerate(rows[:n]):
        print(f"     [{i+1}]")
        for k, v in list(row.items())[:8]:
            v_str = str(v)
            v_disp = v_str[:50] + "…" if len(v_str) > 50 else v_str
            print(f"         {k}: {v_disp}")


# ── 許可證匹配核心 ──────────────────────────────────────────────
def fda_lic_to_keys(license_str):
    """衛署藥製字第042374號 → {'042374', '42374'}"""
    m = re.search(r'第(\d+)號', license_str)
    if not m:
        return set()
    n = m.group(1)
    return {n, n.lstrip('0')} - {''}


def nhi_code_to_keys(code):
    """BC42374100 → {'42374', '423741'} 等多種可能截取"""
    if not code:
        return set()
    digits = re.sub(r'^[A-Za-z]+', '', code.strip())
    if not digits.isdigit():
        return set()
    keys = set()
    for n in (5, 6, 7):
        if len(digits) >= n + 2:
            sub = digits[:n]
            keys.add(sub)
            keys.add(sub.lstrip('0'))
    return keys - {''}


# ── 主流程 ──────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print(f"  藥品資料預處理｜{datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 60)

    print("\n【Step 1】下載原始資料...")
    raw37   = fetch_fda_json(API_37,  "未註銷藥品許可證資料集（API 37）")
    raw39   = fetch_fda_json(API_39,  "藥品仿單資料集（API 39）")
    raw42   = fetch_fda_json(API_42,  "藥品外觀圖檔資料集（API 42）")
    raw_nhi = fetch_nhi_csv (API_NHI, "健保用藥品項查詢（NHI CSV）")

    print(f"\n  筆數 → 37:{len(raw37):,}  39:{len(raw39):,}  42:{len(raw42):,}  NHI:{len(raw_nhi):,}")
    if not raw37:
        sys.exit("\n⚠  API 37 無資料")

    # ── 勘查 ────────────────────────────────────────────────────
    print("\n  === 完整欄位清單 ===")
    show_keys("API 37", raw37)
    show_keys("API 39", raw39)
    show_keys("API 42", raw42)
    show_keys("NHI",    raw_nhi)
    if raw_nhi:
        show_samples("NHI", raw_nhi, n=3)

    # ── 欄位映射 ────────────────────────────────────────────────
    print("\n  === 自動欄位映射 ===")
    K37_indication = detect_field(raw37, "適應症")
    K37_ingredient = detect_field(raw37, "主成分", "成分")
    K37_usage      = detect_field(raw37, "用法用量", "用法")
    print(f"  API 37: 適應症={K37_indication}  成分={K37_ingredient}  用法={K37_usage}")

    K39_lic     = detect_field(raw39, "許可證字號")
    K39_package = detect_field(raw39, "仿單圖檔連結", "仿單檔案連結", "仿單連結")
    K39_outer   = detect_field(raw39, "外盒圖檔連結", "外盒連結")
    print(f"  API 39: lic={K39_lic}  仿單={K39_package}  外盒={K39_outer}")

    K42_lic   = detect_field(raw42, "許可證字號")
    K42_image = detect_field(raw42, "外觀圖檔連結", "圖檔連結", "圖檔名稱")
    print(f"  API 42: lic={K42_lic}  圖檔={K42_image}")

    KN_drugcode = detect_field(raw_nhi, "藥品代號", "藥品代碼")
    KN_chapter  = detect_field(raw_nhi, "給付規定章節", "給付規定")
    KN_link     = detect_field(raw_nhi, "給付規定章節連結", "給付規定連結")
    KN_drugurl  = detect_field(raw_nhi, "藥品代碼超連結", "藥品代號超連結")
    print(f"  NHI: 代號={KN_drugcode}  章節={KN_chapter}  章節連結={KN_link}  代碼超連結={KN_drugurl}")

    # ── Step 2：建立索引 ────────────────────────────────────────
    print("\n【Step 2】建立索引字典...")

    # 仿單（含外盒）
    pkg_dict = {}
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

    # 外觀
    img_dict = {}
    if K42_lic and K42_image:
        for row in raw42:
            lic = (row.get(K42_lic) or "").strip()
            img = (row.get(K42_image) or "").strip()
            if lic and img:
                img_dict.setdefault(lic, []).append(img)

    # NHI：以「許可證子字串」為 key 建索引
    nhi_index = {}     # licnum_key -> nhi_record
    nhi_with_chapter_count = 0
    if KN_drugcode:
        for row in raw_nhi:
            code = (row.get(KN_drugcode) or "").strip()
            keys = nhi_code_to_keys(code)
            if not keys:
                continue
            chapter = (row.get(KN_chapter) or "").strip() if KN_chapter else ""
            link    = (row.get(KN_link)    or "").strip() if KN_link    else ""
            drugurl = (row.get(KN_drugurl) or "").strip() if KN_drugurl else ""
            payload = {
                "nhiChapter":  chapter,
                "nhiLink":     link,
                "nhiDrugCode": code,
                "nhiDrugUrl":  drugurl,
            }
            if chapter:
                nhi_with_chapter_count += 1
            for k in keys:
                # 同 key 多筆時，優先保留有 chapter 的
                if k not in nhi_index or (chapter and not nhi_index[k]["nhiChapter"]):
                    nhi_index[k] = payload

    print(f"  仿單索引：{len(pkg_dict):,}")
    print(f"  圖檔索引：{len(img_dict):,}")
    print(f"  健保索引鍵值：{len(nhi_index):,}（NHI 含給付規定原始筆數 {nhi_with_chapter_count:,}）")

    # ── Step 3：合併 ────────────────────────────────────────────
    print("\n【Step 3】合併資料...")
    output = []
    matched = 0
    for drug in raw37:
        lic = (drug.get("許可證字號") or "").strip()
        if not lic:
            continue
        # 嘗試所有可能 key 找 NHI
        nhi = {}
        for k in fda_lic_to_keys(lic):
            if k in nhi_index:
                nhi = nhi_index[k]
                break
        if nhi.get("nhiChapter"):
            matched += 1

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
            "nhiDrugCode":   nhi.get("nhiDrugCode", ""),
            "nhiDrugUrl":    nhi.get("nhiDrugUrl", ""),
        })
    print(f"  合併完成：{len(output):,} 筆，健保有規定匹配：{matched:,} 筆")

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
    print(f"  ✓  完成（{os.path.getsize(OUTPUT_FILE)/1e6:.2f} MB）")

    # ── Step 5：健全性檢查 ──────────────────────────────────────
    print("\n【Step 5】健全性檢查...")
    print(f"  有適應症　：{sum(1 for d in output if d['indication']):,}")
    print(f"  有成分　　：{sum(1 for d in output if d['ingredients']):,}")
    print(f"  有用法　　：{sum(1 for d in output if d['usage']):,}")
    print(f"  有仿單連結：{sum(1 for d in output if d['packageLinks']):,}")
    print(f"  有外觀圖檔：{sum(1 for d in output if d['imageLinks']):,}")
    print(f"  有健保給付：{sum(1 for d in output if d['nhiChapter']):,}")

    samples = [d for d in output if d['nhiChapter']][:3]
    if samples:
        print("\n  🔬 健保匹配範例：")
        for d in samples:
            print(f"     {d['licenseNumber']} ↔ NHI代號 {d['nhiDrugCode']}")
            print(f"        規定：{d['nhiChapter'][:60]}…")

    print("\n" + "=" * 60)
    print("  完成！drugs_data.json 已就緒。")
    print("=" * 60)


if __name__ == "__main__":
    main()