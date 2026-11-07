// ⏳ 待审 — agent 写进来但还没被人工确认的卡片（proposal）
// 数据：/admin/cradle/api/pending（4174 /api/pending）
// 动作：/admin/cradle/api/approve  {ids:[...], action:"approve"|"reject"}
import { get, post, escHtml, fmtDateTime } from "../api.js";
import { loadingBlock, toast } from "../ui.js";

export default {
  title: "待审",
  async mount(root) {
    root.innerHTML = `<div id="pd-body">${loadingBlock()}</div>`;
    const box = () => root.querySelector("#pd-body");

    async function render() {
      let d;
      try {
        d = await get("/admin/cradle/api/pending");
      } catch (e) {
        box().innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>读取失败：${escHtml(e.message)}</div></div>`;
        return;
      }
      const items = d.items || [];
      if (!items.length) {
        box().innerHTML = `
          <div class="banner banner-info"><span>💡</span><div>
            <b>没有待审卡。</b>agent 通过 MCP 写进来的记忆默认是 <code>proposal</code>，
            批准后才会参与检索——这里是那道闸门。
          </div></div>
          <div class="empty">队列是空的</div>`;
        return;
      }
      const rows = items.map(c => `
        <div class="item" data-id="${c.id}">
          <div class="item-row">
            <span class="item-title">${escHtml(c.title || "(无标题)")}</span>
            <span class="faint text-xs">#${c.id} · ${escHtml(c.owner || "-")} · ${escHtml((c.created_at || "").slice(0, 16))}</span>
          </div>
          <div class="item-sub clamp3">${escHtml((c.body || "").slice(0, 300))}</div>
          <div class="item-actions">
            <button class="btn btn-sm btn-primary" data-act="approve">批准</button>
            <button class="btn btn-sm btn-danger-soft" data-act="reject">拒绝</button>
          </div>
        </div>`).join("");
      box().innerHTML = `
        <div class="banner banner-warn"><span>⏳</span><div>
          共 <b>${items.length}</b> 张待审。批准 = 转 <code>active</code>，立刻进检索；拒绝 = 转 <code>rejected</code>。
        </div></div>
        <div class="btn-row" style="margin-bottom:10px">
          <button class="btn btn-sm btn-secondary" id="pd-all">全部批准</button>
          <button class="btn btn-sm btn-ghost" id="pd-refresh">刷新</button>
        </div>
        <div id="pd-list">${rows}</div>`;

      box().querySelector("#pd-refresh").onclick = render;
      box().querySelector("#pd-all").onclick = () => act(items.map(c => c.id), "approve");
      box().querySelector("#pd-list").onclick = ev => {
        const btn = ev.target.closest("button[data-act]");
        if (!btn) return;
        const id = +btn.closest(".item").dataset.id;
        act([id], btn.dataset.act);
      };
    }

    async function act(ids, action) {
      try {
        const r = await post("/admin/cradle/api/approve", { ids, action });
        toast(`已${action === "approve" ? "批准" : "拒绝"} ${r.n ?? ids.length} 张`, "ok");
      } catch (e) {
        toast("失败：" + e.message, "err");
      }
      render();
    }

    render();
  },
};
