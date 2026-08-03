#!/usr/bin/env python3
"""
公众号文章扫描器 v4 — 基于 dajiala API + SQLite

策略（按用户要求）:
- 仅在中国法定工作日运行（自动识别周末/节假日/调休）
- 每天 8:00-22:00，每 2 小时扫描一次（由定时自动化控制频率）
- 每次扫描：拉当天全部发文 → 对当天累积全部文章查阅读量
- 阅读量在 history_by_ghid 接口中已自带，无需额外调用
- 第二天从头开始，不追前一天数据

用法:
  python dajiala_scanner.py                    # 单次扫描（当天, 自动跳过非工作日）
  python dajiala_scanner.py --date 2026-08-01  # 指定日期
  python dajiala_scanner.py --force            # 强制执行（忽略工作日检查）

依赖:
  ghid_cache.json  → 公众号 ghid 缓存
  db.py            → SQLite 存储
  dajiala_client.py → API 客户端
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta, date

from chinese_calendar import is_workday

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GHID_CACHE = os.path.join(SCRIPT_DIR, "ghid_cache.json")
ARTICLES_JSON = os.path.join(SCRIPT_DIR, "articles.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "scanner.log")

API_KEY = "JZLc3be08b3935540ff"
TZ = timezone(timedelta(hours=8))


def log(msg: str):
    ts = datetime.now(TZ).strftime("%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_scan(target_date: str = None, force: bool = False):
    """主扫描流程"""
    from db import ArticleDB
    from dajiala_client import DajialaClient

    if target_date is None:
        target_date = datetime.now(TZ).strftime("%Y-%m-%d")

    # 工作日检查
    check_date = date.fromisoformat(target_date)
    if not force and not is_workday(check_date):
        reason = "周末/节假日"
        weekday_cn = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        log(f"⏸️ 跳过: {target_date} ({weekday_cn[check_date.weekday()]}, {reason})")
        return {
            "date": target_date,
            "skipped": True,
            "reason": f"{weekday_cn[check_date.weekday()]}, {reason}",
        }

    log("=" * 40)
    log(f"🚀 v4 扫描开始 | 日期: {target_date}")

    # 加载账号
    ghid_cache = json.load(open(GHID_CACHE))
    accounts = [{"name": k, "ghid": v["ghid"]} for k, v in ghid_cache.items() if v]
    log(f"   账号: {len(accounts)} 个")

    # 扫描
    client = DajialaClient(API_KEY)
    articles, summary = client.scan_all(accounts, target_date)
    log(f"   获取: {summary['total_articles']} 篇, 消费 ¥{summary['total_cost']:.2f}")

    # 写入 SQLite
    db = ArticleDB()
    new_count = 0
    for a in articles:
        is_new = db.upsert_article(
            account_name=a["account"],
            url=a["url"],
            title=a["title"],
            publish_time=a["publish_time"],
            digest=a.get("digest", ""),
            cover=a.get("cover", ""),
            data_source=a.get("data_source", "dajiala"),
            read_count=a.get("read_count", 0),
            like_count=a.get("zan_count", 0),
        )
        if is_new:
            new_count += 1

    # 当天已存在的文章: 更新阅读量（追日内变化）
    updated_count = len(articles) - new_count

    # 存活检测
    stale_count = db.mark_stale(threshold_hours=24.0)

    # 统计
    stats = db.stats_summary()
    total_active = stats["total_active"]

    log(f"   SQLite: 新增 {new_count} 篇, 更新 {updated_count} 篇")
    log(f"   总计: {total_active} 篇活跃, {stale_count} 篇过期")

    # 导出兼容 JSON
    db.export_json(ARTICLES_JSON)
    # 导出前端 wechat.json
    try: __import__("subprocess").run([sys.executable, os.path.join(SCRIPT_DIR, "export_wechat.py")], capture_output=True)
    except: pass

    # 余额来自最后一次 API 响应
    balance = summary.get("last_balance", "?")

    log(f"   余额: ¥{balance}")
    log("✅ 完成\n")

    return {
        "date": target_date,
        "new": new_count,
        "updated": updated_count,
        "total_active": total_active,
        "stale": stale_count,
        "cost": summary["total_cost"],
        "balance": balance,
    }


if __name__ == "__main__":
    target = None
    force = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--date" and i + 1 < len(args):
            target = args[i + 1]
            i += 2
        elif args[i] == "--force":
            force = True
            i += 1
        else:
            i += 1

    result = run_scan(target, force)
    if result.get("skipped"):
        print(f"\n⏸️ 非工作日，已跳过: {result['reason']}")
    else:
        print(f"\n📊 扫描完成:")
        print(f"   日期: {result['date']}")
        print(f"   新增: {result['new']} 篇")
        print(f"   更新: {result['updated']} 篇")
        print(f"   活跃: {result['total_active']} 篇")
        print(f"   消费: ¥{result['cost']:.2f}")
        print(f"   余额: ¥{result['balance']}")
