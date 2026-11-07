#!/usr/bin/env node
/**
 * pitsync —— 从 DSH 原始会话里自动捞「踩坑」，推到摇篮。
 *
 * 判定：tool/result 里出现真错误 → 找随后的 tool/call 命令当「修法」。
 * 只推有修法的（高置信），没修法的留给人工。
 *
 * 用法：pitsync [--days 14] [--dry]
 */
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const { execFileSync } = require("child_process");

const SD = process.env.PITSRC || "/path/to/sessions/phone";
const RECENT = process.env.PITSRC_LIVE || "";
const API = process.env.CRADLE_API || "http://127.0.0.1:4174";
const TOKEN = (() => {
  try { return fs.readFileSync(process.env.HOME + "/.cradle-token", "utf8").trim(); } catch { return ""; }
})();

const ERR = /(?:\b\w*(?:Error|Exception)\b\s*[:：]|Traceback \(most recent call last\)|EADDRINUSE|ECONNREFUSED|ECONNRESET|ENOENT|EACCES|ETIMEDOUT|cannot assign requested address|no such (?:file|column|table|module)|database disk image is malformed|permission denied|command not found|exit code [1-9]|Exited \(\d+\)|listen tcp4? [^ ]+ bind|ModuleNotFoundError|ImportError|SyntaxError|IndentationError|NameError|UnboundLocalError)/i;
const FIXY = /(docker|systemctl|curl|ssh|sed|grep|python|node|npm|pnpm|apt|rm |mv |cp |chmod|iptables|sqlite3|export |\/etc\/|\/opt\/|--\w+)/;

function textOf(ev) {
  const buf = [];
  const walk = (x, d = 0) => {
    if (d > 6 || x == null) return;
    if (typeof x === "string") buf.push(x);
    else if (Array.isArray(x)) x.forEach((v) => walk(v, d + 1));
    else if (typeof x === "object") Object.values(x).forEach((v) => walk(v, d + 1));
  };
  walk(ev.data);
  walk(ev.error);
  return buf.join("\n");
}

function loadEvents(file) {
  let raw;
  if (file.endsWith(".zstd")) {
    raw = execFileSync("zstd", ["-dc", file], { maxBuffer: 512 * 1024 * 1024 }).toString("utf8");
  } else {
    raw = fs.readFileSync(file, "utf8");
  }
  const out = [];
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    try { out.push(JSON.parse(line)); } catch {}
  }
  return out;
}

function errLine(t) {
  for (const l of String(t || "").split(/[\r\n]+/)) {
    const s = l.trim();
    if (s.length < 8 || s.length > 400) continue;
    if (!ERR.test(s)) continue;
    const cjk = (s.match(/[\u4e00-\u9fff]/g) || []).length / s.length;
    if (cjk > 0.1) continue;
    if (/=>|\?\?|===|\bconst\b|\bfunction\s*\(/.test(s)) continue;
    return s;
  }
  return null;
}

function normSig(s) {
  return s
    .replace(/^\s*(?:\$|#|>)\s*/, "")
    .replace(/\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?/g, " ")
    .replace(/\b(?:10|192\.168)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b/g, "IP")
    .replace(/:\d{2,5}\b/g, ":PORT")
    .replace(/(?:[\w.-]+\/)+([\w.-]{2,})/g, "$1")
    .replace(/\b0x[0-9a-f]{4,}\b/gi, "HEX")
    .replace(/\b\d{3,}\b/g, "N")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 140);
}

function scan(file, cutoffMs) {
  const evs = loadEvents(file);
  const items = [];
  for (let i = 0; i < evs.length; i++) {
    const ev = evs[i];
    if (ev.type !== "tool/result") continue;
    const ts = Date.parse(ev.time || ev.createdAt || 0);
    if (cutoffMs && ts && ts < cutoffMs) continue;
    const t = textOf(ev);
    const line = errLine(t);
    if (!line) continue;
    // 往后 6 条事件里找一条 tool/call 当修法
    let fix = null, fixName = null;
    for (let j = i + 1; j < Math.min(evs.length, i + 8); j++) {
      const n = evs[j];
      const nm = n?.data?.name || n?.name;
      const args = JSON.stringify(n?.data?.arguments || n?.data?.args || "");
      if (n.type === "tool/call" && args && FIXY.test(args)) {
        fix = args.replace(/\\n/g, " ").slice(0, 240);
        fixName = nm;
        break;
      }
    }
    if (!fix) continue;
    const sig = normSig(line);
    if (sig.length < 8) continue;
    items.push({
      title: ("踩坑：" + sig).slice(0, 80),
      topic: sig,
      body: `现象：${line}\n修法（后来执行的命令，${fixName || "?"}）：${fix}\n来源：${path.basename(file)}`,
      quote: line.slice(0, 400),
      source: "pitfall/phone:" + path.basename(file).slice(0, 40),
      score: 6,
    });
  }
  return items;
}

(async () => {
  const days = Number((process.argv.find((a) => a.startsWith("--days=")) || "--days=14").split("=")[1]) || 14;
  const dry = process.argv.includes("--dry");
  const cutoff = Date.now() - days * 864e5;

  const files = [];
  for (const f of fs.readdirSync(SD)) if (f.endsWith(".jsonl")) files.push(path.join(SD, f));
  try {
    for (const d of fs.readdirSync(RECENT)) {
      const p = path.join(RECENT, d, "session.v4.jsonl.zstd");
      if (fs.existsSync(p)) files.push(p);
    }
  } catch {}

  const seen = new Set();
  const all = [];
  for (const f of files) {
    let got = [];
    try { got = scan(f, cutoff); } catch (e) { continue; }
    for (const it of got) {
      if (seen.has(it.topic)) continue;
      seen.add(it.topic);
      all.push(it);
    }
  }
  console.log(`扫描 ${files.length} 个会话，得到 ${all.length} 条候选`);
  if (!all.length) return;
  if (dry) { all.slice(0, 10).forEach((it) => console.log("-", it.topic)); return; }

  const r = await fetch(API + "/api/pitfall/ingest", {
    method: "POST",
    headers: { "content-type": "application/json", ...(TOKEN ? { authorization: "Bearer " + TOKEN } : {}) },
    body: JSON.stringify({ items: all.slice(0, 50) }),
  });
  console.log(r.status, (await r.text()).slice(0, 300));
})();
