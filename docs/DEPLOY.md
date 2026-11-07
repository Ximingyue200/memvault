# 部署

三种人看这篇：只想跑起来看一眼的、打算长期用的、要接到自己 agent 上的。按需跳。

---

## 0. 先说清楚：默认用的是"演示 API"

仓库里 `llm.base` 的默认值指向 **`https://text.pollinations.ai/openai`** —— 一个免费的公共
OpenAI 兼容端点。它不需要 key，所以 `git clone` 完就能直接抽卡、能跑 dream，不用先注册任何账号。

**但它有三个问题**：慢（单次 1.5–13 秒）、会限流（并发或高频直接 402 `FREE_LIMIT`）、随时可能
挂掉或改规则。**只适合验证流程，不适合日常用。** 正式用请换成自己的上游 —— 见第 3 节，两分钟的事。

面板第一次打开时底部会有一条黄色的提示，点「知道了」就不再出现（存在浏览器 localStorage）。

### 演示模式下会发生什么

- **对话/问答**：能正常跑，就是慢。
- **抽卡（learn）**：免费端点被限流（402）或返回空时，会自动退回一个本地规则抽取器
  （`memvault/demoextract.py`，按句切、留「有事实感」的句子、归成 note 卡）。所以你能亲眼看到
  卡片进库、检索能命中 —— 但那不是模型在理解，只是让流程能走通。
- **做梦 / 编年史 / 画像**：**需要真模型**。演示端点写不出连贯的长文，这部分基本是空的。
- 单段原文 **少于 200 字不抽卡**（`learn.MIN_CHARS`），所以别拿一句话测试。
- 没有向量服务时：卡片照常入库，只是标记「缺向量」，检索退化成 BM25 + LIKE。
  日志里会出现 `[warn] 向量补写失败（不影响入库）`，不影响游标推进。

---

## 1. 最快跑起来（3 步）

```bash
git clone https://github.com/Ximingyue200/memvault.git && cd memvault
pip install -r requirements.txt      # 只有 numpy；不装也能跑，只是向量检索退化
./deploy.sh
```

`deploy.sh` 做的事：建 `./data/`、把 `MEMVAULT_DB` / `GW_DB` 指过去、起三个进程、打印地址。

起来之后：

| 入口 | 地址 | 说明 |
|---|---|---|
| 面板 | http://127.0.0.1:4174/admin/ | 记忆库、卡片、待审、定时任务、配置 |
| 网关 | http://127.0.0.1:4180/v1 | OpenAI 兼容，**带**记忆注入 |
| 纯净入口 | http://127.0.0.1:4182/v1 | 同样转发，**不带**注入（对比/排错用） |
| MCP | http://127.0.0.1:4181/mcp | `recall / retain / stats / report` 四个工具 |

停：`./deploy.sh stop`

> 首次启动会自动建表（37 张）+ 灌默认设置，不需要手动跑迁移。

---

## 2. 接你的 agent

### 2.1 走网关（自动记忆，推荐）

任何支持自定义 `base_url` 的客户端：

```
BASE_URL = http://<host>:4180/v1
API_KEY  = 任意非空字符串（网关自己不校验）
MODEL    = openai            # 或你在 settings 里配的模型名
```

它会在每轮把「常驻层 + 检索到的事实卡」拼进 system，回答完把这一轮写进 buffer，凌晨统一落库。
不想要注入就填 `:4182/v1`，行为跟直连上游一样。

### 2.2 走 MCP（只能挂 MCP 的 agent）

```
URL        = http://<host>:4181/mcp
transport  = streamable_http
```

四个工具：`recall(query, topk, min_score)` / `retain(text, context)` / `stats()` / `report()`。
**agent 写入默认是 proposal**，要在面板「待审」里批准才进检索 —— 这是为了防止多 agent 互相污染。

---

## 3. 换成自己的 API（重要）

默认演示端点只够验证。换成本地或云端任选，只要 OpenAI 兼容：

**DeepSeek（便宜、中文好）**
```bash
export MEMVAULT_LLM_URL=https://api.deepseek.com
export MEMVAULT_LLM_MODEL=deepseek-flash        # 或 deepseek-reasoner
export MEMVAULT_LLM_KEY=sk-你的key
./deploy.sh
```

**硅基流动 / OpenAI / 自建 vLLM / Ollama** 同理，改 `MEMVAULT_LLM_URL` + `MODEL` + `KEY` 即可
（Ollama 用 `http://127.0.0.1:11434/v1`，key 填 `ollama`）。

也可以不改环境变量，直接在面板改：**记忆库参数 → 全局 LLM 地址 / 全局 LLM 模型**，以及
「分任务接入」里给 learn / dream / summary 分别指定不同的 key 和模型（省钱用：提炼走便宜的，
做梦走贵的）。改完不用重启，下次调用生效。

### 向量服务（可选）

不配也能用：检索会退化成纯 BM25 + LIKE 兜底，短中文查询靠 `like` 也能命中，只是召回粗一点。
要向量检索就起一个 bge-m3 的 OpenAI 兼容 embeddings 服务：

```bash
export MEMVAULT_EMBED_URL=http://127.0.0.1:8081/v1/embeddings
export MEMVAULT_EMBED_MODEL=bge-m3
```

维度要跟库里一致（默认 1024）。换维度会让旧向量作废。

---

## 4. 定时任务（自动生长）

不装定时器也能用 —— 只是「存进去的东西不会自己变成卡片」。要自动生长，把这几个挂到 cron/systemd：

| 任务 | 建议频率 | 作用 |
|---|---|---|
| `python3 -m memvault.collect` | 每 10 分钟 | 把新对话切成 span |
| `python3 -m memvault.learn` | 每 30 分钟 | span → 事实卡（**花钱的那一步**） |
| `python3 -m memvault.dream` | 每小时 | 编年史 / 画像 / 自述 |
| `python3 -m memvault.focus` | 每小时 | 重建「未完成的事」 |
| `python3 -m memvault.soften` | 每天 | 软化过期的强断言 |
| `python3 -m memvault.supersede` | 每天 | 处理取代关系 |
| `python3 -m memvault.backup` | 每天 | SQLite 在线备份 |

成本参考：演示端点免费；DeepSeek 下一条 learn 约 ¥0.005–0.01，一天几十条的量级 ¥1 以内。

---

## 5. 常见问题

**起不来，报 `unable to open database file`**
没设 `MEMVAULT_DB`，代码默认路径是作者机器上的 `/opt/memvault/data/...`。用 `./deploy.sh` 会自动设好，
手工起的话要自己 export。

**面板打不开 / 接口 502 `cradle unreachable`**
网关把面板请求代理到 `127.0.0.1:4174`。如果你改了 server 的端口，面板就会 502 —— 保持默认端口，
或者同时改 `GW_CRADLE_PORT`。

**搜不到东西**
两种原因：①没有向量服务且查询是长中文句子（BM25 切不开中文）—— 配 bge-m3，或把查询压到 5 个字内；
②新写入的是 proposal，还没批。

**回答明显是编的**
看「完形强度」和卡片的 `confidence`。高完形强度会补全叙事，但每条结论都应该能顺着证据链回到 span，
在面板卡片详情里能看到原始证据。

**能不能公网开**
不建议裸奔：面板的 `/admin/config`、`/admin/providers` 没有鉴权（密钥已打码，但拓扑全暴露）。
真要从外面访问，前面套一层带 Basic Auth 的反代，或者走 SSH 隧道。
