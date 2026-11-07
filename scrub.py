#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 memvault 的卡片导出成「可公开」的脱敏数据集。

隐私策略（四层）：
  L1 硬屏蔽  —— 凭据类内容整卡丢弃（密码/密钥/token/cookie/身份证/银行卡…）
  L2 结构替换 —— IP / MAC / URL / 邮箱 / UUID / 用户名 / 手机号 → 占位符
  L3 语义泛化 —— 真实人名 / 公司 / 学校 / 医院 / 具体住址 → 泛化词
  L4 白名单留 —— 型号、端口号、软件版本、命令这类技术锚点必须留（否则数据集没用）

用法：
  python3 scrub.py --db /path/memvault.db --out ./public
  python3 scrub.py --db ... --out ... --report    # 只出统计，不写文件
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict

# ---------------------------------------------------------------- L1 硬屏蔽
# 命中即整卡丢弃（不看上下文，宁可错杀）
BLOCK_PATTERNS = [
    (r"\bpassword\s*[:=]\s*\S+", "password-assign"),
    (r"\bpasswd\s*[:=]\s*\S+", "passwd-assign"),
    (r"(?i)\bpwd\s*[:=]\s*\S+", "pwd-assign"),
    (r"\bsk-[A-Za-z0-9_\-]{16,}", "openai-key"),
    (r"\bgh[pousr]_[A-Za-z0-9]{20,}", "github-token"),
    (r"\bglpat-[A-Za-z0-9_\-]{16,}", "gitlab-token"),
    (r"\bxox[baprs]-[A-Za-z0-9\-]{10,}", "slack-token"),
    (r"\bAKIA[0-9A-Z]{16}\b", "aws-key"),
    (r"\bBearer\s+[A-Za-z0-9\-._~+/]{20,}", "bearer-token"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private-key"),
    (r"\b\d{17}[\dXx]\b", "cn-id-card"),          # 身份证 18 位
    (r"\b\d{15}\b(?=.*身份证)", "cn-id-card-15"),
    (r"\b62\d{14,17}\b", "bank-card"),              # 银联卡
    (r"(?i)\bcookie\s*[:=]\s*\S{20,}", "cookie-value"),
    (r"(?i)\bsession(id)?\s*[:=]\s*[A-Za-z0-9_\-]{20,}", "session-value"),
    # 中文叙述里的真实凭据值（「密码为 xxx」「账号是 xxx」）
    (r"(?:密码|口令|passwd|password)\s*(?:为|是|=|:|：)\s*[^\s，。；、]{3,}", "pwd-value-cn"),
    (r"(?:账号|账户|用户名)\s*(?:为|是|=|:|：)\s*[A-Za-z0-9@._\-]{3,}", "account-value-cn"),
    # 明文 JWT
    (r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}", "jwt"),
    # 组网 / 隧道 / 私有服务的 access token（长字母数字串，且前文有 token/secret/key 语义）
    (r"(?i)(?:token|secret|api ?key|access ?key)\s*(?:为|是|=|:|：)\s*[A-Za-z0-9_\-\.]{10,}", "token-value"),
    # 32 位裸 hash（md5 风格）——可能是密码散列或设备 ID
    (r"\b[0-9a-f]{32}\b", "bare-hash32"),
    # 手机号/微信/QQ 标明身份
    (r"(?:微信|QQ|手机)[号:：]\s*\d{5,}", "im-id"),
]

# ---------------------------------------------------------------- L2 结构替换
REPLACERS = [
    # 私有网段 / 常见内网 IP → 按出现顺序编号
    (re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "IP_PRIVATE"),
    (re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "IP_PRIVATE"),
    (re.compile(r"\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b"), "IP_PRIVATE"),
    # 公网 IP 也模糊掉（除非是文档保留地址）
    (re.compile(r"\b(?!127\.|0\.|255\.|8\.8\.8\.8|1\.1\.1\.1)(?:\d{1,3}\.){3}\d{1,3}\b"), "IP_PUBLIC"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "MAC"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "UUID"),
    (re.compile(r"\b[\w.+-]+@(?:[\w-]+\.)+[a-zA-Z]{2,}\b"), "EMAIL"),
    (re.compile(r"\b1[3-9]\d{9}\b"), "PHONE_CN"),
    # 手机号被写成 197xxxxx（非 1[3-9] 开头的老号段，也一并按 11 位数字兜底）
    (re.compile(r"\b1\d{10}\b"), "PHONE_CN"),
    # 座机 / 带区号固定电话：0 + 2~3 位区号 + 7~8 位号码（0914-6563108 / 010-12345678）
    (re.compile(r"\b0\d{2,3}[- ]?\d{7,8}\b"), "PHONE_LANDLINE"),
    # URL：保留 scheme + 端口号（技术锚点），域名替换
    (re.compile(r"(https?)://([^/\s:\)\]]+)"), "URL"),
]

# ---------------------------------------------------------------- L3 语义泛化
# 只泛化「明显指向真实主体」的词，不碰技术名词
GENERIC_WORDS = {
    # 人名（用户自述/转述里出现过的）
    "助手": "USER", "大飞鱼": "USER", "Ximingyue200": "USER", "ximingyue200": "USER",
    "example-user": "USER",
    # 家庭/居住
    "家里": "住处", "我家": "住处", "老家": "住处",
}

# 需要泛化的「具体机构名」—— 用正则粗匹配（后缀驱动，不点具体名字）
ORG_PATTERNS = [
    (re.compile(r"[\u4e00-\u9fa5]{2,6}(?:大学|学院|中学|小学)"), "SCHOOL"),
    (re.compile(r"[\u4e00-\u9fa5]{2,8}(?:人民医院|附属医院|医院|卫生院)"), "HOSPITAL"),
    (re.compile(r"[\u4e00-\u9fa5]{2,10}(?:有限公司|股份公司|集团|科技公司)"), "COMPANY"),
    # 镇 / 乡 / 村 / 街道 级地名（"金丝峡镇"、"XX村委会"）
    (re.compile(r"[\u4e00-\u9fa5]{2,6}(?:镇|乡|村委会|街道办|街道)(?![\u4e00-\u9fa5]{0,2}(?:域名|端口|文件))"), "PLACE"),
    (re.compile(r"[\u4e00-\u9fa5]{2,5}村(?![民口道])"), "PLACE"),
]

ALLOW_TECH = {
    # 这些词里含 IP/URL 字样但不是隐私，先保留
}

_counter = defaultdict(Counter)


def _tag(kind, raw):
    """同一类占位符按出现顺序编号：IP_PRIVATE_1 / IP_PRIVATE_2 ..."""
    _counter[kind][raw] += 1
    return raw


def _map(kind, raw):
    key = (kind, raw.lower())
    idx = _counter_maps.get(key)
    if idx is None:
        idx = len(_counter_maps_by_kind[kind]) + 1
        _counter_maps[key] = idx
        _counter_maps_by_kind[kind][raw.lower()] = idx
    return "<%s_%d>" % (kind, idx)


_counter_maps = {}
_counter_maps_by_kind = defaultdict(dict)


def scrub_text(text):
    """返回 (脱敏后文本, 命中记录列表)"""
    hits = []
    if not text:
        return "", hits

    # L1
    for pat, name in BLOCK_PATTERNS:
        if re.search(pat, text):
            hits.append(("L1", name))
            return None, hits          # 整卡作废

    out = text

    # L3 先做（机构名、人名），避免被 L2 拆碎
    for w, rep in GENERIC_WORDS.items():
        if w in out:
            out = out.replace(w, "<%s>" % rep)
            hits.append(("L3", "word:" + w))
    for pat, name in ORG_PATTERNS:
        found = pat.findall(out)
        for f in found:
            out = out.replace(f, _map(name, f))
            hits.append(("L3", name))

    # L2
    for pat, name in REPLACERS:
        if name == "URL":
            def _url(m):
                scheme, host = m.group(1), m.group(2)
                return "%s://%s" % (scheme, _map("DOMAIN", host))
            new = pat.sub(_url, out)
            if new != out:
                hits.append(("L2", "URL"))
                out = new
        else:
            def _sub(m, _n=name):
                return _map(_n, m.group(0))
            new = pat.sub(_sub, out)
            if new != out:
                hits.append(("L2", name))
                out = new

    # 端口号保留（技术锚点），但 <IP_x>:<port> 这种组合已在上一步拆开
    return out, hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--min-len", type=int, default=20, help="正文短于此值丢弃")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT id, title, body, kind, category, topic, owner, occurred_at, created_at, importance
        FROM cards
        WHERE status='active' AND owner IN ('human','both')
        ORDER BY id
    """).fetchall()

    kept, dropped = [], []
    stat_hits = Counter()
    drop_reason = Counter()

    for r in rows:
        body = (r["body"] or "").strip()
        if len(body) < args.min_len:
            dropped.append((r["id"], "too-short"))
            drop_reason["too-short"] += 1
            continue
        new_body, h1 = scrub_text(body)
        if new_body is None:
            sec = [x[1] for x in h1 if x[0] == "L1"]
            dropped.append((r["id"], "blocked:" + ",".join(sec)))
            drop_reason["blocked:" + ",".join(sec)] += 1
            continue
        new_title, h2 = scrub_text(r["title"] or "")
        if new_title is None:
            dropped.append((r["id"], "blocked-title"))
            drop_reason["blocked-title"] += 1
            continue
        for lv, name in h1 + h2:
            stat_hits["%s:%s" % (lv, name)] += 1
        kept.append({
            "id": r["id"],
            "title": new_title.strip()[:120],
            "body": new_body,
            "kind": r["kind"],
            "category": r["category"] or "其它",
            "topic": r["topic"],
            "occurred_at": (r["occurred_at"] or r["created_at"] or "")[:10],
            "importance": r["importance"],
        })

    print("=" * 60)
    print("输入卡片（active + human/both）: %d" % len(rows))
    print("保留                         : %d" % len(kept))
    print("丢弃                         : %d" % len(dropped))
    print("-" * 60)
    print("丢弃原因:")
    for k, v in drop_reason.most_common():
        print("  %-30s %d" % (k, v))
    print("-" * 60)
    print("脱敏命中（按层）:")
    for k, v in sorted(stat_hits.items(), key=lambda x: -x[1])[:25]:
        print("  %-30s %d" % (k, v))
    print("-" * 60)
    print("占位符种类:")
    for kind, m in _counter_maps_by_kind.items():
        print("  %-14s %d 种" % (kind, len(m)))

    if args.report or not args.out:
        return

    os.makedirs(args.out, exist_ok=True)
    # 按 category 分文件，方便看
    by_cat = defaultdict(list)
    for c in kept:
        by_cat[c["category"]].append(c)
    for cat, items in sorted(by_cat.items()):
        fn = cat.replace("/", "_").replace(" ", "_") + ".json"
        with open(os.path.join(args.out, fn), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, "all.json"), "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=1)
    print("\n已写入 %s（%d 个分类文件 + all.json）" % (args.out, len(by_cat)))


if __name__ == "__main__":
    main()
