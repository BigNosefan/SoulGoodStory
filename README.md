# Soul Good Story Demo

一个由 AI 驱动的「区块链式」故事接龙 demo：系统/用户起一个开头，大家一句句接龙（限字数），AI 把碎片串成一篇连贯故事。

- 产品需求：`goodStoryPrd.md`
- 技术方案：`TECH_DESIGN.md`

## 快速开始

```bash
cd goodStory
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

浏览器打开 **http://localhost:5001**。首次启动会自动插入一个示例故事。

## 功能

- **首页**：故事列表（标题 / AI 摘要 / 接龙数 / 参与人数 / 状态）+ 悬浮发布按钮。
- **详情页**：① AI 串联正文 ② 接龙入口（跳转发布页）③ 接龙记录（原始片段 + 贡献者），每条接龙可点 👍 好评 / 👎 差评（文案随机、AJAX 计数、可取消/切换），右侧还有 🌶️ AI辣评（DeepSeek 结合上文逐条点评，异步生成 + 缓存）。
- **发布页**：双模式 —— 发起新故事（开头 ≤ 50 字）/ 接龙（≤ 20 字）。
- 区块链式顺序追加、并发链尾校验、不允许连续接龙、达 50 段自动完结、发起人可手动完结。
- 游客可浏览；接龙/发布需登录（输入昵称即可，无密码）。

## AI 串联

按环境变量自动选择引擎（优先级 **DeepSeek > Claude > mock**），任何真实调用失败都会自动回退 mock，详情页会显示当前用的是哪个引擎。

- **mock（默认）**：内置规则串联器，零依赖、无需任何 Key。
- **DeepSeek（推荐）**：OpenAI 兼容接口，用 Python 标准库调用，**无需额外装包**。
- **Claude**：需 `pip install anthropic`。

把 `.env.example` 复制为 `.env` 填入 key（`.env` 已被 git 忽略，不会提交），启动时自动加载：

```bash
cp .env.example .env
# 编辑 .env：
#   DEEPSEEK_API_KEY=sk-...
#   GOODSTORY_MODEL=deepseek-v4-flash
python app.py
```

也可直接用环境变量：`DEEPSEEK_API_KEY=sk-... GOODSTORY_MODEL=deepseek-v4-flash python app.py`

## 环境变量（也可写入 .env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PORT` | `5001` | 服务端口 |
| `DEEPSEEK_API_KEY` | 无 | 设置后启用 DeepSeek 串联 |
| `ANTHROPIC_API_KEY` | 无 | 设置后启用 Claude 串联（DeepSeek 优先） |
| `GOODSTORY_MODEL` | 按引擎 | 模型名（DeepSeek 默认 `deepseek-chat`，Claude 默认 `claude-opus-4-8`） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek 接口地址 |
| `GOODSTORY_SECRET` | `dev-secret-change-me` | Flask session 密钥 |

## 部署到 Vercel

已含 `vercel.json` + `api/index.py`。serverless 有几点限制，务必注意：

1. **配环境变量**：Vercel 项目 → Settings → Environment Variables 添加 `DEEPSEEK_API_KEY` 和 `GOODSTORY_MODEL=deepseek-v4-flash`（`.env` 不会被部署，不配会回退 mock）。改完需 Redeploy。
2. **数据是临时的**：SQLite 存于 `/tmp`，冷启动重置、多实例间不共享。内置示例故事每个实例会自建，可正常体验；用户新建内容仅在同一实例内可见。要持久/共享请改用 Postgres（Neon / Vercel Postgres）或换 Render / Railway 等常驻进程平台。
3. AI 生成改为「轮询时同步生成」（serverless 无常驻后台线程），`maxDuration` 设为 60s。

## 说明

- 数据存于 `goodstory.db`（SQLite，首次运行自动创建）。删除该文件即可重置数据。
- 这是本地演示用 demo：登录为昵称简化版，未做内容风控/埋点，使用 Flask 开发服务器，请勿用于生产。
