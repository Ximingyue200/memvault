"""HTTP 层：给 DSH 插件用的三个端点。

    GET  /health
    GET  /find?q=xxx&limit=6      autoRecall 用
    GET  /search?q=xxx&limit=10   主动检索
    POST /memlearn                写入一张卡

用标准库，无第三方依赖。
"""
import json
import os
import re
import sqlite3
import re
import shutil
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import embed, extract as extract_mod, search as search_mod, store, tree as _tree, selfnote as _sn
from . import dream as _dream
from . import summary as _summary
from . import profile as _profile
from . import procedure as _procedure
from . import focus as _focus

DB = os.environ.get("MEMVAULT_DB", "/opt/memvault/data/memvault.db")
HOST = os.environ.get("MEMVAULT_HOST", "0.0.0.0")
PORT = int(os.environ.get("MEMVAULT_PORT", "4174"))
API_KEY = os.environ.get("MEMVAULT_KEY", "")

_lock = threading.Lock()
SPANS = {}  # span_id -> conv_id, 启动时从 cradle 灌一次

UI_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MemVault</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font:15px/1.65 -apple-system,"PingFang SC","Noto Sans CJK SC",sans-serif;padding:16px}
header{position:sticky;top:0;background:#0d1117;padding:12px 0;border-bottom:1px solid #21262d;z-index:9}
h1{font-size:15px;font-weight:600;color:#58a6ff;letter-spacing:.5px;display:inline}
.meta{font-size:12px;color:#6e7681;margin-left:10px}
form{display:flex;gap:8px;margin-top:12px}
input{flex:1;background:#161b22;border:1px solid #30363d;border-radius:6px;padding:9px 12px;color:#c9d1d9;font-size:15px;outline:none}
input:focus{border-color:#58a6ff}
button{background:#238636;border:0;border-radius:6px;padding:9px 18px;color:#fff;font-size:14px;cursor:pointer}
button:active{background:#2ea043}
.stat{margin:14px 0 6px;font-size:12px;color:#6e7681}
.card{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:14px;margin-bottom:10px}
.card:hover{border-color:#30363d}
.row{display:flex;align-items:baseline;gap:8px;margin-bottom:6px}
.score{font:11px/1 ui-monospace,monospace;background:#1f6feb22;color:#58a6ff;padding:3px 6px;border-radius:4px;flex:none}
.kind{font-size:11px;color:#8b949e;background:#21262d;padding:2px 6px;border-radius:4px;flex:none}
.title{font-size:15px;font-weight:600;color:#e6edf3}
.body{font-size:13.5px;color:#8b949e;white-space:pre-wrap;word-break:break-word;max-height:4.5em;overflow:hidden;cursor:pointer}
.body.open{max-height:none;color:#c9d1d9}
.empty{color:#6e7681;font-size:13px;padding:30px 0;text-align:center}

@media (max-width:700px){
.wrap{display:block}
nav{width:auto;max-height:none;position:static;border-right:none;border-bottom:1px solid #262a31;
    display:flex;overflow-x:auto;overflow-y:hidden;white-space:nowrap;padding:0 8px;top:auto}
nav .grp{display:none}
nav a{flex:0 0 auto;padding:9px 12px;border-left:none;border-bottom:2px solid transparent;font-size:12.5px}
nav a.on{border-left:none;border-bottom-color:#7cc4ff;background:transparent}
main{padding:16px 14px 80px}
h1{font-size:18px}
}
</style></head><body>
<header>
  <h1>MEMVAULT</h1><span class="meta" id="meta">loading…</span>
  <form id="f"><input id="q" placeholder="搜一句话 / 一个端口 / 一个包名…" autocomplete="off" autofocus><button>搜索</button></form>
</header>
<div class="stat" id="stat"></div>
<div id="out"></div>
<script>
const out=document.getElementById('out'),stat=document.getElementById('stat'),meta=document.getElementById('meta');
fetch('/health').then(r=>r.json()).then(d=>{meta.textContent=d.cards+' cards · '+d.vectors+' vectors'}).catch(()=>{meta.textContent='offline'});
document.getElementById('f').onsubmit=e=>{e.preventDefault();go()};
async function go(){
  const q=document.getElementById('q').value.trim(); if(!q)return;
  out.innerHTML='<div class="empty">检索中…</div>'; stat.textContent='';
  const t0=performance.now();
  try{
    const r=await fetch('/find?q='+encodeURIComponent(q)+'&limit=20&max_chars=900');
    const d=await r.json();
    const ms=Math.round(performance.now()-t0);
    stat.textContent=d.count+' 条结果 · '+ms+' ms';
    if(!d.count){out.innerHTML='<div class="empty">没有命中</div>';return}
    out.innerHTML=d.results.map(c=>`<div class="card">
      <div class="row"><span class="score">${c.score.toFixed(3)}</span>
      <span class="kind">${c.kind||'-'}</span><span class="title">${esc(c.title)}</span></div>
      <div class="body" onclick="this.classList.toggle('open')">${esc(c.content)}</div></div>`).join('');
  }catch(e){out.innerHTML='<div class="empty">出错: '+e+'</div>'}
}
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
</script></body></html>"""



_WEBUI = os.path.join(os.path.dirname(__file__), "webui.html")
_webui_cache = {"mtime": 0.0, "html": ""}


GWENV = "/etc/memvault-gw.env"
GWENV_META = [
    ("GW_UPSTREAM", "上游 API 域名", "api.deepseek.com", "只写域名，不带 https://"),
    ("GW_TOPK", "注入卡片数", "6", "每轮注入几张卡；越大越慢越贵"),
    ("GW_MIN_SCORE", "最低分", "0.30", "低于这个分数的卡不注入"),
    ("GW_DEBUG", "调试日志", "1", "1=把每轮 query 记进日志"),
    ("MEMVAULT_EMBED_URL", "向量服务地址", "http://127.0.0.1:11434/v1/embeddings", "OpenAI 兼容 /v1/embeddings"),
    ("MEMVAULT_EMBED_MODEL", "向量模型", "bge-m3", "必须与库里 vectors 同维（1024）"),
    ("MEMVAULT_CHUNK", "切块字数", "2800", "每块最多多少字"),
]
GWENV_SECRET = [("MEMVAULT_LLM_KEY", "上游 API Key"), ("GW_TOKEN", "网关令牌")]


def _gwenv_read():
    d = {}
    try:
        for line in open(GWENV, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    except Exception:
        pass
    return d


def _gwenv_state():
    cur = _gwenv_read()
    items = {}
    for k, label, dv, hint in GWENV_META:
        items[k] = {"v": cur.get(k, ""), "label": label, "def": dv, "hint": hint, "secret": False}
    for k, label in GWENV_SECRET:
        items[k] = {"v": ("****" + cur.get(k, "")[-4:]) if cur.get(k) else "", "label": label,
                    "def": "", "hint": "留空表示不改", "secret": True}
    return {"path": GWENV, "items": items,
            "restart_hint": "保存后自动重启 memvault-gw（4174 不受影响）"}


def _gwenv_write(updates):
    cur = _gwenv_read()
    allowed = set(m[0] for m in GWENV_META) | set(x[0] for x in GWENV_SECRET)
    n = 0
    for k, v in (updates or {}).items():
        if k not in allowed:
            continue
        if v is None or (isinstance(v, str) and v.startswith("****")):
            continue
        cur[k] = str(v)
        n += 1
    order = [m[0] for m in GWENV_META] + [x[0] for x in GWENV_SECRET]
    seen, lines = set(), ["# 由 4174 设置页写入 %s" % time.strftime("%Y-%m-%d %H:%M:%S")]
    for k in order:
        if k in cur:
            lines.append("%s=%s" % (k, cur[k]))
            seen.add(k)
    for k, v in cur.items():
        if k not in seen:
            lines.append("%s=%s" % (k, v))
    tmp = GWENV + ".tmp"
    open(tmp, "w", encoding="utf-8").write(chr(10).join(lines) + chr(10))
    os.chmod(tmp, 0o600)
    os.replace(tmp, GWENV)
    return n


def _webui_html():
    """新 UI：单文件 SPA，改文件即生效（按 mtime 缓存）。"""
    try:
        mt = os.path.getmtime(_WEBUI)
        if mt != _webui_cache["mtime"] or not _webui_cache["html"]:
            with open(_WEBUI, encoding="utf-8") as f:
                _webui_cache["html"] = f.read()
            _webui_cache["mtime"] = mt
        return _webui_cache["html"]
    except Exception:
        return UI_HTML


TL_HTML = "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n<title>cradle \u00b7 \u7f16\u5e74\u53f2</title>\n<style>\n*{box-sizing:border-box}\nbody{margin:0;background:#14161a;color:#d8dbe0;font:15px/1.7 -apple-system,system-ui,\"PingFang SC\",sans-serif}\nheader{position:sticky;top:0;background:#14161a;border-bottom:1px solid #262a31;padding:12px 16px;display:flex;gap:12px;align-items:center;z-index:5}\nheader b{color:#7cc4ff;font-weight:600}\nheader a{color:#6b7280;text-decoration:none;font-size:13px}\n.wrap{display:block;min-height:100vh}\nnav{display:flex;overflow-x:auto;white-space:nowrap;padding:0 8px;border-bottom:1px solid #262a31;position:sticky;top:49px;background:#14161a;z-index:4}\nnav .grp{display:none}\nnav a{display:block;flex:0 0 auto;padding:9px 12px;color:#9aa3af;text-decoration:none;font-size:12.5px;border-bottom:2px solid transparent}\nnav a:hover{background:#1b1e24;color:#e5e7eb}\nnav a.on{border-bottom-color:#7cc4ff;color:#e5e7eb}\nmain{flex:1;min-width:0;padding:20px 18px 80px;overflow-x:hidden}\nh1{font-size:20px;margin:0 0 4px;font-weight:600;color:#e5e7eb}\n.meta{font-size:12px;color:#6b7280;margin-bottom:18px}\n.sum{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;font-size:14.5px;line-height:1.85;color:#c3c9d2}\n.sec{margin-top:26px}\n.sec h3{font-size:13px;color:#6b7280;font-weight:500;margin:0 0 8px;letter-spacing:.05em}\n.item{padding:8px 0;border-bottom:1px solid #1e2229;font-size:13.5px;color:#9aa3af}\n.item b{color:#c3c9d2;font-weight:500}\n.tag{display:inline-block;padding:1px 7px;border-radius:3px;font-size:11px;margin-right:6px;background:#1e2530;color:#7cc4ff}\n.tag.done{background:#152a1e;color:#4ade80}\n.tag.hold{background:#2a2415;color:#fbbf24}\n.empty{color:#4b5563;font-size:13px}\n@media (min-width:900px){\n.wrap{display:flex}\nnav{display:block;width:190px;flex:0 0 190px;border-right:1px solid #262a31;border-bottom:none;padding:8px 0;overflow-y:auto;max-height:calc(100vh - 49px)}\nnav .grp{display:block;font-size:11px;color:#4b5563;padding:10px 10px 4px;letter-spacing:.08em}\nnav a{padding:6px 10px;border-bottom:none;border-left:2px solid transparent}\nnav a.on{border-left-color:#7cc4ff;border-bottom-color:transparent;background:#1b1e24}\nmain{padding:24px 26px 80px;max-width:820px}\n}\n</style></head><body>\n<header><b>cradle</b><span style=\"color:#4b5563;font-size:12px\">\u7f16\u5e74\u53f2</span><a href=\"/\" style=\"margin-left:auto\">\u641c\u7d22 \u2197</a></header>\n<div class=\"wrap\"><nav id=\"nav\"></nav><main id=\"main\"><p class=\"empty\">\u52a0\u8f7d\u4e2d\u2026</p></main></div>\n<script>\nconst ORDER={core:0,year:1,quarter:2,month:3};\nconst LABEL={core:'\u603b\u7eb2',year:'\u5e74',quarter:'\u5b63',month:'\u6708'};\nlet items=[];\nfunction esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}\nasync function boot(){\n  const r=await fetch('/chron'); const d=await r.json(); items=d.items||[];\n  const nav=document.getElementById('nav'); let last='';\n  items.sort((a,b)=>ORDER[a.granularity]-ORDER[b.granularity]||(a.start_ts||'').localeCompare(b.start_ts||''));\n  for(const it of items){\n    if(it.granularity!==last){last=it.granularity;const g=document.createElement('div');g.className='grp';g.textContent=LABEL[last]||last;nav.appendChild(g);}\n    const a=document.createElement('a');a.href='#/'+it.period_id;a.textContent=it.period_id+'  '+it.title;a.dataset.pid=it.period_id;\n    a.onclick=e=>{e.preventDefault();show(it.period_id)};nav.appendChild(a);\n  }\n  show(items.length?items[0].period_id:null);\n}\nasync function show(pid){\n  if(!pid)return;\n  document.querySelectorAll('nav a').forEach(a=>a.classList.toggle('on',a.dataset.pid===pid));\n  const r=await fetch('/chron/'+encodeURIComponent(pid)); const d=await r.json();\n  const turns=(d.turns||[]).map(t=>`<div class=\"item\"><b>${esc(t.when||'')}</b>\u3000${esc(t.what||'')}</div>`).join('');\n  const threads=(d.threads||[]).map(t=>{\n    const st=t.state||'';\n    const cls=st==='\u5b8c\u6210'?'done':(st==='\u6401\u7f6e'?'hold':'');\n    return `<div class=\"item\"><span class=\"tag ${cls}\">${esc(st)}</span><b>${esc(t.name||'')}</b>\u3000<span style=\"color:#6b7280\">${esc(t.last||'')}</span></div>`;\n  }).join('');\n  const ents=(d.entities||[]).map(e=>`<span class=\"tag\">${esc(e)}</span>`).join('');\n  document.getElementById('main').innerHTML=`\n    <h1>${esc(d.title||d.period_id)}</h1>\n    <div class=\"meta\">${esc(d.period_id)} \u00b7 ${d.msg_count||0} \u6761\u6d88\u606f \u00b7 ${esc((d.start_ts||'').slice(0,10))} \u2192 ${esc((d.end_ts||'').slice(0,10))}</div>\n    <div class=\"sum\">${esc(d.summary||'')}</div>\n    ${turns?'<div class=\"sec\"><h3>\u8f6c\u6298</h3>'+turns+'</div>':''}\n    ${threads?'<div class=\"sec\"><h3>\u7ebf\u7d22</h3>'+threads+'</div>':''}\n    ${ents?'<div class=\"sec\"><h3>\u6d89\u53ca\u7684\u5b9e\u4f53</h3>'+ents+'</div>':''}\n  `;\n  window.scrollTo(0,0);\n}\nboot();\n</script></body></html>\n"

CRADLE_DB = os.environ.get("CRADLE_DB", "/opt/cradle/data/cradle.db")


def _chron_dict(row):
    d = dict(row)
    for k in ("threads", "entities", "turns"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                d[k] = []
        else:
            d[k] = []
    return d


def _con():
    return store.connect(DB)

def _injections(qs):
    """每轮注入的记录（shim 写入 injections 表）。"""
    con = _con()
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS injections(
            id INTEGER PRIMARY KEY, at TEXT, kind TEXT, query TEXT,
            n_cards INTEGER, card_ids TEXT, chars INTEGER, ms INTEGER, has_self INTEGER)""")
        con.commit()
    except Exception:
        pass
    try:
        lim = int((qs.get("limit") or ["60"])[0])
    except Exception:
        lim = 60
    kind = (qs.get("kind") or [""])[0]
    if kind in ("gw", "recall"):
        rows = con.execute("SELECT * FROM injections WHERE kind=? ORDER BY id DESC LIMIT ?",
                           (kind, lim)).fetchall()
    else:
        rows = con.execute("SELECT * FROM injections ORDER BY id DESC LIMIT ?", (lim,)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["card_ids"] = json.loads(d.get("card_ids") or "[]")
        except Exception:
            d["card_ids"] = []
        items.append(d)
    # 两种 kind 语义不同，必须分开给，否则「注入 1000 字」与「检索返回 1.3 万字」混成一个数：
    #   gw     = 真正拼进 prompt 的注入块（chars 就是注入字符数）
    #   recall = 检索 API（/v1/recall）返回给客户端的记忆正文量，不是注入
    def _agg(where="1=1"):
        r = con.execute("SELECT COUNT(*) n, COALESCE(SUM(chars),0) c FROM injections WHERE " + where).fetchone()
        return int(r["n"]), int(r["c"])

    n_all, c_all = _agg()
    n_gw, c_gw = _agg("kind='gw'")
    n_rc, c_rc = _agg("kind='recall'")
    td_gw = con.execute("SELECT COUNT(*) n FROM injections WHERE kind='gw' AND at>=date('now')").fetchone()["n"]
    td_rc = con.execute("SELECT COUNT(*) n FROM injections WHERE kind='recall' AND at>=date('now')").fetchone()["n"]
    return {"items": items, "kind": kind or "all",
            "total": n_all, "sum_chars": c_all,
            "avg_chars": int(c_all / n_all) if n_all else 0,
            "gw": {"n": n_gw, "chars": c_gw, "avg": int(c_gw / n_gw) if n_gw else 0, "today": td_gw},
            "recall_api": {"n": n_rc, "chars": c_rc, "avg": int(c_rc / n_rc) if n_rc else 0, "today": td_rc},
            "today": td_gw}



def _log_recall(con, q, card_ids):
    """记一次召回：谁被什么问题捞出来过。失败静默，不能拖慢检索。"""
    try:
        import hashlib as _h
        import datetime as _dt
        qh = _h.md5((" ".join((q or "").lower().split())).encode("utf-8")).hexdigest()[:16]
        at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _lock:
            con.executemany(
                "INSERT INTO card_recall(card_id, query_hash, at) VALUES(?,?,?)",
                [(int(cid), qh, at) for cid in card_ids if cid],
            )
            con.commit()
    except Exception:
        pass



def _payload(rows, max_chars=500):
    return [
        {
            "id": r["id"],
            "score": r["score"],
            "title": r["title"],
            "content": (r["body"] or "")[:max_chars],
            "kind": r["kind"],
            "topic": r["topic"],
        }
        for r in rows
    ]


class Handler(BaseHTTPRequestHandler):
    server_version = "memvault/0.1"

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if not API_KEY:
            return True
        got = self.headers.get("X-API-Key") or self.headers.get("Authorization", "").replace("Bearer ", "")
        return got == API_KEY

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send_html(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(p.query)

        if p.path in ("/dream", "/dreams/ui", "/tree2d", "/mind", "/tline", "/tl",
                       "/snap3d", "/star3d", "/star", "/map2d", "/settings.html"):
            # 这些是旧查看页，已下线；面板统一在 :4180/admin
            host = (self.headers.get("host") or "127.0.0.1").split(":")[0]
            self.send_response(302)
            self.send_header("Location", "http://%s:4180/admin" % host)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p.path in ("/", "/ui"):
            # 只保留一套界面（4180/admin）；旧界面统一跳过去
            host = (self.headers.get("host") or "127.0.0.1").split(":")[0]
            self.send_response(302)
            self.send_header("Location", "http://%s:4180/admin" % host)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return


        if p.path == "/snap3d":
            qst = urllib.parse.parse_qs(p.query or "")
            def _f(k, dflt):
                try: return float((qst.get(k) or [dflt])[0])
                except ValueError: return dflt
            def _i(k, dflt):
                try: return int((qst.get(k) or [dflt])[0])
                except ValueError: return dflt
            try:
                from . import render3d
                raw, n = render3d.render(
                    yaw=_f("yaw", 0.0), pitch=_f("pitch", 0.25),
                    w=max(200, min(1600, _i("w", 900))),
                    h=max(200, min(2000, _i("h", 1200))),
                    zoom=_f("zoom", 1.0),
                    limit=max(200, min(12000, _i("limit", 4000))))
            except Exception as e:
                return self._send(501, {"error": "render disabled", "detail": str(e)[:160],
                                        "hint": "缺 Pillow，装 python3-pil 或改用 /star3d 前端渲染"})
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)
            return

        if p.path in ("/star3d", "/3d"):
            try:
                html = open("/opt/memvault/memvault/snap3d.html", encoding="utf-8").read()
            except Exception:
                html = open("/opt/memvault/memvault/star3d.html", encoding="utf-8").read()
            return self._send_html(200, html)


            try:
                html = open("/opt/memvault/memvault/star3d.html", encoding="utf-8").read()
            except Exception as e:
                return self._send(500, {"error": str(e)})
            return self._send_html(200, html)

        if p.path == "/graph3d":
            con = _con()
            try:
                qst = urllib.parse.parse_qs(p.query or "")
                try:
                    lim = int((qst.get("limit") or ["3000"])[0])
                except ValueError:
                    lim = 3000
                # 12k 个 mesh 超出手机 GPU 承受范围，默认只画 3000
                rows = con.execute(
                    """SELECT m.card_id, m.x, m.y, m.z, m.month,
                              c.title, c.kind, LENGTH(c.body)
                       FROM map3d m JOIN cards c ON c.id=m.card_id
                       WHERE c.status='active'
                       ORDER BY EXISTS(
                           SELECT 1 FROM edges e WHERE e.src=m.card_id OR e.dst=m.card_id
                       ) DESC, m.card_id DESC
                       LIMIT ?""", (lim,)).fetchall()
                edges = con.execute(
                    """SELECT src, dst, kind, weight FROM edges
                       WHERE edge_type IN ('fact','structural_near')
                       LIMIT 24000""").fetchall()
            finally:
                con.close()
            nodes = []
            for cid, x, y, z, mo, title, kind, blen in rows:
                nodes.append({
                    "id": cid, "cid": cid,
                    "x": x * 260, "y": y * 260, "z": z * 260,
                    "fx": x * 260, "fy": y * 260, "fz": z * 260,
                    "title": title or "", "group": kind or "fact",
                    "month": mo or "",
                    "size": max(1.0, min(3.0, (blen or 100) / 400.0)),
                })
            ids = {n["id"] for n in nodes}
            links = [{"source": a, "target": b, "color": "rgba(90,169,255,.20)", "width": 0.4}
                     for a, b, k, w in edges if a in ids and b in ids]
            return self._send(200, {"nodes": nodes, "edges": links})

        if p.path in ("/dream", "/dreams/ui"):
            fp = os.path.join(os.path.dirname(__file__), "dream.html")
            if os.path.exists(fp):
                return self._send_html(200, open(fp, encoding="utf-8").read())
            return self._send(404, {"error": "dream.html missing"})

        if p.path == "/dreams":
            qs = urllib.parse.parse_qs(p.query)
            st = (qs.get("status") or [None])[0]
            kd = (qs.get("kind") or [None])[0]
            try:
                con = _con()
                return self._send(200, {"items": _dream.listing(con, st, kind=kd)})
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:200]})

        if p.path == "/procedures":
            qs = urllib.parse.parse_qs(p.query)
            st = qs.get("status", [None])[0]
            cat = qs.get("category", [None])[0]
            con = _con()
            return self._send(200, {"items": _procedure.listing(con, st, cat)})

        if p.path in ("/focus", "/tasks"):
            qs_ = urllib.parse.parse_qs(p.query)
            st_ = (qs_.get("status") or [None])[0]
            con = _con()
            return self._send(200, {"items": _focus.listing(con, status=st_)})

        if p.path == "/profile/note":
            con = _con()
            try:
                r = con.execute("SELECT v, updated_at FROM settings WHERE k='profile.note'").fetchone()
                if r and r[0]:
                    return self._send(200, {"note": json.loads(r[0]), "built_at": r[1]})
            except Exception:
                pass
            return self._send(200, {"note": None})

        if p.path == "/profile":
            con = _con()
            return self._send(200, _profile.collect(con))

        if p.path == "/injections":
            return self._send(200, _injections(urllib.parse.parse_qs(p.query)))

        if p.path == "/summaries":
            try:
                con = _con()
                return self._send(200, {"items": _summary.listing(con)})
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:200]})

        if p.path == "/models":
            try:
                con = _con()
                ids = _dream.list_models(_dream.get_settings(con))
                return self._send(200, {"ok": True, "items": ids})
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:300]})

        if p.path in ("/settings", "/api/settings"):
            try:
                con = _con()
                return self._send(200, {"items": _dream.get_settings(con), "defaults": _dream.DEFAULTS})
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:200]})

        if p.path in ("/tree2d", "/mind"):
            fp = os.path.join(os.path.dirname(__file__), "tree2d.html")
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    self._send_html(200, f.read())
            except Exception as e:
                self._send(500, {"error": str(e)})
            return
        if p.path in ("/tline", "/tl"):
            fp = os.path.join(os.path.dirname(__file__), "tline.html")
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    self._send_html(200, f.read())
            except Exception as e:
                self._send(500, {"error": str(e)})
            return
        if p.path in ("/self", "/selfnote"):
            con = _con()
            try:
                _sn.ensure(con)
                rows = list(con.execute(
                    "SELECT id,at,body,trigger,weight,source FROM self_notes "
                    "ORDER BY weight DESC, at DESC LIMIT 60"))
            except Exception as e:
                con.close()
                return self._send(500, {"error": str(e)})
            con.close()
            return self._send(200, {"count": len(rows), "items": [
                {"id": r[0], "at": r[1], "body": r[2], "trigger": r[3],
                 "weight": r[4], "source": r[5]} for r in rows]})

        if p.path == "/self/add" and self.command == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                d = json.loads(self.rfile.read(n) or b"{}")
                body = (d.get("body") or "").strip()
                if not body:
                    return self._send(400, {"error": "body required"})
                con = _con()
                _sn.ensure(con)
                _sn.add(con, body, d.get("trigger", ""),
                        float(d.get("weight", 1.0)), source="manual")
                con.close()
                return self._send(200, {"ok": True})
            except Exception as e:
                return self._send(500, {"error": str(e)})

        if p.path in ("/admin", "/admin/", "/a"):
            # 只保留一套界面（4180/admin）；旧界面统一跳过去
            host = (self.headers.get("host") or "127.0.0.1").split(":")[0]
            self.send_response(302)
            self.send_header("Location", "http://%s:4180/admin" % host)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p.path in ("/timeline", "/chron/ui"):
            return self._send_html(200, TL_HTML)

        if p.path == "/export":
            # 原始文本导出（人可读、可回灌）
            con = _con()
            try:
                tmp = "/tmp/_mem_export_%d.txt" % int(time.time() * 1000)
                from . import textio as _tio
                try:
                    lim = int((qs.get("convs") or qs.get("limit") or ["3"])[0])
                except ValueError:
                    lim = 3
                r = _tio.export_text(DB, tmp, conv_limit=(lim or 3))
                size = r["bytes"]
                fname = "memvault-%s.txt" % time.strftime("%Y%m%d-%H%M%S")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % fname)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                with open(tmp, "rb") as f:
                    shutil.copyfileobj(f, self.wfile, 256 * 1024)
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                return
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._send(500, {"error": str(e), "tb": traceback.format_exc()[-800:]})
            finally:
                con.close()


        # ==== NEWUI API (managed) ====
        if p.path == "/api/gwenv":
            return self._send(200, _gwenv_state())

        if p.path == "/api/overview":
            con = _con()
            try:
                since = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 86400))
                one = lambda sql, a=(): con.execute(sql, a).fetchone()[0]
                cards = one("select count(*) from cards")
                active = one("select count(*) from cards where status='active'")
                proposal = one("select count(*) from cards where status='proposal'")
                archive = one("select count(*) from cards where status='archive'")
                spans = one("select count(*) from spans")
                vectors = one("select count(*) from vectors")
                supersedes = one("select count(*) from cards where supersedes is not null")
                heat = con.execute("select avg(heat) from cards where status='active'").fetchone()[0] or 0.0
                never = one("select count(*) from cards where status='active' and id not in (select card_id from card_recall)")
                # 口径：只有 kind='gw' 是真正拼进 prompt 的注入块；
                # kind='recall' 是检索 API 返回给客户端的记忆正文量，不是注入，单独统计。
                inj_n, inj_c = con.execute(
                    "select count(*), coalesce(avg(chars),0) from injections where at>=? and kind='gw'",
                    (since,)).fetchone()
                rec_api_n, rec_api_c = con.execute(
                    "select count(*), coalesce(avg(chars),0) from injections where at>=? and kind='recall'",
                    (since,)).fetchone()
                rec_n = one("select count(*) from card_recall where at>=?", (since,))
                new_cards = one("select count(*) from cards where created_at>=?", (since,))
                new_spans = one("select count(*) from spans where created_at>=?", (since,))
                by_kind = con.execute("select kind, count(*) c from cards where status='active' group by 1 order by c desc").fetchall()
                by_owner = con.execute("select owner, count(*) c from cards group by 1 order by c desc").fetchall()
                seen = one("select count(distinct span_id) from card_evidence")
                pending_learn = max(0, spans - (one("select count(*) from card_evidence") and 0)) if False else (spans - (one("select count(distinct span_id) from card_evidence")))
            finally:
                con.close()
            try:
                db_mb = round(os.path.getsize(DB) / 1048576.0, 1)
            except Exception:
                db_mb = 0
            rep_dir = "/opt/memvault/reports"
            reports, report = [], ""
            try:
                reports = sorted([f[:-3] for f in os.listdir(rep_dir) if f.endswith(".md") and f != "latest.md"])[-20:]
                reports.reverse()
                report = open(os.path.join(rep_dir, "latest.md"), encoding="utf-8").read()
            except Exception:
                pass
            backups, last_backup = [], ""
            try:
                bdir = "/opt/memvault/data/backups"
                backups = sorted([f for f in os.listdir(bdir) if f.endswith(".gz")])[-7:]
                last_backup = backups[-1][:17] if backups else ""
            except Exception:
                pass
            balance = None
            try:
                import json as _j, urllib.request as _u
                key = os.environ.get("MEMVAULT_LLM_KEY", "")
                if key:
                    rq = _u.Request("https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + key})
                    with _u.urlopen(rq, timeout=6) as rr:
                        jj = _j.loads(rr.read())
                        balance = float((jj.get("balance_infos") or [{}])[0].get("total_balance") or 0)
            except Exception:
                balance = None
            return self._send(200, {
                "cards": cards, "active": active, "proposal": proposal, "archive": archive,
                "spans": spans, "vectors": vectors, "supersedes": supersedes,
                "heat": round(float(heat), 4), "never": never, "covered_spans": seen,
                "pending_learn": pending_learn,
                "today": {"inject": inj_n, "inject_chars": round(float(inj_c), 1),
                          "recall_api": rec_api_n, "recall_api_chars": round(float(rec_api_c), 1),
                          "recall": rec_n,
                          "new_cards": new_cards, "new_spans": new_spans},
                "by_kind": [[k or "", v] for k, v in by_kind],
                "by_owner": [[k or "", v] for k, v in by_owner],
                "db_mb": db_mb, "reports": reports, "report": report[:4000],
                "backups": backups, "last_backup": last_backup, "balance": balance,
            })

        if p.path == "/api/settings":
            con = _con()
            try:
                return self._send(200, {"ok": True, "items": _dream.get_settings(con), "defaults": _dream.DEFAULTS})
            finally:
                con.close()

        if p.path == "/api/pending":
            con = _con()
            try:
                rows = con.execute(
                    "select id,title,body,kind,owner,created_at,scope,project_id,importance from cards "
                    "where status='proposal' order by id desc limit 200").fetchall()
            finally:
                con.close()
            return self._send(200, {"items": [
                {"id": r[0], "title": r[1], "body": r[2], "kind": r[3], "owner": r[4],
                 "created_at": r[5], "scope": r[6], "project_id": r[7], "importance": r[8],
                 "status": "proposal"} for r in rows], "count": len(rows)})

        if p.path == "/api/cards":
            def _i2(k, d):
                try: return int((qs.get(k) or [d])[0])
                except Exception: return d
            off = max(0, _i2("offset", 0))
            lim = max(1, min(200, _i2("limit", 30)))
            st = (qs.get("status") or [""])[0]
            kd = (qs.get("kind") or [""])[0]
            ow = (qs.get("owner") or [""])[0]
            sc = (qs.get("scope") or [""])[0]
            kw = (qs.get("q") or [""])[0].strip()
            sort = (qs.get("sort") or ["recent"])[0]
            w, a = [], []
            if st: w.append("status=?"); a.append(st)
            if kd: w.append("kind=?"); a.append(kd)
            if ow: w.append("owner=?"); a.append(ow)
            if sc: w.append("scope=?"); a.append(sc)
            if kw: w.append("(title like ? or body like ?)"); a += ["%" + kw + "%", "%" + kw + "%"]
            where = ("where " + " and ".join(w)) if w else ""
            order = {"recent": "id desc", "hot": "heat desc, id desc", "cold": "heat asc, id asc"}.get(sort, "id desc")
            con = _con()
            try:
                total = con.execute("select count(*) from cards " + where, a).fetchone()[0]
                rows = con.execute(
                    "select id,title,body,kind,owner,status,scope,project_id,heat,importance,pinned,"
                    "supersedes,created_at from cards " + where + " order by " + order + " limit ? offset ?",
                    a + [lim, off]).fetchall()
                sup_by = {}
                ids = [r[0] for r in rows]
                if ids:
                    q = "select supersedes, id from cards where supersedes in (%s)" % ",".join("?" * len(ids))
                    for old, new in con.execute(q, ids):
                        sup_by[old] = new
            finally:
                con.close()
            items = []
            for r in rows:
                items.append({"id": r[0], "title": r[1], "body": r[2], "content": r[2], "kind": r[3],
                              "owner": r[4], "status": r[5], "scope": r[6], "project_id": r[7],
                              "heat": r[8], "importance": r[9], "pinned": r[10],
                              "supersedes": r[11], "created_at": r[12],
                              "superseded_by": sup_by.get(r[0])})
            return self._send(200, {"total": total, "offset": off, "limit": lim, "items": items})

        if p.path.startswith("/api/card/") and p.path.count("/") == 3:
            try:
                cid = int(p.path.rsplit("/", 1)[1])
            except Exception:
                return self._send(400, {"error": "bad id"})
            con = _con()
            try:
                row = con.execute(
                    "select id,title,body,kind,topic,owner,status,scope,project_id,heat,importance,pinned,"
                    "supersedes,created_at,updated_at,claim_type,category,occurred_at,softened_at,source_path "
                    "from cards where id=?", (cid,)).fetchone()
                if not row:
                    return self._send(404, {"error": "not found"})
                keys = ["id", "title", "body", "kind", "topic", "owner", "status", "scope", "project_id",
                        "heat", "importance", "pinned", "supersedes", "created_at", "updated_at",
                        "claim_type", "category", "occurred_at", "softened_at", "source_path"]
                card = dict(zip(keys, row))
                ev = con.execute(
                    "select s.span_id, e.role, e.quote, s.ts, s.conv_id from card_evidence e "
                    "left join spans s on s.span_id=e.span_id where e.card_id=? limit 40", (cid,)).fetchall()
                rl = con.execute(
                    "select at, query_hash from card_recall where card_id=? order by at desc limit 20", (cid,)).fetchall()
                nxt = con.execute("select id from cards where supersedes=?", (cid,)).fetchone()
            finally:
                con.close()
            return self._send(200, {
                "card": card,
                "evidence": [{"span_id": r[0], "role": r[1], "quote": r[2], "ts": r[3], "conv_id": r[4]} for r in ev],
                "recalls": [{"at": r[0], "query_hash": r[1]} for r in rl],
                "superseded_by": nxt[0] if nxt else None,
            })

        if p.path == "/api/usage":
            # 按任务统计 LLM 花费：读 /opt/memvault/data/llm_usage.jsonl（_meter 写的）
            try:
                import glob as _g
                cut = time.time() - 86400
                agg = {}
                for fp in _g.glob("/opt/memvault/data/llm_usage*.jsonl"):
                    try:
                        with open(fp, encoding="utf-8") as fh:
                            for line in fh:
                                try:
                                    d = json.loads(line)
                                except Exception:
                                    continue
                                ts = d.get("ts") or ""
                                try:
                                    t = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")) + time.timezone - 2 * time.timezone
                                except Exception:
                                    t = 0
                                if t and t < cut:
                                    continue
                                k = "%s|%s" % (d.get("task") or "unknown", d.get("key") or "-")
                                g = agg.setdefault(k, {"calls": 0, "in": 0, "out": 0,
                                                       "cached": 0, "cny": 0.0})
                                g["calls"] += 1
                                g["in"] += int(d.get("in") or 0)
                                g["out"] += int(d.get("out") or 0)
                                g["cached"] += int(d.get("cached") or 0)
                                g["cny"] += float(d.get("cny") or 0)
                    except FileNotFoundError:
                        pass
                items = [dict(task=k.split("|")[0], key=k.split("|")[1], **v) for k, v in sorted(
                    agg.items(), key=lambda kv: -kv[1]["cny"])]
                tot = {"calls": sum(i["calls"] for i in items),
                       "cny": round(sum(i["cny"] for i in items), 4)}
                return self._send(200, {"window": "24h", "total": tot, "items": items})
            except Exception as e:
                return self._send(200, {"window": "24h", "total": {"calls": 0, "cny": 0},
                                        "items": [], "error": str(e)[:120]})

        if p.path == "/api/supersedes":
            con = _con()
            try:
                rows = con.execute(
                    "select n.id, n.title, n.kind, n.created_at, c.id, c.title, c.status"
                    " from cards n join cards c on c.id = n.supersedes"
                    " where n.supersedes is not null and n.supersedes <> 0"
                    " order by n.id desc limit 200").fetchall()
                return self._send(200, {"count": len(rows), "items": [
                    {"new_id": r[0], "new_title": r[1], "kind": r[2], "new_at": r[3],
                     "old_id": r[4], "old_title": r[5], "old_status": r[6]}
                    for r in rows]})
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
            finally:
                con.close()

        if p.path == "/api/backup":
            import subprocess as _sp
            try:
                r = _sp.run(["/usr/local/bin/mvbackup.sh"], capture_output=True, text=True, timeout=900)
                return self._send(200, {"ok": r.returncode == 0, "code": r.returncode,
                                        "out": (r.stdout or "")[-2000:], "err": (r.stderr or "")[-800:]})
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)[:200]})

        if p.path == "/api/ops":
            import subprocess as _sp

            def _sh(cmd):
                try:
                    return _sp.run(cmd, shell=True, capture_output=True, text=True, timeout=15).stdout.strip()
                except Exception as e:
                    return "ERR " + str(e)[:120]

            svcs = []
            for s in ["memvault", "memvault-shim", "memvault-gw", "memvault-mcp",
                      "memvault-backup.timer", "memvault-health.timer",
                      "memvault-supersede.timer", "memvault-learn.timer"]:
                svcs.append({"name": s, "active": _sh("systemctl is-active %s" % s)})
            timers = _sh("systemctl list-timers --all --no-pager --plain 'memvault-*' 'memvault*' 2>/dev/null | head -20")
            bdir = "/opt/memvault/data/backups"
            baks = []
            try:
                for f in sorted(os.listdir(bdir))[-8:]:
                    p = os.path.join(bdir, f)
                    baks.append({"name": f, "mb": round(os.path.getsize(p) / 1048576.0, 1)})
            except Exception:
                pass
            return self._send(200, {"services": svcs, "timers": timers, "backups": baks,
                                    "uptime": _sh("uptime -p")})

        if p.path == "/api/registry":
            try:
                return self._send(200, json.load(open("/opt/memvault/registry/index.json", encoding="utf-8")))
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})

        if p.path == "/api/export/cards":
            try:
                lim = int((qs.get("limit") or ["100"])[0])
            except Exception:
                lim = 100
            lim = max(1, min(5000, lim))
            con = _con()
            try:
                rows = con.execute("select id,title,body,kind,owner,created_at from cards order by id limit ?", (lim,)).fetchall()
            finally:
                con.close()
            body = "\n\n".join("#%d [%s/%s] %s\n%s" % (r[0], r[3] or "-", r[4] or "-", r[1] or "", r[2]) for r in rows)
            raw = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Content-Disposition", 'attachment; filename="cards-%d.txt"' % lim)
            self.end_headers()
            self.wfile.write(raw)
            return

        if p.path.startswith("/report/"):
            name = p.path[len("/report/"):]
            if "/" in name or ".." in name:
                return self._send(400, {"error": "bad name"})
            if not name.endswith(".md"):
                name += ".md"          # 面板里写的是 /report/latest，补上后缀
            if not name.endswith(".md"):
                return self._send(400, {"error": "bad name"})
            fp = os.path.join("/opt/memvault/reports", name)
            if not os.path.exists(fp):
                return self._send(404, {"error": "not found"})
            raw = open(fp, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        if p.path == "/health":
            con = _con()
            try:
                return self._send(200, {"ok": True, **store.stats(con)})
            finally:
                con.close()

        # /threads → 线索表（可按状态筛，支持 stale_days）
        if p.path == "/threads":
            qst = urllib.parse.parse_qs(p.query or "")
            state = (qst.get("state") or [""])[0]
            lim = int((qst.get("limit") or ["80"])[0])
            con = _con()
            try:
                sql = ("SELECT thread_id,name,kind,state,note,first_period,last_period,"
                       "seen,valid_from,last_touched FROM threads")
                args = []
                if state:
                    sql += " WHERE state=?"
                    args.append(state)
                sql += (" ORDER BY CASE state WHEN 'blocked' THEN 0 WHEN 'active' THEN 1 "
                        "WHEN 'shifted' THEN 2 ELSE 3 END, last_period DESC LIMIT ?")
                args.append(lim)
                rows = con.execute(sql, args).fetchall()
            except Exception as e:
                self._send(500, {"error": str(e)})
                return
            finally:
                con.close()
            self._send(200, [{
                "id": r[0], "name": r[1], "kind": r[2], "state": r[3],
                "note": r[4], "first": r[5], "last": r[6], "seen": r[7],
                "valid_from": r[8],
            } for r in rows])
            return

        # /tree → 思维导图数据（root→kind→topic）
        if p.path == "/tree":
            qst = urllib.parse.parse_qs(p.query or "")
            per = int((qst.get("per") or qst.get("per_kind") or ["40"])[0])
            by = (qst.get("by") or ["kind"])[0]
            try:
                self._send(200, _tree.load(DB, by=by, per=per))
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # /tree/cards → 某 topic 下的卡片
        if p.path == "/tree/cards":
            qst = urllib.parse.parse_qs(p.query or "")
            kind = (qst.get("kind") or [""])[0]
            group = (qst.get("group") or [""])[0] or kind
            by = (qst.get("by") or ["kind"])[0]
            topic = (qst.get("topic") or [""])[0]
            lim = int((qst.get("limit") or ["60"])[0])
            try:
                self._send(200, _tree.cards_of(DB, group, topic, by=by, limit=lim))
            except Exception as e:
                self._send(500, {"error": str(e)})
            return

        # /graph → tideglass 3D 星图数据
        if p.path == "/graph":
            qs3 = urllib.parse.parse_qs(p.query or "")
            lim = int((qs3.get("limit") or ["2500"])[0])
            con = _con()

            if (qs3.get("seeded") or ["0"])[0] in ("1", "true", "yes"):
                # 按 edges 表的连接数取前 lim 个节点，再取它们之间的边——
                # 保证返回的图「真的有边」，而不是从一堆孤立卡里碰运气。
                deg = {}
                alledges = []
                try:
                    for a, b, kind, w, et, lab in con.execute(
                            "SELECT src, dst, kind, weight, edge_type, label FROM edges"):
                        alledges.append((a, b, et, lab or kind))
                        deg[a] = deg.get(a, 0) + 1
                        deg[b] = deg.get(b, 0) + 1
                except Exception:
                    alledges = []
                top = sorted(deg.items(), key=lambda kv: -kv[1])[:lim]
                topids = [int(k) for k, _ in top]
                nodes = []
                if topids:
                    q = ("SELECT id,title,body,kind,owner,created_at,source_path,occurred_at "
                         "FROM cards WHERE id IN (" + ",".join("?" * len(topids)) + ")")
                    for r in con.execute(q, topids):
                        nodes.append({
                            "id": str(r[0]), "label": (r[1] or "")[:40],
                            "group": r[3] or "definition", "summary": (r[2] or "")[:220],
                            "source": (r[6] or "").split("/")[-1][:60], "confidence": None,
                            "title": (r[1] or "")[:80], "size": 6,
                            "created_at": (r[7] or r[5] or "")[:10],
                        })
                idset = {int(n["id"]) for n in nodes}
                edges = []
                for a, b, et, lab in alledges:
                    if a in idset and b in idset:
                        edges.append({"from": str(a), "to": str(b), "label": lab,
                                      "width": 1.0 if et == "fact" else (0.45 if et == "structural" else 0.35),
                                      "edge_type": et,
                                      "opacity": 0.85 if et == "fact" else (0.30 if et == "structural" else 0.18)})
                d2 = {}
                for e in edges:
                    d2[e["from"]] = d2.get(e["from"], 0) + 1
                    d2[e["to"]] = d2.get(e["to"], 0) + 1
                for n in nodes:
                    n["size"] = 6 + min(d2.get(n["id"], 0), 20) * 2
                con.close()
                return self._send(200, {"nodes": nodes, "edges": edges,
                                        "count": len(nodes), "edge_count": len(edges),
                                        "seeded": True})

            span_ts = {}
            try:
                for cid2, ts2 in con.execute(
                    "SELECT ce.card_id, MIN(sp.ts) FROM card_evidence ce "
                    "JOIN spans sp ON sp.span_id = ce.span_id GROUP BY ce.card_id"):
                    if ts2:
                        span_ts[cid2] = str(ts2)[:10]
            except Exception:
                span_ts = {}
            # 节点：新卡（有 evidence）优先全要，旧卡按 kind 均匀抽样补足
            newed = {r[0] for r in con.execute(
                "SELECT DISTINCT card_id FROM card_evidence")}
            rows = []
            n_new = max(50, int(lim * 0.55))
            for r in con.execute(
                    "SELECT id,title,body,kind,owner,created_at,source_path,occurred_at "
                    "FROM cards WHERE status='active' AND id IN "
                    "(SELECT DISTINCT card_id FROM card_evidence) "
                    "ORDER BY created_at DESC LIMIT ?", (n_new,)):
                rows.append(r)
            got = {r[0] for r in rows}
            rest = max(0, lim - len(rows))
            per = {}
            oldrows = con.execute(
                "SELECT id,title,body,kind,owner,created_at,source_path,occurred_at "
                "FROM cards WHERE status='active' ORDER BY id DESC").fetchall()
            quota = max(1, rest // 6)
            for r in oldrows:
                if r[0] in got:
                    continue
                k = r[3] or "?"
                if per.get(k, 0) >= quota:
                    continue
                per[k] = per.get(k, 0) + 1
                rows.append(r)
                got.add(r[0])
                if len(rows) >= lim:
                    break
            nodes = []
            idset = set()
            for r in rows:
                cid, title, body, kind, owner, created, spath, occ = r
                idset.add(cid)
                nodes.append({
                    "id": str(cid),
                    "label": (title or "")[:40],
                    "group": kind or "definition",
                    "summary": (body or "")[:220],
                    "source": (spath or "").split("/")[-1][:60],
                    "confidence": None,
                    "title": (title or "")[:80],
                    "size": 6,
                    "created_at": (span_ts.get(cid) or occ or created or "")[:10],
                })
            # 边：从预计算的 edges 表读（fact / inferred / structural）
            edges = []
            try:
                for a, b, kind, w, et, lab in con.execute(
                        "SELECT src, dst, kind, weight, edge_type, label FROM edges"):
                    if a not in idset or b not in idset:
                        continue
                    edges.append({
                        "from": str(a), "to": str(b),
                        "label": lab or kind,
                        "width": 1.0 if et == "fact" else (0.45 if et == "structural" else 0.35),
                        "edge_type": et,
                        "opacity": 0.85 if et == "fact" else (0.30 if et == "structural" else 0.18),
                    })
            except Exception as e:
                edges = []
            deg = {}
            for e in edges:
                if e.get("edge_type") != "fact":
                    continue
                deg[e["from"]] = deg.get(e["from"], 0) + 1
                deg[e["to"]] = deg.get(e["to"], 0) + 1
            for n in nodes:
                n["size"] = 6 + min(deg.get(n["id"], 0), 20) * 2
            return self._send(200, {"nodes": nodes, "edges": edges,
                                    "count": len(nodes), "edge_count": len(edges)})

        # /chronicle?level=core_abs|core&period=xxx → 压缩摘要（给注入用）
        if p.path == "/chronicle":
            qs2 = urllib.parse.parse_qs(p.query or "")
            level = (qs2.get("level") or ["core_abs"])[0]
            period = (qs2.get("period") or [""])[0]
            try:
                ccon = sqlite3.connect(DB, timeout=5)
                cols = ["period_id","granularity","start_ts","end_ts","msg_count",
                        "title","summary","threads","entities","turns","carries_over","built_at"]
                if period:
                    row = ccon.execute("SELECT * FROM chronicles WHERE period_id=?", (period,)).fetchone()
                else:
                    row = ccon.execute("SELECT * FROM chronicles WHERE period_id='core'").fetchone()
                ccon.close()
            except Exception as e:
                return self._send(500, {"ok": False, "error": str(e)})
            if not row:
                return self._send(404, {"ok": False, "error": "no chronicle"})
            d = _chron_dict(dict(zip(cols, row)))
            body = d.get("summary") or ""
            if level == "core_abs":
                parts = re.split(r"(?<=[。；])", body)
                ab = ""
                for seg in parts:
                    if len(ab) + len(seg) > 120: break
                    ab += seg
                body = ab or body[:120]
            return self._send(200, {"ok": True, "level": level, "period_id": d["period_id"],
                                    "title": d["title"], "summary": body, "chars": len(body)})

        # /chron                → 编年史目录（月/季/年/core）
        # /chron/{period_id}    → 单条编年史
        # /core                 → 当前状态总纲
        if p.path in ("/chron", "/core"):
            con = _con()
            try:
                if p.path == "/core":
                    row = con.execute(
                        "SELECT * FROM chronicles WHERE period_id='core'").fetchone()
                    if not row:
                        return self._send(404, {"error": "core not built"})
                    return self._send(200, _chron_dict(row))
                rows = con.execute(
                    "SELECT period_id,granularity,start_ts,end_ts,msg_count,title "
                    "FROM chronicles ORDER BY "
                    "CASE granularity WHEN 'core' THEN 0 WHEN 'year' THEN 1 "
                    "WHEN 'quarter' THEN 2 ELSE 3 END, start_ts").fetchall()
                return self._send(200, {"items": [dict(r) for r in rows],
                                        "count": len(rows)})
            finally:
                con.close()

        m = re.match(r"^/chron/([A-Za-z0-9\-]+)$", p.path)
        if m:
            con = _con()
            try:
                row = con.execute(
                    "SELECT * FROM chronicles WHERE period_id=?", (m.group(1),)).fetchone()
                if not row:
                    return self._send(404, {"error": "period not found"})
                return self._send(200, _chron_dict(row))
            finally:
                con.close()

        # /card/{id}            → 卡片全文
        # /card/{id}/evidence   → 这张卡依据的原文（溯源链）
        m = re.match(r"^/card/(\d+)(/evidence)?$", p.path)
        if m:
            cid = int(m.group(1))
            con = _con()
            try:
                row = con.execute(
                    "SELECT id,title,body,kind,topic,source_path,source_range,created_at,status,related_card "
                    "FROM cards WHERE id=?", (cid,)).fetchone()
                if not row:
                    return self._send(404, {"error": "card not found"})
                card = dict(row)
                if m.group(2):
                    ev = []
                    links = list(con.execute(
                        "SELECT span_id, role, quote, char_start, char_end "
                        "FROM card_evidence WHERE card_id=? ORDER BY id", (cid,)))
                    try:
                        c2 = sqlite3.connect(CRADLE_DB)
                        c2.row_factory = sqlite3.Row
                        for l in links:
                            sp = c2.execute(
                                "SELECT conv_id, seq, ts FROM spans WHERE span_id=?",
                                (l["span_id"],)).fetchone()
                            d = dict(l)
                            d["conv_id"] = sp["conv_id"] if sp else None
                            d["seq"] = sp["seq"] if sp else None
                            d["ts"] = sp["ts"] if sp else None
                            ev.append(d)
                        c2.close()
                    except Exception as e:
                        for l in links:
                            d = dict(l); d["conv_id"] = d["seq"] = d["ts"] = None
                            ev.append(d)
                    return self._send(200, {"card": card, "evidence": ev,
                                            "count": len(ev)})
                return self._send(200, {"card": card})
            finally:
                con.close()

        if p.path in ("/find", "/search"):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            q = (qs.get("q") or [""])[0].strip()
            if not q:
                return self._send(400, {"error": "missing q"})
            limit = int((qs.get("limit") or ["6"])[0])
            min_score = float((qs.get("min_score") or ["0.30"])[0])
            max_chars = int((qs.get("max_chars") or ["500"])[0])
            intent = (qs.get("intent") or [None])[0]
            owner = (qs.get("owner") or [None])[0]
            claim = (qs.get("claim") or [None])[0]
            hop = int((qs.get("hop") or ["1"])[0])
            mode = (qs.get("mode") or [""])[0].strip().lower()
            con = _con()
            try:
                # mode=timeline：问「什么时候/哪次」时换一种检索方式。
                # 不走向量，因为「哪张卡最像」和「哪次对话提到过」是两个问题。
                if mode == "timeline":
                    tl = search_mod.timeline(con, q, limit=limit)
                    bundle = {"cards": [], "timeline": tl, "mode": "timeline"}
                    _log_recall(con, q, [])
                    bundle["memories"] = tl
                    bundle["resources"] = []
                    return self._send(200, bundle)

                _fn = search_mod.deep_search if hop >= 2 else search_mod.search
                bundle = _fn(con, q, topk=limit,
                             min_score=min_score, intent=intent,
                             owner=owner, claim=claim)
                for c in bundle.get("cards", []):
                    if max_chars and len(c.get("body") or "") > max_chars:
                        c["body"] = c["body"][:max_chars] + "…"
                _log_recall(con, q, [c.get("id") or c.get("card_id") for c in bundle.get("cards", [])][:8])

                # 兼容 OpenViking 客户端的字段形状：client.find() 读 memories[]
                bundle["memories"] = [{
                    "uri": f"viking://user/da-feiyu/memories/card/{c['id']}",
                    "title": c.get("title") or "",
                    "content": c.get("body") or "",
                    "abstract": (c.get("body") or "")[:200],
                    "score": c.get("score") or 0.0,
                    "kind": c.get("kind"),
                    "owner": c.get("owner"),
                    "claim_type": c.get("claim_type"),
                    "card_id": c["id"],
                    # 时间必须给出去：用户问「我什么时候…」时，没有日期就答不了。
                    # occurred_at 是这件事发生的时间，created_at 是这张卡入库的时间。
                    "occurred_at": c.get("occurred_at") or "",
                    "created_at": c.get("created_at") or "",
                    "when": (c.get("occurred_at") or c.get("created_at") or "")[:10],
                    "evidence": c.get("evidence") or [],
                } for c in bundle.get("cards", [])]
                bundle["resources"] = []

                # ── 编年史：只返回信号 + 轻量摘要，不参与事实排序 ──
                hint = search_mod.chrono_hint(q)
                bundle["chrono_hint"] = hint
                if hint.get("need"):
                    crow = None
                    try:
                        ccon = sqlite3.connect(DB, timeout=5)
                        crow = ccon.execute(
                            "SELECT * FROM chronicles WHERE period_id='core'").fetchone()
                    except Exception:
                        crow = None
                    finally:
                        try: ccon.close()
                        except Exception: pass
                    if crow:
                        cd = _chron_dict(dict(zip(
                            ["period_id","granularity","start_ts","end_ts","msg_count",
                             "title","summary","threads","entities","turns","carries_over","built_at"],
                            crow)))
                        body = cd.get("summary") or ""
                        parts = re.split(r"(?<=[。；])", body)
                        ab = ""
                        for seg in parts:
                            if len(ab) + len(seg) > 120: break
                            ab += seg
                        bundle["memories"].insert(0, {
                            "uri": "viking://user/da-feiyu/memories/chrono/core",
                            "title": f"【当前状态】{cd.get('title') or ''}",
                            "content": (ab or body[:120]),
                            "abstract": (ab or body[:120]),
                            "score": 1.0,
                            "kind": "chronicle",
                            "owner": "both",
                            "card_id": 0,
                        })
                return self._send(200, bundle)
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:200]})
            finally:
                con.close()

        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)


        # ==== 能力登记簿（可写） ====
        if p.path == "/api/registry":
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                _n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(_n) or b"{}") if _n else {}
            except Exception:
                return self._send(400, {"error": "bad json"})

            import os as _oss

            IDX = "/opt/memvault/registry/index.json"
            SKD = "/opt/memvault/registry/skills"
            NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

            def _load():
                try:
                    return json.load(open(IDX, encoding="utf-8"))
                except Exception:
                    return {"skills": [], "mcp": []}

            def _save(doc):
                doc["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                tmp = IDX + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(doc, f, ensure_ascii=False, indent=2)
                _oss.chmod(tmp, 0o644)
                _oss.replace(tmp, IDX)

            def _clean_skill(s):
                n = str(s.get("name") or "").strip()
                if not NAME_RE.match(n):
                    raise ValueError("名字只能用字母数字和 . _ - ：%r" % n)
                return {
                    "name": n,
                    "title": str(s.get("title") or n)[:120],
                    "desc": str(s.get("desc") or "")[:600],
                    "source": str(s.get("source") or "手工登记")[:300],
                    "enabled": bool(s.get("enabled", True)),
                }

            def _clean_mcp(m):
                n = str(m.get("name") or "").strip()
                if not NAME_RE.match(n):
                    raise ValueError("名字只能用字母数字和 . _ - ：%r" % n)
                url = str(m.get("url") or "").strip()
                if not (url.startswith("http://") or url.startswith("https://")):
                    raise ValueError("地址必须以 http:// 或 https:// 开头")
                return {
                    "name": n,
                    "title": str(m.get("title") or n)[:120],
                    "url": url[:300],
                    "transport": str(m.get("transport") or "streamable_http")[:40],
                    "desc": str(m.get("desc") or "")[:600],
                }

            op = str(data.get("op") or "save")
            if op not in ("save", "body", "delete"):
                return self._send(400, {"error": "未知 op：%s（只认 save / body / delete）" % op[:40]})

            if op == "body":
                # 单独写某个技能的正文
                n = str(data.get("name") or "")
                if not NAME_RE.match(n):
                    return self._send(400, {"error": "bad name"})
                _oss.makedirs(SKD, exist_ok=True)
                fp = _oss.path.join(SKD, n + ".md")
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(str(data.get("text") or ""))
                return self._send(200, {"ok": True, "path": fp, "bytes": _oss.path.getsize(fp)})

            if op == "delete":
                doc = _load()
                kind, name = data.get("kind"), str(data.get("name") or "")
                if kind == "skill":
                    doc["skills"] = [s for s in doc.get("skills", []) if s.get("name") != name]
                    fp = _oss.path.join(SKD, name + ".md")
                    if NAME_RE.match(name) and _oss.path.exists(fp):
                        _oss.remove(fp)
                elif kind == "mcp":
                    doc["mcp"] = [m for m in doc.get("mcp", []) if m.get("name") != name]
                else:
                    return self._send(400, {"error": "kind must be skill|mcp"})
                _save(doc)
                return self._send(200, {"ok": True, "deleted": name})

            # 默认：整份保存（前端增删改后一次提交）
            try:
                skills = [_clean_skill(s) for s in (data.get("skills") or [])]
                mcp = [_clean_mcp(m) for m in (data.get("mcp") or [])]
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            names = [s["name"] for s in skills]
            if len(names) != len(set(names)):
                return self._send(400, {"error": "技能名重复"})
            mnames = [m["name"] for m in mcp]
            if len(mnames) != len(set(mnames)):
                return self._send(400, {"error": "MCP 名重复"})
            _save({"skills": skills, "mcp": mcp})
            return self._send(200, {"ok": True, "skills": len(skills), "mcp": len(mcp)})

        # ==== NEWUI API POST (managed) ====
        if p.path == "/api/card":
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                _n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(_n) or b"{}") if _n else {}
            except Exception:
                data = {}
            try:
                cid = int(data.get("id") or 0)
            except Exception:
                return self._send(400, {"error": "id required"})
            sets, args = [], []
            if data.get("status"):
                st = str(data["status"])
                if st not in ("active", "archive", "proposal", "rejected"):
                    return self._send(400, {"error": "bad status"})
                sets.append("status=?")
                args.append(st)
                if st == "active":
                    sets.append("promoted_at=?")
                    args.append(time.strftime("%Y-%m-%d %H:%M:%S"))
            if "pinned" in data:
                sets.append("pinned=?")
                args.append(1 if data.get("pinned") else 0)
            if not sets:
                return self._send(400, {"error": "nothing to update"})
            sets.append("updated_at=?")
            args.append(time.strftime("%Y-%m-%d %H:%M:%S"))
            con = _con()
            try:
                con.execute("update cards set " + ",".join(sets) + " where id=?", args + [cid])
                con.commit()
            finally:
                con.close()
            return self._send(200, {"ok": True, "id": cid})

        if p.path == "/api/approve":
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                _n2 = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(_n2) or b"{}") if _n2 else {}
            except Exception:
                data = {}
            ids = [int(x) for x in (data.get("ids") or []) if str(x).isdigit()]
            act = str(data.get("action") or "approve")
            if not ids:
                return self._send(400, {"error": "ids required"})
            st = "active" if act == "approve" else "rejected"
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            con = _con()
            try:
                q = "update cards set status=?, promoted_at=?, updated_at=? where id in (%s)" % ",".join("?" * len(ids))
                con.execute(q, [st, now, now] + ids)
                con.commit()
            finally:
                con.close()
            return self._send(200, {"ok": True, "n": len(ids), "status": st})

        if p.path == "/api/gwenv":
            try:
                ln = int(self.headers.get("Content-Length") or 0)
                upd = json.loads(self.rfile.read(ln) or b"{}")
            except Exception as e:
                return self._send(400, {"error": "bad json: %s" % str(e)[:80]})
            try:
                n = _gwenv_write(upd)
            except Exception as e:
                return self._send(500, {"error": str(e)[:200]})
            import subprocess as _sp
            r = _sp.run(["systemctl", "restart", "memvault-gw"], capture_output=True, text=True, timeout=30)
            return self._send(200, {"ok": True, "saved": n, "restart": r.returncode})

        if p.path in ("/focus/close", "/focus/reopen", "/summary/delete"):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                data = {}
            if p.path in ("/focus/close", "/focus/reopen"):
                cid = (data.get("conv_id") or "").strip()
                if not cid:
                    return self._send(400, {"ok": False, "error": "conv_id required"})
                try:
                    fn = _focus.close if p.path == "/focus/close" else _focus.reopen
                    return self._send(200, fn(_con(), cid))
                except Exception as e:
                    return self._send(500, {"error": type(e).__name__, "detail": str(e)[:300]})
            q_ = (data.get("query") or "").strip()
            if not q_:
                return self._send(400, {"ok": False, "error": "query required"})
            try:
                c = _con()
                with c:
                    cur = c.execute("DELETE FROM summaries WHERE query=?", (q_,))
                return self._send(200, {"ok": True, "deleted": cur.rowcount})
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:300]})
        if p.path == "/dreams/new":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                data = {}
            con = _con()
            try:
                r = _dream.new_hand(con, data.get("title"), data.get("body"), data.get("span"))
            finally:
                con.close()
            return self._send(200 if r.get("ok") else 400, r)

        m = re.match(r"^/dreams/(\d+)/status$", p.path)
        if m:
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                data = {}
            st = (data.get("status") or "").strip()
            if st not in ("draft", "confirmed", "rejected"):
                return self._send(400, {"error": "bad status", "got": st})
            try:
                con = _con()
                with _lock:
                    con.execute("UPDATE dreams SET status=? WHERE id=?", (st, int(m.group(1))))
                    con.commit()
                return self._send(200, {"ok": True, "id": int(m.group(1)), "status": st})
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:200]})

        if p.path == "/import":
            # 从原始文本回灌 conv_log
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > 300 * 1024 * 1024:
                    return self._send(400, {"error": "bad Content-Length"})
                raw = self.rfile.read(n)
                tmp = "/tmp/_mem_import_%d.txt" % int(time.time() * 1000)
                with open(tmp, "wb") as f:
                    f.write(raw)
                from . import textio as _tio
                r = _tio.import_text(DB, tmp, dry=False)
                try:
                    import os
                    os.unlink(tmp)
                except OSError:
                    pass
                return self._send(200, r)
            except Exception as e:
                return self._send(500, {"error": str(e)})

        if p.path in ("/settings", "/api/settings"):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                data = {}
            try:
                con = _con()
                n_set = _dream.set_settings(con, data)
                out = {"ok": True, "saved": n_set, "items": _dream.get_settings(con)}
                if "dream.cron" in (data or {}):
                    ok, msg = _dream.sync_timer(out["items"]["dream.cron"], "human")
                    out["timer"] = {"ok": ok, "value": msg}
                if "dream.machine_cron" in (data or {}):
                    ok2, msg2 = _dream.sync_timer(out["items"]["dream.machine_cron"], "machine")
                if "focus.cron" in (data or {}):
                    ok3, msg3 = _dream.sync_timer(out["items"]["focus.cron"], "focus")
                    out["timer_machine"] = {"ok": ok2, "value": msg2}
                return self._send(200, out)
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:300]})

        if p.path in ("/dream/run", "/dreams/run"):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                data = {}
            apply_ = bool(data.get("apply"))
            kind_ = (data.get("kind") or "human").strip()
            days_ = data.get("days")
            try:
                if kind_ == "machine":
                    r = _dream.run_machine(apply=apply_)
                else:
                    q_ = (data.get("query") or "").strip() or None
                    r = _dream.run(apply=apply_, days=(int(days_) if days_ else None), query=q_)
                return self._send(200, r)
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:300]})

        if p.path in ("/procedure/run", "/procedures/run"):
            data = _body(self)
            cats = data.get("categories") or None
            r = _procedure.run(cats=cats, apply=bool(data.get("apply", True)))
            return self._send(200, r)

        if p.path == "/procedures/status":
            data = _body(self)
            con = _con()
            return self._send(200, _procedure.set_status(con, int(data.get("id") or 0), str(data.get("status") or "draft")))

        if p.path in ("/focus/build", "/focus/run"):
            data = _body(self)
            con = _con()
            cid = data.get("conv_id")
            if cid:
                return self._send(200, _focus.build(con, cid, apply=bool(data.get("apply", True))))
            return self._send(200, _focus.build_recent(con, hours=int(data.get("hours") or 72),
                                                       apply=bool(data.get("apply", True)),
                                                       limit=int(data.get("limit") or 10)))

        if p.path in ("/summary/refresh", "/summaries/refresh"):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                data = {}
            q_ = (data.get("query") or "").strip()
            try:
                c = _con()
                if not q_:
                    return self._send(200, _summary.refresh_all(c))
                return self._send(200, _summary.refresh(c, q_, apply=bool(data.get("apply", True))))
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:300]})

        if p.path in ("/dream/merge", "/dreams/merge"):
            if not self._auth():
                return self._send(401, {"error": "unauthorized"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n) or b"{}") if n else {}
            except Exception:
                data = {}
            try:
                c = _con(); st = _dream.get_settings(c)
                kind_ = (data.get("kind") or "human").strip()
                r = _dream.merge_pairs(c, st, kind=kind_, apply=bool(data.get("apply")))
                return self._send(200, {"ok": True, "kind": kind_, "pairs": r})
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:300]})

        if p.path != "/memlearn":
            return self._send(404, {"error": "not found"})
        if not self._auth():
            return self._send(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send(400, {"error": "bad json", "detail": str(e)[:120]})

        # 两种用法：传 messages/text 走提炼，传 content 直接写卡
        text = data.get("messages") or data.get("text")
        if text is not None:
            if isinstance(text, list):
                text = "\n".join(
                    "%s: %s" % (m.get("role", "?"), m.get("content", ""))
                    if isinstance(m, dict) else str(m)
                    for m in text
                )
            try:
                cards = extract_mod.extract(str(text))
            except Exception as e:
                return self._send(500, {"error": "extract failed",
                                        "detail": type(e).__name__ + ": " + str(e)[:160]})
            con = _con()
            ids = []
            try:
                with _lock:
                    for c in cards:
                        cid, created = store.upsert(con, c)
                        ids.append({"id": cid, "created": created})
                        if created:
                            store.put_vector(
                                con, cid, embed.embed([c["title"] + "\n" + c["body"][:3000]])[0]
                            )
                    con.commit()
            except Exception as e:
                return self._send(500, {"error": type(e).__name__, "detail": str(e)[:200]})
            finally:
                con.close()
            return self._send(201, {"extracted": len(cards), "cards": ids})

        body = (data.get("content") or data.get("body") or "").strip()
        if not body:
            return self._send(400, {"error": "missing content"})
        card = {
            "title": (data.get("title") or body.split("\n")[0])[:120],
            "body": body,
            "kind": data.get("kind") or data.get("type"),
            "topic": data.get("topic"),
            "source_path": data.get("source") or "http",
            "created_at": data.get("created_at"),
        }
        con = _con()
        try:
            with _lock:
                cid, created = store.upsert(con, card)
                if created:
                    vec = embed.embed([card["title"] + "\n" + body[:3000]])[0]
                    store.put_vector(con, cid, vec)
                con.commit()
            return self._send(201 if created else 200, {"id": cid, "created": created})
        except Exception as e:
            return self._send(500, {"error": type(e).__name__, "detail": str(e)[:200]})
        finally:
            con.close()


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    sys.stderr.write("memvault listening on %s:%d (db=%s)\n" % (HOST, PORT, DB))
    srv.serve_forever()


if __name__ == "__main__":
    main()
