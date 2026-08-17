# 结构重构设计:更名 stock-watch + 抓取器包化 + 统一 CLI 契约

- 日期:2026-07-17
- 状态:已与用户对齐,待评审
- 关联:本设计只做**结构重构**,不改任何业务行为;既有 31 单测在重构后必须全绿。

## 1. 背景与动机

项目原名 `weibo-blogger-watch`,最初只抓微博。现已扩成**多源 A 股观察简报系统**:微博抓取、雪球抓取、行情快照(market)、LLM 简报(report)、飞书推送(notify)、Hermes 编排(hermes)。当前存在两处不一致:

1. **命名名不副实**:项目/目录仍叫 `weibo-blogger-watch`,微博只是众多数据源之一。
2. **模块形态不齐**:`market/`、`report/`、`notify/` 已是包;而微博(`main.py`/`crawler.py`/`login.py`/`storage.py`)与雪球(`xueqiu_crawl.py`/`xueqiu_crawler.py`/`xueqiu_login.py`/`xueqiu_storage.py`)是根目录扁平脚本。
3. **CLI 调用风格三套并存**(见 [hermes/SKILL.md](../../../hermes/SKILL.md)):脚本 `python main.py`、脚本 `python xueqiu_crawl.py`、包 `python -m market.cli`。对 agent(Hermes 及未来其他 agent)不友好,每个模块要记特例。

目标:统一命名、统一模块形态、统一 CLI 调用契约,便于 Hermes 及其他 agent 迁移调用。

## 2. 目标与非目标

**目标**
- 项目更名为 `stock-watch`,并把外层工作区目录一并改名。
- 微博、雪球各自收成一个 Python 包(`weibo/`、`xueqiu/`),与 `market/`/`report/`/`notify/` 形态一致。
- 共享层 `errors.py`/`obs.py` 收进 `common/` 包。
- 统一 CLI 契约为 `python -m <包> <动词>`(所有 5 个功能包一致,零特例)。
- 全部文档(README、hermes、docs)与配置中对旧名/旧路径/旧命令的引用同步更新。
- 重构后 31 单测全绿。

**非目标**
- 不改任何抓取/存储/简报/推送的业务逻辑与字段。
- 不改数据库结构、表名、`data/app.db` 文件名。
- 不引入向后兼容 shim(不保留旧的 `python main.py` 入口)。
- 不做 git 提交(当前非 git 仓库)。

## 3. 目标结构

```
stock-watch/
├─ weibo/      __init__.py  __main__.py  cli.py  login.py  crawler.py  storage.py
├─ xueqiu/     __init__.py  __main__.py  cli.py  login.py  crawler.py  storage.py
├─ market/     __init__.py  __main__.py  cli.py  fetcher.py  storage.py
├─ report/     __init__.py  __main__.py  cli.py  reader.py  prompt.py  llm.py  builder.py  storage.py
├─ notify/     __init__.py  __main__.py  cli.py  notifier.py
├─ common/     __init__.py  errors.py  obs.py
├─ hermes/     SKILL.md  cron.md
├─ tests/      (import 路径全部更新)
├─ docs/       superpowers/specs · superpowers/plans
├─ data/       app.db(运行时生成)
├─ logs/
├─ config.example.json   README.md   requirements.txt   AGENTS.md   .gitignore
```

### 文件搬迁映射

| 现在(根目录) | 迁移到 |
|---|---|
| `main.py` | `weibo/cli.py`(CLI 逻辑),新增 `weibo/__main__.py` 转调 |
| `crawler.py` | `weibo/crawler.py` |
| `login.py` | `weibo/login.py` |
| `storage.py` | `weibo/storage.py` |
| `xueqiu_crawl.py` | `xueqiu/cli.py`,新增 `xueqiu/__main__.py` 转调 |
| `xueqiu_crawler.py` | `xueqiu/crawler.py` |
| `xueqiu_login.py` | `xueqiu/login.py` |
| `xueqiu_storage.py` | `xueqiu/storage.py` |
| `errors.py` | `common/errors.py` |
| `obs.py` | `common/obs.py` |

