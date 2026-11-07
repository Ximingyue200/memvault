# 持续集成

三个 job，都在 `.github/workflows/ci.yml` 里。目的只有一个：**在别人拉代码之前，先把「一眼能看出来的错」拦住**，而不是假装有一套完整测试。

## job 1 · Python 语法与导入

| 步骤 | 干什么 |
|---|---|
| `compileall -q -b memvault *.py` | 全部 .py 过一遍字节码编译，语法错、缩进错、编码错在这一步就红。`-b` 让产物与源码并列，**不写 `__pycache__`**，免得污染工作区 |
| 导入冒烟 | 依次 `import memvault.{store,search,heat,schema,embed,gateway,mcp_server,effort,_meter,server}`。语法没错但 `import` 时炸（循环导入、缺模块、模块级代码在无库环境下就报错）的问题，在这一步红 |

矩阵跑 **3.10 / 3.11 / 3.12**。低版本经常暴露 `match`、`X | None` 之类的兼容问题——这个项目在树莓派和老 Debian 上跑得多，值得守。

依赖只装 `numpy` 一个。项目刻意做到「核心只用标准库 + numpy」，CI 装得越少，这条约束越难被悄悄破坏。

## job 2 · 面板 JS 语法

面板是原生 ES module，没有打包步骤，所以**没有构建来替你发现语法错**。

直接 `node --check foo.js` 会把它当 CommonJS 解析，遇到 `import` 就报错。所以这个 job 的做法是：**把每个 `.js` 复制成临时 `.mjs` 再 `--check`**，Node 见 `.mjs` 就按 ESM 解析。零依赖、不引入 eslint/babel，一条 `while read` 扫完 `admin-panel/` 与 `memvault/` 下所有 js。

失败时用 `::error file=…` 的注解形式输出，GitHub 会在 PR 的文件视图里直接标行。

> 为什么不上 eslint：一个人维护的项目，规则集本身会变成第二个要维护的东西。语法错让 Node 管，风格靠自己。

## job 3 · 仓库卫生

两条硬规矩，都是踩过坑才写进来的：

1. **不许提交真实密钥**：正则拦 `sk-…`（OpenAI/DeepSeek 系）、`gwk-…`（网关 token）、`ghp_` / `github_pat_`（GitHub）、`tvly-…`（Tavily）。这些一旦进公开库，轮换都来不及。占位符（`sk-memvault`、`sk-...`）不会命中，因为要求 24 位以上十六进制。
2. **不许提交超过 2MB 的文件**：`data/`、模型权重、数据库快照都不该进 git。这条同时也防新手把 `memvault.db` 一起 commit 上来。

## 它不做什么

- **不跑真实检索/抽取**：那需要模型 key 和一个真库，CI 里没有，也不该有。这类验证在本地做（`scripts/` 里那套），或者以后用录制好的 fixture 做成离线测试。
- **不检查文案、不检查性能**：没有意义的部分不做。

## 本地怎么复现

```bash
# 1) Python
python3 -m compileall -q -b memvault *.py
python3 -c "import memvault.store, memvault.search, memvault.gateway"

# 2) 面板
find admin-panel memvault -name '*.js' | while read f; do
  t=$(mktemp --suffix=.mjs); cp "$f" "$t"; node --check "$t" || echo "❌ $f"; rm -f "$t"
done

# 3) 卫生
grep -rInE "sk-[a-fA-F0-9]{24,}|gwk-[a-fA-F0-9]{24,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}" --include='*.py' --include='*.js' --include='*.md' .
find . -type f -size +2M -not -path './.git/*'
```

## 之后可能加的

- 把 `search` 的混合打分做成**离线 fixture 测试**（给定一批卡片与查询，断言排序），这样融合权重改动才有回归保护。
- `scrub.py` 的脱敏规则单测：给一组含凭据的样本，断言输出里没有它们。
- 发布时打 tag 并附带 `CHANGELOG.md` 的对应段落。
