// 📝 人设 — 多套卡片，点一下就切换
import { get, post, escHtml } from '../api.js';
import { toast, emptyState } from '../ui.js';

const COLORS = ['#5aa9ff', '#5fd48a', '#ffc857', '#ff8fab', '#b48ef0', '#4fd1c5'];

export default {
  title: '人设',
  personas: [],
  active: '',
  editing: null,

  async mount(root) {
    this.root = root;
    root.innerHTML = `
      <p class="page-intro">一张卡片就是一套人设。点卡片切换，被选中的那套会放在 system prompt 最前面。</p>
      <div class="banner banner-info"><span>💡</span><div>
        <b>怎么生效：</b>人设拼在 system prompt 的开头，记忆卡和技能块跟在后面。
        没有选任何一套时，网关用默认身份句（防止模型把记忆卡当成自我描述）。
      </div></div>
      <div class="section-title mt24">📝 人设卡片</div>
      <div id="persona-grid" class="grid2"></div>
      <div class="mt16"><button class="btn btn-primary" data-new>+ 新建人设</button></div>
      <div id="persona-edit"></div>
    `;
    await this.load();
    root.querySelector('[data-new]').onclick = () => this.edit(null);
  },

  async load() {
    try {
      const d = await get('/admin/personas');
      this.personas = d.personas || [];
      this.active = d.active || '';
    } catch (e) {
      this.personas = [];
      this.active = '';
    }
    this.render();
  },

  render() {
    const el = this.root.querySelector('#persona-grid');
    if (!el) return;
    if (!this.personas.length) {
      el.innerHTML = emptyState({ icon: '📝', msg: '还没有人设', hint: '点下面「新建人设」，写一句「你是…」' });
      return;
    }
    el.innerHTML = this.personas.map((p, i) => {
      const on = p.id === this.active;
      const c = p.color || COLORS[i % COLORS.length];
      return `
      <div class="card" style="border-left:4px solid ${c};${on ? 'outline:2px solid ' + c : ''}">
        <div class="row" style="justify-content:space-between;align-items:center">
          <div>
            <b>${escHtml(p.name || '未命名')}</b>
            ${on ? '<span class="badge" style="margin-left:6px">当前</span>' : ''}
          </div>
          <div class="row" style="gap:6px">
            ${on ? '' : `<button class="btn btn-secondary btn-sm" data-use="${i}">用这个</button>`}
            <button class="btn btn-secondary btn-sm" data-edit="${i}">编辑</button>
            <button class="btn btn-secondary btn-sm" data-del="${i}">删除</button>
          </div>
        </div>
        <div class="item-sub mt8">${escHtml(p.desc || '')}</div>
        <div class="item-sub mt8" style="opacity:.6;white-space:pre-wrap">${escHtml((p.prompt || '').slice(0, 120))}${(p.prompt || '').length > 120 ? '…' : ''}</div>
      </div>`;
    }).join('');
    el.querySelectorAll('[data-use]').forEach(b => { b.onclick = () => this.use(+b.dataset.use); });
    el.querySelectorAll('[data-edit]').forEach(b => { b.onclick = () => this.edit(+b.dataset.edit); });
    el.querySelectorAll('[data-del]').forEach(b => { b.onclick = () => this.del(+b.dataset.del); });
  },

  edit(i) {
    const p = i === null ? { id: 'p' + Date.now().toString(36), name: '', desc: '', prompt: '', color: COLORS[this.personas.length % COLORS.length] } : this.personas[i];
    this.editing = { i, p: { ...p } };
    const box = this.root.querySelector('#persona-edit');
    box.innerHTML = `
      <div class="card mt16">
        <div class="section-title">${i === null ? '新建人设' : '编辑人设'}</div>
        <div class="mt8"><label class="lbl">名字</label>
          <input class="input" id="pe-name" value="${escHtml(p.name || '')}" placeholder="如：助手 / 老师 / 审稿人"></div>
        <div class="mt8"><label class="lbl">一句话说明</label>
          <input class="input" id="pe-desc" value="${escHtml(p.desc || '')}" placeholder="这套人设什么时候用"></div>
        <div class="mt8"><label class="lbl">人设正文（会放在 system prompt 最前面）</label>
          <textarea class="mono" id="pe-prompt" rows="12" spellcheck="false" placeholder="你是……&#10;&#10;说话方式：……&#10;底线：……">${escHtml(p.prompt || '')}</textarea></div>
        <div class="row mt12" style="gap:8px">
          <button class="btn btn-primary" data-save>保存</button>
          <button class="btn btn-secondary" data-cancel>取消</button>
        </div>
      </div>`;
    box.querySelector('[data-save]').onclick = () => this.saveEdit();
    box.querySelector('[data-cancel]').onclick = () => { box.innerHTML = ''; };
  },

  async saveEdit() {
    const { i, p } = this.editing;
    const name = this.root.querySelector('#pe-name').value.trim();
    const desc = this.root.querySelector('#pe-desc').value.trim();
    const prompt = this.root.querySelector('#pe-prompt').value;
    if (!name) { toast('名字不能空', 'err'); return; }
    const obj = { ...p, name, desc, prompt };
    if (i === null) {
      this.personas.push(obj);
      if (!this.active) this.active = obj.id;
    } else {
      this.personas[i] = obj;
    }
    this.root.querySelector('#persona-edit').innerHTML = '';
    await this.save();
  },

  async use(i) {
    this.active = this.personas[i].id;
    await this.save();
  },

  async del(i) {
    const p = this.personas[i];
    if (!confirm(`删除人设「${p.name}」？`)) return;
    this.personas.splice(i, 1);
    if (this.active === p.id) this.active = this.personas[0]?.id || '';
    await this.save();
  },

  async save() {
    try {
      await post('/admin/personas', { personas: this.personas, active: this.active });
      this.render();
      toast('已保存');
    } catch (e) {
      toast('保存失败：' + e.message, 'err');
    }
  },
};
