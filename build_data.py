#!/usr/bin/env python3
"""
藥品資料預處理腳本 build_data.py
=================================
從食藥署與健保署下載四個 API 資料，合併輸出為精簡 JSON。

格式（經實測）：
  - FDA 37/39/42：ZIP 檔，內含 JSON
  - NHI：UTF-8 BOM 開頭的 CSV

NHI 對應策略（精確版）：
  1. 先以「許可證數字」對「藥品代號去字母前綴後的中段」做匹配
  2. 排除原料藥（製劑原料）不參與健保匹配，避免字軌撞號
  3. 數字匹配後，用「英文名核心字」做二次驗證

依賴：pip install requests
"""

import json, sys, os, re, time, io, csv, zipfile
from datetime import datetime

try:
    import requests, urllib3
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

# 英文劑型/常用詞（比對核心字時排除）
FILLER_WORDS = {
    'TABLET','TABLETS','CAPSULE','CAPSULES','INJECTION','SOLUTION','SUSPENSION',
    'CREAM','OINTMENT','SYRUP','POWDER','GEL','LOTION','SPRAY','DROPS',
    'INHALER','SUPPOSITORY','PATCH','FILM','COATED','ENTERIC','EXTENDED',
    'RELEASE','SUSTAINED','MODIFIED','ORAL','PROLONGED','CONTROLLED',
    'STANDARD','GENERIC','BRAND','NEW','NEO','PLUS','FORTE','MITE','ULTRA',
    'TAB','TABS','CAP','CAPS','INJ','SOLN','SUSP','MICROGRAM','MILLIGRAM',
    'GRAM','UNIT','UNITS','HUNDRED','THOUSAND',
}
# ────────────────────────────────────────────────────────────────


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


# ── 許可證匹配核心 ──────────────────────────────────────────────
def fda_lic_to_keys(license_str):
    m = re.search(r'第(\d+)號', license_str)
    if not m:
        return set()
    n = m.group(1)
    return {n, n.lstrip('0')} - {''}


def nhi_code_to_keys(code):
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


# ── 過濾與驗證邏輯 ──────────────────────────────────────────────
def is_raw_material(drug):
    """判斷是否為原料藥（不應參與健保匹配）"""
    usage   = (drug.get('用法用量') or '').strip()
    licType = (drug.get('許可證種類') or '').strip()
    if '原料' in licType:
        return True
    if '製劑原料' in usage:
        return True
    return False


def core_words(name):
    """提取英文名的核心詞，排除劑型/規格等干擾字"""
    if not name:
        return set()
    words = set(re.findall(r'[A-Z]{4,}', name.upper()))
    return words - FILLER_WORDS


def names_match(fda_en, nhi_en):
    """
    核心字交集驗證：兩個英文名是否「相關」。
    - 雙方都缺資料 → 視為通過（不阻擋）
    - 雙方核心字交集 >= 1 → 通過
    - 否則 → 視為不匹配
    """
    f = core_words(fda_en)
    n = core_words(nhi_en)
    if not f or not n:
        return True  # 資料不足無法判斷，不擋
    return bool(f & n)


