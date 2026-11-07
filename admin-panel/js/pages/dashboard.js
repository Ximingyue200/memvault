// 📊 仪表盘 — 一屏看完记忆库的当前状态
import { get, escHtml } from '../api.js';
import { statCard, card, badge, loadingBlock } from '../ui.js';

export default {
  title: '仪表盘',
  d: null,
  err: '',

  async mount(root) {
    this.root = root;
    root.innerHTML = loadingBlock();
    await this.load();
  },

  async load() {
    try {
      this.d = await get('/admin/dashboard');
      this.err = this.d && this.d.ok === false ? (this.d.err || '接口返回失败') : '';
    } catch (e) {
      this.d = null;
      this.err = e.message;
    }
    this.render();
  },

  render() {
    const root = this.root;
    if (this.err) {
      root.innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>读取失败：${escHtml(this.err)}</div></div>`;
      return;
    }
    const d = this.d || {};
    const sw = d.sw || {};
    const on = v => v ? badge('开启', 'accent') : badge('关闭', 'muted');

    root.innerHTML = `
      <div class="banner banner-info"><span>📊</span><div>
        这是记忆库的当前状态。<b>卡片</b>是内容单位，<b>热度</b>是「被用到过几次」的短期记忆指标（半衰期 ${escHtml(String(d.half_life || '7'))} 天）。
      </div></div>

      <div class="grid grid-4 mb16">
        ${statCard({ label: '🧩 记忆卡片', value: (d.active || 0).toLocaleString(), cls: 'accent', sub: `共 ${(d.cards || 0).toLocaleString()} 张 · ${d.softened || 0} 张已软化` })}
        ${statCard({ label: '🔥 平均热度', value: (d.avg_heat || 0).toFixed(3), cls: 'gold', sub: `热 ${d.hot || 0} · 冷 ${d.cold || 0} · 命中过 ${d.hit_once || 0}` })}
        ${statCard({ label: '🌙 梦', value: d.dreams || 0, cls: 'purple', sub: `待确认 ${d.dreams_draft || 0}` })}
        ${statCard({ label: '📎 证据链', value: (d.evidence || 0).toLocaleString(), cls: 'info', sub: `原文 ${(d.spans || 0).toLocaleString()} 段` })}
      </div>

      <div class="grid grid-4 mb16">
        ${statCard({ label: '📌 置顶', value: d.pinned || 0, sub: '半衰期 30 天' })}
        ${statCard({ label: '🧵 线索', value: d.threads || 0, sub: '跨会话的事' })}
        ${statCard({ label: '🔧 程序', value: d.procedures || 0, sub: '做事套路' })}
        ${statCard({ label: '📚 编年史', value: d.chronicles || 0, sub: `摘要 ${d.summaries || 0}` })}
      </div>

      <div class="grid grid-2 mb16">
        ${card({
          title: '🔀 开关',
          body: `
            <div class="kv"><span class="k">自动注入</span><span class="v">${on(sw.inject)}</span></div>
            <div class="kv"><span class="k">温度衰减</span><span class="v">${on(sw.heat)}</span></div>
            <div class="kv"><span class="k">自动软化</span><span class="v">${on(sw.soften)}</span></div>
            <div class="kv"><span class="k">定时做梦</span><span class="v">${on(sw.dream)}</span></div>
            <p class="muted" style="margin-top:10px;font-size:12px">在「记忆库参数」页改。</p>
          `
        })}
        ${card({
          title: '🌙 最近的梦',
          actions: `<a class="btn btn-sm btn-secondary" href="#/dream">前往 →</a>`,
          body: `
            <div class="kv"><span class="k">总数</span><span class="v">${d.dreams || 0}</span></div>
            <div class="kv"><span class="k">待确认</span><span class="v">${badge(String(d.dreams_draft || 0), 'warn')}</span></div>
            <div class="kv"><span class="k">已收下</span><span class="v">${d.dreams_ok || 0}</span></div>
            <div class="kv"><span class="k">最后一条</span><span class="v">${escHtml(d.dreams_last || '从未')}</span></div>
          `
        })}
      </div>

      ${card({
        title: '🆕 最近添加',
        actions: `<a class="btn btn-sm btn-secondary" href="#/memories">全部 →</a>`,
        body: (d.recent || []).length
          ? (d.recent || []).map(m => `
            <a class="kv" href="#/memories" style="text-decoration:none;color:inherit;display:flex;gap:8px;align-items:baseline;padding:8px 0;border-bottom:1px solid var(--border)">
              <span class="k" style="min-width:0;flex:1">
                <b style="font-size:13.5px">${escHtml(m.title || '(无标题)')}</b>
                <span class="muted" style="display:block;font-size:12px;margin-top:2px">${escHtml((m.abstract || '').slice(0, 70))}</span>
              </span>
              <span class="v" style="white-space:nowrap">
                ${m.softened ? badge('🌫️', 'muted') : ''}
                ${badge((m.heat || 0).toFixed(2), m.heat >= 0.6 ? 'gold' : 'muted')}
              </span>
            </a>`).join('')
          : '<p class="muted">还没有卡片</p>'
      })}

      ${card({
        title: '🔍 注入试算',
        actions: '<span class="muted" style="font-size:12px">输入一句话，看现在说这句会塞进什么</span>',
        body: `
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <input id="pv-q" type="text" placeholder="例如：某个项目 / 端口 8006 / 某个型号" style="flex:1;min-width:200px">
            <button class="btn btn-primary" id="pv-go">试算</button>
          </div>
          <div id="pv-out" style="margin-top:12px"></div>
        `
      })}

      ${card({
        title: '📦 数据导入 / 导出',
        actions: '<span class="muted" style="font-size:12px">原始文本，人能看懂，也能原样灌回去</span>',
        body: `
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button class="btn btn-primary" id="io-export">⬇️ 导出全部会话</button>
            <button class="btn" id="io-export100">导出最近 100 个会话</button>
            <span id="io-msg" class="muted" style="font-size:12px"></span>
          </div>
          <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <input id="io-file" type="file" accept=".txt,text/plain" style="flex:1;min-width:220px">
              <button class="btn" id="io-import">⬆️ 导入</button>
            </div>
            <div id="io-out" style="margin-top:10px;font-size:12px"></div>
          </div>
        `
      })}
    `;

    const go = root.querySelector('#pv-go');
    const inp = root.querySelector('#pv-q');
    const out = root.querySelector('#pv-out');
    const run = async () => {
      const q = (inp.value || '').trim();
      out.innerHTML = '<p class="muted">计算中…</p>';
      try {
        const d = await get('/admin/preview-inject?q=' + encodeURIComponent(q));
        const rows = (d.parts || []).map(p => {
          const bar = p.chars > 0
            ? badge(p.chars.toLocaleString() + ' 字', p.chars > 2000 ? 'gold' : 'muted')
            : badge('0 字', 'muted');
          const body = p.chars > 0
            ? '<details style="margin-top:6px"><summary class="muted" style="cursor:pointer;font-size:12px">展开原文</summary>'
              + '<pre style="white-space:pre-wrap;word-break:break-all;font-size:12px;line-height:1.6;background:var(--bg-soft,#f7f7f5);padding:10px;border-radius:8px;margin-top:6px;max-height:420px;overflow:auto">'
              + escHtml(p.text) + '</pre></details>'
            : '';
          return '<div style="padding:10px 0;border-bottom:1px solid var(--border)">'
            + '<div style="display:flex;gap:8px;align-items:baseline"><b style="flex:1;font-size:13px">' + escHtml(p.name) + '</b>' + bar + '</div>'
            + (p.note ? '<div class="muted" style="font-size:12px;margin-top:3px">' + escHtml(p.note) + '</div>' : '')
            + body + '</div>';
        }).join('');
        out.innerHTML = '<div style="display:flex;gap:10px;align-items:baseline;padding:8px 0 12px">'
          + '<b style="font-size:15px">合计 ' + (d.total || 0).toLocaleString() + ' 字</b>'
          + badge('≈ ' + (d.token_est || 0).toLocaleString() + ' token', 'info')
          + '<span class="muted" style="font-size:12px">' + escHtml(d.note || '') + '</span></div>'
          + (rows || '<p class="muted">没有内容</p>');
      } catch (e) {
        out.innerHTML = '<p class="muted">失败：' + escHtml(e.message) + '</p>';
      }
    };

    const msg = root.querySelector('#io-msg');
    const outEl = root.querySelector('#io-out');
    const dl = (url) => {
      if (msg) msg.textContent = '生成中…';
      const a = document.createElement('a');
      a.href = url;
      a.download = '';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => { if (msg) msg.textContent = '已开始下载（全量约 70MB）'; }, 800);
    };
    const be = root.querySelector('#io-export');
    const be100 = root.querySelector('#io-export100');
    const bi = root.querySelector('#io-import');
    const fi = root.querySelector('#io-file');
    if (be) be.onclick = () => dl('/export');
    if (be100) be.onclick = () => dl('/export?convs=100');
    if (be100) be100.onclick = () => dl('/export?convs=100');
    if (bi) bi.onclick = async () => {
      const f = fi && fi.files && fi.files[0];
      if (!f) { if (outEl) outEl.innerHTML = '<span style="color:#c33">先选一个 .txt 文件</span>'; return; }
      if (outEl) outEl.innerHTML = '<span class="muted">上传中…（' + Math.round(f.size / 1024) + ' KB）</span>';
      try {
        const buf = await f.arrayBuffer();
        const r = await fetch('/import', {
          method: 'POST',
          headers: { 'Content-Type': 'text/plain; charset=utf-8' },
          body: buf
        });
        const j = await r.json();
        if (outEl) outEl.innerHTML = '<b>导入完成</b>：会话 ' + (j.convs || 0)
          + ' / 消息 ' + (j.msgs || 0)
          + ' / 跳过重复 ' + (j.skipped_dup || 0)
          + ' / 跳过噪声 ' + (j.skipped_noise || 0);
      } catch (e) {
        if (outEl) outEl.innerHTML = '<span style="color:#c33">失败：' + escHtml(e.message) + '</span>';
      }
    };
    if (go) go.onclick = run;
    if (inp) inp.onkeydown = (e) => { if (e.key === 'Enter') run(); };
  }
};
