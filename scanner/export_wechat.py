#!/usr/bin/env python3
"""导出公众号数据 → data/wechat.json（当日数据，时间倒序，10万+独立板块）
规则: 北京8点前→沿用昨日数据; 8点后→严格当日数据(没发的号显示空)
"""
import json, sqlite3, os
from datetime import date, datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
NOW = datetime.now(TZ)
TODAY = date.today().isoformat()
HOUR = NOW.hour

# 8点前用昨天，8点后严格用今天
if HOUR < 8:
    target_date = (date.today() - timedelta(days=1)).isoformat()
else:
    target_date = TODAY

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(SCRIPT_DIR, "articles.db")
EXCLUDE = ['大河财立方', '经济参考报']
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    'SELECT title, account_name, url, read_count, publish_time FROM articles WHERE is_dedup=0 ORDER BY publish_time DESC'
).fetchall()
conn.close()

# 按目标日期过滤
filtered = []
for r in rows:
    if r['account_name'] in EXCLUDE: continue
    pd = str(r['publish_time'])[:10] if r['publish_time'] else ''
    if pd != target_date: continue
    filtered.append({
        'title': r['title'],
        'account': r['account_name'],
        'url': r['url'],
        'read_count': r['read_count'] or 0,
        'publish_time': str(r['publish_time']),
    })
# 按阅读量倒序
filtered.sort(key=lambda r: r['publish_time'], reverse=True)

from collections import Counter
rank_counter = Counter(r['account'] for r in filtered)
ranking = [{'name': a, 'cnt': rank_counter[a], 'k100': 0} for a in rank_counter]
ranking.sort(key=lambda x: x['cnt'], reverse=True)

data = {
    'date': target_date,
    'stats': {'total': len(filtered)},
    'ranking': ranking,
    'top100k': [],
    'articles': filtered,
}

os.makedirs(DATA_DIR, exist_ok=True)
out = os.path.join(DATA_DIR, 'wechat.json')
with open(out, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, default=str)

print(f'{target_date}: {len(filtered)}篇')
for r in ranking:
    print(f'  {r["name"]}: {r["cnt"]}篇')
