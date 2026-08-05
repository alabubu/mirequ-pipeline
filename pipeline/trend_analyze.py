#!/usr/bin/env python3
"""热点趋势分析
- 词频分析：同一主题跨平台的覆盖度
- 综合打分：rank×0.6 + freq×0.3 + heat×0.1
- AI 解读：DeepSeek 分析趋势变化（每30分钟一次）

输出 data/trend.json
"""
import json, os, re, hashlib
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
HOT_FILE = os.path.join(DATA_DIR, "hot.json")
TREND_FILE = os.path.join(DATA_DIR, "trend.json")
CACHE_FILE = os.path.join(DATA_DIR, ".trend_cache.json")
AI_INTERVAL_MIN = 30  # AI分析间隔（分钟）
AI_MODEL = "deepseek-chat"
AI_KEY = os.environ.get("AI_API_KEY", "")
AI_BASE = os.environ.get("AI_API_BASE", "https://api.deepseek.com")

TZ = timezone(timedelta(hours=8))
NOW = datetime.now(TZ)

# =========== 工具函数 ===========

def extract_keywords(title):
    """提取标题中的核心关键词（2-4字词组）"""
    import re
    # 去掉标点，取长度>=2的词
    cleaned = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', title)
    words = []
    for i in range(len(cleaned)):
        for l in (2, 3, 4):
            if i+l <= len(cleaned):
                words.append(cleaned[i:i+l])
    return words

def normalize_title(title):
    """标题标准化"""
    t = re.sub(r'[#＃\s]', '', title)
    t = re.sub(r'[？?！!，,。.、；;：:（）()【】\[\]""'']', '', t)
    return t[:20]

def load_json(path):
    if not os.path.exists(path): return {}
    with open(path) as f: return json.load(f)

def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, ensure_ascii=False, default=str)

# =========== 1. 词频分析 ===========

def analyze_frequency(hot_data):
    """统计每条热搜出现的平台数和总热度"""
    if not hot_data or not hot_data.get("platforms"):
        return [], []

    platforms = hot_data["platforms"]
    
    # 按标准化标题分组
    title_groups = defaultdict(list)
    for p in platforms:
        rank = 1
        for item in p.get("items", [])[:30]:
            norm = normalize_title(item.get("title", ""))
            if not norm: continue
            title_groups[norm].append({
                "title": item["title"],
                "platform": p["name"],
                "rank": rank,
                "url": item.get("url", ""),
                "heat_raw": item.get("heat", ""),
            })
            rank += 1

    # 聚合
    topics = []
    for norm, entries in title_groups.items():
        n_platforms = len(set(e["platform"] for e in entries))
        best = min(entries, key=lambda x: x["rank"])
        topic = {
            "title": best["title"],
            "platforms": n_platforms,
            "platform_list": [e["platform"] for e in entries[:5]],
            "best_rank": best["rank"],
            "url": best["url"],
            "heat_raw": best["heat_raw"],
        }
        topics.append(topic)

    # 按跨平台数排序
    topics.sort(key=lambda x: (-x["platforms"], x["best_rank"]))
    
    # 日升榜：新出现在多个平台的话题
    rising = [t for t in topics if t["platforms"] >= 2]
    
    return topics, rising

# =========== 2. 综合打分 ===========

def calculate_score(topic, max_platforms):
    """排名×0.6 + 频次×0.3 + 热度×0.1"""
    # 排名分：best_rank越低越好，映射到0-1
    rank_score = max(0, 1 - (topic["best_rank"] - 1) / 30)  # 第1名=1, 第30名=0
    # 频次分：跨平台数映射
    freq_score = min(1, topic["platforms"] / max(1, max_platforms))
    # 热度分
    heat_score = 0.5  # 默认中等
    try:
        raw = topic.get("heat_raw", "0")
        num = float(re.sub(r'[^\d.]', '', str(raw)))
        if num > 0:
            heat_score = min(1, num / 1000000)
    except:
        pass
    
    return round(rank_score * 0.6 + freq_score * 0.3 + heat_score * 0.1, 4)


