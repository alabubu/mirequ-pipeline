#!/usr/bin/env python3
"""
SQLite 数据库模块 — 替代 articles.json

设计要点（吸收自小微方案）:
- msg_key = COALESCE(msg_id, url) 双保险去重
- upsert 语义: 新文章 INSERT, 已有文章 UPDATE 互动数据 + last_seen_time
- is_dedup: 标记本次轮询未出现的文章（被删/下架）
- last_seen_time: 每次轮询更新, 存活检测依据
- 互动数据按维度独立存储（read_count/like_count/...），便于 SQL 环比/排行
"""

import os
import sqlite3
from datetime import datetime
from typing import Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, "articles.db")


# ==================== DDL ====================

DDL = """
CREATE TABLE IF NOT EXISTS articles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_name    TEXT    NOT NULL,
    msg_id          TEXT,                       -- 文章 msg_id（可能为空，url 兜底）
    msg_key         TEXT    NOT NULL UNIQUE,    -- COALESCE(msg_id, url)
    url             TEXT    NOT NULL,
    title           TEXT,
    publish_time    TEXT,                       -- ISO 8601

    -- 互动数据（按维度独立）
    read_count      INTEGER DEFAULT 0,
    like_count      INTEGER DEFAULT 0,
    wonderful_count INTEGER DEFAULT 0,          -- 在看/推荐
    share_count     INTEGER DEFAULT 0,
    collect_count   INTEGER DEFAULT 0,
    comment_count   INTEGER DEFAULT 0,

    -- 元数据（继承自原 articles.json）
    digest          TEXT,
    cover           TEXT,
    data_source     TEXT DEFAULT 'MP-API',       -- MP-API / Client-API / Sogou

    -- 系统字段
    last_seen_time  TEXT    NOT NULL,           -- 最近一次轮询时间
    created_at      TEXT    NOT NULL,           -- 首次发现时间
    updated_at      TEXT    NOT NULL,           -- 互动数据最后更新时间
    is_dedup        INTEGER DEFAULT 0           -- 0=活跃, 1=已下架/重复
);

CREATE INDEX IF NOT EXISTS idx_account ON articles(account_name);
CREATE INDEX IF NOT EXISTS idx_publish_time ON articles(publish_time);
CREATE INDEX IF NOT EXISTS idx_last_seen ON articles(last_seen_time);
CREATE INDEX IF NOT EXISTS idx_is_dedup ON articles(is_dedup);
CREATE INDEX IF NOT EXISTS idx_msg_key ON articles(msg_key);
"""


# ==================== 核心类 ====================

