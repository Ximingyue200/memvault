#!/usr/bin/env python3
"""memvault 设置项登记表（单一事实源）。

和 kiwi-mem 的 config-schema.js 同一个思路：
每个参数一行，字段齐全 —— group / label / type / def / input / desc。
前端渲染器遍历这张表自动生成表单，新增参数只加一行，不碰 HTML。

key 命名约定（和 dream.py 的 DEFAULTS、settings 表的 k 一致）：
  gw.*       网关参数（gateway.py 读，可热更新）
  inject.*   注入行为（shim / gateway 都读）
  dream.*    人工梦生成器（dream.py 读）
  focus.*    任务（焦点）重建（focus.py 读）
  llm.*      模型配置（llm_cfg 三级继承：llm.<feat>.<k> > llm.<k> > 老 dream.api_*）
"""

SETTING_META = {
    # ── 网关（gateway.py 真正读的，可热更新）─────────────────────────────
    "gw.topk": {
        "group": "网关",
        "label": "注入条数",
        "type": "int",
        "def": "6",
        "input": "int",
        "desc": "每次检索注入多少条记忆。太多占 token，太少易遗漏。建议 3-10，默认 6。",
    },
    "gw.min_score": {
        "group": "网关",
        "label": "相似度门槛",
        "type": "float",
        "def": "0.30",
        "input": "float",
        "desc": "低于此分数的卡不注入。0.30 宽松（召回多），0.55 只留强命中。建议 0.25-0.50。",
    },
    "gw.upstream": {
        "group": "网关",
        "label": "上游地址",
        "type": "text",
        "def": "api.deepseek.com",
        "input": "text",
        "desc": "转发目标主机（不带 https:// 和路径）。改后需重启网关进程才生效。",
    },

    # ── 记忆温度（半遗忘）──────────────────────────────────────────────
    "memory.enabled": {
        "group": "记忆温度", "label": "启用温度衰减", "type": "bool", "def": "1",
        "input": "checkbox",
        "desc": "开启后，卡片按半衰期自然冷却；被检索命中则升温。关掉则所有卡恒温 1.0。",
    },
    "memory.half_life_days": {
        "group": "记忆温度", "label": "半衰期（天）", "type": "float", "def": "7.0",
        "input": "float",
        "desc": "多久不碰就冷掉一半。3 天=近期热点，7 天=一周内，30 天=只有大事件才留热。",
    },
    "memory.heat_boost": {
        "group": "记忆温度", "label": "命中升温量", "type": "float", "def": "0.25",
        "input": "float",
        "desc": "每被检索命中一次加多少温度。0.25 = 连续命中 4 次就到顶。建议 0.15-0.35。",
    },
    "memory.heat_threshold_high": {
        "group": "记忆温度", "label": "hot 门槛（全文注入）", "type": "float", "def": "0.60",
        "desc": "热度高于此值，卡片正文完整注入。热度是靠被检索命中攒的，0.25/次，4 次到顶。建议 0.50-0.80。",
    },
    "memory.heat_threshold_medium": {
        "group": "记忆温度", "label": "warm 门槛（摘要注入）", "type": "float", "def": "0.04",
        "desc": "热度在此值与 hot 门槛之间，只注入摘要（截断字数见下一项）。低于此值且有命中记录的卡跳过不注。建议 0.02-0.15。",
    },
    "memory.heat_medium_truncate": {
        "group": "记忆温度", "label": "warm 摘要字数", "type": "int", "def": "200",
        "desc": "warm 档注入时正文截断到多少字。60 字连一句技术描述都说不完（「NAS 上的 LXC 容器，Debian 12」就占一半），建议 150-400。",
    },
    "memory.heat_hot_max": {
        "group": "记忆温度", "label": "hot 全文上限（字）", "type": "int", "def": "600",
        "desc": "hot 档虽然叫全文，但要设上限防爆。库里最长卡 6714 字（pve 全量档案），平均 142 字。建议 400-1200。",
    },
    "chronicle_inject_enabled": {
        "group": "编年史注入", "label": "注入编年史总结", "type": "bool", "def": "1",
        "desc": "把「当前状态（core）」和最近一个月总结拼进 system prompt。这是「他最近在干什么」的宏观视角，不是具体某条记忆。",
    },
    "chronicle_inject_core_fresh_days": {
        "group": "编年史注入", "label": "当前状态新鲜度（天）", "type": "int", "def": "120",
        "desc": "core 总结的 end_ts 距今超过这么多天就不注入——否则它描述的是几个月前的状态，模型会当成「现在」。重建 core 后可调大。",
    },
    "chronicle_inject_core_max": {
        "group": "编年史注入", "label": "当前状态字数上限", "type": "int", "def": "800",
        "desc": "core 那条总结（原文 1971 字）截断到多少字。建议 500-1200。",
    },
    "chronicle_inject_month_max": {
        "group": "编年史注入", "label": "最近月总结字数上限", "type": "int", "def": "600",
        "desc": "最近一条月总结（原文 1300-1600 字）截断到多少字。建议 400-900。",
    },
    "memory.heat_cap": {
        "group": "记忆温度", "label": "温度上限", "type": "float", "def": "1.0",
        "input": "float",
        "desc": "单卡最高温度，防止一条卡被反复命中后永久锁死热榜。",
    },
    "memory.drowsy": {
        "group": "记忆温度", "label": "困了阈值", "type": "float", "def": "0.15",
        "input": "float",
        "desc": "低于此温度算「困了」——不再主动注入上下文，但被精确问到时仍能检索出来。",
    },
    "memory.cold": {
        "group": "记忆温度", "label": "冷了阈值", "type": "float", "def": "0.04",
        "input": "float",
        "desc": "低于此温度算「冷了」——只在关键词精确命中时出现，几乎不参与语义召回。",
    },
    "memory.soften_enabled": {
        "group": "记忆温度", "label": "自动软化", "type": "bool", "def": "0",
        "input": "checkbox",
        "desc": "开启后定时任务把冷透的老卡压成一句话（原文存 body_orig，可随时还原）。关掉则只保留手动触发。",
    },
    "memory.soften_min_age_days": {
        "group": "记忆温度", "label": "软化最小年龄（天）", "type": "int", "def": "90",
        "input": "int",
        "desc": "从没被检索过、且放了超过这么多天的卡才会被软化。太短会误伤新卡。建议 60-180。",
    },
    "handoff.enabled": {"group": "换窗", "label": "启用无缝换窗", "type": "bool", "def": "0",
                          "desc": "新对话首条消息时，注入上一个会话的摘要与结尾原文。"},
    "handoff.tail_count": {"group": "换窗", "label": "结尾原文条数", "type": "int", "def": "4", "min": 0, "max": 20,
                          "desc": "注入上一个会话的最后几条原文。0 = 只给摘要。网关侧尚未读取这些值。"},
    "handoff_enabled": {"group": "换窗", "label": "启用无缝换窗", "type": "bool", "def": "0",
                     "desc": "新对话首条消息时，注入上一个会话的摘要与结尾原文。网关侧尚未读取。"},
    "handoff_tail_count": {"group": "换窗", "label": "衔接结尾原文条数", "type": "int", "def": "6", "min": 0, "max": 20,
                     "desc": "衔接时带上一对话结尾多少条原文（约 3 轮）。"},
    "handoff_summary_model": {"group": "换窗", "label": "衔接概要模型", "type": "text", "def": "",
                     "desc": "生成衔接概要的模型。留空跟随压缩模型。"},
    "memory.soften_daily_cap": {
        "group": "记忆温度", "label": "每天软化上限", "type": "int", "def": "20",
        "input": "int", "min": 0, "max": 500,
        "desc": "每天最多软化多少张（0 = 不限）。防止某天一口气把整库压扁，出问题也追得回来。"
    },
    "memory.soften_budget": {
        "group": "记忆温度", "label": "软化后字数上限", "type": "int", "def": "90",
        "input": "int",
        "desc": "压成一句话的目标长度。太小会丢关键值，太大等于没压。建议 60-120。",
    },
    "memory.cold_penalty": {
        "group": "记忆温度", "label": "冷卡折扣", "type": "float", "def": "0.55",
        "input": "float",
        "desc": "检索时冷卡得分乘这个系数。1.0 = 不折扣（只展示不生效），0.4 = 冷卡很难挤进前几条。",
    },

    # ── 注入行为 ──────────────────────────────────────────────────────────
    "websearch.enabled": {"group": "联网", "label": "联网搜索", "type": "bool", "def": "0",
                          "input": "bool", "desc": "开启后，网关在注入记忆之外再搜一次网，把结果拼进上下文。"},
    "websearch.engine": {"group": "联网", "label": "搜索引擎", "type": "text", "def": "bing",
                         "input": "text", "desc": "bing=本地抓取无需 key（质量不稳）；tavily/bocha/zhipu 需要 key。"},
    "websearch.limit": {"group": "联网", "label": "结果条数", "type": "int", "def": "5",
                        "input": "int", "desc": "每次搜索取几条结果拼进上下文。"},
    "websearch.key": {"group": "联网", "label": "搜索 API Key", "type": "text", "def": "",
                      "input": "text", "desc": "填 Tavily / 博查 / 智谱 的 key。留空则用 Bing 本地抓取。"},
    "websearch.mode": {
        "group": "联网搜索", "label": "搜索时机", "type": "text",
        "def": "off", "input": "select", "options": ["off", "auto", "always"],
        "desc": "off=不搜（默认）；auto=命中「最新/今天/多少钱/发布」才搜；"
                "always=每次都搜。请求体带 web_search:true 可单次强制。",
    },
    "websearch.engine": {
        "group": "联网搜索", "label": "搜索引擎", "type": "text",
        "def": "tavily", "input": "text",
        "desc": "tavily / bocha / zhipu。改完即时生效。",
    },
    "websearch.limit": {
        "group": "联网搜索", "label": "结果条数", "type": "int",
        "def": "5", "input": "int",
        "desc": "注入多少条搜索结果。太多占 token，建议 3-8。",
    },
    "inject.enabled": {
        "group": "注入",
        "label": "自动注入",
        "type": "bool",
        "def": "1",
        "input": "bool",
        "desc": "关闭后网关不再自动注入记忆，请求照常转发。等价于把网关变成纯反代。",
    },

    # ── 人工梦生成器 ──────────────────────────────────────────────────────
    "dream.enabled": {
        "group": "梦境",
        "label": "做梦开关",
        "type": "bool",
        "def": "1",
        "input": "bool",
        "desc": "关闭后定时器不再自动做梦。手动触发仍可用。",
    },
    "dream.days": {
        "group": "梦境",
        "label": "回看天数",
        "type": "int",
        "def": "90",
        "input": "int",
        "desc": "做梦时读最近多少天的卡片。越大素材越全，越小越聚焦近期。默认 90。",
    },
    "dream.per_run": {
        "group": "梦境",
        "label": "每次生成几条梦",
        "type": "int",
        "def": "2",
        "input": "int",
        "desc": "一次做梦生成多少条判断。太多会互相撞题。建议 1-3，默认 2。",
    },
    "dream.max_cats": {
        "group": "梦境",
        "label": "最多几个主题",
        "type": "int",
        "def": "10",
        "input": "int",
        "desc": "做梦时最多抽几个 category 作为素材来源。默认 10。",
    },
    "dream.cards_per_cat": {
        "group": "梦境",
        "label": "每组抽几张",
        "type": "int",
        "def": "4",
        "input": "int",
        "desc": "每个主题抽几张卡（首尾+等距）。必须 ≥2，否则会除零报错。默认 4。",
    },
    "dream.auto_confirm": {
        "group": "梦境",
        "label": "生成后直接收下",
        "type": "bool",
        "def": "0",
        "input": "bool",
        "desc": "开启后生成的梦直接 status=confirmed，跳过人工确认。建议保持关闭，梦要人看过才算数。",
    },
    "dream.cron": {
        "group": "梦境",
        "label": "做梦定时",
        "type": "cron",
        "def": "*-*-* *:07:00 Asia/Shanghai",
        "input": "cron",
        "desc": "systemd timer 格式，必须带时区后缀。现在默认每小时第 7 分检查一次「料够不够」，料够才真跑。",
    },

    "dream.min_fresh": {
        "group": "梦境",
        "label": "新卡够多少才做梦",
        "type": "int",
        "def": "30",
        "input": "int",
        "desc": "自上次做梦以来新增的卡达到这个数，才触发生成。调小=更勤但更碎，调大=更攒但更慢。默认 30。",
    },
    "dream.max_gap_days": {
        "group": "梦境",
        "label": "最多隔几天没做梦",
        "type": "int",
        "def": "7",
        "input": "int",
        "desc": "即使新卡不够，距上次做梦超过这么多天也强制跑一次，避免长期不整理。默认 7。",
    },

    # ── 机器梦生成器 ──────────────────────────────────────────────────────
    "dream.machine_max_gap_days": {
        "group": "机器梦",
        "label": "机器梦最多隔几天",
        "type": "int",
        "def": "14",
        "input": "int",
        "desc": "距上次机器梦超过这么多天就真跑一次。机器梦看的是 AI 自己最近的行为，不需要新卡。默认 14。",
    },
    "dream.machine_cron": {
        "group": "机器梦",
        "label": "机器梦定时",
        "type": "cron",
        "def": "*-*-* *:17:00 Asia/Shanghai",
        "input": "cron",
        "desc": "机器梦（AI 关于自己的判断）定时。默认每小时第 17 分检查一次，距上次超过 machine_max_gap_days 天才真跑。",
    },

    # ── 任务（焦点）重建 ──────────────────────────────────────────────────
    "focus.cron": {
        "group": "任务",
        "label": "任务重建定时",
        "type": "cron",
        "def": "*-*-* 00/2:20:00",
        "input": "cron",
        "desc": "systemd timer 格式。默认每 2 小时第 20 分钟跑一次。保存后自动重建 timer。",
    },
    "focus.hours": {
        "group": "任务",
        "label": "回溯小时数",
        "type": "int",
        "def": "72",
        "input": "int",
        "desc": "重建任务时回溯最近多少小时内的会话。默认 72（3 天）。",
    },

    # ── 模型配置 ──────────────────────────────────────────────────────────
    "dream.api_base": {
        "group": "模型",
        "label": "LLM API 地址",
        "type": "text",
        "def": "https://api.deepseek.com",
        "input": "text",
        "desc": "生成器调用的 OpenAI 兼容 API 基地址。改后生成器下次调用生效。",
    },
    "dream.model": {
        "group": "模型",
        "label": "默认模型",
        "type": "text",
        "def": "openai",
        "input": "model",
        "desc": "生成器默认模型 id。演示端点用 openai；换成 DeepSeek 后可填 deepseek-flash / deepseek-v4-pro。",
    },
    "llm.base": {
        "group": "模型",
        "label": "全局 LLM 地址",
        "type": "text",
        "def": "https://text.pollinations.ai/openai",
        "input": "text",
        "desc": "各功能未单独指定时的全局 API 基地址。默认是免费公共端点（演示用：慢、会限流）；正式使用请换成自己的 OpenAI 兼容服务，见 docs/DEPLOY.md。",
    },
    "llm.model": {
        "group": "模型",
        "label": "全局 LLM 模型",
        "type": "text",
        "def": "openai",
        "input": "model",
        "desc": "各功能未单独指定时的全局模型 id。演示端点用 openai。",
    },
    "llm.summary.model": {
        "group": "模型",
        "label": "摘要模型",
        "type": "text",
        "def": "",
        "input": "model",
        "desc": "知识摘要单独用的模型 id。留空跟随全局默认。",
    },
    "tool_drawer_enabled": {
        "group": "工具", "label": "内部工具按需展开", "type": "bool",
        "def": "0", "input": "bool",
        "desc": "开启后内部工具按语义相关性展开；关闭则每次全部传给模型。当前网关尚无内部工具，开启不影响行为。",
    },
    "mcp_mode": {
        "group": "工具", "label": "外部 MCP 模式", "type": "text",
        "def": "off", "input": "text",
        "desc": "off 全禁用 · auto 按语意自动路由 · manual 只用你手动启用的服务器。外部 MCP 客户端尚未实现，改此项不影响行为。",
    },
    "ext_drawer_threshold": {
        "group": "工具", "label": "外部抽屉相似度阈值", "type": "float",
        "def": "0.35", "input": "float",
        "desc": "外部工具按语义相关性展开时的相似度门槛。越低越容易展开。",
    },
    "mcp_baidu_key": {
        "group": "工具", "label": "百度 AI 搜索 Key", "type": "password",
        "def": "", "input": "text",
        "desc": "千帆 AppBuilder 的 bce-v3/ALTAK-... 令牌。填了才启用百度 AI 搜索 MCP（1 个工具：搜索并总结）。",
    },
    "mcp_baidu_url": {
        "group": "工具", "label": "百度 AI 搜索端点", "type": "text",
        "def": "https://qianfan.baidubce.com/v2/ai_search/mcp", "input": "text",
        "desc": "一般不用改。换了接入点才需要动。",
    },
    "mcp_tavily_url": {
        "group": "工具", "label": "Tavily MCP 地址", "type": "text",
        "def": "", "input": "text",
        "desc": "填 https://mcp.tavily.com/mcp/?tavilyApiKey=xxx 就启用远程 MCP（5 个工具：search/extract/crawl/map/research）。留空则关闭。",
    },
    "mcp_servers": {
        "group": "工具", "label": "外部 MCP 服务器", "type": "text",
        "def": "[]", "input": "text",
        "desc": "JSON 数组，每项 {name,url,transport,enabled}。由「工具抽屉」页的编辑器维护。",
    },
    "skills.enabled": {
        "group": "工具", "label": "技能注入", "type": "bool",
        "def": "1", "input": "bool",
        "desc": "独立于记忆注入：开启后把「技能」页里启用的条目拼进 system prompt。关记忆不影响它。",
    },
    "reminder_tools_enabled": {
        "group": "工具", "label": "提醒工具", "type": "bool",
        "def": "0", "input": "bool",
        "desc": "控制提醒系统的工具是否可见。当前网关无提醒实现，开启不影响行为。",
    },
    "profile.auto_update": {
        "group": "画像", "label": "随对话自动更新", "type": "bool",
        "def": "1", "input": "bool",
        "desc": "开启后，同一会话静默超过「更新间隔」时，后台读这段对话判断要不要改画像。宁可不改。",
    },
    "profile.update_gap": {
        "group": "画像", "label": "更新间隔（秒）", "type": "int",
        "def": "900", "input": "int",
        "desc": "会话静默多少秒算「这一段聊完了」。太短会频繁调模型，太长更新不及时。建议 600-1800，默认 900（15 分钟）。",
    },
}

# 分组显示顺序（前端按这个顺序渲染分组）
GROUPS = ["网关", "注入", "记忆温度", "联网搜索", "梦境", "机器梦", "任务", "程序", "摘要", "模型", "画像", "工具", "其它"]

# 类型 → 前端 input 类型提示（渲染器据此选控件）
INPUT_HINTS = {
    "int": "number",
    "float": "number",
    "bool": "checkbox",
    "text": "text",
    "cron": "text",
    "model": "select",
    "prompt": "textarea",
}


def to_bool(v):
    """settings 表存字符串，bool 参数统一这样判断。"""
    return str(v).strip().lower() not in ("0", "false", "off", "no", "", "none")
