# 雪球模块改造设计:从"抓博主"到"抓注意力"(一期)

- 日期:2026-07-17
- 状态:设计待评审
- 关联:一期最小闭环(见 `2026-07-16-daily-stock-brief-phase1-design.md`)、包结构重构(`2026-07-17-package-restructure-stock-watch-design.md`)

## 1. 背景与动机

现有 `xueqiu/` 模块镜像微博,按 `user_id` 抓**指定博主**的时间线 + 评论。但项目的北极星目标是:**通过雪球讨论判断市场注意力在往哪些板块/个股流动,发现潜在方向**。

"按博主抓"与该目标不匹配——只盯几个大V等于提前把答案过滤掉,无法**发现**你原本没关注的板块异动。经讨论确认:

- 要做"注意力流向"分析,需要**广度**采集 + **可算增速的量化时间序列**,而不是少数博主的长文。
- 雪球的**热股榜/人气榜**本身就是平台用全站行为算好的"讨论热度聚合",是最干净、最省、最抗风控的量化信号。
- **热门 / ETF 等帖子流**是算法推荐的长篇观点,单篇价值低、样本小有偏、且多为事后复盘(滞后),一期不抓。
- 文本佐料改用 **7x24 快讯**(简短、事实性的消息/公告),用于解释"某板块为什么突然热"(催化剂),比长篇感悟有用。

### 预期边界(必须承认)

讨论热度对**未来收益预测性很弱**,多为同步/滞后信号;真正有价值的是**增速拐点、新叙事萌芽、反向情绪**。本模块定位为**"提出假设 / 提示注意力异动"**,结论必须叠加 `market` 模块的行情/资金流交叉验证,**不是预测神器**。

## 2. 一期范围

纳入:

1. **热股榜**(数字硬指标:排名 + 热度分值)→ 存每日快照,构成注意力时间序列。**主力信号**。
2. **7x24 快讯**(简短文本)→ 存消息流,供 LLM 读催化剂。**佐料**。

不纳入(一期):热门 / ETF / 达人 / 关注 / 自选 / 视频 / 基金 / 私募帖子流;评论抓取;板块级热度(改由 `market` 模块用结构化行业/ETF 行情补,不从雪球帖子推)。

保留但停用:现有 `user_timeline` 按博主抓取代码不接入每日流程,不删除(未来三期"大V策略提炼"可能复用)。

## 3. 端点设计

> ⚠️ 下列**新端点在公司网(防火墙拦雪球)无法验证**,**实现前第一步必须在能联网的环境实测**。热股榜是整套方案地基,若端点失效,退路是 `adata` 的东财人气榜 / 同花顺热度榜(现成库,更稳)。

| 用途 | 端点(待实测) | 说明 |
|---|---|---|
| 热股榜 | `stock.xueqiu.com/v5/stock/hot_stock/list.json?size=100&_type=10&type=10` | `type` 区分沪深/港/美;`size` 取前 N。返回排名列表 + 热度值 |
| 7x24 快讯 | `xueqiu.com/statuses/livenews/list.json?since_id=-1&max_id=-1&count=15` | 滚动快讯,按 `max_id` 游标翻页 |

复用(不变):`api.xueqiu.com/statuses/show.json`(长文补全,一期用不到)、`xueqiu.com/statuses/v3/comments.json`(评论,一期不抓)。

登录:热榜/快讯基本为公开数据,但仍复用现有 [xueqiu/login.py](../../xueqiu/login.py) 的 Playwright profile 取新鲜 cookie 以防限流;若无 cookie 也应尽量降级可用。

## 4. 存储设计(沿用 `data/app.db`,`xq_` 前缀)

### 新增 `xq_hot_rank`(热股榜每日快照 — 时间序列核心)

一只票一天一行,`(date, symbol)` 主键;当天重复跑只更新,天然幂等,不需要增量水位。

| 列 | 类型 | 说明 |
|---|---|---|
| `date` | TEXT | 交易日 `YYYYMMDD` |
| `symbol` | TEXT | 如 `SZ000001` |
| `name` | TEXT | 股票名 |
| `rank` | INTEGER | 当日热度排名 |
| `hot_value` | REAL | 热度分值(有则存,可空) |
| `market_type` | TEXT | 沪深/港/美(对应 `type`) |
| `fetched_at` | TEXT | 抓取时刻 ISO |

主键:`PRIMARY KEY(date, symbol)`;索引:`(symbol, date)` 便于取单票热度历史算环比。

