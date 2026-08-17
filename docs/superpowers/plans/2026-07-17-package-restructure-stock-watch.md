# stock-watch 结构重构实现计划(纯结构重构,零业务行为改动)

> **For agentic workers:** REQUIRED: Use the `subagent-driven-development` agent (recommended) or `executing-plans` agent to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把根目录扁平的微博/雪球脚本收成 `weibo/`、`xueqiu/` 包,共享层收进 `common/` 包,给 5 个功能包统一 `python -m <包> <动词>` CLI 契约,并同步全部文档/配置的旧名/旧命令/旧路径——全程不改任何业务逻辑,31 单测始终全绿。

**Architecture:** 纯文件搬迁 + import 改写。分 7 个阶段,**每个代码阶段结束后仓库都处于一致、可测状态**(31 测全绿),避免大爆炸式一次性改完再验证。搬迁用 PowerShell `Move-Item` 原样移动文件内容,再逐文件改小范围 import 行,内容零改写。市场/简报/推送三包内部已是绝对包导入(`from report.llm import ...`),本身可从仓库根运行,**不动**,只改 `import obs` 与加 `__main__.py`。

**Tech Stack:** Python 3、标准库 `argparse`/`unittest`、PowerShell(Windows,`Move-Item`)。无第三方构建。所有命令在**仓库根目录**执行。

---

## 权威依据与硬约束

- 唯一权威依据:[docs/superpowers/specs/2026-07-17-package-restructure-stock-watch-design.md](docs/superpowers/specs/2026-07-17-package-restructure-stock-watch-design.md)。本计划与设计冲突时以设计为准。
- **硬验收门槛(每个代码阶段末都要过,最终 Phase 6 再总验一次):**
  - `python -m unittest discover -s tests` → **Ran 31 tests … OK**(31 测全绿)。
  - 五条冒烟:`python -m weibo --help` / `python -m xueqiu --help` / `python -m market --help` / `python -m report --help` / `python -m notify --help` 均打印各自 usage 且 `prog` 显示为 `python -m <包>`。
- **不引入任何向后兼容 shim**:不保留旧 `python main.py` / `python xueqiu_crawl.py` 入口,不保留根目录旧脚本。
- **不做 git 操作**:当前非 git 仓库,本计划不含任何 `git add/commit/push`。
- **不动敏感/运行时产物**:`.wb_profile/`、`.xq_profile/`、`data/`、`logs/`、`config.json` 一律不移动、不修改、不提交。
- **保持 AGENTS.md 约定**:搬迁不得破坏——每个 `.py` 顶部 `from __future__ import annotations`;内置泛型标注;中文注释/docstring/CLI 输出;错误分层(抓取层 `RuntimeError` 子类 `AuthError`/`RateLimitError`,CLI 层捕获打印 `[warn]`/`[error]` 到 stderr,配置/输入错误抛 `SystemExit`)。本计划所有搬迁均保留原文件内容,仅改 import 与少量 docstring/提示文案,天然不破坏这些约定。
- **目录改名是用户手动步骤**(Phase 7):agent **不得**尝试重命名当前打开的工作区根目录。

---

## 现状核实结果(已用只读方式查清 — 供执行者对照)

搬迁后每个文件需改写的 import(**新路径 = 搬迁后的位置**):

| 文件(搬迁后) | 现有 import 行 | 改为 |
|---|---|---|
| `common/errors.py` | (无本地 import) | 不变 |
| `common/obs.py` | (无本地 import) | 不变 |
| `weibo/crawler.py` | `from errors import AuthError, RateLimitError` | `from common.errors import AuthError, RateLimitError` |
| `weibo/login.py` | (无本地 import) | 仅改 SystemExit 文案 |
| `weibo/storage.py` | (无本地 import) | 不变 |
| `weibo/cli.py`(原 main.py) | `import login as login_mod` / `import obs` / `from crawler import (...)` / `from errors import AuthError` / `from storage import Storage` | `from . import login as login_mod` / `from common import obs` / `from .crawler import (...)` / `from common.errors import AuthError` / `from .storage import Storage` |
| `xueqiu/crawler.py` | `from crawler import html_to_text, now_iso` / `from errors import AuthError, RateLimitError` | `from weibo.crawler import html_to_text, now_iso` / `from common.errors import AuthError, RateLimitError` |
| `xueqiu/login.py` | (无本地 import) | 不变 |
| `xueqiu/storage.py` | (无本地 import) | 不变 |
| `xueqiu/cli.py`(原 xueqiu_crawl.py) | `import obs` / `import xueqiu_login as login_mod` / `from errors import AuthError` / `from main import load_config` / `from xueqiu_crawler import (...)` / `from xueqiu_storage import XueqiuStorage` | `from common import obs` / `from . import login as login_mod` / `from common.errors import AuthError` / `from weibo.cli import load_config` / `from .crawler import (...)` / `from .storage import XueqiuStorage` |
| `market/cli.py` | `import obs` | `from common import obs`(+ 加 `prog` + 改 docstring) |
| `report/cli.py` | `import obs` | `from common import obs`(+ 加 `prog` + 改 docstring) |
| `notify/cli.py` | `import obs` | `from common import obs`(+ 加 `prog` + 改 docstring) |
| `tests/test_errors.py` | `from errors import AuthError, RateLimitError` | `from common.errors import AuthError, RateLimitError` |
| `tests/test_obs.py` | `import obs` | `from common import obs` |
| `tests/test_xueqiu_crawler.py` | `from xueqiu_crawler import slim_xq_post, select_xq_comments, xq_ms_to_epoch` | `from xueqiu.crawler import slim_xq_post, select_xq_comments, xq_ms_to_epoch` |
| `tests/test_xueqiu_storage.py` | `from xueqiu_storage import XueqiuStorage` | `from xueqiu.storage import XueqiuStorage` |

