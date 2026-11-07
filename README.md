# MemVault — 本地优先的 Agent 记忆中间件


> ⚠️ 默认 LLM 指向免费的 `text.pollinations.ai`（无需 key、慢、会限流），
> 只够验证流程。换自己的 API：见 [docs/DEPLOY.md](docs/DEPLOY.md#3-换掉演示端点)。

一个跑在自己机器上的记忆层：**网关**接管所有模型的对话流量，自动检索、注入、记账；**MCP 服务端**给只能用 MCP 的 agent 提供同一套记忆；**卡片 + 证据**保证每条记忆都能追回到原文；**面板**让人能看见、能改、能审。

> 一次记忆，多个 Agent 共享。换模型、换客户端、换工具，记忆不跟着丢。

---

## 它解决什么

Agent 客户端各记各的，换个模型就失忆，换个工具就重来。MemVault 把「记忆」从 agent 里剥出来，做成一个独立进程：

- **Gateway 模式**（透明代理）：客户端只改 `base_url`，之后的检索、注入、落盘、记账全自动。
- **MCP 模式**（按需取用）：不支持自定义 `base_url` 的客户端，挂一个 MCP 服务器，拿 `recall / retain / stats / report / pitfall / pitfall_add` 等九个工具。
- 两种模式共用同一个库、同一套卡片与证据。

## 核心特性

| 能力 | 说明 |
|---|---|
| 混合检索 | 向量（bge-m3 1024 维）+ BM25（SQLite FTS5），按 `0.65 * vec + 0.35 * bm25` 融合，再过交叉编码器重排 |
| 卡片 + 证据 | 每条记忆是一张卡，`card_evidence` 指向具体 span 的字符区间，永远能回原文核对 |
| 常驻层 + 检索层 | 常驻层固定预算、每轮全给（回答「这个人是谁」）；检索层按问题取（回答「那件具体的事」） |
| 幂等去重 | `content_hash` + 余弦阈值，同义复述合并，不重复计费 |
| 取代与降权 | 新卡 `supersedes` 旧卡，旧卡分数乘 0.55、不删，证据链仍在 |
| 提案门槛 | agent 写入默认 `proposal`，人批准后才进检索；人工记忆永远高于模型判断 |
| 温度衰减 | 半衰期 7 天（`pinned` 30 天），冷卡降权、热卡抬权 |
| Token 记账 | 拦 `urlopen` 记录每次调用的 in/out/缓存与花费，按任务 + key 聚合 |
| 分任务路由 | 提炼 / 梦境 / 摘要可以各用一套 key、模型、上游 |
| 无注入入口 | 同一进程再开一个端口，跳过全部记忆拼装，方便对比与排障 |
| Agent 身份 | `api_tokens` 表 + `mvtoken`；token 名就是 agent 名，服务端反查，队列里的身份不可伪造 |
| 写入暂存队列 | `retain` 先进 `retain_queue`，攒够 5 条 / 1500 字再统一抽取、判重、合并，避免碎片成卡 |
| 跨 agent 可见 | `pending` 看别人写了什么；`recall` 同时返回 `pending_hits`，没出库的也能被问到 |
| 并发安全 | 进程内锁 + 跨进程 `flock` + 行级原子领取，MCP 与定时器同时提炼也只出一张卡 |

## 界面

只有一套界面：**MemVault 管理面板**（源码在 `admin-panel/`）。默认 HTTP 端口就能直接打开：
`http://<host>/admin`（网关在 80 上跑同一套路由，`:4180/admin` 同样可用）。
原生 ES module + 一个 CSS，没有构建步骤。

记忆库自己的 `:4174` 只留 API 与 `/admin/cradle/*` 代理，旧页面一律 302 到面板 ——
一个入口、一份实现，不做两套半成品。

### MemVault 管理面板（`admin-panel/`）

原生 ES module + 一个 CSS，双击 `index.html` 之外不需要任何工具链。侧栏按**「人在做什么」**分组，而不是按表结构分组，并且可以整列收起：

| 分组 | 页面 |
|---|---|
| 我的记忆库 | 记忆库、记忆库参数 |
| 概览 | 仪表盘 |
| 接入配置 | 供应商与模型、联网搜索、工具抽屉、技能、能力登记簿 |
| 人设与记忆 | 人设、用户画像、记忆碎片、碎片分类 |
| 记忆机制 | 记忆机制、Dream、日历与整理、上下文压缩、项目分隔 |
| 运维与安全 | 网关、同步与备份、安全、待审、取代与冲突、运维与健康 |
| 系统 | 原始配置 |

两条设计原则写死在代码里：

- **每页顶部一条讲解**（`LEDES`）：先说「这页是干什么的、什么时候该来」，再给控件。参数页甚至给默认值对照，避免「不知道该填什么」。
- **没接线的页不许假装能用**：`app.js` 的 `WIRED` 白名单决定哪些页真渲染，其余只显示一句「这页还没接后端」，而不是给你一堆点了没反应的按钮。

## 架构

```
              ┌──────────── 客户端（Eta / Hermes / 自建 agent）────────────┐
              │  base_url: 4180 (带记忆)   4182 (纯转发)   MCP: 4181        │
              └───────────────┬───────────────────┬───────────────────────┘
                              │                   │
                    ┌─────────▼─────────┐  ┌──────▼──────┐
                    │  gateway.py       │  │ mcp_server  │
                    │  检索→注入→落盘    │  │ 9 个工具     │
                    └─────────┬─────────┘  └──────┬──────┘
                              │                   │
                    ┌─────────▼───────────────────▼─────────┐
                    │              memvault 包               │
                    │  store / search / heat / extract / …   │
                    └─────────┬───────────────────┬─────────┘
                              │                   │
                     ┌────────▼───────┐   ┌───────▼────────┐
                     │ SQLite (WAL)   │   │ 上游模型 API    │
                     │ cards/spans/   │   │ 任意 OpenAI 兼容 │
                     │ vectors/FTS5   │   └────────────────┘
                     └────────────────┘
```

单文件 SQLite，无外部服务依赖；向量存 BLOB，检索用 numpy 暴力算（十万级卡片足够）。

## 快速开始

```bash
git clone <this-repo> memvault && cd memvault
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # numpy / urllib3 / 可选 sentence-transformers

export MEMVAULT_DB=./data/memvault.db
export MEMVAULT_LLM_KEY=sk-...              # 任意 OpenAI 兼容上游
export MEMVAULT_LLM_API=https://api.deepseek.com/chat/completions
export MEMVAULT_EMBED_URL=http://127.0.0.1:8081/v1/embeddings   # bge-m3 服务
python3 -m memvault.server                # 面板与 API：:4174
python3 -m memvault.gateway               # 网关：:4180 / 无注入 :4182
python3 -m memvault.mcp_server            # MCP：:4181
```

首次启动会建表并灌默认设置。

嫌上面麻烦就直接 `./deploy.sh`：它把 `MEMVAULT_DB` 指到 `./data`、起三个进程、
打印四个入口，`./deploy.sh stop` 收工。默认 LLM 走一个免 key 的演示端点，
抽不出卡时会用本地规则兜底，够你把界面点一遍。要真用就换成自己的模型（见 `docs/DEPLOY.md`）。

### 客户端怎么接

```bash
# OpenAI 兼容（自动注入记忆）
export OPENAI_BASE_URL=http://127.0.0.1:4180/v1
export OPENAI_API_KEY=any
```

```json
// MCP 客户端
{"mcpServers": {"memvault": {"url": "http://127.0.0.1:4181/mcp", "transport": "streamable_http"}}}
```

四个入口，各管一件事：

| 入口 | 用途 |
|---|---|
| `:4180/v1` | 自动注入记忆的 OpenAI 兼容口（对话走这里） |
| `:4182/v1` | 同一进程、**不注入**的纯净口，用来对比或排障 |
| `:4181/mcp` | MCP：`recall` / `retain` / `stats` / `report` / `pitfall` / `pitfall_add` / `pending` / `queue_stats` / `flush` |
| `:80` | 与 4180 同进程同路由：`/admin` 面板 + `/v1` 都能走，省掉记端口 |
| `:4174` | 记忆库自己的 HTTP API（面板经由 `/admin/cradle/*` 调用） |

> 建过 token 之后，`/v1/*`（4180、4182）与 `/mcp` 都要带 `Authorization: Bearer <token>`。
> OpenAI 兼容客户端里把它填进 `api_key` 即可；MCP 客户端填在 header。
> 还没建 token 时表为空，一律放行，方便首次跑起来。

## 目录

```
memvault/          核心包（store / search / heat / extract / gateway / mcp_server / server …）
                  新增：initdb（空库建表）/ auth（token）/ qflush（暂存队列）/ demoextract（无 key 兜底）
admin-panel/       管理面板（原生 ES module，无构建步骤）
docs/              架构说明与已知问题
cron_job.py        定时任务入口（collect / learn / dream / focus / soften / supersede）
scrub.py           入库前的凭据与隐私脱敏
```

## 设计边界

- **不做**通用 RAG 平台，只解决「agent 的长期记忆」。
- **不替** agent 做决策，只提供它缺少的连续性与经验。
- 记忆默认本地、默认可删、默认可追；外部调用失败一律 fail-open，不阻塞对话。

---

## 持续集成

`.github/workflows/ci.yml` 三个 job，都在几分钟内跑完（GitHub Actions 直接可用；Forgejo 把同一份 YAML 放进 `.forgejo/workflows/` 也能跑，或者照下面命令在本地跑）：

| job | 检查什么 |
|---|---|
| Python | 全部 `.py` 过 `compileall`；核心 10 个模块逐个 `import` 冒烟；矩阵 3.10 / 3.11 / 3.12 |
| 面板 JS | 每个 `.js` 复制成 `.mjs` 后 `node --check`，因为原生 ES module 没有构建步骤替你发现语法错 |
| 仓库卫生 | 拦真实密钥（`sk-` / `gwk-` / `ghp_` / `github_pat_` / `tvly-`）与超过 2MB 的文件 |

刻意没做的：不跑真实检索与抽取（需要模型 key 和真库，CI 里没有也不该有），不跑 lint 规则集（一个人维护，规则本身会变成第二个要维护的东西）。细节见 [`docs/CI.md`](docs/CI.md)。

## 多 agent 协作

一套库，多个 agent 写。为了避免「谁都能覆盖谁」和「每句碎片都写成一张卡」，写入走两道闸：

**① 身份：token 就是 agent 名**

```bash
mvtoken create eta          # 打印一次明文，只存 sha256
mvtoken list                # 看谁还有效
mvtoken disable eta
```

建了第一个 token 之后，`/v1/*` 与 `/mcp` 就要带 `Authorization: Bearer <token>`；
**表为空时不校验**，所以升级过程中不会把自己锁在外面。agent 不用自报名字，
服务端按 token 反查 —— 队列里的 `agent` 字段是可信的。

**② 写入：先进暂存队列**

`retain` 不直接入库，先落 `retain_queue`，攒到阈值再统一「抽取 → 判重 → 合并/取代」：

| 参数 | 默认 | 说明 |
|---|---|---|
| `MEMVAULT_Q_MIN` | 5 | 条数到了就刷 |
| `MEMVAULT_Q_CHARS` | 1500 | 字数到了就刷 |
| `MEMVAULT_Q_AGE_MIN` | 0 | 按时间触发；**0 = 关闭**，只按条数/字数刷 |
| `MEMVAULT_Q_CAP` | 50 | 硬上限，必刷 |
| `MEMVAULT_Q_SIM_DUP` | 0.93 | 余弦 ≥ 此值 → 判重跳过 |
| `MEMVAULT_Q_SIM_NEW` | 0.80 | 0.80–0.93 → 新建并 supersede 旧卡 |

> **`recall` 会同时搜暂存**：返回体里 `cards` 是已入库的卡，`pending_hits` 是还没出库的
> 片段（带 `agent` 与 `score`，跨 agent 可见）。所以「字数不够还没刷出去」的东西也能被问到。

三个工具配合：

- `pending(limit)` —— 看别人刚写了什么还没提炼（多 agent 协作的可见性）
- `queue_stats()` —— 各 agent 攒了多少、最老一条多久
- `flush(force)` —— 立刻提炼，不等阈值

`memvault-qflush.timer` 每 10 分钟看一次，够了就刷；队列刷出的卡是 `active`
（`owner=agent`），不需要人工批准 —— 它们已经过判重与取代，不是碎片。


## 致谢 / Credits

这个项目的**代码是原创的，设计是站在别人肩膀上的**。下面逐条写清楚「抄了什么、抄自哪里」，并按许可证保留对上游的感谢。

### 概念与设计借鉴

| 借鉴自 | 抄了哪些想法 | 我们的改动 |
|---|---|---|
| [**Hindsight**](https://github.com/vectorize-io/hindsight) | bank 作为硬隔离边界、consolidation（把零散事实归纳成观察）、mental model 定时重写、MCP 工具面的形状、coding-agent 的「按仓库一个 bank」思路 | 只保留「卡片 + 证据」这条主线，去掉图谱与心智模型的重实现，换成可追原文的 span 模型 |
| [**Mem0**](https://github.com/mem0ai/mem0) | 增量记忆操作（ADD / UPDATE / DELETE）、相似度去重后再落库、把「提取」和「存储」分离 | 提取结果一律先进 `proposal`，人工批准才转正；去重阈值与温度衰减自己调 |
| [**Letta / MemGPT**](https://github.com/letta-ai/letta) | 常驻层（resident，固定预算、每轮全给）与检索层（archival，按问取）的两段式上下文 | 常驻层用 SQL 视图现算（画像 / 本月 / 线索 / 便签 / 今天），不做 LLM 摘要，省 token |
| [**OpenViking**](https://github.com/volcengine/OpenViking) | `viking://` 风格的资源层次、span → card → evidence 的分层思路 | 保留 `memvault-shim` 兼容其 API 形状，内部完全换成自己的表结构 |
| Okapi BM25（Robertson & Zaragoza, 2009） | 稀疏检索与 logistic 归一 | 与向量分线性融合，权重 0.65 / 0.35 是实测调出来的 |
| Reciprocal Rank Fusion（Cormack et al., 2009） | 多路召回融合 | 作为重排器不可用时的兜底策略（`rrf` passthrough） |

### 依赖与模型

| 项目 | 用途 |
|---|---|
| [**BAAI BGE-M3**](https://huggingface.co/BAAI/bge-m3) | 1024 维多语向量，中文效果好、单模型搞定稠密检索 |
| [**BAAI bge-reranker-v2-m3**](https://huggingface.co/BAAI/bge-reranker-v2-m3) | 交叉编码器精排；ONNX int8 量化后 CPU 上 10 条候选约 0.2s |
| [**HuggingFace Text Embeddings Inference**](https://github.com/huggingface/text-embeddings-inference) | 重排与向量的服务运行时（CPU / ORT 后端） |
| [**ONNX Runtime**](https://github.com/microsoft/onnxruntime) | int8 量化推理 |
| [**SQLite / FTS5**](https://www.sqlite.org/fts5.html) | 存储与 BM25 全文检索，单文件、可备份、可 grep |
| [**DeepSeek**](https://www.deepseek.com/) | 记忆抽取与归纳的默认模型；`reasoning_effort` 分档控成本 |
| [**NumPy**](https://numpy.org/) | 向量相似度计算 |
| [**Forgejo**](https://forgejo.org/) | 自建代码托管（本项目的私有开发仓库就放在它上面） |

### 特别说明

- 以上项目**没有被复制代码**，只借鉴了设计；如有个别函数签名保持一致（例如 MCP 的 JSON-RPC 形状、OpenAI 的 `/v1/chat/completions` 与 `/v1/responses` 协议），那是为了兼容公开协议，不是为了抄实现。
- 每个依赖的许可证以各自仓库为准；本项目在分发时同样保留它们的版权声明。
- 如果上面漏掉了谁，那是我的疏忽，不是不认账 —— 提 issue 我补上。

## License

MIT
| **记坑** | 主路径是 `pitfall_add`（MCP）：必须带修法，认出命令直接 active、否则进 proposal；同一报错签名重复写只加 `hits`。辅助：`scripts/collectors/pitsync.js` 从会话里捞 `isError=true` 自动配对，**默认关闭**（配错的修法比没有更糟） |
