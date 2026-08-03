#!/usr/bin/env python3
"""
公众号文章扫描器 v3 — 基于微信公众平台后台 API
- searchbiz: 搜索公众号获取 fakeid
- appmsg:    拉取文章列表（标题、链接、时间、摘要）
- 限流控制:  每页间隔 180 秒，账号间间隔 10 秒
- 去重存储:  以文章链接为 key，增量追加
- 产出格式:  articles.json，兼容小程序

用法:
  首次/刷新 fakeid:  python scanner.py --resolve
  日常扫描:          python scanner.py

配置:
  config.json  → cookie, token, 限流参数
  accounts.json → 公众号名单（首次 scan 后自动缓存 fakeid）
"""

import json
import os
import re
import hashlib
import random
import sys
import time
from datetime import datetime
from urllib.parse import urlencode

import requests

# ================= 路径 =================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
ACCOUNTS_FILE = os.path.join(SCRIPT_DIR, "accounts.json")
ARTICLES_FILE = os.path.join(SCRIPT_DIR, "articles.json")  # 兼容导出
LOG_FILE = os.path.join(SCRIPT_DIR, "scanner.log")
FAKEID_CACHE = os.path.join(SCRIPT_DIR, "fakeid_cache.json")

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
]


# ================= 工具 =================
def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def generate_id():
    raw = f"{datetime.now().timestamp()}{random.random()}"
    return hashlib.md5(raw.encode()).hexdigest()[:16]


def normalize_url(url: str) -> str:
    """标准化文章 URL 用于去重"""
    m = re.match(r"(https?://mp\.weixin\.qq\.com/s\?[^#]+)", url)
    if m:
        return m.group(1)
    m = re.match(r"(https?://mp\.weixin\.qq\.com/s/[\w\-]+)", url)
    if m:
        return m.group(1)
    return url.rstrip("/")


def extract_sn(url: str) -> str:
    """从文章 URL 提取 sn (唯一标识)"""
    m = re.search(r"[?&]sn=([a-f0-9]+)", url)
    if m:
        return m.group(1)
    m = re.search(r"/s/([\w\-]+)", url)
    if m:
        return m.group(1)
    return hashlib.md5(url.encode()).hexdigest()[:16]


# ================= 配置 =================
class Config:
    def __init__(self):
        data = load_json(CONFIG_FILE, {})
        self.cookie = data.get("cookie", "")
        self.token = data.get("token", "")
        self.page_interval = data.get("page_interval", 180)
        self.account_interval = data.get("account_interval", 10)
        self.max_pages = data.get("max_pages", 3)
        self.count_per_page = data.get("count_per_page", 5)

    @property
    def headers(self):
        return {
            "Cookie": self.cookie,
            "User-Agent": random.choice(USER_AGENTS),
            "Referer": "https://mp.weixin.qq.com/",
        }

    def is_valid(self) -> bool:
        return bool(self.cookie and self.token)


# ================= MP API =================
class MPApi:
    """微信公众平台后台 API 封装"""

    BASE = "https://mp.weixin.qq.com"

    def __init__(self, config: Config):
        self.cfg = config
        self.s = requests.Session()

    def search_biz(self, query: str) -> list[dict]:
        """搜索公众号，返回 [{fakeid, nickname, ...}]"""
        try:
            r = self.s.get(
                f"{self.BASE}/cgi-bin/searchbiz",
                params={
                    "action": "search_biz",
                    "begin": "0",
                    "count": "1",
                    "query": query,
                    "token": self.cfg.token,
                    "lang": "zh_CN",
                    "f": "json",
                    "ajax": "1",
                },
                headers=self.cfg.headers,
                timeout=20,
            )
            data = r.json()
            if data.get("base_resp", {}).get("ret") == 0:
                return data.get("list", [])
        except Exception as e:
            log(f"  ⚠️ search_biz({query}) 失败: {e}")
        return []

    def get_articles(
        self, fakeid: str, begin: int = 0, count: int = 5
    ) -> tuple[int, list[dict]]:
        """
        拉取文章列表
        返回 (total_count, [{title, link, update_time, digest, ...}])
        """
        try:
            r = self.s.get(
                f"{self.BASE}/cgi-bin/appmsg",
                params={
                    "action": "list_ex",
                    "begin": str(begin),
                    "count": str(count),
                    "fakeid": fakeid,
                    "type": "9",
                    "token": self.cfg.token,
                    "lang": "zh_CN",
                    "f": "json",
                    "ajax": "1",
                },
                headers=self.cfg.headers,
                timeout=20,
            )
            data = r.json()
            ret = data.get("base_resp", {}).get("ret", -1)
            if ret == 200013:  # freq control
                return -1, []
            if ret != 0:
                log(f"  ⚠️ appmsg ret={ret}: {data.get('base_resp', {}).get('err_msg', '')}")
                return 0, []
            total = data.get("app_msg_cnt", 0)
            articles = data.get("app_msg_list", [])
            return total, articles
        except Exception as e:
            log(f"  ⚠️ appmsg 失败: {e}")
        return 0, []


