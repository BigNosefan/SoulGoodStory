"""AI 串联：把"开头 + 全部接龙片段"串成连贯故事正文。

- 默认：内置 mock 串联器（规则拼接 + 过渡词 + 分段），零依赖、无需任何 Key。
- 可选：设置环境变量 ANTHROPIC_API_KEY 后，自动改用 Claude API（默认模型
  claude-opus-4-8，可用 GOODSTORY_MODEL 覆盖，如 claude-haiku-4-5 更快更省）。
  真实调用失败时自动回退 mock，保证 demo 不中断。
"""

import os

# 过渡词，按片段顺序循环插入，让 mock 串联更像"故事"
_CONNECTORS = ["", "接着，", "然后，", "不久后，", "与此同时，", "没想到，", "就在这时，", "后来，"]
_TERMINALS = "。！？…」』）)】"


def stitch(opening, segments):
    """opening: 开头字符串；segments: 接龙片段字符串列表（不含开头）。返回正文字符串。"""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _stitch_with_claude(opening, segments)
        except Exception as e:  # 网络/SDK/鉴权等任何问题都回退 mock
            print(f"[ai] Claude 调用失败，回退 mock 串联：{e}")
    return _stitch_mock(opening, segments)


def _normalize(s):
    s = (s or "").strip()
    if s and s[-1] not in _TERMINALS:
        s += "。"
    return s


def _stitch_mock(opening, segments):
    units = [_normalize(opening)]
    for i, seg in enumerate(segments):
        connector = _CONNECTORS[i % len(_CONNECTORS)]
        units.append(connector + _normalize(seg))
    # 每 3 个单元聚成一段，提升可读性
    paragraphs = ["".join(units[i:i + 3]) for i in range(0, len(units), 3)]
    return "\n\n".join(paragraphs)


def _stitch_with_claude(opening, segments):
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("GOODSTORY_MODEL", "claude-opus-4-8")

    seg_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(segments)) or "（暂无接龙）"
    prompt = (
        "下面是一个故事接龙。请把【开头】和后续所有【接龙片段】按顺序串联、润色成"
        "一篇连贯通顺的中文故事。\n"
        "要求：忠实保留每个片段的核心情节，不新增重大情节，不改变故事走向；"
        "段落清晰；只输出故事正文，不要任何解释或前后缀。\n\n"
        f"【开头】\n{opening}\n\n"
        f"【接龙片段（按顺序）】\n{seg_text}\n"
    )

    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()
