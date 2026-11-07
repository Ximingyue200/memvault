// 🛠️ 工具抽屉 — 总开关 + 外部 MCP 服务器编辑器 + 工具探测
import { get, post, escHtml } from '../api.js';
import { toast, badge, emptyState, modal, delegate, setBusy, ctl } from '../ui.js';
import { loadConfig, saveConfig, renderConfigPage, wireConfig } from '../config.js';

const TRANSPORTS = [
  { value: 'streamable_http', label: 'Streamable HTTP' },
  { value: 'sse', label: 'SSE' },
];

export default {
  title: '工具抽屉',
  servers: [],
  cfg: {},

  async mount(root) {
    this.root = root;
    root.innerHTML = `
      <p class="page-intro">工具抽屉让模型按需展开工具：内部工具走向量路由，外部 MCP 服务器按语义相关性自动接入，省 token 又不漏能力。</p>
      <div class="banner banner-info"><span>🧭</span><div>
        <b>外部 MCP 模式（mcp_mode）：</b>
        <code>off</code> 全部禁用 · <code>auto</code> 按对话语义自动路由展开 · <code>manual</code> 仅启用你手动选择的服务器。
      </div></div>
      <div id="reminder-cfg"></div>
      <div id="cfg"></div>
      <div class="section-title mt24">🔌 外部 MCP 服务器</div>
      <p class="card-desc mb12">在这里维护外部 MCP server 列表（比手动改 JSON 友好）。每项含名称、地址、传输方式与开关。保存后会自动清空缓存并刷新外部抽屉。</p>
      <div id="mcp-editor"></div>
    `;

    try {
      this.cfg = await loadConfig();
    } catch (e) {
      root.querySelector('#cfg').innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>加载配置失败：${escHtml(e.message)}</div></div>`;
      this.cfg = {};
    }

    const reminderCfgEl = root.querySelector('#reminder-cfg');
    reminderCfgEl.innerHTML = renderConfigPage('reminderTools', this.cfg);
    wireConfig(reminderCfgEl, this.cfg);

    const cfgEl = root.querySelector('#cfg');
    cfgEl.innerHTML = renderConfigPage('tools', this.cfg);
    wireConfig(cfgEl, this.cfg);

    this.servers = this.parseServers(this.cfg.mcp_servers);
    this.renderEditor();
    const regHost = document.createElement('div');
    regHost.innerHTML = `<div class="section-title mt24">📦 登记簿里的 MCP 端点</div>
      <p class="card-desc mb12 faint text-xs">想增删端点或改地址？去侧栏「能力登记簿」。</p>
      <p class="card-desc mb12">已经跑着的记忆服务端点，可被任意 agent 直接接上；不是每家都要接，用哪个接哪个。</p>
      <div id="reg-mcp" class="card"><p class="muted text-sm">加载中…</p></div>`;
    root.appendChild(regHost);
    this.renderRegMcp(regHost.querySelector('#reg-mcp'));

    delegate(root, {
      addRow: () => { this.servers = this.collectRows(); this.servers.push({ name: '', url: '', transport: 'streamable_http', headers: {}, enabled: true }); this.renderEditor(); },
      delRow: (el) => { this.servers = this.collectRows(); this.servers.splice(Number(el.dataset.i), 1); this.renderEditor(); },
      saveMcp: (el) => this.saveServers(el),
      listTools: (el) => this.listTools(el),
    });
  },

  parseServers(raw) {
    if (!raw) return [];
    try {
      const arr = JSON.parse(raw);
      if (!Array.isArray(arr)) return [];
      return arr.map(s => ({
        name: s?.name ?? '',
        url: s?.url ?? '',
        transport: s?.transport === 'sse' ? 'sse' : 'streamable_http',
        enabled: s?.enabled !== false,
      }));
    } catch {
      toast('mcp_servers JSON 解析失败，已按空列表处理', 'err');
      return [];
    }
  },

  renderEditor() {
    const el = this.root.querySelector('#mcp-editor');
    const rows = this.servers.length
      ? this.servers.map((s, i) => `
        <div class="item" data-mcp-row="${i}">
          <div class="grid grid-2" style="gap:10px">
            <div class="field"><label>名称</label>${ctl.text('m-name', s.name, '如：天气服务')}</div>
            <div class="field"><label>传输方式</label>${ctl.select('m-transport', s.transport, TRANSPORTS)}</div>
          </div>
          <div class="field"><label>URL</label>${ctl.text('m-url', s.url, 'https://example.com/mcp')}</div>
          <div class="field"><label>请求头（可选 JSON）</label>${ctl.text('m-headers', s.headers && Object.keys(s.headers).length ? JSON.stringify(s.headers) : '', '{"Authorization":"Bearer xxx"}')}</div>
          <div class="btn-row" style="justify-content:space-between">
            <label class="switch">${enabledCheckbox(s.enabled)}<span class="slider"></span></label>
            <button class="btn btn-xs btn-danger-soft" data-act="delRow" data-i="${i}">删除该服务器</button>
          </div>
        </div>`).join('')
      : emptyState({ icon: '🔌', msg: '还没有外部 MCP 服务器', hint: '点下方「+ 添加服务器」接入一个 MCP server' });

    el.innerHTML = `
      ${rows}
      <div class="toolbar mt16">
        <button class="btn btn-secondary" data-act="addRow">+ 添加服务器</button>
        <span class="spacer"></span>
        <button class="btn btn-secondary" data-act="listTools">🔍 列出工具</button>
        <button class="btn btn-primary" data-act="saveMcp">保存服务器列表</button>
      </div>`;
  },

  // 从当前 DOM 读回行数据（保留用户未保存的编辑）
  collectRows() {
    const out = [];
    this.root.querySelectorAll('[data-mcp-row]').forEach(row => {
      const hEl = row.querySelector('[data-key="m-headers"]');
      let hdr = {};
      if (hEl && hEl.value.trim()) {
        try { hdr = JSON.parse(hEl.value); } catch (e) { hdr = {}; }
        if (!hdr || typeof hdr !== 'object') hdr = {};
      }
      out.push({
        name: row.querySelector('[data-key="m-name"]').value.trim(),
        url: row.querySelector('[data-key="m-url"]').value.trim(),
        transport: row.querySelector('[data-key="m-transport"]').value,
        headers: hdr,
        enabled: row.querySelector('[data-key="m-enabled"]').checked,
      });
    });
    return out;
  },

  async saveServers(btn) {
    const arr = this.collectRows();
    const bad = arr.find(s => !s.url);
    if (bad) { toast('每个服务器都需要填写 URL', 'err'); return; }
    this.servers = arr;
    setBusy(btn, true, '保存中');
    try {
      await saveConfig('mcp_servers', JSON.stringify(arr));
      this.cfg.mcp_servers = JSON.stringify(arr);
      await post('/admin/mcp/clear-cache');
      toast('已保存并刷新外部抽屉');
    } catch (e) {
      toast('保存失败：' + e.message, 'err');
    } finally {
      setBusy(btn, false);
    }
  },

  async listTools(btn) {
    const servers = this.collectRows()
      .filter(s => s.url && s.enabled)
      .map(s => ({ url: s.url, transport: s.transport, name: s.name }));
    if (!servers.length) {
      toast('没有可探测的服务器（需填 URL 且已启用）', 'err');
      return;
    }
    setBusy(btn, true, '探测中');
    try {
      const d = await post('/admin/mcp/list-tools', { servers });
      this.showToolsModal(d);
    } catch (e) {
      toast('探测失败：' + e.message, 'err');
    } finally {
      setBusy(btn, false);
    }
  },

  async renderRegMcp(el) {
    let d = {};
    try { d = await get('/admin/cradle/api/registry'); } catch (e) {
      el.innerHTML = `<p class="muted text-sm">读取登记簿失败：${escHtml(e.message)}</p>`; return;
    }
    const arr = d.mcp || [];
    if (!arr.length) { el.innerHTML = `<p class="muted text-sm">还没登记端点</p>`; return; }
    el.innerHTML = arr.map(m => `
      <div class="item">
        <div class="item-row">
          <span class="item-title">${escHtml(m.name)}</span>
          <span class="badge badge-info">${escHtml(m.transport || 'http')}</span>
        </div>
        <div class="item-sub mono text-xs">${escHtml(m.url || '')}</div>
        <div class="item-sub">${escHtml(m.title || '')}${m.desc ? ' · ' + escHtml(m.desc) : ''}</div>
        <div class="row mt8"><button class="btn btn-secondary btn-sm" data-fill="${escHtml(m.url || '')}">填进上面的服务器列表</button></div>
      </div>`).join('');
    el.querySelectorAll('[data-fill]').forEach(b => {
      b.onclick = () => {
        const url = b.dataset.fill;
        const box = this.root.querySelector('[data-key="m-url"]');
        if (!box) { toast('找不到服务器地址输入框', 'err'); return; }
        box.value = url;
        toast('已填入地址，记得点保存', 'ok');
      };
    });
  },
showToolsModal(d) {
    const tools = d.tools || [];
    const map = d.tool_map || {};
    const body = tools.length
      ? tools.map(t => `
        <div class="item">
          <div class="item-title">${escHtml(t.name || '')} ${map[t.name] ? badge(map[t.name], 'info') : ''}</div>
          ${t.description ? `<div class="item-sub" style="white-space:pre-wrap">${escHtml(t.description)}</div>` : ''}
        </div>`).join('')
      : emptyState({ icon: '🛠️', msg: '没有探测到任何工具', hint: '检查 URL、传输方式与服务器连通性' });
    modal({
      title: `探测到 ${d.count ?? tools.length} 个工具`,
      wide: true,
      body,
      footer: `<button class="btn btn-secondary" data-close>关闭</button>`,
    });
  },
};

function enabledCheckbox(on) {
  return `<input type="checkbox" data-key="m-enabled" ${on ? 'checked' : ''}>`;
}
