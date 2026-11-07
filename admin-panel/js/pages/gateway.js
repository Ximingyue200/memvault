// 🚦 网关与延迟 — 上游、注入参数、逐接口延迟
import { get, post, escHtml } from "../api.js";
import { loadingBlock, toast } from "../ui.js";

const TARGETS = [
  { path: "/admin/cradle/api/overview", label: "记忆库 overview", desc: "4174 汇总" },
  { path: "/admin/cradle/api/ops", label: "运维 ops", desc: "4174 服务/备份" },
  { path: "/v1/models", label: "模型列表", desc: "网关可见模型" },
  { path: "/health", label: "健康", desc: "网关自身" },
];

export default {
  title: "网关与延迟",
  async mount(root) {
    this.root = root;
    root.innerHTML = `<div id="gw-body">${loadingBlock()}</div>`;
    const box = () => root.querySelector("#gw-body");

    let env = { items: {} }, ov = {};
    try {
      env = await get("/admin/cradle/api/gwenv");
      ov = await get("/admin/cradle/api/overview");
    } catch (e) {
      box().innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>读取失败：${escHtml(e.message)}</div></div>`;
      return;
    }
    const it = env.items || {};
    const v = k => (it[k] && it[k].v) || "—";

    const t = ov.today || {};
    box().innerHTML = `
      <div class="banner banner-info"><span>🚦</span><div>
        消息走 <code>4180</code> → 上游 <b>${escHtml(v("GW_UPSTREAM"))}</b>；每轮先向 4174 取记忆，再拼进 prompt。
        注入 <b>${escHtml(v("GW_TOPK"))}</b> 张、最低分 <b>${escHtml(v("GW_MIN_SCORE"))}</b>。
      </div></div>

      <div class="stat-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px">
        <div class="card"><div class="faint text-xs">今日注入</div><div style="font-size:26px;font-weight:600">${t.inject ?? 0}</div></div>
        <div class="card"><div class="faint text-xs">今日检索</div><div style="font-size:26px;font-weight:600">${t.recall ?? 0}</div></div>
        <div class="card"><div class="faint text-xs">注入卡片</div><div style="font-size:26px;font-weight:600">${ov.active ?? 0}</div><div class="faint text-xs">活跃卡池</div></div>
        <div class="card"><div class="faint text-xs">向量模型</div><div class="text-sm" style="margin-top:6px">${escHtml(v("MEMVAULT_EMBED_MODEL"))}</div></div>
      </div>

      <div class="card-head mt24"><div class="card-title">🔌 上游与注入</div>
        <a class="btn btn-sm btn-secondary" href="#/console-config">改参数</a></div>
      <div class="card">
        ${row("上游", v("GW_UPSTREAM"))}
        ${row("注入张数", v("GW_TOPK"))}
        ${row("最低分", v("GW_MIN_SCORE"))}
        ${row("调试日志", v("GW_DEBUG"))}
        ${row("向量地址", v("MEMVAULT_EMBED_URL"))}
        ${row("向量模型", v("MEMVAULT_EMBED_MODEL"))}
      </div>

      <div class="card-head mt24"><div class="card-title">📡 连接自检</div>
        <button class="btn btn-sm btn-primary" id="ping">开始自检</button></div>
      <div class="card" id="pingbox"><p class="muted text-sm">尚未自检</p></div>

      <p class="faint text-xs mt24">
        真正的延迟在模型生成上：一次带记忆问答通常 5–9 秒（记忆检索 1–2 秒 + 上游 4–7 秒）。
      </p>`;

    box().querySelector("#ping").onclick = async ev => {
      const el = ev.target;
      el.disabled = true;
      el.textContent = "测试中…";
      const out = [];
      for (const t of TARGETS) {
        const t0 = performance.now();
        let ok = false, code = 0;
        try {
          const r = await fetch(t.path, { cache: "no-store" });
          ok = r.ok; code = r.status;
        } catch { ok = false; }
        out.push({ ...t, ms: Math.round(performance.now() - t0), ok, code });
      }
      box().querySelector("#pingbox").innerHTML = out.map(o => `
        <div class="item" style="padding:8px 12px">
          <div class="item-row">
            <span class="text-sm">${o.ok ? "✅" : "❌"} ${escHtml(o.label)}</span>
            <span class="badge ${o.ms < 300 ? "badge-info" : o.ms < 1200 ? "badge-warn" : "badge-danger"}">${o.ms} ms</span>
          </div>
          <div class="item-sub faint text-xs">${escHtml(o.path)} · HTTP ${o.code}</div>
        </div>`).join("");
      el.disabled = false;
      el.textContent = "再测一次";
    };
  },
};

function row(k, val) {
  return `<div class="kv"><div class="k">${escHtml(k)}</div><div class="mono text-sm">${escHtml(String(val))}</div></div>`;
}
