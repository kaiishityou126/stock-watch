# market 重设计实现计划:自顶向下「资金 + 情绪」发现漏斗

> **For agentic workers:** REQUIRED: Use the `subagent-driven-development` agent (recommended) or `executing-plans` agent to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 [设计文档](../specs/2026-07-23-market-discovery-funnel-redesign-design.md) 扩展 `market` 包——把行情数据层从「板块资金流 + 龙虎榜」两表,扩成三表(`index_snapshot` + `market_sentiment` + `market_snapshot` 扩展),为未来「主动点名 板块 + 个股」的每日简报打地基。

**Architecture:** 三层改动(storage→fetcher→cli),从里到外;reader 读取层跟着扩。每个 Phase 结束后仓库可测(全测绿)。不改微博/雪球/notify 的任何逻辑;不建 `stock_fund_flow`(未来期)。

**Tech Stack:** Python 3.10+、akshare、SQLite、unittest。所有命令在仓库根目录执行。

---

## 权威依据与硬约束

- 唯一权威依据:[docs/superpowers/specs/2026-07-23-market-discovery-funnel-redesign-design.md](../specs/2026-07-23-market-discovery-funnel-redesign-design.md)。本计划与设计冲突时以设计为准。
- **硬验收门槛(每个 Phase 末都要过):**
  - `python -m unittest discover -s tests` → 全绿(≥51 测;Phase 末增长)。
  - `python -m market --help` 正常;`python -m market snapshot` 在无网络时只报 akshare 异常(不崩)。
- **项目约定**:每个 `.py` 顶部 `from __future__ import annotations`;内置泛型标注;中文注释/docstring;错误边界用 `MarketError`(CLI 层捕获记 ERROR 日志);新增可配置项先加进 `market/cli.py` 的 defaults 与 config.example.json + README。
- **不做 git 操作**:当前非 git 仓库。
- **数据库无迁移成本**:真实抓取未跑过,`market_snapshot` 表无生产数据,可直接改 schema(DROP + CREATE)。若将来有数据需要 ALTER 再写迁移——本计划假设无数据。

---

## 现状文件清单(改动范围)

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `market/storage.py` | **大改** | 扩 market_snapshot(+sector_type,+四级净额);新建 index_snapshot、market_sentiment 表及 CRUD |
| `market/fetcher.py` | **大改** | 扩 fetch_sector_fund_flow(四级净额 + 行业/概念双跑);新增 fetch_index、fetch_sentiment |
| `market/cli.py` | 中改 | cmd_snapshot 编排三张表;config 新增 sector_types 列表 |
| `report/reader.py` | 中改 | collect() 新增读 index_snapshot、market_sentiment |
| `report/prompt.py` | 小改 | 新增大盘+情绪段的格式化(供 LLM prompt) |
| `tests/test_market_storage.py` | 大改 | 补新表 + 扩展表的去重/upsert 测试 |
| `tests/test_market_fetcher.py` | 大改 | FakeAk 扩展四级净额;新增 index/sentiment 假数据+测试 |
| `tests/test_report_reader.py` | 小改 | 新表读取测试 |
| `config.example.json` | 小改 | market 节加 sector_types 等 |
| `README.md` | 小改 | 功能描述更新 |
| `AGENTS.md` | 小改 | market 包说明更新 |

**不改的文件:** `weibo/`、`xueqiu/`、`common/`、`notify/`、`report/builder.py`、`report/llm.py`、`report/storage.py`、`hermes/`、所有非 market/report 的测试。

---

## Phase 1 — 存储层:扩 market_snapshot + 新建 index_snapshot / market_sentiment

完成后:三张表可建可读写,旧 market_snapshot 测试适配新 schema,新表有去重/upsert 测试。

### Task 1.1: 扩展 market_snapshot schema 与 CRUD

**Files:** `market/storage.py`

