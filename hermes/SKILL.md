---
name: daily-stock-brief
description: 每日 A 股观察简报：雪球抓取 → 盘后行情快照 → 读数据 → 调 LLM 生成简报 → 推飞书
---

# 每日 A 股观察简报

工作目录即 stock-watch 仓库根目录。数据已由 cron 抓取入库,本 skill 只读数据 + 生成简报。

## 流程

### 1. 读取当日数据

数据在 `data/app.db` (SQLite)。当天日期 YYYYMMDD 格式(如 20260804)。

**大盘指数** (`index_snapshot`, WHERE trade_date = 当天):
- index_code, index_name, close, pct_change, turnover, volume, source

**市场情绪** (`market_sentiment`, WHERE trade_date = 当天):
- up_count, down_count, limit_up_count, limit_down_count, up_down_ratio, total_turnover, margin_balance

**板块资金流** (`market_snapshot`, WHERE trade_date = 当天, ORDER BY pct_change DESC):
- sector, sector_type(行业/概念), pct_change, turnover, main_net_inflow_est, retail_net_inflow_est
- 取涨幅 Top 10 和跌幅 Top 5,主力净流入 Top 5 和净流出 Top 5

**雪球热榜** (`xq_hot_rank`, WHERE date = 当天, ORDER BY rank LIMIT 20):
- symbol, name, rank, hot_value

**7x24 快讯** (`xq_livenews`, WHERE created_ts >= 24小时前计算的 epoch 秒, ORDER BY created_ts DESC LIMIT 15):
- text, created_ts

**龙虎榜** (`dragon_tiger`, WHERE trade_date = 当天, ORDER BY net_buy DESC):
- stock_name, reason, net_buy

### 2. 生成《每日观察简报》

用上述数据生成 600 字以内中文简报,结构:

1. **一句总览** — 今日市场整体基调
2. **大盘** — 各指数涨跌,海外与 A 股不同步需注明
3. **情绪** — 涨跌比、涨跌停、成交额、两融
4. **板块资金流** — 领涨/领跌板块 Top,主力资金流向突出的板块
5. **雪球注意力** — 结合热榜和快讯,指出关注焦点;所有热度信号需标注"需交叉验证,非买卖建议"
6. **龙虎榜** — 如有数据,列出重点
7. **风险提示** — 一句

**规则:**
- 不搬运原文,只出加工结论
- 不给任何操作建议
- 资金流为按单子大小估算的近似值(非真实机构/散户),措辞谨慎:"主力净流入估算""中小单净流出估算"
- 热榜/快讯反映市场注意力(讨论热度),对未来收益预测性弱,需与当日行情/资金流交叉印证

### 3. 推送

最终回复即是简报正文。Hermes 自动投递到飞书(`--deliver feishu`)。
