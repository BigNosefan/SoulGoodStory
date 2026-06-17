"""好故事（GoodStory）demo —— Flask 本地服务器。

运行：python app.py  （默认 http://localhost:5001）
"""

import os
import threading

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, abort,
)

import db
import ai


def _load_dotenv():
    """轻量读取项目根目录 .env（KEY=VALUE），不覆盖已存在的环境变量。无需第三方依赖。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("GOODSTORY_SECRET", "dev-secret-change-me")


@app.context_processor
def _inject_ai_provider():
    # 让所有模板都能显示"当前用的是哪个串联引擎"
    return {"ai_provider": ai.provider_label()}

# 评价文案（好评/差评各 5 条，前端每条接龙随机展示一条）
GOOD_REVIEWS = ["神来一笔", "妙笔生花", "封神操作", "脑洞清奇", "全场最佳"]
BAD_REVIEWS = ["注水文", "强行尬接", "逻辑崩坏", "跑题预警", "平平无奇"]

# 接龙失败原因 -> 用户提示
RELAY_ERRORS = {
    "not_found": "故事不存在",
    "finished": "故事已完结，无法接龙",
    "consecutive": "不能连续接龙，请等待其他人接龙后再继续",
    "conflict": "已有新的接龙，故事已更新，请基于最新内容重新接龙",
}


# ---------- 辅助 ----------

def current_user():
    if "user_id" in session:
        return {"id": session["user_id"], "nickname": session.get("nickname", "")}
    return None


def _generate_ai(story_id):
    """实际调用 AI 串联并写回（可能耗时数秒）。结束时把状态置回 idle。"""
    try:
        blocks = db.get_blocks(story_id)
        if blocks:
            opening = blocks[0]["raw_content"]
            segments = [b["raw_content"] for b in blocks[1:]]
            db.update_ai_content(story_id, ai.stitch(opening, segments))
    finally:
        db.set_ai_status(story_id, "idle")


def kick_ai(story_id):
    """异步生成：先标记"生成中"，再丢到后台线程，请求立即返回。"""
    db.set_ai_status(story_id, "pending")
    threading.Thread(target=_generate_ai, args=(story_id,), daemon=True).start()


def recover_pending():
    """启动时重跑遗留的"生成中"故事：后台线程随进程退出而中断，
    会留下 ai_status=pending 的孤儿，页面就会一直转圈。重启时重新触发即可。"""
    for s in db.list_stories():
        if s.get("ai_status") == "pending":
            kick_ai(s["id"])


# 单条接龙的 AI 辣评：异步生成，最多并发 4 条，同一块同时只生成一次
_REVIEW_SEMA = threading.Semaphore(4)
_reviewing = set()
_reviewing_lock = threading.Lock()


def _generate_review(block_id):
    blk = db.get_block(block_id)
    if not blk or blk["sequence"] < 1:
        return
    blocks = db.get_blocks(blk["story_id"])
    opening = blocks[0]["raw_content"] if blocks else ""
    prev = [b["raw_content"] for b in blocks if 1 <= b["sequence"] < blk["sequence"]]
    db.set_block_review(block_id, ai.review(opening, prev, blk["raw_content"]))


def kick_review(block_id):
    with _reviewing_lock:
        if block_id in _reviewing:
            return
        _reviewing.add(block_id)

    def run():
        try:
            with _REVIEW_SEMA:
                _generate_review(block_id)
        finally:
            with _reviewing_lock:
                _reviewing.discard(block_id)

    threading.Thread(target=run, daemon=True).start()


# ---------- 首页 ----------

@app.route("/")
def index():
    return render_template("index.html", stories=db.list_stories(), user=current_user())


# ---------- 登录 / 退出 ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nickname = (request.form.get("nickname") or "").strip()
        if not nickname:
            flash("请输入昵称")
            return redirect(url_for("login"))
        user = db.get_or_create_user(nickname)
        session["user_id"] = user["id"]
        session["nickname"] = user["nickname"]
        flash(f"欢迎，{user['nickname']}！")
        return redirect(request.args.get("next") or url_for("index"))
    return render_template("login.html", user=current_user())


@app.route("/logout")
def logout():
    session.clear()
    flash("已退出登录")
    return redirect(url_for("index"))


# ---------- 详情页 ----------

@app.route("/story/<int:story_id>")
def story_detail(story_id):
    story = db.get_story(story_id)
    if not story:
        abort(404)
    blocks = db.get_blocks(story_id)
    user = current_user()
    counts, mine = db.get_ratings(story_id, user["id"] if user else None)
    for b in blocks:  # 把每条接龙的好评/差评数与"我的投票"挂到 block 上
        c = counts.get(b["id"], {"good": 0, "bad": 0})
        b["good"], b["bad"], b["mine"] = c["good"], c["bad"], mine.get(b["id"])
        if b["sequence"] >= 1 and not b["ai_review"]:
            kick_review(b["id"])  # 没有辣评的接龙，进页面即开始异步生成
    tail = blocks[-1] if blocks else None
    is_creator = bool(user and user["id"] == story["creator_id"])
    consecutive = bool(user and tail and tail["author_id"] == user["id"])
    return render_template(
        "detail.html",
        story=story, blocks=blocks, user=user,
        is_creator=is_creator, consecutive=consecutive,
        max_blocks=db.MAX_BLOCKS, relay_max=db.MAX_RELAY,
        good_reviews=GOOD_REVIEWS, bad_reviews=BAD_REVIEWS,
    )


# ---------- AI 串联状态（前端轮询） ----------

@app.route("/story/<int:story_id>/ai_status")
def ai_status(story_id):
    story = db.get_story(story_id)
    if not story:
        abort(404)
    paragraphs = [p for p in (story["ai_content"] or "").split("\n\n") if p]
    return {"status": story["ai_status"], "paragraphs": paragraphs}


# ---------- 发布页（发起 / 接龙 双模式） ----------

@app.route("/publish", methods=["GET", "POST"])
def publish():
    user = current_user()
    if not user:
        return redirect(url_for("login", next=request.full_path))

    story_id = request.values.get("story_id", type=int)

    if request.method == "POST":
        content = (request.form.get("content") or "").strip()

        if story_id:  # 接龙模式
            if not content or len(content) > db.MAX_RELAY:
                flash(f"接龙内容需为 1–{db.MAX_RELAY} 字")
                return redirect(url_for("publish", story_id=story_id))
            expected = request.form.get("expected_sequence", type=int)
            res = db.add_block(story_id, expected, content, user["id"])
            if not res["ok"]:
                flash(RELAY_ERRORS.get(res["error"], "接龙失败"))
                return redirect(url_for("story_detail", story_id=story_id))
            kick_ai(story_id)  # 接龙已落盘，AI 串联异步进行
            if res.get("block_id"):
                kick_review(res["block_id"])  # 这条接龙的 AI 辣评也异步生成
            flash("接龙成功，故事已达上限并完结！" if res.get("finished") else "接龙成功！")
            return redirect(url_for("story_detail", story_id=story_id))

        # 发起模式
        if not content or len(content) > db.MAX_OPENING:
            flash(f"开头需为 1–{db.MAX_OPENING} 字")
            return redirect(url_for("publish"))
        new_id = db.create_story(content, user["id"])
        kick_ai(new_id)  # 开头已落盘，AI 串联异步进行
        flash("发布成功！")
        return redirect(url_for("story_detail", story_id=new_id))

    # GET
    if story_id:  # 接龙模式：进入前校验
        story = db.get_story(story_id)
        if not story:
            abort(404)
        if story["status"] != "ongoing":
            flash("故事已完结，无法接龙")
            return redirect(url_for("story_detail", story_id=story_id))
        blocks = db.get_blocks(story_id)
        tail = blocks[-1] if blocks else None
        if tail and tail["author_id"] == user["id"]:
            flash("不能连续接龙，请等待其他人接龙后再继续")
            return redirect(url_for("story_detail", story_id=story_id))
        return render_template(
            "publish.html", mode="relay", story=story, tail=tail, blocks=blocks,
            user=user, maxlen=db.MAX_RELAY,
        )

    return render_template("publish.html", mode="new", user=user, maxlen=db.MAX_OPENING)


# ---------- 手动完结 ----------

@app.route("/story/<int:story_id>/finish", methods=["POST"])
def finish(story_id):
    user = current_user()
    if not user:
        return redirect(url_for("login"))
    res = db.finish_story(story_id, user["id"])
    flash("故事已完结" if res["ok"] else res.get("msg", "操作失败"))
    return redirect(url_for("story_detail", story_id=story_id))


# ---------- 接龙评价（好评 / 差评，AJAX） ----------

@app.route("/block/<int:block_id>/rate", methods=["POST"])
def rate(block_id):
    user = current_user()
    if not user:
        return {"ok": False, "error": "login"}, 401
    kind = request.form.get("kind")
    if kind not in ("good", "bad"):
        return {"ok": False, "error": "bad_kind"}, 400
    res = db.rate_block(block_id, user["id"], kind)
    if not res["ok"]:
        return {"ok": False, "error": res["error"]}, 404
    counts = db.get_block_counts(block_id)
    return {
        "ok": True,
        "good": counts["good"],
        "bad": counts["bad"],
        "mine": db.get_user_vote(block_id, user["id"]),
    }


@app.route("/block/<int:block_id>/review")
def block_review(block_id):
    blk = db.get_block(block_id)
    if not blk:
        abort(404)
    if not blk["ai_review"]:
        kick_review(block_id)  # 还没生成就触发
    return {"ready": bool(blk["ai_review"]), "review": blk["ai_review"]}


# ---------- 启动：建表 + 首次种子数据 ----------

def ensure_seed():
    """首次启动时插入一个示例故事，让首页非空、可直接体验。"""
    if db.list_stories():
        return
    editor = db.get_or_create_user("系统小编")
    u1 = db.get_or_create_user("阿橘")
    u2 = db.get_or_create_user("小满")
    sid = db.create_story("末日第七天，冰箱里只剩最后一罐可乐。", editor["id"])
    db.add_block(sid, 0, "我盯着它看了整整三个小时。", u1["id"])
    db.add_block(sid, 1, "窗外忽然传来敲门声，三长两短。", u2["id"])
    _generate_ai(sid)  # 种子数据同步生成，保证首页一打开就有正文


db.init_db()
ensure_seed()
recover_pending()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
