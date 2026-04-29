#!/usr/bin/env python3
"""
藥品資料預處理腳本 build_data.py
=================================
從食藥署、健保署 API 下載資料，並從健保署完整給付規定 PDF 解析章節對照表，
合併輸出為精簡 JSON 供前端使用。

格式（經實測）：
  - FDA 37/39/42：ZIP 檔，內含 JSON
  - NHI CSV：UTF-8 BOM 開頭的 CSV
  - NHI PDF：完整給付規定（pdftotext 提取章節對照）

判定邏輯：
  - 健保品項 = NHI 有對應代號（不論有無特殊給付規定）
  - 特殊給付規定 = NHI 給付規定章節欄位有值（如「2.6.1.」）
  - 章節對照 = 從 PDF 解析「2.6.1.」對應的完整規定文字

依賴：pip install requests
系統工具：pdftotext（需 apt install poppler-utils，GitHub Actions 預裝）
"""

import json, sys, os, re, time, io, csv, zipfile, subprocess, tempfile
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

# 健保署完整給付規定 PDF（每月更新，URL 結構穩定）
NHI_PDF_PAGE = "https://www.nhi.gov.tw/ch/cp-13108-67ddf-2508-1.html"

OUTPUT_FILE = "drugs_data.json"
TIMEOUT_SEC = 180

# 成分名常見干擾字（鹽類、單位、英文劑型詞）
INGREDIENT_NOISE = {
    'BESYLATE','MALEATE','HYDROCHLORIDE','HCL','SULFATE','SULPHATE',
    'SODIUM','CALCIUM','POTASSIUM','MAGNESIUM','PHOSPHATE','CITRATE',
    'TARTRATE','SUCCINATE','FUMARATE','MESYLATE','ACETATE','LACTATE',
    'CHLORIDE','BROMIDE','IODIDE','OXIDE','MONOHYDRATE','DIHYDRATE',
    'TRIHYDRATE','ANHYDROUS','HYDRATE','HYDRATED',
    'TABLET','TABLETS','CAPSULE','CAPSULES','INJECTION','SOLUTION',
    'GRAM','GRAMS','UNIT','UNITS',
}
# ────────────────────────────────────────────────────────────────


# ── 下載 ────────────────────────────────────────────────────────
def download(url, label, verify=True, raw=False):
    print(f"  ⬇  下載中：{label}")
    if not verify:
        print("     ℹ  停用 SSL 驗證")
    start = time.time()
    resp = requests.get(url, timeout=TIMEOUT_SEC, verify=verify, headers={
        "User-Agent": "Mozilla/5.0 TFDA-DrugSearch/1.0",
        "Accept": "*/*",
    })
    resp.raise_for_status()
    data = resp.content
    print(f"     ✓  下載完成（{len(data)/1e6:.2f} MB，{time.time()-start:.1f} 秒）")
    return data


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


# ── 解析健保署完整給付規定 PDF ──────────────────────────────────
def fetch_nhi_chapters():
    """
    從健保署網頁找最新的「完整給付規定」PDF，下載並解析章節對照表。
    回傳 dict: { "2.6.1": {"title":..., "content":...}, ... }
    """
    print("\n  📖 解析健保署完整給付規定 PDF...")
    try:
        # Step 1: 抓網頁找 PDF 連結
        resp = requests.get(NHI_PDF_PAGE, timeout=60, verify=False, headers={
            "User-Agent": "Mozilla/5.0 TFDA-DrugSearch/1.0"
        })
        resp.raise_for_status()
        html = resp.text

        # 找出網頁中包含「完整給付規定」的 PDF 連結
        # 常見格式：href="/ch/dl-XXXXX-完整給付規定YYYMMDD.pdf"
        pdf_matches = re.findall(
            r'href="([^"]*完整給付規定[^"]*\.pdf)"',
            html
        )
        if not pdf_matches:
            # 退而求其次：找任何 PDF 連結
            pdf_matches = re.findall(r'href="([^"]*\.pdf)"', html)

        if not pdf_matches:
            print("     ⚠  網頁中找不到 PDF 連結，跳過章節對照")
            return {}

        pdf_url = pdf_matches[0]
        if pdf_url.startswith('/'):
            pdf_url = 'https://www.nhi.gov.tw' + pdf_url
        print(f"     ℹ  PDF URL: {pdf_url}")

        # Step 2: 下載 PDF
        pdf_resp = requests.get(pdf_url, timeout=180, verify=False, headers={
            "User-Agent": "Mozilla/5.0 TFDA-DrugSearch/1.0"
        })
        pdf_resp.raise_for_status()
        pdf_bytes = pdf_resp.content
        print(f"     ✓  PDF 下載完成（{len(pdf_bytes)/1e6:.1f} MB）")

        # Step 3: 用 pdftotext 提取（layout 模式保留排版）
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
            f.write(pdf_bytes)
            pdf_path = f.name
        txt_path = pdf_path.replace('.pdf', '.txt')

        result = subprocess.run(
            ['pdftotext', '-layout', pdf_path, txt_path],
            capture_output=True, timeout=120
        )
        if result.returncode != 0:
            print(f"     ✗  pdftotext 失敗：{result.stderr.decode('utf-8', 'replace')}")
            return {}

        with open(txt_path, 'r', encoding='utf-8') as f:
            text = f.read()

        os.unlink(pdf_path)
        os.unlink(txt_path)
        print(f"     ℹ  提取文字 {len(text):,} 字元")

        # Step 4: 解析章節
        chapters = parse_chapters(text)
        print(f"     ✓  解析得 {len(chapters):,} 個章節")
        return chapters

    except Exception as e:
        print(f"     ⚠  PDF 解析失敗：{e}（將使用無對照模式）")
        return {}


