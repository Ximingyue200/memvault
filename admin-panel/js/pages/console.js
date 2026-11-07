// 📊 记忆库 — 本网关自己的统计页，数据走 /stats 与 /recent
import { get, escHtml } from "../api.js";
import { loadingBlock, toast } from "../ui.js";

function stat(label, value, sub, color) {
  return `<div class="card" style="padding:16px">
    <div class="faint text-xs">${escHtml(label)}</div>
    <div style="font-size:32px;font-weight:600;line-height:1.2;color:${color || "inherit"}">${escHtml(String(value ?? "-"))}</div>
    ${sub ? `<div class="faint text-xs">${escHtml(sub)}</div>` : ""}
  </div>`;
}

export default {
  title: "记忆库",
  async mount(root) {
    this.root = root;
    root.innerHTML = `<div id="cc-body">${loadingBlock()}</div>`;
    let st = {}, rc = { items: [] };
    try {
      st = await get("/stats");
      rc = await get("/recent");
    } catch (e) {
      root.querySelector("#cc-body").innerHTML =
        `<div class="banner banner-warn"><span>⚠️</span><div>读取失败：${escHtml(e.message)}</div></div>`;
      return;
    }
    const items = rc.items || rc.recent || [];
    const rows = items.map(c => `
      <div class="item" style="padding:10px 12px">
        <div class="item-row">
          <span class="text-sm">${escHtml(c.title || "")}</span>
          <span class="faint text-xs">${escHtml(c.when || "")}</span>
        </div>
      </div>`).join("");

    root.querySelector("#cc-body").innerHTML = `
      <div class="banner banner-info"><span>💡</span><div>
        这里是<b>本网关自己的记忆库</b>——上游模型每轮对话前看到的「旧事」就从这里取。
        参数在「记忆库参数」页改，改完即时生效。
      </div></div>

      <div class="stat-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px">
        ${stat("记忆卡片", st.cards, st.active + " 条 active", "#5aa9ff")}
        ${stat("证据链", st.evidence, "引用原文的条数", "#b28dff")}
        ${stat("对话原文", st.spans, "可回溯的消息片段")}
        ${stat("梦", st.dreams, (st.dreams_draft || 0) + " 条待确认", "#ffc857")}
        ${stat("套路", st.procedures, "程序性记忆")}
        ${stat("线索", st.threads, "跨会话的事")}
      </div>

      <div class="card-head mt24">
        <div class="card-title">🧠 最近记忆</div>
        <a class="btn btn-sm btn-secondary" href="#/console-config">改参数</a>
      </div>
      <div class="card">${rows || `<p class="muted text-sm">暂无</p>`}</div>

      <div class="mt24 faint text-xs">
        上次注入：${escHtml(st.last_inject || "—")} ·
        上次做梦：${escHtml(st.dream_last || "—")} ·
        注入记录：${st.injections ?? 0} 次（今天 ${st.injections_today ?? 0}）
      </div>
    `;
  },
};
