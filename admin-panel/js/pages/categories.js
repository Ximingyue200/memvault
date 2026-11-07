// 碎片分类：这些分类由提炼时自动打，不是手建的。
// 这里只做展示 + 点击跳到记忆碎片页按类筛选。
import { escHtml, escAttr, get } from '../api.js';

export default {
  title: '碎片分类',

  cats: [], sum: 0,

  async mount(root) {
    this.root = root;
    root.innerHTML = `
      <h2 class="page-title">🏷️ 碎片分类</h2>
      <p class="page-intro">分类是提炼时按内容自动打的标签（33 类中的 25 类有卡）。点任意一类，跳到「记忆碎片」页按它筛选。</p>
      <div id="cat-list"><div class="item-sub">载入中…</div></div>
    `;
    await this.load();
  },

  async load() {
    const el = this.root.querySelector('#cat-list');
    let list = [];
    try {
      const d = await get('/admin/categories');
      list = d.categories || [];
    } catch (e) {
      el.innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>${escHtml(e.message || e)}</div></div>`;
      return;
    }
    this.cats = list;
    this.sum = list.reduce((a, c) => a + (c.count || 0), 0);

    el.innerHTML = `
      <div class="card">
        <div class="item-sub">${list.length} 个分类 · 共 ${this.sum.toLocaleString()} 张卡</div>
      </div>
      <div class="mt16">
        ${list.map(c => `
          <div class="card" data-cat="${escAttr(c.name)}" style="cursor:pointer;margin-bottom:8px;display:flex;align-items:center;gap:10px">
            <span style="font-size:20px;flex:none">${escHtml(c.icon || '📁')}</span>
            <div style="flex:1;min-width:0">
              <div style="font-weight:600;font-size:14.5px">${escHtml(c.name)}</div>
              <div class="item-sub" style="margin-top:2px">${c.count.toLocaleString()} 张 · 占 ${(c.count / this.sum * 100).toFixed(1)}%</div>
            </div>
            <span style="flex:none;width:64px;height:6px;border-radius:3px;background:#eef0f3;overflow:hidden">
              <span style="display:block;height:6px;width:${Math.max(2, c.count / list[0].count * 100).toFixed(0)}%;background:${escAttr(c.color || '#74a838')}"></span>
            </span>
          </div>
        `).join('')}
      </div>
    `;

    const self = this;
    el.querySelectorAll('[data-cat]').forEach(elm => {
      elm.onclick = () => {
        location.hash = '#/memories';
        sessionStorage.setItem('prefilter_category', elm.dataset.cat);
      };
    });
  },
};
