"""SQLite 数据层：users / stories / blocks。

把"开头"也存为一个 block（sequence=0，创世段），所有片段统一在 blocks 表，
链尾 = MAX(sequence)。接龙数 = MAX(sequence)，参与人数 = COUNT(DISTINCT author_id)。
"""

import os
import sqlite3
import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "goodstory.db")

# 业务常量（与 PRD v1.0 决策一致）
MAX_OPENING = 50   # 开头字数上限
MAX_RELAY = 20     # 单次接龙字数上限
MAX_BLOCKS = 50    # 接龙达到该段数自动完结


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")  # 后台线程写库时，读请求最多等 5s 而不是直接报错
    return conn


def init_db():
    conn = get_db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname   TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS stories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            title      TEXT NOT NULL,
            creator_id INTEGER NOT NULL REFERENCES users(id),
            status     TEXT NOT NULL DEFAULT 'ongoing',
            ai_content TEXT NOT NULL DEFAULT '',
            ai_status  TEXT NOT NULL DEFAULT 'idle',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS blocks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id    INTEGER NOT NULL REFERENCES stories(id),
            sequence    INTEGER NOT NULL,
            raw_content TEXT NOT NULL,
            author_id   INTEGER NOT NULL REFERENCES users(id),
            created_at  TEXT NOT NULL,
            ai_review   TEXT NOT NULL DEFAULT '',
            UNIQUE(story_id, sequence)
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            block_id   INTEGER NOT NULL REFERENCES blocks(id),
            user_id    INTEGER NOT NULL REFERENCES users(id),
            kind       TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(block_id, user_id)
        );
        """
    )
    # 兼容旧库：补 ai_status 列（旧版本建的表没有该列）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(stories)").fetchall()]
    if "ai_status" not in cols:
        conn.execute("ALTER TABLE stories ADD COLUMN ai_status TEXT NOT NULL DEFAULT 'idle'")
    bcols = [r[1] for r in conn.execute("PRAGMA table_info(blocks)").fetchall()]
    if "ai_review" not in bcols:
        conn.execute("ALTER TABLE blocks ADD COLUMN ai_review TEXT NOT NULL DEFAULT ''")
    conn.commit()
    conn.close()


# ---------- 用户 ----------

def get_or_create_user(nickname):
    nickname = nickname.strip()[:20]
    conn = get_db()
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM users WHERE nickname = ?", (nickname,)).fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO users (nickname, created_at) VALUES (?, ?)",
            (nickname, _now()),
        )
        conn.commit()
        row = cur.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
    user = dict(row)
    conn.close()
    return user


# ---------- 故事 ----------

def _make_title(opening):
    t = opening.strip().replace("\n", " ")
    return (t[:16] + "…") if len(t) > 16 else t


def create_story(opening, creator_id):
    """创建故事，并写入开头（sequence=0）。返回 story_id。"""
    opening = opening.strip()
    title = _make_title(opening)
    ts = _now()
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO stories (title, creator_id, status, ai_content, created_at, updated_at) "
        "VALUES (?, ?, 'ongoing', '', ?, ?)",
        (title, creator_id, ts, ts),
    )
    story_id = cur.lastrowid
    cur.execute(
        "INSERT INTO blocks (story_id, sequence, raw_content, author_id, created_at) "
        "VALUES (?, 0, ?, ?, ?)",
        (story_id, opening, creator_id, ts),
    )
    conn.commit()
    conn.close()
    return story_id


_STORY_SELECT = """
    SELECT s.*,
        (SELECT MAX(sequence) FROM blocks WHERE story_id = s.id) AS block_count,
        (SELECT COUNT(DISTINCT author_id) FROM blocks WHERE story_id = s.id) AS participant_count,
        u.nickname AS creator_name
    FROM stories s
    JOIN users u ON u.id = s.creator_id
