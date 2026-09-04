---
name: daily-stock-brief
description: A股行情问答与每日观察简报。一切判断交给 report.plan,按它给的 action 执行一步:
  send=发现成PDF,generate=渲染PDF再发,answer=文本回答,message=只回一句话(休市/未开盘/拉取失败)。
  定时任务(每日盘后)用同一技能生成日报并交付。
---

# 每日 A 股观察简报

工作目录 = stock-watch 仓库根目录。**所有判断(哪天、复不复用、补不补拉、休市/未开盘)都交给
`python -m report.plan`,你只照它返回的 `action` 执行一步。不要自己判断、不要自己跑 reader/find/market、
不要自己算日期或比时间。**

## 第 1 步:跑决策命令

```
python -m report.plan --date <日期符号> --kind <brief|item> [--groups 块,块]
```

- `--date`:`today`(今天)/ `yesterday`(昨天)/ `N-days-ago` / `last-tue`(上周二)/ `this-mon`(本周一)/ `YYYYMMDD`。
  把用户的话直接换成符号,交给命令解析,**别自己算日期**。定时任务用 `today`。
- `--kind`:
  - 用户要「简报 / 日报 / 研报 / 复盘 / 观察 / 每日A股」,或**定时触发** → `brief`。
  - 用户点名某项数据(大盘 / 情绪 / 板块 / 热榜 / 快讯 / 龙虎榜 / "今天涨了没")→ `item`,并按下表加 `--groups`。
- `--groups`(仅 `item` 需要,可多个):大盘=`index` 情绪=`sentiment` 板块=`sectors` 热榜=`hot_rank`
  快讯=`livenews` 龙虎榜=`dragon`。`brief` **不传** `--groups`。

命令在仓库根目录执行(库路径由 `config.json` 的 `db_path` 决定)。**plan 已经把"补拉今天行情、判交易日 /
时段 / 复用"全做完了,你不用管。** 输出纯 JSON,含一个 `action`。

## 第 2 步:照 `action` 执行(只做这一件事)

- `"action": "send"` → 最终回复**只有一行** `MEDIA:<path 字段>`。结束。
- `"action": "message"` → 最终回复**只有** `text` 字段那句话(如"今日休市!" / "尚未开盘!" /
  "行情暂时拉取失败,请稍后再试" / "…无数据…")。**不出 PDF、不 MEDIA**。结束。
- `"action": "generate"` → 用 `data` 渲染 PDF(见第 3 步),最终回复 `MEDIA:<pdf 绝对路径>`。
- `"action": "answer"` → 用 `data` 写一段文本答用户。**不出 PDF、不 MEDIA**。结束。

若 action 带 `"intraday": true` → 正文 / 回答**开头加一句**:"盘中实时快照,收盘数据以盘后为准"。

## 第 3 步(仅 generate / answer):用 `data` 组织内容

`data` 即行情数据,结构:`date` / `indexes` / `sentiment` / `sectors`(已是四组 `gainers` 领涨 /
`losers` 领跌 / `inflow` 主力净流入 / `outflow` 净流出)/ `xq_hot_rank_today` / `xq_hot_rank_prev` /
`xq_livenews` / `dragon_tiger` / `is_close_data`。某块为空就写"该块暂无数据",继续其余,不报错。

### generate(出 PDF)

1. 按下方结构把 markdown 正文**普通写文件**写进 `brief.md`(**禁止任何形式的代码执行**——`python -c`、
   `execute_code`、写并运行任何 `.py`——来拼这个文件;普通写文件即可,不需转义)。
2. 执行:
   ```
   python -m report.render --title "A 股观察简报 · YYYY-MM-DD" --content-file brief.md --out data/reports/<YYYYMMDD>-A股观察简报.pdf
   ```
   - 日期一律用 action 的 `date` 字段(**不是**用户措辞"今天/昨天");`--title` 与 `--out` 文件名**固定,不准自拟**。
3. 取 render stdout 的 `path` 字段;最终回复**只有** `MEDIA:<path>` 一行。

### answer(文本)

只答用户问的那一项,格式自由,遵守下方措辞规则。

### 完整简报正文结构(600~800 字)

1. **总览** — 当日基调一句
2. **大盘** — 各指数涨跌(海外与 A 股不同步需注明)
3. **情绪** — 涨跌比 / 涨跌停 / 成交额 / 两融
4. **板块资金流** — 领涨 / 领跌板块,主力资金流向突出的板块
5. **雪球注意力** — 热榜两日对比 + 快讯,指出关注焦点与排名跃升的票;标"需交叉验证,非买卖建议"
6. **龙虎榜** — 如有则列重点;无(盘中)写"待盘后披露"
7. **风险提示** — 一句

### 措辞(所有回答通用)

- 不搬运原文,只出加工结论;不给任何操作建议。
- 资金流为按单子大小估算的近似值:"主力净流入估算" / "中小单净流出估算"。
- 热榜 / 快讯"反映市场注意力,需与板块资金流 / 盘面交叉印证,非买卖建议"。
- `intraday: true` 时开头加盘中快照说明;两融"最近披露(T+1)"。

## 硬约束(违反即任务失败)

1. 最终回复:generate → **只** `MEDIA:` 一行;answer / message → **只有**回答文本 / 那句话。**都不加**
   "已生成 / 请查收"之类说明,不补状态汇报、不贴错误堆栈。
2. **禁止任何形式的代码执行**(`python -c` / `execute_code` / 写并运行 `.py`)来拼 `brief.md` 或做验证 / 探测。
3. 不跑测试、不查表、不比较文件、不探环境。`report.plan` / `report.render` 均为已验证命令,直接跑;
   真出 ImportError,把缺包信息原文转述给用户,任务结束。
4. 任何用户可见输出(临时 JSON、最终回复)里**不出现**工具词:Hermes / cron / skill / report.plan /
   report.render / market snapshot / 命令行 / 投递 / 渠道 / iLink / sendmessage / PDF。
