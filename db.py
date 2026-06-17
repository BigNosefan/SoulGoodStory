"""数据层：Upstash Redis（通过 REST API + 标准库 urllib，无需额外依赖）。

需要环境变量（由 app.py 从 .env 加载，Vercel 在后台配置）：
  UPSTASH_REDIS_REST_URL   例：https://xxx.upstash.io
  UPSTASH_REDIS_REST_TOKEN

所有键以 gs: 前缀命名，避免与同库其它数据冲突。对外函数签名与原 SQLite 版保持一致，
app.py 无需改动。把"开头"也存为 sequence=0 的区块；故事/区块的聚合字段做了反范式缓存，
以便列表页一次读取。
"""

import os
import json
import time
import datetime
import urllib.request

MAX_OPENING = 50   # 开头字数上限
MAX_RELAY = 20     # 单次接龙字数上限
MAX_BLOCKS = 50    # 接龙达到该段数自动完结

_PREFIX = "gs:"


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _k(*parts):
    return _PREFIX + ":".join(str(p) for p in parts)


# ---------- Upstash REST 封装 ----------

def _conf():
    url = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
    if not url or not token:
        raise RuntimeError("缺少 UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN 环境变量")
    return url, token


def _post(path, payload):
    url, token = _conf()
    req = urllib.request.Request(
        url + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def r(*args):
    """执行单条 Redis 命令，返回 result。"""
    data = _post("/", [str(a) for a in args])
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError("Redis: " + str(data["error"]))
    return data.get("result") if isinstance(data, dict) else data


def rpipe(cmds):
    """批量执行命令，返回 result 列表（顺序对应）。"""
    if not cmds:
        return []
    payload = [[str(a) for a in cmd] for cmd in cmds]
    data = _post("/pipeline", payload)
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("error"):
            raise RuntimeError("Redis: " + str(item["error"]))
        out.append(item.get("result") if isinstance(item, dict) else item)
    return out


def _dict(flat):
    """HGETALL 的扁平数组 [k1,v1,k2,v2] -> dict。"""
    if not flat:
        return {}
    return {flat[i]: flat[i + 1] for i in range(0, len(flat), 2)}


def init_db():
    # Redis 无需建表；连通性在首次真实命令时校验。
    return


def claim_seed():
    """并发冷启动时只让一个实例播种种子数据。"""
    return bool(r("SET", _k("seeded"), "1", "NX"))


# ---------- 序列化 ----------

def _story_dict(d):
    return {
        "id": int(d["id"]),
        "title": d.get("title", ""),
        "creator_id": int(d.get("creator_id", 0)),
        "creator_name": d.get("creator_name", ""),
        "status": d.get("status", "ongoing"),
        "ai_content": d.get("ai_content", ""),
        "ai_status": d.get("ai_status", "idle"),
        "block_count": int(d.get("block_count", 0)),
        "participant_count": int(d.get("participant_count", 0)),
        "created_at": d.get("created_at", ""),
        "updated_at": d.get("updated_at", ""),
    }


def _block_dict(d):
    return {
        "id": int(d["id"]),
        "story_id": int(d.get("story_id", 0)),
        "sequence": int(d.get("sequence", 0)),
        "raw_content": d.get("raw_content", ""),
        "author_id": int(d.get("author_id", 0)),
        "author_name": d.get("author_name", ""),
        "created_at": d.get("created_at", ""),
        "ai_review": d.get("ai_review", ""),
    }


# ---------- 用户 ----------

def get_or_create_user(nickname):
    nickname = nickname.strip()[:20]
    uid = r("GET", _k("nick", nickname))
    if uid:
        return {"id": int(uid), "nickname": nickname}
    uid = r("INCR", _k("seq", "user"))
    r("HSET", _k("user", uid), "id", uid, "nickname", nickname, "created_at", _now())
    r("SET", _k("nick", nickname), uid)
    return {"id": int(uid), "nickname": nickname}


def _user_name(uid):
    return r("HGET", _k("user", uid), "nickname") or ""


# ---------- 故事 ----------

def _make_title(opening):
    t = opening.strip().replace("\n", " ")
    return (t[:16] + "…") if len(t) > 16 else t


def _append_block(story_id, seq, content, author_id, author_name, ts):
    bid = r("INCR", _k("seq", "block"))
    r("HSET", _k("block", bid),
      "id", bid, "story_id", story_id, "sequence", seq,
      "raw_content", content, "author_id", author_id, "author_name", author_name,
      "created_at", ts, "ai_review", "")
    r("RPUSH", _k("story", story_id, "blocks"), bid)
    return int(bid)


def create_story(opening, creator_id):
    opening = opening.strip()
    creator_name = _user_name(creator_id)
    sid = r("INCR", _k("seq", "story"))
    ts = _now()
    r("HSET", _k("story", sid),
      "id", sid, "title", _make_title(opening), "creator_id", creator_id,
      "creator_name", creator_name, "status", "ongoing",
      "ai_content", "", "ai_status", "idle",
      "block_count", 0, "participant_count", 1,
      "created_at", ts, "updated_at", ts)
    r("SADD", _k("story", sid, "authors"), creator_id)
    r("ZADD", _k("stories"), time.time(), sid)
    _append_block(sid, 0, opening, creator_id, creator_name, ts)  # 开头 = 创世块 seq 0
    return int(sid)


def list_stories():
    ids = r("ZREVRANGE", _k("stories"), 0, -1) or []
    if not ids:
        return []
    res = rpipe([["HGETALL", _k("story", sid)] for sid in ids])
    return [_story_dict(_dict(flat)) for flat in res if flat]


def get_story(story_id):
    d = _dict(r("HGETALL", _k("story", story_id)))
    return _story_dict(d) if d else None


def update_ai_content(story_id, text):
    r("HSET", _k("story", story_id), "ai_content", text)


def set_ai_status(story_id, status):
    r("HSET", _k("story", story_id), "ai_status", status)


def finish_story(story_id, user_id):
    d = _dict(r("HGETALL", _k("story", story_id)))
    if not d:
        return {"ok": False, "msg": "故事不存在"}
    if int(d.get("creator_id", 0)) != user_id:
        return {"ok": False, "msg": "只有发起人可以完结故事"}
    if d.get("status") != "ongoing":
        return {"ok": False, "msg": "故事已完结"}
    r("HSET", _k("story", story_id), "status", "finished", "updated_at", _now())
    r("ZADD", _k("stories"), time.time(), story_id)
    return {"ok": True}


# ---------- 区块（接龙片段） ----------

def get_blocks(story_id):
    ids = r("LRANGE", _k("story", story_id, "blocks"), 0, -1) or []
    if not ids:
        return []
    res = rpipe([["HGETALL", _k("block", bid)] for bid in ids])
    return [_block_dict(_dict(flat)) for flat in res if flat]


def get_tail(story_id):
    bid = r("LINDEX", _k("story", story_id, "blocks"), -1)
    if not bid:
        return None
    d = _dict(r("HGETALL", _k("block", bid)))
    return _block_dict(d) if d else None


def get_block(block_id):
    d = _dict(r("HGETALL", _k("block", block_id)))
    return _block_dict(d) if d else None


def set_block_review(block_id, text):
    r("HSET", _k("block", block_id), "ai_review", text)


def add_block(story_id, expected_sequence, content, author_id):
    """追加接龙片段。返回 {ok, sequence, finished, block_id} 或 {ok:False, error}。

    注：Redis REST 无事务，链尾校验为读后写的乐观并发（demo 并发量低，足够）。
    """
    content = content.strip()
    story = _dict(r("HGETALL", _k("story", story_id)))
    if not story:
        return {"ok": False, "error": "not_found"}
    if story.get("status") != "ongoing":
        return {"ok": False, "error": "finished"}

    tail = get_tail(story_id)
    tail_seq = tail["sequence"] if tail else -1
    tail_author = tail["author_id"] if tail else None

    if tail_author is not None and tail_author == author_id:
        return {"ok": False, "error": "consecutive"}
    if expected_sequence is not None and expected_sequence != tail_seq:
        return {"ok": False, "error": "conflict", "tail_sequence": tail_seq}

    new_seq = tail_seq + 1
    ts = _now()
    bid = _append_block(story_id, new_seq, content, author_id, _user_name(author_id), ts)

    finished = new_seq >= MAX_BLOCKS
    is_new_author = r("SADD", _k("story", story_id, "authors"), author_id)
    fields = ["updated_at", ts, "status", "finished" if finished else "ongoing", "block_count", new_seq]
    if is_new_author:
        fields += ["participant_count", int(story.get("participant_count", 1)) + 1]
    r("HSET", _k("story", story_id), *fields)
    r("ZADD", _k("stories"), time.time(), story_id)
    return {"ok": True, "sequence": new_seq, "finished": finished, "block_id": bid}


# ---------- 评价（好评 / 差评） ----------

def rate_block(block_id, user_id, kind):
    """再点同一类型 = 取消(toggle)；点另一类型 = 切换。"""
    if not r("EXISTS", _k("block", block_id)):
        return {"ok": False, "error": "not_found"}
    key = _k("rate", block_id)
    existing = r("HGET", key, user_id)
    if existing is None:
        r("HSET", key, user_id, kind)
    elif existing == kind:
        r("HDEL", key, user_id)
    else:
        r("HSET", key, user_id, kind)
    return {"ok": True}


def _count(d):
    vals = list(d.values())
    return {"good": vals.count("good"), "bad": vals.count("bad")}


def get_block_counts(block_id):
    return _count(_dict(r("HGETALL", _k("rate", block_id))))


def get_user_vote(block_id, user_id):
    return r("HGET", _k("rate", block_id), user_id)


def get_ratings(story_id, user_id=None):
    """返回 (counts, mine)：counts[block_id]={good,bad}；mine[block_id]=kind。"""
    ids = r("LRANGE", _k("story", story_id, "blocks"), 0, -1) or []
    counts, mine = {}, {}
    if not ids:
        return counts, mine
    res = rpipe([["HGETALL", _k("rate", bid)] for bid in ids])
    for bid, flat in zip(ids, res):
        d = _dict(flat)
        counts[int(bid)] = _count(d)
        if user_id is not None and d.get(str(user_id)):
            mine[int(bid)] = d[str(user_id)]
    return counts, mine
