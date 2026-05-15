#!/usr/bin/env python3
"""
one-carat.com からポコチャのランクボーダーデータを取得し data.json を更新する。

実行: python3 scraper.py
依存: 標準ライブラリのみ（urllib, re, json）
"""

import urllib.request
import urllib.error
import re
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

USER_AGENT = "Mozilla/5.0 (compatible; PocochaBorderUpdater/1.0)"
CATEGORY_URL = "https://one-carat.com/campus/archives/category/streamer-tips/pococha-rank-border"
RANKS = ['D1','D2','D3','C1','C2','C3','B1','B2','B3','A1','A2','A3','S1','S2','S3','S4','S5','S6']
JST = timezone(timedelta(hours=9))

# 索引ページを何ページ分まで巡回するか（新しい順）。1ページ = 約3ヶ月分。
INDEX_PAGES_TO_SCAN = 3


def fetch(url, retries=2):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for i in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode('utf-8', errors='ignore')
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if i < retries:
                time.sleep(2 ** i)
    raise last_err


def find_articles_on_index(html):
    """索引ページから (年, 月, 締め時間, URL) を抽出。
    タイトルが <a href="..."> ... <p>【YYYY年M月】...《N時締め》</p> ... </a> のような
    入れ子構造で出現するため、a タグ単位でブロックに分割して中身を見る。
    """
    out = []
    # <a ... href="https://one-carat.com/campus/archives/NNNN" ...> ... </a> を非貪欲に抽出
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


def parse_article(html):
    """記事HTMLからランクごとの [±0, +1, +2] を抽出"""
    text = re.sub(r'<[^>]+>', ' ', html)
    text = text.replace('&nbsp;', ' ')
    text = re.sub(r'\s+', ' ', text)

    result = {}
    for rank in RANKS:
        # 例: 【D1】［±0］2,900 ［+1］8,900 ［+2］16,700
        # ブラケットや空白のゆらぎを許容
        pat = re.compile(
            r'(?:[【\[\(]?\s*)' + re.escape(rank) + r'(?:\s*[】\]\)]?)'
            r'[^0-9±+]{0,40}'
            r'±\s*0[^0-9]{0,15}([\d,]+)'
            r'[^0-9+]{0,40}'
            r'\+\s*1[^0-9]{0,15}([\d,]+)'
            r'[^0-9+]{0,40}'
            r'\+\s*2[^0-9]{0,15}([\d,]+)'
        )
        m = pat.search(text)
        if m:
            try:
                vals = [int(m.group(i + 1).replace(',', '')) for i in range(3)]
                # サニティチェック: 0より大きい・極端でない
                if all(100 <= v <= 50_000_000 for v in vals):
                    result[rank] = vals
            except ValueError:
                pass
    return result


def extract_article_day(html, expected_month):
    """記事に書かれている日付 (N日) を抽出。複数あれば最大値（最新日）を返す。
    expected_month は記事タイトルの月（記事の本文「N月M日」のNと一致するはず）。"""
    # 「M月D日」の形式を優先（本文の見出し）
    days = []
    for m in re.finditer(r'(\d{1,2})月(\d{1,2})日', html):
        mo, d = int(m.group(1)), int(m.group(2))
        if mo == expected_month and 1 <= d <= 31:
            days.append(d)
    if days:
        return max(days)
    # フォールバック: <h3>N日</h3> のような形
    for m in re.finditer(r'<h3[^>]*>[^<]*?(\d{1,2})日', html):
        d = int(m.group(1))
        if 1 <= d <= 31:
            days.append(d)
    return max(days) if days else None


def load_existing(path):
    if path.exists():
        with path.open(encoding='utf-8') as f:
            return json.load(f)
    return {"source": CATEGORY_URL, "updated": "initial", "data": {}}


def main():
    data_path = Path(__file__).parent / 'data.json'
    existing = load_existing(data_path)
    data = existing.get('data', {})

    # 索引から記事を収集
    articles = []
    for page in range(1, INDEX_PAGES_TO_SCAN + 1):
        url = CATEGORY_URL if page == 1 else f"{CATEGORY_URL}/page/{page}"
        try:
            html = fetch(url)
        except Exception as e:
            print(f"[warn] index page {page} fetch failed: {e}", file=sys.stderr)
            continue
        articles.extend(find_articles_on_index(html))
        time.sleep(1)

    # 重複排除
    seen = set()
    unique = []
    for year, month, t, url in articles:
        key = (year, month, t)
        if key in seen:
            continue
        seen.add(key)
        unique.append((year, month, t, url))

    print(f"[info] found {len(unique)} unique articles in index")

    # 既存にない or 当月のものを更新（当月は途中で値が変わる可能性があるので毎回上書き）
    now = datetime.now(JST)
    current_ym = (now.year, now.month)

    added = updated = daily_added = 0
    for year, month, t, url in unique:
        month_key = f"{year}-{month:02d}"
        is_current_month = (year, month) == current_ym
        already_have = month_key in data and t in data[month_key]

        # 当月は毎日 _d[day] に追記したいので、過去月でも _d が無い記事はフェッチする
        need_fetch = is_current_month or (not already_have)
        if not need_fetch:
            continue

        try:
            html = fetch(url)
        except Exception as e:
            print(f"[warn] fetch failed {url}: {e}", file=sys.stderr)
            continue

        ranks = parse_article(html)
        if len(ranks) < 10:
            print(f"[warn] only {len(ranks)} ranks parsed from {url} (expected 18) — skip", file=sys.stderr)
            continue

        # 既存のキー（ランク値・_d など）を保持しつつマージ
        time_bucket = data.setdefault(month_key, {}).setdefault(t, {})
        # ランクごとの値を更新（最新日 = 月末候補値として上書き）
        for r, v in ranks.items():
            time_bucket[r] = v

        # 記事が「何日のデータ」かを抽出して日別データを保存
        day = extract_article_day(html, month)
        if day is not None:
            d_bucket = time_bucket.setdefault('_d', {})
            day_str = str(day)
            is_new_day = day_str not in d_bucket
            d_bucket[day_str] = ranks
            if is_new_day:
                daily_added += 1
                print(f"[daily]  {month_key} {t}h day={day} ({len(ranks)} ranks)")

        if already_have:
            updated += 1
            print(f"[update] {month_key} {t}h ({len(ranks)} ranks)")
        else:
            added += 1
            print(f"[add]    {month_key} {t}h ({len(ranks)} ranks)")
        time.sleep(1)

    existing['data'] = data
    existing['updated'] = now.strftime('%Y-%m-%d %H:%M JST')
    existing['source'] = CATEGORY_URL

    with data_path.open('w', encoding='utf-8') as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"[done] added={added} updated={updated} daily_added={daily_added} total_months={len(data)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
