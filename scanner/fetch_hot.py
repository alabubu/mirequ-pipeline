#!/usr/bin/env python3
"""
热点数据采集 — 从 newsnow API 拉所有平台热榜 → 输出 hot.json
每 5 分钟跑一次，前端直接读同域 JSON 文件
"""
import json, time, os, requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(SCRIPT_DIR, "hot.json")
APP_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "mirequ", "hot.json")

PLATFORMS = {
    "zhihu": "知乎",
    "weibo": "微博",
    "douyin": "抖音",
    "bilibili-hot-search": "B站",
    "wallstreetcn-hot": "华尔街见闻",
    "tieba": "贴吧",
    "baidu": "百度",
    "cls-hot": "财联社",
    "thepaper": "澎湃新闻",
    "ifeng": "凤凰网",
    "toutiao": "今日头条",
    "jin10": "金十数据",
}

BASE = "https://newsnow.busiyi.world/api/s"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://newsnow.busiyi.world/",
    "Origin": "https://newsnow.busiyi.world",
}

results = []
for pid, name in PLATFORMS.items():
    try:
        r = requests.get(f"{BASE}?id={pid}&latest", headers=HEADERS, timeout=15)
        data = r.json()
        items = data.get("items", [])
        results.append({
            "id": pid, "name": name, "count": len(items),
            "items": [
                {
                    "title": it.get("title", ""),
                    "url": it.get("url", ""),
                    "heat": (it.get("extra") or {}).get("info", ""),
                }
                for it in items
            ],
        })
        print(f"  {name}: {len(items)} 条")
    except Exception as e:
        print(f"  {name}: ERROR - {e}")
        results.append({"id": pid, "name": name, "count": 0, "items": []})
    time.sleep(0.3)

total = sum(r["count"] for r in results)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump({"updated": time.strftime("%H:%M:%S"), "total": total, "platforms": results}, f, ensure_ascii=False, indent=2)
with open(APP_DIR, "w", encoding="utf-8") as f:
    json.dump({"updated": time.strftime("%H:%M:%S"), "total": total, "platforms": results}, f, ensure_ascii=False, indent=2)

print(f"\n→ hot.json ({total} 条热点)")
print(f"→ app_v4/hot.json (待部署)")