- [ ] **Step 1:** SCHEMA 中 `market_snapshot` 表新增列:
  - `sector_type TEXT NOT NULL DEFAULT '行业'` — 区分行业/概念
  - `super_large_net REAL` — 超大单净额
  - `large_net REAL` — 大单净额
  - `medium_net REAL` — 中单净额
  - `small_net REAL` — 小单净额
  - 主键改为 `PRIMARY KEY (trade_date, sector, sector_type)`
- [ ] **Step 2:** `upsert_snapshot()` SQL 新增列绑定(ON CONFLICT 对应扩展主键)。
- [ ] **Step 3:** `snapshot_for()` 返回结果包含新列。

### Task 1.2: 新建 index_snapshot 表与 CRUD

**Files:** `market/storage.py`

- [ ] **Step 1:** SCHEMA 新增 `index_snapshot` 表:
  ```sql
  CREATE TABLE IF NOT EXISTS index_snapshot (
      trade_date  TEXT NOT NULL,
      index_code  TEXT NOT NULL,
      index_name  TEXT,
      close       REAL,
      pct_change  REAL,
      turnover    REAL,   -- A股指数有;海外 NULL
      volume      REAL,   -- A股指数有;海外 NULL
      source      TEXT,
      fetched_at  TEXT,
      PRIMARY KEY (trade_date, index_code)
  );
  ```
- [ ] **Step 2:** 新增 `upsert_index(rows: list[dict])` 方法(ON CONFLICT DO UPDATE)。
- [ ] **Step 3:** 新增 `index_for(trade_date: str) -> list[dict]` 查询方法。

### Task 1.3: 新建 market_sentiment 表与 CRUD

**Files:** `market/storage.py`

- [ ] **Step 1:** SCHEMA 新增 `market_sentiment` 表:
  ```sql
  CREATE TABLE IF NOT EXISTS market_sentiment (
      trade_date        TEXT PRIMARY KEY,
      up_count          INTEGER,
      down_count        INTEGER,
      limit_up_count    INTEGER,
      limit_down_count  INTEGER,
      up_down_ratio     REAL,
      total_turnover    REAL,
      margin_balance    REAL,
      fetched_at        TEXT
  );
  ```
- [ ] **Step 2:** 新增 `upsert_sentiment(row: dict)` 方法(ON CONFLICT DO UPDATE;一天一行)。
- [ ] **Step 3:** 新增 `sentiment_for(trade_date: str) -> dict | None` 查询方法。

### Task 1.4: 存储层测试

**Files:** `tests/test_market_storage.py`

- [ ] **Step 1:** 现有 `test_snapshot_dedup` 适配新 schema:row 加 `sector_type` / `super_large_net` / `large_net` / `medium_net` / `small_net`。PK 含 sector_type 的去重验证。
- [ ] **Step 2:** 新增 `test_index_dedup`:同 (trade_date, index_code) 插两遍,验 upsert 覆盖。
- [ ] **Step 3:** 新增 `test_sentiment_dedup`:同 trade_date 插两遍,验 upsert 覆盖;验 sentiment_for 返回 dict。
- [ ] **Step 4:** 跑测试,全绿。

---

## Phase 2 — 取数层:扩 fetcher 支持三张表

完成后:fetcher 能取大盘指数 / 市场情绪 / 扩展板块资金流(行业+概念双跑、四级净额),全靠注入 ak 假对象的单测验证。

### Task 2.1: 扩展 fetch_sector_fund_flow + fetch_snapshot(四级净额 + 行业/概念双跑)

**Files:** `market/fetcher.py`

