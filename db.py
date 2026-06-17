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
            UNIQUE(story_id, sequence)
        );
        """
    )
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
        finished = new_seq >= MAX_BLOCKS
        new_status = "finished" if finished else "ongoing"
        cur.execute(
            "UPDATE stories SET updated_at = ?, status = ? WHERE id = ?",
            (ts, new_status, story_id),
        )
        conn.commit()
        return {"ok": True, "sequence": new_seq, "finished": finished}
    except sqlite3.IntegrityError:
        # 并发抢同一 sequence
        return {"ok": False, "error": "conflict"}
    finally:
        conn.close()
