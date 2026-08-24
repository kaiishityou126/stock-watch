# 注册到 Hermes(无人值守)

按顺序操作,让 stock-watch 抓取 + Hermes 简报全自动跑。下文 `<REPO>` = 本仓库绝对路径,如 `D:\work\AI\project\stock-watch`。

## 第 1 步:把本系统拷到主机

把整个仓库拷贝到服务器任意目录。下文 `<REPO>` 填这个目录。

## 第 2 步:装好前置

1. 按主 [README](../README.md) 完成「安装」「配置」。
2. 确认安装 Hermes:`hermes --version`。
3. 配置 Hermes 的 LLM 模型:`hermes model`。
4. 配置 Hermes 的微信网关:`hermes gateway setup`(选微信/Weixin)。

## 第 3 步:拷脚本

把 [hermes/scripts/](scripts/) 里三个 `.py` 拷到 Hermes 脚本目录:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\hermes\scripts"
Copy-Item "<REPO>\hermes\scripts\*.py" "$env:LOCALAPPDATA\hermes\scripts\"
```

## 第 4 步:装技能(agent 任务用)

Hermes 只接受**技能名**(相对名),拒绝绝对路径;先装技能,后面 cron 用 `--skill <技能名>` 引用:

```powershell
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\hermes\skills\stock-watch\daily-stock-brief"
Copy-Item "<REPO>\hermes\skills\daily-stock-brief_skill.md" "$env:LOCALAPPDATA\hermes\skills\stock-watch\daily-stock-brief\SKILL.md"
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\hermes\skills\stock-watch\blogger-strategy"
Copy-Item "<REPO>\hermes\skills\blogger-strategy_skill.md" "$env:LOCALAPPDATA\hermes\skills\stock-watch\blogger-strategy\SKILL.md"
```

## 第 5 步:建 cron 任务

```powershell
# 微博增量抓取(no-agent:纯数据采集,不调 LLM)
hermes cron create "30 9,12,15,21 * * *" `
  --no-agent `
  --script stockwatch_weibo.py `
  --workdir <REPO> `
  --name "stockwatch-weibo"

# 雪球抓取(no-agent:纯数据采集)
hermes cron create "10 8,9,11,12,14,15,17,19,20,22 * * *" `
  --no-agent `
  --script stockwatch_xueqiu.py `
  --workdir <REPO> `
  --name "stockwatch-xueqiu"

# 盘后行情快照(no-agent:纯数据采集,18:00 等龙虎榜披露再拉,一次全量含龙虎榜)
hermes cron create "0 18 * * 1-5" `
  --no-agent `
  --script stockwatch_market.py `
  --workdir <REPO> `
  --name "stockwatch-market"

# 每日简报(agent 模式:18:10 生成,简报含当天龙虎榜;读雪球+行情→调LLM→推微信)
hermes cron create "10 18 * * 1-5" `
  --skill daily-stock-brief `
  --workdir <REPO> `
  --deliver weixin `
  --name "stockwatch-daily"

# 大 V 策略提炼(agent 模式:读微博博文→调LLM→输出分析)
hermes cron create "0 20 * * 5" `
  --skill blogger-strategy `
  --workdir <REPO> `
  --deliver weixin `
  --name "stockwatch-blogger"

# 告警监控(no-agent:扫日志→有问题输出→Hermes 投递)
hermes cron create "55 8-22 * * *" `
  --no-agent `
  --script stockwatch_alert.py `
  --workdir <REPO> `
  --deliver weixin `
  --name "stockwatch-alert"
```

> 大 V 策略提炼也支持自然语言随时触发——在 Hermes 对话中说 "分析下最近微博大V的观点" 即可,不一定要等 cron。

## 第 6 步:起 gateway 常驻

```powershell
hermes gateway install
```

## 第 7 步:验证

1. `hermes cron list` —— 确认五条已登记。
2. `hermes cron run stockwatch-weibo` —— 手动触发,看 `data/app.db` 有新数据。
3. `hermes cron run stockwatch-daily` —— 手动触发,看微信收到简报。

完成。之后到点自动跑,无需再管。

---

## 改时刻表(可选)

- 改时间:`hermes cron edit <job_id> --schedule "新cron"`
- 管理命令:`hermes cron list | run | pause | resume | remove`
