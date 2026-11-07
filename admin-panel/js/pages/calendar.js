// 📅 日历与整理 — 按天看记忆，热的日子颜色深
import { get, escHtml } from '../api.js';
import { badge, card, loadingBlock, modal } from '../ui.js';

const MN = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];

export default {
  title: '日历与整理',
  y: 2026, m: 11,
  data: null,
  err: '',

  async mount(root) {
    this.root = root;
    if (!this._inited) {
      const now = new Date();
      this.y = now.getFullYear();
      this.m = now.getMonth() + 1;
      this._inited = true;
    }
    root.innerHTML = loadingBlock();
    await this.load();
  },

  async load() {
    try {
      this.data = await get(`/admin/calendar?y=${this.y}&m=${this.m}`);
      this.err = this.data && this.data.ok === false ? (this.data.err || '接口失败') : '';
      if (this.data && (this.data.years || []).length && !(this.data.years || []).includes(this.y)) {
        this.y = this.data.years[0];
      }
    } catch (e) {
      this.data = null; this.err = e.message;
    }
    this.render();
  },

  async day(d) {
    try {
      const r = await get(`/admin/calendar?day=${d}`);
      const items = r.items || [];
      modal({
        title: `${d} · ${items.length} 张卡`,
        wide: true,
        body: items.length ? items.map(x => `
          <div style="padding:10px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;gap:8px;align-items:baseline">
              <b style="flex:1;font-size:13.5px">${escHtml(x.title || '(无标题)')}</b>
              ${x.softened ? badge('🌫️', 'muted') : ''}
              ${badge((x.heat || 0).toFixed(2), x.heat >= 0.6 ? 'gold' : 'muted')}
            </div>
            <div class="muted" style="font-size:12px;margin-top:4px">${escHtml(x.abstract || '')}</div>
            <div style="margin-top:6px">${badge(escHtml(x.kind), 'muted')} ${badge(escHtml(x.category || '未分类'), 'muted')}</div>
          </div>`).join('') : '<p class="muted">这天没有卡片</p>',
      });
    } catch (e) { alert('读取失败：' + e.message); }
  },

  render() {
    const root = this.root;
    if (this.err) { root.innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>${escHtml(this.err)}</div></div>`; return; }
    const d = this.data || {};
    const days = d.days || {};
    const mx = Math.max(1, ...Object.values(days).map(v => v.n));
    const first = new Date(this.y, this.m - 1, 1).getDay();
    const dim = new Date(this.y, this.m, 0).getDate();
    const pad = (first + 6) % 7;

    let cells = '';
    for (let i = 0; i < pad; i++) cells += '<div></div>';
    for (let i = 1; i <= dim; i++) {
      const key = `${this.y}-${String(this.m).padStart(2,'0')}-${String(i).padStart(2,'0')}`;
      const v = days[key];
      const n = v ? v.n : 0;
      const a = n ? 0.10 + 0.55 * (n / mx) : 0;
      const bg = n ? `background:rgba(22,163,74,${a.toFixed(3)});` : 'background:var(--bg-subtle);';
      cells += `<div data-day="${key}" style="aspect-ratio:1;border-radius:8px;padding:5px;cursor:${n?'pointer':'default'};${bg}border:1px solid var(--border);display:flex;flex-direction:column;justify-content:space-between">
        <span style="font-size:11.5px;color:var(--text-muted)">${i}</span>
        ${n ? `<span style="font-size:13px;font-weight:700">${n}</span>` : ''}
      </div>`;
    }

    root.innerHTML = `
      <div class="banner banner-info"><span>📅</span><div>
        按天看记忆。<b>颜色越深 = 这天记下的越多</b>，点任意一天看当天的卡片。归档依据是 <code>occurred_at</code>（事情发生的时间），没有的用创建时间。
      </div></div>

      ${card({
        title: `📅 ${this.y} 年 ${MN[this.m-1]}`,
        actions: `
          <button class="btn btn-sm btn-secondary" data-mv="-1">← 上月</button>
          <button class="btn btn-sm btn-secondary" data-mv="1">下月 →</button>
          <select id="cy" class="btn btn-sm btn-secondary">${(d.years||[this.y]).map(y=>`<option ${y==this.y?'selected':''}>${y}</option>`).join('')}</select>
        `,
        body: `
          <p class="muted" style="margin:0 0 10px;font-size:12.5px">这个月共 <b>${d.total||0}</b> 张</p>
          <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:5px;font-size:11px;color:var(--text-faint);margin-bottom:4px">
            <div>一</div><div>二</div><div>三</div><div>四</div><div>五</div><div>六</div><div>日</div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:5px">${cells}</div>
        `
      })}
    `;

    root.querySelectorAll('[data-day]').forEach(el => {
      el.onclick = () => { const k = el.getAttribute('data-day'); if ((days[k]||{}).n) this.day(k); };
    });
    root.querySelectorAll('[data-mv]').forEach(el => {
      el.onclick = () => {
        let m = this.m + Number(el.getAttribute('data-mv'));
        if (m < 1) { m = 12; this.y--; } else if (m > 12) { m = 1; this.y++; }
        this.m = m; this.load();
      };
    });
    const sel = root.querySelector('#cy');
    if (sel) sel.onchange = () => { this.y = Number(sel.value); this.load(); };
  }
};
