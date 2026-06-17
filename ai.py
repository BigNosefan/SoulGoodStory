"""AI 串联：把"开头 + 全部接龙片段"串成连贯故事正文。

按环境变量自动选择 provider（优先级从高到低）：
  1. DEEPSEEK_API_KEY  -> DeepSeek（OpenAI 兼容接口，用标准库 urllib 调用，无需额外依赖）
  2. ANTHROPIC_API_KEY -> Claude（需 pip install anthropic）
  3. 都没有            -> 内置 mock 串联器（零依赖）

任何真实调用失败都会自动回退 mock，保证 demo 不中断。
模型名用 GOODSTORY_MODEL 覆盖（DeepSeek 默认 deepseek-chat，Claude 默认 claude-opus-4-8）。
"""

import os
import json
import urllib.request

# mock 串联用的过渡词，按片段顺序循环插入
_CONNECTORS = ["", "接着，", "然后，", "不久后，", "与此同时，", "没想到，", "就在这时，", "后来，"]
_TERMINALS = "。！？…」』）)】"

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
_DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
_CLAUDE_DEFAULT_MODEL = "claude-opus-4-8"


def active_provider():
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return "mock"


def provider_label():
    """给页面展示用：当前实际使用的串联引擎。"""
    p = active_provider()
    if p == "deepseek":
        return f"DeepSeek · {os.environ.get('GOODSTORY_MODEL', _DEEPSEEK_DEFAULT_MODEL)}"
    if p == "claude":
        return f"Claude · {os.environ.get('GOODSTORY_MODEL', _CLAUDE_DEFAULT_MODEL)}"
    return "内置 mock 串联器"


def stitch(opening, segments):
    """opening: 开头字符串；segments: 接龙片段字符串列表（不含开头）。返回正文字符串。"""
    provider = active_provider()
    try:
        if provider == "deepseek":
            return _stitch_deepseek(opening, segments)
        if provider == "claude":
            return _stitch_claude(opening, segments)
    except Exception as e:  # 网络/鉴权/模型等任何问题都回退 mock
        print(f"[ai] {provider} 调用失败，回退 mock 串联：{e}")
    return _stitch_mock(opening, segments)


def _build_prompt(opening, segments):
    seg_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(segments)) or "（暂无接龙）"
    return (
        "这是一个多人故事接龙。请把【开头】和后续【接龙片段】按顺序融合、改写成"
        "一段连贯流畅、可读性强的中文故事正文。\n"
        "要求：\n"
        "1) 保留每个片段的核心情节与先后顺序；\n"
        "2) 补充自然的过渡与衔接，让前后读起来像一篇完整的故事，而不是逐句罗列；\n"
        "3) 可对措辞做润色、补充少量细节，但不要新增重大情节、不要改变故事走向；\n"
        "4) 只输出故事正文（1–4 个自然段），不要分点、不要标题、不要任何解释或前后缀。\n\n"
        f"【开头】\n{opening}\n\n"
        f"【接龙片段（按顺序）】\n{seg_text}\n"
    )


def _stitch_deepseek(opening, segments):
    key = os.environ["DEEPSEEK_API_KEY"]
    model = os.environ.get("GOODSTORY_MODEL", _DEEPSEEK_DEFAULT_MODEL)
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": _build_prompt(opening, segments)}],
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    # DeepSeek 推理模型会返回 reasoning_content（思考）+ content（正文），只取 content
    return data["choices"][0]["message"]["content"].strip()


def _stitch_claude(opening, segments):
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("GOODSTORY_MODEL", _CLAUDE_DEFAULT_MODEL)
    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": _build_prompt(opening, segments)}],
    )
    return "".join(b.text for b in msg.content if b.type == "text").strip()


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
    paragraphs = ["".join(units[i:i + 3]) for i in range(0, len(units), 3)]
    return "\n\n".join(paragraphs)
