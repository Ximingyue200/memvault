// 记忆碎片：只显示我们真有的字段 —— 时间、证据数、被召回次数。
// 点开任何一条，能看回原文（evidence 里的 quote）。
import { escHtml, escAttr, get, post } from '../api.js';

const KINDS = [
  ['', '全部类型'],
  ['definition', '定义'],
  ['fact', '事实'],
  ['event', '事件'],
  ['experience', '经验'],
  ['preference', '偏好'],
  ['tool', '工具'],
  ['note', '笔记'],
];

const SORTS = [
  ['recent', '按时间'],
  ['evidence', '按证据数'],
  ['recall', '按被召回'],
];

const KIND_CN = {
  definition: '定义', fact: '事实', event: '事件',
  experience: '经验', preference: '偏好', tool: '工具', note: '笔记',
};

export default {
  title: '记忆碎片',

  q: '', kind: '', category: '', sort: 'recent', page: 1, size: 20,
  data: null, cats: [], catOpen: false,

  async mount(root) {
    this.root = root;
    this.q = ''; this.kind = ''; this.sort = 'recent'; this.page = 1; this.softened = false;
    this.category = sessionStorage.getItem('prefilter_category') || '';
    sessionStorage.removeItem('prefilter_category');
    root.innerHTML = '<div class="item-sub">载入中…</div>';
    try {
      const c = await get('/admin/categories');
      this.cats = c.categories || [];
    } catch (e) {
      this.cats = [];
    }
    await this.refresh();
    // 支持 #/memories?open=123 直达某一条
    const m = (location.hash || '').match(/[?&]open=(\d+)/);
    if (m) setTimeout(() => this.open(Number(m[1])), 150);
  },

  async refresh() {
    const root = this.root;
    let d;
    try {
      d = await get(`/admin/memories?q=${encodeURIComponent(this.q)}&kind=${this.kind}&category=${encodeURIComponent(this.category)}&sort=${this.sort}&page=${this.page}&size=${this.size}&softened=${this.softened ? 1 : 0}`);
    } catch (e) {
      d = { error: String(e.message || e), memories: [], total: 0 };
    }
    this.data = d;
    this.render();
  },

  render() {
    const root = this.root;
    const d = this.data || {};
    const list = d.memories || [];
    const total = d.total || 0;
    const pages = Math.max(1, Math.ceil(total / this.size));

    root.innerHTML = `
      <h2 class="page-title">🧩 记忆碎片</h2>
      <p class="page-intro">每条都能点回原文。时间来自对话发生那天，证据数是这条卡挂了几个原文片段。</p>

      <div class="card">
        <div class="row" style="gap:8px;align-items:center">
          <input class="input" style="flex:1;min-width:140px" id="m-q" placeholder="搜标题或正文…" value="${escAttr(this.q)}">
          <button class="btn btn-primary" id="m-go">搜</button>
        </div>

        <div style="margin-top:12px">
          <div class="item-sub" style="font-size:11.5px;margin-bottom:6px">类型</div>
          <div class="row" style="gap:6px;flex-wrap:wrap">
            ${KINDS.map(k => `<button class="btn btn-xs ${k[0] === this.kind ? 'btn-primary' : 'btn-secondary'}" data-kind="${k[0]}">${k[1]}</button>`).join('')}
          </div>
        </div>

        <div style="margin-top:12px">
          <div class="item-sub" style="font-size:11.5px;margin-bottom:6px">排序</div>
          <div class="row" style="gap:6px;flex-wrap:wrap">
            ${SORTS.map(k => `<button class="btn btn-xs ${k[0] === this.sort ? 'btn-primary' : 'btn-secondary'}" data-sort="${k[0]}">${k[1]}</button>`).join('')}
            <button class="btn btn-xs ${this.softened ? 'btn-primary' : 'btn-secondary'}" id="m-soften-only" title="只看正文已压缩的卡（原文仍在 body_orig）">🌫️ 只看已软化</button>
          </div>
        </div>

        <div style="margin-top:12px">
          <div class="row" style="justify-content:space-between;align-items:center;margin-bottom:6px">
            <span class="item-sub" style="font-size:11.5px">分类${this.category ? ' · <b style="color:var(--c-accent)">' + escHtml(this.category) + '</b>' : ''}</span>
            <button class="btn btn-xs btn-secondary" id="m-cat-toggle">${this.catOpen ? '收起' : '选分类'}</button>
          </div>
          ${this.catOpen ? `<div class="row" style="gap:6px;flex-wrap:wrap">
            <button class="btn btn-xs ${!this.category ? 'btn-primary' : 'btn-secondary'}" data-cat="">全部分类</button>
            ${this.cats.map(c => `<button class="btn btn-xs ${c.name === this.category ? 'btn-primary' : 'btn-secondary'}" data-cat="${escAttr(c.name)}">${escHtml(c.name)} <span style="opacity:.55">${c.count}</span></button>`).join('')}
          </div>` : ''}
        </div>

        <div class="item-sub mt8">共 ${total.toLocaleString()} 条 · 第 ${this.page}/${pages} 页</div>
      </div>

      ${d.error ? `<div class="banner banner-warn">⚠️ ${escHtml(d.error)}</div>` : ''}

      <div class="mt16">
        ${list.map(m => `
          <div class="card" data-id="${m.id}" style="cursor:pointer;margin-bottom:10px">
            <div style="font-weight:600;font-size:14.5px">${escHtml(m.title || '(无标题)')}</div>
            <div class="item-sub" style="margin-top:6px;line-height:1.55">${escHtml((m.content || '').slice(0, 96))}${(m.content || '').length > 96 ? '…' : ''}</div>
            <div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap">
              <span class="badge badge-muted">${escHtml(KIND_CN[m.kind] || m.kind || '—')}</span>
              ${m.category ? `<span class="badge badge-muted">${escHtml(m.category)}</span>` : ''}
              <span class="badge badge-muted">📅 ${escHtml(m.when || '无日期')}</span>
              <span class="badge badge-muted"${m.evidence_n ? '' : 'opacity:.4'}">📎 ${m.evidence_n}</span>
              <span class="badge badge-muted"${m.recall_n ? '' : 'opacity:.4'}">🎯 ${m.recall_n}</span>
              ${m.softened_at ? `<span class="badge badge-warn" title="正文已压缩，原文在 body_orig（点开看）">🌫️ 已软化</span>` : ''}
            </div>
          </div>
        `).join('') || '<div class="item-sub">没有匹配的卡片</div>'}
      </div>

      <div class="row" style="gap:8px;justify-content:center;margin-top:16px">
        <button class="btn btn-secondary" id="m-prev" ${this.page <= 1 ? 'disabled' : ''}>上一页</button>
        <button class="btn btn-secondary" id="m-next" ${this.page >= pages ? 'disabled' : ''}>下一页</button>
      </div>
    `;

    const self = this;
    root.querySelector('#m-go').onclick = () => self.apply();
    root.querySelector('#m-q').onkeydown = (e) => { if (e.key === 'Enter') self.apply(); };
    root.querySelectorAll('[data-kind]').forEach(b => {
      b.onclick = () => { self.q = root.querySelector('#m-q').value.trim(); self.kind = b.dataset.kind; self.page = 1; self.refresh(); };
    });
    root.querySelector('#m-soften-only')?.addEventListener('click', () => {
      this.softened = !this.softened; this.page = 1; this.refresh();
    });
    root.querySelectorAll('[data-sort]').forEach(b => {
      b.onclick = () => { self.sort = b.dataset.sort; self.page = 1; self.refresh(); };
    });
    root.querySelector('#m-cat-toggle').onclick = () => { self.catOpen = !self.catOpen; self.render(); };
    root.querySelectorAll('[data-cat]').forEach(b => {
      b.onclick = () => { self.category = b.dataset.cat; self.page = 1; self.catOpen = false; self.refresh(); };
    });
    root.querySelector('#m-prev').onclick = () => { self.page -= 1; self.refresh(); };
    root.querySelector('#m-next').onclick = () => { self.page += 1; self.refresh(); };
    root.querySelectorAll('[data-id]').forEach(el => {
      el.onclick = () => self.open(el.dataset.id);
    });
  },

  apply() {
    const root = this.root;
    this.q = (root.querySelector('#m-q').value || '').trim();
    this.page = 1;
    this.refresh();
  },

  async open(id) {
    let d;
    try {
      d = await get(`/admin/memories/${id}`);
    } catch (e) {
      alert('读取失败：' + (e.message || e));
      return;
    }
    const c = d.card || {};
    const evs = d.evidence || [];
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;inset:0;background:rgba(28,32,24,.42);backdrop-filter:blur(2px);z-index:999;display:flex;align-items:flex-end;justify-content:center';
    box.innerHTML = `
      <div style="background:var(--surface,#fff);color:var(--text,#20261a);width:100%;max-width:760px;max-height:88vh;overflow:auto;border-radius:18px 18px 0 0;padding:20px;box-shadow:0 -8px 32px rgba(28,32,24,.18)">
        <div class="row" style="justify-content:space-between;align-items:flex-start;gap:10px">
          <div style="flex:1;min-width:0">
            <div style="font-weight:700;font-size:16.5px;color:var(--text,#20261a);letter-spacing:.2px">${escHtml(c.title || '(无标题)')}</div>
            <div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap">
              <span class="badge badge-muted">${escHtml(KIND_CN[c.kind] || c.kind || '—')}</span>
              <select class="input" id="dt-cat" style="width:auto;font-size:12px;padding:2px 8px;height:28px">
                <option value="">（未分类）</option>
                ${(d.all_categories || []).map(x => `<option value="${escAttr(x)}" ${x === c.category ? 'selected' : ''}>${escHtml(x)}</option>`).join('')}
              </select>
              <button class="btn btn-xs btn-secondary" id="dt-save-cat">改分类</button>
              ${c.when ? `<span class="badge badge-muted">📅 ${escHtml(c.when)}</span>` : ''}
              ${c.owner ? `<span class="badge badge-muted" title="谁说的">${escHtml(c.owner)}</span>` : ''}
              ${c.pinned ? `<span class="badge badge-gold" title="置顶：半衰期 30 天">📌 置顶</span>` : ''}
              ${(c.heat !== null && c.heat !== undefined) ? `<span class="badge badge-muted" title="当前温度">🌡️ ${c.heat}</span>` : ''}
            </div>
          </div>
          <button class="btn btn-secondary" id="dt-close" style="flex:none">关闭</button>
        </div>
        ${c.softened_at ? `
          <div style="margin-top:12px;padding:9px 12px;background:var(--surface-2,#fbfbf7);border-left:3px solid #c9a227;border-radius:8px;font-size:12px;color:var(--text-soft,#565e4b);line-height:1.6">
            🌫️ <b>已软化</b>（${escHtml(c.softened_at)}）— 细节褪色、要点保留。原文与证据链完好，见下方。
          </div>` : ''}
        <div style="margin-top:16px;line-height:1.75;white-space:pre-wrap;font-size:14.5px;color:var(--text,#20261a)">${escHtml(c.body || c.content || '')}</div>
        ${c.body_orig ? `
          <details style="margin-top:12px">
            <summary style="cursor:pointer;font-size:12.5px;color:var(--text-soft,#565e4b);user-select:none">📄 软化前原文（${c.body_orig.length} 字）</summary>
            <div style="margin-top:8px;padding:12px;background:var(--surface-2,#fbfbf7);border:1px solid var(--border,#e6e5da);border-radius:10px;font-size:13px;line-height:1.75;white-space:pre-wrap;color:var(--text-soft,#565e4b)">${escHtml(c.body_orig)}</div>
          </details>` : ''}
        <div style="margin-top:20px;border-top:1px solid var(--border,#e6e5da);padding-top:14px">
          <div style="font-weight:600;font-size:13.5px;color:var(--text-soft,#565e4b)">📎 原文证据（${evs.length} 条）</div>
          ${evs.length ? evs.map((e, i) => `
            <div style="margin-top:10px;padding:12px;background:var(--surface-2,#fbfbf7);border:1px solid var(--border,#e6e5da);border-radius:12px">
              <div style="font-size:11.5px;color:var(--text-muted,#7d8570);letter-spacing:.2px">${i + 1}. ${escHtml(e.role || '?')} · ${escHtml(e.ts || '无时间')}</div>
              <div style="margin-top:7px;font-size:13.5px;line-height:1.7;white-space:pre-wrap;color:var(--text,#20261a)">${escHtml((e.quote || '').slice(0, 500))}</div>
            </div>
          `).join('') : '<div style="margin-top:10px;color:var(--text-muted,#7d8570);font-size:12.5px">这张卡没有挂原文（旧卡）</div>'}
        </div>
      </div>
    `;
    box.onclick = (e) => { if (e.target === box) box.remove(); };
    box.querySelector('#dt-close').onclick = () => box.remove();
    box.querySelector('#dt-save-cat').onclick = async () => {
      const sel = box.querySelector('#dt-cat');
      const val = sel.value;
      try {
        await post(`/admin/memories/${c.id}/category`, { category: val });
        box.remove();
        await this.refresh();
      } catch (e) {
        alert('改分类失败：' + (e.message || e));
      }
    };
    document.body.appendChild(box);
  },
};
