"""AI 串联：把"开头 + 全部接龙片段"串成连贯故事正文。

按环境变量自动选择 provider（优先级从高到低）：
  1. DEEPSEEK_API_KEY  -> DeepSeek（OpenAI 兼容接口，用标准库 urllib 调用，无需额外依赖）
  2. ANTHROPIC_API_KEY -> Claude（需 pip install anthropic）
  3. 都没有            -> 内置 mock 串联器（零依赖）

任何真实调用失败都会自动回退 mock，保证 demo 不中断。
模型名用 GOODSTORY_MODEL 覆盖（DeepSeek 默认 deepseek-chat，Claude 默认 claude-opus-4-8）。
"""

import os
import re
import json
import urllib.request
import urllib.error

# mock 串联用的过渡词，按片段顺序循环插入
_CONNECTORS = ["", "接着，", "然后，", "不久后，", "与此同时，", "没想到，", "就在这时，", "后来，"]
_TERMINALS = "。！？…」』）)】"

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
_DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
_CLAUDE_DEFAULT_MODEL = "claude-opus-4-8"


def _deepseek_model():
    # 容错：去掉环境变量里可能带的空格/引号/换行（Vercel 后台粘贴常见），否则 model 非法会 400
    raw = os.environ.get("GOODSTORY_MODEL", _DEEPSEEK_DEFAULT_MODEL)
    return raw.strip().strip('"').strip("'") or _DEEPSEEK_DEFAULT_MODEL


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
        return f"DeepSeek · {_deepseek_model()}"
    if p == "claude":
        return f"Claude · {os.environ.get('GOODSTORY_MODEL', _CLAUDE_DEFAULT_MODEL)}"
    return "内置 mock 串联器"


def stitch(opening, segments):
    """返回 (正文, 来源)，来源 ∈ deepseek / claude / mock。"""
    provider = active_provider()
    if provider != "mock":
        try:
            if provider == "deepseek":
                return _stitch_deepseek(opening, segments), provider
            if provider == "claude":
                return _stitch_claude(opening, segments), provider
        except Exception as e:  # 网络/鉴权/模型等任何问题都回退 mock
            print(f"[ai] {provider} 调用失败，回退 mock 串联：{e}")
    return _stitch_mock(opening, segments), "mock"


_TAG_RE_AI = re.compile(r"\[\[(\d+)\|([^\]]*)\]\]")
_PUNCT_RE = re.compile(r"[\s，,。、！!？?：:；;\"'“”‘’「」『』（）()【】—…～~·]")


def _dedupe_tags(text):
    """去掉模型在 [[编号|短语]] 之前重复写出的同一短语（高亮标注导致的句子重复）。

    模型常见错误：先在正文写出短语 X，紧接着又补一个 [[N|X]]，渲染后 X 出现两遍。
    两种识别（忽略标点/空格后比较）：
      ① 正文后缀 == 短语前缀（部分重叠，如"穿着凉席"⊂"穿着凉席跳舞"）；
      ② 正文末段 ≈ 整个短语（中间个别字差异，如"断电"/"断点"），相似度≥70%。
    命中则删掉正文里重复的那部分，只保留 [[ ]] 内的一份。
    """
    if not text or "[[" not in text:
        return text

    def norm(s):
        return _PUNCT_RE.sub("", s or "")

    out, last = [], 0
    for m in _TAG_RE_AI.finditer(text):
        pre = text[last:m.start()]
        npn = norm(m.group(2))
        cut = 0
        if npn and pre:
            npre = norm(pre)
            for L in range(min(len(npre), len(npn)), 0, -1):   # ① 前缀重叠
                if npre[-L:] == npn[:L]:
                    cut = L
                    break
            if not (cut >= 3 and cut >= len(npn) * 0.5):
                cut = 0
            if cut == 0 and len(npn) >= 4 and len(npre) >= len(npn):   # ② 整段模糊
                tail = npre[-len(npn):]
                same = sum(1 for a, b in zip(tail, npn) if a == b)
                if same >= len(npn) * 0.7:
                    cut = len(npn)
        if cut:
            cnt, idx = 0, len(pre)
            while idx > 0 and cnt < cut:   # 从末尾删掉 cut 个有效字符（跳过标点）
                idx -= 1
                if not _PUNCT_RE.match(pre[idx]):
                    cnt += 1
            pre = pre[:idx]
        out.append(pre)
        out.append(m.group(0))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def _build_prompt(opening, segments):
    seg_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(segments)) or "（暂无接龙）"
    target = (len(opening) + sum(len(s) for s in segments)) * 4
    return (
        "这是一个多人故事接龙。请把【开头】和后续【接龙片段】按顺序融合、改写并充分扩写成"
        "一篇连贯流畅、可读性强的中文故事正文。\n"
        "要求：\n"
        "1) 保留每个片段的核心情节与先后顺序；\n"
        "2) 补充自然的过渡与衔接，让前后读起来像一篇完整的故事，而不是逐句罗列；\n"
        f"3) 充分扩写：目标篇幅约 {target} 字（约为所有片段原文总长的 4 倍），通过补充环境、"
        "动作、神态、心理、对话等细节把故事写得饱满耐读；但不得新增重大情节、不得改变故事走向；\n"
        "4) 高亮标注：把正文中【已经写出的】关键短语，就地用 [[编号|短语]] 直接套起来"
        "（编号为该片段在下方列表中的序号）。务必注意：被套住的短语在正文里只能出现这一次，"
        "千万不要在 [[ ]] 前面或后面再重复写一遍同样的文字。每条接龙至少套一处、只套关键短语"
        "（几个字到一小句）；开头(创世段)不要标注。\n"
        "   ✅ 正确：我[[1|穿着凉席跳舞]]，试图取暖。\n"
        "   ❌ 错误：我穿着凉席跳舞[[1|穿着凉席跳舞]]，试图取暖。（短语重复了两次）\n"
        "5) 只输出故事正文（可分多个自然段，可包含上述 [[编号|短语]] 标注），"
        "不要分点、不要标题、不要任何解释或前后缀。\n\n"
        f"【开头】\n{opening}\n\n"
        f"【接龙片段（按顺序）】\n{seg_text}\n"
    )


