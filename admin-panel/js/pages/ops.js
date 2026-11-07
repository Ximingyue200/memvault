// 🩺 运维与健康 — 服务/定时器/备份/日报/余额
// 数据：/admin/cradle/api/ops、/admin/cradle/api/overview、/admin/cradle/report/latest.md
import { get, escHtml } from "../api.js";
import { loadingBlock } from "../ui.js";

function dot(active) {
  const ok = active === "active";
  return `<span class="dot ${ok ? "dot-hot" : ""}" style="background:${ok ? "#3fbf6f" : "#9aa3b0"}"></span>`;
}

export default {
  title: "运维与健康",
  async mount(root) {
  // 💰 按任务花费（_meter 记账，24 小时窗口）
  (async () => {
    try {
      const u = await get('/cradle/api/usage');
      const box = document.createElement('section');
      const rows = (u.items || []).map(i => `<tr>
        <td>${escHtml(i.task)}</td><td class="mono">${escHtml(i.key||"-")}</td><td style="text-align:right">${i.calls}</td>
        <td style="text-align:right">${i.in.toLocaleString()}</td>
        <td style="text-align:right">${i.out.toLocaleString()}</td>
        <td style="text-align:right">${i.cached.toLocaleString()}</td>
        <td style="text-align:right">¥${(i.cny || 0).toFixed(3)}</td></tr>`).join('');
      box.innerHTML = `<h3 style="margin:18px 0 8px">💰 按任务花费 · 最近 24 小时</h3>
        <div class="card"><table class="tbl">
        <thead><tr><th>任务</th><th>Key</th><th>调用</th><th>输入</th><th>输出</th><th>命中缓存</th><th>估算</th></tr></thead>
        <tbody>${rows || '<tr><td colspan="6">还没有记账（重启后第一次 LLM 调用才会写入）</td></tr>'}</tbody>
        <tfoot><tr><th>合计</th><th></th><th>${u.total?.calls || 0}</th><th colspan="3"></th>
        <th>¥${(u.total?.cny || 0).toFixed(3)}</th></tr></tfoot></table>
        <p class="hint">单价取 settings 的 llm.price_in / llm.price_out / llm.price_cached（默认 2 / 3 / 0.5 元每百万 token）。</p></div>`;
      root.appendChild(box);
    } catch (e) { /* 记账没开也不报错 */ }
  })();


    root.innerHTML = `<div id="op-body">${loadingBlock()}</div>`;
    let ops = {}, ov = {}, report = "";
    try {
      ops = await get("/admin/cradle/api/ops");
      ov = await get("/admin/cradle/api/overview");
    } catch (e) {
      root.querySelector("#op-body").innerHTML =
        `<div class="banner banner-warn"><span>⚠️</span><div>读取失败：${escHtml(e.message)}</div></div>`;
      return;
    }
    try {
      const r = await fetch("/admin/cradle/report/latest.md");
      report = r.ok ? await r.text() : "";
    } catch { report = ""; }

    const svcs = (ops.services || []).map(s => `
      <div class="item" style="padding:8px 12px">
        <div class="item-row">
          <span class="text-sm">${dot(s.active)}${escHtml(s.name)}</span>
          <span class="badge ${s.active === "active" ? "badge-info" : "badge-warn"}">${escHtml(s.active)}</span>
        </div>
      </div>`).join("");

    const baks = (ops.backups || []).map(b => `
      <div class="item" style="padding:8px 12px">
        <div class="item-row">
          <span class="text-sm mono">${escHtml(b.name)}</span>
          <span class="faint text-xs">${b.mb} MB</span>
        </div>
      </div>`).join("");

    const t = ov.today || {};
    root.querySelector("#op-body").innerHTML = `
      <div class="banner banner-info"><span>🩺</span><div>
        这张表是「它到底还在不在干活」的证据：注入次数、检索次数、新卡数，掉到 0 就是坏了。
      </div></div>

      <div class="stat-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px">
        <div class="card"><div class="faint text-xs">今日注入</div><div style="font-size:26px;font-weight:600">${t.inject ?? 0}</div><div class="faint text-xs">平均 ${(t.inject_chars || 0).toFixed ? t.inject_chars.toFixed(0) : t.inject_chars} 字/次${t.recall_api ? " · 检索接口 " + t.recall_api + " 次" : ""}</div></div>
        <div class="card"><div class="faint text-xs">今日检索</div><div style="font-size:26px;font-weight:600">${t.recall ?? 0}</div><div class="faint text-xs">次</div></div>
        <div class="card"><div class="faint text-xs">今日新卡</div><div style="font-size:26px;font-weight:600">${t.new_cards ?? 0}</div><div class="faint text-xs">待学 ${ov.pending_learn ?? 0}</div></div>
        <div class="card"><div class="faint text-xs">库大小</div><div style="font-size:26px;font-weight:600">${ov.db_mb ?? "-"}</div><div class="faint text-xs">MB</div></div>
        <div class="card"><div class="faint text-xs">余额</div><div style="font-size:26px;font-weight:600">${ov.balance ?? "-"}</div><div class="faint text-xs">DeepSeek</div></div>
        <div class="card"><div class="faint text-xs">开机</div><div class="text-sm" style="margin-top:6px">${escHtml(ops.uptime || "-")}</div></div>
      </div>

      <div class="grid-2" style="margin-top:18px">
        <div>
          <div class="section-title">服务</div>
          <div class="card">${svcs || '<div class="empty">无</div>'}</div>
        </div>
        <div>
          <div class="section-title">最近备份</div>
          <div class="card">${baks || '<div class="empty">还没有备份</div>'}</div>
        </div>
      </div>

      <div class="section-title mt24">定时器</div>
      <pre class="mono text-xs" style="white-space:pre-wrap;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px">${escHtml(ops.timers || "-")}</pre>

      <div class="section-title mt24">今日日报</div>
      <pre class="mono text-xs" style="white-space:pre-wrap;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px">${escHtml(report || "暂无日报")}</pre>`;
  },
};
