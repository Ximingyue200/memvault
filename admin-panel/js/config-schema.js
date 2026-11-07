// ============================================================
// config-schema.js — 全部配置项的权威登记表（单一事实源）v1.6.2
//
// 与后端 config.py 的 CONFIG_SCHEMA 一一对应。每个 key 都登记：
//   label / type / def / input / desc / hasDefault（可恢复默认的 prompt）
//
// 本次改版（归类修正）：
//   · 新登记 reasoning_effort（思考强度）→ 网关「对话行为」组
//     —— 后端一直有，此前面板任何页面都改不了。
//   · 新登记 openrouter_provider_order_enabled → 供应商「默认模型与路由」组
//   · default_title_model / prompt_title_summary：供应商页 → 网关「对话行为」组
//     —— 标题生成与供应商无关，是网关的对话层功能。
//   · merge_retention_days / merge_min_keep：记忆机制 → Dream「产物清理」组
//     —— 它们管的是 Dream merge 产物。
//   · last_dream_date 移出 Dream 参数组（它是运行状态，不是旋钮）；
//     未编排 → 自动落入「全部配置」的兜底区，仍可查看。
// ============================================================

export const CONFIG_META = {
  // ===== memvault（本网关自己的参数） =====
  "gw.topk":            { label:"注入条数", type:"int", def:"6", input:"int", group:"网关", desc:"每次检索注入多少条记忆。太多占 token，太少易遗漏。建议 3-10，默认 6。" },
  "gw.min_score":       { label:"相似度门槛", type:"float", def:"0.30", input:"float", group:"网关", desc:"低于此分数的卡不注入。0.30 宽松（召回多），0.55 只留强命中。建议 0.25-0.50。" },
  "gw.upstream":        { label:"上游地址", type:"text", def:"api.deepseek.com", input:"text", group:"网关", desc:"转发目标主机（不带 https:// 和路径）。改后需重启网关进程才生效。" },
  "inject.enabled":     { label:"自动注入", type:"bool", def:"1", input:"bool", group:"注入", desc:"关闭后网关不再自动注入记忆，请求照常转发。等价于把网关变成纯反代。" },
  "dream.enabled":      { label:"做梦开关", type:"bool", def:"1", input:"bool", group:"梦境", desc:"关闭后定时器不再自动做梦。手动触发仍可用。" },
  "dream.days":         { label:"回看天数", type:"int", def:"90", input:"int", group:"梦境", desc:"做梦时读最近多少天的卡片。越大素材越全，越小越聚焦近期。默认 90。" },
  "dream.per_run":      { label:"每次生成几条梦", type:"int", def:"2", input:"int", group:"梦境", desc:"一次做梦生成多少条判断。太多会互相撞题。建议 1-3，默认 2。" },
  "dream.max_cats":     { label:"主题分组数", type:"int", def:"10", input:"int", group:"梦境", desc:"按分类取多少组素材。默认 10。" },
  "dream.cards_per_cat":{ label:"每组抽卡数", type:"int", def:"4", input:"int", group:"梦境", desc:"每个分类抽几张卡。必须 >= 2（=1 会除零）。默认 4。" },
  "dream.auto_confirm": { label:"自动收下梦", type:"bool", def:"0", input:"bool", group:"梦境", desc:"打开后新梦直接标为已确认，跳过人工审。建议保持关闭。" },
  "dream.cron":         { label:"人工梦定时", type:"text", def:"*-*-* *:07:00 Asia/Shanghai", input:"text", group:"梦境", desc:"systemd OnCalendar 格式，必须带时区后缀。现在每小时第 7 分检查一次「料够不够」，料够才真跑。改完自动重建 timer。" },
  "dream.min_fresh":    { label:"新卡够多少才做梦", type:"int", def:"30", input:"int", group:"梦境", desc:"自上次做梦以来新增的卡达到这个数才触发生成。调小=更勤但更碎，调大=更攒但更慢。默认 30。" },
  "dream.max_gap_days": { label:"最多隔几天没做梦", type:"int", def:"7", input:"int", group:"梦境", desc:"即使新卡不够，距上次做梦超过这么多天也强制跑一次，避免长期不整理。默认 7。" },
  "dream.machine_cron": { label:"机器梦定时", type:"text", def:"*-*-* *:17:00 Asia/Shanghai", input:"text", group:"机器梦", desc:"每小时第 17 分检查一次，距上次超过 machine_max_gap_days 天才真跑。" },
  "dream.machine_max_gap_days": { label:"机器梦最多隔几天", type:"int", def:"14", input:"int", group:"机器梦", desc:"距上次机器梦超过这么多天就真跑一次。机器梦看的是 AI 自己最近的行为，不需要新卡。默认 14。" },
  "focus.cron":         { label:"任务重建定时", type:"text", def:"*-*-* 00/2:20:00", input:"text", group:"任务", desc:"多久重建一次任务清单。默认每 2 小时。" },
  "focus.hours":        { label:"任务回溯小时", type:"int", def:"72", input:"int", group:"任务", desc:"重建任务时回看多少小时的会话。默认 72。" },
  "dream.model":        { label:"梦境模型", type:"text", def:"deepseek-flash", input:"text", group:"模型", desc:"做梦用的模型 id。" },
  "llm.model":          { label:"全局模型", type:"text", def:"deepseek-flash", input:"text", group:"模型", desc:"各功能未单独指定时的全局模型 id。" },
  "llm.base":           { label:"全局 API 地址", type:"text", def:"https://api.deepseek.com", input:"text", group:"模型", desc:"全局 LLM 接口地址。" },
  "learn.api_key":   { label:"提炼独立 Key", type:"text", def:"", input:"text", group:"分任务接入", desc:"留空跟随全局 MEMVAULT_LLM_KEY。不同任务可以用不同账号的 key。" },
  "learn.api_base":  { label:"提炼独立上游", type:"text", def:"", input:"text", group:"分任务接入", desc:"留空跟随全局。例：https://api.deepseek.com/v1 或内网另一个网关。" },
  "learn.model":     { label:"提炼独立模型", type:"text", def:"", input:"text", group:"分任务接入", desc:"留空跟随全局 llm.model。" },
  "dream.api_key":   { label:"梦境独立 Key", type:"text", def:"", input:"text", group:"分任务接入", desc:"留空跟随全局。" },
  "dream.api_base":  { label:"梦境独立上游", type:"text", def:"", input:"text", group:"分任务接入", desc:"留空跟随全局。" },
  "summary.api_key": { label:"摘要独立 Key", type:"text", def:"", input:"text", group:"分任务接入", desc:"编年/浓缩/自述/柔化共用。留空跟随全局。" },
  "summary.model":   { label:"摘要独立模型", type:"text", def:"", input:"text", group:"分任务接入", desc:"留空跟随全局 llm.summary.model。" },
  "llm.summary.model":  { label:"摘要模型", type:"text", def:"", input:"text", group:"模型", desc:"生成知识摘要用的模型。留空跟随全局。" },
  "llm.reasoning_effort":     { label:"后台全局推理等级", input:"select", def:"", options:[
    { value:"", label:"跟随模型默认（不指定）" }, { value:"none", label:"none · 完全不思考" },
    { value:"low", label:"low · 想一点" }, { value:"medium", label:"medium · 中等" },
    { value:"high", label:"high · 充分思考" }, { value:"max", label:"max · 最大" },
  ], desc:"后台任务（提炼/摘要）的默认思考强度。留空 = 跟随上游模型默认。" },
  "dream.reasoning_effort":   { label:"梦境推理等级", input:"select", def:"max", options:[
    { value:"", label:"跟随全局" }, { value:"none", label:"none · 不思考" },
    { value:"low", label:"low" }, { value:"medium", label:"medium" },
    { value:"high", label:"high" }, { value:"max", label:"max · 最大（当前）" },
  ], desc:"做梦是联想任务，值得给它思考。建议 high 或 max。" },
  "learn.reasoning_effort":   { label:"提炼推理等级", input:"select", def:"max", options:[
    { value:"", label:"跟随全局" }, { value:"none", label:"none · 不思考（最省）" },
    { value:"low", label:"low" }, { value:"medium", label:"medium" },
    { value:"high", label:"high" }, { value:"max", label:"max · 最大（当前）" },
  ], desc:"卡片提炼 / 分类 / 取代判断。max 更准但更慢更贵。" },
  "llm.summary.reasoning_effort": { label:"摘要推理等级", input:"select", def:"low", options:[
    { value:"", label:"跟随全局" }, { value:"none", label:"none · 不思考" },
    { value:"low", label:"low（当前）" }, { value:"medium", label:"medium" },
    { value:"high", label:"high" }, { value:"max", label:"max" },
  ], desc:"编年 / 浓缩 / 柔化 / 自述这类写作任务。" },
  // —— 记忆基础 ——
  memory_enabled:        { label:'记忆系统总开关', type:'bool', def:'true', input:'bool', desc:'关闭后不提取记忆、不注入碎片，但对话仍正常保存转发。' },
  extract_interval:      { label:'提取间隔（轮）', type:'int', def:'5', input:'int', desc:'每隔多少轮对话提取一次记忆。越小越频繁但更费 token。建议 3–10。' },
  max_inject:            { label:'每次注入条数', type:'int', def:'15', input:'int', desc:'每次对话注入多少条语义相关碎片。太多占 token，太少易遗漏。建议 5–30。' },
  locked_inject_ratio:   { label:'锁定保底占比', type:'float', def:'0.2', input:'float', desc:'命中的锁定记忆至少占据注入名额的比例。0 = 与普通碎片纯竞争，1 = 全部名额都可被锁定记忆保底占用。' },
  semantic_threshold:    { label:'语义搜索阈值', type:'float', def:'0.25', input:'float', desc:'低于此相似度的碎片不会被注入。建议 0.15–0.5。' },
  dedup_threshold:       { label:'去重相似度阈值', type:'float', def:'0.55', input:'float', desc:'新碎片与已有碎片文字重叠超过此值判为重复、不存储。建议 0.4–0.7。' },
  default_memory_model:  { label:'记忆提取模型', type:'text', def:'', input:'model', desc:'记忆提取用的模型。建议小模型（如 Haiku）省成本。留空跟随聊天模型。' },
  default_embedding_model:{ label:'嵌入模型', type:'text', def:'', input:'model', desc:'向量嵌入模型，决定语义搜索质量。更换后需在「记忆碎片」页执行向量迁移。' },
  prompt_memory_extract: { label:'记忆提取提示词', type:'text', def:'', input:'prompt', hasDefault:true, desc:'指导模型如何从对话中抽取记忆碎片。留空用内置默认。' },

  // —— 热度系统 ——
  heat_half_life_normal:  { label:'普通记忆半衰期（天）', type:'float', def:'3', input:'float', desc:'普通碎片（重要度 < 分界线）的热度半衰期。越大淡忘越慢。' },
  heat_half_life_important:{ label:'重要记忆半衰期（天）', type:'float', def:'7', input:'float', desc:'重要碎片（重要度 ≥ 分界线）的热度半衰期。' },
  heat_recall_extend:     { label:'召回延长倍率', type:'float', def:'0.5', input:'float', desc:'每次被召回时半衰期延长的倍率。0.5 = 延长 50%。' },
  heat_threshold_high:    { label:'高热度阈值（全文注入）', type:'float', def:'0.7', input:'float', desc:'热度高于此值的碎片全文注入对话。' },
  heat_threshold_medium:  { label:'中热度阈值（摘要注入）', type:'float', def:'0.3', input:'float', desc:'热度在此值与高阈值之间的碎片只注入摘要；低于此值跳过。' },
  heat_importance_line:   { label:'重要度分界线', type:'int', def:'8', input:'int', desc:'重要度 ≥ 此值的碎片使用更长的半衰期。' },
  heat_emotion_line:      { label:'高情绪分界线', type:'int', def:'6', input:'int', desc:'情绪浓度 ≥ 此值的碎片视为高情绪，自动锁定条件更宽松、衰减更慢。' },
  heat_medium_truncate:   { label:'中热度摘要截断字数', type:'int', def:'60', input:'int', desc:'中热度碎片摘要注入时截断到多少字。' },
  cleanup_heat_threshold: { label:'清理低热度阈值', type:'float', def:'0.15', input:'float', desc:'热度低于此值且又冷又老的碎片会被清理（软删除）。' },

  // —— 自动软化 ——
  auto_soften_enabled:    { label:'自动软化', type:'bool', def:'true', input:'bool', desc:'是否每晚温柔模糊老旧碎片：细节褪色、要点保留（模拟人脑遗忘）。' },
  auto_soften_daily_limit:{ label:'每日软化上限', type:'int', def:'10', input:'int', desc:'每天最多软化多少条，防止一次性大量重写。' },
  auto_soften_min_age:    { label:'软化最小天数', type:'int', def:'5', input:'int', desc:'碎片至少存在多少天才会被考虑软化。' },
  soften_cooldown_days:   { label:'软化冷却天数', type:'int', def:'21', input:'int', desc:'同一碎片两次软化之间的冷却期。' },

  // —— 自动锁定 / 退役 ——
  autolock_access_count:  { label:'自动锁定·召回次数', type:'int', def:'10', input:'int', desc:'普通碎片被召回多少次后自动锁定保护。' },
  autolock_diversity:     { label:'自动锁定·话题多样性', type:'int', def:'5', input:'int', desc:'普通碎片被多少个不同话题召回后自动锁定。' },
  autolock_emo_access:    { label:'自动锁定·高情绪召回', type:'int', def:'6', input:'int', desc:'高情绪碎片被召回多少次后自动锁定（更宽松）。' },
  autolock_emo_diversity: { label:'自动锁定·高情绪多样性', type:'int', def:'3', input:'int', desc:'高情绪碎片被多少个不同话题召回后自动锁定。' },
  lock_retire_enabled:    { label:'锁定退役', type:'bool', def:'true', input:'bool', desc:'长期未召回的「自动锁定」碎片是否自动退役（解锁但不删除，让出注入空间）。手动锁定永不退役。' },
  lock_retire_days:       { label:'锁定退役天数', type:'int', def:'90', input:'int', desc:'自动锁定碎片超过多少天未召回则退役。' },

  // —— Dream 产物清理（从「记忆机制」移入 Dream 页）——
  merge_retention_days:   { label:'合并产物保留天数', type:'int', def:'90', input:'int', desc:'被 Dream 合并后的旧碎片保留多少天后清理。' },
  merge_min_keep:         { label:'合并保留下限', type:'int', def:'20', input:'int', desc:'即便符合清理条件，也至少保留多少条碎片。' },

  // —— 场景注入（Dream 产出）——
  scene_inject_enabled:   { label:'场景注入', type:'bool', def:'true', input:'bool', desc:'是否在聊天时注入 Dream 整合出的叙事场景，提供长期记忆的宏观视角。' },
  scene_inject_limit:     { label:'场景注入条数', type:'int', def:'2', input:'int', desc:'每次最多注入多少条场景。' },
  scene_inject_min_sim:   { label:'场景注入相似度', type:'float', def:'0.5', input:'float', desc:'场景与当前对话的最低相似度。' },

  // —— Dream ——
  dream_enabled:          { label:'梦境系统', type:'bool', def:'true', input:'bool', desc:'AI 通过做梦融合记忆场景、生成前瞻。关闭后不再自动触发梦境（犯困提示一并静默）；「状态」页的手动开始 Dream 不受影响。' },
  dream_model:            { label:'Dream 模型', type:'text', def:'', input:'model', desc:'Dream 记忆整合用的模型。' },
  prompt_dream:           { label:'Dream 提示词', type:'text', def:'', input:'prompt', hasDefault:true, desc:'指导 Dream 如何清理、融合碎片并推断前瞻。留空用内置默认。' },
  dream_drowsy_threshold: { label:'犯困碎片阈值', type:'int', def:'30', input:'int', desc:'未处理碎片积累超过此数量后，AI 开始表现犯困、提示该睡了。' },
  last_dream_date:        { label:'上次 Dream 日期', type:'text', def:'', input:'text', desc:'运行状态，自动更新。在 Dream「状态」页查看，一般无需手动改。' },

  // —— 用户/助手画像 ——
  user_profile:           { label:'用户画像', type:'text', def:'', input:'prompt', hasDefault:false, desc:'AI 对用户的整体认知，注入 system prompt。由每日整理自动更新，也可手动编辑。' },
  prompt_user_profile:    { label:'画像更新提示词', type:'text', def:'', input:'prompt', hasDefault:true, desc:'指导每日整理如何更新用户画像。留空用内置默认。' },

  // —— 日历 / 每日整理 ——
  calendar_inject_enabled:{ label:'日历注入', type:'bool', def:'true', input:'bool', desc:'是否把日/周/月/季/年总结注入 system prompt，让 AI 知道最近发生了什么。' },
  default_digest_model:   { label:'每日整理模型', type:'text', def:'', input:'model', desc:'日页面与各级总结生成用的模型。建议小模型。' },
  prompt_daily_digest:    { label:'每日整理提示词', type:'text', def:'', input:'prompt', hasDefault:true, desc:'每日整理主提示词。留空用内置默认。' },
  prompt_daily_digest_page:{ label:'日页面生成提示词', type:'text', def:'', input:'prompt', hasDefault:true, desc:'生成单日页面的提示词。' },
  prompt_weekly_summary:  { label:'周总结提示词', type:'text', def:'', input:'prompt', hasDefault:true, desc:'生成周总结的提示词。' },
  prompt_monthly_summary: { label:'月总结提示词', type:'text', def:'', input:'prompt', hasDefault:true, desc:'生成月总结的提示词。' },
  prompt_period_summary:  { label:'季度/年总结提示词', type:'text', def:'', input:'prompt', hasDefault:true, desc:'生成季度与年度总结的提示词。' },

  // —— 上下文压缩 ——
  auto_compress_enabled:  { label:'自动压缩', type:'bool', def:'true', input:'bool', desc:'对话过长时自动把前文压成摘要，省 token 又不失忆。' },
  auto_compress_msg_limit:{ label:'压缩触发条数', type:'int', def:'40', input:'int', desc:'对话消息数超过此值触发压缩。' },
  auto_compress_token_limit:{ label:'压缩触发 token', type:'int', def:'30000', input:'int', desc:'估算 token 超过此值触发压缩。' },
  auto_compress_keep_last:{ label:'压缩后保留原文条数', type:'int', def:'4', input:'int', desc:'压缩时末尾保留多少条原文不被压缩。' },
  compress_ratio:        { label:'压缩输出比例', type:'float', def:'0.35', input:'float', desc:'摘要目标长度占待压原文的比例。0.35 表示约 35%。' },
  compress_output_max:   { label:'压缩输出上限', type:'int', def:'4000', input:'int', desc:'摘要输出的 token 上限，防止摘要过长拖累每轮请求。' },
  default_compress_model: { label:'压缩模型', type:'text', def:'', input:'model', desc:'上下文压缩用的模型。建议小模型。' },
  prompt_compress:        { label:'压缩提示词', type:'text', def:'', input:'prompt', hasDefault:false, desc:'指导如何压缩前文。留空用内置默认。' },

  // —— 无缝换窗 handoff ——
  handoff_enabled:        { label:'无缝换窗', type:'bool', def:'true', input:'bool', desc:'新对话自动衔接上一个对话——注入全程概要 + 结尾原文，避免「失忆」。仅新对话首条注入一次。' },
  handoff_tail_count:     { label:'衔接结尾原文条数', type:'int', def:'6', input:'int', desc:'衔接时带上一对话结尾多少条原文（约 3 轮）。' },
  handoff_summary_model:  { label:'衔接概要模型', type:'text', def:'', input:'model', desc:'生成衔接概要的模型。留空跟随压缩模型。' },

  // —— 工具抽屉 / 外部 MCP ——
  tool_drawer_enabled:    { label:'工具抽屉', type:'bool', def:'false', input:'bool', desc:'内部工具是否走向量路由按需展开。关闭则所有内部工具每次都传给模型。' },
  mcp_mode:               { label:'外部 MCP 模式', type:'text', def:'auto', input:'select', options:['off','auto','manual'], desc:'off=全禁用，auto=按语义自动路由，manual=只启用手动选择的。只影响配置来源的外部抽屉。' },
  mcp_servers:            { label:'外部 MCP 服务器', type:'text', def:'', input:'json', desc:'外部 MCP server 列表（JSON 数组）。在「工具抽屉」页用专用编辑器维护。' },
  mcp_manual_ids:         { label:'手动 MCP 选择', type:'text', def:'', input:'json', desc:'manual 模式下启用的 MCP 服务器 ID 列表（JSON 数组）。' },
  ext_drawer_threshold:   { label:'外部抽屉相似度阈值', type:'float', def:'0.40', input:'float', desc:'外部工具与对话内容的语义相似度门槛，低于此值不展开。' },
  ext_drawer_max_open:    { label:'外部抽屉同开上限', type:'int', def:'3', input:'int', desc:'单次对话最多同时展开几个外部工具抽屉。' },
  reminder_tools_enabled: { label:'提醒工具', type:'bool', def:'true', input:'bool', desc:'控制提醒系统的 4 个工具，关闭后传统模式与工具抽屉中均不可见。' },

  // —— 兼容与回退（W2-03）——
  memory_event_ledger_write_enabled: { label:'事件账本原子写入', type:'bool', def:'true', input:'bool', desc:'开启后一轮对话作为单个事务写入事件账本，带上项目、轮次与用量身份，任一步失败整体回滚。关闭则退回旧的两次独立写入（新字段留空）。出问题时关掉即可回退，不需要发版。' },
  session_identity_v2_enabled: { label:'会话身份 v2', type:'bool', def:'false', input:'bool', desc:'前端没传对话 ID 时如何生成会话身份。关闭＝沿用「首条消息内容哈希」，同样的开场白会被并成同一段会话。开启＝改用随机 ID 并回传给前端，会话不再串线；但尚未采纳回传 ID 的第三方客户端会每轮换会话，工具抽屉状态与 OpenRouter 黏性路由会断。前端适配完成前保持关闭。' },
  sync_delete_privacy_capability_enabled: { label:'同步删除隐私能力', type:'bool', def:'true', input:'bool', desc:'关闭后能力端点宣称 ready=false，客户端保持休眠；删除章、清理与迟到写保护仍然继续生效。' },

  // —— 联网搜索 ——
  search_engine:          { label:'搜索引擎', type:'text', def:'', input:'engine', desc:'联网搜索使用的引擎。' },
  search_api_key:         { label:'搜索 API Key', type:'text', def:'', input:'pass', desc:'所选搜索引擎的 API Key（local 类型引擎无需）。' },
  search_max_results:     { label:'搜索结果条数', type:'int', def:'5', input:'int', desc:'每次搜索返回多少条结果。' },

  // —— 模型默认与路由 ——
  default_chat_model:     { label:'默认聊天模型', type:'text', def:'', input:'model', desc:'未在前端指定时使用的默认聊天模型。' },
  openrouter_provider_order_enabled: { label:'OpenRouter 锁定 Anthropic', type:'bool', def:'false', input:'bool', desc:'走 OpenRouter 时优先路由到 Anthropic 官方 provider，Claude 模型更稳定。（v1.6.1 新登记：此前只能改数据库）' },

  // —— 网关 / 对话行为 ——
  default_title_model:    { label:'标题生成模型', type:'text', def:'', input:'model', desc:'自动生成对话标题用的模型。建议小模型。' },
  prompt_title_summary:   { label:'标题生成提示词', type:'text', def:'', input:'prompt', hasDefault:false, desc:'指导如何生成对话标题。留空用内置默认。' },
  reasoning_effort:       { label:'思考强度', type:'text', def:'off', input:'select', options:['off','auto','low','medium','high'], desc:'网关转发时的默认思考强度：客户端没带推理等级时用这个补上。off/auto = 不补，跟随模型默认。' },

  // —— 网关 / 性能 ——
  prompt_cache_enabled:   { label:'Prompt 缓存', type:'bool', def:'true', input:'bool', desc:'Claude 模型的显式缓存：重复的 system prompt 前缀只收 1/10 费用。非 Claude 自动跳过。' },
  prompt_cache_ttl:       { label:'Prompt 缓存 TTL', type:'text', def:'1h', input:'select', options:['5m','1h'], desc:'缓存保留时长。1h 适合慢节奏长对话；5m 写入稍便宜，适合高频连续使用。' },

  // —— 客户端同步项（不进功能页，仅「全部配置」可见）——
  user_nickname:          { label:'用户昵称', type:'text', def:'', input:'text', desc:'用户昵称。（同步给客户端，一般不在面板编辑）' },
  user_avatar:            { label:'用户头像', type:'text', def:'', input:'text', desc:'用户头像（URL 或标识）。（同步给客户端，一般不在面板编辑）' },
  assistant_avatar:       { label:'助手头像', type:'text', def:'', input:'text', desc:'助手头像（URL 或标识）。（同步给客户端，一般不在面板编辑）' },
  assistant_settings:     { label:'助手设置', type:'text', def:'', input:'json', desc:'助手相关设置（JSON）。（同步给客户端，一般不在面板编辑）' },
  custom_skills:          { label:'自定义技能', type:'text', def:'', input:'json', desc:'自定义技能列表（JSON）。（同步给客户端，一般不在面板编辑）' },
  quick_phrases:          { label:'快捷短语', type:'text', def:'', input:'json', desc:'快捷短语列表（JSON）。（同步给客户端，一般不在面板编辑）' },
  mcp_switches:           { label:'MCP 开关', type:'text', def:'', input:'json', desc:'各 MCP 的开关状态（JSON）。（同步给客户端，一般不在面板编辑）' },
  theme_preference:       { label:'主题偏好', type:'text', def:'', input:'text', desc:'客户端主题偏好。（同步给客户端，一般不在面板编辑）' },
};

