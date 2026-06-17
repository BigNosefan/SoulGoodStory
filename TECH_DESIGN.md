# 好故事（GoodStory）Demo · 技术方案

> 目标：用 Python 做一个**本地服务器**形式的可运行 demo，通过浏览器访问，验证 PRD v1.0 的核心玩法（区块链式故事接龙 + AI 串联）。对应 PRD：`goodStoryPrd.md`。

---

## 1. 技术选型

### 1.1 需求侧约束
- Python 实现；本地服务器形式；浏览器可访问的网页入口。
- 三个页面：首页 / 详情页 / 发布页；需要表单提交、列表、详情。
- 核心逻辑：顺序追加（区块链式）、并发链尾校验、不允许连续接龙、AI 串联。
- demo 性质：**即开即用**，依赖越少越好。

### 1.2 框架对比与结论

| 候选 | 优点 | 对本 demo 的问题 | 结论 |
| --- | --- | --- | --- |
| **Flask** | 轻量、单文件可跑、Jinja2 模板开箱即用、内置开发服务器 | 无 | ✅ **选用** |
| FastAPI | 现代、异步、自带 API 文档 | 偏 API，做多页面网页还要额外配模板/前端，对 demo 偏重 | ❌ |
| Django | 全功能 | 对一个 demo 过重，目录/配置心智负担大 | ❌ |
| Streamlit/Gradio | 出原型快 | 多页面 + 路由 + 表单流转受限，不像真实"网页入口" | ❌ |

**最终技术栈：**

| 层 | 选型 | 说明 |
| --- | --- | --- |
| Web 框架 | **Flask 3** | 路由 + 内置本地服务器 |
| 模板 | **Jinja2**（Flask 自带） | 渲染三个 HTML 页面 |
| 存储 | **SQLite**（Python 标准库 `sqlite3`） | 零安装、持久化、支持事务与唯一约束（做并发控制） |
| 前端 | 原生 HTML + CSS + 极少 JS | 字数计数、完结二次确认 |
| 登录 | Flask `session` + 昵称 | demo 简化（无密码），用于贡献者归属与"不允许连续接龙" |
| AI 串联 | **Mock 串联器（默认）** + 可选 Claude API | 无 Key 也能跑；配 `ANTHROPIC_API_KEY` 后自动走真实模型 |

---

## 2. 架构

```
浏览器
  │  HTTP
  ▼
Flask (app.py)  ──路由──►  首页 / 详情 / 发布 / 登录 / 接龙提交 / 完结
  │                         │
  │ 数据访问                 │ 每次接龙后
  ▼                         ▼
db.py (SQLite)          ai.py  ──►  stitch(opening, segments)
  users / stories / blocks       ├─ 默认：mock 串联（规则拼接+过渡词+分段）
                                 └─ 可选：Claude API（claude-opus-4-8，可配）
```

- **无前端框架**：Jinja2 服务端渲染，浏览器直接打开即用。
- **AI 解耦**：`ai.stitch()` 统一入口；有 `ANTHROPIC_API_KEY` 走真实模型，否则/失败回退 mock。

---

## 3. 数据模型（SQLite）

把"开头"也存为一个 block（`sequence = 0`，创世段），所有片段统一在 `blocks` 表，链尾 = `MAX(sequence)`。

```
users
  id            INTEGER PK
  nickname      TEXT UNIQUE
  created_at    TEXT

stories
  id            INTEGER PK
  title         TEXT            -- 由开头截取
  creator_id    INTEGER FK->users
  status        TEXT            -- ongoing / finished
  ai_content    TEXT            -- AI 串联正文缓存
  created_at    TEXT
  updated_at    TEXT

blocks
  id            INTEGER PK
  story_id      INTEGER FK->stories
  sequence      INTEGER         -- 区块高度；0=开头，接龙从 1 起
  raw_content   TEXT            -- 用户原始片段（只读，不可篡改）
  author_id     INTEGER FK->users
  created_at    TEXT
  UNIQUE(story_id, sequence)    -- 乐观锁：并发追加同一 sequence 必失败其一
```