# ── 顯示輔助 ────────────────────────────────────────────────────
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

    # ── 欄位映射 ────────────────────────────────────────────────
    print("\n  === 自動欄位映射 ===")
    K37_indication = detect_field(raw37, "適應症")
    K37_ingredient = detect_field(raw37, "主成分", "成分")
    K37_usage      = detect_field(raw37, "用法用量", "用法")
    K37_lictype    = detect_field(raw37, "許可證種類")
    print(f"  API 37: 適應症={K37_indication}  成分={K37_ingredient}  用法={K37_usage}  類別={K37_lictype}")

    K39_lic     = detect_field(raw39, "許可證字號")
    K39_package = detect_field(raw39, "仿單圖檔連結", "仿單檔案連結", "仿單連結")
    K39_outer   = detect_field(raw39, "外盒圖檔連結")
    print(f"  API 39: lic={K39_lic}  仿單={K39_package}  外盒={K39_outer}")

    K42_lic   = detect_field(raw42, "許可證字號")
    K42_image = detect_field(raw42, "外觀圖檔連結", "圖檔連結")
    print(f"  API 42: lic={K42_lic}  圖檔={K42_image}")

    KN_drugcode = detect_field(raw_nhi, "藥品代號")
    KN_chapter  = detect_field(raw_nhi, "給付規定章節")
    KN_link     = detect_field(raw_nhi, "給付規定章節連結")
    KN_drugurl  = detect_field(raw_nhi, "藥品代碼超連結")
    KN_chname   = detect_field(raw_nhi, "藥品中文名稱", "中文品名")
    KN_enname   = detect_field(raw_nhi, "藥品英文名稱", "英文品名")
    print(f"  NHI: 代號={KN_drugcode}  章節={KN_chapter}  英文名={KN_enname}")

    # ── Step 2：建立索引 ────────────────────────────────────────
    print("\n【Step 2】建立索引字典...")

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

    img_dict = {}
    if K42_lic and K42_image:
        for row in raw42:
            lic = (row.get(K42_lic) or "").strip()
            img = (row.get(K42_image) or "").strip()
            if lic and img:
                img_dict.setdefault(lic, []).append(img)

    # NHI：每個 key 可能對到多筆 NHI 候選（同一許可證號可能有多個字軌的健保品項）
    # 改成 list 儲存所有候選，比對時再用英文名驗證選對的
    nhi_index = {}  # key -> [候選清單]
    nhi_with_chapter = 0
    if KN_drugcode:
        for row in raw_nhi:
            code = (row.get(KN_drugcode) or "").strip()
            keys = nhi_code_to_keys(code)
            if not keys:
                continue
            chapter = (row.get(KN_chapter) or "").strip() if KN_chapter else ""
            link    = (row.get(KN_link)    or "").strip() if KN_link    else ""
            drugurl = (row.get(KN_drugurl) or "").strip() if KN_drugurl else ""
            enname  = (row.get(KN_enname)  or "").strip() if KN_enname  else ""
            chname  = (row.get(KN_chname)  or "").strip() if KN_chname  else ""

            payload = {
                "nhiChapter":  chapter,
                "nhiLink":     link,
                "nhiDrugCode": code,
                "nhiDrugUrl":  drugurl,
                "nhiEnName":   enname,
                "nhiChName":   chname,
            }
            if chapter:
                nhi_with_chapter += 1
            for k in keys:
                nhi_index.setdefault(k, []).append(payload)

    print(f"  仿單索引：{len(pkg_dict):,}")
    print(f"  圖檔索引：{len(img_dict):,}")
    print(f"  健保索引鍵值：{len(nhi_index):,}（NHI 含給付規定 {nhi_with_chapter:,} 筆）")

    # ── Step 3：合併（精確匹配）─────────────────────────────────
    print("\n【Step 3】合併資料（含原料藥過濾與英文名驗證）...")
    output = []
    raw_count    = 0       # 原料藥
    matched_nhi  = 0       # 健保品項
    rejected_nm  = 0       # 被英文名驗證擋下
    for drug in raw37:
        lic = (drug.get("許可證字號") or "").strip()
        if not lic:
            continue

        is_raw = is_raw_material(drug)
        if is_raw:
            raw_count += 1

        nhi = {}
        # 只有非原料藥才查健保
        if not is_raw:
            fda_en = drug.get("英文品名") or ""
            for k in fda_lic_to_keys(lic):
                cands = nhi_index.get(k)
                if not cands:
                    continue
                # 從候選中找英文名匹配最好的有給付規定的
                best = None
                for c in cands:
                    if not c.get("nhiChapter"):
                        continue
                    if names_match(fda_en, c.get("nhiEnName", "")):
                        best = c
                        break
                if best:
                    nhi = best
                    break
                # 英文名都沒過 → 記錄被擋筆數（但有候選）
                if any(c.get("nhiChapter") for c in cands):
                    rejected_nm += 1
                    break

        if nhi.get("nhiChapter"):
            matched_nhi += 1

        output.append({
            "licenseNumber": lic,
            "licenseType":   (drug.get(K37_lictype) or "").strip() if K37_lictype else "",
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
            "isRawMaterial": is_raw,
            "isNhi":         bool(nhi.get("nhiChapter")),
        })

    print(f"  總筆數：{len(output):,}")
    print(f"  原料藥：{raw_count:,}（已排除健保匹配）")
    print(f"  健保品項：{matched_nhi:,}")
    print(f"  英文名驗證排除：{rejected_nm:,}（數字撞號但藥名不符）")

    # ── Step 4：輸出 ────────────────────────────────────────────
    print(f"\n【Step 4】寫入 {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "_meta": {
                "generatedAt":  datetime.now().isoformat(),
                "totalRecords": len(output),
                "nhiRecords":   matched_nhi,
                "rawMaterials": raw_count,
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
    print(f"  原料藥　　：{sum(1 for d in output if d['isRawMaterial']):,}")
    print(f"  健保品項　：{sum(1 for d in output if d['isNhi']):,}")

    # 驗證：列出名稱含 NORVASC / AMLODIPINE 的筆數
    print("\n  🔬 驗證範例（搜尋 'NORVASC' 與 'AMLODIPINE'）：")
    for kw in ['NORVASC', 'AMLODIPINE', 'ATORVASTATIN']:
        matches = [d for d in output if kw in (d['enName'] or '').upper()]
        nhi_in_match = [d for d in matches if d['isNhi']]
        raw_in_match = [d for d in matches if d['isRawMaterial']]
        print(f"     {kw}: 共 {len(matches)} 筆 | 健保 {len(nhi_in_match)} | 原料 {len(raw_in_match)}")
        for d in matches[:3]:
            tag = "💚NHI" if d['isNhi'] else ("⚠️原料" if d['isRawMaterial'] else "  一般")
            print(f"       {tag} {d['licenseNumber']} | {d['chName'][:20]} | {d['enName'][:35]}")
            if d['isNhi']:
                print(f"             → NHI {d['nhiDrugCode']} | {d['nhiChapter'][:30]}")

    print("\n" + "=" * 60)
    print("  完成！drugs_data.json 已就緒。")
    print("=" * 60)


if __name__ == "__main__":
    main()