# 好故事（GoodStory）Demo

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
- **详情页**：① AI 串联正文 ② 接龙入口（跳转发布页）③ 接龙记录（原始片段 + 贡献者）。
- **发布页**：双模式 —— 发起新故事（开头 ≤ 50 字）/ 接龙（≤ 20 字）。
- 区块链式顺序追加、并发链尾校验、不允许连续接龙、达 50 段自动完结、发起人可手动完结。
- 游客可浏览；接龙/发布需登录（输入昵称即可，无密码）。

## AI 串联

- **默认**：内置 mock 串联器，零依赖、无需任何 Key。
- **可选真实模型**：设置 API Key 后自动启用，失败自动回退 mock。

```bash
export ANTHROPIC_API_KEY=sk-...            # 启用真实 AI 串联
export GOODSTORY_MODEL=claude-haiku-4-5    # 可选，默认 claude-opus-4-8
python app.py
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PORT` | `5001` | 服务端口 |
| `ANTHROPIC_API_KEY` | 无 | 设置后启用真实 AI 串联 |
| `GOODSTORY_MODEL` | `claude-opus-4-8` | 真实串联使用的模型 |
| `GOODSTORY_SECRET` | `dev-secret-change-me` | Flask session 密钥 |

## 说明

- 数据存于 `goodstory.db`（SQLite，首次运行自动创建）。删除该文件即可重置数据。
- 这是本地演示用 demo：登录为昵称简化版，未做内容风控/埋点，使用 Flask 开发服务器，请勿用于生产。
