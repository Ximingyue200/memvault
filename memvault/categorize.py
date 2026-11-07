"""把 topic 归到固定分类树（LLM + 规则兜底）。"""
import json
import os
import re
import sqlite3
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

CATS = [
    "硬件/CPU", "硬件/内存", "硬件/存储", "硬件/主板整机", "硬件/网络设备",
    "硬件/显示外设", "硬件/手机平板",
    "系统/虚拟化", "系统/网络", "系统/Linux", "系统/Windows",
    "开发/语言", "开发/工具", "开发/AI模型", "开发/逆向破解",
    "应用/记忆库", "应用/游戏", "应用/手机", "应用/网络服务",
    "应用/硬件设计", "应用/影音图像",
    "学习/学习方法", "学习/外语", "学习/资料整理",
    "工作/职业规划", "工作/协作沟通",
    "生活/健康", "生活/消费", "生活/情感关系", "生活/家庭日常",
    "兴趣/影视动漫", "兴趣/阅读写作", "兴趣/音乐",
    "其它",
]

PROMPT = """把下面这些技术关键词归到给定分类。只输出 JSON 对象，键是关键词，值是分类名。

分类只能从这些里选：
{cats}

关键词（每行一个）：
{words}

规则：
1 一个关键词只能有一个分类
2 看不出属于哪个就用「其它」
3 手机刷机/Magisk/IMEI 归「应用/手机」
4 PVE/LXC/容器/直通 归「系统/虚拟化」
5 OpenWrt/QWRT/AdGuard/Clash/DNS/DHCP/VPN/frp 归「系统/网络」
6 XXXX/7840/AMD/Intel CPU 归「硬件/CPU」
7 SSD/傲腾/SATA/NVMe/扩容盘 归「硬件/存储」
8 记忆库/OpenViking/memvault/cradle/MCP 归「应用/记忆库」

只输出 JSON，不要解释。"""

BASE = os.environ.get("MEMVAULT_LLM_BASE", "https://api.deepseek.com/v1")
KEY = os.environ.get("MEMVAULT_LLM_KEY", "")
MODEL = os.environ.get("MEMVAULT_LLM_MODEL", "deepseek-chat")


def _llm(prompt, max_tokens=2000):
    body = json.dumps({
        "model": MODEL, "temperature": 0.1, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        **_effort_kw("learn"),
    }).encode()
    req = urllib.request.Request(
        BASE.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + KEY})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"]


def main(db):
    con = sqlite3.connect(db)
    try:
        con.execute("ALTER TABLE cards ADD COLUMN category TEXT")
        con.commit()
    except sqlite3.OperationalError:
        pass
    rows = list(con.execute(
        "SELECT topic, count(*) n FROM cards WHERE status='active' "
        "AND topic IS NOT NULL AND topic<>'' GROUP BY topic ORDER BY n DESC"))
    topics = [t for t, _ in rows if t]
    print(f"topics: {len(topics)}", flush=True)

    mapping = {}
    B = 120
    for i in range(0, len(topics), B):
        chunk = topics[i:i + B]
        prompt = PROMPT.format(cats="\n".join(CATS), words="\n".join(chunk))
        try:
            raw = _llm(prompt, max_tokens=3000)
            m = json.loads(raw)
            for k, v in m.items():
                if v in CATS:
                    mapping[k] = v
        except Exception as e:
            print(f"  batch {i}: {e}", flush=True)
        print(f"  {i+len(chunk)}/{len(topics)} mapped={len(mapping)}", flush=True)

    con.executemany("UPDATE cards SET category=? WHERE topic=?",
                    [(v, k) for k, v in mapping.items()])
    con.execute("UPDATE cards SET category='其它' WHERE category IS NULL")
    con.commit()
    print("--- 分布 ---", flush=True)
    for r in con.execute(
            "SELECT category, count(*) n FROM cards WHERE status='active' "
            "GROUP BY category ORDER BY n DESC"):
        print(f"  {r[0]:16s} {r[1]:6d}")
    con.close()


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "/opt/memvault/data/memvault.db")
