# 架构

## 一句话

把历史对话切成片段（`spans`），提炼成卡片（`cards`），
再在每一轮对话开始前，把相关的卡片塞回上下文。

## 三层

```
┌─────────────────────────────────────────────────────────┐
│  入口层                                                   │
│    gateway.py   OpenAI 兼容反代 :4180                     │
│    server.py    管理面板后端   :4174                      │
│    shim.py      OpenViking API 兼容 :4175                 │
│    cron_job.py  定时任务（dream/focus/procedure/scene）    │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  生成层（把原始材料变成判断）                              │
│    dream.py      从卡片提炼判断（不是事实，是推断）        │
│    scene.py      跨时间跨主题的叙事                       │
│    procedure.py  遇到这类问题他怎么做（套路）              │
│    summary.py    按问题生成的知识摘要                     │
│    chronicle.py  年/季/月/日逐级压缩                      │
│    profile.py    用户画像                                 │
│    focus.py      未完成的事                               │
│    rollup.py     编年史的上卷                             │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  注入层（决定 AI 每轮看到什么）                            │
│    resident.py   常驻层：五段固定内容                     │
│    handoff.py    无缝换窗：衔接上一会话                   │
│    shim.py       recall 检索块                           │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│  存储层                                                   │
│    store.py      SQLite 封装，唯一写入口                  │
│    search.py     向量 + BM25 混合检索，带热度加权          │
│    heat.py       热度衰减                                 │
│    soften.py     冷卡压缩                                 │
│    schema.py     配置项元数据                             │
└─────────────────────────────────────────────────────────┘
```

## 数据流

```
对话原文
   │
   ├─→ spans          切段后的原始片段（唯一可回核的真相源）
   │      │
   │      └─→ cards        提炼成一句话（带证据链 card_evidence）
   │             │
   │             ├─→ dreams     判断 / 推断
   │             ├─→ scenes     场景
   │             ├─→ procedures 套路
   │             └─→ summaries  摘要
   │
   └─→ conv_log       网关实时写入（当前只写，不提炼 ← 已知缺口）
```

## 表清单

| 表 | 行数 | 作用 |
|---|---|---|
| `spans` | 54,402 | 原始对话片段，唯一可回核的真相源 |
| `cards` | 12,582 | 提炼出的卡片 |
| `card_evidence` | 19,226 | 卡片 ↔ span 的证据链 |
| `cards_fts` | 12,582 | trigram 全文索引 |
| `vectors` | 12,510 | bge-m3 embedding（1024 维，L2 归一化） |
| `card_recall` | 1,577 | 卡片被检索命中的记录 |
| `cards_rejected` | 14 | 被质检门拒掉的卡（可观测，不静默丢） |
| `dreams` | 24 | 推断（draft / confirmed / rejected / merged） |
| `scenes` | 28 | 场景 |
| `procedures` | 23 | 程序性记忆 |
| `summaries` | 1 | 知识摘要 |
| `chronicles` | 26 | 编年史（年/季/月/日） |
| `focus` | 21 | 未完成的事 |
| `threads` | 73 | 跨会话的线索 |
| `thread_state` | 76 | 线索状态 |
| `self_notes` | 12 | 自我笔记 |
| `edges` | 22,499 | 卡片之间的关联 |
| `map2d` / `map3d` | 12,217 | 降维坐标（可视化用） |
| `conv_log` | 82 | 网关实时写入的原始对话 |
| `injections` | 257 | 每次注入的审计记录 |
| `settings` | 48 | 配置项（k/v） |
| `providers` / `provider_models` | 2 / 3 | 上游模型供应商 |

## 五条契约

写在 `store.py` 的注释里，代码逐条对应：

1. **单向流** — `spans → cards → dreams/scenes`，不回头改上游
2. **不可变** — 卡片改写要留版本，不原地覆盖
3. **证据闭合** — `content[start:end] == quote`，否则拒收（进 `cards_rejected`）
4. **拒收可观测** — 拒掉的卡留痕，不是静默丢
5. **supersede 时间线** — 新卡替代旧卡时留链，不删

## 关键设计决策

### 为什么纯标准库

`sqlite3` + `http.server` + `urllib` 就够。没有 FastAPI、没有 ORM。
好处：容器里零依赖，`python3 -m memvault.gateway` 直接起。
代价：路由要手写（`do_GET` 里一串 `if p == ...`），SSE 要自己分帧。

### 为什么 FTS5 用 trigram

SQLite 的 `simple` tokenizer 不切中文 —— 整句当一个词，中文检索基本失效。
`trigram` 按三字符切片，中文能命中，代价是索引大 3 倍。

### 为什么 embedding 必须 L2 归一化

`bge-m3` 输出非归一化向量，直接点积等于「长度 × 余弦」，
长文本会无脑占优。归一化后点积才等于余弦相似度。
**这一条不遵守，分数全废。**

### 为什么注入只给 title + abstract，不给 content

一条卡片的 `content` 能到 3,000 字，8 条就撑爆上下文。
给 `title + abstract + when` 约 200 字/条，8 条 = 1,600 字（占上下文 1/40）。

### 为什么检索失败必须放行

网关不能成为单点故障。`search()` 抛异常 → 打日志 → 继续转发请求。

### 为什么热度要分档截断

`hot` 给 600 字，`warm`/`drowsy` 给 200 字，`cold`/`dead` 直接跳过。
不分档的话，25 条卡就能吃掉一半上下文。

## 已知缺口

1. **`conv_log → spans → cards` 是断的**
   网关实时写 `conv_log`，但没有任何代码把它转成 `spans` 或 `cards`。
   所以「走网关就自动记住新东西」不成立，能查到旧事全靠 11-02 之前一次性导入的 49,509 条消息。

2. **配置项要改两处**
   `memvault/schema.py` 的 `SETTING_META`（后端）+ `admin-panel/js/config-schema.js` 的 `CONFIG_PAGES`（前端渲染）。
   漏一处，值存进去了但页面不显示。

3. **`scene` 去重靠 slug，而 slug 含 title**
   `md5(title + span)[:12]` —— 同一件事换个标题就是新 slug，`ON CONFLICT DO NOTHING` 挡不住。
