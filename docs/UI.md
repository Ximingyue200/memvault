# 界面设计说明

两套界面，同一份数据。

## Kiwi-Mem 管理面板（admin-panel/）

- 技术：原生 ES module，无打包、无框架；每页一个 `export default { title, async mount(root) }`。
- 导航：`js/routes.js` 的 `NAV` 七组（我的记忆库 / 概览 / 接入配置 / 人设与记忆 / 记忆机制 / 运维与安全 / 系统），可收起。
- 接线：`js/app.js` 的 `WIRED` 白名单决定真渲染的页；未接线的页只给提示，不放假按钮。
- 讲解：`routes.js` 的 `LEDES` 给每页一句话「这页干什么、什么时候来」。
- 组件：`js/ui.js`（card / kv / statCard / toast / modal / ctl）+ `js/api.js`（get/post/put/del + 格式化）。

## 深色单页（memvault/webui.html）

- 28 KB 单文件，零依赖；七屏：概览 / 检索 / 卡片 / 待审 / 时间线 / 图谱 / 设置。
- 图谱按「度数取种子 + 诱导子图」构图，避免抽样节点与边集不重合导致的「有节点没边」。
- 设置页把 `settings` 与网关环境变量放在一起，改完即生效（环境变量那次会重启网关）。