- [ ] **Step 1:** `fetch_sector_fund_flow` 的 `_require` 新增四级列:`"今日超大单净流入-净额"`, `"今日大单净流入-净额"`, `"今日中单净流入-净额"`, `"今日小单净流入-净额"`。输出 dict 新增 `super_large_net` / `large_net` / `medium_net` / `small_net` + `sector_type`(从参数推导:`"行业资金流"` → `"行业"`,`"概念资金流"` → `"概念"`)。`main_net_inflow_est` 改为 `super_large_net + large_net`,`retail_net_inflow_est` 改为 `medium_net + small_net`。
- [ ] **Step 2:** 新增 `fetch_all_sectors(trade_date, ak=None) -> list[dict]`:内部依次调 `fetch_snapshot(..sector_type="行业资金流")` + `fetch_snapshot(..sector_type="概念资金流")`(地域丢弃),合并返回。这是 cli 层调的顶层入口。
- [ ] **Step 3:** 列名容错:akshare 可能已有主力净流入-净额(=超大+大)作合并列;若四级列缺失但合并列在 → fallback 用合并列 + 置四级为 None,记 warning 不崩。

### Task 2.2: 新增 fetch_index(大盘指数)

**Files:** `market/fetcher.py`

- [ ] **Step 1:** 定义常量 `A_INDEXES` 与 `OVERSEAS_INDEXES`(代码→名称映射)。
- [ ] **Step 2:** 实现 `fetch_index(trade_date: str, ak=None) -> list[dict]`:
  - A 股三大指数:用 `stock_zh_index_daily_em` 或 `index_zh_a_hist`(按 trade_date 取当日行)→ 取 close/pct_change/turnover/volume。
  - 海外六指数:用 `index_us_stock_sina` / `stock_hk_index_daily_em` / 等接口→ 仅取 close/pct_change,turnover/volume = NULL。
  - 单条接口失败 → 跳过该指数,记 warning,不阻断其他(best-effort)。
- [ ] **Step 3:** `_require` 列缺失容错(接口列名易变)。

### Task 2.3: 新增 fetch_sentiment(市场情绪)

**Files:** `market/fetcher.py`

- [ ] **Step 1:** 实现 `fetch_sentiment(trade_date: str, ak=None) -> dict`:
  - 涨跌停/家数:`stock_market_activity_legu()` → 取最新一行的上涨/下跌/涨停/跌停。
  - 全市场成交额:从大盘指数数据或 `stock_zh_a_spot_em` 汇总(若已有 index 数据可复用)。
  - 两融余额:`stock_margin_sse()` + `stock_margin_szse()` 合计(T+1 慢数据;取不到填 None)。
  - `up_down_ratio` = `up_count / down_count`(down_count=0 时置 None)。
- [ ] **Step 2:** 返回单 dict(一天一行),键名对齐 storage 表字段。

### Task 2.4: 取数层测试

**Files:** `tests/test_market_fetcher.py`

- [ ] **Step 1:** `_FakeAk` 扩展:
  - `stock_sector_fund_flow_rank` 返回含四级列(超大/大/中/小);
  - 新增 `stock_zh_index_daily_em` / `index_us_stock_sina` 等假方法;
  - 新增 `stock_market_activity_legu` / `stock_margin_sse` / `stock_margin_szse` 假方法。
- [ ] **Step 2:** 现有 `test_fund_flow_est_mapping` 扩展:验 super_large_net / large_net / medium_net / small_net + sector_type。
- [ ] **Step 3:** 新增 `test_fetch_all_sectors`:验行业+概念双跑合并、sector_type 分别正确。
- [ ] **Step 4:** 新增 `test_fetch_index`:验 A 股有 turnover/volume,海外 turnover=None。
- [ ] **Step 5:** 新增 `test_fetch_sentiment`:验涨跌停 + up_down_ratio + margin_balance。
- [ ] **Step 6:** 跑测试,全绿。

---

## Phase 3 — CLI 层:cmd_snapshot 编排三张表

完成后:`python -m market snapshot` 一次调用依次拉大盘+情绪+板块(行业+概念)+龙虎榜,四块各自 try/except 不互相阻断。

### Task 3.1: 改写 cmd_snapshot

**Files:** `market/cli.py`

