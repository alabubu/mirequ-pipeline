#!/usr/bin/env python3
"""
dajiala API 客户端 — history_by_ghid Pro 接口

特点:
- 一个接口同时返回文章列表 + 阅读/点赞数据
- 每页 10 次发文（每次 1-8 篇），支持翻页
- QPS ≤ 2/s, 500 重试 3 次
- 按日过滤文章
"""

import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

BASE_URL = "https://www.dajiala.com/fbmain/monitor/v3"
TZ = timezone(timedelta(hours=8))  # Asia/Shanghai


class DajialaClient:
    def __init__(self, api_key: str):
        self.key = api_key
        self.total_cost = 0.0
        self.last_balance = 0.0

    # ---------- 底层调用 ----------

    def _post(self, endpoint: str, payload: dict, retries: int = 3) -> dict:
        for attempt in range(retries):
            try:
                r = requests.post(
                    f"{BASE_URL}/{endpoint}",
                    json=payload,
                    timeout=30,
                )
                data = r.json()
                if isinstance(data, str):
                    data = json.loads(data)
                code = data.get("code", 500)

                # QPS 限流
                if code == -1:
                    print(f"    ⚠️ QPS 超限，等待 5s ...")
                    time.sleep(5)
                    continue

                # 网络错误重试
                if code == 500 and attempt < retries - 1:
                    time.sleep(2)
                    continue

                # 金额不足
                if code == 20001:
                    print(f"    💰 余额不足！")
                    return data

                return data

            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(2)

        return {"code": 500, "msg": "重试耗尽"}

    # ---------- 文章获取 ----------

    def fetch_articles(
        self,
        ghid: str,
        target_date: Optional[str] = None,
        max_pages: int = 5,
    ) -> list[dict]:
        """
        获取公众号文章（翻页 + 按日过滤）

        返回: [{title, url, publish_time, read_count, zan_count, digest, cover, ...}]
        """
        all_articles = []

        if target_date is None:
            target_date = datetime.now(TZ).strftime("%Y-%m-%d")

        offset = ""
        for page in range(max_pages):
            data = self._post("history_by_ghid", {
                "ghid": ghid,
                "nickname": "",
                "url": "",
                "offset": offset,
                "key": self.key,
            })

            code = data.get("code", 500)
            if code != 0:
                break

            self.total_cost += data.get("cost", 0)
            self.last_balance = data.get("remain_money", self.last_balance)

            msglist = data.get("MsgList", {})
            msgs = msglist.get("Msg", [])
            paging = msglist.get("PagingInfo", {})

            for msg in msgs:
                appmsg = msg.get("AppMsg", {})
                create_time = appmsg.get("BaseInfo", {}).get("CreateTime", 0)
                if create_time:
                    pub_date = datetime.fromtimestamp(create_time, TZ).strftime("%Y-%m-%d")
                else:
                    pub_date = ""

                # 按日过滤：已经超过目标日期，停止
                if pub_date and pub_date < target_date:
                    return all_articles

                details = appmsg.get("DetailInfo", [])
                for art in details:
                    if pub_date == target_date:
                        all_articles.append({
                            "title": art.get("Title", ""),
                            "url": art.get("ContentUrl", ""),
                            "publish_time": datetime.fromtimestamp(
                                art.get("send_time", create_time), TZ
                            ).strftime("%Y-%m-%d %H:%M") if art.get("send_time") or create_time else pub_date,
                            "read_count": art.get("Read", 0),
                            "zan_count": art.get("Zan", 0),
                            "digest": art.get("Digest", "")[:200],
                            "cover": art.get("CoverImgUrl", ""),
                            "is_original": art.get("IsOriginal", 0) == 1,
                            "item_show_type": art.get("ItemShowType", 0),
                            "data_source": "dajiala",
                        })

            # 翻页
            is_end = paging.get("IsEnd", 1)
            if is_end == 1:
                break
            offset = paging.get("Offset", "")
            if not offset:
                break

            time.sleep(0.6)  # QPS 控制：每秒最多 2 次

        return all_articles

    # ---------- 精确阅读量 ----------

    def get_read_count(self, url: str) -> dict:
        """按文章 URL 获取精确阅读/点赞/在看数据"""
        data = self._post("read_zan", {"url": url, "key": self.key})
        if data.get("code") != 0:
            return {"read": 0, "zan": 0, "looking": 0}
        d = data.get("data", {})
        self.total_cost += data.get("cost_money", 0.04)
        self.last_balance = data.get("remain_money", self.last_balance)
        return {
            "read": d.get("read", 0),
            "zan": d.get("zan", 0),
            "looking": d.get("looking", 0),
        }

    # ---------- 批量 ----------

    def scan_all(
        self,
        accounts: list[dict],
        target_date: Optional[str] = None,
    ) -> tuple[list[dict], dict]:
        """
        批量扫描所有公众号

        accounts: [{"name": "第一财经", "ghid": "gh_xxx"}, ...]
        返回: (all_articles, cost_summary)
        """
        self.total_cost = 0.0
        all_articles = []
        cost_per_account = {}

        for i, acc in enumerate(accounts):
            name = acc["name"]
            ghid = acc.get("ghid", "")
            if not ghid:
                print(f"  ⏭️ 跳过 (无 ghid): {name}")
                continue

            before = self.total_cost
            print(f"  [{i+1}/{len(accounts)}] {name} ...", end=" ", flush=True)
            articles = self.fetch_articles(ghid, target_date)
            cost = self.total_cost - before
            cost_per_account[name] = cost
            all_articles.extend([{**a, "account": name} for a in articles])
            print(f"{len(articles)} 篇, ¥{cost:.2f}")

            time.sleep(0.6)

        return all_articles, {
            "total_cost": self.total_cost,
            "per_account": cost_per_account,
            "total_articles": len(all_articles),
            "last_balance": self.last_balance,
        }


# ---------- 命令行测试 ----------

if __name__ == "__main__":
    import os
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    cache_path = os.path.join(SCRIPT_DIR, "ghid_cache.json")

    key = "JZLc3be08b3935540ff"
    ghid_cache = json.load(open(cache_path))
    accounts = [{"name": k, "ghid": v["ghid"]} for k, v in ghid_cache.items() if v]

    client = DajialaClient(key)
    articles, summary = client.scan_all(accounts)

    print(f"\n{'='*50}")
    print(f"📊 总文章: {summary['total_articles']} 篇")
    print(f"💰 总消费: ¥{summary['total_cost']:.2f}")
    for name, cost in summary["per_account"].items():
        print(f"   {name}: ¥{cost:.2f}")

    if articles:
        print(f"\n📰 样本 (前 5 篇):")
        for a in articles[:5]:
            print(f"   [{a['account']}] {a['title'][:40]}")
            print(f"      阅读={a['read_count']}, 点赞={a['zan_count']}, {a['publish_time']}")
