# -*- coding: utf-8 -*-
"""演示模式用的本地兜底抽取器（不联网、不花钱）。

默认的免费演示端点（text.pollinations.ai）会限流，一旦 402，抽取就一条都出不来。
这个兜底保证「clone 下来跑一遍」至少能看到卡片进库：

  · 按中英文标点切句
  · 留下带「事实感」的句子（有 / 是 / 用 / 住 / 叫 / 决定 / 喜欢 / 选 / 装 / 换 …）
  · 归成 note 卡，标题取前 24 字

它不是模型，只是让流程能走通。真要用，请换成自己的 API（见 docs/DEPLOY.md）。
"""
import re

_SPLIT = re.compile(r"[。！？!?\n]+")
_KEEP = re.compile(r"(是|有|用|住|叫|决定|喜欢|选|装|换|不要|必须|习惯|偏好|在|做|搞|上|下)")
_DROP = re.compile(r"^(嗯|好的|哈哈|谢谢|收到|ok|OK)\b")
MAXLEN = 600


def split_cards(text, max_cards=8):
    """把一段话切成一两条 note 卡；抽不出来就返回空列表。"""
    text = (text or "").strip()
    if not text:
        return []
    out, seen = [], set()
    for raw in _SPLIT.split(text):
        s = raw.strip(" \t\r，,；;：:")
        if len(s) < 6 or len(s) > MAXLEN:
            continue
        if _DROP.match(s):
            continue
        if not _KEEP.search(s):
            continue
        key = s[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "title": s[:24],
            "body": s,
            "kind": "note",
            "topic": None,
        })
        if len(out) >= max_cards:
            break
    return out