**不改的文件(重要):** `market/fetcher.py`、`market/storage.py`、`report/reader.py`、`report/prompt.py`、`report/llm.py`、`report/builder.py`、`report/storage.py`、`notify/notifier.py`、以及 8 个已用包导入的测试(test_market_fetcher/test_market_storage/test_notifier/test_notify_cli/test_report_llm/test_report_prompt/test_report_reader/test_report_storage)——它们的绝对包导入从仓库根照常工作,**不要触碰**。

---

## File Structure(搬迁与新增)

**新增目录/文件:**
- `common/__init__.py`、`weibo/__init__.py`、`weibo/__main__.py`、`xueqiu/__init__.py`、`xueqiu/__main__.py`、`market/__main__.py`、`report/__main__.py`、`notify/__main__.py`

**搬迁(`Move-Item`,内容不变,仅后续改 import):**
- `errors.py` → `common/errors.py`;`obs.py` → `common/obs.py`
- `crawler.py` → `weibo/crawler.py`;`login.py` → `weibo/login.py`;`storage.py` → `weibo/storage.py`;`main.py` → `weibo/cli.py`
- `xueqiu_crawler.py` → `xueqiu/crawler.py`;`xueqiu_login.py` → `xueqiu/login.py`;`xueqiu_storage.py` → `xueqiu/storage.py`;`xueqiu_crawl.py` → `xueqiu/cli.py`

**修改(仅文档/配置,Phase 5):** `README.md`、`AGENTS.md`、`hermes/SKILL.md`、`hermes/cron.md`

**基线确认:** 执行前先跑一次 `python -m unittest discover -s tests`,确认起点就是 31 绿(否则先与用户对齐,勿在红的基线上重构)。

---

## Phase 1 — 建立 `common/` 包并改写所有 obs/errors 引用

完成后:根目录仍有 8 个抓取脚本(位置不变),但 obs/errors 已进 `common/`,全库引用一致,31 测全绿。

### Task 1.1: 创建 common 包目录与 __init__.py

**Files:**
- Create: `common/__init__.py`

- [ ] **Step 1: 创建 `common/__init__.py`**

```python
"""共享层:错误类型(errors)与可观测性(obs)。"""
from __future__ import annotations
```

