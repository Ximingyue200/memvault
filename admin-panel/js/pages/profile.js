// 👤 用户画像 — 四板块（基本档案/沟通洞察/近期重点/长期偏好）+ 盲点，可编辑、可重算
import { get, post, escHtml } from '../api.js';
import { toast, emptyState } from '../ui.js';

const BLOCKS = [
  ['basic',    '📌 基本档案', '稳定事实：身份、健康、居住、家庭'],
  ['insights', '🔍 沟通洞察', '怎么跟他说话最好：语气、决策风格、敏感点'],
  ['recent',   '🔥 近期重点', '最近在做什么。会变的，过时的应删'],
  ['values',   '💡 长期偏好', '审美、世界观、对技术·工具·钱的态度'],
];

export default {
  title: '用户画像',
  data: null,

  async mount(root) {
    this.root = root;
    root.innerHTML = `<div id="pf-box">正在加载…</div>`;
    await this.load();
  },

  async load() {
    const box = this.root.querySelector('#pf-box');
    box.textContent = '正在加载…';
    try {
      this.data = await get('/admin/profile-full');
      this.render();
    } catch (e) {
      box.innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>读取失败：${escHtml(e.message)}</div></div>`;
    }
  },

  render() {
    const box = this.root.querySelector('#pf-box');
    const d = this.data || {};
    const note = d.note || {};
    const noteAt = d.note_at || '';
    const agg = d.agg || {};
    const st = agg.stats || {};
    const blocks = note.blocks || {};
    const blind = note.blind || [];
    const total = BLOCKS.reduce((n, [k]) => n + ((blocks[k] || []).length), 0);

    const statCards = [
      ['记忆卡', st.cards], ['梦', st.dreams], ['套路', st.procedures],
      ['画像条目', total], ['未完成', st.tasks_open], ['已完成', st.tasks_done],
    ].filter(x => x[1] != null).map(([k, v]) =>
      `<div class="card" style="padding:12px"><div class="item-sub">${k}</div><div style="font-size:24px;font-weight:600">${v}</div></div>`
    ).join('');

    const blockHtml = BLOCKS.map(([k, title, hint]) => {
      const arr = blocks[k] || [];
      if (!arr.length) return '';
      return `
      <div class="card mt16">
        <div class="row" style="justify-content:space-between;align-items:baseline">
          <div class="section-title" style="margin:0">${title}</div>
          <div class="item-sub">${arr.length} 条</div>
        </div>
        <div class="item-sub" style="margin:2px 0 8px">${hint}</div>
        ${arr.map(x => `<div class="item" style="padding:6px 0"><div class="item-sub">· ${escHtml(x)}</div></div>`).join('')}
      </div>`;
    }).join('');

    const dreams = (agg.dreams || []).slice(0, 6).map(x =>
      `<div class="item"><div class="item-title">${escHtml(x.title || '')}</div>
       <div class="item-sub">${escHtml((x.span || '') + ' · ' + (x.kind === 'machine' ? '机器梦' : '人工梦'))}</div></div>`
    ).join('');

    const focusRows = (agg.focus || []).slice(0, 8).map(f => `
      <div class="item">
        <div class="item-title">${escHtml(f.topic || '')}</div>
        <div class="item-sub">${escHtml((f.open_items || '').slice(0, 100))}</div>
      </div>`).join('');

    box.innerHTML = `
      <p class="page-intro">AI 对你的长期认知，四个板块。可手动改，也可让模型按最近的记忆重写。</p>

      <div class="row" style="gap:8px;flex-wrap:wrap;margin-bottom:12px">
        <button class="btn btn-primary" data-rebuild>🔄 重新整理</button>
        <button class="btn btn-secondary" data-edit>✏️ 手动编辑</button>
        <span class="item-sub" id="pf-msg"></span>
      </div>

      ${note.summary ? `
      <div class="card" style="border-left:4px solid #b48ef0">
        <div class="section-title">🪞 一段话</div>
        <div style="font-size:16px;line-height:1.7;margin-top:8px">${escHtml(note.summary)}</div>
        ${noteAt ? `<div class="item-sub mt8">更新于 ${escHtml(noteAt)}</div>` : ''}
      </div>` : `
      <div class="banner banner-warn"><span>⚠️</span><div>还没有画像。点「重新整理」让模型写一份。</div></div>`}

      ${blockHtml}

      ${blind.length ? `
      <div class="card mt16" style="border-left:4px solid #ffc857">
        <div class="section-title">🕳️ 盲点</div>
        ${blind.map(b => `<div class="item"><div class="item-sub">${escHtml(b)}</div></div>`).join('')}
      </div>` : ''}

      <div class="section-title mt24">📊 数据规模</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px">${statCards}</div>

      ${dreams ? `<div class="card mt16"><div class="section-title">🌙 关于你的判断（梦）</div>${dreams}</div>` : ''}
      ${focusRows ? `<div class="card mt16"><div class="section-title">📋 正在做什么</div>${focusRows}</div>` : ''}
    `;
    box.querySelector('[data-rebuild]').onclick = (e) => this.rebuild(e.currentTarget);
    box.querySelector('[data-edit]').onclick = () => this.renderEdit();
  },

  renderEdit() {
    const box = this.root.querySelector('#pf-box');
    const note = (this.data && this.data.note) || {};
    const blocks = note.blocks || {};
    const blind = (note.blind || []).slice(0, 4);
    while (blind.length < 2) blind.push('');

    box.innerHTML = `
      <div class="card">
        <div class="section-title">✏️ 手动编辑</div>
        <div class="item-sub" style="margin-bottom:8px">每个板块一行一条，空行会被忽略。</div>

        <label class="lbl mt8">一段话（可留空）</label>
        <textarea class="mono" id="pf-sum" rows="2" spellcheck="false">${escHtml(note.summary || '')}</textarea>

        ${BLOCKS.map(([k, title, hint]) => `
          <label class="lbl mt16">${title}</label>
          <div class="item-sub" style="margin-bottom:4px">${hint}</div>
          <textarea class="mono" id="pf-${k}" rows="8" spellcheck="false" placeholder="一行一条">${escHtml((blocks[k] || []).join('\n'))}</textarea>
        `).join('')}

        <label class="lbl mt16">盲点</label>
        <div id="pf-blind">
          ${blind.map((b, i) => `
            <textarea class="mono mt8" id="pf-b${i}" rows="2" spellcheck="false" placeholder="他可能没意识到的">${escHtml(b || '')}</textarea>`).join('')}
        </div>

        <div class="row mt16" style="gap:8px">
          <button class="btn btn-primary" data-save>保存</button>
          <button class="btn btn-secondary" data-cancel>取消</button>
        </div>
      </div>
    `;
    box.querySelector('[data-save]').onclick = () => this.saveEdit();
    box.querySelector('[data-cancel]').onclick = () => this.render();
  },

  async saveEdit() {
    const box = this.root.querySelector('#pf-box');
    const lines = (id) => (box.querySelector('#' + id)?.value || '')
      .split('\n').map(x => x.trim()).filter(Boolean);

    const note = {
      summary: (box.querySelector('#pf-sum')?.value || '').trim(),
      blocks: {},
      blind: [],
    };
    for (const [k] of BLOCKS) note.blocks[k] = lines('pf-' + k);
    for (let i = 0; i < 4; i++) {
      const b = (box.querySelector('#pf-b' + i)?.value || '').trim();
      if (b) note.blind.push(b);
    }
    const total = Object.values(note.blocks).reduce((n, a) => n + a.length, 0);
    if (!note.summary && !total) { toast('画像不能全空', 'err'); return; }
    try {
      await post('/admin/profile-note', note);
      toast('已保存');
      await this.load();
    } catch (e) {
      toast('保存失败：' + e.message, 'err');
    }
  },

  async rebuild(btn) {
    const msg = this.root.querySelector('#pf-msg');
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = '整理中…';
    if (msg) msg.textContent = '读记忆、调模型，大概十几秒';
    try {
      const r = await post('/admin/profile-rebuild', {});
      if (r.ok) {
        toast('已重新整理');
        await this.load();
      } else {
        toast('失败：' + (r.error || '未知'), 'err');
        if (msg) msg.textContent = r.error || '';
      }
    } catch (e) {
      toast('失败：' + e.message, 'err');
      if (msg) msg.textContent = e.message;
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  },
};
