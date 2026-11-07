// 🌙 梦境 — 从最近的卡里提炼的判断。不是事实，是推断。
import { get, post, escHtml, escAttr } from '../api.js';
import { badge, toast, confirmDialog, statCard } from '../ui.js';

const KIND_TABS = [
  ['all', '全部'],
  ['human', '人工梦'],
  ['machine', '机器梦'],
];
const ST_TABS = [
  ['', '全部'],
  ['draft', '待确认'],
  ['confirmed', '已收下'],
  ['rejected', '已否决'],
];

export default {
  title: '梦境',
  kind: 'all',
  status: '',
  items: [],
  stats: {},
  busy: false,
  panel: '',      // '', 'hand', 'merge'

  async mount(root) {
    this.root = root;
    await this.refresh();
    this.render();
  },

  async refresh() {
    const qs = ['limit=100'];
    if (this.kind !== 'all') qs.push('kind=' + encodeURIComponent(this.kind));
    if (this.status) qs.push('status=' + encodeURIComponent(this.status));
    try {
      const d = await get('/admin/dreams?' + qs.join('&'));
      this.items = d.items || [];
      this.stats = d;
    } catch (e) {
      this.items = [];
      this.stats = { error: e.message };
    }
  },

  chip(label, active, act, val) {
    return `<button class="btn ${active ? 'btn-primary' : 'btn-secondary'} btn-sm"
      data-act="${act}" data-val="${escAttr(val)}">${escHtml(label)}</button>`;
  },

  render() {
    const s = this.stats;
    const err = s.error ? `<div class="banner banner-warn"><span>⚠️</span><div>${escHtml(s.error)}</div></div>` : '';

    this.root.innerHTML = `
      <p class="page-intro">从最近的卡里提炼的判断 · 不是事实，是推断。每条都挂证据，可以核对，也可以否决。</p>
      ${err}

      <div class="card">
        <div class="card-head"><div class="card-title">🌙 当前状态</div></div>
        <div class="grid grid-4 mb16">
          ${statCard({ value: s.total, label: '总数', cls: '' }) + statCard({ value: s.draft, label: '待确认', cls: 'gold' }) + statCard({ value: s.confirmed, label: '已收下', cls: 'accent' }) + statCard({ value: s.rejected, label: '已否决', cls: '' }) + statCard({ value: s.human, label: '人工梦', cls: '' }) + statCard({ value: s.machine, label: '机器梦', cls: '' })}
        </div>
        <div class="item-sub" style="margin-top:10px">
          最后一条：${escHtml((s.last || '').replace('T', ' ').slice(0, 19) || '—')}
        </div>
      </div>

      <div class="card">
        <div class="card-head"><div class="card-title">做梦</div></div>
        <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
          <input class="input" id="d-q" placeholder="想问什么？（留空 = 盲抽，例如：他的网络是怎么变的）" style="flex:1;min-width:220px">
          <button class="btn btn-primary btn-sm" data-act="run">做梦</button>
          <button class="btn btn-secondary btn-sm" data-act="new">手写一条</button>
          <button class="btn btn-secondary btn-sm" data-act="merge">合并重复</button>
        </div>
        <div class="item-sub" style="margin-top:10px">
          一次生成 2 条，需要调一次大模型；结果进「待确认」，不会自动收下。
        </div>
        <div id="d-panel" style="margin-top:12px"></div>
      </div>

      <div class="card">
        <div class="card-head">
          <div class="card-title">梦的记录</div>
          <span class="badge badge-muted">共 ${this.items.length} 条</span>
        </div>
        <div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:8px">
          ${KIND_TABS.map(([v, t]) => this.chip(t, this.kind === v, 'kind', v)).join('')}
        </div>
        <div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:14px">
          ${ST_TABS.map(([v, t]) => this.chip(t, this.status === v, 'status', v)).join('')}
        </div>
        <div id="d-list">${this.renderList()}</div>
      </div>
    `;

    this.wire();
    if (this.panel) this.renderPanel();
  },

  renderList() {
    if (!this.items.length) {
      return `<div class="empty">这一档还没有梦。点上面「做梦」生成几条，或者「手写一条」。</div>`;
    }
    return this.items.map(d => {
      const st = d.status || 'draft';
      const stBadge = st === 'confirmed' ? 'badge-ok' : st === 'rejected' ? 'badge-muted' : 'badge-warn';
      const stText = st === 'confirmed' ? '已收下' : st === 'rejected' ? '已否决' : '待确认';
      const kindBadge = d.kind === 'machine' ? '机器梦' : (d.kind === 'hand' ? '手写' : '人工梦');
      const ev = (d.evidence || []).slice(0, 8).join(' ');
      return `
      <div class="card" style="margin-bottom:12px">
        <div class="item-title">${escHtml(d.title || '(无标题)')}</div>
        <div class="item-sub" style="white-space:pre-wrap;margin:8px 0 10px">${escHtml((d.body || '').slice(0, 420))}</div>
        <div class="row" style="gap:6px;flex-wrap:wrap;align-items:center">
          ${badge(kindBadge, 'badge-muted')}
          ${badge(stText, stBadge)}
          ${d.source_query ? badge('问：' + d.source_query.slice(0, 18), 'badge-info') : ''}
          ${ev ? badge('证据 ' + ev, 'badge-muted') : ''}
          ${d.confidence != null ? badge('可信度 ' + d.confidence, 'badge-muted') : ''}
          <span style="flex:1"></span>
          ${st !== 'confirmed' ? `<button class="btn btn-secondary btn-sm" data-act="set" data-id="${d.id}" data-val="confirmed">收下</button>` : ''}
          ${st !== 'rejected' ? `<button class="btn btn-secondary btn-sm" data-act="set" data-id="${d.id}" data-val="rejected">否决</button>` : ''}
          ${st !== 'draft' ? `<button class="btn btn-secondary btn-sm" data-act="set" data-id="${d.id}" data-val="draft">打回</button>` : ''}
        </div>
      </div>`;
    }).join('');
  },

  renderPanel() {
    const el = this.root.querySelector('#d-panel');
    if (!el) return;
    if (this.panel === 'hand') {
      el.innerHTML = `
        <div class="card" style="background:var(--surface-2)">
          <div class="card-title" style="margin-bottom:10px">新建手写梦</div>
          <input class="input" id="d-ht" placeholder="标题：他是 X 型，不是 Y 型" style="margin-bottom:8px">
          <textarea class="input" id="d-hb" rows="5" placeholder="正文。我印象里……&#10;&#10;> 这是我印象里的推断，不是事实。可信度 0.5，素材来自 YYYY-MM 至 YYYY-MM 的对话。"
            style="margin-bottom:8px;resize:vertical"></textarea>
          <input class="input" id="d-hs" placeholder="素材跨度（可选）：2026-06 → 2026-10" style="margin-bottom:10px">
          <button class="btn btn-primary btn-sm" data-act="hand-save">保存</button>
          <button class="btn btn-secondary btn-sm" data-act="panel-close">取消</button>
        </div>`;
    } else if (this.panel === 'merge') {
      el.innerHTML = `
        <div class="card" style="background:var(--surface-2)">
          <div class="card-title" style="margin-bottom:8px">合并重复的梦</div>
          <div class="item-sub" style="margin-bottom:10px">
            先按 embedding 找相似的，再让模型判断是不是同一件事。只有真正重复的才会被合并，碰巧主题相近的不会。
          </div>
          <button class="btn btn-secondary btn-sm" data-act="merge-dry">先看看有几对</button>
          <button class="btn btn-primary btn-sm" data-act="merge-apply">真的合并</button>
          <button class="btn btn-secondary btn-sm" data-act="panel-close">取消</button>
          <div id="d-mres" style="margin-top:10px"></div>
        </div>`;
    } else {
      el.innerHTML = '';
    }
  },

  wire() {
    this.root.addEventListener('click', async (e) => {
      const b = e.target.closest('[data-act]');
      if (!b) return;
      const act = b.dataset.act;
      if (act === 'kind') { this.kind = b.dataset.val; await this.reload(); return; }
      if (act === 'status') { this.status = b.dataset.val; await this.reload(); return; }
      if (act === 'panel-close') { this.panel = ''; this.renderPanel(); return; }
      if (act === 'new') { this.panel = this.panel === 'hand' ? '' : 'hand'; this.renderPanel(); return; }
      if (act === 'merge') { this.panel = this.panel === 'merge' ? '' : 'merge'; this.renderPanel(); return; }
      if (act === 'run') { await this.doRun(); return; }
      if (act === 'hand-save') { await this.doHand(); return; }
      if (act === 'merge-dry' || act === 'merge-apply') { await this.doMerge(act === 'merge-apply'); return; }
      if (act === 'set') { await this.setStatus(Number(b.dataset.id), b.dataset.val); return; }
    });
  },

  async reload() {
    await this.refresh();
    // 只重绘列表区，避免输入框被清空
    const el = this.root.querySelector('#d-list');
    if (el) el.innerHTML = this.renderList();
    const head = this.root.querySelectorAll('.card-title');
    // 更新筛选 chip 的选中态
    this.root.querySelectorAll('[data-act="kind"]').forEach(x =>
      x.className = 'btn btn-sm ' + (x.dataset.val === this.kind ? 'btn-primary' : 'btn-secondary'));
    this.root.querySelectorAll('[data-act="status"]').forEach(x =>
      x.className = 'btn btn-sm ' + (x.dataset.val === this.status ? 'btn-primary' : 'btn-secondary'));
  },

  async doRun() {
    const q = (this.root.querySelector('#d-q') || {}).value || '';
    if (this.busy) return;
    this.busy = true;
    toast(q ? '正在按问题做梦…' : '正在盲抽做梦…', 'info');
    try {
      const r = await post('/admin/dreams/run', { query: q.trim(), dry: false });
      if (r.ok) {
        const n = (r.dreams || []).length;
        toast(n ? `生成了 ${n} 条，进待确认` : '这次没生成（素材不够或压缩不足）', n ? 'ok' : 'warn');
        this.panel = '';
        await this.reload();
        this.render();
      } else {
        toast('失败：' + (r.error || '未知错误'), 'err');
      }
    } catch (e) {
      toast('失败：' + e.message, 'err');
    } finally {
      this.busy = false;
    }
  },

  async doHand() {
    const t = (this.root.querySelector('#d-ht') || {}).value || '';
    const b = (this.root.querySelector('#d-hb') || {}).value || '';
    const s = (this.root.querySelector('#d-hs') || {}).value || '';
    if (!t.trim() || !b.trim()) { toast('标题和正文都要填', 'warn'); return; }
    try {
      const r = await post('/admin/dreams/new', { title: t.trim(), body: b.trim(), span: s.trim() });
      if (r.ok) { toast('已保存并算作已收下', 'ok'); this.panel = ''; this.kind = 'all'; await this.refresh(); this.render(); }
      else toast('失败：' + (r.error || ''), 'err');
    } catch (e) { toast('失败：' + e.message, 'err'); }
  },

  async doMerge(apply) {
    const box = this.root.querySelector('#d-mres');
    if (apply && !(await confirmDialog('真的合并？合并后原来的两条会标记为已合并，原文还在。'))) return;
    if (box) box.innerHTML = '<div class="item-sub">分析中，可能要几十秒…</div>';
    try {
      const r = await post('/admin/dreams/merge', { apply: !!apply });
      if (!r.ok) { if (box) box.innerHTML = `<div class="item-sub">失败：${escHtml(r.error || '')}</div>`; return; }
      const pairs = r.pairs || r.merged || [];
      if (box) {
        box.innerHTML = pairs.length
          ? `<div class="item-sub">${apply ? '已合并' : '找到'} ${pairs.length} 对：</div>` +
            pairs.map(p => `<div class="item-sub" style="margin-top:6px">· ${escHtml((p.a_title || p.a || '').slice(0, 30))} ⇄ ${escHtml((p.b_title || p.b || '').slice(0, 30))}</div>`).join('')
          : '<div class="item-sub">没有发现重复的梦。</div>';
      }
      if (apply) { await this.reload(); }
    } catch (e) {
      if (box) box.innerHTML = `<div class="item-sub">失败：${escHtml(e.message)}</div>`;
    }
  },

  async setStatus(id, status) {
    try {
      const r = await post(`/admin/dreams/${id}/status`, { status });
      if (r.ok) { await this.reload(); }
      else toast('失败：' + (r.error || ''), 'err');
    } catch (e) { toast('失败：' + e.message, 'err'); }
  },
};
