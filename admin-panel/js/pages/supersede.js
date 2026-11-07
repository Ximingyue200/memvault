// ♻️ 取代关系 — 新卡 declare supersedes 旧卡，旧卡检索时降权
// 数据：/admin/cradle/api/supersedes
import { get, escHtml } from "../api.js";
import { loadingBlock } from "../ui.js";

export default {
  title: "取代与冲突",
  async mount(root) {
    root.innerHTML = `<div id="sp-body">${loadingBlock()}</div>`;
    let d;
    try {
      d = await get("/admin/cradle/api/supersedes");
    } catch (e) {
      root.querySelector("#sp-body").innerHTML =
        `<div class="banner banner-warn"><span>⚠️</span><div>读取失败：${escHtml(e.message)}</div></div>`;
      return;
    }
    const items = d.items || [];
    const rows = items.map(it => `
      <div class="item">
        <div class="item-row">
          <span class="item-title">#${it.new_id} ${escHtml(it.new_title || "")}</span>
          <span class="badge badge-muted">${escHtml(it.kind || "-")}</span>
        </div>
        <div class="item-sub">
          <span class="faint">取代</span> #${it.old_id} ${escHtml(it.old_title || "")}
          <span class="badge badge-warn">${escHtml(it.old_status || "-")}</span>
        </div>
      </div>`).join("");
    root.querySelector("#sp-body").innerHTML = `
      <div class="banner banner-info"><span>🔁</span><div>
        旧卡一旦被取代，检索时分数乘 <b>0.55</b>；它不会被删，证据链照样查得到。
        目前共 <b>${d.count ?? items.length}</b> 对。
      </div></div>
      <div class="card">${rows || `<div class="empty">还没有取代关系</div>`}</div>
      <p class="faint text-xs mt24">
        判定规则：严格对立（一侧 TODO 一侧 DONE 且另一侧相反）+ 标题相似度 ≥ 0.55；
        每晚 04:10 由 <code>memvault-supersede.timer</code> 自动跑。
      </p>`;
  },
};