// 各功能页的配置编排：master 总开关 + 分组旋钮
export const CONFIG_PAGES = {
  tools: {
    title: "工具抽屉", icon: "🧰",
    groups: [
      { title: "内部工具", desc: "内部工具是否走向量路由按需展开。关闭则所有内部工具每次都传给模型。", items: ["tools.enabled", "skills.enabled"] },
      { title: "外部 MCP", desc: "外部工具按语义相关性自动展开。外部 MCP 客户端尚未实现。", items: ["mcp.mode"] },
    ],
  },
  reminderTools: {
    title: "提醒工具", icon: "⏰",
    groups: [
      { title: "提醒工具", desc: "控制提醒系统的工具是否可见。当前网关无提醒实现。", items: ["reminder.enabled"] },
    ],
  },

  console: {
    groups: [
      { title:"🌐 网关", desc:"转发与注入的行为。改完即时生效，不用重启。", keys:["gw.topk","gw.min_score","gw.upstream"] },
      { title:"💉 注入", keys:["inject.enabled"] },
      { title:"🌙 梦境（人工梦：关于你）", master:"dream.enabled", keys:["dream.days","dream.per_run","dream.max_cats","dream.cards_per_cat","dream.auto_confirm","dream.min_fresh","dream.max_gap_days","dream.cron"] },
      { title:"🤖 机器梦（关于我）", keys:["dream.machine_max_gap_days","dream.machine_cron"] },
      { title:"📋 任务", keys:["focus.cron","focus.hours"] },
      { title:"🧠 模型", desc:"各功能可单独指定，留空跟随全局。", keys:["llm.model","llm.base","dream.model","llm.summary.model"] },
      { title:"🔑 分任务接入", desc:"提炼/梦境/摘要可以各用一套 key、模型与上游；留空 = 跟随全局。改动 60 秒内生效。", keys:["learn.api_key","learn.api_base","learn.model","dream.api_key","dream.api_base","summary.api_key","summary.model"] },
      { title:"💭 后台推理等级", desc:"提炼/梦境/摘要各自调云端模型时的思考强度。留空跟随全局，全局留空则跟随模型默认。", keys:["llm.reasoning_effort","dream.reasoning_effort","learn.reasoning_effort","llm.summary.reasoning_effort"] },
    ],
  },
  memories: {
    master: 'memory_enabled',
    groups: [
      { title:'提取与注入', desc:'记忆系统的核心节奏。', keys:['extract_interval','max_inject','locked_inject_ratio','semantic_threshold','dedup_threshold'] },
      { title:'模型', desc:'后台任务建议用小模型省成本。', keys:['default_memory_model','default_embedding_model'] },
      { title:'提示词', keys:['prompt_memory_extract'] },
    ],
  },
  metabolism: {
    groups: [
      { title:'🔥 热度系统', desc:'热度决定碎片如何被注入（全文/摘要/跳过）以及何时被淡忘。', keys:['heat_half_life_normal','heat_half_life_important','heat_recall_extend','heat_threshold_high','heat_threshold_medium','heat_importance_line','heat_emotion_line','heat_medium_truncate','cleanup_heat_threshold'] },
      { title:'🌫️ 自动软化', desc:'模拟人脑遗忘：老碎片细节褪色、要点保留。', master:'auto_soften_enabled', keys:['auto_soften_daily_limit','auto_soften_min_age','soften_cooldown_days'] },
      { title:'🔒 自动锁定与退役', desc:'反复跨话题召回的碎片自动锁定保护；长期不用的自动锁定碎片可退役。', master:'lock_retire_enabled', keys:['autolock_access_count','autolock_diversity','autolock_emo_access','autolock_emo_diversity','lock_retire_days'] },
    ],
  },
  dream: {
    groups: [
      { title:'Dream 整合', master:'dream_enabled', keys:['dream_model','dream_drowsy_threshold','prompt_dream'] },
      { title:'🎬 场景注入', desc:'Dream 产出的叙事场景在聊天时自动注入。', master:'scene_inject_enabled', keys:['scene_inject_limit','scene_inject_min_sim'] },
      { title:'🧹 Dream 产物清理', desc:'合并产物的保留策略。（从「记忆机制」页移来——它们管的是 Dream 的产物）', keys:['merge_retention_days','merge_min_keep'] },
    ],
  },
  profile: {
    groups: [
      { title:'用户画像', keys:['user_profile','prompt_user_profile'] },
    ],
  },
  calendar: {
    master: 'calendar_inject_enabled',
    groups: [
      { title:'模型', keys:['default_digest_model'] },
      { title:'提示词', desc:'日/周/月/季/年各级总结的提示词。', keys:['prompt_daily_digest','prompt_daily_digest_page','prompt_weekly_summary','prompt_monthly_summary','prompt_period_summary'] },
    ],
  },
  compression: {
    master: 'auto_compress_enabled',
    groups: [
      { title:'触发条件', keys:['auto_compress_msg_limit','auto_compress_token_limit','auto_compress_keep_last'] },
      { title:'输出控制', keys:['compress_ratio','compress_output_max'] },
      { title:'模型与提示词', keys:['default_compress_model','prompt_compress'] },
    ],
  },
  handoff: {
    master: 'handoff_enabled',
    groups: [ { title:'衔接参数', keys:['handoff_tail_count','handoff_summary_model'] } ],
  },
  tools: {
    master: 'tool_drawer_enabled',
    groups: [
      { title:'外部 MCP 抽屉', desc:'外部工具按语义相关性自动展开。', keys:['mcp_mode','ext_drawer_threshold','ext_drawer_max_open'] },
      { title:'高级（JSON）', keys:['mcp_manual_ids'] },
    ],
  },
  reminderTools: {
    groups: [
      { title:'⏰ 提醒工具', desc:'独立于工具抽屉与记忆开关；关闭后两种工具模式都隐藏提醒工具。', keys:['reminder_tools_enabled'] },
    ],
  },
  websearch: {
    groups: [ { title:'搜索配置', keys:['search_engine','search_api_key','search_max_results'] } ],
  },
  providers: {
    groups: [ { title:'默认模型与路由', desc:'未指定时的兜底模型与路由行为。', keys:['default_chat_model','openrouter_provider_order_enabled'] } ],
  },
  gateway: {
    groups: [
      { title:'对话行为', desc:'网关转发对话时的行为：标题生成与思考强度。', keys:['default_title_model','prompt_title_summary','reasoning_effort'] },
      { title:'性能', keys:['prompt_cache_enabled','prompt_cache_ttl'] },
      { title:'兼容与回退', desc:'出问题时可以立刻关掉的新行为开关，改完即时生效、无需发版。', keys:['memory_event_ledger_write_enabled','session_identity_v2_enabled','sync_delete_privacy_capability_enabled'] },
    ],
  },
};

// 这 8 个 prompt 有内置默认、可恢复（对应 /admin/default-prompts）
export const RESTORABLE_PROMPTS = [
  'prompt_memory_extract','prompt_daily_digest','prompt_user_profile','prompt_daily_digest_page',
  'prompt_weekly_summary','prompt_monthly_summary','prompt_period_summary','prompt_dream',
];

// 返回未被任何页编排的 key（兜底用；last_dream_date 与客户端同步项会落在这里）
export function orphanKeys() {
  const placed = new Set();
  for (const pg of Object.values(CONFIG_PAGES)) {
    if (pg.master) placed.add(pg.master);
    for (const g of pg.groups) { if (g.master) placed.add(g.master); (g.keys || []).forEach(k => placed.add(k)); }
  }
  return Object.keys(CONFIG_META).filter(k => !placed.has(k));
}
