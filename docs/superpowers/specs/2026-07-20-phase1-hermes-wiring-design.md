# 一期:微博/雪球抓取由 Hermes 无人值守跑通(设计)

日期:2026-07-20
状态:待用户复核

## 1. 目标与范围

**一期只做一件事**:让已完成的微博 / 雪球抓取脚本,在 Hermes 内置 cron 驱动下**无人值守跑通抓取 + 落库**,并在抓取出问题时**推飞书告警**。

**明确纳入**:
- 微博增量抓取(指定博主博文+评论,需登录 profile)。
- 雪球增量抓取(热股榜 + 7x24 快讯,匿名可跑,无需登录)。
- 失败告警(连续 401/403 熔断 + ERROR 日志 → 飞书)。
- Hermes cron 注册 + gateway 常驻。

**明确排除**(留给后续期):
- LLM 分析 / 博主方法论提炼 / 投资理论。
- 行情快照(market)、简报生成(report)、简报推送(notify send)。
- akshare / tushare 等行情数据源。
- agent 模式编排。

## 2. 关键决策(已与用户对齐)

- **执行模式 = no-agent 脚本模式**(非 agent 模式)。一期抓取是纯确定性命令,无需 LLM 现场判断 → 零 token、不用 pin provider、最稳。
- **告警功能已完成**,一期不再写抓取/告警逻辑,只做接线。链路:抓取端埋点(`obs.set_paused` 写 `logs/<模块>.paused` + ERROR 日志)→ `obs.scan_issues` 扫描(按级别字段判定 + 字节 offset 防重复)→ `notify alert` 推飞书(`notifier.send`,webhook + 可选签名)。
- **SKILL.md 保留不动**:它是完整 6 步流水线蓝图,一期 no-agent 不读它,留给后续 agent 期。
- **推送渠道 = 飞书**(国内直连;Telegram 被墙不用)。
- **生产环境 = Windows 云服务器**(公司网络连不上微博,验证与部署都在云服务器)。

## 3. 执行单元:模块各自独立 + 独立日志监控

设计原则:**每个功能是独立模块,互不干扰,各写各的日志**(`logs/<模块>-YYYY-MM-DD.log`)。一期两个抓取模块 weibo / xueqiu(未来的资讯查询、akshare 读取等同样各自独立);告警**不塞进抓取脚本**,而是由一个**独立的日志监控任务**统一负责。这样抓取模块只管抓取,监控模块只管扫日志推送,职责分离、可各自增删。

三个薄脚本(源头放仓库 `hermes/scripts/`,版本可控、可测,部署时拷到 `~/.hermes/scripts/`),每个只 `subprocess` 一条命令:

| 脚本 | 干什么 |
|---|---|
| `stockwatch_weibo.py` | `python -m weibo crawl --mode incremental` |
| `stockwatch_xueqiu.py` | `python -m xueqiu crawl --mode incremental` |
| `stockwatch_monitor.py` | `python -m notify alert`(扫**所有**模块 ERROR + 熔断 flag) |

抓取脚本示例(雪球版仅模块名不同):

```python
"""Hermes no-agent 定时脚本:微博增量抓取(一期)。

放 ~/.hermes/scripts/;由 hermes cron --no-agent --script 调用。
cwd 由 cron 的 --workdir 指定为仓库根目录,故可直接 python -m weibo。
只负责抓取;告警由独立的 stockwatch_monitor 任务统一处理。
"""
from __future__ import annotations

import subprocess
import sys

sys.exit(subprocess.run([sys.executable, "-m", "weibo", "crawl", "--mode", "incremental"]).returncode)
```

监控脚本:

```python
"""Hermes no-agent 定时脚本:日志监控告警(一期)。

周期扫各模块 ERROR 日志 + 熔断 flag,有问题推飞书(文案写明模块/步骤/原因)。
notify alert 用字节 offset 去重,跑再频也不会对同一条错误重复告警。
"""
from __future__ import annotations

import subprocess
import sys

sys.exit(subprocess.run([sys.executable, "-m", "notify", "alert"]).returncode)
```

- `notify alert` 的告警文案已写明模块/步骤/原因(见 [notify/cli.py](../../../notify/cli.py) 的 `format_alert`,形如 `[错误] weibo / 抓取: uid=... 抓取中断: ...`、`[熔断] xueqiu / 熔断: 连续 3 次 401/403`)。
- **"哪类错误该让系统整体退出"暂不定**,退出码语义留给后续测试再定;当前脚本只透传各自命令的退出码。

### 3.1 当前抓取失败策略(现状,不改)

微博 / 雪球 `cmd_crawl` 现有的分层容错(见 [weibo/cli.py](../../../weibo/cli.py)):

1. **开跑前看熔断 flag**:`logs/<源>.paused` 存在 → WARNING + `SystemExit`,**跳过本轮**(需人工删 flag)。
2. **登录预检失败** → ERROR + `SystemExit`(提示重登)。
3. **逐目标抓取**:普通错误(`RuntimeError`)→ 记 ERROR,**跳过该目标继续**;登录失效(`AuthError` 401/403)→ 记 ERROR 累计,**连续**达阈值(`auth_fail_breaker`,默认 3)→ 写熔断 flag + `break` 停本轮,抓成功一个则清零。
4. 结束打印汇总。