- [ ] **Step 2: 移动 errors.py 与 obs.py 进 common/**

Run(仓库根目录):
```powershell
Move-Item errors.py common\errors.py
Move-Item obs.py common\obs.py
```
Expected: 根目录不再有 `errors.py`/`obs.py`;`common\errors.py`、`common\obs.py` 存在。校验:
```powershell
Test-Path errors.py, obs.py, common\errors.py, common\obs.py
```
Expected: `False False True True`

### Task 1.2: 改写所有对 obs/errors 的引用

**Files:**
- Modify: `crawler.py`, `main.py`, `xueqiu_crawler.py`, `xueqiu_crawl.py`, `market/cli.py`, `report/cli.py`, `notify/cli.py`, `tests/test_errors.py`, `tests/test_obs.py`

> 说明:此阶段这些文件**仍在根目录**(除 market/report/notify/tests 已在各自目录)。仅改 obs/errors 引用;**不要**动 `from crawler import` / `from storage import` / `import login` / `from main import` / `from xueqiu_crawler import` 等——它们此刻仍按根名工作。

- [ ] **Step 1: `crawler.py`**

替换:
```python
from errors import AuthError, RateLimitError
```
为:
```python
from common.errors import AuthError, RateLimitError
```

- [ ] **Step 2: `main.py`(两处)**

替换:
```python
import login as login_mod
import obs
```
为:
```python
import login as login_mod
from common import obs
```
再替换:
```python
from errors import AuthError
```
为:
```python
from common.errors import AuthError
```

- [ ] **Step 3: `xueqiu_crawler.py`**

替换:
```python
from errors import AuthError, RateLimitError
```
为:
```python
from common.errors import AuthError, RateLimitError
```

- [ ] **Step 4: `xueqiu_crawl.py`(两处)**

替换:
```python
import obs
import xueqiu_login as login_mod
from errors import AuthError
```
为:
```python
from common import obs
import xueqiu_login as login_mod
from common.errors import AuthError
```

- [ ] **Step 5: `market/cli.py`**

替换:
```python
import obs
```
为:
```python
from common import obs
```

- [ ] **Step 6: `report/cli.py`**

替换:
```python
import obs
```
为:
```python
from common import obs
```

- [ ] **Step 7: `notify/cli.py`**

替换:
```python
import obs
```
为:
```python
from common import obs
```

- [ ] **Step 8: `tests/test_errors.py`**

替换:
```python
from errors import AuthError, RateLimitError
```
为:
```python
from common.errors import AuthError, RateLimitError
```

- [ ] **Step 9: `tests/test_obs.py`**

替换:
```python
import obs
```
为:
```python
from common import obs
```

- [ ] **Step 10: 验证 31 测全绿**

Run(仓库根目录):
```powershell
python -m unittest discover -s tests
```
Expected: 末尾 `Ran 31 tests in ...` + `OK`。若红:多半是漏改某处 obs/errors 引用,按报错文件定位补改后重跑。

---

## Phase 2 — 建立 `weibo/` 包

搬迁微博 4 文件,改包内相对导入,并把仍在根目录的雪球脚本对微博的跨引用指向 `weibo.*`。完成后 31 测全绿 + `python -m weibo --help` 冒烟通过。

### Task 2.1: 创建 weibo 包骨架

**Files:**
- Create: `weibo/__init__.py`, `weibo/__main__.py`

- [ ] **Step 1: 创建 `weibo/__init__.py`**

```python
"""微博抓取包:login / crawler / storage / cli。"""
from __future__ import annotations
```

- [ ] **Step 2: 创建 `weibo/__main__.py`**

```python
"""包入口:python -m weibo <login|crawl>。"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
```

### Task 2.2: 搬迁微博 4 文件

**Files:**
- Move: `crawler.py`→`weibo/crawler.py`, `login.py`→`weibo/login.py`, `storage.py`→`weibo/storage.py`, `main.py`→`weibo/cli.py`

- [ ] **Step 1: 移动文件**

Run(仓库根目录):
```powershell
Move-Item crawler.py weibo\crawler.py
Move-Item login.py   weibo\login.py
Move-Item storage.py weibo\storage.py
Move-Item main.py    weibo\cli.py
```
Expected 校验:
```powershell
Test-Path main.py, crawler.py, login.py, storage.py, weibo\cli.py, weibo\crawler.py, weibo\login.py, weibo\storage.py
```
Expected: `False False False False True True True True`

### Task 2.3: 改写 weibo 包内 import + docstring + prog

**Files:**
- Modify: `weibo/cli.py`, `weibo/login.py`

> `weibo/crawler.py` 的 `from common.errors import ...` 已在 Phase 1 完成,随文件搬来即正确,无需再改。

- [ ] **Step 1: `weibo/cli.py` — 改包内相对导入**

替换:
```python
import login as login_mod
from common import obs
from crawler import (
    WeiboClient,
    now_iso,
    select_comments,
    slim_post,
)
from common.errors import AuthError
from storage import Storage
```
为:
```python
from . import login as login_mod
from common import obs
from .crawler import (
    WeiboClient,
    now_iso,
    select_comments,
    slim_post,
)
from common.errors import AuthError
from .storage import Storage
```

- [ ] **Step 2: `weibo/cli.py` — 更新顶部 docstring 命令**

替换:
```python
"""CLI 编排。

命令:
  python main.py login                      扫码登录(首次 / 失效时)
  python main.py crawl --mode full          首次全量:翻遍每个博主全部博文+评论
  python main.py crawl --mode incremental   增量:只取新博文 + 刷新最近 N 天评论(默认)

博主列表、各种开关都在 config.json(从 config.example.json 复制)。
"""
```
为:
```python
"""CLI 编排。

命令:
  python -m weibo login                      扫码登录(首次 / 失效时)
  python -m weibo crawl --mode full          首次全量:翻遍每个博主全部博文+评论
  python -m weibo crawl --mode incremental   增量:只取新博文 + 刷新最近 N 天评论(默认)

博主列表、各种开关都在 config.json(从 config.example.json 复制)。
"""
```

- [ ] **Step 3: `weibo/cli.py` — argparse 加 prog**

替换:
```python
    ap = argparse.ArgumentParser(description="微博单/多博主博文+评论抓取(自用)")
```
为:
```python
    ap = argparse.ArgumentParser(prog="python -m weibo", description="微博单/多博主博文+评论抓取(自用)")
```

- [ ] **Step 4: `weibo/login.py` — 更新超时提示文案**

替换:
```python
        raise SystemExit("登录超时:未检测到登录态,请重试 `python main.py login`")
```
为:
```python
        raise SystemExit("登录超时:未检测到登录态,请重试 `python -m weibo login`")
```

### Task 2.4: 把根目录雪球脚本对微博的跨引用指向 weibo 包

**Files:**
- Modify: `xueqiu_crawler.py`, `xueqiu_crawl.py`

> 雪球脚本此阶段仍在根目录,但它们引用的 `crawler`/`main` 已搬进 `weibo/`,必须立即改指,否则 import 断裂。

- [ ] **Step 1: `xueqiu_crawler.py`**

替换:
```python
from weibo.crawler import html_to_text, now_iso
from common.errors import AuthError, RateLimitError
```
> 注意:当前文件里这两行是 `from crawler import html_to_text, now_iso` 与(Phase 1 已改成)`from common.errors import ...`。把 `from crawler import ...` 改为 `from weibo.crawler import ...`。最终两行应如上所示。

- [ ] **Step 2: `xueqiu_crawl.py`**

替换:
```python
from main import load_config
```
为:
```python
from weibo.cli import load_config
```

### Task 2.5: 验证 Phase 2

- [ ] **Step 1: 31 测全绿**

Run:
```powershell
python -m unittest discover -s tests
```
Expected: `Ran 31 tests ...` + `OK`。(test_xueqiu_* 仍从根 `xueqiu_crawler`/`xueqiu_storage` 导入,而 `xueqiu_crawler` 现引用 `weibo.crawler` + `common.errors`,均可从根解析。)

- [ ] **Step 2: weibo CLI 冒烟**

Run:
```powershell
python -m weibo --help
```
Expected: 打印 usage,首行形如 `usage: python -m weibo [-h] [--config CONFIG] {login,crawl} ...`。

---

## Phase 3 — 建立 `xueqiu/` 包

### Task 3.1: 创建 xueqiu 包骨架

**Files:**
- Create: `xueqiu/__init__.py`, `xueqiu/__main__.py`

- [ ] **Step 1: 创建 `xueqiu/__init__.py`**

```python
"""雪球抓取包:login / crawler / storage / cli。"""
from __future__ import annotations
```

- [ ] **Step 2: 创建 `xueqiu/__main__.py`**

```python
"""包入口:python -m xueqiu <login|crawl>。"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
```

### Task 3.2: 搬迁雪球 4 文件

**Files:**
- Move: `xueqiu_crawler.py`→`xueqiu/crawler.py`, `xueqiu_login.py`→`xueqiu/login.py`, `xueqiu_storage.py`→`xueqiu/storage.py`, `xueqiu_crawl.py`→`xueqiu/cli.py`

- [ ] **Step 1: 移动文件**

Run(仓库根目录):
```powershell
Move-Item xueqiu_crawler.py xueqiu\crawler.py
Move-Item xueqiu_login.py   xueqiu\login.py
Move-Item xueqiu_storage.py xueqiu\storage.py
Move-Item xueqiu_crawl.py   xueqiu\cli.py
```
Expected 校验:
```powershell
Test-Path xueqiu_crawl.py, xueqiu_crawler.py, xueqiu\cli.py, xueqiu\crawler.py, xueqiu\login.py, xueqiu\storage.py
```
Expected: `False False True True True True`

### Task 3.3: 改写 xueqiu 包内 import + docstring + prog

**Files:**
- Modify: `xueqiu/cli.py`

> `xueqiu/crawler.py` 的 `from weibo.crawler import ...`(Phase 2 已改)与 `from common.errors import ...`(Phase 1 已改)随文件搬来即正确,无需再改。

- [ ] **Step 1: `xueqiu/cli.py` — 改包内相对导入**

替换:
```python
from common import obs
import xueqiu_login as login_mod
from common.errors import AuthError
from weibo.cli import load_config
from xueqiu_crawler import (
    XueqiuClient,
    now_iso,
    select_xq_comments,
    slim_xq_post,
)
from xueqiu_storage import XueqiuStorage
```
为:
```python
from common import obs
from . import login as login_mod
from common.errors import AuthError
from weibo.cli import load_config
from .crawler import (
    XueqiuClient,
    now_iso,
    select_xq_comments,
    slim_xq_post,
)
from .storage import XueqiuStorage
```

- [ ] **Step 2: `xueqiu/cli.py` — 更新顶部 docstring 命令**

替换:
```python
"""雪球 CLI 编排(镜像 main.py)。

命令:
  python xueqiu_crawl.py login                    登录(首次 / 失效时)
  python xueqiu_crawl.py crawl --mode full        全量:翻遍每个博主时间线 + 评论
  python xueqiu_crawl.py crawl --mode incremental 增量:只取比 last_ts 新的博文 + 刷新近 N 天评论

复用 main.py 的 config(同 config.json),读 xueqiu_bloggers / xq_* 配置项。
"""
```
为:
```python
"""雪球 CLI 编排(镜像 weibo 包)。

命令:
  python -m xueqiu login                    登录(首次 / 失效时)
  python -m xueqiu crawl --mode full        全量:翻遍每个博主时间线 + 评论
  python -m xueqiu crawl --mode incremental 增量:只取比 last_ts 新的博文 + 刷新近 N 天评论

复用 weibo 包的 load_config(同 config.json),读 xueqiu_bloggers / xq_* 配置项。
"""
```

- [ ] **Step 3: `xueqiu/cli.py` — argparse 加 prog**

替换:
```python
    ap = argparse.ArgumentParser(description="雪球单/多博主博文+评论抓取(自用)")
```
为:
```python
    ap = argparse.ArgumentParser(prog="python -m xueqiu", description="雪球单/多博主博文+评论抓取(自用)")
```

### Task 3.4: 更新雪球测试的 import

**Files:**
- Modify: `tests/test_xueqiu_crawler.py`, `tests/test_xueqiu_storage.py`

- [ ] **Step 1: `tests/test_xueqiu_crawler.py`**

替换:
```python
from xueqiu_crawler import slim_xq_post, select_xq_comments, xq_ms_to_epoch
```
为:
```python
from xueqiu.crawler import slim_xq_post, select_xq_comments, xq_ms_to_epoch
```

- [ ] **Step 2: `tests/test_xueqiu_storage.py`**

替换:
```python
from xueqiu_storage import XueqiuStorage
```
为:
```python
from xueqiu.storage import XueqiuStorage
```

### Task 3.5: 验证 Phase 3

- [ ] **Step 1: 31 测全绿**

Run:
```powershell
python -m unittest discover -s tests
```
Expected: `Ran 31 tests ...` + `OK`。

- [ ] **Step 2: xueqiu CLI 冒烟**

Run:
```powershell
python -m xueqiu --help
```
Expected: usage 首行形如 `usage: python -m xueqiu [-h] [--config CONFIG] {login,crawl} ...`。

---

## Phase 4 — 给 market / report / notify 加 `__main__.py` + prog + docstring

三包内部文件不搬、内部绝对导入不动;`import obs` 已在 Phase 1 改成 `from common import obs`。本阶段只补统一入口与自洽帮助。

### Task 4.1: 创建三个 __main__.py

**Files:**
- Create: `market/__main__.py`, `report/__main__.py`, `notify/__main__.py`

- [ ] **Step 1: `market/__main__.py`**

```python
"""包入口:python -m market <snapshot>。"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: `report/__main__.py`**

```python
"""包入口:python -m report <build>。"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: `notify/__main__.py`**

```python
"""包入口:python -m notify <send|alert>。"""
from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    main()
```

### Task 4.2: 加 prog + 更新 docstring

**Files:**
- Modify: `market/cli.py`, `report/cli.py`, `notify/cli.py`

- [ ] **Step 1: `market/cli.py` — docstring**

替换:
```python
  python -m market.cli snapshot [--date YYYYMMDD] [--config config.json]
```
为:
```python
  python -m market snapshot [--date YYYYMMDD] [--config config.json]
```

- [ ] **Step 2: `market/cli.py` — prog**

替换:
```python
    ap = argparse.ArgumentParser(description="A 股板块行情 + 龙虎榜快照(自用)")
```
为:
```python
    ap = argparse.ArgumentParser(prog="python -m market", description="A 股板块行情 + 龙虎榜快照(自用)")
```

- [ ] **Step 3: `report/cli.py` — docstring**

替换:
```python
"""简报 CLI:python -m report.cli build [--date YYYYMMDD]
```
为:
```python
"""简报 CLI:python -m report build [--date YYYYMMDD]
```

- [ ] **Step 4: `report/cli.py` — prog**

替换:
```python
    ap = argparse.ArgumentParser(description="每日观察简报生成(自用)")
```
为:
```python
    ap = argparse.ArgumentParser(prog="python -m report", description="每日观察简报生成(自用)")
```

- [ ] **Step 5: `notify/cli.py` — docstring**

替换:
```python
  python -m notify.cli send  [--date YYYY-MM-DD]   推当天简报(读 daily_report)
  python -m notify.cli alert [--modules weibo,xueqiu,market,report]
```
为:
```python
  python -m notify send  [--date YYYY-MM-DD]   推当天简报(读 daily_report)
  python -m notify alert [--modules weibo,xueqiu,market,report]
```

- [ ] **Step 6: `notify/cli.py` — prog**

> `notify/cli.py` 的 `main()` 内是 `import argparse` 后再 `ap = argparse.ArgumentParser(...)`。只改 ArgumentParser 那一行。

替换:
```python
    ap = argparse.ArgumentParser(description="统一推送(简报 + 告警)")
```
为:
```python
    ap = argparse.ArgumentParser(prog="python -m notify", description="统一推送(简报 + 告警)")
```

### Task 4.3: 验证 Phase 4

- [ ] **Step 1: 31 测全绿**

Run:
```powershell
python -m unittest discover -s tests
```
Expected: `Ran 31 tests ...` + `OK`。

- [ ] **Step 2: 三包 CLI 冒烟**

Run:
```powershell
python -m market --help
python -m report --help
python -m notify --help
```
Expected: 三条各自打印 usage,`prog` 分别为 `python -m market` / `python -m report` / `python -m notify`;`notify` 的子命令含 `{send,alert}`。

---

## Phase 5 — 文档 / 配置同步更新

按设计 §6/§8:全量更新 `README.md`、`AGENTS.md`、`hermes/SKILL.md`、`hermes/cron.md` 的旧命令/旧项目名/旧路径。**历史事实类文档不动**:`docs/superpowers/plans/2026-07-16-daily-stock-brief-phase1.md`、`docs/superpowers/specs/2026-07-16-daily-stock-brief-phase1-design.md`、以及本次设计文档 `docs/superpowers/specs/2026-07-17-package-restructure-stock-watch-design.md`(它们记录历史/动机,`weibo-blogger-watch` 与旧命令是历史事实注记,保留)。`config.example.json` 无模块名引用,`db_path` 保持 `data/app.db`,不改。

### Task 5.1: 更新 README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 标题**

替换:
```markdown
# weibo-blogger-watch
```
为:
```markdown
# stock-watch
```

- [ ] **Step 2: 架构块文件名**

替换:
```
login.py    Playwright 扫码登录 → 登录态持久化到 .wb_profile/
              登录后 SSO 到 m.weibo.cn 种下手机端 cookie;抓取时 headless 复用取最新 cookie
crawler.py  requests 直连 m 站:博文列表(getIndex/since_id) + 长文补全(detail) + 评论(hotflow)
storage.py  SQLite:posts / comments / crawl_state(id 主键→天然去重)
main.py     CLI:login / crawl --mode full|incremental;博主列表读 config.json
```
为:
```
weibo/login.py    Playwright 扫码登录 → 登录态持久化到 .wb_profile/
                    登录后 SSO 到 m.weibo.cn 种下手机端 cookie;抓取时 headless 复用取最新 cookie
weibo/crawler.py  requests 直连 m 站:博文列表(getIndex/since_id) + 长文补全(detail) + 评论(hotflow)
weibo/storage.py  SQLite:posts / comments / crawl_state(id 主键→天然去重)
weibo/cli.py      CLI:login / crawl --mode full|incremental;博主列表读 config.json
```

- [ ] **Step 3: 依赖安装块的 cd 路径**

替换:
```powershell
cd d:\work\AI\project\weibo-blogger-watch
```
为:
```powershell
cd d:\work\AI\project\stock-watch
```

- [ ] **Step 4: 使用块命令(3 条 + 提示行)**

替换:
```powershell
# 1. 首次扫码登录(浏览器弹出,用微博 App 扫码;登录态存 .wb_profile/)
python main.py login

# 2. 首次全量(翻遍每个博主历史博文 + 抓评论)
python main.py crawl --mode full

# 3. 之后增量(只取新博文 + 刷新最近 3 天评论)—— 定时用这个
python main.py crawl --mode incremental
```
为:
```powershell
# 1. 首次扫码登录(浏览器弹出,用微博 App 扫码;登录态存 .wb_profile/)
python -m weibo login

# 2. 首次全量(翻遍每个博主历史博文 + 抓评论)
python -m weibo crawl --mode full

# 3. 之后增量(只取新博文 + 刷新最近 3 天评论)—— 定时用这个
python -m weibo crawl --mode incremental
```
再替换:
```markdown
cookie 失效时抓取会报 "登录态失效",重跑 `python main.py login` 扫一次即可。
```
为:
```markdown
cookie 失效时抓取会报 "登录态失效",重跑 `python -m weibo login` 扫一次即可。
```

- [ ] **Step 5: 本机定时块(命令 + WorkingDirectory)**

替换:
```powershell
$action = New-ScheduledTaskAction -Execute "python" `
  -Argument "main.py crawl --mode incremental" `
  -WorkingDirectory "d:\work\AI\project\weibo-blogger-watch"
```
为:
```powershell
$action = New-ScheduledTaskAction -Execute "python" `
  -Argument "-m weibo crawl --mode incremental" `
  -WorkingDirectory "d:\work\AI\project\stock-watch"
```

- [ ] **Step 6: Hermes 接入块(目录名 + 命令)**

替换:
```markdown
1. 把本目录搬进 `~/.hermes/skills/finance/weibo-blogger-watch/scripts/`。
2. 加 `SKILL.md`,让 agent 用 terminal 调 `python main.py crawl --mode incremental`,再从 `data/app.db` 读数据。
```
为:
```markdown
1. 把本目录搬进 `~/.hermes/skills/finance/stock-watch/scripts/`。
2. 加 `SKILL.md`,让 agent 用 terminal 调 `python -m weibo crawl --mode incremental`,再从 `data/app.db` 读数据。
```

- [ ] **Step 7: 校验 README 无残留**

Run(仓库根目录):
```powershell
Select-String -Path README.md -Pattern "weibo-blogger-watch","python main\.py","python xueqiu_crawl\.py","python -m \w+\.cli"
```
Expected: 无输出(全部命中已清)。

### Task 5.2: 更新 AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: 架构块(4 层文件路径 + 标题描述)**

替换:
```markdown
## 架构(4 层,职责单一)

- [login.py](login.py) — Playwright 扫码登录,登录态持久化到 `.wb_profile/`;抓取时 headless 复用该 profile 现取 cookie(自动保鲜)。`get_cookie_header()` 拼出 requests 用的 Cookie 头。
- [crawler.py](crawler.py) — `requests` 直连微博**手机端 H5 接口**(`m.weibo.cn`,参照 MediaCrawler):博文列表 `getIndex`(containerid `107603<uid>`,`since_id` 游标翻页)、长文补全(`/detail/<mid>` 的 `$render_data`,带重试)、评论 `hotflow`。带重试/退避 + 被封刷 cookie + 登录预检(`/api/config`)+ 抓前 `prime_creator`(100505 容器)预热。`slim_post`/`slim_comment` 把 HTML 裁纯文本入库(保留 `raw_json`、转发原文、配图 URL);`select_comments` 按 `comment_scope` 过滤(默认 `author_only` 只留博主评论+其父评论)。
- [storage.py](storage.py) — SQLite,三表 `posts`/`comments`/`crawl_state`。评论 `INSERT OR REPLACE` 去重;博文 `ON CONFLICT DO UPDATE`(保留 `comments_done` 断点)。`crawl_state` 存增量水位 `last_max_id` + 全量断点 `full_since_id`/`full_done`。
- [main.py](main.py) — CLI 编排,`crawl_one()` 是核心抓取循环;博主列表与开关读 `config.json`。
```
为:
```markdown
## 架构(微博包 4 层,职责单一)

- [weibo/login.py](weibo/login.py) — Playwright 扫码登录,登录态持久化到 `.wb_profile/`;抓取时 headless 复用该 profile 现取 cookie(自动保鲜)。`get_cookie_header()` 拼出 requests 用的 Cookie 头。
- [weibo/crawler.py](weibo/crawler.py) — `requests` 直连微博**手机端 H5 接口**(`m.weibo.cn`,参照 MediaCrawler):博文列表 `getIndex`(containerid `107603<uid>`,`since_id` 游标翻页)、长文补全(`/detail/<mid>` 的 `$render_data`,带重试)、评论 `hotflow`。带重试/退避 + 被封刷 cookie + 登录预检(`/api/config`)+ 抓前 `prime_creator`(100505 容器)预热。`slim_post`/`slim_comment` 把 HTML 裁纯文本入库(保留 `raw_json`、转发原文、配图 URL);`select_comments` 按 `comment_scope` 过滤(默认 `author_only` 只留博主评论+其父评论)。
- [weibo/storage.py](weibo/storage.py) — SQLite,三表 `posts`/`comments`/`crawl_state`。评论 `INSERT OR REPLACE` 去重;博文 `ON CONFLICT DO UPDATE`(保留 `comments_done` 断点)。`crawl_state` 存增量水位 `last_max_id` + 全量断点 `full_since_id`/`full_done`。
- [weibo/cli.py](weibo/cli.py) — CLI 编排,`crawl_one()` 是核心抓取循环;博主列表与开关读 `config.json`。雪球包 [xueqiu/](xueqiu/) 结构镜像微博包;共享层在 [common/](common/)(errors + obs);行情/简报/推送在 [market/](market/)、[report/](report/)、[notify/](notify/)。
```

- [ ] **Step 2: 常用命令块**

替换:
```powershell
pip install -r requirements.txt
playwright install chromium                     # 首次装浏览器驱动
python main.py login                            # 扫码登录 / 失效重登
python main.py crawl --mode full                # 首次全量(建基线)
python main.py crawl --mode incremental         # 增量(定时用这个)
```
为:
```powershell
pip install -r requirements.txt
playwright install chromium                     # 首次装浏览器驱动
python -m weibo login                           # 扫码登录 / 失效重登
python -m weibo crawl --mode full               # 首次全量(建基线)
python -m weibo crawl --mode incremental        # 增量(定时用这个)
```
再替换命令块下方一行:
```markdown
无自动化测试;改动后手动跑 `crawl` 验证。存储去重逻辑可用临时 SQLite 单测验证。
```
为:
```markdown
统一 CLI 契约:`python -m <包> <动词>`(weibo/xueqiu login|crawl;market snapshot;report build;notify send|alert)。所有命令在仓库根目录运行。测试:`python -m unittest discover -s tests`(31 测)。
```

- [ ] **Step 3: 项目约定块的 DEFAULTS 引用**

替换:
```markdown
- 新增可配置项:先加进 [main.py](main.py) 的 `DEFAULTS`,再在 [config.example.json](config.example.json) 与 README 里同步说明。
```
为:
```markdown
- 新增可配置项:先加进 [weibo/cli.py](weibo/cli.py) 的 `DEFAULTS`,再在 [config.example.json](config.example.json) 与 README 里同步说明。
```

- [ ] **Step 4: 陷阱块的 storage 链接**

替换:
```markdown
- 改字段裁剪只动 `slim_post` / `slim_comment`;新增入库字段要同步改 [storage.py](storage.py) 的 `SCHEMA` 和对应 upsert SQL。
```
为:
```markdown
- 改字段裁剪只动 `slim_post` / `slim_comment`;新增入库字段要同步改 [weibo/storage.py](weibo/storage.py) 的 `SCHEMA` 和对应 upsert SQL。
```

- [ ] **Step 5: 校验 AGENTS.md 无残留**

Run:
```powershell
Select-String -Path AGENTS.md -Pattern "\(main\.py\)","\(crawler\.py\)","\(login\.py\)","\(storage\.py\)","python main\.py"
```
Expected: 无输出。

### Task 5.3: 更新 hermes/SKILL.md

**Files:**
- Modify: `hermes/SKILL.md`

- [ ] **Step 1: 前置块登录命令**

替换:
```markdown
- 部署主机=国内网络 Windows;已 `python xueqiu_crawl.py login` 与 `python main.py login` 完成登录态。
```
为:
```markdown
- 部署主机=国内网络 Windows;已 `python -m xueqiu login` 与 `python -m weibo login` 完成登录态。
```

- [ ] **Step 2: 6 步标准流程命令**

替换:
```markdown
1. 微博增量:`python main.py crawl --mode incremental`
2. 雪球增量:`python xueqiu_crawl.py crawl --mode incremental`
3. 盘后行情快照(交易日 15:30 后):`python -m market.cli snapshot`
4. 收盘后简报:`python -m report.cli build`
5. 推送简报:`python -m notify.cli send`
6. 告警检查(每轮末必做):`python -m notify.cli alert`
```
为:
```markdown
1. 微博增量:`python -m weibo crawl --mode incremental`
2. 雪球增量:`python -m xueqiu crawl --mode incremental`
3. 盘后行情快照(交易日 15:30 后):`python -m market snapshot`
4. 收盘后简报:`python -m report build`
5. 推送简报:`python -m notify send`
6. 告警检查(每轮末必做):`python -m notify alert`
```

- [ ] **Step 3: 校验 SKILL.md 无残留**

Run:
```powershell
Select-String -Path hermes\SKILL.md -Pattern "python main\.py","python xueqiu_crawl\.py","python -m \w+\.cli"
```
Expected: 无输出。

### Task 5.4: 更新 hermes/cron.md

**Files:**
- Modify: `hermes/cron.md`

- [ ] **Step 1: 时刻表命令列**

替换:
```markdown
| 微博+雪球增量 | 09:40 / 12:30 / 14:30 | main.py + xueqiu_crawl.py crawl --mode incremental |
| 盘后行情快照 | 交易日 15:35 | python -m market.cli snapshot |
| 生成简报 | 交易日 16:00 | python -m report.cli build |
| 推送简报 | 交易日 16:05 | python -m notify.cli send |
| 告警检查 | 每次任务后 | python -m notify.cli alert |
```
为:
```markdown
| 微博+雪球增量 | 09:40 / 12:30 / 14:30 | python -m weibo + python -m xueqiu crawl --mode incremental |
| 盘后行情快照 | 交易日 15:35 | python -m market snapshot |
| 生成简报 | 交易日 16:00 | python -m report build |
| 推送简报 | 交易日 16:05 | python -m notify send |
| 告警检查 | 每次任务后 | python -m notify alert |
```

- [ ] **Step 2: 校验 cron.md 无残留**

Run:
```powershell
Select-String -Path hermes\cron.md -Pattern "main\.py","xueqiu_crawl\.py","python -m \w+\.cli"
```
Expected: 无输出。

---

## Phase 6 — 最终总验收

- [ ] **Step 1: 目标结构落地检查**

Run(仓库根目录):
```powershell
Test-Path weibo\__init__.py, weibo\__main__.py, weibo\cli.py, weibo\crawler.py, weibo\login.py, weibo\storage.py, xueqiu\__init__.py, xueqiu\__main__.py, xueqiu\cli.py, xueqiu\crawler.py, xueqiu\login.py, xueqiu\storage.py, common\__init__.py, common\errors.py, common\obs.py, market\__main__.py, report\__main__.py, notify\__main__.py
```
Expected: 全部 `True`。

再确认根目录 8 个扁平抓取脚本 + 2 个共享脚本已消失:
```powershell
Test-Path main.py, crawler.py, login.py, storage.py, xueqiu_crawl.py, xueqiu_crawler.py, xueqiu_login.py, xueqiu_storage.py, errors.py, obs.py
```
Expected: 全部 `False`。

- [ ] **Step 2: 31 单测全绿(硬门槛)**

Run:
```powershell
python -m unittest discover -s tests
```
Expected: `Ran 31 tests in ...` + `OK`。

- [ ] **Step 3: 五条 CLI --help 冒烟(硬门槛)**

Run:
```powershell
python -m weibo --help
python -m xueqiu --help
python -m market --help
python -m report --help
python -m notify --help
```
Expected: 五条各自打印 usage 且不报错;`prog` 分别显示 `python -m weibo|xueqiu|market|report|notify`。

- [ ] **Step 4: 全库旧命令/旧名残留检查(排除历史文档)**

Run(仓库根目录):
```powershell
Get-ChildItem -Recurse -Include *.py,*.md -File |
  Where-Object { $_.FullName -notmatch "\\docs\\superpowers\\" } |
  Select-String -Pattern "python main\.py","python xueqiu_crawl\.py","python -m \w+\.cli","weibo-blogger-watch"
```
Expected: 无输出。(`docs/superpowers/` 下的历史 spec/plan 与本次设计属历史事实注记,已排除。)

---

## Phase 7 — 目录改名(⚠️ 用户手动步骤,agent 不执行)

外层工作区目录无法在打开状态下由工具重命名。文件**内部**引用已在 Phase 5 全部改好,只剩外层目录改名,由**用户手动**完成:

1. 关闭 VS Code(松开对该目录的占用)。
2. 资源管理器中把 `d:\work\AI\project\weibo-blogger-watch` 重命名为 `d:\work\AI\project\stock-watch`。
3. 重新用 VS Code 打开 `d:\work\AI\project\stock-watch`。

`.wb_profile/`、`.xq_profile/`、`data/`、`logs/`、`config.json` 随目录移动、内容不受影响(均为相对路径)。改名后可再跑一次 Phase 6 Step 2/3 确认无碍。

---

## 自查清单(计划作者已核对)

- **设计覆盖**:更名(§Phase 5 + Phase 7)、weibo 包(Phase 2)、xueqiu 包(Phase 3)、common 包(Phase 1)、5 包统一 `__main__.py`+prog 契约(Phase 2/3/4)、import 全改写(现状表 + Phase 1–3)、文档/配置同步(Phase 5)、31 测+5 冒烟硬门槛(每阶段 + Phase 6)、无 shim/无 git/不动敏感物(约束段)——逐条对齐设计 §2–§9。
- **无占位符**:所有代码步骤均给出可直接粘贴的完整内容或精确 old→new 行替换。
- **类型/名称一致性**:入口函数统一 `main()`;`__main__.py` 统一 `from .cli import main`;跨包引用统一 `from weibo.cli import load_config`、`from weibo.crawler import ...`、`from common.errors import ...`、`from common import obs`;prog 统一 `python -m <包>`。全程一致。
- **阶段可独立验证**:Phase 1–4 每阶段末都能跑通 31 测(中间态刻意保持可运行),不是最后才验。
