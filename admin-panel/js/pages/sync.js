// 💾 备份与数据 — 本地 7 份 + 异地 15 份，一条命令在线备份
import { get, post, escHtml } from "../api.js";
import { loadingBlock, toast } from "../ui.js";

export default {
  title: "备份与数据",
  async mount(root) {
    root.innerHTML = `<div id="sy-body">${loadingBlock()}</div>`;
    const box = () => root.querySelector("#sy-body");

    async function render() {
      let ops = {};
      try {
        ops = await get("/admin/cradle/api/ops");
      } catch (e) {
        box().innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>读取失败：${escHtml(e.message)}</div></div>`;
        return;
      }
      const baks = ops.backups || [];
      box().innerHTML = `
        <div class="banner banner-info"><span>💾</span><div>
          每天 <b>03:30</b> 自动备份：本机留 7 份，另一台留 15 份。
          用的是 SQLite 在线备份接口（<code>Connection.backup()</code>），不锁库、不用停服务。
        </div></div>

        <div class="btn-row" style="margin-bottom:12px">
          <button class="btn btn-primary btn-sm" id="run">立即备份</button>
          <button class="btn btn-secondary btn-sm" id="reload">刷新</button>
        </div>
        <div id="runout"></div>

        <div class="section-title">本机备份 · ${baks.length}</div>
        <div class="card">
          ${baks.map(b => `
            <div class="item" style="padding:8px 12px">
              <div class="item-row">
                <span class="text-sm mono">${escHtml(b.name)}</span>
                <span class="faint text-xs">${b.mb} MB</span>
              </div>
            </div>`).join("") || '<div class="empty">还没有备份</div>'}
        </div>
        <p class="faint text-xs mt24">
          异地目标：<code><user>@<backup-host>:/path/to/backup/</code>（docker 宿主）。
          备份脚本 <code>/usr/local/bin/mvbackup.sh</code>，定时器 <code>memvault-backup.timer</code>。
        </p>`;

      box().querySelector("#reload").onclick = render;
      box().querySelector("#run").onclick = async ev => {
        const b = ev.target;
        b.disabled = true;
        b.textContent = "备份中…（约 10 秒）";
        try {
          const r = await post("/admin/cradle/api/backup", {});
          const okk = r.ok !== false;
          toast(okk ? "备份完成" : "备份失败", okk ? "ok" : "err");
          box().querySelector("#runout").innerHTML =
            `<pre class="mono text-xs" style="white-space:pre-wrap;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px">${escHtml((r.out || "") + (r.err ? "\n" + r.err : "") || "(无输出)")}</pre>`;
        } catch (e) {
          toast("失败：" + e.message, "err");
        }
        b.disabled = false;
        b.textContent = "立即备份";
        render();
      };
    }
    render();
  },
};