- **接龙数** = `MAX(sequence)`；**参与人数** = `COUNT(DISTINCT author_id)`。

---

## 4. 关键逻辑（映射 PRD 决策）

| PRD 决策 | 实现 |
| --- | --- |
| 区块链式顺序追加（Q8 仅 sequence + 只读） | 新片段 `sequence = 链尾 + 1`；`blocks` 只插入不更新/删除 |
| 并发链尾校验（§7.2） | 提交带 `expected_sequence`；服务端比对链尾 + `UNIQUE(story_id,sequence)` 兜底，冲突提示刷新 |
| 不允许连续接龙（Q3） | 提交前校验链尾 `author_id != 当前用户`，详情页入口同步置灰 |
| 字数限制（Q2） | 开头 ≤ 50 字、接龙 ≤ 20 字；前端 `maxlength` + 服务端二次校验 |
| 完结机制（Q5） | 接龙后 `sequence >= 50` 自动 `finished`；发起人可手动完结 |
| 接龙跳转发布页（Q7） | 详情页占位入口 → `GET /publish?story_id=`（发布页双模式） |
| 游客可浏览（Q6） | 首页/详情免登录；接龙/发布需登录（昵称） |
| AI 串联（§10.1） | 每次成功接龙后 `stitch(开头, 全部片段)` 重算并缓存到 `ai_content` |
| 不做风控/埋点/hash（Q4/Q9/Q8） | 仅前端字数/非空校验；不接埋点；不做 hash 链 |

### 路由一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 首页：故事列表 + 发布按钮 |
| GET | `/story/<id>` | 详情页：AI 正文 + 接龙入口 + 接龙记录 |
| GET/POST | `/publish` | 发布页：发起（无参）/ 接龙（`story_id`）双模式 |
| GET/POST | `/login`、GET `/logout` | 昵称登录/退出 |
| POST | `/story/<id>/finish` | 发起人手动完结 |

---

## 5. AI 串联设计

`ai.stitch(opening, segments)`：
- **默认 mock**：将开头 + 片段规范标点、插入过渡词（"接着""突然""就在这时"…）、按段落聚合，产出连贯正文。无需任何 Key。
- **可选真实模型**：设置环境变量 `ANTHROPIC_API_KEY` 即自动启用；用官方 `anthropic` SDK 调 `client.messages.create`，默认模型 `claude-opus-4-8`（可用 `GOODSTORY_MODEL` 覆盖，如改 `claude-haiku-4-5` 更快更省）。调用失败自动回退 mock，保证 demo 不中断。

---

## 6. 运行方式

```bash
cd goodStory
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py            # 默认 http://localhost:5001
```

浏览器打开 `http://localhost:5001`。可选真实 AI：

```bash
export ANTHROPIC_API_KEY=sk-...      # 启用真实串联
export GOODSTORY_MODEL=claude-haiku-4-5   # 可选：更快更省
python app.py
```

环境变量：`PORT`（默认 5001）、`ANTHROPIC_API_KEY`（启用真实 AI）、`GOODSTORY_MODEL`（默认 `claude-opus-4-8`）、`GOODSTORY_SECRET`（session 密钥）。

---

## 7. 目录结构

```
goodStory/
  goodStoryPrd.md      # 产品需求文档
  TECH_DESIGN.md       # 本文档
  requirements.txt
  app.py               # Flask 路由
  db.py                # SQLite 模型与数据访问
  ai.py                # AI 串联（mock + 可选 Claude）
  templates/           # base / index / detail / publish / login
  static/style.css
  README.md
```

---

## 8. Demo 边界（非生产）

- 登录为昵称简化版，无密码、无鉴权强度；session 密钥为开发默认值。
- 无内容风控、无埋点（与 PRD V1 一致）。
- SQLite 单文件、Flask 开发服务器，仅用于本地演示，不用于生产部署。
- AI mock 仅做规则串联，效果不及真实模型；接 Key 后体验为准。