def score_topics(topics):
    """综合打分并排序"""
    if not topics:
        return []
    max_p = max(t["platforms"] for t in topics)
    for t in topics:
        t["score"] = calculate_score(t, max_p)
    topics.sort(key=lambda x: -x["score"])
    return topics[:30]

# =========== 3. AI 分析 ===========

def should_ai_analyze():
    """检查是否需要AI分析（从缓存读取上次时间）"""
    cache = load_json(CACHE_FILE)
    last_str = cache.get("last_ai") if cache else None
    if not last_str:
        return True
    try:
        last = datetime.fromisoformat(last_str)
        elapsed = (NOW - last).total_seconds() / 60
        return elapsed >= AI_INTERVAL_MIN
    except:
        return True

def ai_analyze(topics):
    """调用DeepSeek分析趋势"""
    if not AI_KEY:
        return "未配置 AI_API_KEY"

    titles = [f"{i+1}. [{t['platforms']}平台] {t['title']}" for i, t in enumerate(topics[:50])]
    title_list = "\n".join(titles)

    prompt = f"""你是专业的热点趋势分析师。下面是最新全网热搜TOP50，请用200字中文总结：
1. 今日最大的热点方向（1-2个）
2. 哪些话题正在升温（标注跨平台数）
3. 不同平台之间有没有关联（比如微博和知乎同时在讨论什么）

数据：
{title_list}

请直接回答，不要重复题目。"""

    for attempt in range(2):
        try:
            req = Request(
                f"{AI_BASE}/v1/chat/completions",
                data=json.dumps({
                    "model": AI_MODEL,
                    "messages": [
                        {"role": "system", "content": "你是专业热点分析师，用精炼中文回答，不超过200字。"},
                        {"role": "user", "content": prompt}
                    ],
                    "max_tokens": 800,
                    "temperature": 1.0,
                }).encode(),
                headers={
                    "Authorization": f"Bearer {AI_KEY}",
                    "Content-Type": "application/json"
                }
            )
            resp = urlopen(req, timeout=60)
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt == 1:
                return f"AI分析失败: {str(e)[:100]}"
            continue

    return "AI分析异常"

# =========== 4. 存储缓存 ===========

def update_cache(insight=None):
    cache = load_json(CACHE_FILE) or {}
    cache["last_ai"] = NOW.isoformat()
    if insight and "未配置" not in insight and "失败" not in insight:
        cache["last_insight"] = insight
    save_json(CACHE_FILE, cache)

# =========== 主流程 ===========

def main():
    hot_data = load_json(HOT_FILE)
    if not hot_data:
        print("无hot.json数据")
        return

    print(f"分析时间: {NOW.strftime('%H:%M:%S')}")

    # 词频分析
    topics, rising = analyze_frequency(hot_data)
    print(f"词频: {len(topics)} 话题, {len(rising)} 升温")

    # 综合打分
    ranked = score_topics(topics)
    top20 = ranked[:20]
    print(f"TOP20: {', '.join(t['title'][:15] for t in top20[:5])}")

    # 平台统计
    platform_stats = []
    for p in hot_data.get("platforms", []):
        platform_stats.append({
            "name": p["name"],
            "count": len(p.get("items", [])),
            "top_title": p["items"][0]["title"] if p.get("items") else "",
        })

    # AI 分析
    insight = ""
    if should_ai_analyze():
        print("执行AI分析...")
        insight = ai_analyze(ranked)
        update_cache(insight)
        print(f"AI: {insight[:80]}...")
    else:
        # 用上次的分析结果
        cache = load_json(CACHE_FILE) or {}
        insight = cache.get("last_insight", "")
        print("使用缓存的AI分析")

    # 输出
    trend = {
        "updated": NOW.strftime("%H:%M:%S"),
        "date": NOW.strftime("%Y-%m-%d"),
        "top20": top20,
        "rising": rising[:10],
        "insight": insight,
        "platform_stats": platform_stats,
    }
    save_json(TREND_FILE, trend)

    print(f"输出 trend.json: top20={len(top20)}, insight_len={len(insight)}")

if __name__ == "__main__":
    main()