# ================= 主流程 =================
def resolve_fakeids(api: MPApi, accounts: list[dict]) -> dict:
    """为每个公众号查找 fakeid，缓存到文件"""
    cache = load_json(FAKEID_CACHE, {})
    new_count = 0

    for acc in accounts:
        name = acc["name"]
        if name in cache and cache[name]:
            continue

        log(f"🔍 查找: {name}")
        results = api.search_biz(name)
        if results:
            fid = results[0]["fakeid"]
            cache[name] = fid
            log(f"  ✅ fakeid={fid}, 匹配={results[0]['nickname']}")
            new_count += 1
            time.sleep(2)  # searchbiz 之间轻微间隔
        else:
            log(f"  ❌ 未找到: {name}")

    if new_count > 0:
        save_json(FAKEID_CACHE, cache)
        log(f"💾 缓存了 {new_count} 个新 fakeid")

    return cache


def scan_all(api: MPApi, accounts: list[dict], fakeids: dict):
    """扫描所有公众号的文章，写入 SQLite"""
    from db import ArticleDB

    db = ArticleDB()

    new_total = 0
    found_total = 0

    for acc in accounts:
        name = acc["name"]
        fid = fakeids.get(name)

        if not fid:
            log(f"⏭️ 跳过 (无 fakeid): {name}")
            continue

        log(f"📢 {name}")

        acc_new = 0
        for page in range(cfg.max_pages):
            begin = page * cfg.count_per_page
            total, articles = api.get_articles(fid, begin, cfg.count_per_page)

            if total == -1:  # freq control
                log(f"  🚫 被限流，等待 {cfg.page_interval}s ...")
                time.sleep(cfg.page_interval)
                total, articles = api.get_articles(fid, begin, cfg.count_per_page)
                if total == -1:
                    log(f"  ❌ 仍然限流，跳过 {name}")
                    break

            if page == 0:
                log(f"  共 {total} 篇")

            if not articles:
                break

            for a in articles:
                found_total += 1
                url = a.get("link", "")
                msg_key = url  # MP API 不返回 msg_id，用 url 做 msg_key

                # sqlite 层去重
                if db.exists(msg_key):
                    # 文章已存在，刷新 last_seen_time（防止被误标 stale）
                    db.mark_seen(name, url)
                    continue

                ts = a.get("update_time", 0)
                pub_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""

                is_new = db.upsert_article(
                    account_name=name,
                    url=url,
                    title=a.get("title", ""),
                    publish_time=pub_date,
                    digest=a.get("digest", "")[:200],
                    cover=a.get("cover", ""),
                    data_source="MP-API",
                )

                if is_new:
                    new_total += 1
                    acc_new += 1

                log(f"  {'✅' if is_new else '🔄'} {a.get('title', '?')[:50]}")

            # 页间间隔（限流控制）
            if len(articles) >= cfg.count_per_page:
                time.sleep(cfg.page_interval)
            else:
                break

        if acc_new > 0:
            log(f"  📊 +{acc_new} 篇新文章")

        # 账号间间隔
        time.sleep(cfg.account_interval)

    # 存活检测：标记本轮未出现的文章（超过 24h 未出现 → is_dedup=1）
    stale_count = db.mark_stale(threshold_hours=24.0)
    if stale_count > 0:
        log(f"🧹 标记 {stale_count} 篇为过期（24h 未出现）")

    total = db.count()
    log(f"📊 本轮: 发现 {found_total} 篇, 新增 {new_total} 篇, 总计 {total} 篇（活跃）")

    # 兼容导出: 生成 articles.json 供小程序等下游消费
    if new_total > 0 or stale_count > 0:
        db.export_json(ARTICLES_FILE)
        log(f"📤 已导出 articles.json")


# ================= 入口 =================
if __name__ == "__main__":
    log("=" * 40)

    # 自动认证（首次弹出 Chrome 扫码，后续复用凭证）
    from auth import WxAuth
    try:
        auth = WxAuth()
        cookie, token = auth.ensure_credentials()
        # 写入 Config 对象供后续使用
        new_config = auth._config
        save_json(CONFIG_FILE, new_config)
    except RuntimeError as e:
        log(f"❌ 认证失败: {e}")
        sys.exit(1)

    # 加载配置
    cfg = Config()
    if not cfg.is_valid():
        log("❌ 缺少 cookie/token")
        sys.exit(1)

    # 加载账号
    accounts_data = load_json(ACCOUNTS_FILE, {})
    accounts = accounts_data.get("accounts", [])
    if not accounts:
        log("❌ 未配置公众号，请编辑 scanner/accounts.json")
        sys.exit(1)

    log(f"🚀 开始扫描 {len(accounts)} 个公众号")
    log(f"   限流: {cfg.page_interval}s/页, {cfg.account_interval}s/号, 最多{cfg.max_pages}页")

    api = MPApi(cfg)

    # 解析 fakeid（一次性的，缓存后下次跳过）
    resolve_mode = "--resolve" in sys.argv
    fakeids = load_json(FAKEID_CACHE, {})

    if resolve_mode or not fakeids:
        log("🔍 解析 fakeid ...")
        fakeids = resolve_fakeids(api, accounts)

    scan_all(api, accounts, fakeids)

    # 尝试更新阅读量（如果配置了 reading_config.json）
    try:
        from reading import batch_update as update_readings
        log("📊 尝试更新阅读量...")
        update_readings(interval=8, limit=10)
    except Exception:
        pass  # 阅读量更新是可选的

    log("✅ 完成\n")
