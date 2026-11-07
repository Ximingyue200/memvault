"""提炼：对话 → 卡片。只保留能被验证的具体内容。"""
import json
import os
import re
import urllib.request


try:  # token 记账：拦 urlopen，把 usage 落到 /opt/memvault/data/llm_usage.jsonl
    from . import _meter  # noqa: F401
except Exception:
    pass

try:
    from .effort import kwargs as _effort_kw
except Exception:  # 直接当脚本跑时没有包上下文
    def _effort_kw(task=None):
        return {}

LLM_URL = os.environ.get("MEMVAULT_LLM_URL", "https://text.pollinations.ai/openai")
LLM_MODEL = os.environ.get("MEMVAULT_LLM_MODEL", "openai")
LLM_KEY = os.environ.get("MEMVAULT_LLM_KEY", "")
DEMO_MODE = ("pollinations" in LLM_URL) or not LLM_KEY  # 免费/无 key：可用本地兜底

PROMPT = """从下面的对话里提取值得三个月后还记得的内容。

只提取「下次遇到同类问题时能用上」的东西——判断、参数选择、踩过的坑、为什么这么做。
一条都写不出来就返回 []，不要硬凑。

绝对不要提取以下六类，遇到直接跳过：

A. 运行时环境与上下文
   工作目录、文件权限策略（如 danger-full-access）、端口号、GUI 地址、
   审批设置、模型名、主机名、shell 名、AGENTS.md / system prompt 里的任何规定。
   这些是「这台机器此刻的状态」，不是知识。

B. 一次性观测数字
   「检索返回 12 条共 5,619 字」「一条 recall 记录 49.1k 字 1060ms」
   「最近 6 小时 0 条、24 小时 0 条」「门槛 0.55 剩下 2 条」——
   这类具体到某一次运行的统计，一小时后就没用了。

C. 操作动作与指令
   「用户要求导出对话」「执行了某条命令」「打开了某个页面」「用户说按你说的来」。
   动作本身不是知识，动作背后的判断才是。

D. 对本系统自身调试过程的记录
   「某表是某天的快照」「某参数改成某值后剩几条」「某功能还没接上」。
   除非这条记录写出了可复用的判据（例如「bge-m3 必须 L2 归一化，否则分数全废」），
   否则不要。

E. 用户对 AI 的元评价
   「这个方案好」「继续」「按你说的来」「两个一起」。

F. 只有当「记住这次做过什么」有价值、而「下次怎么做」没有价值的内容。

其他规则：
1. 每条必须落在具体的东西上——名字、数字、路径、命令、端口、型号、动作、原因。
2. 禁止抽象化：不写「讨论了 X」「优化了 Y」「觉得 Z 很重要」。要写「把 A 改成 B，因为 C」。
3. 一条一件事，不要合并。同一件事的不同阶段分开写。
4. 保留失败尝试和踩过的坑——那是以后能省时间的地方。
5. 类型只能是 fact / tool / event / preference / experience 之一。
6. 如果这段对话是用户在请 AI 代写作业、范文、作文、实习总结、报告、检讨书、情书这类
   「用户不需要自己拥有的知识」，直接返回 []，不要提取任何内容。
7. 如果内容是 AI 自己生成的范例文本（不是用户本人的决策、操作、踩坑），返回 []。
8. content 里不要抄原文、不要写「原文：」前缀、不要用引号包裹对话片段。
   用你自己的话写成一句陈述句。

输出 JSON 数组，不要别的：
[{"type": "...", "title": "12 字以内", "content": "一段话，含锚点"}]

对话：
"""


def _post(payload, timeout=300):
    if isinstance(payload, dict):
        payload = dict(payload)
        payload.update(_effort_kw("learn"))
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        LLM_URL,
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + LLM_KEY},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _fallback(text, max_cards=8):
    """演示端点限流/返回空时，用本地规则抽两条，保证流程走得通。"""
    if not DEMO_MODE:
        return []
    try:
        from .demoextract import split_cards
        cards = split_cards(text, max_cards)
        if cards:
            print("[demo] 上游没给出可用结果，已用本地规则抽 %d 条（仅演示）" % len(cards), flush=True)
        return cards
    except Exception:
        return []


def extract(text, max_cards=8, max_tokens=8000):
    text = (text or "").strip()
    if not text:
        return []
    if not LLM_KEY:
        # 默认的演示端点不需要 key；换成需要鉴权的上游时请设 MEMVAULT_LLM_KEY
        print("[warn] MEMVAULT_LLM_KEY 未设置：演示端点可直连，其他上游会 401", flush=True)
    text = text[:24000]
    d = _post({
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": PROMPT + text}],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    })
    msg = d["choices"][0]["message"]
    out = (msg.get("content") or "").strip()
    rc = (msg.get("reasoning_content") or "").strip()
    # reasoning 模型常把 token 全花在 thinking 上，content 空；从 reasoning 里兜底找 JSON
    m = re.search(r"\[.*\]", out, re.S)
    if not m and rc:
        m = re.search(r"\[[\s\S]*\]", rc, re.S)
    if not m:
        return _fallback(text, max_cards)
    try:
        items = json.loads(m.group(0))
    except Exception:
        return _fallback(text, max_cards)
    if not isinstance(items, list):
        return []
    cards = []
    for it in items[:max_cards]:
        c = (it.get("content") or "").strip()
        if not c:
            continue
        cards.append({
            "title": (it.get("title") or c.split("\n")[0])[:120],
            "body": c,
            "kind": it.get("type") or it.get("kind"),
            "topic": it.get("topic"),
        })
    return cards or _fallback(text, max_cards)