### 新增 `xq_livenews`(7x24 快讯)

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PRIMARY KEY | 快讯 id,去重 |
| `created_ts` | INTEGER | epoch 秒 |
| `created_at` | TEXT | ISO |
| `text` | TEXT | 纯文本正文(HTML 裁净) |
| `raw_json` | TEXT | 原始 json |
| `fetched_at` | TEXT | 抓取时刻 |

索引:`(created_ts DESC)`。

### 保留不动

`xq_posts` / `xq_comments` / `xq_crawl_state`:一期不写入(供停用的博主抓取代码使用),schema 不变。

## 5. 抓取流程

`XueqiuClient` 复用现有 session / 重试退避 / 401·403·429 分类 / cookie 刷新骨架([xueqiu/crawler.py](../../xueqiu/crawler.py)),新增两个取数方法:

- `fetch_hot_rank(size, market_type) -> list[dict]`:拉热股榜,`slim_hot_rank` 裁成 `xq_hot_rank` 行。
- `fetch_livenews(max_id, count) -> tuple[list[dict], next_max_id]`:按游标翻页拉快讯,`slim_livenews` 裁净入库;抓到已存在 id 即停(增量)。

CLI([xueqiu/cli.py](../../xueqiu/cli.py))`crawl` 动词改为一次跑完:

1. 抓热股榜 → `upsert_hot_rank`(当天 upsert)。
2. 抓 7x24 → 翻页直到遇到已入库 id 或达页数上限 → `insert_livenews`(id 去重)。

熔断沿用:连续 401/403 超 `auth_fail_breaker` 写 `logs/xueqiu.paused` 并告警。`--mode full/incremental` 对热榜无意义(每日快照),一期 `crawl` 默认即"抓当天";保留参数仅为 CLI 契约一致,增量语义仅作用于 7x24。

## 6. 与下游衔接

### `report`(每日简报)

- [report/reader.py](../../report/reader.py) `collect()` 新增读取:
  - `xq_hot_rank`:取**今日 + 前一交易日**两天,供计算排名/热度**环比**(新上榜、排名跃升、掉榜)。
  - `xq_livenews`:取当日(`lookback_hours` 窗口内)快讯。
  - 沿用已加的"表不存在则返回 []"容错。
- [report/prompt.py](../../report/prompt.py) 新增"雪球注意力"段:热榜 Top N + 环比异动(↑新面孔/↑跃升/↓退潮)+ 当日代表性 7x24 快讯,提示 LLM 输出"注意力在向哪些板块流动 + 可能的催化剂/叙事 + 反指风险",并强制附交叉验证/免责措辞。

### `market`

板块级热度由 `market` 模块用结构化行业/ETF 行情(akshare/adata)补充,与雪球注意力信号在简报中交叉印证。本设计不改 `market`,仅约定 report 层把两者并列喂 LLM。

## 7. 配置变更(`config.json`)

新增(键名/是否嵌套**以实现时对齐现有 config 风格为准**——现有雪球用扁平键 `xq_*`,下面仅示意语义):

```jsonc
"xq_hot_rank_size": 100,      // 热股榜取前 N
"xq_hot_rank_market": "沪深",  // 沪深/港/美
"xq_livenews_max_pages": 5,   // 7x24 单次最多翻几页
"xq_profile_dir": ".xq_profile"  // 已有,复用
```

废弃(保留键但一期不生效):`xueqiu_bloggers`、`xq_max_pages`、`xq_comment_scope` 等按博主抓取相关项。同步更新 [config.example.json](../../config.example.json) 与 README。

## 8. 测试

- `slim_hot_rank` / `slim_livenews` 字段裁剪(用假 json 断言入库结构)。
- `xq_hot_rank` 当天 upsert 幂等、`(date,symbol)` 去重;`xq_livenews` id 去重。
- `reader.collect()` 读热榜两日 + 快讯;表缺失容错。
- 环比计算(新上榜/跃升/退潮判定)。
- 复用 `_FakeClient` 模式,不发真实网络请求。

## 9. 实现顺序(建议)

1. **联网实测热股榜 + 7x24 端点**(阻塞项,失效则切 adata 退路)。
2. storage:加 `xq_hot_rank` / `xq_livenews` 及 upsert/insert。
3. crawler:`fetch_hot_rank` / `fetch_livenews` + slim 函数。
4. cli:`crawl` 编排两源 + 熔断。
5. reader/prompt:接入简报 + 环比。
6. 配置 + README + 测试。

## 10. 未决 / 二期

- 二期:自建"提及频次"分析(爬个股讨论数 cashtag)、个股→板块映射表、情绪反向指标、板块轮动。
- 三期:大V策略提炼(复用保留的 `user_timeline` 代码)。
- 热股榜历史回补:平台通常只给当前榜,历史靠每日累积,无法回补——需尽早开始每日跑。