def parse_chapters(text: str) -> dict:
    """從 PDF 純文字解析 X.X.X. 章節對照"""
    # 移除分頁符與頁尾頁碼
    text = re.sub(r'\n\s*\f\s*', '\n', text)
    text = re.sub(r'\n\s*\d{1,4}\s*\n', '\n', text)

    # 抓所有 X.X.X. 開頭的章節（最少兩層編號，避免誤抓）
    chapter_re = re.compile(r'^(\d{1,2}(?:\.\d{1,3}){1,4}\.?)\s*(.+?)$', re.MULTILINE)
    matches = []
    for m in chapter_re.finditer(text):
        num = m.group(1).rstrip('.')
        # 過濾合理範圍
        parts = num.split('.')
        if not all(p.isdigit() for p in parts):
            continue
        if int(parts[0]) > 20:
            continue
        matches.append((m.start(), num, m.group(2).strip()))

    # 建立 {章節 → 內容} 對照
    chapters = {}
    for i, (start, num, title) in enumerate(matches):
        end = matches[i+1][0] if i+1 < len(matches) else len(text)
        content = text[start:end].strip()
        # 清理
        content = re.sub(r'\s*\(\d+\.\d+\.\d+更新\)\s*\n', '\n', content)
        content = re.sub(r'\n{3,}', '\n\n', content)
        chapters[num] = {
            'title': title[:150],
            'content': content[:3000],
        }
    return chapters


def lookup_chapter(chapter_str: str, chapters: dict) -> dict:
    """
    NHI 章節欄位可能是「2.6.1.」或「2.6.1.,10.4.」（多章節），
    回傳對應的完整對照清單。
    """
    if not chapter_str or not chapters:
        return []
    result = []
    # 用逗號或分號切分
    for raw in re.split(r'[,;，；]', chapter_str):
        num = raw.strip().rstrip('.').strip()
        if not num:
            continue
        # 嘗試完全匹配
        if num in chapters:
            result.append({'chapter': num, **chapters[num]})
            continue
        # 嘗試父層匹配（章節 2.6.1 不存在時退到 2.6）
        parts = num.split('.')
        for i in range(len(parts), 0, -1):
            parent = '.'.join(parts[:i])
            if parent in chapters:
                result.append({'chapter': num, **chapters[parent], 'matched': parent})
                break
    return result


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


# ── 過濾與驗證 ──────────────────────────────────────────────────
def is_raw_material(drug):
    licType = (drug.get('許可證種類') or '').strip()
    usage   = (drug.get('用法用量')   or '').strip()
    if '原料' in licType or '製劑原料' in usage:
        return True
    return False


def ingredient_core(s):
    if not s:
        return set()
    return set(re.findall(r'[A-Z]{5,}', s.upper())) - INGREDIENT_NOISE