即:会停下的只有"熔断 flag 存在 / 登录预检不过 / 连续登录失效超阈值"三种;单个目标的普通错误只记日志跳过。这些 ERROR/熔断都会被监控任务扫到并推送。

## 4. 时刻表:怎么配、怎么调

时区 Asia/Shanghai。三条 cron 各一条命令(一个源一条 + 一条监控)。cron 表达式格式:

```
分 时 日 月 周      # 逗号 = 多个值;* = 每个
```

调整速查:
- **改时间**:改前两段。如 `40 9 * * *` = 每天 09:40。
- **改次数**:小时段用逗号列多个值(分钟通常固定,方便和别的源错峰)。如微博想改成 3 次 → `40 9,15,21 * * *`。
- **错峰**:不同源用不同分钟,永不撞点(下方默认微博 :40、雪球 :10、监控 :55)。

**默认(仅示例,你自行调整)**:

| 任务 | cron 表达式 | 含义 |
|---|---|---|
| 微博抓取 | `40 9,12,15,21 * * *` | 每天 4 次(:40) |
| 雪球抓取 | `10 8,9,11,12,14,15,17,19,20,22 * * *` | 每天 10 次(:10) |
| 日志监控 | `55 8-22 * * *` | 8–22 点每小时一次(:55),兜住各源错误 |

监控跑得越勤,收到告警越快;因 offset 去重,跑勤也不会重复告警。

## 5. 注册步骤(写进 hermes/README.md)

**前置条件**(在云服务器上):
1. Hermes 已装通:`hermes --version`。
2. 依赖已装:`pip install -r requirements.txt` + `playwright install chromium`。
3. `config.json` 就位:`bloggers`、雪球配置、`notify.feishu_webhook` 已填;需签名则设环境变量 `FEISHU_SIGN_SECRET`。
4. 微博登录一次(RDP 扫码):`python -m weibo login`。雪球匿名可跑,登录可选。

**注册**:
1. 拷三个脚本到 `~/.hermes/scripts/`。
2. 建三条 cron(`--workdir` 必填,cron 默认脱离仓库跑):

   ```powershell
   hermes cron create "40 9,12,15,21 * * *" --no-agent --script stockwatch_weibo.py --workdir C:\stock-watch --name "stockwatch-weibo"
   hermes cron create "10 8,9,11,12,14,15,17,19,20,22 * * *" --no-agent --script stockwatch_xueqiu.py --workdir C:\stock-watch --name "stockwatch-xueqiu"
   hermes cron create "55 8-22 * * *" --no-agent --script stockwatch_monitor.py --workdir C:\stock-watch --name "stockwatch-monitor"
   ```

   （抓取任务的飞书告警由独立监控任务统一发;若想让 Hermes 在脚本硬崩时也兜底报一次,可额外给抓取任务加 `--deliver feishu`,非必需。）
3. 起 gateway 常驻(cron 靠它每 60s tick,须开机自启):`hermes gateway install`(装成 Windows 服务)。

**验证**:
- `hermes cron list` 确认三条已登记。
- `hermes cron run <job_id>` 立即手动触发一次,查看数据落库(`data/app.db`)+ 日志(`logs/`)。
- 故意制造失败(如临时改坏 cookie),再跑监控任务确认飞书能收到写明模块/原因的告警。

## 6. 文件计划

- **新增** `hermes/scripts/stockwatch_weibo.py`、`hermes/scripts/stockwatch_xueqiu.py`、`hermes/scripts/stockwatch_monitor.py`(上述脚本源头)。
- **新增** `hermes/README.md`:独立的注册说明(手敲 vs 自动的关系、前置条件、拷脚本、建 cron、起 gateway、验证、时刻表调整)。
- **主 README** 新增"注册到 Hermes"小节(一句概述 + 指向 hermes/README.md)。
- **删除** `hermes/cron.md`(内容已并入 hermes/README.md)。
- **[hermes/SKILL.md](../../../hermes/SKILL.md)**:保留不动(后续 agent 期用)。

## 7. 测试

- 三个脚本改为 `main()` + `__main__` 守卫、命令抽成模块常量 `CMD`,便于测试(避免 import 即执行)。
- [tests/test_hermes_scripts.py](../../../tests/test_hermes_scripts.py):用 importlib 按路径加载脚本,mock `subprocess.run`,验证各脚本 `CMD` 尾部(模块/子命令/参数)正确 + `main()` 透传退出码。
- 全量 51 测通过(原 48 + 新 3),未改抓取/告警/存储逻辑。

## 8. 风险与提醒

- **云机房 IP 易被微博风控**:小号 + 低频 + 随机延时(代码已有);上线盯日志,频繁 401/403 = IP 被盯,再考虑代理或挪回家里 PC。
- **Windows no-agent 脚本用 `.py`**(走 `sys.executable`);`.sh` 在原生 Windows 有问题,不用。
- **合规**:仅自用低频,不商业化分发,不转发原文。`.wb_profile/`、`data/` 已 gitignore,绝不提交。
