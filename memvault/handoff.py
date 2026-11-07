# -*- coding: utf-8 -*-
"""无缝换窗：新会话首条消息时，把上一个会话的结尾原文接上。

只在「本会话第一次出现」时返回内容，之后一律空字符串，
所以对同一会话的后续轮次零成本（一次 COUNT 查询）。

参数（settings 表）：
  handoff_enabled      1/0   开关
  handoff_tail_count   6     带几条原文（约 3 轮问答）
  handoff_summary_model  ''  预留：生成概要用，当前未用
"""

import os
import sqlite3


def _prev_conv(c, ck):
    row = c.execute(
        "SELECT conv_id, MAX(at) AS t FROM conv_log WHERE conv_id<>? "
        "GROUP BY conv_id ORDER BY t DESC LIMIT 1", (ck,)).fetchone()
    return row[0] if row else None


def handoff_block(ck):
    """新会话首条时的衔接块；非首条返回空串。"""
    from .gateway import to_bool, gw_setting, log, con   # 延迟导入，避免循环
    try:
        if not ck:
            return ""
        if not to_bool(gw_setting("handoff_enabled", "0")):
            return ""
        c = con()
        n = c.execute("SELECT COUNT(*) FROM conv_log WHERE conv_id=?", (ck,)).fetchone()[0]
        if n:
            return ""
        prev = _prev_conv(c, ck)
        if not prev:
            return ""
        try:
            tail = int(gw_setting("handoff_tail_count", "6") or 6)
        except Exception:
            tail = 6
        tail = max(0, min(20, tail))
        rows = c.execute(
            "SELECT role, content FROM conv_log WHERE conv_id=? ORDER BY id DESC LIMIT ?",
            (prev, tail + 1)).fetchall()
        if not rows:
            return ""
        rows = list(reversed(rows))
        out = [
            '<handoff source="memvault" note="这是上一个会话的结尾原文。'
            '用户换了新窗口继续，可能接着这个话题；但他新说的话优先级更高，'
            '如果新话题与这里无关，就当它不存在">'
        ]
        for role, content in rows:
            txt = (content or "").strip()
            if not txt:
                continue
            who = "用户" if role == "user" else "你"
            if len(txt) > 500:
                txt = txt[:500].rstrip() + "…"
            out.append("%s：%s" % (who, txt))
        out.append("</handoff>")
        if len(out) <= 2:
            return ""
        log("handoff: ck=%s <- prev=%s (%d 条)" % (ck, prev, len(rows)))
        return "\n".join(out)
    except Exception as e:
        log("handoff_block:", e)
        return ""
