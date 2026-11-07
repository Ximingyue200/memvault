// 🗜️ 上下文压缩 — 注入预算看板：每轮到底塞了多少字进上下文
import { get, put, escHtml } from '../api.js';
import { statCard, card, badge, loadingBlock, toast } from '../ui.js';

const TOK = 1.6; // 中文约 1.6 字符 = 1 token

export default {
  title: '上下文压缩',
  d: null, err: '',

  async mount(root) {
    this.root = root;
    root.innerHTML = loadingBlock();
    await this.load();
  },

  async load() {
    try {
      this.d = await get('/admin/inject-budget');
      this.cfgHandoff = await get('/admin/config').catch(() => ({}));
      this.models = (await get('/admin/all-saved-models').catch(() => ({ models: [] }))).models || [];
      this.err = this.d && this.d.ok === false ? (this.d.err || '接口失败') : '';
    } catch (e) { this.d = null; this.err = e.message; }
    this.render();
  },

  render() {
    const root = this.root;
    if (this.err) { root.innerHTML = `<div class="banner banner-warn"><span>⚠️</span><div>${escHtml(this.err)}</div></div>`; return; }
    const d = this.d || {};
    const rec = d.recent || [];
    const fixed = d.fixed || {};
    const cfg = d.cfg || {};
    const avgTok = Math.round((d.avg_chars || 0) / TOK);

    root.innerHTML = `
      <div class="banner banner-info"><span>🗜️</span><div>
        这不是「压缩对话」，而是<b>看清楚每轮往上下文里塞了多少字</b>。数字来自 <code>injections</code> 表。
        <br><b>注意：</b>2026-11-05 之前的历史记录按「响应体总长」计（含 evidence 原文），偏大约 5–8 倍；之后的记录是<b>真实注入正文</b>（标题 + 摘要）。
      </div></div>

      <div class="grid grid-4 mb16">
        ${statCard({ label: '📥 累计注入', value: (d.total || 0).toLocaleString(), cls: 'accent', sub: '次' })}
        ${statCard({ label: '📝 平均字数', value: (d.avg_chars || 0).toLocaleString(), cls: 'gold', sub: `≈ ${avgTok.toLocaleString()} token（含老数据）` })}
        ${statCard({ label: '🧩 平均卡片', value: d.avg_cards || 0, sub: '张 / 次' })}
        ${statCard({ label: '📌 固定内容', value: ((fixed['画像'] || 0) + (fixed['自我笔记'] || 0)).toLocaleString(), cls: 'purple', sub: `画像 ${fixed['画像'] || 0} · 笔记 ${fixed['自我笔记'] || 0}` })}
      </div>

      ${card({
        title: '⚙️ 当前门槛',
        body: `
          <div class="kv"><span class="k">注入条数 <code>gw.topk</code></span><span class="v">${escHtml(String(cfg['gw.topk'] || '—'))}</span></div>
          <div class="kv"><span class="k">相似度门槛 <code>gw.min_score</code></span><span class="v">${escHtml(String(cfg['gw.min_score'] || '—'))}</span></div>
          <div class="kv"><span class="k">自动注入 <code>inject.enabled</code></span><span class="v">${cfg['inject.enabled'] === '1' ? badge('开启', 'accent') : badge('关闭', 'muted')}</span></div>
          <div class="kv"><span class="k">工具展开阈值 <code>ext_drawer_threshold</code></span><span class="v">${escHtml(String(cfg['ext_drawer_threshold'] || '—'))}</span></div>
          <p class="muted" style="margin-top:10px;font-size:12px">在「记忆库参数」页改，改完即时生效。</p>
        `
      })}

      ${card({
        title: '🪟 无缝换窗（新窗自动衔接上一条对话）',
        body: (() => {
          const c0 = this.cfgHandoff || {};
          const raw = c0.config || c0;
          const on = String(raw['handoff_enabled'] ?? '0') === '1';
          const tail = String(raw['handoff_tail_count'] ?? '6');
          const model = String(raw['handoff_summary_model'] ?? '');
          const opts = ['<option value="">（跟随全局）</option>']
            .concat((this.models || []).filter(m => (m.model_type || 'chat') !== 'embedding')
              .map(m => `<option value="${escHtml(m.model_id)}" ${m.model_id === model ? 'selected' : ''}>${escHtml(m.provider)} / ${escHtml(m.model_id)}</option>`));
          return `
            <div style="display:flex;align-items:center;gap:12px;padding:6px 0 14px">
              <div style="flex:1">
                <b>启用换窗</b>
                <div class="muted" style="font-size:12px;margin-top:3px">新对话第一条消息时，注入上一个对话的概要 + 结尾原文。</div>
              </div>
              <label class="switch"><input type="checkbox" id="hf-on" ${on ? 'checked' : ''}><span class="slider"></span></label>
            </div>
            <div class="field">
              <label>结尾原文条数 <code>handoff_tail_count</code></label>
              <input type="number" id="hf-tail" min="0" max="20" step="1" value="${escHtml(tail)}">
              <p class="muted" style="font-size:12px">约 3 轮对话的量。0 = 只给概要。</p>
            </div>
            <div class="field">
              <label>概要模型 <code>handoff_summary_model</code></label>
              <select id="hf-model">${opts.join('')}</select>
              <p class="muted" style="font-size:12px">预留：当前换窗只带结尾原文，不生成概要，此项目前无效果。</p>
            </div>
            <p class="muted" style="font-size:12px;margin-top:8px">✅ 已生效：新会话的第一条消息会自动拼接上一个会话的结尾原文（&lt;handoff&gt; 块），之后各轮不再重复。需客户端每轮都带完整历史（用首条 user 消息识别会话）。</p>
          `;
        })()
      })}

      ${card({
        title: '📋 最近 40 次注入',
        body: rec.length ? rec.map(r => `
          <div style="padding:9px 0;border-bottom:1px solid var(--border)">
            <div style="display:flex;gap:8px;align-items:baseline">
              <b style="flex:1;font-size:13px">${escHtml(r.q || '(空查询)')}</b>
              ${badge((r.n_chars || 0).toLocaleString() + ' 字', (r.n_chars || 0) > 8000 ? 'warn' : 'muted')}
            </div>
            <div class="muted" style="font-size:11.5px;margin-top:3px">
              ${r.n_cards || 0} 张 · ${r.ms || 0}ms · ${escHtml((r.at || '').replace('T', ' ').slice(0, 19))}
            </div>
          </div>`).join('') : '<p class="muted">还没有注入记录</p>'
      })}
    `;
    this.wireHandoff();
  },

  wireHandoff() {
    const root = this.root;
    const on = root.querySelector('#hf-on');
    const tail = root.querySelector('#hf-tail');
    const model = root.querySelector('#hf-model');
    const save = async (k, v) => {
      try { await put('/admin/config/' + k, { value: String(v) }); toast('已保存'); }
      catch (e) { toast('保存失败：' + e.message, 'err'); }
    };
    if (on) on.onchange = () => save('handoff_enabled', on.checked ? '1' : '0');
    if (tail) tail.onchange = () => save('handoff_tail_count', tail.value);
    if (model) model.onchange = () => save('handoff_summary_model', model.value);
  }
};
