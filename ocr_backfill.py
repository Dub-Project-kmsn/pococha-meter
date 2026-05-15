#!/usr/bin/env python3
"""
過去日次データのOCRバックフィル。
one-carat.com の各記事に貼られている「早見表PNG」をダウンロードし、
tesseract OCR でテキスト化、表構造を解析して data.json の _d (daily) に追記する。

依存:
  - tesseract-ocr (システム): apt-get install tesseract-ocr tesseract-ocr-jpn
  - pip: pillow

実行:
  python3 ocr_backfill.py
"""

import urllib.request
import urllib.error
import re
import json
import sys
import os
import time
import subprocess
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
from io import BytesIO

DEBUG = os.environ.get('OCR_DEBUG') == '1'
LIMIT = int(os.environ.get('OCR_LIMIT') or 0)  # 0=無制限

USER_AGENT = "Mozilla/5.0 (compatible; PocochaBorderOCR/1.0)"
CATEGORY_URL = "https://one-carat.com/campus/archives/category/streamer-tips/pococha-rank-border"
RANKS = ['D1','D2','D3','C1','C2','C3','B1','B2','B3','A1','A2','A3','S1','S2','S3','S4','S5','S6']
# 早見表画像は3組のランク帯ごとに2枚（日付前半/後半）。alt属性で「早見表」とマークされている。
RANK_GROUPS = [
    ['D1','D2','D3','C1','C2','C3'],
    ['B1','B2','B3','A1','A2','A3'],
    ['S1','S2','S3','S4','S5','S6'],
]
JST = timezone(timedelta(hours=9))
INDEX_PAGES_TO_SCAN = 6  # OCRバックフィルは過去広めに巡回


def fetch(url, retries=2):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if i < retries:
                time.sleep(2 ** i)
    raise last_err


def find_articles_on_index(html):
    out = []
    a_pat = re.compile(
        r'<a[^>]*href="(https://one-carat\.com/campus/archives/\d+)/?"[^>]*>(.*?)</a>',
        re.DOTALL
    )
    title_pat = re.compile(r'【(\d{4})年(\d{1,2})月】[^<]*?《(\d{1,2})時締め》')
    for am in a_pat.finditer(html):
        url, inner = am.group(1), am.group(2)
        tm = title_pat.search(inner)
        if not tm:
            continue
        year, month, t = int(tm.group(1)), int(tm.group(2)), tm.group(3)
        if t not in ('13', '22', '24'):
            continue
        out.append((year, month, t, url))
    return out


def extract_chart_image_urls(html):
    """記事HTMLから早見表PNGのURLリストを抽出（出現順）"""
    urls = []
    # alt属性に「早見表」を含む img を抽出
    for m in re.finditer(r'<img[^>]+>', html):
        tag = m.group(0)
        if '早見表' not in tag:
            continue
        src_m = re.search(r'src="([^"]+\.png)"', tag)
        if src_m:
            urls.append(src_m.group(1))
    # 重複除去（順序維持）
    seen = set()
    uniq = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def ocr_image(image_bytes):
    """画像をtesseractでOCRしテキストを返す。失敗時は空文字列。"""
    if not shutil.which('tesseract'):
        raise RuntimeError("tesseract コマンドが見つかりません。apt-get install tesseract-ocr が必要です。")
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tf:
        tf.write(image_bytes)
        img_path = tf.name
    try:
        # 数字+少数の日本語のみの表なので、汎用設定でOK
        # --psm 6: 単一のテキストブロックと想定
        result = subprocess.run(
            ['tesseract', img_path, '-', '--psm', '6', '-l', 'jpn+eng'],
            capture_output=True, text=True, timeout=60
        )
        return result.stdout
    finally:
        Path(img_path).unlink(missing_ok=True)


