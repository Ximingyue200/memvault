// ⚙️ 记忆库参数 — 复用面板的 schema 渲染器，数据走本网关的 /admin/config
import { escHtml } from "../api.js";
import { loadingBlock, toast } from "../ui.js";
import { loadConfig, renderConfigGroups, wireConfig, ensureModelDatalist } from "../config.js";

export default {
  title: "记忆库参数",
  async mount(root) {
    this.root = root;
    ensureModelDatalist();
    root.innerHTML = `<div id="cc-body">${loadingBlock()}</div>`;
    let cfg;
    try {
      cfg = await loadConfig();
    } catch (e) {
      root.querySelector("#cc-body").innerHTML =
        `<div class="banner banner-warn"><span>⚠️</span><div>加载失败：${escHtml(e.message)}</div></div>`;
      toast(e.message, "err");
      return;
    }
    const box = root.querySelector("#cc-body");
    const html = renderConfigGroups("console", cfg);
    box.innerHTML = html
      ? html
      : `<div class="banner banner-info"><span>💡</span><div>没有可编辑的参数。</div></div>`;
    wireConfig(box, cfg);
  },
};