def ingredients_match(fda_ingr, nhi_ingr):
    """成分核心字交集驗證（兩邊都用學名，標準化高）"""
    f = ingredient_core(fda_ingr)
    n = ingredient_core(nhi_ingr)
    if not f or not n:
        return True
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

    # 解析給付規定 PDF
    nhi_chapters = fetch_nhi_chapters()

    print(f"\n  筆數 → 37:{len(raw37):,}  39:{len(raw39):,}  42:{len(raw42):,}  NHI:{len(raw_nhi):,}")
    print(f"  健保章節對照：{len(nhi_chapters):,}")

    if not raw37:
        sys.exit("\n⚠  API 37 無資料")

    # ── 欄位映射 ────────────────────────────────────────────────
    print("\n  === 自動欄位映射 ===")
    K37_indication = detect_field(raw37, "適應症")
    K37_ingredient = detect_field(raw37, "主成分", "成分")
    K37_usage      = detect_field(raw37, "用法用量", "用法")
    K37_lictype    = detect_field(raw37, "許可證種類")

    K39_lic     = detect_field(raw39, "許可證字號")
    K39_package = detect_field(raw39, "仿單圖檔連結", "仿單檔案連結")
    K39_outer   = detect_field(raw39, "外盒圖檔連結")

    K42_lic   = detect_field(raw42, "許可證字號")
    K42_image = detect_field(raw42, "外觀圖檔連結", "圖檔連結")

    KN_drugcode  = detect_field(raw_nhi, "藥品代號")
    KN_chapter   = detect_field(raw_nhi, "給付規定章節")
    KN_link      = detect_field(raw_nhi, "給付規定章節連結")
    KN_drugurl   = detect_field(raw_nhi, "藥品代碼超連結")
    KN_chname    = detect_field(raw_nhi, "藥品中文名稱", "中文品名")
    KN_enname    = detect_field(raw_nhi, "藥品英文名稱", "英文品名")
    KN_ingredient= detect_field(raw_nhi, "成分")
    KN_validto   = detect_field(raw_nhi, "有效迄日")
    print(f"  API 37: 適應症={K37_indication} 成分={K37_ingredient} 用法={K37_usage} 類別={K37_lictype}")
    print(f"  API 39: lic={K39_lic} 仿單={K39_package} 外盒={K39_outer}")
    print(f"  API 42: lic={K42_lic} 圖檔={K42_image}")
    print(f"  NHI: 代號={KN_drugcode} 章節={KN_chapter} 成分={KN_ingredient} 有效迄日={KN_validto}")

    # ── Step 2：建立索引 ────────────────────────────────────────
    print("\n【Step 2】建立索引字典...")

    # 仿單
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

    # 圖檔
    img_dict = {}
    if K42_lic and K42_image:
        for row in raw42:
            lic = (row.get(K42_lic) or "").strip()
            img = (row.get(K42_image) or "").strip()
            if lic and img:
                img_dict.setdefault(lic, []).append(img)

    # NHI 索引
    today = datetime.now().strftime('%Y-%m-%d')
    nhi_index = {}
    nhi_with_chapter = 0
    for row in raw_nhi:
        code = (row.get(KN_drugcode) or "").strip()
        keys = nhi_code_to_keys(code)
        if not keys:
            continue

        # 過濾已停用的健保品項（有效迄日已過）
        validto = (row.get(KN_validto) or "").strip() if KN_validto else ""
        # NHI 日期格式可能是 YYYY-MM-DD 或 YYYY/MM/DD 或民國年
        is_expired = False
        if validto:
            v = validto.replace('/', '-')
            # 民國轉西元
            m = re.match(r'^(\d{2,3})-(\d{1,2})-(\d{1,2})$', v)
            if m and int(m.group(1)) < 200:
                v = f"{int(m.group(1))+1911}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if re.match(r'^\d{4}-\d{2}-\d{2}$', v):
                is_expired = (v < today)

        chapter = (row.get(KN_chapter)    or "").strip() if KN_chapter    else ""
        link    = (row.get(KN_link)       or "").strip() if KN_link       else ""
        drugurl = (row.get(KN_drugurl)    or "").strip() if KN_drugurl    else ""
        enname  = (row.get(KN_enname)     or "").strip() if KN_enname     else ""
        chname  = (row.get(KN_chname)     or "").strip() if KN_chname     else ""
        ingr    = (row.get(KN_ingredient) or "").strip() if KN_ingredient else ""

        payload = {
            "nhiChapter":    chapter,
            "nhiLink":       link,
            "nhiDrugCode":   code,
            "nhiDrugUrl":    drugurl,
            "nhiEnName":     enname,
            "nhiChName":     chname,
            "nhiIngredient": ingr,
            "isExpired":     is_expired,
        }
        if chapter:
            nhi_with_chapter += 1
        for k in keys:
            nhi_index.setdefault(k, []).append(payload)

    print(f"  仿單索引：{len(pkg_dict):,}")
    print(f"  圖檔索引：{len(img_dict):,}")
    print(f"  健保索引鍵值：{len(nhi_index):,}（NHI 含給付規定 {nhi_with_chapter:,} 筆）")

    # ── Step 3：合併 ────────────────────────────────────────────
    print("\n【Step 3】合併資料...")
    output = []
    raw_count = matched_nhi = with_chapter = 0
    for drug in raw37:
        lic = (drug.get("許可證字號") or "").strip()
        if not lic:
            continue

        is_raw = is_raw_material(drug)
        if is_raw:
            raw_count += 1

        nhi = {}
        if not is_raw:
            fda_ingr = drug.get(K37_ingredient) if K37_ingredient else ""
            best = best_active = None
            for k in fda_lic_to_keys(lic):
                cands = nhi_index.get(k, [])
                if not cands:
                    continue
                # 第一輪：找未過期 + 成分匹配的（優先有給付規定）
                for c in cands:
                    if c.get("isExpired"):
                        continue
                    if not ingredients_match(fda_ingr, c.get("nhiIngredient", "")):
                        continue
                    if c.get("nhiChapter") and not best:
                        best = c
                    if not best_active:
                        best_active = c
                if best:
                    break
            # 優先有 chapter 的；其次未過期的；都沒有就 best_active（也可能是 None）
            nhi = best or best_active or {}

        is_nhi = bool(nhi.get("nhiDrugCode")) and not nhi.get("isExpired", False)
        if is_nhi:
            matched_nhi += 1
        if nhi.get("nhiChapter"):
            with_chapter += 1

        # 章節對照（將 NHI 章節欄位 "2.6.1." 解析為完整文字）
        chapter_details = lookup_chapter(nhi.get("nhiChapter", ""), nhi_chapters)

        output.append({
            "licenseNumber":  lic,
            "licenseType":    (drug.get(K37_lictype)    or "").strip() if K37_lictype    else "",
            "chName":         (drug.get("中文品名")     or "").strip(),
            "enName":         (drug.get("英文品名")     or "").strip(),
            "indication":     (drug.get(K37_indication) or "").strip() if K37_indication else "",
            "ingredients":    (drug.get(K37_ingredient) or "").strip() if K37_ingredient else "",
            "usage":          (drug.get(K37_usage)      or "").strip() if K37_usage      else "",
            "packageLinks":   pkg_dict.get(lic, []),
            "imageLinks":     img_dict.get(lic, []),
            "nhiChapter":     nhi.get("nhiChapter", ""),
            "nhiDrugCode":    nhi.get("nhiDrugCode", ""),
            "nhiEnName":      nhi.get("nhiEnName", ""),
            "chapterDetails": chapter_details,
            "isRawMaterial":  is_raw,
            "isNhi":          is_nhi,
        })

    print(f"  總筆數：{len(output):,}")
    print(f"  原料藥：{raw_count:,}")
    print(f"  健保品項：{matched_nhi:,}（其中有特殊給付規定 {with_chapter:,}）")

    # ── Step 4：輸出 ────────────────────────────────────────────
    print(f"\n【Step 4】寫入 {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "_meta": {
                "generatedAt":    datetime.now().isoformat(),
                "totalRecords":   len(output),
                "nhiRecords":     matched_nhi,
                "withChapter":    with_chapter,
                "rawMaterials":   raw_count,
                "chaptersTotal":  len(nhi_chapters),
            },
            "data": output,
        }, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  ✓  完成（{os.path.getsize(OUTPUT_FILE)/1e6:.2f} MB）")

    # ── Step 5：驗證 ────────────────────────────────────────────
    print("\n【Step 5】驗證範例...")
    for kw in ['NORVASC', 'AMLODIPINE', 'ATORVASTATIN', 'METFORMIN']:
        matches = [d for d in output if kw in (d['enName'] or '').upper()]
        nhi_in = [d for d in matches if d['isNhi']]
        with_ch = [d for d in matches if d['nhiChapter']]
        print(f"\n  🔬 {kw}: 共 {len(matches)} | 健保 {len(nhi_in)} | 有給付規定 {len(with_ch)}")
        for d in matches[:4]:
            tag = "💚NHI" if d['isNhi'] else ("⚗原料" if d['isRawMaterial'] else " 一般")
            chap = d['nhiChapter'] or '無'
            print(f"     {tag} {d['licenseNumber']} | {(d['chName'] or '')[:18]:18} | 代號:{d['nhiDrugCode'] or '-':12} | 章節:{chap[:18]}")

    print("\n" + "=" * 60)
    print("  完成！drugs_data.json 已就緒。")
    print("=" * 60)


if __name__ == "__main__":
    main()