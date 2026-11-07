// 📦 能力登记簿 — 技能与 MCP 端点，可直接改
// 只登记不预装：谁要用谁按名字拉一份。改完立即对 mvreg / 各 agent 生效。
import { get, post, escHtml } from "../api.js";
import { loadingBlock, toast } from "../ui.js";

const TRANSPORTS = ["streamable_http", "sse", "stdio", "http"];

export default {
  title: "能力登记簿",
  doc: { skills: [], mcp: [] },

  async mount(root) {
    this.root = root;
    root.innerHTML = `
      <p class="page-intro">技能和 MCP 端点的总账。这里只登记，不往每台 agent 上塞——谁要用谁按名字拉一份。</p>
      <div class="banner banner-info"><span>📦</span><div>
        手机上 <code>mvreg pull &lt;名字&gt;</code> 拉技能；别的机器
        <code>GET /registry/skill/&lt;名字&gt;</code>。MCP 端点复制地址填进对应 agent 就行。
      </div></div>

      <div class="btn-row mt16" style="margin-bottom:8px;position:sticky;top:0;z-index:5;background:var(--bg,#fff);padding:8px 0">
        <button class="btn btn-primary btn-sm" id="save">保存改动</button>
        <button class="btn btn-secondary btn-sm" id="reload">放弃改动</button>
        <span class="faint text-xs" id="dirty"></span>
      </div>

      <div id="reg-body">${loadingBlock()}</div>`;

    await this.load();
    const q = s => root.querySelector(s);
    q("#save").onclick = () => this.save();
    q("#reload").onclick = () => { if (confirm("放弃未保存的改动？")) this.load(); };
  },

  async load() {
    try {
      this.doc = await get("/admin/cradle/api/registry");
    } catch (e) {
      this.root.querySelector("#reg-body").innerHTML =
        `<div class="banner banner-warn"><span>⚠️</span><div>读不到登记簿：${escHtml(e.message)}</div></div>`;
      return;
    }
    this.doc.skills = this.doc.skills || [];
    this.doc.mcp = this.doc.mcp || [];
    this.render();
    this.markClean();
  },

  render() {
    const el = this.root.querySelector("#reg-body");
    const skills = this.doc.skills, mcp = this.doc.mcp;
    el.innerHTML = `
      <div class="section-title">技能 · ${skills.length}</div>
      <div id="skill-cards">${skills.map((s, i) => this.skillCard(s, i)).join("")}</div>
      <div class="btn-row mt8"><button class="btn btn-secondary btn-sm" id="add-skill">＋ 新增技能</button></div>

      <div class="section-title mt24">MCP 端点 · ${mcp.length}</div>
      <div id="mcp-cards">${mcp.map((m, i) => this.mcpCard(m, i)).join("")}</div>
      <div class="btn-row mt8"><button class="btn btn-secondary btn-sm" id="add-mcp">＋ 新增 MCP 端点</button></div>

      <p class="faint text-xs mt24">
        更新于 ${escHtml(this.doc.updated || "—")} · 文件 <code>/opt/memvault/registry/index.json</code> ·
        技能正文在 <code>registry/skills/&lt;名字&gt;.md</code>
      </p>`;

    el.querySelectorAll("input[data-k],textarea[data-k],select[data-k]").forEach(inp => {
      const auto = () => { if (inp.tagName === "TEXTAREA") grow(inp); };
      auto();
      inp.addEventListener("input", () => { auto(); this.collect(); this.markDirty(); });
      inp.addEventListener("change", () => { auto(); this.collect(); this.markDirty(); });
    });

    el.querySelector("#add-skill").onclick = () => {
      const n = prompt("技能名字（字母数字 . _ -）：");
      if (!n) return;
      this.collect();
      this.doc.skills.unshift({ name: n.trim(), title: n.trim(), desc: "", source: "手工登记", enabled: true });
      this.render(); this.markDirty();
    };
    el.querySelector("#add-mcp").onclick = () => {
      const n = prompt("端点名字，如 cradle：");
      if (!n) return;
      this.collect();
      this.doc.mcp.unshift({ name: n.trim(), title: n.trim(), url: "http://10.200.0.17:4181/mcp", transport: "streamable_http", desc: "" });
      this.render(); this.markDirty();
    };
    el.querySelectorAll("[data-dels]").forEach(b => {
      b.onclick = () => {
        const i = +b.dataset.dels;
        if (!confirm(`删除技能「${this.doc.skills[i].name}」？对应的 md 文件也会删。`)) return;
        this.collect(); this.doc.skills.splice(i, 1); this.render(); this.markDirty();
      };
    });
    el.querySelectorAll("[data-delm]").forEach(b => {
      b.onclick = () => {
        const i = +b.dataset.delm;
        if (!confirm(`删除端点「${this.doc.mcp[i].name}」？`)) return;
        this.collect(); this.doc.mcp.splice(i, 1); this.render(); this.markDirty();
      };
    });
    el.querySelectorAll("[data-copys]").forEach(b => {
      b.onclick = () => copy(`mvreg pull ${this.doc.skills[+b.dataset.copys].name}`);
    });
    el.querySelectorAll("[data-copym]").forEach(b => {
      b.onclick = () => copy(this.doc.mcp[+b.dataset.copym].url || "");
    });
  },

  skillCard(s, i) {
    return `
      <div class="card" style="margin-bottom:10px;padding:12px">
        <div class="row" style="gap:8px;align-items:flex-end;flex-wrap:wrap">
          <label style="flex:1;min-width:120px">
            <div class="faint text-xs">名字</div>
            <input class="input input-sm" data-k="name" value="${escHtml(s.name)}" style="width:100%">
          </label>
          <label style="flex:1.4;min-width:140px">
            <div class="faint text-xs">标题</div>
            <input class="input input-sm" data-k="title" value="${escHtml(s.title || "")}" style="width:100%">
          </label>
          <label class="text-xs" style="padding-bottom:6px;white-space:nowrap">
            <input type="checkbox" data-k="enabled" ${s.enabled === false ? "" : "checked"}> 启用
          </label>
        </div>
        <label style="display:block;margin-top:8px">
          <div class="faint text-xs">说明（什么时候用）</div>
          <textarea class="input" data-k="desc" rows="3"
            style="width:100%;resize:vertical;line-height:1.55;min-height:64px">${escHtml(s.desc || "")}</textarea>
        </label>
        <div class="row mt8" style="gap:8px;flex-wrap:wrap">
          <button class="btn btn-secondary btn-sm" data-copys="${i}">拉取命令</button>
          <button class="btn btn-secondary btn-sm" data-dels="${i}">删除</button>
          <span class="faint text-xs" style="align-self:center">${escHtml(s.source || "")}</span>
        </div>
      </div>`;
  },

  mcpCard(m, i) {
    return `
      <div class="card" style="margin-bottom:10px;padding:12px">
        <div class="row" style="gap:8px;align-items:flex-end;flex-wrap:wrap">
          <label style="flex:1;min-width:120px">
            <div class="faint text-xs">名字</div>
            <input class="input input-sm" data-k="name" value="${escHtml(m.name)}" style="width:100%">
          </label>
          <label style="flex:0 0 150px">
            <div class="faint text-xs">传输</div>
            <select class="input input-sm" data-k="transport" style="width:100%">
              ${TRANSPORTS.map(t => `<option value="${t}" ${m.transport === t ? "selected" : ""}>${t}</option>`).join("")}
            </select>
          </label>
        </div>
        <label style="display:block;margin-top:8px">
          <div class="faint text-xs">地址</div>
          <input class="input input-sm mono" data-k="url" value="${escHtml(m.url || "")}"
                 style="width:100%;font-size:12px">
        </label>
        <label style="display:block;margin-top:8px">
          <div class="faint text-xs">说明</div>
          <textarea class="input" data-k="desc" rows="2"
            style="width:100%;resize:vertical;line-height:1.55;min-height:52px">${escHtml(m.desc || "")}</textarea>
        </label>
        <div class="row mt8" style="gap:8px">
          <button class="btn btn-secondary btn-sm" data-copym="${i}">复制地址</button>
          <button class="btn btn-secondary btn-sm" data-delm="${i}">删除</button>
        </div>
      </div>`;
  },

  collect() {
    const el = this.root.querySelector("#reg-body");
    el.querySelectorAll("#skill-cards .card").forEach((card, i) => {
      const s = this.doc.skills[i];
      if (!s) return;
      card.querySelectorAll("[data-k]").forEach(inp => {
        const k = inp.dataset.k;
        s[k] = k === "enabled" ? inp.checked : inp.value;
      });
    });
    el.querySelectorAll("#mcp-cards .card").forEach((card, i) => {
      const m = this.doc.mcp[i];
      if (!m) return;
      card.querySelectorAll("[data-k]").forEach(inp => { m[inp.dataset.k] = inp.value; });
    });
  },

  markDirty() { const d = this.root.querySelector("#dirty"); if (d) d.textContent = "● 有未保存的改动"; },
  markClean() { const d = this.root.querySelector("#dirty"); if (d) d.textContent = ""; },

  async save() {
    this.collect();
    const btn = this.root.querySelector("#save");
    btn.disabled = true; btn.textContent = "保存中…";
    try {
      const r = await post("/admin/cradle/api/registry", {
        op: "save", skills: this.doc.skills, mcp: this.doc.mcp,
      });
      if (r.error) throw new Error(r.error);
      toast(`已保存：${r.skills} 个技能 / ${r.mcp} 个端点`, "ok");
      this.markClean();
      await this.load();
    } catch (e) {
      toast("保存失败：" + e.message, "err");
    }
    btn.disabled = false; btn.textContent = "保存改动";
  },
};

function grow(ta) {
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight + 2, 320) + "px";
}

function copy(text) {
  if (!text) { toast("没内容可复制", "err"); return; }
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => toast("已复制：" + text, "ok"),
      () => toast("复制失败：" + text, "err"));
  } else {
    toast(text, "ok");
  }
}