def parse_chart_text(text, rank_group):
    """OCR結果テキストから {day: {rank: [±0,+1,+2]}} を構築。

    期待される行パターン:
      "11 月 ±0 3,000 9,200 21,300 38,300 60,300 86,200"
      "1 9,800 25,700 ..."       (+1 行)
      "2 17,600 45,300 ..."      (+2 行)

    実際にはOCRで日付/曜日が省かれたり結合されたりする。
    日付セルを安定して取るため、行の連続パターンを使う:
      ±0/+1/+2 を順番に検出して 6 個の数値を取得 → 1日分。
    """
    # 正規化: 全角数字・空白
    text = text.replace('，', ',').replace('．', '.')
    text = re.sub(r'[ \t]+', ' ', text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    # 行に含まれる「±0」「+1」「+2」マーカーを基準に分類
    daily = {}  # day -> {rank: [±0,+1,+2]}
    current_day = None
    buf = {0: None, 1: None, 2: None}  # ±0, +1, +2 のそれぞれ [v_rank0..v_rank5]

    def flush():
        nonlocal current_day, buf
        if current_day is None:
            return
        if all(buf[i] is not None and len(buf[i]) == len(rank_group) for i in range(3)):
            day_data = {}
            for ri, r in enumerate(rank_group):
                vals = [buf[0][ri], buf[1][ri], buf[2][ri]]
                if all(100 <= v <= 50_000_000 for v in vals):
                    day_data[r] = vals
            if len(day_data) == len(rank_group):
                daily[str(current_day)] = day_data
        current_day = None
        buf = {0: None, 1: None, 2: None}

    num_pat = re.compile(r'\d[\d,]*')

    for ln in lines:
        # 日付の検出: 行頭付近に 1〜31 の独立した整数があれば日付
        # ただし数値が金額として混じるので、行頭の数字のみを見る
        leading = re.match(r'^(\d{1,2})\b', ln)
        marker_m = re.search(r'(±\s*0|[\+＋]\s*1|[\+＋]\s*2)', ln)

        if not marker_m:
            continue

        marker = marker_m.group(1)
        if '±' in marker:
            mi = 0
        elif '1' in marker:
            mi = 1
        else:
            mi = 2

        # 行から数値を全部抽出
        nums = [int(s.replace(',', '')) for s in num_pat.findall(ln) if ',' in s or len(s) >= 3]
        # 末尾の rank_group 数の数値を採用
        if len(nums) >= len(rank_group):
            nums = nums[-len(rank_group):]
        else:
            continue

        # 日付の更新（±0 の行で行頭数字があれば新しい日）
        if mi == 0 and leading:
            d = int(leading.group(1))
            if 1 <= d <= 31:
                flush()
                current_day = d
        elif mi == 0 and current_day is None:
            # 行頭数字なしの ±0 行 — スキップ
            continue

        buf[mi] = nums

    flush()
    return daily


def load_existing(path):
    if path.exists():
        with path.open(encoding='utf-8') as f:
            return json.load(f)
    return {"source": CATEGORY_URL, "updated": "initial", "data": {}}


def merge_daily(data, month_key, t, daily_partial):
    """daily_partial = {day: {rank: [...]}} を data[month_key][t]['_d'] にマージ。
    既存値があれば優先（OCR誤認識から既存正解を守る）。"""
    bucket = data.setdefault(month_key, {}).setdefault(t, {}).setdefault('_d', {})
    merged_days = 0
    for day, ranks in daily_partial.items():
        existing = bucket.get(day, {})
        # ランクをマージ: 既存にあるランクは温存
        for r, v in ranks.items():
            existing.setdefault(r, v)
        bucket[day] = existing
        merged_days += 1
    return merged_days


def process_article(year, month, t, url, data):
    """1記事をOCR処理して daily に追記"""
    print(f"[ocr] {year}-{month:02d} {t}h -> {url}", file=sys.stderr)
    try:
        html_bytes = fetch(url)
        html = html_bytes.decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"[warn] article fetch failed: {e}", file=sys.stderr)
        return 0

    img_urls = extract_chart_image_urls(html)
    if not img_urls:
        print(f"[warn] no chart images in {url}", file=sys.stderr)
        return 0

    print(f"[ocr]   {len(img_urls)} chart images", file=sys.stderr)

    # 画像は通常6枚（3ランク帯 × 2: 日付前半/後半）
    # ランク帯の順序は記事内に [D-C], [B-A], [S] の順で並ぶ
    month_key = f"{year}-{month:02d}"
    total_days = 0

    for idx, img_url in enumerate(img_urls):
        # ランク帯の判定: 6枚なら 0,1->D-C / 2,3->B-A / 4,5->S
        if len(img_urls) >= 6:
            group_idx = idx // 2
        elif len(img_urls) >= 3:
            group_idx = idx
        else:
            group_idx = idx % 3
        if group_idx >= len(RANK_GROUPS):
            continue
        rank_group = RANK_GROUPS[group_idx]

        try:
            img_bytes = fetch(img_url)
            text = ocr_image(img_bytes)
        except Exception as e:
            print(f"[warn]   image OCR failed {img_url}: {e}", file=sys.stderr)
            continue

        daily_partial = parse_chart_text(text, rank_group)
        if daily_partial:
            n = merge_daily(data, month_key, t, daily_partial)
            print(f"[ocr]   img{idx} group={rank_group[0]}-{rank_group[-1]}  parsed {len(daily_partial)} days", file=sys.stderr)
            total_days += n
        else:
            print(f"[warn]   img{idx} parse yielded 0 days", file=sys.stderr)
            if DEBUG:
                print(f"[debug] === OCR raw text for img{idx} ===", file=sys.stderr)
                print(text, file=sys.stderr)
                print(f"[debug] === END ===", file=sys.stderr)
        time.sleep(0.5)

    return total_days


def main():
    data_path = Path(__file__).parent / 'data.json'
    existing = load_existing(data_path)
    data = existing.get('data', {})

    # 索引から記事を収集
    articles = []
    for page in range(1, INDEX_PAGES_TO_SCAN + 1):
        url = CATEGORY_URL if page == 1 else f"{CATEGORY_URL}/page/{page}"
        try:
            html = fetch(url).decode('utf-8', errors='ignore')
        except Exception as e:
            print(f"[warn] index page {page} fetch failed: {e}", file=sys.stderr)
            continue
        articles.extend(find_articles_on_index(html))
        time.sleep(1)

    seen = set()
    unique = []
    for year, month, t, url in articles:
        key = (year, month, t)
        if key in seen:
            continue
        seen.add(key)
        unique.append((year, month, t, url))

    print(f"[info] {len(unique)} articles to OCR", file=sys.stderr)

    total = 0
    processed = 0
    for year, month, t, url in unique:
        if LIMIT and processed >= LIMIT:
            print(f"[info] OCR_LIMIT={LIMIT} reached, stopping", file=sys.stderr)
            break
        total += process_article(year, month, t, url, data)
        processed += 1

    now = datetime.now(JST)
    existing['data'] = data
    existing['updated'] = now.strftime('%Y-%m-%d %H:%M JST (OCR)')

    with data_path.open('w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"[done] daily_days_merged={total}", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
