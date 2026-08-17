# stock-watch

自用的 **A 股舆情观察系统**:盯微博 / 雪球上的财经博主与散户讨论,结合当日大盘指数、市场情绪与板块资金流。定位**自用 / 研究**,数据管道由 Hermes 定时驱动,简报生成与飞书推送由 Hermes 负责。

> ⚠️ 仅个人低频自用。抓取微博 / 雪球违反其用户协议;请勿高频、勿商业化分发、勿公开转发原文。评论含他人个人信息,注意保护;建议用小号登录。

## 功能

- **微博抓取**(`weibo`)—— 扫码登录一次即免手动 cookie,走手机端 H5 接口抓指定博主的博文(含长文补全)+ 评论,SQLite 自动去重,支持首次全量 + 每日增量 + 断点续抓。
- **雪球抓取**(`xueqiu`)—— 一期抓全站**注意力信号**:热股榜(每日快照,数字硬指标)+ 7x24 快讯(短文本佐料);不再按博主抓时间线。
- **板块行情**(`market`)—— 盘后拉:大盘指数(A 股三大 + 海外)、市场情绪(涨跌家数 / 涨跌停 / 成交额 / 两融)、板块资金流(行业 + 概念全量,四级净额)、龙虎榜。基于 akshare。
- **日志告警**(`notify alert`)—— 扫描运行日志 / 熔断标记,有异常输出到 stdout(由 Hermes 投递)。

抓取用轻量 `requests` 直连,登录态用 Playwright 持久化;数据统一入 `data/app.db`。

## 安装

需要 Python 3.10+(开发用 3.13;Windows / 国内网络环境)。

```powershell
cd d:\work\AI\project\stock-watch
pip install -r requirements.txt
playwright install chromium        # 首次装浏览器驱动(几百 MB,登录用)
```

> 纯 Python 项目,**无需编译**。改完代码跑测试即可验证(见文末「跑测试」)。

## 配置

复制 `config.example.json` 为 `config.json` 再改。**必填**只有 `bloggers` 里的 uid;其余不写就用默认值。

下面是全部配置项的说明(**真实 `config.json` 是标准 JSON,不能带注释;请以 [config.example.json](config.example.json) 为准复制**):

```jsonc
{
  // ── 微博 ──
  "bloggers": [
    { "uid": "1234567890", "note": "某财经大V;uid = weibo.com/u/<数字>" }
  ],
  "max_post_pages": 2000,         // 博文翻页保险丝(每页约 10 篇),翻到底自然停
  "max_comment_pages": 5,         // 每篇博文评论翻几页
  "comment_refresh_days": 3,      // 增量时回头重刷最近几天博文的评论
  "enable_full_text": true,       // 长文是否抓详情页补全完整正文
  "comment_scope": "author_only", // author_only(只博主评论+父评论) / all / none
  "min_delay": 1.5,               // 每次请求前随机间隔(秒)下限,防风控
  "max_delay": 3.5,               // 上限
  "profile_dir": ".wb_profile",   // 微博登录态目录(= 账号凭证,勿提交)

  // ── 雪球(一期:抓全站注意力信号,不再按博主抓)──
  "xq_profile_dir": ".xq_profile",
  "xq_hot_rank_size": 100,        // 热股榜取前 N(每日快照,构成注意力时间序列)
  "xq_hot_rank_market": "沪深",    // 沪深 / 港 / 美
  "xq_livenews_max_pages": 5,     // 7x24 快讯单次最多翻几页(增量:遇已入库 id 停)
  // 下列旧键一期停用,保留供未来三期大 V 策略提炼:
  "xueqiu_bloggers": [], "xq_max_pages": 200, "xq_max_comment_pages": 5, "xq_comment_scope": "author_only",

  // ── 熔断 ──
  "auth_fail_breaker": 3,         // 连续多少个博主登录失效就熔断停抓

  // ── 行情 ──
  "market": {
    "top_n": 40                   // 预留
  },

  // ── 公共 ──
  "db_path": "data/app.db",       // SQLite 库路径
  "log_dir": "logs"               // 日志 / 熔断 flag 目录
}
```

LLM 模型配置与飞书推送由 **Hermes** 统一管理(stock-watch 只负责数据抓取与存储),本项目无需额外配置。

> `config.json` 含 uid,已在 `.gitignore`,勿提交;`config.example.json` 是不含隐私的模板。

## 快速启动

所有命令都在**仓库根目录**执行。

### 首次:登录 + 建全量基线

```powershell
python -m weibo login                    # 浏览器弹出,用微博 App 扫码
python -m xueqiu login                   # 雪球登录(扫码 / 账号密码)
python -m weibo crawl --mode full        # 翻遍历史博文 + 评论(可能几十分钟,支持断点续抓)
python -m xueqiu crawl                    # 雪球一期不分全量/增量:直接抓当天热股榜(快照)+ 7x24 快讯
```

### 日常:手动跑一轮

```powershell
python -m weibo crawl --mode incremental     # 只取新博文 + 刷新最近几天评论
python -m xueqiu crawl                        # 抓当天热股榜(快照 upsert)+ 7x24 快讯(增量)
python -m market snapshot                    # 盘后(交易日 15:30 后)拉大盘指数 + 市场情绪 + 板块资金流(行业+概念)+ 龙虎榜
python -m notify alert                       # 扫日志 ERROR + 熔断 flag
```

简报生成与飞书推送由 **Hermes agent** 负责(见 [hermes/SKILL.md](hermes/SKILL.md)),不在本项目中手动执行。

- 登录态存在 `.wb_profile/` / `.xq_profile/`,抓取自动复用;cookie 失效会报「登录态失效」,重跑对应 `login` 扫一次即可。
- 连续登录失效会**熔断**并写 `logs/weibo.paused` / `logs/xueqiu.paused`,人工处理后删掉该文件再跑。

## 注册到 Hermes(无人值守)

生产环境把命令交给 **Hermes 内置 cron** 到点自动跑,实现无人值守。**步骤见 [hermes/README.md](hermes/README.md)。**

首次部署推荐用一键引导脚本(Windows / Ubuntu / macOS 通用,可中断可重复跑),它会串起依赖安装、填 uid、装 / 配 Hermes、扫码登录、建 cron 全流程:

```powershell
python deploy.py     OR     py deploy.py
```

## 跑测试

纯 Python 无编译步骤;改完跑单元测试:

```powershell
python -m unittest discover -s tests
```

## 深入了解

- [AGENTS.md](AGENTS.md) —— 架构分层、各包内部原理(增量 / 断点续抓 / 字段裁剪)、微博接口陷阱与工程约定。
- [hermes/README.md](hermes/README.md) —— 注册到 Hermes 的完整步骤(脚本 / cron / gateway / 时刻表)。
- [hermes/SKILL.md](hermes/SKILL.md) —— Hermes agent 日常流水线蓝图(抓取→读数据→调 LLM→推飞书)。

## 合规提醒

- 自用 / 研究可接受;**别商业化、别高频、别公开转发原文**;推送只发 LLM 加工后的结论。
- 微博 / 雪球接口会变,字段一变就要跟着改;请求已带重试 / 退避,并区分登录失效(401/403)与限流 / 风控(418/429)。
- `.wb_profile/` / `.xq_profile/`(登录态 = 账号凭证)、`data/`(含他人信息)、`config.json`(含 uid)已在 `.gitignore`,**绝不要提交**。
