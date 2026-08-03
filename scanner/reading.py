#!/usr/bin/env python3
"""
阅读量获取模块 — 基于微信客户端 API (getappmsgext)

前置条件：
  1. 打开微信 PC 版
  2. 配置 Fiddler/Mitmproxy 抓 HTTPS 包
  3. 在微信中打开任意公众号文章
  4. 从抓包结果获取 appmsg_token 和 Cookie
  5. 填入 scanner/reading_config.json

用法:
  单篇测试:  python reading.py --test "https://mp.weixin.qq.com/s/..."
  批量更新:  python reading.py
"""

import json
import os
import re
import sys
import time
from urllib.parse import urlparse, parse_qs

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
READING_CONFIG = os.path.join(SCRIPT_DIR, "reading_config.json")

USER_AGENT_MOBILE = (
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) "
    "Version/4.0 Chrome/57.0.2987.132 MQQBrowser/6.2 Mobile"
)


def load_config():
    if not os.path.exists(READING_CONFIG):
        return {}
    with open(READING_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(READING_CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def parse_article_url(url: str) -> dict:
    """从文章 URL 提取 __biz, mid, idx, sn"""
    params = {}
    parsed = urlparse(url)

    # 长链接格式: ?__biz=xxx&mid=xxx&idx=1&sn=xxx&chksm=...
    qs = parse_qs(parsed.query)
    for key in ["__biz", "mid", "idx", "sn"]:
        if key in qs:
            params[key] = qs[key][0]

    # 短链接格式: /s/xxxxx
    if not params:
        match = re.search(r"/s/([\w\-]+)", url)
        if match:
            params["short_id"] = match.group(1)

    return params


def get_reading_data(url: str, appmsg_token: str, cookie: str) -> dict | None:
    """
    调用 getappmsgext API 获取阅读量
    返回: {"read_num": int, "like_num": int, "old_like_num": int}
    """
    article_params = parse_article_url(url)

    # 短链接需要先访问一次获取真实参数
    if "short_id" in article_params and "__biz" not in article_params:
        try:
            r = requests.get(url, timeout=15, allow_redirects=True)
            article_params = parse_article_url(r.url)
        except Exception:
            return None

    required = ["__biz", "mid", "sn", "idx"]
    if not all(k in article_params for k in required):
        print(f"  ⚠️ 无法解析文章参数: {url[:60]}...")
        return None

    api_url = f"https://mp.weixin.qq.com/mp/getappmsgext?appmsg_token={appmsg_token}&x5=0"

    headers = {
        "User-Agent": USER_AGENT_MOBILE,
        "Cookie": cookie,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "is_only_read": "1",
        "is_temp_url": "0",
        "appmsg_type": "9",
        "__biz": article_params["__biz"],
        "mid": article_params["mid"],
        "sn": article_params["sn"],
        "idx": article_params["idx"],
    }

    try:
        r = requests.post(api_url, headers=headers, data=data, timeout=15)
        result = r.json()

        if "appmsgstat" not in result:
            err = result.get("base_resp", {}).get("ret", "?")
            print(f"  ⚠️ API 返回异常 ret={err}: {url[:50]}...")
            return None

        stat = result["appmsgstat"]
        return {
            "read_num": stat.get("read_num", 0),
            "like_num": stat.get("like_num", 0),
            "old_like_num": stat.get("old_like_num", 0),
        }

    except Exception as e:
        print(f"  ❌ 请求失败: {e}")
        return None


def batch_update(interval: float = 8.0, limit: int = 50):
    """
    批量更新 SQLite 中所有文章的阅读量
    interval: 每篇文章请求间隔（秒），建议 5-10s
    limit: 单次最多更新数量
    """
    from db import ArticleDB

    cfg = load_config()
    appmsg_token = cfg.get("appmsg_token", "")
    cookie = cfg.get("cookie", "")

    if not appmsg_token or not cookie:
        print("❌ 缺少 appmsg_token 或 cookie")
        print("   请先按指南抓包，将凭证填入 scanner/reading_config.json")
        return

    db = ArticleDB()
    total = db.count()
    to_update = db.get_needs_reading_update(max_age_hours=4.0, limit=limit)

    print(f"📊 待更新: {len(to_update)} 篇 (共 {total} 篇)")

    updated = 0
    for i, a in enumerate(to_update):
        print(f"  [{i+1}/{len(to_update)}] {a.get('title', '?')[:40]} ...")
        data = get_reading_data(a["url"], appmsg_token, cookie)

        if data is None:
            print(f"    ⏭️ 跳过")
            time.sleep(interval * 0.5)
            continue

        stats = {
            "read_count": data["read_num"],
            "like_count": data.get("like_num", 0),
            "wonderful_count": data.get("old_like_num", 0),
            "share_count": 0,
            "collect_count": 0,
            "comment_count": 0,
        }

        db.update_reading_stats(a["msg_key"], stats)

        print(f"    ✅ 阅读={data['read_num']}, 点赞={data['like_num']}")
        updated += 1
        time.sleep(interval)

    if updated > 0:
        print(f"💾 已更新 {updated} 篇\n")
        # 更新后重新导出 JSON 兼容文件
        import os
        articles_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "articles.json")
        db.export_json(articles_file)


def test_single(url: str):
    """测试单篇文章的阅读量获取"""
    cfg = load_config()
    appmsg_token = cfg.get("appmsg_token", "")
    cookie = cfg.get("cookie", "")

    if not appmsg_token or not cookie:
        print("❌ 缺少凭证，先按指南抓包")
        print("   填入 scanner/reading_config.json:")
        print(json.dumps({
            "appmsg_token": "你的_appmsg_token",
            "cookie": "你的_微信客户端_cookie"
        }, ensure_ascii=False, indent=2))
        return

    params = parse_article_url(url)
    print(f"文章参数: {params}")
    print(f"appmsg_token: {appmsg_token[:30]}...")
    print(f"cookie: {cookie[:50]}...")

    data = get_reading_data(url, appmsg_token, cookie)
    if data:
        print(f"\n📊 结果:")
        print(f"  阅读数: {data['read_num']}")
        print(f"  点赞数: {data['like_num']}")
        print(f"  在看数: {data['old_like_num']}")
    else:
        print("\n❌ 获取失败，请检查:")
        print("  1. appmsg_token 是否过期（每 4 小时过期）")
        print("  2. cookie 是否正确")
        print("  3. 文章链接是否有效")


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "--test":
        test_single(sys.argv[2])
    else:
        batch_update()
