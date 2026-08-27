# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

自用的 A 股舆情观察系统(个人低频研究):抓微博博主博文+评论(`weibo`)、雪球全站注意力信号(`xueqiu`:热股榜日快照 + 7x24 快讯)、盘后行情(`market`:大盘指数/市场情绪/板块资金流/龙虎榜,基于 akshare),全部写入单个 SQLite 库 `data/app.db`。**LLM 简报生成与推送不在此仓库**——由外部 Hermes agent 完成,本仓库只负责「抓取 + 存储 + 只读查询」。纯 Python,需 3.10+(开发用 3.13),Windows / 国内网络环境。

## 常用命令

```powershell
pip install -r requirements.txt          # 无编译步骤
playwright install chromium              # 首次装浏览器驱动(仅登录用)

# 测试(unittest;无需网络/登录态,测试内自建临时库或传假对象)
python -m unittest discover -s tests                              # 全部
python -m unittest tests.test_report_reader                       # 单个文件
python -m unittest tests.test_report_reader.TestReportReader.test_collect_respects_lookback   # 单个用例

# 统一 CLI 契约:python -m <包> <动词>,一律在仓库根目录执行
python -m weibo login                          # 扫码登录 / 失效重登
python -m weibo crawl --mode full|incremental  # 全量建基线 / 增量(定时用)
python -m xueqiu login | crawl                 # 雪球不分全量增量
python -m market snapshot [--groups index,sentiment,sectors,dragon] [--date YYYYMMDD]
python -m notify alert                         # 扫日志 ERROR + 熔断 flag
python -m report.reader --date today --groups index,sectors   # 只读,stdout 输出纯 JSON

python deploy.py                               # 首次部署一键引导(依赖/登录/Hermes cron 全流程)
```

## 架构

数据流:Hermes cron 定时触发 → 各爬虫包写 `data/app.db` → `report.reader` 读出 JSON → Hermes agent(装成 skill)组 prompt 调 LLM → 推送。仓库内无常驻进程。

- **单库多包**:所有包共用 `data/app.db`,表按包划分——`posts`/`comments`/`crawl_state`(weibo)、`xq_posts`/`xq_hot_rank`/`xq_livenews`(xueqiu)、`index_snapshot`/`market_sentiment`/`market_snapshot`/`dragon_tiger`(market,全按 `trade_date` 存日快照)、`daily_report`(report)。某数据源没跑过、对应表不存在是常态,**读取侧一律当空结果处理,不许崩**(见 `report/reader.py` 的 `_rows`)。
- [common/](common) 共享层:`errors.py` 分型异常(AuthError=401/403 触发熔断,RateLimitError=429 退避重试;均继承 RuntimeError 保证 `except RuntimeError` 兜得住)、`obs.py`(结构化日志 + 熔断 flag `logs/<module>.paused` + 按字节偏移去重的 ERROR 扫描)、`stdio.py`(**每个 CLI 的 main() 入口必须调一次 `force_utf8_stdio()`**,否则 Windows 管道下 GBK 输出乱码)。
- [weibo/](weibo) 与 [xueqiu/](xueqiu) 结构镜像:login(Playwright 持久化 profile,抓取时 headless 现取 cookie)/ crawler(requests 直连手机端 H5 接口)/ storage(SQLite)/ cli。雪球复用 `weibo.cli.load_config`;所有配置缺省值集中在 `weibo/cli.py` 的 `DEFAULTS`。
- [market/fetcher.py](market/fetcher.py):akshare **以参数 `ak` 注入便于测试**(测试传假对象而非真 pandas);akshare 列名易变,边界处 `_pick`/`_require` 容错——缺列填 None 并记 MarketError,不静默给错数。模块加载时全局禁用 SSL 验证(国内网络自用工具)。四段快照在 CLI 里各自 try/except,互不阻断。
- [report/reader.py](report/reader.py) 是给 LLM 的唯一读数入口:stdout 必须保持**纯 JSON**(报错也输出 `{"error": ...}` 且 exit 0,不许 traceback)。库路径解析链:`--db` → config.json 的 `db_path` → `data/app.db`。输出带元数据信号:`is_close_data`(今天行情是否 15:35 后拉的收盘口径)、`is_today_data:false`(自动取日时库里还没有今天数据,下游应补拉而非拿旧日期冒充当天)。
- [hermes/](hermes) 不是 Python 包:`scripts/*.py` 是薄 subprocess 包装(cron 以 `--workdir <仓库根>` 运行,故脚本内可直接 `python -m weibo`);`skills/*_skill.md` 拷到 Hermes 技能目录作 SKILL.md,是 agent 行为契约(只许 `python -m report.reader` 读数据)。Hermes 只接受技能相对名,不接受绝对路径。
- 设计文档在 [docs/superpowers/](docs/superpowers)(specs = 设计,plans = 实施);代码注释里说的「设计文档」指这里。架构分层详见 [AGENTS.md](AGENTS.md),注册 Hermes 步骤见 [hermes/README.md](hermes/README.md)。

## 项目约定

- **中文优先**:注释、docstring、CLI 输出、commit message(`fix: 中文描述` / `feat(reader): ...`)都用中文。
- 全部文件 `from __future__ import annotations` + 内置泛型标注(`list[dict]`、`str | None`);每个模块顶部 docstring 说明职责与接口细节。
- 错误分层:抓取层把 HTTP 状态翻译成异常;CLI 层捕获后打 `[warn]`/`[error]` 到 stderr,不中断整批;用户输入/配置错误抛 `SystemExit`(带中文提示),不用 RuntimeError。
- 新增配置项三处同步:`weibo/cli.py` 的 `DEFAULTS` → `config.example.json` → README。
- 改入库字段:`slim_post`/`slim_comment`(字段裁剪)与对应包 `storage.py` 的 SCHEMA + upsert SQL 必须同改。

## 外部接口易变(重要陷阱)

微博(m.weibo.cn)与雪球接口的字段/限流策略随时会变:报错优先修字段映射,别改架构。返回非 JSON 通常是被反爬拦截(`_get_json` 已带退避重试),不是代码 bug。日志行格式固定为 `时间 | 级别 | 步骤 | 消息`,`notify alert` 按**第 2 段字段 == ERROR** 判定告警、不做中文文本匹配——改日志文案安全,改日志格式要连带改扫描逻辑。

## 安全红线

`.wb_profile/`、`.xq_profile/`(=账号凭证)、`data/`(含他人个人信息)、`config.json`(含 uid)、`logs/` 已在 `.gitignore`,**绝不提交**。不要 `git add .`,逐项确认;推 `main` 前二次确认。仅个人低频自用:勿高频抓取、勿商业化分发、勿公开转发原文,推送只发 LLM 加工后的结论。
