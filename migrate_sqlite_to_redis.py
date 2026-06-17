"""一次性迁移：把本地 SQLite(goodstory.db) 的数据导入 Upstash Redis。

用法（项目目录下，确保 .env 里有 UPSTASH_REDIS_REST_URL / TOKEN）：
    python migrate_sqlite_to_redis.py            # 预览(dry-run)，只统计不写入
    python migrate_sqlite_to_redis.py --yes      # 真正迁移：先清空 Redis 的 gs:* 命名空间，再按本地数据写入

保留原始 id；故事/区块的派生字段（creator_name/author_name/block_count/
participant_count/authors/blocks 列表/stories 有序集）在迁移时一并重建，
键格式与 db.py 完全一致。
"""

import os
import sys
import sqlite3
import datetime
import time
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "goodstory.db")


def _load_dotenv():
    path = os.path.join(BASE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
import db  # 复用 db.py 的 Redis 封装（r / rpipe / _k）与键格式


def _epoch(ts):
    try:
        return datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return time.time()


def _col(row, key, default=""):
    return row[key] if key in row.keys() else default


def clear_namespace():
    cursor, total = "0", 0
    while True:
        cursor, keys = db.r("SCAN", cursor, "MATCH", "gs:*", "COUNT", 300)
        if keys:
            db.r("DEL", *keys)
            total += len(keys)
        if cursor == "0":
            break
    return total


def main(apply):
    if not os.path.exists(DB_PATH):
        print("找不到", DB_PATH)
        return
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    users = conn.execute("SELECT * FROM users").fetchall()
    stories = conn.execute("SELECT * FROM stories").fetchall()
    blocks = conn.execute("SELECT * FROM blocks ORDER BY story_id, sequence").fetchall()
    try:
        ratings = conn.execute("SELECT * FROM ratings").fetchall()
    except sqlite3.OperationalError:
        ratings = []

    print(f"SQLite 数据：用户 {len(users)} / 故事 {len(stories)} / 区块 {len(blocks)} / 评价 {len(ratings)}")

    if not apply:
        print("\n这是预览(dry-run)，未写入。加 --yes 执行迁移（会先清空 Redis 的 gs:* 命名空间）。")
        return

    print("清空 Redis gs:* 命名空间 ...")
    print(f"  已删除 {clear_namespace()} 个键")

    nick = {u["id"]: u["nickname"] for u in users}

    # 用户
    for u in users:
        db.r("HSET", db._k("user", u["id"]),
             "id", u["id"], "nickname", u["nickname"], "created_at", _col(u, "created_at"))
        db.r("SET", db._k("nick", u["nickname"]), u["id"])

    by_story = defaultdict(list)
    for b in blocks:
        by_story[b["story_id"]].append(b)

    # 故事 + 区块
    for s in stories:
        sid = s["id"]
        bs = by_story.get(sid, [])
        block_count = max((b["sequence"] for b in bs), default=0)
        authors = {b["author_id"] for b in bs}
        ai_content = _col(s, "ai_content")
        ai_status = "idle" if ai_content else "pending"  # 没正文的留 pending，进页面会自动补生成
        db.r("HSET", db._k("story", sid),
             "id", sid, "title", s["title"], "creator_id", s["creator_id"],
             "creator_name", nick.get(s["creator_id"], ""),
             "status", _col(s, "status", "ongoing"),
             "ai_content", ai_content, "ai_status", ai_status,
             "block_count", block_count, "participant_count", len(authors) or 1,
             "created_at", _col(s, "created_at"), "updated_at", _col(s, "updated_at"))
        if authors:
            db.r("SADD", db._k("story", sid, "authors"), *authors)
        db.r("ZADD", db._k("stories"), _epoch(_col(s, "updated_at")), sid)
        for b in bs:
            db.r("HSET", db._k("block", b["id"]),
                 "id", b["id"], "story_id", b["story_id"], "sequence", b["sequence"],
                 "raw_content", b["raw_content"], "author_id", b["author_id"],
                 "author_name", nick.get(b["author_id"], ""),
                 "created_at", _col(b, "created_at"), "ai_review", _col(b, "ai_review"))
        if bs:
            db.r("RPUSH", db._k("story", sid, "blocks"), *[b["id"] for b in bs])

    # 评价
    for rt in ratings:
        db.r("HSET", db._k("rate", rt["block_id"]), rt["user_id"], rt["kind"])

    # 自增计数器对齐到最大 id，避免后续新建撞 id
    if users:
        db.r("SET", db._k("seq", "user"), max(u["id"] for u in users))
    if stories:
        db.r("SET", db._k("seq", "story"), max(s["id"] for s in stories))
    if blocks:
        db.r("SET", db._k("seq", "block"), max(b["id"] for b in blocks))
    # 标记已播种，避免应用再插入示例故事
    db.r("SET", db._k("seeded"), "1")

    print(f"\n迁移完成：写入 用户 {len(users)} / 故事 {len(stories)} / 区块 {len(blocks)} / 评价 {len(ratings)}")
    print("Redis 现有故事数 ZCARD gs:stories =", db.r("ZCARD", db._k("stories")))


if __name__ == "__main__":
    main(apply="--yes" in sys.argv)