class ArticleDB:
    """文章数据库操作"""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    # ---------- 初始化 ----------

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(DDL)

    # ---------- 核心写入 ----------

    def upsert_article(
        self,
        account_name: str,
        url: str,
        msg_id: Optional[str] = None,
        title: Optional[str] = None,
        publish_time: Optional[str] = None,
        digest: Optional[str] = None,
        cover: Optional[str] = None,
        data_source: str = "MP-API",
        **stats,
    ) -> bool:
        """
        有 msg_id → UPDATE 互动数据 + last_seen_time
        无 msg_id → 用 url 做 msg_key, INSERT 新记录
        返回 True=新插入, False=更新
        """
        msg_key = msg_id or url
        now = datetime.now().isoformat()

        with self._get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM articles WHERE msg_key = ?", (msg_key,)
            ).fetchone()

            if existing:
                # 已存在 → 更新互动数据 + last_seen_time
                conn.execute(
                    """
                    UPDATE articles SET
                        read_count      = COALESCE(?, read_count),
                        like_count      = COALESCE(?, like_count),
                        wonderful_count = COALESCE(?, wonderful_count),
                        share_count     = COALESCE(?, share_count),
                        collect_count   = COALESCE(?, collect_count),
                        comment_count   = COALESCE(?, comment_count),
                        title           = COALESCE(?, title),
                        publish_time    = COALESCE(?, publish_time),
                        last_seen_time  = ?,
                        updated_at      = ?,
                        is_dedup        = 0
                    WHERE id = ?
                    """,
                    (
                        stats.get("read_count"),
                        stats.get("like_count"),
                        stats.get("wonderful_count"),
                        stats.get("share_count"),
                        stats.get("collect_count"),
                        stats.get("comment_count"),
                        title,
                        publish_time,
                        now,
                        now,
                        existing["id"],
                    ),
                )
                return False
            else:
                # 新文章 → INSERT
                conn.execute(
                    """
                    INSERT INTO articles (
                        account_name, msg_id, msg_key, url, title, publish_time,
                        read_count, like_count, wonderful_count,
                        share_count, collect_count, comment_count,
                        digest, cover, data_source,
                        last_seen_time, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_name,
                        msg_id,
                        msg_key,
                        url,
                        title,
                        publish_time,
                        stats.get("read_count", 0),
                        stats.get("like_count", 0),
                        stats.get("wonderful_count", 0),
                        stats.get("share_count", 0),
                        stats.get("collect_count", 0),
                        stats.get("comment_count", 0),
                        digest,
                        cover,
                        data_source,
                        now,
                        now,
                        now,
                    ),
                )
                return True

    def mark_seen(self, account_name: str, url: str):
        """仅更新 last_seen_time（用于存活检测，不修改互动数据）"""
        msg_key = url  # 扫描阶段只有 url
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE articles SET last_seen_time = ? WHERE msg_key = ?",
                (now, msg_key),
            )

    def mark_stale(self, threshold_hours: float = 24.0) -> int:
        """
        将超过 threshold_hours 未出现的活跃文章标记为 is_dedup=1。
        返回标记数量。
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE articles SET is_dedup = 1
                WHERE is_dedup = 0
                  AND last_seen_time < datetime('now', ? || ' hours')
                """,
                (f"-{threshold_hours}",),
            )
            return cursor.rowcount

    # ---------- 查询 ----------

    def count(self, include_dedup: bool = False) -> int:
        """总文章数"""
        where = "" if include_dedup else "WHERE is_dedup = 0"
        with self._get_conn() as conn:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM articles {where}").fetchone()
            return row["cnt"] if row else 0

    def exists(self, msg_key: str) -> bool:
        """检查 msg_key 是否已存在"""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM articles WHERE msg_key = ? LIMIT 1", (msg_key,)
            ).fetchone()
            return row is not None

    def get_active(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """获取活跃文章列表"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM articles
                WHERE is_dedup = 0
                ORDER BY publish_time DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_by_account(self, account_name: str, limit: int = 20) -> list[dict]:
        """按公众号获取文章"""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM articles
                WHERE account_name = ? AND is_dedup = 0
                ORDER BY publish_time DESC
                LIMIT ?
                """,
                (account_name, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_needs_reading_update(self, max_age_hours: float = 4.0, limit: int = 50) -> list[dict]:
        """
        获取需要更新阅读量的文章:
        - read_count = 0 的
        - 或 updated_at 超过 max_age_hours 未更新的
        """
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM articles
                WHERE is_dedup = 0
                  AND (
                      read_count = 0
                      OR updated_at < datetime('now', ? || ' hours')
                  )
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (f"-{max_age_hours}", limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_reading_stats(self, msg_key: str, stats: dict):
        """更新互动数据 + 记录时间戳"""
        now = datetime.now().isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE articles SET
                    read_count      = ?,
                    like_count      = ?,
                    wonderful_count = ?,
                    share_count     = ?,
                    collect_count   = ?,
                    comment_count   = ?,
                    updated_at      = ?
                WHERE msg_key = ?
                """,
                (
                    stats.get("read_count", 0),
                    stats.get("like_count", 0),
                    stats.get("wonderful_count", 0),
                    stats.get("share_count", 0),
                    stats.get("collect_count", 0),
                    stats.get("comment_count", 0),
                    now,
                    msg_key,
                ),
            )

    def stats_summary(self) -> dict:
        """汇总统计"""
        with self._get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM articles WHERE is_dedup = 0"
            ).fetchone()["cnt"]
            stale = conn.execute(
                "SELECT COUNT(*) as cnt FROM articles WHERE is_dedup = 1"
            ).fetchone()["cnt"]
            accounts = conn.execute(
                "SELECT account_name, COUNT(*) as cnt FROM articles WHERE is_dedup = 0 GROUP BY account_name"
            ).fetchall()
            return {
                "total_active": total,
                "total_stale": stale,
                "by_account": {r["account_name"]: r["cnt"] for r in accounts},
            }

    # ---------- 兼容层 ----------

    def export_json(self, path: str):
        """导出为 articles.json 兼容格式（给小程序等下游消费）"""
        import json
        articles = []
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, account_name as account, url, title, publish_time as publishDate,
                       read_count as views, digest, cover, data_source,
                       created_at as createdAt, updated_at as updatedAt
                FROM articles
                WHERE is_dedup = 0
                ORDER BY publish_time DESC
                """
            ).fetchall()
            for r in rows:
                d = dict(r)
                d["id"] = str(d["id"])  # 兼容原字符串 id
                d["note"] = f"[{d.get('data_source','MP-API')}] {d.get('publishDate','')}"
                d["viewHistory"] = []
                articles.append(d)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