"""


def list_stories():
    conn = get_db()
    rows = conn.execute(
        _STORY_SELECT + " ORDER BY s.updated_at DESC, s.id DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_story(story_id):
    conn = get_db()
    row = conn.execute(_STORY_SELECT + " WHERE s.id = ?", (story_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_ai_content(story_id, text):
    conn = get_db()
    conn.execute("UPDATE stories SET ai_content = ? WHERE id = ?", (text, story_id))
    conn.commit()
    conn.close()


def set_ai_status(story_id, status):
    """status: 'pending'（生成中）/ 'idle'（已就绪）。"""
    conn = get_db()
    conn.execute("UPDATE stories SET ai_status = ? WHERE id = ?", (status, story_id))
    conn.commit()
    conn.close()


def finish_story(story_id, user_id):
    conn = get_db()
    cur = conn.cursor()
    s = cur.execute("SELECT creator_id, status FROM stories WHERE id = ?", (story_id,)).fetchone()
    if s is None:
        conn.close()
        return {"ok": False, "msg": "故事不存在"}
    if s["creator_id"] != user_id:
        conn.close()
        return {"ok": False, "msg": "只有发起人可以完结故事"}
    if s["status"] != "ongoing":
        conn.close()
        return {"ok": False, "msg": "故事已完结"}
    cur.execute("UPDATE stories SET status = 'finished', updated_at = ? WHERE id = ?", (_now(), story_id))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------- 区块（接龙片段） ----------

def get_blocks(story_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT b.*, u.nickname AS author_name "
        "FROM blocks b JOIN users u ON u.id = b.author_id "
        "WHERE b.story_id = ? ORDER BY b.sequence ASC",
        (story_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tail(story_id):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM blocks WHERE story_id = ? ORDER BY sequence DESC LIMIT 1",
        (story_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_block(block_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM blocks WHERE id = ?", (block_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_block_review(block_id, text):
    conn = get_db()
    conn.execute("UPDATE blocks SET ai_review = ? WHERE id = ?", (text, block_id))
    conn.commit()
    conn.close()


def add_block(story_id, expected_sequence, content, author_id):
    """追加一个接龙片段。

    返回 dict：成功 {ok:True, sequence, finished}；失败 {ok:False, error}
    error ∈ not_found / finished / consecutive / conflict
    """
    content = content.strip()
    conn = get_db()
    cur = conn.cursor()
    try:
        story = cur.execute("SELECT status FROM stories WHERE id = ?", (story_id,)).fetchone()
        if story is None:
            return {"ok": False, "error": "not_found"}
        if story["status"] != "ongoing":
            return {"ok": False, "error": "finished"}

        tail = cur.execute(
            "SELECT sequence, author_id FROM blocks WHERE story_id = ? ORDER BY sequence DESC LIMIT 1",
            (story_id,),
        ).fetchone()
        tail_seq = tail["sequence"] if tail else -1
        tail_author = tail["author_id"] if tail else None

        # 不允许连续接龙
        if tail_author is not None and tail_author == author_id:
            return {"ok": False, "error": "consecutive"}

        # 并发链尾校验
        if expected_sequence is not None and expected_sequence != tail_seq:
            return {"ok": False, "error": "conflict", "tail_sequence": tail_seq}

        new_seq = tail_seq + 1
        ts = _now()
        # UNIQUE(story_id, sequence) 兜底并发：两人同时插入同一 sequence，必失败其一
        cur.execute(
            "INSERT INTO blocks (story_id, sequence, raw_content, author_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (story_id, new_seq, content, author_id, ts),
        )
        new_block_id = cur.lastrowid
        finished = new_seq >= MAX_BLOCKS
        new_status = "finished" if finished else "ongoing"
        cur.execute(
            "UPDATE stories SET updated_at = ?, status = ? WHERE id = ?",
            (ts, new_status, story_id),
        )
        conn.commit()
        return {"ok": True, "sequence": new_seq, "finished": finished, "block_id": new_block_id}
    except sqlite3.IntegrityError:
        # 并发抢同一 sequence
        return {"ok": False, "error": "conflict"}
    finally:
        conn.close()


# ---------- 评价（好评 / 差评） ----------

def rate_block(block_id, user_id, kind):
    """好评/差评。再次点同一类型 = 取消(toggle)；点另一类型 = 切换。"""
    conn = get_db()
    cur = conn.cursor()
    if cur.execute("SELECT id FROM blocks WHERE id = ?", (block_id,)).fetchone() is None:
        conn.close()
        return {"ok": False, "error": "not_found"}
    existing = cur.execute(
        "SELECT kind FROM ratings WHERE block_id = ? AND user_id = ?", (block_id, user_id)
    ).fetchone()
    if existing is None:
        cur.execute(
            "INSERT INTO ratings (block_id, user_id, kind, created_at) VALUES (?, ?, ?, ?)",
            (block_id, user_id, kind, _now()),
        )
    elif existing["kind"] == kind:
        cur.execute("DELETE FROM ratings WHERE block_id = ? AND user_id = ?", (block_id, user_id))
    else:
        cur.execute(
            "UPDATE ratings SET kind = ?, created_at = ? WHERE block_id = ? AND user_id = ?",
            (kind, _now(), block_id, user_id),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


def get_block_counts(block_id):
    conn = get_db()
    row = conn.execute(
        "SELECT SUM(kind = 'good') AS good, SUM(kind = 'bad') AS bad FROM ratings WHERE block_id = ?",
        (block_id,),
    ).fetchone()
    conn.close()
    return {"good": row["good"] or 0, "bad": row["bad"] or 0}


def get_user_vote(block_id, user_id):
    conn = get_db()
    row = conn.execute(
        "SELECT kind FROM ratings WHERE block_id = ? AND user_id = ?", (block_id, user_id)
    ).fetchone()
    conn.close()
    return row["kind"] if row else None


def get_ratings(story_id, user_id=None):
    """返回 (counts, mine)：counts[block_id]={good,bad}；mine[block_id]=kind。"""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT b.id AS block_id,
               SUM(CASE WHEN r.kind = 'good' THEN 1 ELSE 0 END) AS good,
               SUM(CASE WHEN r.kind = 'bad'  THEN 1 ELSE 0 END) AS bad
        FROM blocks b LEFT JOIN ratings r ON r.block_id = b.id
        WHERE b.story_id = ?
        GROUP BY b.id
        """,
        (story_id,),
    ).fetchall()
    counts = {r["block_id"]: {"good": r["good"] or 0, "bad": r["bad"] or 0} for r in rows}
    mine = {}
    if user_id:
        mrows = conn.execute(
            "SELECT r.block_id, r.kind FROM ratings r JOIN blocks b ON b.id = r.block_id "
            "WHERE b.story_id = ? AND r.user_id = ?",
            (story_id, user_id),
        ).fetchall()
        mine = {r["block_id"]: r["kind"] for r in mrows}
    conn.close()
    return counts, mine
