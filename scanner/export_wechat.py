#!/usr/bin/env python3
"""导出公众号数据 → data/wechat.json（当日数据，时间倒序，10万+独立板块）"""
import json, sqlite3, os
from datetime import date

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(SCRIPT_DIR, "articles.db")
EXCLUDE = ['大河财立方', '经济参考报']
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
TODAY = date.today().isoformat()

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    'SELECT title, account_name, url, read_count, publish_time FROM articles WHERE is_dedup=0 ORDER BY publish_time DESC'
).fetchall()
conn.close()

filtered = []
for r in rows:
    if r['account_name'] in EXCLUDE:
        continue
    publish_date = str(r['publish_time'])[:10] if r['publish_time'] else ''
    if publish_date != TODAY:
        continue
    filtered.append({
        'title': r['title'],
        'account': r['account_name'],
        'url': r['url'],
        'read_count': min(r['read_count'] or 0, 100000),
        'publish_time': str(r['publish_time']),
    })

top100k = [r for r in filtered if r['read_count'] >= 100000]

from collections import Counter
rank_counter = Counter(r['account'] for r in filtered)
hot_counter = Counter(r['account'] for r in top100k)
ranking = [{'name': a, 'cnt': rank_counter[a], 'k100': hot_counter.get(a, 0)} for a in rank_counter]
ranking.sort(key=lambda x: x['cnt'], reverse=True)

data = {
    'date': TODAY,
    'stats': {'total': len(filtered), 'hot100k': len(top100k)},
    'ranking': ranking,
    'top100k': top100k,
    'articles': filtered,
}

os.makedirs(DATA_DIR, exist_ok=True)
out = os.path.join(DATA_DIR, 'wechat.json')
with open(out, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, default=str)

print(f'{TODAY}: {len(filtered)}篇 ({len(top100k)}篇10万+)')
for r in ranking:
    print(f'  {r["name"]}: {r["cnt"]}篇 {r["k100"]}个10万+')
