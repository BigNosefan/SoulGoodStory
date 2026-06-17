"""好故事（GoodStory）demo —— Flask 本地服务器。

运行：python app.py  （默认 http://localhost:5001）
"""

import os

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, abort,
)

import db
import ai

app = Flask(__name__)
app.secret_key = os.environ.get("GOODSTORY_SECRET", "dev-secret-change-me")

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


def refresh_ai(story_id):
    """重新生成并缓存故事的 AI 串联正文。"""
    blocks = db.get_blocks(story_id)
    if not blocks:
        return
    opening = blocks[0]["raw_content"]
    segments = [b["raw_content"] for b in blocks[1:]]
    db.update_ai_content(story_id, ai.stitch(opening, segments))


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
    tail = blocks[-1] if blocks else None
    is_creator = bool(user and user["id"] == story["creator_id"])
    consecutive = bool(user and tail and tail["author_id"] == user["id"])
    return render_template(
        "detail.html",
        story=story, blocks=blocks, user=user,
        is_creator=is_creator, consecutive=consecutive,
        max_blocks=db.MAX_BLOCKS, relay_max=db.MAX_RELAY,
    )


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
            refresh_ai(story_id)
            flash("接龙成功，故事已达上限并完结！" if res.get("finished") else "接龙成功！")
            return redirect(url_for("story_detail", story_id=story_id))

        # 发起模式
        if not content or len(content) > db.MAX_OPENING:
            flash(f"开头需为 1–{db.MAX_OPENING} 字")
            return redirect(url_for("publish"))
        new_id = db.create_story(content, user["id"])
        refresh_ai(new_id)
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
        tail = db.get_tail(story_id)
        if tail and tail["author_id"] == user["id"]:
            flash("不能连续接龙，请等待其他人接龙后再继续")
            return redirect(url_for("story_detail", story_id=story_id))
        return render_template(
            "publish.html", mode="relay", story=story, tail=tail,
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
    refresh_ai(sid)


db.init_db()
ensure_seed()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5001"))
    app.run(host="0.0.0.0", port=port, debug=False)
