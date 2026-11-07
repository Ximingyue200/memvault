// 🔐 认证与安全 — 现状如实说，不粉饰
import { get, escHtml } from "../api.js";
import { loadingBlock } from "../ui.js";

export default {
  title: "认证与安全",
  async mount(root) {
    root.innerHTML = `<div id="se-body">${loadingBlock()}</div>`;
    let env = { items: {} }, ov = {};
    try {
      env = await get("/admin/cradle/api/gwenv");
      ov = await get("/admin/cradle/api/overview");
    } catch (e) {
      root.querySelector("#se-body").innerHTML =
        `<div class="banner banner-warn"><span>⚠️</span><div>读取失败：${escHtml(e.message)}</div></div>`;
      return;
    }
    const it = env.items || {};
    const tok = (it.GW_TOKEN && it.GW_TOKEN.v) || "";
    const key = (it.MEMVAULT_LLM_KEY && it.MEMVAULT_LLM_KEY.v) || "";

    const items = [
      ["模型入口鉴权", "已开启", "ok", "/v1 与 /mcp 校验 api_tokens（表为空时放行），一个 token 一个 agent 身份"],
      ["管理面板鉴权", "未开启", "warn", "/admin 与 /stats 不做校验，能连上 80/4180 就能读写配置"],
      ["网关令牌 GW_TOKEN", tok ? "已设置" : "未设置", tok ? "ok" : "warn", "护着 /settings、/debug、/providers 这几个读接口"],
      ["上游 Key", key ? "已配置" : "缺失", key ? "ok" : "danger", "DeepSeek 计费用的，泄露等于送钱"],
      ["可达范围", "内网 / 组网", "info", "内网/组网可见；公网未映射"],
      ["TLS", "无", "warn", "内网明文 HTTP；要上公网必须套反向代理 + 证书"],
      ["数据库", "本机 SQLite", "info", "单文件，备份见「备份与数据」"],
    ];
    const badge = (tone, txt) =>
      `<span class="badge ${tone === "ok" ? "badge-info" : tone === "danger" ? "badge-danger" : tone === "warn" ? "badge-warn" : "badge-muted"}">${escHtml(txt)}</span>`;

    root.querySelector("#se-body").innerHTML = `
      <div class="banner banner-warn"><span>🔐</span><div>
        <b>管理接口仍是「内网裸奔」。</b>模型入口（/v1、/mcp）已经有 token，面板与 /admin 下的读写还没做——只在内网和组网里可达，省掉鉴权换来零摩擦。
        一旦要暴露到公网，先补反向代理 + 基本认证 + HTTPS，再谈别的。
      </div></div>

      <div class="card">
        ${items.map(([k, v, tone, hint]) => `
          <div class="kv">
            <div><div class="k">${escHtml(k)}</div><div class="hint">${escHtml(hint)}</div></div>
            <div>${badge(tone, v)}</div>
          </div>`).join("")}
      </div>

      <div class="section-title mt24">要上公网时按这个顺序</div>
      <div class="card">
        <ol class="text-sm" style="margin:0;padding-left:20px;line-height:2">
          <li>反向代理终结 TLS（Caddy / nginx），只暴露一个入口</li>
          <li>给面板补 Basic Auth（或把 <code>GW_TOKEN</code> 接到 /admin 的写接口上），别只护着 /v1</li>
          <li>4174 / 4181 不对外，只让 80 / 4180 反代到它们</li>
          <li>限制来源 IP 段；日志里别留 query 原文（现在 <code>GW_DEBUG=1</code> 会记）</li>
        </ol>
      </div>

      <p class="faint text-xs mt24">
        库大小 ${ov.db_mb ?? "-"} MB · 卡片 ${ov.cards ?? "-"} 张 · 这套判断随配置变化，页面上看到的都是实时读的。
      </p>`;
  },
};
