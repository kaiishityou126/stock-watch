# AGENTS.md

微博单/多博主博文+评论抓取工具(**自用/研究**),为后续 LLM 分析 / Hermes 定时推送做数据源。完整用法见 [README.md](README.md)。

## 架构(微博包 4 层,职责单一)

- [weibo/login.py](weibo/login.py) — Playwright 扫码登录,登录态持久化到 `.wb_profile/`;抓取时 headless 复用该 profile 现取 cookie(自动保鲜)。`get_cookie_header()` 拼出 requests 用的 Cookie 头。
- [weibo/crawler.py](weibo/crawler.py) — `requests` 直连微博**手机端 H5 接口**(`m.weibo.cn`,参照 MediaCrawler):博文列表 `getIndex`(containerid `107603<uid>`,`since_id` 游标翻页)、长文补全(`/detail/<mid>` 的 `$render_data`,带重试)、评论 `hotflow`。带重试/退避 + 被封刷 cookie + 登录预检(`/api/config`)+ 抓前 `prime_creator`(100505 容器)预热。`slim_post`/`slim_comment` 把 HTML 裁纯文本入库(保留 `raw_json`、转发原文、配图 URL);`select_comments` 按 `comment_scope` 过滤(默认 `author_only` 只留博主评论+其父评论)。
- [weibo/storage.py](weibo/storage.py) — SQLite,三表 `posts`/`comments`/`crawl_state`。评论 `INSERT OR REPLACE` 去重;博文 `ON CONFLICT DO UPDATE`(保留 `comments_done` 断点)。`crawl_state` 存增量水位 `last_max_id` + 全量断点 `full_since_id`/`full_done`。
- [weibo/cli.py](weibo/cli.py) — CLI 编排,`crawl_one()` 是核心抓取循环;博主列表与开关读 `config.json`。雪球包 [xueqiu/](xueqiu/) 结构镜像微博包;共享层在 [common/](common/)(errors + obs);行情层在 [market/](market/);数据读取在 [report/](report/)(reader + storage);告警在 [notify/](notify/)(alert)。LLM 简报生成与飞书推送由 Hermes 负责。

## 行情层(market,自顶向下发现漏斗)

[market/storage.py](market/storage.py) 四张日快照表(全按 `trade_date` 存,供算环比/加速/反向):`index_snapshot`(大盘:A 股三大存收/涨/额/量,海外六只存收/涨)、`market_sentiment`(情绪硬指标腿,一天一行:涨跌家数/涨跌停/成交额/两融)、`market_snapshot`(板块,主键 `(trade_date,sector,sector_type)`,行业+概念同表,四级净额 + 合成 `main/retail_net_inflow_est`)、`dragon_tiger`(龙虎榜,热度验真)。[market/fetcher.py](market/fetcher.py) 是 akshare 薄封装(第三方库以参数 `ak` 注入便于测试;列名易变处用 `_pick`/`_require` 容错,best-effort 不崩)。[market/cli.py](market/cli.py) `python -m market snapshot` 四段各自 try/except 互不阻断。「主线板块」是下游分析产出,不预筛存储。设计见 [docs/superpowers/specs/2026-07-23-market-discovery-funnel-redesign-design.md](docs/superpowers/specs/2026-07-23-market-discovery-funnel-redesign-design.md)。

## 常用命令

```powershell
pip install -r requirements.txt
playwright install chromium                     # 首次装浏览器驱动
python -m weibo login                           # 扫码登录 / 失效重登
python -m weibo crawl --mode full               # 首次全量(建基线)
python -m weibo crawl --mode incremental        # 增量(定时用这个)
```

统一 CLI 契约:`python -m <包> <动词>`(weibo/xueqiu login|crawl;market snapshot;notify alert)。所有命令在仓库根目录运行。测试:`python -m unittest discover -s tests`。

## 项目约定

- **中文优先**:代码注释、docstring、CLI 输出、commit message 都用中文,贴合现有风格。
- 每个模块顶部有 docstring 说明职责与微博接口细节;新增函数沿用简短中文 docstring。
- 全部文件用 `from __future__ import annotations` + 内置泛型类型标注(`list[dict]`、`str | None`)。
- 错误分层:抓取层 `crawler._get()` 把 HTTP 状态翻译成 `RuntimeError`(401/403=登录失效、429=限流);CLI 层捕获后打印 `[warn]`/`[error]` 到 stderr,不中断整批。
- 用户输入 / 配置错误抛 `SystemExit`(带中文提示),不用 `RuntimeError`。
- 新增可配置项:先加进 [weibo/cli.py](weibo/cli.py) 的 `DEFAULTS`,再在 [config.example.json](config.example.json) 与 README 里同步说明。

## 微博接口易变(重要陷阱)

走 m 站(`getIndex` / `hotflow` / `/detail`),字段与限流策略会变。报错优先对字段而非改架构:
- 返回非 JSON 通常是被反爬拦截(`_get_json` 已退避重试),不是代码 bug。
- `created_at` 若是相对时间("刚刚"/"x分钟前")会导致 `parse_weibo_ts` 返回 None,影响按时间查询——真实抓取后需确认格式。
- 改字段裁剪只动 `slim_post` / `slim_comment`;新增入库字段要同步改 [weibo/storage.py](weibo/storage.py) 的 `SCHEMA` 和对应 upsert SQL。

## 安全 / 合规红线

- **绝不提交** `.wb_profile/`(=账号凭证)和 `data/`(含他人个人信息);已在 `.gitignore`。
- 仅个人低频自用,勿高频、勿商业化分发、勿公开转发原文;推送只给 LLM 加工后的结论。
- 推 `main`/`master` 前二次确认;不要 `git add .`,逐项确认避免误加敏感文件。