`market/`、`report/`、`notify/` 内部文件不搬,仅各加一个 `__main__.py`。

## 4. 统一 CLI 契约

每个功能包放一个 `__main__.py`,内容为一行式转调各自 `cli.py` 的入口函数(如 `from .cli import main; main()`),使 `python -m <包>` 可直接运行。对外命令契约(Hermes / 任意 agent 唯一参照):

```
python -m weibo   login | crawl --mode full|incremental
python -m xueqiu  login | crawl --mode full|incremental
python -m market  snapshot
python -m report  build
python -m notify  send | alert
```

`cli.py` 内部 `argparse` 的 `prog` 应显示为 `python -m <包>`,帮助信息自洽。

## 5. import 改写规则

- 包内互引改相对导入:如 `weibo/cli.py` 引 `from .crawler import ...`、`from .storage import ...`、`from .login import ...`。
- 所有对共享层的引用:`import errors` / `import obs` → `from common import errors, obs`(或 `from common.errors import ...`),覆盖 `xueqiu/*`、`market/*`、`report/*`、`notify/*` 中所有引用点。
- `tests/` 下所有 `import` 改为新的包路径(如 `from weibo import storage`、`from common import obs`)。
- 运行方式从"脚本同目录导入"变为"包导入",因此所有命令须在**仓库根目录**下以 `python -m ...` 运行(与 Hermes 工作目录约定一致)。

## 6. 文档 / 配置更新点

- **README.md**:标题 `weibo-blogger-watch` → `stock-watch`;架构段、使用段、定时段、Hermes 段里的命令全部换成新契约;所有 `d:\work\AI\project\weibo-blogger-watch` 路径 → `stock-watch`;搬进 Hermes 的目录名同步。
- **hermes/SKILL.md、hermes/cron.md**:6 步命令与 cron 表换成新契约;登录命令改 `python -m weibo login` / `python -m xueqiu login`。
- **AGENTS.md**:项目名、架构文件清单(4 层文件路径)更新为包路径。
- **docs/ 既有 spec/plan**:项目名引用更新(不改历史事实描述)。
- **config.example.json**:无模块名引用,`db_path` 保持 `data/app.db` 不变。

## 7. 目录改名(用户手动步骤)

外层目录 `weibo-blogger-watch` → `stock-watch` 无法在工作区打开状态下由工具完成。文件**内部**引用由本次重构一并改好;目录改名由用户手动执行:

1. 关闭 VS Code(松开对该目录的占用)。
2. 资源管理器中把 `d:\work\AI\project\weibo-blogger-watch` 重命名为 `d:\work\AI\project\stock-watch`。
3. 重新用 VS Code 打开 `d:\work\AI\project\stock-watch`。

`.wb_profile/`、`.xq_profile/`、`data/`、`logs/`、`config.json` 均随目录移动,内容不受影响(路径为相对路径)。

## 8. 验收标准

- 目标结构落地,根目录不再有 8 个扁平抓取脚本与 2 个共享脚本。
- `python -m weibo/xueqiu/market/report/notify` 五条命令均可打印各自 `--help` 且子命令可用。
- `python -m unittest discover -s tests` → 31 测全绿。
- README / hermes / AGENTS.md / docs 中不再残留旧命令(`python main.py`、`python xueqiu_crawl.py`、`python -m *.cli`)与旧项目名(除历史事实注记外)。

## 9. 风险与缓解

- **漏改 import 导致运行/测试失败**:以"重跑 31 单测 + 五条 `--help` 冒烟"为硬门槛,红了即定位补改。
- **相对导入要求以包方式运行**:统一 `python -m` 契约后不再支持"直接 `python weibo/cli.py`",文档需明确"在仓库根目录运行"。
- **目录改名遗漏内部绝对路径**:重构阶段全库检索 `weibo-blogger-watch` 确保零残留。
