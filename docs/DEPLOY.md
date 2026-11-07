# 部署

面向能自己管机器的人，只写**不写就会踩**的部分。

---

## 默认用的是演示端点（知道就行）

`llm.base` 默认 `https://text.pollinations.ai/openai` —— 免费、无需 key、慢、会 402 限流。
它只保证流程能跑通，不保证质量。换自己的 API 见第 3 节。

演示模式下的实际行为：

| 环节 | 表现 |
|---|---|
| 对话（网关 4180） | 能跑，延迟 1.5–13s |
| 抽卡（learn） | 端点限流或返回空时，退回本地规则抽取（`demoextract.py`），能出 note 卡，但不是模型理解 |
| 做梦 / 编年史 / 画像 | **写不出来**，需要真模型 |
| 向量 | 没配 embedding 就退化成 BM25 + LIKE，卡片照常入库，只是日志有 `[warn] 向量补写失败` |
| 抽卡门槛 | 单段原文 < 200 字（`learn.MIN_CHARS`）直接跳过 |

---

## 1. 跑起来

```bash
git clone https://github.com/Ximingyue200/memvault.git && cd memvault
pip install -r requirements.txt     # 只有 numpy
./deploy.sh                         # 建 ./data、起三个进程、打印端点
./deploy.sh stop
```

| 入口 | 地址 |
|---|---|
| 面板 | `http://<host>/admin`（`:80`，与 `:4180/admin` 同一套） |
| 网关（带注入） | `:4180/v1` |
| 网关（不注入） | `:4182/v1` |
| MCP | `:4181/mcp` |

空库首启自建 37 张表 + 默认设置，不需要迁移脚本。

`deploy.sh` 导出的是 `MEMVAULT_DB` / `GW_DB` / `MEMVAULT_LLM_*`。手工起进程时这几个变量得自己给 ——
代码里的默认库路径是作者机器上的 `/opt/memvault/data/memvault.db`，不设就是 `unable to open database file`。

---

## 2. 接 agent

**网关（自动注入）**

```
base_url = http://<host>:4180/v1
api_key  = 任意非空（网关不校验）
model    = settings 里配的那个
```

每轮把常驻层 + 检索到的事实卡拼进 system；回答完写 buffer，凌晨统一落库。要对比直连就换 `:4182/v1`。

**MCP（只能挂 MCP 的 agent）**

```
url = http://<host>:4181/mcp
transport = streamable_http
```

`recall(query, topk, min_score)` / `retain(text, context)` / `stats()` / `report()`。
agent 写入默认 `proposal`，批准后才进检索（防多 agent 互相污染）。

---

## 3. 换掉演示端点

环境变量（推荐）或面板「记忆库参数」都行，改完下次调用生效：

```bash
export MEMVAULT_LLM_URL=https://api.deepseek.com          # Ollama: http://127.0.0.1:11434/v1
export MEMVAULT_LLM_MODEL=deepseek-flash
export MEMVAULT_LLM_KEY=sk-...
./deploy.sh
```

面板里还能给 learn / dream / summary 分别指定模型和 key（「分任务接入」），提炼走便宜的、做梦走贵的。

**向量（可选）**：起一个 bge-m3 的 OpenAI 兼容 embeddings 服务

```bash
export MEMVAULT_EMBED_URL=http://127.0.0.1:8081/v1/embeddings
export MEMVAULT_EMBED_MODEL=bge-m3          # 维度必须与库一致（默认 1024）
```

---

## 4. 定时任务

不装也能用，只是存进去的东西不会自己变成卡片。

| 任务 | 频率 | 作用 |
|---|---|---|
| `python3 -m memvault.collect` | 10 min | 新对话 → span |
| `python3 -m memvault.learn` | 30 min | span → 事实卡（唯一花钱的一步） |
| `python3 -m memvault.dream` | 1 h | 编年史 / 画像 / 自述 |
| `python3 -m memvault.focus` | 1 h | 未完成的事 |
| `python3 -m memvault.soften` | 每天 | 软化过期断言 |
| `python3 -m memvault.supersede` | 每天 | 取代关系 |
| `python3 -m memvault.backup` | 每天 | SQLite 在线备份 |

DeepSeek 下一条 learn 约 ¥0.005–0.01。

---

## 5. 会遇到的几件事

**面板 502 `cradle unreachable`** —— 网关把 `/admin/cradle/*` 代理到 `127.0.0.1:4174`。改了 server 端口就会断，保持默认或同时改代理目标。

**搜不到** —— ①无向量服务 + 长中文句子（BM25 切不开中文），配 bge-m3 或把查询压短；②新卡还是 proposal，没批。

**回答像编的** —— 看完形强度和卡片 `confidence`；卡片详情里能顺着证据回到原始 span。

---

## 6. 放到公网之前

这一节是常识，写在这儿只是免得事后想起来。

- **面板和控制接口没有登录**：`/admin/*`、`/admin/cradle/*`、`/settings`、`/debug` 任何人都能打开。
  API key 一律打码成 `****尾4`，但拓扑、开关、卡片正文、原文 span 都是明文可见的。
- **模型入口按 token 校验，表空时放行**：建过 token 之后 `/v1`（`:80` / `:4180` / `:4182`）与 `/mcp` 都要 `Authorization: Bearer <token>`；没建 token 的空库仍是全放行。
- 所以：只在内网/本机开，或者套一层反代（Nginx / Caddy）加 Basic Auth / OIDC，再加 TLS。
- `allow_public_bind` 之类的东西本项目没有，别指望它自己拦。
- 真要公网多用户，至少把 `:4181`（MCP）和 `:4174`（API）挡在反代后面，只对外放一个入口（`:80` 或 `:4180`）并配自己的前置鉴权。