def _deepseek_chat(prompt, max_tokens=2000):
    key = os.environ["DEEPSEEK_API_KEY"].strip()
    body = json.dumps({
        "model": _deepseek_model(),
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        DEEPSEEK_BASE_URL.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 把 DeepSeek 的真实错误体带进日志，便于定位（如模型名非法、额度等）
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"DeepSeek HTTP {e.code}: {detail}") from None
    # DeepSeek 推理模型会返回 reasoning_content（思考）+ content（正文），只取 content
    content = (data["choices"][0]["message"].get("content") or "").strip()
    if not content:
        # 推理占满 max_tokens 时 content 可能为空；视为失败以回退 mock，避免缓存空值/无限轮询
        raise RuntimeError("DeepSeek 返回空 content（max_tokens 可能被 reasoning 占满）")
    return content


def _stitch_deepseek(opening, segments):
    target = (len(opening) + sum(len(s) for s in segments)) * 4
    max_tokens = min(8000, max(2500, target * 5))   # 给扩写 + 推理留足额度，避免截断/空 content
    return _dedupe_tags(_deepseek_chat(_build_prompt(opening, segments), max_tokens=max_tokens))


def _stitch_claude(opening, segments):
    import anthropic

    client = anthropic.Anthropic()
    model = os.environ.get("GOODSTORY_MODEL", _CLAUDE_DEFAULT_MODEL)
    msg = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": _build_prompt(opening, segments)}],
    )
    return _dedupe_tags("".join(b.text for b in msg.content if b.type == "text").strip())


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


# ===== 单条接龙的 AI 辣评 =====

_MOCK_REVIEWS = [
    "这转折，编剧看了想转行。",
    "稳是稳，就是稳得像白开水。",
    "脑洞开到隔壁宇宙去了。",
    "就离谱，但莫名好磕。",
    "字数达标，深度欠费。",
]


def review(opening, prev_segments, target):
    """返回 (辣评, 来源)，来源 ∈ deepseek / mock。"""
    if os.environ.get("DEEPSEEK_API_KEY"):
        try:
            return _review_deepseek(opening, prev_segments, target), "deepseek"
        except Exception as e:
            print(f"[ai] 辣评调用失败，回退 mock：{e}")
    return _review_mock(target), "mock"


def _review_deepseek(opening, prev_segments, target):
    ctx = "\n".join([opening] + list(prev_segments)) if prev_segments else opening
    prompt = (
        "你是一个毒舌又有网感的故事点评官。下面是一个多人接龙故事的上文，以及最新的一句接龙。\n"
        "请只针对【最新这一句】给出一句简短犀利、有梗有态度的「辣评」：可吐槽可夸，"
        "控制在 25 字以内；只输出辣评本身，不要引号、不要解释。\n\n"
        f"【故事上文】\n{ctx}\n\n"
        f"【最新这一句接龙】\n{target}\n"
    )
    return _deepseek_chat(prompt, max_tokens=1024)


def _review_mock(target):
    import zlib
    return _MOCK_REVIEWS[zlib.crc32(target.encode("utf-8")) % len(_MOCK_REVIEWS)]