- [ ] **Step 1:** 导入新 fetcher 函数:`fetch_all_sectors`、`fetch_index`、`fetch_sentiment`。导入新 storage 方法:`upsert_index`、`upsert_sentiment`。
- [ ] **Step 2:** `cmd_snapshot` 内部四段,各自 try/except MarketError,任一失败记 ERROR 不阻断其余:
  1. 大盘指数 → `fetch_index` → `store.upsert_index`
  2. 市场情绪 → `fetch_sentiment` → `store.upsert_sentiment`
  3. 板块资金流 → `fetch_all_sectors`(行业+概念全量) → `store.upsert_snapshot`
  4. 龙虎榜 → `fetch_dragon_tiger` → `store.upsert_dragon_tiger`(不变)
- [ ] **Step 3:** 打印汇总(指数 N 条 / 情绪 OK/失败 / 板块 N 条 / 龙虎榜 N 条)。

### Task 3.2: config 扩展

**Files:** `market/cli.py`, `config.example.json`

- [ ] **Step 1:** config `market` 节不再需要单一 `sector_type`(现在固定双跑行业+概念);可删或标废弃。保留 `top_n`。
- [ ] **Step 2:** `config.example.json` 同步更新 market 节注释。

---

## Phase 4 — 读取层:reader + prompt 适配新表

完成后:report/reader 的 collect() 能读三张新表并传给 prompt;prompt 能格式化大盘 + 情绪段。

### Task 4.1: reader.collect() 新增读取

**Files:** `report/reader.py`

- [ ] **Step 1:** 新增读 `index_snapshot`:按 trade_date 取。
- [ ] **Step 2:** 新增读 `market_sentiment`:按 trade_date 取(一行)。
- [ ] **Step 3:** 现有 sectors 查询加 `sector_type` 列(供 prompt 区分行业/概念)。
- [ ] **Step 4:** collect() 返回 dict 新增 `"indexes"` / `"sentiment"` 键。

### Task 4.2: prompt 格式化

**Files:** `report/prompt.py`

- [ ] **Step 1:** 新增 `_fmt_indexes(indexes: list[dict]) -> str`:格式化大盘走势段。
- [ ] **Step 2:** 新增 `_fmt_sentiment(sentiment: dict | None) -> str`:格式化情绪温度段。
- [ ] **Step 3:** 主 prompt 组装函数加入大盘 + 情绪两段(插在板块资金流之前)。

### Task 4.3: reader/prompt 测试

**Files:** `tests/test_report_reader.py`, `tests/test_report_prompt.py`

- [ ] **Step 1:** reader 测试:建库→插 index_snapshot + market_sentiment 数据→验 collect 返回包含。
- [ ] **Step 2:** prompt 测试:传假数据验格式化输出非空。
- [ ] **Step 3:** 跑测试,全绿。

---

## Phase 5 — 文档与收口

### Task 5.1: 文档更新

**Files:** `README.md`, `AGENTS.md`

- [ ] **Step 1:** README 功能描述更新 market 行(从"板块资金流+龙虎榜"→"大盘指数+市场情绪+板块资金流(行业+概念全量)+龙虎榜")。
- [ ] **Step 2:** README config 注释更新 market 节。
- [ ] **Step 3:** AGENTS.md market 段更新(三张表说明)。
- [ ] **Step 4:** 最终全量跑测试,全绿,收口。

---

## 任务统计

| Phase | 任务数 | 改动文件 | 预计新增/改写行数 |
|---|---|---|---|
| 1 存储层 | 4 tasks (12 steps) | storage.py + test | ~120 行改 + ~60 行新测 |
| 2 取数层 | 4 tasks (14 steps) | fetcher.py + test | ~200 行新增 + ~100 行新测 |
| 3 CLI | 2 tasks (5 steps) | cli.py + config | ~50 行改 |
| 4 读取层 | 3 tasks (7 steps) | reader.py + prompt.py + test | ~80 行新增 + ~40 行新测 |
| 5 文档 | 1 task (4 steps) | README + AGENTS | ~30 行改 |
| **合计** | **14 tasks (42 steps)** | **10 文件** | **~680 行** |
