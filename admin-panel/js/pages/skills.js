// 🎯 技能 — 手动登记的技能清单（名字 + 一句说明 + 开关）
import { get, post, escHtml } from '../api.js';
import { toast, emptyState, delegate } from '../ui.js';

export default {
  title: '技能',
  skills: [],

  async mount(root) {
    this.root = root;
    root.innerHTML = `
      <p class="page-intro">登记你手上的技能：名字、一句话说清「什么时候该用」，以及开关。启用的条目会随记忆一起喂给模型。</p>
      <div class="banner banner-info"><span>💡</span><div>
        <b>技能是什么：</b>一段可复用的操作说明，比如「部署 PVE 前先 <code>pct config</code> 看 <code>nesting</code>」。
        它不像记忆卡那样要求原文出处，是你直接告诉模型「我有这招」。
      </div></div>
      <div class="section-title mt24">🎯 技能清单</div>
      <div id="skill-list"></div>
      <div class="row mt16" style="gap:8px">
        <input id="sk-name" class="input" placeholder="技能名，如 pve-deploy" style="flex:1">
        <input id="sk-desc" class="input" placeholder="一句话说明：什么时候用" style="flex:2">
        <button class="btn btn-primary" data-add>添加</button>
      </div>
    `;
    await this.load();
    const regHost = document.createElement('div');
    regHost.innerHTML = `<div class="section-title mt24">📦 文件技能（登记簿）</div>
      <p class="card-desc mb12 faint text-xs">想改名/改说明/增删？去侧栏「能力登记簿」，那里能直接编辑。</p>
      <p class="card-desc mb12">这些是真实躺在机器上的 <code>SKILL.md</code>：只登记、不预装，谁要用谁按名字拉一份。</p>
      <div id="reg-list" class="card"><p class="muted text-sm">加载中…</p></div>`;
    root.appendChild(regHost);
    this.renderRegistry(regHost.querySelector('#reg-list'));
    delegate(root, { add: () => this.add() });
  },

  async renderRegistry(el) {
    let d = {};
    try { d = await get('/admin/cradle/api/registry'); } catch (e) {
      el.innerHTML = `<p class="muted text-sm">读取登记簿失败：${escHtml(e.message)}</p>`; return;
    }
    const arr = d.skills || [];
    if (!arr.length) { el.innerHTML = `<p class="muted text-sm">登记簿是空的</p>`; return; }
    el.innerHTML = arr.map(s => {
      const off = s.enabled === false;
      return `<div class="item">
        <div class="item-row">
          <span class="item-title">${escHtml(s.name)}</span>
          <span class="badge ${off ? 'badge-muted' : 'badge-info'}">${off ? '停用' : '可用'}</span>
        </div>
        <div class="item-sub">${escHtml(s.title || '')}${s.desc ? ' · ' + escHtml(s.desc) : ''}</div>
        <div class="item-sub faint text-xs mono">${escHtml(s.source || '')} · 拉取：mvreg pull ${escHtml(s.name)}</div>
      </div>`;
    }).join('') + `<p class="faint text-xs mt8">清单更新于 ${escHtml(d.updated || '—')}</p>`;
  },
async load() {
    try {
      const d = await get('/admin/skills');
      this.skills = d.skills || [];
    } catch (e) {
      this.skills = [];
    }
    this.render();
  },

  render() {
    const el = this.root.querySelector('#skill-list');
    if (!el) return;
    if (!this.skills.length) {
      el.innerHTML = emptyState({ icon: '🎯', msg: '还没有技能', hint: '在下面填名字和一句说明，点「添加」' });
      return;
    }
    el.innerHTML = this.skills.map((s, i) => {
      const off = s.enabled === false;
      return `
      <div class="item" data-row data-i="${i}">
        <div class="item-title">${escHtml(s.name || '')}${off ? ' <span class="badge">已停用</span>' : ''}</div>
        <div class="item-sub">${escHtml(s.desc || '')}</div>
        <div class="row mt8" style="gap:8px">
          <button class="btn btn-secondary btn-sm" data-toggle="${i}">${off ? '启用' : '停用'}</button>
          <button class="btn btn-secondary btn-sm" data-del="${i}">删除</button>
        </div>
      </div>`;
    }).join('');
    el.querySelectorAll('[data-toggle]').forEach(b => { b.onclick = () => this.toggle(+b.dataset.toggle); });
    el.querySelectorAll('[data-del]').forEach(b => { b.onclick = () => this.del(+b.dataset.del); });
  },

  async add() {
    const n = this.root.querySelector('#sk-name').value.trim();
    const d = this.root.querySelector('#sk-desc').value.trim();
    if (!n) { toast('名字不能空', 'err'); return; }
    if (this.skills.some(s => s.name === n)) { toast('这个名字已经有了', 'err'); return; }
    this.skills.push({ name: n, desc: d, enabled: true, at: new Date().toISOString().slice(0, 10) });
    this.root.querySelector('#sk-name').value = '';
    this.root.querySelector('#sk-desc').value = '';
    await this.save();
  },

  async toggle(i) {
    this.skills[i].enabled = this.skills[i].enabled === false;
    await this.save();
  },

  async del(i) {
    this.skills.splice(i, 1);
    await this.save();
  },

  async save() {
    try {
      await post('/admin/skills', { skills: this.skills });
      this.render();
      toast('已保存');
    } catch (e) {
      toast('保存失败：' + e.message, 'err');
    }
  },
};
