// 🌡️ 记忆机制 — 温度 / 半遗忘。参数走 config.js 的 schema 渲染。
import { get, post, escHtml, escAttr } from '../api.js';
import { toast, loadingBlock } from '../ui.js';
import { loadConfig, saveConfig } from '../config.js';

const BANDS = [
  ['hot', '高温', '#d9534f', '最近常被命中，注入时优先'],
  ['warm', '温热', '#e0a33e', '还在用，正常参与召回'],
  ['drowsy', '困了', '#8a8f7a', '不再主动注入，问到还能出来'],
  ['cold', '冷了', '#6b7280', '只在关键词精确命中时出现'],
  ['dead', '遗忘', '#4b5563', '命中过但已冷透'],
  ['never', '从未', '#c8c9c4', '从没被检索到过'],
];

const BAND_CN = Object.fromEntries(BANDS.map(b => [b[0], b[1]]));

export default {
  title: '记忆机制',

  data: null,

  async mount(root) {
    this.root = root;
    root.innerHTML = loadingBlock();
    await this.load();
  },

  async load() {
    const root = this.root;
    let d, cfg;
    try {
      [d, cfg] = await Promise.all([
        get('/admin/memory-mechanism'),
        loadConfig().catch(() => ({})),
      ]);
    } catch (e) {
      root.innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>${escHtml(e.message || e)}</div></div>`;
      return;
    }
    this.data = d;
    this.cfg = cfg;
    this.render();
  },

  render() {
    const root = this.root;
    const self = this;
    const d = this.data || {};
    const b = d.buckets || {};
    const sample = d.sample || 1;
    const total = d.total_active || 0;

    const cfg0 = this.cfg || {};

    const bar = BANDS.map(([k, label, color]) => {
      const n = b[k] || 0;
      const pct = n / sample * 100;
      if (pct < 0.4) return '';
      return `<span title="${label} ${n}" style="display:block;height:100%;width:${pct}%;background:${color}"></span>`;
    }).join('');

    root.innerHTML = `
      <h2 class="page-title">🌡️ 记忆机制</h2>
      <p class="page-intro">每条记忆有温度。被检索命中就升温，长期不碰按半衰期冷却。冷到阈值以下的卡不再主动注入，但不删除 —— 精确问到还能出来。这就是「半遗忘」。</p>

      <div class="card">
        <div class="row" style="justify-content:space-between;align-items:baseline;margin-bottom:10px">
          <div class="card-title">温度分布</div>
          <div class="item-sub">抽样 ${sample.toLocaleString()} / 活跃 ${total.toLocaleString()} · 平均 ${d.avg_heat ?? 0}</div>
        </div>
        <div style="display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--surface-hover)">${bar}</div>
        <div class="row" style="gap:14px;flex-wrap:wrap;margin-top:12px">
          ${BANDS.map(([k, label, color, desc]) => `
            <span style="display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--text-soft)" title="${escAttr(desc)}">
              <span style="width:8px;height:8px;border-radius:2px;background:${color};flex:none"></span>
              ${label} <b style="color:var(--text)">${(b[k] || 0).toLocaleString()}</b>
            </span>
          `).join('')}
        </div>
        <div class="item-sub" style="margin-top:10px">有温度记录的卡：<b>${d.backfilled || 0}</b> 张（从检索历史反推的初始热度）</div>
      </div>

      <div class="card mt16">
        <div class="row" style="justify-content:space-between;align-items:center">
          <div class="card-title">冷却参数</div>
          <button class="btn btn-xs btn-secondary" id="mm-backfill">重新回填</button>
        </div>
        <div id="mm-config" class="mt12"></div>
      </div>

      <div class="card mt16">
        <div class="card-title" style="margin-bottom:10px">🔥 最热的 20 张</div>
        ${(d.hot || []).length ? (d.hot || []).map(h => `
          <div class="item" style="padding:8px 0;border-bottom:1px solid var(--border)">
            <div class="row" style="gap:8px;align-items:baseline">
              <span class="badge badge-accent" style="flex:none">${h.heat}</span>
              <span style="flex:1;min-width:0;font-size:13.5px">${escHtml(h.title || '(无标题)')}</span>
              <span class="badge badge-muted" style="flex:none">${escHtml(h.last_hit_at ? h.last_hit_at.slice(5, 16) : '—')}</span>
            </div>
          </div>
        `).join('') : '<div class="item-sub">还没有热卡</div>'}
      </div>

      <div class="card mt16">
        <div class="card-title" style="margin-bottom:10px">🧊 最冷的 20 张</div>
        ${(d.cold || []).length ? (d.cold || []).map(h => `
          <div class="item" style="padding:8px 0;border-bottom:1px solid var(--border)">
            <div class="row" style="gap:8px;align-items:baseline">
              <span class="badge badge-muted" style="flex:none">${h.heat}</span>
              <span style="flex:1;min-width:0;font-size:13.5px;color:var(--text-soft)">${escHtml(h.title || '(无标题)')}</span>
              <span class="badge badge-muted" style="flex:none">${escHtml(h.last_hit_at ? h.last_hit_at.slice(5, 16) : '从未')}</span>
            </div>
          </div>
        `).join('') : '<div class="item-sub">没有被检索过但已冷却的卡（大部分卡是「从未命中」，不列在这里）</div>'}
      </div>

      <div class="card mt16">
        <div class="row" style="justify-content:space-between;align-items:center">
          <div class="card-title">🌫️ 自动软化</div>
          <label class="switch" style="flex:none">
            <input type="checkbox" id="mm-soften-toggle" ${String(cfg0['memory.soften_enabled'] ?? '0') === '1' ? 'checked' : ''}>
            <span class="slider"></span>
          </label>
        </div>
        <div class="item-sub" style="margin:8px 0 12px;line-height:1.6">
          冷透的老卡把正文压成一句话，<b>原文存在 body_orig，可随时还原</b>。
          证据链和 spans 一个字节都不动。关掉开关只停定时任务，手动按钮照常可用。
        </div>
        <div class="item-sub" id="mm-soften-count" style="margin:12px 0 10px"></div>
        <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
          <button class="btn btn-secondary btn-sm" id="mm-soften-dry">试跑 10 条（不写入）</button>
          <button class="btn btn-primary btn-sm" id="mm-soften-run">立即软化 50 条</button>
          <span class="item-sub" id="mm-soften-hint"></span>
        </div>
        <div id="mm-soften-out" class="mt12"></div>
      </div>
    `;

    // 参数区（自己渲染，走 saveConfig）
    const cfg = this.cfg || {};
    const KNOBS = [
      ['memory.half_life_days', '半衰期（天）', 'number', '0.5',
       '多久不碰就冷掉一半。3 天=近期热点，7 天=一周内，30 天=只有大事件才留热。'],
      ['memory.heat_boost', '命中升温量', 'number', '0.05',
       '每被检索命中一次加多少温度。0.25 = 连续命中 4 次到顶。建议 0.15-0.35。'],
      ['memory.drowsy', '困了阈值', 'number', '0.01',
       '低于此温度不再主动注入上下文，但被精确问到时仍能检索出来。'],
      ['memory.cold', '冷了阈值', 'number', '0.01',
       '低于此温度只在关键词精确命中时出现，几乎不参与语义召回。'],
      ['memory.cold_penalty', '冷卡折扣', 'number', '0.05',
       '检索时冷卡得分乘这个系数。1.0 = 不折扣（只展示不生效），0.4 = 很难挤进前几条。'],
      ['memory.soften_min_age_days', '软化最小年龄（天）', 'number', '1',
       '从没被检索过、且放了超过这么多天的卡才会被软化。太短会误伤新卡。建议 60-180。'],
      ['memory.soften_budget', '软化后字数上限', 'number', '10',
       '压成一句话的目标长度。太小会丢关键值，太大等于没压。建议 60-120。'],
      ['memory.heat_cap', '温度上限', 'number', '0.1',
       '单卡最高温度，防止一条卡被反复命中后永久锁死热榜。'],
    ];
    const el = root.querySelector('#mm-config');
    el.innerHTML = `
      <div class="row" style="gap:8px;align-items:center;margin-bottom:12px">
        <span style="font-size:13px;color:var(--text-soft)">启用温度衰减</span>
        <label class="switch" style="flex:none">
          <input type="checkbox" data-k="memory.enabled" ${String(cfg['memory.enabled'] ?? '1') !== '0' ? 'checked' : ''}>
          <span class="slider"></span>
        </label>
      </div>
      ${KNOBS.map(([k, label, type, step, desc]) => `
        <div style="margin-top:14px">
          <div style="font-size:13px;font-weight:600;color:var(--text)">${label}</div>
          <div class="item-sub" style="font-size:11px;margin:2px 0 6px">${k}</div>
          <input class="input" type="${type}" step="${step}" data-k="${k}"
                 value="${escAttr(String(cfg[k] ?? ''))}" style="width:140px">
          <div class="item-sub" style="margin-top:5px;font-size:11.5px;line-height:1.5">${desc}</div>
        </div>
      `).join('')}
      <div class="row" style="margin-top:16px;gap:8px">
        <button class="btn btn-primary btn-sm" id="mm-save">保存参数</button>
        <span class="item-sub" id="mm-save-hint"></span>
      </div>
    `;

    el.querySelectorAll('input[data-k]').forEach(inp => {
      inp.addEventListener('change', async () => {
        const k = inp.dataset.k;
        const v = inp.type === 'checkbox' ? (inp.checked ? '1' : '0') : inp.value.trim();
        try {
          await saveConfig(k, v);
          const hint = root.querySelector('#mm-save-hint');
          if (hint) { hint.textContent = `${k} 已保存`; setTimeout(() => { hint.textContent = ''; }, 2500); }
        } catch (e) {
          toast('保存失败：' + (e.message || e), 'err');
        }
      });
    });
    // ── 软化 ──────────────────────────────────────────
    const sToggle = root.querySelector('#mm-soften-toggle');
    if (sToggle) sToggle.addEventListener('change', async () => {
      try {
        await saveConfig('memory.soften_enabled', sToggle.checked ? '1' : '0');
        toast(sToggle.checked ? '自动软化已开启（定时任务生效）' : '自动软化已关闭');
      } catch (e) {
        toast('保存失败：' + (e.message || e), 'err');
        sToggle.checked = !sToggle.checked;
      }
    });

    const cntEl = root.querySelector('#mm-soften-count');
    const refreshCount = async () => {
      if (!cntEl) return;
      try {
        const r = await post('/admin/memory-mechanism/soften', { limit: 0 });
        const bd = r.bands || {};
        const parts = Object.entries(bd).map(([k, v]) => `${k} ${v}`).join(' · ');
        const cap = Number(r.cap || 0);
        const today = Number(r.today || 0);
        const left = r.left;
        const capTxt = cap > 0
          ? `　今日已软化 <b>${today}</b> / ${cap}` +
            (left === 0 ? ' <span style="color:#c0392b">（额度已用尽，明天 0 点重置）</span>'
                        : ` <span style="opacity:.7">（还剩 ${left}）</span>`)
          : '　今日已软化 <b>' + today + '</b>（不限量）';
        cntEl.innerHTML = `当前候选 <b>${(r.candidates || 0).toLocaleString()}</b> 张` +
          (parts ? ` <span style="opacity:.7">（${parts}）</span>` : '') +
          capTxt +
          (r.candidates === 0
            ? '<br><span style="color:#b8860b">还没有卡冷下来。温度机制上线不满 8 天时，冷档（困了/冷了/遗忘）是空的；等卡被检索过、又长期没人再问，它们才会掉进候选。</span>'
            : '');
      } catch (e) {
        cntEl.textContent = '';
      }
    };
    refreshCount();

    const sOut = root.querySelector('#mm-soften-out');
    const sHint = root.querySelector('#mm-soften-hint');
    const runSoften = async (dry) => {
      if (sHint) sHint.textContent = dry ? '试跑中…' : '软化中…';
      try {
        const r = await post('/admin/memory-mechanism/soften', { limit: dry ? 10 : 50, dry });
        const done = (r.done || []);
        const skip = (r.skipped || []);
        if (sHint) sHint.textContent = dry
          ? `可压 ${done.length} 条 / 跳 ${skip.length} 条`
          : `已软化 ${r.applied || 0} 条`;
        if (sOut) sOut.innerHTML = done.length ? `
          <div class="item-sub" style="margin-bottom:6px">${dry ? '以下会被软化（未写入）' : '刚软化的卡'}</div>
          ${done.map(x => `
            <div class="item" style="padding:6px 0;border-bottom:1px solid var(--border)">
              <div class="row" style="gap:8px;align-items:baseline">
                <span style="flex:1;min-width:0;font-size:13px">${escHtml(x.title || '(无标题)')}</span>
                <span class="badge badge-muted" style="flex:none">${x.before}→${x.after}</span>
              </div>
            </div>`).join('')}
        ` : '<div class="item-sub">这批没有可压的（都还热，或已是软化的）</div>';
        if (!dry) await self.load();
      } catch (e) {
        if (sHint) sHint.textContent = '';
        toast('软化失败：' + (e.message || e), 'err');
      }
    };
    const dryBtn = root.querySelector('#mm-soften-dry');
    const runBtn = root.querySelector('#mm-soften-run');
    if (dryBtn) dryBtn.onclick = () => runSoften(true);
    if (runBtn) runBtn.onclick = () => runSoften(false);

    const saveBtn = root.querySelector('#mm-save');
    if (saveBtn) saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      try {
        for (const inp of el.querySelectorAll('input[data-k]')) {
          const k = inp.dataset.k;
          const v = inp.type === 'checkbox' ? (inp.checked ? '1' : '0') : inp.value.trim();
          await saveConfig(k, v);
        }
        toast('参数已保存');
        await self.load();
      } catch (e) {
        toast('保存失败：' + (e.message || e), 'err');
      } finally {
        saveBtn.disabled = false;
      }
    };

    const bf = root.querySelector('#mm-backfill');
    if (bf) bf.onclick = async () => {
      bf.disabled = true;
      bf.textContent = '回填中…';
      try {
        const r = await post('/admin/memory-mechanism/backfill', {});
        toast(`已回填 ${r.refilled} 张`);
        await self.load();
      } catch (e) {
        toast('回填失败：' + (e.message || e), 'err');
      } finally {
        bf.disabled = false;
        bf.textContent = '重新回填';
      }
    };
  },
};
