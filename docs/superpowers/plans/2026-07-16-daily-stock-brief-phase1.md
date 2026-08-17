# 每日股票观察简报系统 · 一期 实现计划

> **给执行者(agentic worker):** 必须用 `subagent-driven-development` agent(推荐)或 `executing-plans` agent 逐任务执行本计划。步骤用 `- [ ]` 复选框追踪进度。
> **配套设计:** [docs/superpowers/specs/2026-07-16-daily-stock-brief-phase1-design.md](docs/superpowers/specs/2026-07-16-daily-stock-brief-phase1-design.md)。执行前先通读设计文档第 3、5、6、9 节。

**目标(Goal):** 在现有微博抓取工程之上,补齐雪球抓取、行情快照、LLM 简报、统一推送、监控熔断五个组件,并用 Hermes cron 串成无人值守的"每天一份中文观察简报 + 出错告警"最小闭环。

**架构(Architecture):** 沿用现有单库 `data/app.db`,新增表而非新建库。新增雪球抓取脚本(镜像微博的 login/crawler/storage/CLI 四层),`market/`、`report/`、`notify/` 三个包,以及共享的 `obs.py`(结构化日志 + 熔断 flag)与 `errors.py`(分型异常)。抓取/行情/简报都是独立可单跑的 Python 脚本;Hermes 只做定时编排 + 推送出口。

**技术栈(Tech Stack):** Python 3.11、requests(已有,复用来调雪球 H5 接口、OpenAI 兼容 LLM、飞书 webhook)、playwright(已有,复用登录态持久化)、akshare/adata(新增,行情薄封装)、stdlib `logging`/`unittest`(结构化日志 + 纯逻辑单测,不引入 pytest)、Hermes(编排 + 推送)。

**约定(必须遵守,来自 [AGENTS.md](AGENTS.md)):**
- 每个文件顶部 `from __future__ import annotations`;类型用内置泛型(`list[dict]`、`str | None`)。
- 每模块顶部中文 docstring;新增函数用简短中文 docstring;注释/CLI 输出全中文。
- 错误分层:抓取层把 HTTP 状态翻译成 `RuntimeError`(及其子类);CLI 层捕获后打印 `[warn]`/`[error]` 到 stderr,不中断整批;用户输入/配置错误抛 `SystemExit`(中文提示)。
- 不加向后兼容 shim / feature flag / 过度错误兜底;只在系统边界(HTTP/DB/第三方库字段)做防御。
- 无 pytest;纯逻辑用 `python -m unittest`,网络/浏览器/LLM/webhook 靠手动跑验证 + 注入假对象做单测。
- 微博/雪球 m 站与 akshare 接口字段易变:报错优先"对字段",返回非 JSON 通常是被反爬而非 bug。
- 合规:`.wb_profile/`、`.xq_profile/`、`data/`、`logs/`、`config.json` 绝不提交;推送只给 LLM 加工结论,不搬原文;密钥走环境变量不入库。

---

## 文件结构(先锁定分解)

新增/修改一览。每个文件单一职责,对外只暴露 CLI 或清晰函数入口。

**共享基础(Phase 0)**
- 新建 `errors.py` — 分型异常 `AuthError`/`RateLimitError`(均继承 `RuntimeError`,不破坏现有 `except RuntimeError`)。
- 新建 `obs.py` — 统一 `logging` 配置(写 `logs/<模块>-YYYY-MM-DD.log`,每行含级别 + 步骤标签)、熔断 flag 读写、给 Hermes 用的 ERROR/flag 扫描(按级别字段判定,带偏移量避免重复告警)。
- 修改 `crawler.py` — `_get_json` 把 401/403 抛 `AuthError`、418/429 抛 `RateLimitError`(其余不变)。
- 修改 `main.py` — cmd_crawl 接入 obs 日志 + 连续 401/403 熔断 + 启动查 flag。
- 修改 `requirements.txt`、`config.example.json`、`.gitignore`。

**雪球抓取(Phase 1),镜像微博四层**
- 新建 `xueqiu_login.py` — playwright 扫码登录 + 登录态持久化到 `.xq_profile/`,`get_cookie_header()`。
- 新建 `xueqiu_crawler.py` — `XueqiuClient`(requests 直连雪球 H5/API + 重试退避 + 分型异常)、`slim_xq_post`/`slim_xq_comment`/`select_xq_comments`。
- 新建 `xueqiu_storage.py` — `XueqiuStorage`,表 `xq_posts`/`xq_comments`/`xq_crawl_state`(结构对齐微博)。
- 新建 `xueqiu_crawl.py` — CLI:`login` / `crawl --mode full|incremental`,含熔断接入。

**行情工具(Phase 2)**
- 新建 `market/__init__.py`
- 新建 `market/storage.py` — `MarketStorage`,表 `market_snapshot`(近似口径字段名带 `_est`)、`dragon_tiger`。
- 新建 `market/fetcher.py` — akshare 薄封装(板块涨跌/成交/资金流近似口径 + 龙虎榜),第三方库以参数注入便于测试。
- 新建 `market/cli.py` — `python -m market.cli snapshot [--date YYYYMMDD]`。

**简报生成(Phase 3)**
- 新建 `report/__init__.py`
- 新建 `report/storage.py` — `ReportStorage`,表 `daily_report`。
- 新建 `report/reader.py` — 从 `data/app.db` 读当天新文本 + 当天行情快照,返回结构化 dict。
- 新建 `report/prompt.py` — 纯函数组 prompt(system/user)。
- 新建 `report/llm.py` — requests 调 OpenAI 兼容 `/chat/completions`(base_url/model 走 config,key 走环境变量),http 客户端可注入。
- 新建 `report/builder.py` + `report/cli.py` — 编排 reader→prompt→llm→存档;`python -m report.cli build [--date]`。

**推送 + 告警(Phase 4)**
- 新建 `notify/__init__.py`
- 新建 `notify/notifier.py` — 统一出口 `send(title, body, level)` → 飞书自定义机器人 webhook(可选签名),http 可注入。
- 新建 `notify/cli.py` — `send --date`(推当天简报)、`alert`(扫 ERROR/flag,有则推告警)。

**编排(Phase 5)**
- 新建 `hermes/SKILL.md` — 让 Hermes agent 用 terminal 调各脚本的步骤说明。
- 新建 `hermes/cron.md` — cron 时刻表 + 首次可用性验证说明。

**测试**
- 新建 `tests/__init__.py` 及各 `tests/test_*.py`(stdlib unittest,纯逻辑 + 注入假对象)。

依赖顺序:Phase 0 → (Phase 1、Phase 2 可并行)→ Phase 3(依赖 1、2 的库表有数据)→ Phase 4(依赖 obs + report)→ Phase 5(串起全部)。

---

## Phase 0 — 共享基础与前置验证

### Task 0.1: 验证 Hermes 可用 + 锁定密钥注入方式

**Files:**
- 无代码产出;记录到 `hermes/cron.md`(Phase 5 建),此处只做环境验证。

- [ ] **Step 1: 验证 Hermes 已装通**

在部署主机(国内 Windows)运行:
```powershell
hermes --version
hermes model
```
Expected: 打印 Hermes 版本号与当前已配置模型。若命令不存在 → 一期无法起来,先按记忆里的安装方式装:
```powershell
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```
装完重开终端再验一次。

- [x] **Step 2: 确认 LLM 与飞书的密钥注入约定**

约定(写进后续 config.example.json 注释,不写真值):
- LLM:`base_url` + `model` 放 config.json 的 `report.llm`;API Key 走环境变量 `LLM_API_KEY`(绝不入库)。
- 飞书:webhook URL 放 config.json 的 `notify.feishu_webhook`;若机器人开了签名校验,密钥走环境变量 `FEISHU_SIGN_SECRET`。

验证环境变量可读:
```powershell
$env:LLM_API_KEY; $env:FEISHU_SIGN_SECRET
```
Expected: 至少 `LLM_API_KEY` 有值(飞书签名可选)。无值先设置。这一步不提交任何东西。

### Task 0.2: 依赖、配置、gitignore 扩展

**Files:**
- Modify: `requirements.txt`
- Modify: `config.example.json`
- Modify: `.gitignore`

- [x] **Step 1: 追加行情库依赖**

编辑 [requirements.txt](requirements.txt),追加两行(adata/akshare 是成熟第三方库,直接装):
```
requests>=2.31,<3
playwright>=1.40
akshare>=1.12
adata>=2.0
```

- [x] **Step 2: 扩展 config.example.json**

把 [config.example.json](config.example.json) 覆盖为(在原微博配置基础上追加雪球/行情/简报/推送/熔断段):
```json
{
  "bloggers": [
    { "uid": "1234567890", "note": "微博:换成你要盯的博主 uid(weibo.com/u/<uid> 里的数字)" }
  ],
  "max_post_pages": 2000,
  "max_comment_pages": 5,
  "comment_refresh_days": 3,
  "enable_full_text": true,
  "comment_scope": "author_only",
  "min_delay": 1.5,
  "max_delay": 3.5,
  "db_path": "data/app.db",
  "profile_dir": ".wb_profile",

  "auth_fail_breaker": 3,

  "xueqiu_bloggers": [
    { "user_id": "1234567", "note": "雪球:主页 xueqiu.com/u/<user_id> 里的数字" }
  ],
  "xq_profile_dir": ".xq_profile",
  "xq_max_pages": 200,
  "xq_max_comment_pages": 5,
  "xq_comment_scope": "author_only",

  "market": {
    "sector_type": "行业资金流",
    "top_n": 40
  },

  "report": {
    "lookback_hours": 24,
    "llm": {
      "base_url": "https://openrouter.ai/api/v1",
      "model": "换成 Hermes 已配的模型名;API Key 走环境变量 LLM_API_KEY"
    }
  },

  "notify": {
    "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/换成你的机器人",
    "note": "若机器人开了签名校验,密钥走环境变量 FEISHU_SIGN_SECRET"
  },

  "log_dir": "logs"
}
```

- [x] **Step 3: 扩展 .gitignore**

编辑 [.gitignore](.gitignore),追加雪球登录态、日志、熔断 flag:
```
# 抓取产物 & 数据库(含他人评论/个人信息,勿提交)
data/
# 登录态(等于账号凭证,绝不能提交)
.wb_profile/
.wb_cookie*
.xq_profile/
# 本地配置(uid 等)
config.json
# 运行日志与熔断 flag(可能含敏感上下文)
logs/
# 环境
__pycache__/
*.pyc
.venv/
venv/
```

- [ ] **Step 4: 提交**

```powershell
git add requirements.txt config.example.json .gitignore
git commit -m "chore: add phase1 deps and config/gitignore scaffolding"
```

### Task 0.3: 分型异常 errors.py

**Files:**
- Create: `errors.py`
- Test: `tests/test_errors.py`

- [x] **Step 1: 写失败测试**

创建 `tests/__init__.py`(空文件)和 `tests/test_errors.py`:
```python
"""errors 分型异常单测:确保子类仍是 RuntimeError(不破坏现有 except)。"""
from __future__ import annotations

import unittest

from errors import AuthError, RateLimitError


class TestErrors(unittest.TestCase):
    def test_auth_is_runtime_error(self):
        self.assertTrue(issubclass(AuthError, RuntimeError))

    def test_ratelimit_is_runtime_error(self):
        self.assertTrue(issubclass(RateLimitError, RuntimeError))

    def test_distinct_types(self):
        self.assertNotEqual(AuthError, RateLimitError)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_errors -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'errors'`。

- [x] **Step 3: 实现 errors.py**

创建 `errors.py`:
```python
"""抓取层分型异常。

均继承 RuntimeError,保证现有 `except RuntimeError` 仍能兜住;
新增子类只为让 CLI/熔断层区分"登录失效"与"限流",从而实现连续 401/403 熔断。
"""

from __future__ import annotations


class AuthError(RuntimeError):
    """登录态失效(HTTP 401/403 或被反爬拦截)。连续出现触发熔断。"""


class RateLimitError(RuntimeError):
    """被限流/风控(HTTP 418/429)。退避重试处理,不触发熔断。"""
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_errors -v`
Expected: PASS(3 tests)。

- [ ] **Step 5: 提交**

```powershell
git add errors.py tests/__init__.py tests/test_errors.py
git commit -m "feat(errors): typed AuthError/RateLimitError for breaker"
```

### Task 0.4: 可观测性 obs.py(日志 + 熔断 flag + 扫描)

**Files:**
- Create: `obs.py`
- Test: `tests/test_obs.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_obs.py`:
```python
"""obs 单测:日志按级别落盘、flag 读写、扫描按级别字段判定(不靠中文正则)、偏移量防重复。"""
from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

import obs


class TestObs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        logging.getLogger("mod").handlers.clear()
        self.tmp.cleanup()

    def test_log_writes_level_and_step(self):
        logger = obs.get_logger("mod", log_dir=self.dir)
        obs.log(logger, logging.ERROR, "抓取", "出错了")
        files = list(self.dir.glob("mod-*.log"))
        self.assertEqual(len(files), 1)
        line = files[0].read_text(encoding="utf-8").strip()
        self.assertIn("ERROR", line)
        self.assertIn("抓取", line)
        self.assertIn("出错了", line)

    def test_flag_roundtrip(self):
        self.assertFalse(obs.is_paused("mod", flag_dir=self.dir))
        obs.set_paused("mod", "连续登录失效", flag_dir=self.dir)
        self.assertTrue(obs.is_paused("mod", flag_dir=self.dir))
        obs.clear_paused("mod", flag_dir=self.dir)
        self.assertFalse(obs.is_paused("mod", flag_dir=self.dir))

    def test_scan_picks_error_by_level_field(self):
        logger = obs.get_logger("mod", log_dir=self.dir)
        obs.log(logger, logging.INFO, "抓取", "正常")     # 含中文"错"字样也不该误报
        obs.log(logger, logging.WARNING, "抓取", "有点慢")
        obs.log(logger, logging.ERROR, "LLM", "调用失败")
        issues = obs.scan_issues(["mod"], log_dir=self.dir, state_dir=self.dir)
        kinds = [i["kind"] for i in issues]
        self.assertIn("error", kinds)
        errs = [i for i in issues if i["kind"] == "error"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["step"], "LLM")

    def test_scan_offset_no_duplicate(self):
        logger = obs.get_logger("mod", log_dir=self.dir)
        obs.log(logger, logging.ERROR, "DB", "写失败")
        first = obs.scan_issues(["mod"], log_dir=self.dir, state_dir=self.dir)
        second = obs.scan_issues(["mod"], log_dir=self.dir, state_dir=self.dir)
        self.assertEqual(len([i for i in first if i["kind"] == "error"]), 1)
        self.assertEqual(len([i for i in second if i["kind"] == "error"]), 0)

    def test_scan_reports_paused(self):
        obs.set_paused("mod", "熔断了", flag_dir=self.dir)
        issues = obs.scan_issues(["mod"], log_dir=self.dir, state_dir=self.dir, flag_dir=self.dir)
        self.assertTrue(any(i["kind"] == "paused" for i in issues))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_obs -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'obs'`。

- [x] **Step 3: 实现 obs.py**

创建 `obs.py`:
```python
"""可观测性:统一结构化日志 + 熔断 flag + 供 Hermes 用的 ERROR/flag 扫描。

设计取舍(见设计文档 6.3):监控靠"脚本自报(写日志/写 flag)+ Hermes 编排时读取推送",
不做独立 tail-日志守护进程。日志每行格式固定:
    时间 | 级别 | 步骤标签 | 消息
扫描按"级别字段==ERROR"判定告警,不靠正则抠中文文案 → 改文案不失效。
扫描带按文件字节偏移量,已读过的 ERROR 不重复告警。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

LOG_DIR = Path("logs")
_FIELD_SEP = " | "


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _StepFormatter(logging.Formatter):
    """确保每条记录都有 step 字段(缺省填 '-'),避免格式化报错。"""

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "step"):
            record.step = "-"
        return super().format(record)


def get_logger(module: str, log_dir: str | Path = LOG_DIR) -> logging.Logger:
    """返回写 logs/<module>-YYYY-MM-DD.log 的 logger(同一 logger 不重复挂 handler)。"""
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{module}-{date.today().isoformat()}.log"
    logger = logging.getLogger(module)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(h, "_wb_path", None) == str(path) for h in logger.handlers):
        h = logging.FileHandler(path, encoding="utf-8")
        h._wb_path = str(path)  # type: ignore[attr-defined]
        h.setFormatter(_StepFormatter("%(asctime)s" + _FIELD_SEP + "%(levelname)s" + _FIELD_SEP + "%(step)s" + _FIELD_SEP + "%(message)s"))
        logger.addHandler(h)
    return logger


def log(logger: logging.Logger, level: int, step: str, msg: str) -> None:
    """带步骤标签写一条日志。level 用 logging.INFO/WARNING/ERROR。"""
    logger.log(level, msg, extra={"step": step})


# ---- 熔断 flag ----
def _flag_path(module: str, flag_dir: str | Path) -> Path:
    return Path(flag_dir) / f"{module}.paused"


def is_paused(module: str, flag_dir: str | Path = LOG_DIR) -> bool:
    return _flag_path(module, flag_dir).exists()


def set_paused(module: str, reason: str, flag_dir: str | Path = LOG_DIR) -> None:
    p = _flag_path(module, flag_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"module": module, "reason": reason, "ts": _now_iso()}, ensure_ascii=False),
        encoding="utf-8",
    )


def clear_paused(module: str, flag_dir: str | Path = LOG_DIR) -> None:
    p = _flag_path(module, flag_dir)
    if p.exists():
        p.unlink()


def paused_reason(module: str, flag_dir: str | Path = LOG_DIR) -> str | None:
    p = _flag_path(module, flag_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("reason")
    except (ValueError, OSError):
        return "(无法读取熔断原因)"


# ---- 扫描(给 Hermes/notify 用)----
def _load_offsets(state_dir: str | Path) -> dict[str, int]:
    p = Path(state_dir) / ".alert_offset.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_offsets(state_dir: str | Path, offsets: dict[str, int]) -> None:
    p = Path(state_dir) / ".alert_offset.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(offsets, ensure_ascii=False), encoding="utf-8")


def scan_issues(
    modules: list[str],
    log_dir: str | Path = LOG_DIR,
    state_dir: str | Path = LOG_DIR,
    flag_dir: str | Path = LOG_DIR,
    day: str | None = None,
) -> list[dict]:
    """扫描当天日志里未读过的 ERROR 行 + 熔断 flag,返回 issue 列表。

    每个 issue:{module, kind: 'error'|'paused', level, step, message, ts}。
    ERROR 判定只看 '级别' 字段(第 2 段),不做中文文本匹配。
    用 .alert_offset.json 记录每个日志文件已读到的字节位置,避免重复告警。
    """
    day = day or date.today().isoformat()
    offsets = _load_offsets(state_dir)
    issues: list[dict] = []

    for module in modules:
        # flag(熔断)优先
        reason = paused_reason(module, flag_dir)
        if reason is not None:
            issues.append(
                {"module": module, "kind": "paused", "level": "ERROR", "step": "熔断", "message": reason, "ts": _now_iso()}
            )
        # 日志 ERROR 行
        path = Path(log_dir) / f"{module}-{day}.log"
        if not path.exists():
            continue
        key = str(path)
        start = int(offsets.get(key, 0))
        size = path.stat().st_size
        if size < start:  # 日志被轮转/清空,从头读
            start = 0
        with path.open("r", encoding="utf-8") as f:
            f.seek(start)
            for raw in f:
                line = raw.rstrip("\n")
                parts = line.split(_FIELD_SEP, 3)
                if len(parts) < 4:
                    continue
                ts, level, step, message = parts
                if level.strip() == "ERROR":
                    issues.append(
                        {"module": module, "kind": "error", "level": "ERROR", "step": step, "message": message, "ts": ts}
                    )
            offsets[key] = f.tell()

    _save_offsets(state_dir, offsets)
    return issues
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_obs -v`
Expected: PASS(5 tests)。

- [ ] **Step 5: 提交**

```powershell
git add obs.py tests/test_obs.py
git commit -m "feat(obs): structured logging, breaker flag, level-based alert scan"
```

### Task 0.5: crawler.py 抛分型异常

**Files:**
- Modify: `crawler.py`(仅 `_get_json` 里构造异常处 + 顶部 import)

- [x] **Step 1: 顶部引入 errors**

在 [crawler.py](crawler.py) 的 `import requests` 之后加一行:
```python
import requests

from errors import AuthError, RateLimitError
```

- [x] **Step 2: 替换 _get_json 里的异常构造**

在 [crawler.py](crawler.py) 的 `_get_json` 内,把 401/403 与 418/429 两处的 `RuntimeError(...)` 改成分型异常(其余分支不动):
```python
            if resp.status_code in (401, 403):
                last_err = AuthError("HTTP %d:登录态失效" % resp.status_code)
                refresh_eligible = True
            elif resp.status_code in (418, 429):
                last_err = RateLimitError("HTTP %d:被限流/风控" % resp.status_code)
```
同一函数里"返回非 JSON(疑似反爬拦截)"那处也升级为 AuthError(反爬拦截等价于会话被盯,值得刷 cookie/熔断):
```python
                except ValueError:
                    # 返回非 JSON 通常是被反爬拦截
                    last_err = AuthError(f"返回非 JSON(疑似反爬拦截):{resp.text[:120]}")
                    refresh_eligible = True
```

- [x] **Step 3: 冒烟验证 import 不破坏**

Run: `python -c "import crawler; print('ok')"`
Expected: 打印 `ok`,无 ImportError。

- [ ] **Step 4: 提交**

```powershell
git add crawler.py
git commit -m "refactor(crawler): raise typed AuthError/RateLimitError from _get_json"
```

### Task 0.6: main.py 接入日志 + 连续 401/403 熔断

**Files:**
- Modify: `main.py`

- [x] **Step 1: 顶部引入 obs 与 errors**

在 [main.py](main.py) 的 `from storage import Storage` 之后加:
```python
from storage import Storage

import obs
from errors import AuthError
```
并把 `import logging` 加到标准库 import 区(与 `import sys` 同区):
```python
import argparse
import json
import logging
import sys
import time
from pathlib import Path
```

- [x] **Step 2: 重写 cmd_crawl,加 flag 检查 + 熔断计数 + ERROR 日志**

把 [main.py](main.py) 现有 `cmd_crawl` 整个替换为:
```python
def cmd_crawl(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    logger = obs.get_logger("weibo", cfg["log_dir"])

    # 启动先看熔断 flag:已熔断则本轮跳过,等用户处理后手动清除
    if obs.is_paused("weibo", cfg["log_dir"]):
        reason = obs.paused_reason("weibo", cfg["log_dir"])
        obs.log(logger, logging.WARNING, "熔断", f"检测到熔断 flag({reason}),本轮跳过抓取")
        raise SystemExit(f"微博抓取已熔断:{reason}。处理后删除 logs/weibo.paused 再跑")

    cookie = login_mod.get_cookie_header(cfg["profile_dir"])
    store = Storage(cfg["db_path"])
    client = WeiboClient(
        cookie,
        cfg["min_delay"],
        cfg["max_delay"],
        refresh_cookie=lambda: login_mod.get_cookie_header(cfg["profile_dir"]),
    )

    if not client.check_login():
        store.close()
        obs.log(logger, logging.ERROR, "抓取", "m 站登录预检未通过")
        raise SystemExit("登录态失效(m 站预检未通过):请重新 `python main.py login`")

    threshold = int(cfg["auth_fail_breaker"])
    consecutive_auth_fail = 0
    total_posts = total_comments = 0
    for uid in cfg["_uids"]:
        try:
            np, nc = crawl_one(client, store, uid, args.mode, cfg)
            consecutive_auth_fail = 0
            total_posts += np
            total_comments += nc
            obs.log(logger, logging.INFO, "抓取", f"uid={uid} 新增博文 {np}、评论 {nc}")
            print(f"uid={uid}:新增博文 {np},评论 {nc} 条")
        except AuthError as e:
            consecutive_auth_fail += 1
            obs.log(logger, logging.ERROR, "抓取", f"uid={uid} 登录失效({consecutive_auth_fail}/{threshold}):{e}")
            print(f"[error] uid={uid} 登录失效:{e}", file=sys.stderr)
            if consecutive_auth_fail >= threshold:
                obs.set_paused("weibo", f"连续 {consecutive_auth_fail} 次 401/403", cfg["log_dir"])
                obs.log(logger, logging.ERROR, "熔断", f"连续 {consecutive_auth_fail} 次登录失效,已熔断停止本轮")
                print("[error] 连续登录失效超阈值,已熔断,停止本轮抓取", file=sys.stderr)
                break
        except RuntimeError as e:
            obs.log(logger, logging.ERROR, "抓取", f"uid={uid} 抓取中断:{e}")
            print(f"[error] uid={uid} 抓取中断:{e}", file=sys.stderr)

    p, c = store.counts()
    store.close()
    print(
        f"\n完成(mode={args.mode}):本次新增博文 {total_posts}、评论 {total_comments} 条;"
        f"库内累计博文 {p}、评论 {c} → {cfg['db_path']}"
    )
```
> 注意:`AuthError` 继承 `RuntimeError`,`except AuthError` 必须在 `except RuntimeError` 之前。

- [x] **Step 3: DEFAULTS 补 log_dir(供不带该键的旧 config 兜底)**

在 [main.py](main.py) 的 `DEFAULTS` 字典追加两键:
```python
    "db_path": "data/app.db",
    "profile_dir": ".wb_profile",
    "auth_fail_breaker": 3,
    "log_dir": "logs",
}
```

- [x] **Step 4: 冒烟验证(无网络也能验 flag 分支)**

先制造 flag 再跑,确认熔断分支生效:
```powershell
python -c "import obs; obs.set_paused('weibo','手动测试','logs')"
python main.py crawl --mode incremental
```
Expected: 打印 `微博抓取已熔断:手动测试...` 并退出(SystemExit)。随后清除:
```powershell
python -c "import obs; obs.clear_paused('weibo','logs')"
```
Expected: `logs/weibo.paused` 消失。

- [ ] **Step 5: 提交**

```powershell
git add main.py
git commit -m "feat(main): weibo crawl breaker on consecutive auth failures + logging"
```

---

## Phase 1 — 雪球抓取(镜像微博四层)

> 参考(记忆已核实):雪球纯 cookie 无签名;时间线 `xueqiu.com/statuses/user_timeline.json?user_id=&page=&count=`(用返回 `maxPage` 翻页);长文补全**必须**用 `api.xueqiu.com/statuses/show.json?id=`(xueqiu.com 同名返回 HTML);评论 `xueqiu.com/statuses/v3/comments.json?id=`;cookie 域 `.xueqiu.com`,登录关键 cookie `xq_is_login`;`created_at` 是毫秒时间戳。字段易变,报错优先对字段。

### Task 1.1: xueqiu_storage.py

**Files:**
- Create: `xueqiu_storage.py`
- Test: `tests/test_xueqiu_storage.py`

- [x] **Step 1: 写失败测试(去重 + 断点)**

创建 `tests/test_xueqiu_storage.py`:
```python
"""雪球存储层单测:临时 SQLite 验证 id 去重、增量水位、断点续抓。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from xueqiu_storage import XueqiuStorage


def _post(pid: str, ts: int) -> dict:
    return {
        "id": pid, "user_id": "u1", "user_name": "张三", "created_at": "",
        "created_ts": ts, "text": "看好新能源", "source": "雪球",
        "retweet_count": 0, "reply_count": 1, "like_count": 2,
        "is_long_text": 0, "pic_urls": "[]", "retweeted_id": "",
        "retweeted_text": "", "raw_json": "{}", "fetched_at": "now",
    }


class TestXueqiuStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "app.db")
        self.store = XueqiuStorage(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_upsert_dedup(self):
        self.store.upsert_posts([_post("1", 100), _post("1", 100)])
        self.assertEqual(self.store.count_posts(), 1)

    def test_last_ts_roundtrip(self):
        self.assertEqual(self.store.get_last_ts("u1"), 0)
        self.store.set_last_ts("u1", 12345, "now")
        self.assertEqual(self.store.get_last_ts("u1"), 12345)

    def test_full_progress_roundtrip(self):
        self.assertEqual(self.store.get_full_progress("u1"), (0, 0))
        self.store.set_full_progress("u1", 3, 0, "now")
        self.assertEqual(self.store.get_full_progress("u1"), (3, 0))

    def test_comment_dedup(self):
        row = {"id": "c1", "post_id": "1", "parent_id": None, "created_at": "",
               "text": "同意", "like_counts": 0, "user_name": "李四",
               "user_id": "u2", "fetched_at": "now"}
        self.store.upsert_comments([row, row])
        self.assertEqual(self.store.count_comments(), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_xueqiu_storage -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'xueqiu_storage'`。

- [x] **Step 3: 实现 xueqiu_storage.py**

创建 `xueqiu_storage.py`:
```python
"""雪球 SQLite 存储层(与微博共用 data/app.db,表名加 xq_ 前缀)。

三张表,结构对齐微博:
  xq_posts       博文(id 主键 → 去重;comments_done 标记评论是否抓过)
  xq_comments    评论(id 主键;parent_id 区分一级/二级)
  xq_crawl_state 每个博主状态(增量水位 last_ts + 全量断点 full_page/full_done)

雪球 created_at 是毫秒时间戳,增量水位用 last_ts(已见过的最大 created_ts,epoch 秒)。
CREATE TABLE IF NOT EXISTS 幂等:与微博 Storage 各自开连接同库互不干扰。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS xq_posts (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    user_name     TEXT,
    created_at    TEXT,
    created_ts    INTEGER,
    text          TEXT,
    source        TEXT,
    retweet_count INTEGER,
    reply_count   INTEGER,
    like_count    INTEGER,
    is_long_text  INTEGER DEFAULT 0,
    pic_urls      TEXT,
    retweeted_id  TEXT,
    retweeted_text TEXT,
    comments_done INTEGER DEFAULT 0,
    raw_json      TEXT,
    fetched_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_xq_posts_user_ts ON xq_posts(user_id, created_ts DESC);

CREATE TABLE IF NOT EXISTS xq_comments (
    id          TEXT PRIMARY KEY,
    post_id     TEXT NOT NULL,
    parent_id   TEXT,
    created_at  TEXT,
    text        TEXT,
    like_counts INTEGER,
    user_name   TEXT,
    user_id     TEXT,
    fetched_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_xq_comments_post ON xq_comments(post_id);

CREATE TABLE IF NOT EXISTS xq_crawl_state (
    user_id    TEXT PRIMARY KEY,
    last_ts    INTEGER DEFAULT 0,
    full_page  INTEGER DEFAULT 0,
    full_done  INTEGER DEFAULT 0,
    updated_at TEXT
);
"""


class XueqiuStorage:
    def __init__(self, db_path: str):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ---- 状态 ----
    def get_last_ts(self, user_id: str) -> int:
        row = self.conn.execute(
            "SELECT last_ts FROM xq_crawl_state WHERE user_id = ?", (user_id,)
        ).fetchone()
        return int(row["last_ts"]) if row else 0

    def set_last_ts(self, user_id: str, value: int, now_iso: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO xq_crawl_state(user_id, last_ts, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET last_ts=excluded.last_ts, "
                "updated_at=excluded.updated_at",
                (user_id, value, now_iso),
            )

    def get_full_progress(self, user_id: str) -> tuple[int, int]:
        row = self.conn.execute(
            "SELECT full_page, full_done FROM xq_crawl_state WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return 0, 0
        return int(row["full_page"] or 0), int(row["full_done"] or 0)

    def set_full_progress(self, user_id: str, page: int, done: int, now_iso: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO xq_crawl_state(user_id, full_page, full_done, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET full_page=excluded.full_page, "
                "full_done=excluded.full_done, updated_at=excluded.updated_at",
                (user_id, page, done, now_iso),
            )

    # ---- 博文 ----
    def upsert_posts(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO xq_posts (id, user_id, user_name, created_at, created_ts, text, "
                "source, retweet_count, reply_count, like_count, is_long_text, pic_urls, "
                "retweeted_id, retweeted_text, raw_json, fetched_at) VALUES "
                "(:id,:user_id,:user_name,:created_at,:created_ts,:text,:source,:retweet_count,"
                ":reply_count,:like_count,:is_long_text,:pic_urls,:retweeted_id,:retweeted_text,"
                ":raw_json,:fetched_at) "
                "ON CONFLICT(id) DO UPDATE SET user_name=excluded.user_name, "
                "created_at=excluded.created_at, created_ts=excluded.created_ts, text=excluded.text, "
                "source=excluded.source, retweet_count=excluded.retweet_count, "
                "reply_count=excluded.reply_count, like_count=excluded.like_count, "
                "is_long_text=excluded.is_long_text, pic_urls=excluded.pic_urls, "
                "retweeted_id=excluded.retweeted_id, retweeted_text=excluded.retweeted_text, "
                "raw_json=excluded.raw_json, fetched_at=excluded.fetched_at",
                rows,
            )

    def posts_pending_comments(self, user_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM xq_posts WHERE user_id = ? AND comments_done = 0 AND reply_count > 0 "
            "ORDER BY created_ts DESC",
            (user_id,),
        ).fetchall()
        return [r["id"] for r in rows]

    def recent_post_ids(self, user_id: str, cutoff_ts: float) -> list[str]:
        rows = self.conn.execute(
            "SELECT id FROM xq_posts WHERE user_id = ? AND created_ts >= ? AND reply_count > 0",
            (user_id, cutoff_ts),
        ).fetchall()
        return [r["id"] for r in rows]

    def mark_comments_done(self, post_id: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE xq_posts SET comments_done = 1 WHERE id = ?", (post_id,))

    # ---- 评论 ----
    def upsert_comments(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT OR REPLACE INTO xq_comments (id, post_id, parent_id, created_at, text, "
                "like_counts, user_name, user_id, fetched_at) VALUES "
                "(:id,:post_id,:parent_id,:created_at,:text,:like_counts,:user_name,:user_id,:fetched_at)",
                rows,
            )

    # ---- 统计 ----
    def count_posts(self) -> int:
        return self.conn.execute("SELECT COUNT(*) n FROM xq_posts").fetchone()["n"]

    def count_comments(self) -> int:
        return self.conn.execute("SELECT COUNT(*) n FROM xq_comments").fetchone()["n"]
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_xueqiu_storage -v`
Expected: PASS(4 tests)。

- [ ] **Step 5: 提交**

```powershell
git add xueqiu_storage.py tests/test_xueqiu_storage.py
git commit -m "feat(xueqiu): storage layer with xq_ tables and dedup"
```

### Task 1.2: xueqiu_login.py

**Files:**
- Create: `xueqiu_login.py`

- [x] **Step 1: 实现 xueqiu_login.py**

创建 `xueqiu_login.py`(镜像 [login.py](login.py),关键 cookie 换成 `xq_is_login`,域 `.xueqiu.com`):
```python
"""雪球登录 & 会话:与微博 login.py 同套路(浏览器持久化 profile)。

- login():打开可见浏览器(持久化 profile),用户扫码/账号登录,登录态存磁盘。
- get_cookie_header():headless 复用同一 profile,先访问雪球主页保鲜 cookie,
  再拼出 requests 用的 Cookie 头。失效时抓取层收到 401/403 → 提示重登。

雪球登录关键 cookie 是 xq_is_login(还有 xq_a_token/xq_id_token/xq_r_token/xqat/u)。
只依赖 playwright。首次:pip install playwright && playwright install chromium
"""

from __future__ import annotations

from pathlib import Path

HOME_URL = "https://xueqiu.com/"
LOGIN_URL = "https://xueqiu.com/#/login"


def _is_logged_in(cookies: list[dict]) -> bool:
    """雪球登录态关键 cookie:xq_is_login=1(或存在 xq_a_token)。"""
    got = {c.get("name"): c.get("value") for c in cookies}
    return got.get("xq_is_login") in ("1", 1) or bool(got.get("xq_a_token"))


def login(profile_dir: str, timeout_sec: int = 180) -> None:
    """打开浏览器让用户登录;检测到 xq_is_login/xq_a_token 即视为成功。"""
    from playwright.sync_api import sync_playwright

    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile_dir, headless=False, args=["--disable-blink-features=AutomationControlled"]
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(HOME_URL)

        if _is_logged_in(ctx.cookies()):
            print("检测到雪球已登录状态,无需重复登录。")
            ctx.close()
            return

        page.goto(LOGIN_URL)
        print("请在弹出的浏览器里登录雪球(扫码或账号密码,最多等待 %d 秒)……" % timeout_sec)

        waited = 0
        step = 2
        while waited < timeout_sec:
            page.wait_for_timeout(step * 1000)
            waited += step
            if _is_logged_in(ctx.cookies()):
                print("雪球登录成功,登录态已保存到 %s" % profile_dir)
                ctx.close()
                return
        ctx.close()
        raise SystemExit("雪球登录超时:未检测到登录态,请重试 `python xueqiu_crawl.py login`")


def get_cookie_header(profile_dir: str) -> str:
    """headless 复用 profile,访问雪球主页保鲜后拼出 Cookie 头。"""
    from playwright.sync_api import sync_playwright

    if not Path(profile_dir).exists():
        raise SystemExit("雪球还没登录:先运行 `python xueqiu_crawl.py login`")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(profile_dir, headless=True)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(HOME_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(800)
            cookies = ctx.cookies()
        finally:
            ctx.close()

    if not _is_logged_in(cookies):
        raise SystemExit("雪球登录态失效:请重新运行 `python xueqiu_crawl.py login`")

    return "; ".join(f"{c['name']}={c['value']}" for c in cookies)
```

- [x] **Step 2: 冒烟验证 import**

Run: `python -c "import xueqiu_login; print('ok')"`
Expected: 打印 `ok`。

- [ ] **Step 3: 提交**

```powershell
git add xueqiu_login.py
git commit -m "feat(xueqiu): playwright login/session persistence"
```

### Task 1.3: xueqiu_crawler.py(客户端 + 字段裁剪)

**Files:**
- Create: `xueqiu_crawler.py`
- Test: `tests/test_xueqiu_crawler.py`(纯函数:slim / select / 时间戳)

- [x] **Step 1: 写失败测试(纯函数,不碰网络)**

创建 `tests/test_xueqiu_crawler.py`:
```python
"""雪球抓取层纯函数单测:时间戳换算、slim_xq_post 裁剪、select_xq_comments 过滤。"""
from __future__ import annotations

import unittest

from xueqiu_crawler import slim_xq_post, select_xq_comments, xq_ms_to_epoch


class TestXueqiuPure(unittest.TestCase):
    def test_ms_to_epoch(self):
        self.assertEqual(xq_ms_to_epoch(1700000000000), 1700000000)
        self.assertIsNone(xq_ms_to_epoch(None))
        self.assertIsNone(xq_ms_to_epoch("bad"))

    def test_slim_post_basic(self):
        s = {
            "id": 42, "user": {"screen_name": "王五", "id": 9},
            "created_at": 1700000000000, "description": "<p>看多 <a>网页链接</a></p>",
            "source": "雪球", "retweet_count": 1, "reply_count": 2, "fav_count": 3,
        }
        row = slim_xq_post("9", s)
        self.assertEqual(row["id"], "42")
        self.assertEqual(row["user_name"], "王五")
        self.assertEqual(row["created_ts"], 1700000000)
        self.assertIn("看多", row["text"])
        self.assertNotIn("<p>", row["text"])
        self.assertEqual(row["like_count"], 3)

    def test_select_comments_author_only(self):
        author = "9"
        comments = [
            {"id": 1, "user": {"id": 9}, "text": "作者一级评论"},
            {"id": 2, "user": {"id": 7}, "text": "路人评论", "comments": [
                {"id": 21, "user": {"id": 9}, "text": "作者的回复"},
            ]},
            {"id": 3, "user": {"id": 5}, "text": "无关路人"},
        ]
        rows = select_xq_comments(author, "42", comments, "author_only")
        ids = {r["id"] for r in rows}
        self.assertIn("1", ids)   # 作者一级评论
        self.assertIn("2", ids)   # 路人父评论(作为作者回复的上下文)
        self.assertIn("21", ids)  # 作者的回复
        self.assertNotIn("3", ids)  # 无关路人应被过滤


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_xueqiu_crawler -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'xueqiu_crawler'`。

- [x] **Step 3: 实现 xueqiu_crawler.py**

创建 `xueqiu_crawler.py`(复用 [crawler.py](crawler.py) 的 `html_to_text`/`now_iso`,DRY):
```python
"""抓取层:requests 直连雪球 H5/API 接口。

雪球纯 cookie、无签名/加密,比微博简单:
- 博主时间线:GET xueqiu.com/statuses/user_timeline.json?user_id=&page=&count=20
    返回 {statuses:[...], maxPage:N};用 maxPage 翻页(不要用 len<count 判停)。
- 长文补全:GET api.xueqiu.com/statuses/show.json?id=<id>(xueqiu.com 同名返回 HTML 不可用)。
- 评论:GET xueqiu.com/statuses/v3/comments.json?id=<id>&count=20&page=&reply=true&asc=false
    二级评论内嵌在每条评论的 comments 字段。

cookie 由 xueqiu_login.get_cookie_header() 提供(域 .xueqiu.com)。带重试/退避;
区分登录失效(401/403)、限流(429)、反爬(非 JSON)。created_at 是毫秒时间戳。
字段易变,报错优先对字段。
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone

import requests

from crawler import html_to_text, now_iso
from errors import AuthError, RateLimitError

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1"
)
HOST = "https://xueqiu.com"
API = "https://api.xueqiu.com"
TIMELINE_URL = HOST + "/statuses/user_timeline.json"
SHOW_URL = API + "/statuses/show.json"
COMMENTS_URL = HOST + "/statuses/v3/comments.json"


def xq_ms_to_epoch(v) -> int | None:
    """雪球毫秒时间戳 → epoch 秒;解析不了返回 None。"""
    try:
        return int(int(v) / 1000)
    except (TypeError, ValueError):
        return None


def xq_ms_to_iso(v) -> str:
    ts = xq_ms_to_epoch(v)
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else ""


def _needs_full(s: dict) -> bool:
    """雪球长文在时间线里被截断,description 常含"查看全文"/"网页链接"锚点。"""
    desc = s.get("description") or ""
    return "查看全文" in desc or "网页链接" in desc


def _xq_pics(s: dict) -> list[str]:
    urls: list[str] = []
    for pic in s.get("pic_urls") or []:
        if isinstance(pic, dict) and pic.get("url"):
            urls.append(pic["url"])
        elif isinstance(pic, str):
            urls.append(pic)
    return urls


class XueqiuClient:
    def __init__(
        self,
        cookie: str,
        min_delay: float,
        max_delay: float,
        refresh_cookie=None,
        max_retries: int = 5,
        retry_wait: float = 3.0,
    ):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.max_retries = max_retries
        self.retry_wait = retry_wait
        self.refresh_cookie = refresh_cookie
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "application/json, text/plain, */*",
                "Origin": HOST,
                "Referer": HOST + "/",
            }
        )
        self._load_cookies(cookie)

    def _load_cookies(self, cookie_str: str) -> None:
        self.session.cookies.clear()
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            self.session.cookies.set(k.strip(), v.strip(), domain=".xueqiu.com")

    def _refresh(self) -> bool:
        if not self.refresh_cookie:
            return False
        try:
            self._load_cookies(self.refresh_cookie())
        except (Exception, SystemExit):
            return False
        return True

    def _sleep(self) -> None:
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    def _get_json(self, url: str, params: dict, referer: str | None = None) -> dict:
        """带重试/退避的 GET;雪球直接返回 JSON(无 ok 包裹)。
        返回体含 error_code 视为异常;401/403 抛 AuthError,429 抛 RateLimitError。"""
        headers = {"Referer": referer} if referer else {}
        last_err: RuntimeError | None = None
        refreshed = False
        for attempt in range(1, self.max_retries + 1):
            self._sleep()
            refresh_eligible = False
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=20)
            except requests.RequestException as e:
                last_err = RuntimeError(f"网络异常:{e}")
                if attempt < self.max_retries:
                    time.sleep(self.retry_wait * attempt)
                continue

            if resp.status_code in (401, 403):
                last_err = AuthError("HTTP %d:雪球登录态失效" % resp.status_code)
                refresh_eligible = True
            elif resp.status_code == 429:
                last_err = RateLimitError("HTTP 429:雪球限流")
            elif resp.status_code != 200:
                last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            else:
                try:
                    body = resp.json()
                except ValueError:
                    last_err = AuthError(f"返回非 JSON(疑似反爬拦截):{resp.text[:120]}")
                    refresh_eligible = True
                else:
                    if isinstance(body, dict) and body.get("error_code"):
                        # 常见:400016 cookie 失效 → 当登录失效处理
                        code = str(body.get("error_code"))
                        msg = body.get("error_description") or "接口报错"
                        if code in ("400016", "1000001"):
                            last_err = AuthError(f"error_code={code}:{msg}")
                            refresh_eligible = True
                        else:
                            last_err = RuntimeError(f"error_code={code}:{msg}")
                    else:
                        return body if isinstance(body, dict) else {"_list": body}

            if refresh_eligible and not refreshed and self._refresh():
                refreshed = True
            if attempt < self.max_retries:
                time.sleep(self.retry_wait * attempt)
        raise last_err or RuntimeError("请求多次失败")

    # ---- 时间线(page/maxPage 翻页)----
    def fetch_timeline(self, user_id: str, page: int) -> tuple[list[dict], int]:
        """返回 (statuses 列表, maxPage)。"""
        params = {"user_id": user_id, "page": page, "count": 20}
        data = self._get_json(TIMELINE_URL, params, referer=f"{HOST}/u/{user_id}")
        statuses = data.get("statuses") or []
        max_page = int(data.get("maxPage") or 0)
        return statuses, max_page

    # ---- 长文补全 ----
    def fetch_full_text(self, status_id: str) -> str | None:
        try:
            data = self._get_json(SHOW_URL, {"id": status_id}, referer=f"{HOST}/")
        except RuntimeError:
            return None
        return data.get("text") or data.get("description")

    # ---- 评论(page 翻页)----
    def fetch_comments(self, status_id: str, max_pages: int) -> list[dict]:
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            params = {"id": status_id, "count": 20, "page": page, "reply": "true", "asc": "false"}
            data = self._get_json(COMMENTS_URL, params, referer=f"{HOST}/")
            batch = data.get("comments") or []
            out.extend(batch)
            if not batch or page >= int(data.get("maxPage") or page):
                break
        return out


# ---- 字段裁剪 ----
def slim_xq_post(user_id: str, s: dict, full_text: str | None = None) -> dict:
    """雪球 status → 入库结构。full_text 为长文补全后的完整正文(优先)。"""
    user = s.get("user") or {}
    rt = s.get("retweeted_status") or {}
    return {
        "id": str(s.get("id", "")),
        "user_id": str(user_id),
        "user_name": user.get("screen_name", ""),
        "created_at": xq_ms_to_iso(s.get("created_at")),
        "created_ts": xq_ms_to_epoch(s.get("created_at")),
        "text": html_to_text(full_text or s.get("description") or s.get("text") or ""),
        "source": s.get("source", ""),
        "retweet_count": s.get("retweet_count", 0),
        "reply_count": s.get("reply_count", 0),
        "like_count": s.get("fav_count", s.get("like_count", 0)),
        "is_long_text": 1 if _needs_full(s) else 0,
        "pic_urls": json.dumps(_xq_pics(s), ensure_ascii=False),
        "retweeted_id": str(rt.get("id", "")) if rt else "",
        "retweeted_text": html_to_text(rt.get("description") or rt.get("text") or "") if rt else "",
        "raw_json": json.dumps(s, ensure_ascii=False),
        "fetched_at": now_iso(),
    }


def slim_xq_comment(post_id: str, parent_id: str | None, c: dict) -> dict:
    user = c.get("user") or {}
    return {
        "id": str(c.get("id", "")),
        "post_id": post_id,
        "parent_id": parent_id,
        "created_at": xq_ms_to_iso(c.get("created_at")),
        "text": html_to_text(c.get("text", "")),
        "like_counts": c.get("like_count", 0),
        "user_name": user.get("screen_name", ""),
        "user_id": str(user.get("id", "")),
        "fetched_at": now_iso(),
    }


def select_xq_comments(user_id: str, post_id: str, comments: list[dict], scope: str) -> list[dict]:
    """按 scope 从评论流挑评论(按 id 去重)。

    - all:所有一级评论 + 内嵌二级评论。
    - author_only(默认):只留博主自己的评论;博主对别人的回复,连父评论一起存作上下文。
    """
    rows: dict[str, dict] = {}

    def add(parent_id: str | None, c: dict) -> None:
        r = slim_xq_comment(post_id, parent_id, c)
        if r["id"]:
            rows[r["id"]] = r

    for c in comments:
        cid = str(c.get("id", ""))
        c_uid = str((c.get("user") or {}).get("id", ""))
        subs = c.get("comments") if isinstance(c.get("comments"), list) else []
        if scope == "all":
            add(None, c)
            for s in subs:
                add(cid, s)
        else:  # author_only
            author_subs = [s for s in subs if str((s.get("user") or {}).get("id", "")) == user_id]
            if c_uid == user_id:
                add(None, c)
            if author_subs:
                add(None, c)
                for s in author_subs:
                    add(cid, s)
    return list(rows.values())
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_xueqiu_crawler -v`
Expected: PASS(3 tests)。

- [ ] **Step 5: 提交**

```powershell
git add xueqiu_crawler.py tests/test_xueqiu_crawler.py
git commit -m "feat(xueqiu): H5/API client + field slimming + comment selection"
```

### Task 1.4: xueqiu_crawl.py(CLI + 熔断接入)

**Files:**
- Create: `xueqiu_crawl.py`

- [x] **Step 1: 实现 xueqiu_crawl.py**

创建 `xueqiu_crawl.py`(镜像 [main.py](main.py) 的编排 + 熔断,模块名 `xueqiu`):
```python
"""雪球 CLI 编排(镜像 main.py)。

命令:
  python xueqiu_crawl.py login                    登录(首次 / 失效时)
  python xueqiu_crawl.py crawl --mode full        全量:翻遍每个博主时间线 + 评论
  python xueqiu_crawl.py crawl --mode incremental 增量:只取比 last_ts 新的博文 + 刷新近 N 天评论

复用 main.py 的 config(同 config.json),读 xueqiu_bloggers / xq_* 配置项。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import obs
import xueqiu_login as login_mod
from errors import AuthError
from main import load_config
from xueqiu_crawler import (
    XueqiuClient,
    now_iso,
    select_xq_comments,
    slim_xq_post,
)
from xueqiu_storage import XueqiuStorage


def _uids(cfg: dict) -> list[str]:
    bloggers = cfg.get("xueqiu_bloggers") or []
    uids = [str(b.get("user_id", "")).strip() for b in bloggers if b.get("user_id")]
    if not uids:
        raise SystemExit("config.json 的 xueqiu_bloggers 列表为空:至少填一个 user_id")
    return uids


def crawl_one(client: XueqiuClient, store: XueqiuStorage, uid: str, mode: str, cfg: dict) -> tuple[int, int]:
    """抓一个雪球博主,返回 (新增博文数, 新增/更新评论条数)。"""
    scope = cfg["xq_comment_scope"]
    prev_ts = store.get_last_ts(uid) if mode == "incremental" else 0

    if mode == "full":
        start_page, full_done = store.get_full_progress(uid)
        if full_done:
            start_page = 0  # 上次已到底,重跑全量从头
        start_page = max(start_page, 1)
    else:
        start_page = 1

    new_posts: list[dict] = []
    seen: set[str] = set()
    reached_old = False
    long_fail: list[str] = []

    page = start_page
    max_page = start_page
    while page <= cfg["xq_max_pages"]:
        statuses, max_page = client.fetch_timeline(uid, page)
        if not statuses:
            break
        batch: list[dict] = []
        for s in statuses:
            pid = str(s.get("id", ""))
            if not pid or pid in seen:
                continue
            ts = None
            try:
                ts = int(int(s.get("created_at")) / 1000)
            except (TypeError, ValueError):
                ts = None
            if mode == "incremental" and prev_ts and ts is not None and ts <= prev_ts:
                reached_old = True
                continue
            seen.add(pid)
            full = None
            from xueqiu_crawler import _needs_full  # 局部导入,避免污染模块级命名
            if cfg["enable_full_text"] and _needs_full(s):
                full = client.fetch_full_text(pid)
                if full is None:
                    long_fail.append(pid)
            batch.append(slim_xq_post(uid, s, full))
        store.upsert_posts(batch)
        new_posts.extend(batch)
        if mode == "full":
            store.set_full_progress(uid, page, 0, now_iso())
        if mode == "incremental" and reached_old:
            break
        if page >= max_page:
            break
        page += 1

    if mode == "full" and page >= max_page:
        store.set_full_progress(uid, max_page, 1, now_iso())
    if long_fail:
        shown = long_fail[:5]
        more = "..." if len(long_fail) > 5 else ""
        print(f"[warn] user_id={uid} {len(long_fail)} 篇长文补全失败:{shown}{more}", file=sys.stderr)

    # 更新增量水位
    ts_list = [p["created_ts"] for p in new_posts if p["created_ts"]]
    if ts_list:
        store.set_last_ts(uid, max(prev_ts, max(ts_list)), now_iso())

    # ---- 评论阶段 ----
    if scope == "none":
        return len(new_posts), 0
    if mode == "full":
        target_ids = store.posts_pending_comments(uid)
    else:
        cutoff = time.time() - cfg["comment_refresh_days"] * 86400
        recent = set(store.recent_post_ids(uid, cutoff))
        target_ids = list({p["id"] for p in new_posts} | recent)

    comment_rows = 0
    for pid in target_ids:
        try:
            top = client.fetch_comments(pid, cfg["xq_max_comment_pages"])
        except RuntimeError as e:
            print(f"[warn] user_id={uid} 博文 {pid} 评论失败:{e}", file=sys.stderr)
            continue
        rows = select_xq_comments(uid, pid, top, scope)
        store.upsert_comments(rows)
        comment_rows += len(rows)
        store.mark_comments_done(pid)

    return len(new_posts), comment_rows


def cmd_crawl(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    logger = obs.get_logger("xueqiu", cfg["log_dir"])

    if obs.is_paused("xueqiu", cfg["log_dir"]):
        reason = obs.paused_reason("xueqiu", cfg["log_dir"])
        obs.log(logger, logging.WARNING, "熔断", f"检测到熔断 flag({reason}),本轮跳过")
        raise SystemExit(f"雪球抓取已熔断:{reason}。处理后删除 logs/xueqiu.paused 再跑")

    uids = _uids(cfg)
    cookie = login_mod.get_cookie_header(cfg["xq_profile_dir"])
    store = XueqiuStorage(cfg["db_path"])
    client = XueqiuClient(
        cookie,
        cfg["min_delay"],
        cfg["max_delay"],
        refresh_cookie=lambda: login_mod.get_cookie_header(cfg["xq_profile_dir"]),
    )

    threshold = int(cfg["auth_fail_breaker"])
    consecutive_auth_fail = 0
    total_posts = total_comments = 0
    for uid in uids:
        try:
            np, nc = crawl_one(client, store, uid, args.mode, cfg)
            consecutive_auth_fail = 0
            total_posts += np
            total_comments += nc
            obs.log(logger, logging.INFO, "抓取", f"user_id={uid} 新增博文 {np}、评论 {nc}")
            print(f"user_id={uid}:新增博文 {np},评论 {nc} 条")
        except AuthError as e:
            consecutive_auth_fail += 1
            obs.log(logger, logging.ERROR, "抓取", f"user_id={uid} 登录失效({consecutive_auth_fail}/{threshold}):{e}")
            print(f"[error] user_id={uid} 登录失效:{e}", file=sys.stderr)
            if consecutive_auth_fail >= threshold:
                obs.set_paused("xueqiu", f"连续 {consecutive_auth_fail} 次 401/403", cfg["log_dir"])
                obs.log(logger, logging.ERROR, "熔断", f"连续 {consecutive_auth_fail} 次登录失效,已熔断停止本轮")
                print("[error] 连续登录失效超阈值,已熔断,停止本轮抓取", file=sys.stderr)
                break
        except RuntimeError as e:
            obs.log(logger, logging.ERROR, "抓取", f"user_id={uid} 抓取中断:{e}")
            print(f"[error] user_id={uid} 抓取中断:{e}", file=sys.stderr)

    pc, cc = store.count_posts(), store.count_comments()
    store.close()
    print(
        f"\n完成(mode={args.mode}):本次新增博文 {total_posts}、评论 {total_comments} 条;"
        f"库内累计雪球博文 {pc}、评论 {cc} → {cfg['db_path']}"
    )


def cmd_login(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    login_mod.login(cfg["xq_profile_dir"])


def main() -> None:
    ap = argparse.ArgumentParser(description="雪球单/多博主博文+评论抓取(自用)")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="command", required=True)
    sp_login = sub.add_parser("login", help="登录并持久化登录态")
    sp_login.set_defaults(func=cmd_login)
    sp_crawl = sub.add_parser("crawl", help="抓取")
    sp_crawl.add_argument("--mode", choices=["full", "incremental"], default="incremental")
    sp_crawl.set_defaults(func=cmd_crawl)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```
> 说明:`load_config` 从 main.py 复用,但它会强制校验微博 `bloggers` 非空。一期微博+雪球都用,通常都有配置;若只想跑雪球,`bloggers` 也需至少留示例项(已在 config.example 保留)。

- [x] **Step 2: DEFAULTS 补雪球键(改 main.py,让 load_config 兜底)**

在 [main.py](main.py) 的 `DEFAULTS` 追加雪球默认键(供 load_config 合并):
```python
    "auth_fail_breaker": 3,
    "log_dir": "logs",
    "xq_profile_dir": ".xq_profile",
    "xq_max_pages": 200,
    "xq_max_comment_pages": 5,
    "xq_comment_scope": "author_only",
}
```

- [x] **Step 3: 冒烟验证 import + flag 分支**

```powershell
python -c "import xueqiu_crawl; print('ok')"
python -c "import obs; obs.set_paused('xueqiu','手动测试','logs')"
python xueqiu_crawl.py crawl --mode incremental
python -c "import obs; obs.clear_paused('xueqiu','logs')"
```
Expected: 先打印 `ok`;再打印 `雪球抓取已熔断:手动测试...` 退出;最后 flag 清除。

- [ ] **Step 4: 提交**

```powershell
git add xueqiu_crawl.py main.py
git commit -m "feat(xueqiu): crawl CLI with incremental/full, breaker, logging"
```

### Task 1.5: 雪球端到端手动验证

**Files:** 无(手动验证)。

- [ ] **Step 1: 登录**

```powershell
python xueqiu_crawl.py login
```
Expected: 弹出浏览器,登录雪球后打印"雪球登录成功";`.xq_profile/` 生成。

- [ ] **Step 2: 全量抓一个博主**

在 config.json 的 `xueqiu_bloggers` 填一个真实 user_id,然后:
```powershell
python xueqiu_crawl.py crawl --mode full
```
Expected: 打印各 user_id 新增博文/评论数;`data/app.db` 出现 `xq_posts`/`xq_comments` 行。字段报错时按"对字段"原则调整 `slim_xq_post`/`fetch_timeline`。

- [ ] **Step 3: 再跑增量验证去重与断点**

```powershell
python xueqiu_crawl.py crawl --mode incremental
```
Expected: 第二次新增博文≈0(去重生效),`xq_crawl_state.last_ts` 有值。用 SQLite 查:
```powershell
python -c "import sqlite3;c=sqlite3.connect('data/app.db');print(c.execute('SELECT COUNT(*) FROM xq_posts').fetchone())"
```

- [ ] **Step 4: 记录接口字段实况到 repo 记忆(可选但推荐)**

若字段与计划假设不符,用 memory 工具在 `/memories/repo/` 记一条雪球字段实况,供后续期复用。

---

## Phase 2 — 行情工具 market/

> akshare/adata 是成熟第三方库,只写薄封装。资金流近似口径直接用 akshare 板块资金流排名的"主力/中单/小单净流入-净额",字段名带 `_est` 并注明"估算,非真实机构/散户"。akshare 列名易变,封装在系统边界做列缺失检查,缺列记 WARNING 而非崩溃。

### Task 2.1: market/storage.py

**Files:**
- Create: `market/__init__.py`(空)
- Create: `market/storage.py`
- Test: `tests/test_market_storage.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_market_storage.py`:
```python
"""行情存储层单测:快照按 (日期,板块) 去重;龙虎榜按 (日期,代码,原因) 去重;近似字段名带 _est。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from market.storage import MarketStorage


class TestMarketStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MarketStorage(str(Path(self.tmp.name) / "app.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_snapshot_dedup(self):
        row = {"trade_date": "20260716", "sector": "半导体", "pct_change": 1.2,
               "turnover": None, "volume": None, "main_net_inflow_est": 1e8,
               "retail_net_inflow_est": -2e7, "source": "akshare", "fetched_at": "now"}
        self.store.upsert_snapshot([row, dict(row, pct_change=1.5)])
        rows = self.store.snapshot_for("20260716")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["pct_change"], 1.5)  # 覆盖为最新
        self.assertIn("main_net_inflow_est", rows[0].keys())

    def test_dragon_tiger_dedup(self):
        row = {"trade_date": "20260716", "stock_code": "000001", "stock_name": "平安",
               "reason": "日涨幅偏离", "net_buy": 1e7, "buy_amount": 2e7, "sell_amount": 1e7,
               "inst_seat_count": 2, "raw_json": "{}", "fetched_at": "now"}
        self.store.upsert_dragon_tiger([row, row])
        self.assertEqual(len(self.store.dragon_tiger_for("20260716")), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_market_storage -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'market'`。

- [x] **Step 3: 实现**

创建 `market/__init__.py`(空)。创建 `market/storage.py`:
```python
"""行情快照存储层(与微博/雪球共用 data/app.db)。

两张表:
  market_snapshot 按 (trade_date, sector) 存板块级行情。
      直接口径:pct_change / turnover(成交额) / volume(成交量)。
      近似口径(字段名带 _est,估算、非真实机构/散户):
        main_net_inflow_est   主力净流入(超大+大单)≈机构
        retail_net_inflow_est 中小单净流入 ≈ 散户
  dragon_tiger 龙虎榜,按 (trade_date, stock_code, reason) 去重;
      inst_seat_count = 机构专用席位数(真机构动向,作补充信号,与资金流分开)。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_snapshot (
    trade_date            TEXT NOT NULL,
    sector                TEXT NOT NULL,
    pct_change            REAL,
    turnover              REAL,   -- 成交额(直接口径),缺数据时为 NULL
    volume                REAL,   -- 成交量(直接口径),缺数据时为 NULL
    main_net_inflow_est   REAL,   -- 主力净流入估算(超大+大单),非真实机构
    retail_net_inflow_est REAL,   -- 中小单净流入估算,非真实散户
    source                TEXT,
    fetched_at            TEXT,
    PRIMARY KEY (trade_date, sector)
);

CREATE TABLE IF NOT EXISTS dragon_tiger (
    trade_date      TEXT NOT NULL,
    stock_code      TEXT NOT NULL,
    stock_name      TEXT,
    reason          TEXT NOT NULL,
    net_buy         REAL,
    buy_amount      REAL,
    sell_amount     REAL,
    inst_seat_count INTEGER,      -- 机构专用席位数(真机构信号)
    raw_json        TEXT,
    fetched_at      TEXT,
    PRIMARY KEY (trade_date, stock_code, reason)
);
"""


class MarketStorage:
    def __init__(self, db_path: str):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def upsert_snapshot(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO market_snapshot (trade_date, sector, pct_change, turnover, volume, "
                "main_net_inflow_est, retail_net_inflow_est, source, fetched_at) VALUES "
                "(:trade_date,:sector,:pct_change,:turnover,:volume,:main_net_inflow_est,"
                ":retail_net_inflow_est,:source,:fetched_at) "
                "ON CONFLICT(trade_date, sector) DO UPDATE SET pct_change=excluded.pct_change, "
                "turnover=excluded.turnover, volume=excluded.volume, "
                "main_net_inflow_est=excluded.main_net_inflow_est, "
                "retail_net_inflow_est=excluded.retail_net_inflow_est, "
                "source=excluded.source, fetched_at=excluded.fetched_at",
                rows,
            )

    def upsert_dragon_tiger(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT OR REPLACE INTO dragon_tiger (trade_date, stock_code, stock_name, reason, "
                "net_buy, buy_amount, sell_amount, inst_seat_count, raw_json, fetched_at) VALUES "
                "(:trade_date,:stock_code,:stock_name,:reason,:net_buy,:buy_amount,:sell_amount,"
                ":inst_seat_count,:raw_json,:fetched_at)",
                rows,
            )

    def snapshot_for(self, trade_date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM market_snapshot WHERE trade_date = ? ORDER BY pct_change DESC",
            (trade_date,),
        ).fetchall()
        return [dict(r) for r in rows]

    def dragon_tiger_for(self, trade_date: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM dragon_tiger WHERE trade_date = ? ORDER BY net_buy DESC",
            (trade_date,),
        ).fetchall()
        return [dict(r) for r in rows]
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_market_storage -v`
Expected: PASS(2 tests)。

- [ ] **Step 5: 提交**

```powershell
git add market/__init__.py market/storage.py tests/test_market_storage.py
git commit -m "feat(market): snapshot + dragon_tiger storage with est fields"
```

### Task 2.2: market/fetcher.py(akshare 薄封装)

**Files:**
- Create: `market/fetcher.py`
- Test: `tests/test_market_fetcher.py`(注入假 akshare,验证列映射与近似口径)

- [x] **Step 1: 写失败测试(注入假 akshare)**

创建 `tests/test_market_fetcher.py`:
```python
"""行情抓取层单测:注入假 akshare(返回 list[dict] 模拟 DataFrame.to_dict('records')),
验证板块资金流近似口径映射(主力=主力净流入-净额;散户=中单+小单)与列缺失容错。"""
from __future__ import annotations

import unittest

from market import fetcher


class _FakeDF:
    def __init__(self, records: list[dict]):
        self._records = records
        self.columns = list(records[0].keys()) if records else []

    def to_dict(self, orient: str):
        assert orient == "records"
        return self._records


class _FakeAk:
    def stock_sector_fund_flow_rank(self, indicator, sector_type):
        return _FakeDF([{
            "名称": "半导体", "今日涨跌幅": 2.1,
            "今日主力净流入-净额": 1.0e8,
            "今日中单净流入-净额": -3.0e7,
            "今日小单净流入-净额": -1.0e7,
        }])

    def stock_board_industry_name_em(self):
        return _FakeDF([{"板块名称": "半导体", "成交额": 5.0e9, "成交量": 1.2e8}])

    def stock_lhb_detail_em(self, start_date, end_date):
        return _FakeDF([{
            "代码": "000001", "名称": "平安银行", "上榜原因": "日涨幅偏离值达7%",
            "龙虎榜净买额": 1.0e7, "龙虎榜买入额": 2.0e7, "龙虎榜卖出额": 1.0e7,
        }])


class TestMarketFetcher(unittest.TestCase):
    def test_fund_flow_est_mapping(self):
        rows = fetcher.fetch_sector_fund_flow("20260716", ak=_FakeAk(), sector_type="行业资金流")
        r = rows[0]
        self.assertEqual(r["sector"], "半导体")
        self.assertAlmostEqual(r["pct_change"], 2.1)
        self.assertAlmostEqual(r["main_net_inflow_est"], 1.0e8)
        self.assertAlmostEqual(r["retail_net_inflow_est"], -4.0e7)  # 中单+小单

    def test_snapshot_merges_quote(self):
        rows = fetcher.fetch_snapshot("20260716", ak=_FakeAk(), sector_type="行业资金流")
        r = rows[0]
        self.assertAlmostEqual(r["turnover"], 5.0e9)
        self.assertAlmostEqual(r["volume"], 1.2e8)
        self.assertEqual(r["trade_date"], "20260716")

    def test_dragon_tiger_mapping(self):
        rows = fetcher.fetch_dragon_tiger("20260716", ak=_FakeAk())
        self.assertEqual(rows[0]["stock_code"], "000001")
        self.assertEqual(rows[0]["reason"], "日涨幅偏离值达7%")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_market_fetcher -v`
Expected: FAIL — `ImportError: cannot import name 'fetcher'` 或属性缺失。

- [x] **Step 3: 实现 market/fetcher.py**

创建 `market/fetcher.py`:
```python
"""行情抓取层:akshare 薄封装(第三方库以参数 ak 注入,便于测试)。

板块资金流近似口径(设计文档 5):
  main_net_inflow_est   = akshare "今日主力净流入-净额"(=超大+大单,akshare 已合并)
  retail_net_inflow_est = "今日中单净流入-净额" + "今日小单净流入-净额"
两者均为估算、非真实机构/散户逐日买卖数据。

akshare 列名易变:在边界做列缺失容错——缺列时该字段填 None 并抛/记 MarketError,
不静默给错数。成交额/成交量为 best-effort(板块名称接口不一定含),缺则 None。
"""

from __future__ import annotations

from datetime import datetime, timezone


class MarketError(RuntimeError):
    """行情抓取失败(akshare 列缺失/接口异常)。CLI 层捕获后记 ERROR 日志。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _records(df) -> list[dict]:
    """兼容 pandas.DataFrame 与测试用假对象;统一转 list[dict]。"""
    return df.to_dict("records")


def _require(row: dict, keys: list[str], ctx: str) -> None:
    missing = [k for k in keys if k not in row]
    if missing:
        raise MarketError(f"{ctx}:akshare 返回缺列 {missing},实际列={list(row.keys())}(接口可能改版,需对字段)")


def fetch_sector_fund_flow(trade_date: str, ak=None, sector_type: str = "行业资金流") -> list[dict]:
    """板块资金流排名 → 近似口径行。ak 缺省时懒加载真实 akshare。"""
    if ak is None:
        import akshare as ak  # noqa: PLC0415
    df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type=sector_type)
    recs = _records(df)
    out: list[dict] = []
    for r in recs:
        _require(r, ["名称", "今日涨跌幅", "今日主力净流入-净额",
                     "今日中单净流入-净额", "今日小单净流入-净额"], "板块资金流")
        out.append({
            "trade_date": trade_date,
            "sector": r["名称"],
            "pct_change": r["今日涨跌幅"],
            "turnover": None,
            "volume": None,
            "main_net_inflow_est": r["今日主力净流入-净额"],
            "retail_net_inflow_est": (r["今日中单净流入-净额"] or 0) + (r["今日小单净流入-净额"] or 0),
            "source": "akshare",
            "fetched_at": _now_iso(),
        })
    return out


def _fetch_sector_quote(ak) -> dict[str, dict]:
    """板块成交额/成交量(best-effort),返回 {板块名: {turnover, volume}}。缺列则跳过。"""
    try:
        df = ak.stock_board_industry_name_em()
        recs = _records(df)
    except Exception:  # 边界:接口不可用不阻断资金流主数据
        return {}
    quote: dict[str, dict] = {}
    for r in recs:
        name = r.get("板块名称") or r.get("名称")
        if not name:
            continue
        quote[name] = {"turnover": r.get("成交额"), "volume": r.get("成交量")}
    return quote


def fetch_snapshot(trade_date: str, ak=None, sector_type: str = "行业资金流") -> list[dict]:
    """完整板块快照 = 资金流近似口径 + 成交额/量(best-effort 合并)。"""
    if ak is None:
        import akshare as ak  # noqa: PLC0415
    rows = fetch_sector_fund_flow(trade_date, ak=ak, sector_type=sector_type)
    quote = _fetch_sector_quote(ak)
    for row in rows:
        q = quote.get(row["sector"])
        if q:
            row["turnover"] = q.get("turnover")
            row["volume"] = q.get("volume")
    return rows


def fetch_dragon_tiger(trade_date: str, ak=None) -> list[dict]:
    """龙虎榜明细。inst_seat_count 需另接口拆解席位,一期先置 None(留二期补)。"""
    if ak is None:
        import akshare as ak  # noqa: PLC0415
    import json as _json
    df = ak.stock_lhb_detail_em(start_date=trade_date, end_date=trade_date)
    recs = _records(df)
    out: list[dict] = []
    for r in recs:
        _require(r, ["代码", "名称", "上榜原因"], "龙虎榜")
        out.append({
            "trade_date": trade_date,
            "stock_code": str(r["代码"]),
            "stock_name": r["名称"],
            "reason": r["上榜原因"],
            "net_buy": r.get("龙虎榜净买额"),
            "buy_amount": r.get("龙虎榜买入额"),
            "sell_amount": r.get("龙虎榜卖出额"),
            "inst_seat_count": None,
            "raw_json": _json.dumps(r, ensure_ascii=False, default=str),
            "fetched_at": _now_iso(),
        })
    return out
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_market_fetcher -v`
Expected: PASS(3 tests)。

- [ ] **Step 5: 提交**

```powershell
git add market/fetcher.py tests/test_market_fetcher.py
git commit -m "feat(market): akshare fetcher with est fund-flow mapping + column guard"
```

### Task 2.3: market/cli.py(snapshot 命令)

**Files:**
- Create: `market/cli.py`

- [x] **Step 1: 实现 market/cli.py**

创建 `market/cli.py`:
```python
"""行情快照 CLI:盘后拉当天板块行情 + 龙虎榜,存进 market_snapshot / dragon_tiger。

  python -m market.cli snapshot [--date YYYYMMDD] [--config config.json]

失败(akshare 接口/列变化)记 ERROR 日志(模块名 market),不写脏数据。
"""

from __future__ import annotations

import argparse
import logging
from datetime import date

import obs
from main import load_config
from market.fetcher import MarketError, fetch_dragon_tiger, fetch_snapshot
from market.storage import MarketStorage


def cmd_snapshot(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    logger = obs.get_logger("market", cfg["log_dir"])
    trade_date = args.date or date.today().strftime("%Y%m%d")
    sector_type = (cfg.get("market") or {}).get("sector_type", "行业资金流")
    store = MarketStorage(cfg["db_path"])
    try:
        sectors = fetch_snapshot(trade_date, sector_type=sector_type)
        store.upsert_snapshot(sectors)
        obs.log(logger, logging.INFO, "行情", f"{trade_date} 板块快照 {len(sectors)} 条已入库")
        print(f"板块快照:{len(sectors)} 条")
    except MarketError as e:
        obs.log(logger, logging.ERROR, "行情", f"{trade_date} 板块快照失败:{e}")
        print(f"[error] 板块快照失败:{e}")
    try:
        dt = fetch_dragon_tiger(trade_date)
        store.upsert_dragon_tiger(dt)
        obs.log(logger, logging.INFO, "行情", f"{trade_date} 龙虎榜 {len(dt)} 条已入库")
        print(f"龙虎榜:{len(dt)} 条")
    except MarketError as e:
        obs.log(logger, logging.ERROR, "行情", f"{trade_date} 龙虎榜失败:{e}")
        print(f"[error] 龙虎榜失败:{e}")
    store.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="A 股板块行情 + 龙虎榜快照(自用)")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("snapshot", help="拉当天快照入库")
    sp.add_argument("--date", default="", help="交易日 YYYYMMDD(默认今天)")
    sp.set_defaults(func=cmd_snapshot)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 手动验证(真 akshare)**

```powershell
python -m market.cli snapshot
```
Expected: 打印板块/龙虎榜条数;`market_snapshot`/`dragon_tiger` 有当天行。若列报错(MarketError),按错误里打印的"实际列"调整 [market/fetcher.py](market/fetcher.py) 的列名映射,并把实况记到 `/memories/repo/`。

- [ ] **Step 3: 验证近似字段标注正确**

```powershell
python -c "import sqlite3;c=sqlite3.connect('data/app.db');r=c.execute('SELECT sector,main_net_inflow_est,retail_net_inflow_est FROM market_snapshot LIMIT 3').fetchall();print(r)"
```
Expected: 打印板块 + 两个 `_est` 估算字段值。

- [ ] **Step 4: 提交**

```powershell
git add market/cli.py
git commit -m "feat(market): snapshot CLI wiring fetcher into storage with logging"
```

---

## Phase 3 — 简报生成 report/

### Task 3.1: report/storage.py(daily_report 表)

**Files:**
- Create: `report/__init__.py`(空)
- Create: `report/storage.py`
- Test: `tests/test_report_storage.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_report_storage.py`:
```python
"""简报存储层单测:按日期唯一、可标记已推送、可读回。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from report.storage import ReportStorage


class TestReportStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ReportStorage(str(Path(self.tmp.name) / "app.db"))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_save_and_get(self):
        self.store.save("2026-07-16", "今日观察……", "test-model", '{"weibo":3}', "now")
        r = self.store.get("2026-07-16")
        self.assertEqual(r["content"], "今日观察……")
        self.assertEqual(r["pushed"], 0)

    def test_save_overwrites_same_date(self):
        self.store.save("2026-07-16", "v1", "m", "{}", "now")
        self.store.save("2026-07-16", "v2", "m", "{}", "now")
        self.assertEqual(self.store.get("2026-07-16")["content"], "v2")

    def test_mark_pushed(self):
        self.store.save("2026-07-16", "x", "m", "{}", "now")
        self.store.mark_pushed("2026-07-16")
        self.assertEqual(self.store.get("2026-07-16")["pushed"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_report_storage -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'report'`。

- [x] **Step 3: 实现**

创建 `report/__init__.py`(空)。创建 `report/storage.py`:
```python
"""简报存储层(与其它组件共用 data/app.db)。

daily_report 按 report_date 唯一存档:留档、防重复推送(pushed 标志)、便于回看。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_report (
    report_date   TEXT PRIMARY KEY,
    content       TEXT,
    model         TEXT,
    source_counts TEXT,           -- JSON:各数据源当天条数,便于回看
    created_at    TEXT,
    pushed        INTEGER DEFAULT 0
);
"""


class ReportStorage:
    def __init__(self, db_path: str):
        p = Path(db_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def save(self, report_date: str, content: str, model: str, source_counts: str, now_iso: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO daily_report (report_date, content, model, source_counts, created_at, pushed) "
                "VALUES (?,?,?,?,?,0) "
                "ON CONFLICT(report_date) DO UPDATE SET content=excluded.content, "
                "model=excluded.model, source_counts=excluded.source_counts, "
                "created_at=excluded.created_at, pushed=0",
                (report_date, content, model, source_counts, now_iso),
            )

    def get(self, report_date: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM daily_report WHERE report_date = ?", (report_date,)
        ).fetchone()
        return dict(row) if row else None

    def mark_pushed(self, report_date: str) -> None:
        with self._tx() as c:
            c.execute("UPDATE daily_report SET pushed = 1 WHERE report_date = ?", (report_date,))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_report_storage -v`
Expected: PASS(3 tests)。

- [ ] **Step 5: 提交**

```powershell
git add report/__init__.py report/storage.py tests/test_report_storage.py
git commit -m "feat(report): daily_report storage"
```

### Task 3.2: report/reader.py(读当天数据)

**Files:**
- Create: `report/reader.py`
- Test: `tests/test_report_reader.py`(seeded db)

- [x] **Step 1: 写失败测试**

创建 `tests/test_report_reader.py`:
```python
"""简报数据读取单测:seed 一个库,验证按 lookback 取当天文本 + 当天行情快照。"""
from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from report.reader import collect


class TestReportReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "app.db")
        now = int(time.time())
        old = now - 3 * 86400
        c = sqlite3.connect(self.db)
        c.executescript(
            "CREATE TABLE posts(id TEXT PRIMARY KEY, uid TEXT, user_name TEXT, created_ts INTEGER, text TEXT);"
            "CREATE TABLE xq_posts(id TEXT PRIMARY KEY, user_id TEXT, user_name TEXT, created_ts INTEGER, text TEXT);"
            "CREATE TABLE market_snapshot(trade_date TEXT, sector TEXT, pct_change REAL, "
            "turnover REAL, volume REAL, main_net_inflow_est REAL, retail_net_inflow_est REAL, "
            "source TEXT, fetched_at TEXT, PRIMARY KEY(trade_date,sector));"
            "CREATE TABLE dragon_tiger(trade_date TEXT, stock_code TEXT, stock_name TEXT, reason TEXT, "
            "net_buy REAL, buy_amount REAL, sell_amount REAL, inst_seat_count INTEGER, raw_json TEXT, "
            "fetched_at TEXT, PRIMARY KEY(trade_date,stock_code,reason));"
        )
        c.execute("INSERT INTO posts VALUES('1','u','大V',?, '看多半导体')", (now,))
        c.execute("INSERT INTO posts VALUES('2','u','大V',?, '这是三天前')", (old,))
        c.execute("INSERT INTO xq_posts VALUES('9','x','雪球客',?, '新能源要涨')", (now,))
        c.execute("INSERT INTO market_snapshot VALUES('20260716','半导体',2.1,1.0,1.0,1e8,-2e7,'akshare','now')")
        c.commit()
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_collect_respects_lookback(self):
        data = collect(self.db, lookback_hours=24, trade_date="20260716")
        wb_texts = [p["text"] for p in data["weibo_posts"]]
        self.assertIn("看多半导体", wb_texts)
        self.assertNotIn("这是三天前", wb_texts)     # 超出 lookback
        self.assertEqual(len(data["xq_posts"]), 1)
        self.assertEqual(len(data["sectors"]), 1)
        self.assertEqual(data["date"], "20260716")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_report_reader -v`
Expected: FAIL — 无 `report.reader`。

- [x] **Step 3: 实现 report/reader.py**

创建 `report/reader.py`:
```python
"""简报数据读取:从 data/app.db 读当天新文本(微博/雪球)+ 当天行情快照 + 龙虎榜。

按 lookback_hours 用 created_ts 圈"当天新增文本";行情/龙虎榜按 trade_date 取。
只读,不写。返回结构化 dict 供 prompt 组装。
"""

from __future__ import annotations

import sqlite3
import time


def _rows(conn: sqlite3.Connection, sql: str, args: tuple) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def collect(db_path: str, lookback_hours: int, trade_date: str) -> dict:
    """汇总当天数据。trade_date 用于行情表(YYYYMMDD);文本按 now-lookback 圈选。"""
    cutoff = time.time() - lookback_hours * 3600
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        weibo = _rows(
            conn,
            "SELECT user_name, text, created_ts FROM posts WHERE created_ts >= ? "
            "AND text != '' ORDER BY created_ts DESC",
            (cutoff,),
        )
        xq = _rows(
            conn,
            "SELECT user_name, text, created_ts FROM xq_posts WHERE created_ts >= ? "
            "AND text != '' ORDER BY created_ts DESC",
            (cutoff,),
        )
        sectors = _rows(
            conn,
            "SELECT sector, pct_change, turnover, volume, main_net_inflow_est, retail_net_inflow_est "
            "FROM market_snapshot WHERE trade_date = ? ORDER BY pct_change DESC",
            (trade_date,),
        )
        dragon = _rows(
            conn,
            "SELECT stock_name, stock_code, reason, net_buy, inst_seat_count "
            "FROM dragon_tiger WHERE trade_date = ? ORDER BY net_buy DESC",
            (trade_date,),
        )
    finally:
        conn.close()

    return {
        "date": trade_date,
        "weibo_posts": weibo,
        "xq_posts": xq,
        "sectors": sectors,
        "dragon_tiger": dragon,
    }
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_report_reader -v`
Expected: PASS(1 test)。

- [ ] **Step 5: 提交**

```powershell
git add report/reader.py tests/test_report_reader.py
git commit -m "feat(report): reader collecting daily text + market snapshot"
```

### Task 3.3: report/prompt.py(纯函数组 prompt)

**Files:**
- Create: `report/prompt.py`
- Test: `tests/test_report_prompt.py`

- [x] **Step 1: 写失败测试**

创建 `tests/test_report_prompt.py`:
```python
"""prompt 组装单测:纯函数,验证含合规约束(不搬原文)、包含板块与文本摘要输入。"""
from __future__ import annotations

import unittest

from report.prompt import build_prompt


class TestPrompt(unittest.TestCase):
    def _data(self):
        return {
            "date": "20260716",
            "weibo_posts": [{"user_name": "大V", "text": "看多半导体"}],
            "xq_posts": [{"user_name": "雪球客", "text": "新能源要涨"}],
            "sectors": [{"sector": "半导体", "pct_change": 2.1,
                         "main_net_inflow_est": 1e8, "retail_net_inflow_est": -2e7,
                         "turnover": None, "volume": None}],
            "dragon_tiger": [{"stock_name": "平安", "reason": "涨幅偏离", "net_buy": 1e7}],
        }

    def test_prompt_has_compliance_and_inputs(self):
        system, user = build_prompt(self._data())
        self.assertIn("不搬运原文", system)
        self.assertIn("半导体", user)
        self.assertIn("看多半导体", user)
        self.assertIn("估算", user)   # 近似口径须提示 LLM

    def test_prompt_handles_empty(self):
        system, user = build_prompt({"date": "20260716", "weibo_posts": [],
                                     "xq_posts": [], "sectors": [], "dragon_tiger": []})
        self.assertIn("20260716", user)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_report_prompt -v`
Expected: FAIL — 无 `report.prompt`。

- [x] **Step 3: 实现 report/prompt.py**

创建 `report/prompt.py`:
```python
"""简报 prompt 组装(纯函数,便于测试)。

合规:要求 LLM 只提"涉及哪些股/板块 + 大 V/散户观点倾向",不搬运原文。
行情近似口径明确告知 LLM 是"估算、非真实机构/散户",避免过度解读。
"""

from __future__ import annotations

SYSTEM = (
    "你是股票舆情观察助手。基于给定的社媒文本与当日行情,产出一份简洁的中文《每日观察简报》。"
    "只归纳:今天大 V/散户在聊哪些股票和板块、观点倾向(看多/看空/分歧),以及这些板块当日表现。"
    "严格要求:不搬运/不逐句复述原文,只输出加工后的结论;这是观察参考视角,不是买卖信号,"
    "不给任何操作建议。行情里的资金流为按单子大小估算的近似值,非真实机构/散户数据,措辞需谨慎。"
)


def _fmt_texts(title: str, posts: list[dict], limit: int = 60) -> str:
    if not posts:
        return f"【{title}】(今日无新增)\n"
    lines = [f"【{title}】共 {len(posts)} 条,摘录:"]
    for p in posts[:limit]:
        text = (p.get("text") or "").replace("\n", " ").strip()
        if len(text) > 120:
            text = text[:120] + "…"
        lines.append(f"- {p.get('user_name','')}: {text}")
    return "\n".join(lines) + "\n"


def _fmt_sectors(sectors: list[dict], limit: int = 20) -> str:
    if not sectors:
        return "【当日板块行情】(无数据)\n"
    lines = ["【当日板块行情】(资金流为估算近似值,非真实机构/散户):"]
    for s in sectors[:limit]:
        lines.append(
            f"- {s.get('sector','')}: 涨跌幅 {s.get('pct_change')}%,"
            f"主力净流入估算 {s.get('main_net_inflow_est')},"
            f"散户净流入估算 {s.get('retail_net_inflow_est')}"
        )
    return "\n".join(lines) + "\n"


def _fmt_dragon(dragon: list[dict], limit: int = 15) -> str:
    if not dragon:
        return "【龙虎榜】(无数据)\n"
    lines = ["【龙虎榜】(机构席位=真机构信号):"]
    for d in dragon[:limit]:
        lines.append(f"- {d.get('stock_name','')}({d.get('reason','')}),净买额 {d.get('net_buy')}")
    return "\n".join(lines) + "\n"


def build_prompt(data: dict) -> tuple[str, str]:
    """返回 (system, user)。data 来自 report.reader.collect。"""
    parts = [
        f"日期:{data.get('date','')}",
        "",
        _fmt_texts("微博大 V 博文", data.get("weibo_posts") or []),
        _fmt_texts("雪球讨论", data.get("xq_posts") or []),
        _fmt_sectors(data.get("sectors") or []),
        _fmt_dragon(data.get("dragon_tiger") or []),
        "",
        "请产出《每日观察简报》:先一句总览,再按板块列要点(涉及哪些股/板块、观点倾向、当日表现),"
        "最后一句风险提示。控制在 500 字内。",
    ]
    return SYSTEM, "\n".join(parts)
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_report_prompt -v`
Expected: PASS(2 tests)。

- [ ] **Step 5: 提交**

```powershell
git add report/prompt.py tests/test_report_prompt.py
git commit -m "feat(report): compliant prompt assembly"
```

### Task 3.4: report/llm.py(OpenAI 兼容调用)

**Files:**
- Create: `report/llm.py`
- Test: `tests/test_report_llm.py`(注入假 http)

- [x] **Step 1: 写失败测试(注入假 requests)**

创建 `tests/test_report_llm.py`:
```python
"""LLM 调用单测:注入假 http,验证请求体结构 + 解析 choices[0].message.content。"""
from __future__ import annotations

import unittest

from report.llm import chat


class _FakeResp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"content": "这是简报"}}]}

    def raise_for_status(self):
        pass


class _FakeHttp:
    def __init__(self):
        self.last = None

    def post(self, url, headers=None, json=None, timeout=None):
        self.last = {"url": url, "headers": headers, "json": json}
        return _FakeResp()


class TestLLM(unittest.TestCase):
    def test_chat_builds_request_and_parses(self):
        http = _FakeHttp()
        out = chat("系统", "用户", base_url="https://x/api/v1", model="m",
                   api_key="k", http=http)
        self.assertEqual(out, "这是简报")
        self.assertTrue(http.last["url"].endswith("/chat/completions"))
        self.assertEqual(http.last["json"]["model"], "m")
        self.assertEqual(http.last["headers"]["Authorization"], "Bearer k")
        roles = [m["role"] for m in http.last["json"]["messages"]]
        self.assertEqual(roles, ["system", "user"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_report_llm -v`
Expected: FAIL — 无 `report.llm`。

- [x] **Step 3: 实现 report/llm.py**

创建 `report/llm.py`:
```python
"""LLM 调用:OpenAI 兼容 /chat/completions(用 Hermes 已配的模型/端点)。

base_url + model 走 config.json 的 report.llm;API Key 走环境变量 LLM_API_KEY(不入库)。
http 客户端参数注入(缺省用 requests),便于测试。
只用 requests(已有依赖),不引 openai SDK。
"""

from __future__ import annotations

from errors import RuntimeError as _RE  # 占位避免误用;实际用内置 RuntimeError


class LLMError(RuntimeError):
    """LLM 调用失败(网络/鉴权/响应结构异常)。CLI 层记 ERROR。"""


def chat(
    system: str,
    user: str,
    base_url: str,
    model: str,
    api_key: str,
    http=None,
    timeout: int = 120,
) -> str:
    """调用 chat completions,返回 assistant 文本。失败抛 LLMError。"""
    if not api_key:
        raise LLMError("缺少 API Key:请设置环境变量 LLM_API_KEY")
    if http is None:
        import requests as http  # noqa: PLC0415
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = http.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:  # 网络/HTTP/JSON 边界
        raise LLMError(f"LLM 请求失败:{e}") from e
    try:
        return body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError, TypeError) as e:
        raise LLMError(f"LLM 响应结构异常:{body}") from e
```
> 修正:删掉第一行错误的占位 import。文件顶部应只保留 `from __future__ import annotations`,不要 `from errors import ...`。实现时确保如下:

```python
from __future__ import annotations


class LLMError(RuntimeError):
    ...
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_report_llm -v`
Expected: PASS(1 test)。

- [ ] **Step 5: 提交**

```powershell
git add report/llm.py tests/test_report_llm.py
git commit -m "feat(report): OpenAI-compatible LLM caller with injectable http"
```

### Task 3.5: report/builder.py + report/cli.py

**Files:**
- Create: `report/builder.py`
- Create: `report/cli.py`

- [x] **Step 1: 实现 report/builder.py**

创建 `report/builder.py`:
```python
"""简报编排:reader → prompt → llm → 存 daily_report。返回简报文本。"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone

from report.llm import chat
from report.prompt import build_prompt
from report.reader import collect
from report.storage import ReportStorage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_report(db_path: str, cfg: dict, trade_date: str | None = None) -> str:
    """生成并存档当天简报,返回文本。LLM 失败会向上抛 LLMError。"""
    trade_date = trade_date or date.today().strftime("%Y%m%d")
    report_cfg = cfg.get("report") or {}
    llm_cfg = report_cfg.get("llm") or {}
    lookback = int(report_cfg.get("lookback_hours", 24))

    data = collect(db_path, lookback_hours=lookback, trade_date=trade_date)
    system, user = build_prompt(data)
    content = chat(
        system,
        user,
        base_url=llm_cfg.get("base_url", ""),
        model=llm_cfg.get("model", ""),
        api_key=os.environ.get("LLM_API_KEY", ""),
    )

    counts = {
        "weibo": len(data["weibo_posts"]),
        "xueqiu": len(data["xq_posts"]),
        "sectors": len(data["sectors"]),
        "dragon_tiger": len(data["dragon_tiger"]),
    }
    # 存档 report_date 用 ISO 日期(YYYY-MM-DD),便于 notify/回看
    report_date = datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")
    store = ReportStorage(db_path)
    try:
        store.save(report_date, content, llm_cfg.get("model", ""), json.dumps(counts, ensure_ascii=False), _now_iso())
    finally:
        store.close()
    return content
```

- [x] **Step 2: 实现 report/cli.py**

创建 `report/cli.py`:
```python
"""简报 CLI:python -m report.cli build [--date YYYYMMDD]

生成当天简报并存 daily_report;失败记 ERROR 日志(模块名 report)。
"""

from __future__ import annotations

import argparse
import logging

import obs
from main import load_config
from report.builder import build_report
from report.llm import LLMError


def cmd_build(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    logger = obs.get_logger("report", cfg["log_dir"])
    try:
        content = build_report(cfg["db_path"], cfg, args.date or None)
        obs.log(logger, logging.INFO, "LLM", "简报已生成并存档")
        print(content)
    except LLMError as e:
        obs.log(logger, logging.ERROR, "LLM", f"简报生成失败:{e}")
        print(f"[error] 简报生成失败:{e}")


def main() -> None:
    ap = argparse.ArgumentParser(description="每日观察简报生成(自用)")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("build", help="生成当天简报")
    sp.add_argument("--date", default="", help="交易日 YYYYMMDD(默认今天)")
    sp.set_defaults(func=cmd_build)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: 手动验证(需 LLM_API_KEY + 库里有当天数据)**

```powershell
$env:LLM_API_KEY="真实key"
python -m report.cli build
```
Expected: 打印一段中文简报;`daily_report` 出现当天行。查:
```powershell
python -c "import sqlite3;c=sqlite3.connect('data/app.db');print(c.execute('SELECT report_date,length(content),pushed FROM daily_report').fetchall())"
```

- [ ] **Step 4: 提交**

```powershell
git add report/builder.py report/cli.py
git commit -m "feat(report): build orchestration + CLI"
```

---

## Phase 4 — 统一推送 notify/ + 告警

### Task 4.1: notify/notifier.py(飞书 webhook 出口)

**Files:**
- Create: `notify/__init__.py`(空)
- Create: `notify/notifier.py`
- Test: `tests/test_notifier.py`(注入假 http)

- [x] **Step 1: 写失败测试**

创建 `tests/test_notifier.py`:
```python
"""统一推送单测:注入假 http,验证飞书 text 消息体 + 级别前缀。"""
from __future__ import annotations

import unittest

from notify.notifier import send


class _FakeResp:
    status_code = 200

    def json(self):
        return {"StatusCode": 0}

    def raise_for_status(self):
        pass


class _FakeHttp:
    def __init__(self):
        self.last = None

    def post(self, url, json=None, timeout=None):
        self.last = {"url": url, "json": json}
        return _FakeResp()


class TestNotifier(unittest.TestCase):
    def test_send_builds_feishu_text(self):
        http = _FakeHttp()
        ok = send("每日简报", "内容正文", level="info",
                  webhook="https://open.feishu.cn/hook/x", http=http)
        self.assertTrue(ok)
        body = http.last["json"]
        self.assertEqual(body["msg_type"], "text")
        self.assertIn("每日简报", body["content"]["text"])
        self.assertIn("内容正文", body["content"]["text"])

    def test_alert_level_prefix(self):
        http = _FakeHttp()
        send("告警", "抓取熔断", level="error",
             webhook="https://open.feishu.cn/hook/x", http=http)
        self.assertIn("❌", http.last["json"]["content"]["text"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_notifier -v`
Expected: FAIL — 无 `notify.notifier`。

- [x] **Step 3: 实现 notify/notifier.py**

创建 `notify/__init__.py`(空)。创建 `notify/notifier.py`:
```python
"""统一推送出口:简报与告警共用的单一函数 send()。

一期渠道 = 飞书自定义机器人 webhook(国内直连;Telegram 被墙不用)。
"渠道由 Hermes 抽象、换渠道只改一处"体现在:所有推送都过这里,换钉钉/企业微信只改本文件。
若机器人开了签名校验,密钥走环境变量 FEISHU_SIGN_SECRET。
http 客户端参数注入(缺省用 requests),便于测试。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

_LEVEL_PREFIX = {"info": "📊", "warn": "⚠️", "warning": "⚠️", "error": "❌"}


def _sign(secret: str, ts: str) -> str:
    """飞书自定义机器人签名:HMAC-SHA256(timestamp+'\\n'+secret),再 base64。"""
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send(
    title: str,
    body: str,
    level: str = "info",
    webhook: str = "",
    http=None,
    timeout: int = 15,
) -> bool:
    """推一条消息到飞书。成功返回 True;失败返回 False(不抛,避免告警自身把流程带崩)。"""
    if not webhook:
        print("[warn] 未配置 notify.feishu_webhook,跳过推送")
        return False
    if http is None:
        import requests as http  # noqa: PLC0415

    prefix = _LEVEL_PREFIX.get(level, "📊")
    text = f"{prefix} {title}\n\n{body}"
    payload: dict = {"msg_type": "text", "content": {"text": text}}

    secret = os.environ.get("FEISHU_SIGN_SECRET", "")
    if secret:
        ts = str(int(time.time()))
        payload["timestamp"] = ts
        payload["sign"] = _sign(secret, ts)

    try:
        resp = http.post(webhook, json=payload, timeout=timeout)
        resp.raise_for_status()
        return True
    except Exception as e:  # 推送失败只告警到 stderr,不抛
        print(f"[warn] 飞书推送失败:{e}")
        return False
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_notifier -v`
Expected: PASS(2 tests)。

- [ ] **Step 5: 提交**

```powershell
git add notify/__init__.py notify/notifier.py tests/test_notifier.py
git commit -m "feat(notify): unified feishu webhook sink with optional signing"
```

### Task 4.2: notify/cli.py(send 简报 + alert 告警)

**Files:**
- Create: `notify/cli.py`
- Test: `tests/test_notify_cli.py`(alert 决策逻辑:有 issue 才推)

- [x] **Step 1: 写失败测试(alert 决策纯逻辑)**

创建 `tests/test_notify_cli.py`:
```python
"""告警决策单测:根据 scan_issues 结果决定是否推 + 组装告警文案(不真推)。"""
from __future__ import annotations

import unittest

from notify.cli import format_alert


class TestNotifyCli(unittest.TestCase):
    def test_no_issue_returns_none(self):
        self.assertIsNone(format_alert([]))

    def test_issues_grouped_text(self):
        issues = [
            {"module": "weibo", "kind": "paused", "step": "熔断", "message": "连续 3 次 401/403"},
            {"module": "report", "kind": "error", "step": "LLM", "message": "调用失败"},
        ]
        text = format_alert(issues)
        self.assertIsNotNone(text)
        self.assertIn("weibo", text)
        self.assertIn("熔断", text)
        self.assertIn("LLM", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_notify_cli -v`
Expected: FAIL — 无 `notify.cli`。

- [x] **Step 3: 实现 notify/cli.py**

创建 `notify/cli.py`:
```python
"""推送 CLI(供 Hermes cron 调用)。

  python -m notify.cli send  [--date YYYY-MM-DD]   推当天简报(读 daily_report)
  python -m notify.cli alert [--modules weibo,xueqiu,market,report]
                                                   扫 ERROR 行 + 熔断 flag,有则推告警

alert 用 obs.scan_issues 按级别字段判定,不靠中文正则;offset 防重复告警。
"""

from __future__ import annotations

from datetime import date

import obs
from main import load_config
from notify.notifier import send
from report.storage import ReportStorage

DEFAULT_MODULES = ["weibo", "xueqiu", "market", "report"]


def format_alert(issues: list[dict]) -> str | None:
    """把 issue 列表组成告警文案;无 issue 返回 None。纯函数,便于测试。"""
    if not issues:
        return None
    lines = []
    for i in issues:
        tag = "熔断" if i.get("kind") == "paused" else "错误"
        lines.append(f"[{tag}] {i.get('module')} / {i.get('step')}: {i.get('message')}")
    return "本轮检测到以下问题:\n" + "\n".join(lines)


def cmd_send(args) -> None:
    cfg = load_config(args.config)
    webhook = (cfg.get("notify") or {}).get("feishu_webhook", "")
    report_date = args.date or date.today().strftime("%Y-%m-%d")
    store = ReportStorage(cfg["db_path"])
    try:
        r = store.get(report_date)
        if not r:
            print(f"[warn] {report_date} 无简报,跳过推送")
            return
        ok = send("每日观察简报 " + report_date, r["content"], level="info", webhook=webhook)
        if ok:
            store.mark_pushed(report_date)
            print("简报已推送")
    finally:
        store.close()


def cmd_alert(args) -> None:
    cfg = load_config(args.config)
    webhook = (cfg.get("notify") or {}).get("feishu_webhook", "")
    modules = args.modules.split(",") if args.modules else DEFAULT_MODULES
    issues = obs.scan_issues(modules, log_dir=cfg["log_dir"], state_dir=cfg["log_dir"], flag_dir=cfg["log_dir"])
    text = format_alert(issues)
    if text is None:
        print("无告警")
        return
    send("系统告警", text, level="error", webhook=webhook)
    print(f"已推送告警,{len(issues)} 项")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="统一推送(简报 + 告警)")
    ap.add_argument("--config", default="config.json")
    sub = ap.add_subparsers(dest="command", required=True)
    sp_send = sub.add_parser("send", help="推当天简报")
    sp_send.add_argument("--date", default="", help="YYYY-MM-DD(默认今天)")
    sp_send.set_defaults(func=cmd_send)
    sp_alert = sub.add_parser("alert", help="扫日志/flag,有问题则推告警")
    sp_alert.add_argument("--modules", default="", help="逗号分隔模块名,默认全部")
    sp_alert.set_defaults(func=cmd_alert)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
```

- [x] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_notify_cli -v`
Expected: PASS(2 tests)。

- [ ] **Step 5: 手动验证真推(需 webhook)**

在 config.json 填 `notify.feishu_webhook`,库里已有当天简报后:
```powershell
python -m notify.cli send
python -m notify.cli alert
```
Expected: 飞书收到简报卡片;`alert` 若无 ERROR/flag 打印"无告警"。

- [ ] **Step 6: 提交**

```powershell
git add notify/cli.py tests/test_notify_cli.py
git commit -m "feat(notify): send report + scan-based alert CLI"
```

---

## Phase 5 — Hermes 编排 + 端到端

### Task 5.1: hermes/SKILL.md + cron.md

**Files:**
- Create: `hermes/SKILL.md`
- Create: `hermes/cron.md`

- [x] **Step 1: 写 hermes/SKILL.md**

创建 `hermes/SKILL.md`(让 Hermes agent 用 terminal 调各脚本;命令与 CLI 一致):
```markdown
---
name: daily-stock-brief
description: 每日股票观察简报一期。用 terminal 依次跑微博/雪球增量抓取、盘后行情快照、收盘后 LLM 简报、飞书推送,并在每轮结束检查告警。工作目录为本仓库根目录,先激活 venv 并设置好 LLM_API_KEY / FEISHU_SIGN_SECRET 环境变量。
---

# 每日股票观察简报(一期)

前置:
- 部署主机=国内网络 Windows;已 `python xueqiu_crawl.py login` 与 `python main.py login` 完成登录态。
- 环境变量 `LLM_API_KEY`(必需)、`FEISHU_SIGN_SECRET`(飞书开签名时需要)已设置。
- config.json 已配 bloggers / xueqiu_bloggers / notify.feishu_webhook / report.llm。

每轮标准流程(全部在仓库根目录执行):

1. 微博增量:`python main.py crawl --mode incremental`
2. 雪球增量:`python xueqiu_crawl.py crawl --mode incremental`
3. 盘后行情快照(交易日 15:30 后):`python -m market.cli snapshot`
4. 收盘后简报:`python -m report.cli build`
5. 推送简报:`python -m notify.cli send`
6. 告警检查(每轮末必做):`python -m notify.cli alert`

规则:
- 步骤 1-5 任一非零退出或打印 [error],继续往下走但**务必执行步骤 6**(alert 会扫 ERROR 日志/熔断 flag 推飞书)。
- 若 crawl 打印"已熔断",不要反复重试;等人工清除 `logs/weibo.paused` / `logs/xueqiu.paused`。
- 非交易日可跳过步骤 3。
```

- [x] **Step 2: 写 hermes/cron.md**

创建 `hermes/cron.md`(cron 时刻表 + 与 Windows 任务计划的取舍):
```markdown
# Hermes cron 时刻表(一期)

由 Hermes 内置 cron 驱动,取代 Windows 任务计划(二者择一,统一用 Hermes)。
时区:Asia/Shanghai。示例(按 Hermes cron 语法配置,具体命令见 SKILL.md):

| 任务 | 建议时刻 | 命令 |
|---|---|---|
| 微博+雪球增量 | 09:40 / 12:30 / 14:30 | main.py + xueqiu_crawl.py crawl --mode incremental |
| 盘后行情快照 | 交易日 15:35 | python -m market.cli snapshot |
| 生成简报 | 交易日 16:00 | python -m report.cli build |
| 推送简报 | 交易日 16:05 | python -m notify.cli send |
| 告警检查 | 每次任务后 | python -m notify.cli alert |

首次可用性验证(实现第一步已做):`hermes --version` / `hermes model` 能跑。
把上述 cron 项注册进 Hermes 后,用 `hermes cron list`(或等价命令)确认已登记。
```

- [ ] **Step 3: 提交**

```powershell
git add hermes/SKILL.md hermes/cron.md
git commit -m "docs(hermes): orchestration skill + cron schedule"
```

### Task 5.2: 全量单测回归

**Files:** 无(回归)。

- [x] **Step 1: 跑全部单测**

Run: `python -m unittest discover -s tests -v`
Expected: 全部 PASS(errors/obs/xueqiu_storage/xueqiu_crawler/market_storage/market_fetcher/report_storage/report_reader/report_prompt/report_llm/notifier/notify_cli)。任一失败就地修。

- [ ] **Step 2: 提交(如有修复)**

```powershell
git add -A
git commit -m "test: green full suite for phase1"
```

### Task 5.3: 熔断演练(伪造连续 401)

**Files:** 无(手动演练,验证完成判定里的"伪造连续 401 → ERROR 日志 + 熔断 flag + 告警推送")。

- [ ] **Step 1: 临时把阈值调低并伪造登录失效**

临时改 config.json:`"auth_fail_breaker": 1`;把 `.wb_profile/` 临时改名模拟登录态失效(或断网),然后:
```powershell
python main.py crawl --mode incremental
```
Expected: 预检或抓取抛 AuthError → `logs/weibo-<日期>.log` 出现 `ERROR ... 熔断` 行;`logs/weibo.paused` 生成。

- [ ] **Step 2: 验证告警推送**

```powershell
python -m notify.cli alert
```
Expected: 飞书收到"系统告警",含 weibo / 熔断 / 原因。再跑一次 `alert` 应因 offset 不重复推 error(paused flag 仍会报,直到清除)。

- [ ] **Step 3: 复原**

```powershell
python -c "import obs; obs.clear_paused('weibo','logs')"
```
恢复 `.wb_profile/` 名字、把 `auth_fail_breaker` 改回 3。删除本次演练产生的 `logs/*.paused`。

- [ ] **Step 4: 记录演练结论(可选)**

用 memory 工具在 `/memories/repo/` 记一条"熔断演练已通过 + 实际接口字段实况",供后续期复用。

### Task 5.4: 端到端无人值守试跑(一天)

**Files:** 无(验收,对齐设计文档第 9 节完成判定)。

- [ ] **Step 1: 按 SKILL.md 手动串一遍**

依次执行 SKILL.md 的 6 步(用真配置 + 真数据),确认:微博/雪球增量入库去重、行情快照近似字段标注正确、简报出中文并存 daily_report、notify 推飞书成功。

- [ ] **Step 2: 注册 Hermes cron 并观察一天**

按 cron.md 把任务注册进 Hermes cron,放置无人值守跑完一个交易日。次日检查:`daily_report` 有当天记录且 `pushed=1`;`logs/` 各模块有 INFO 轨迹;无非预期 ERROR。

- [ ] **Step 3: 验收对照**

对照设计文档第 9 节逐条打勾:微博✅雪球✅增量去重断点、行情✅近似标注、report✅存档、notify✅飞书、伪造 401✅熔断告警(Task 5.3)、Hermes cron✅无人值守。全绿即一期完成。

---

## 自检结论(计划作者自查)

- **设计覆盖**:设计第 3 节 7 个待建项 → 雪球抓取(Phase 1)、market 行情(Phase 2)、存储扩展(1.1/2.1/3.1 分别加 xq_*/market_*/daily_report 表,均在 data/app.db)、report 简报(Phase 3)、notify 推送(Phase 4)、监控/熔断(Task 0.4 obs + 0.5/0.6 crawler/main + 雪球 1.4 + alert 4.2)、Hermes 编排(Phase 5)。逐项有任务。第 5 节存储四类新表全覆盖。第 6 节结构化日志/熔断/按级别告警全覆盖。第 9 节完成判定由 Task 5.3/5.4 逐条验收。
- **占位符扫描**:无 TODO/"稍后实现";所有代码步骤含完整代码。唯一需上线核对的是 akshare/雪球实际列名(设计本身标注接口易变),已在边界做列缺失检查 + "对字段"指引,不是占位符。
- **类型一致性**:`obs.get_logger/log/is_paused/set_paused/paused_reason/clear_paused/scan_issues` 跨 main/xueqiu_crawl/market.cli/report.cli/notify.cli 调用签名一致;`AuthError` 继承 `RuntimeError` 且 `except AuthError` 均在 `except RuntimeError` 前;`report.reader.collect(db_path, lookback_hours, trade_date)` 与 builder 调用一致;`report.llm.chat(...)` 与 builder/测试一致;`notify.send(title, body, level, webhook, http)` 与 cli/测试一致;存储各类的 `count_*`/`get_*`/`upsert_*` 与测试一致。
- **已修正**:report/llm.py 首行误写的占位 import 已在 Step 3 明确要求删除(只留 `from __future__ import annotations`)。

---

## 执行交接

计划已保存到 [docs/superpowers/plans/2026-07-16-daily-stock-brief-phase1.md](docs/superpowers/plans/2026-07-16-daily-stock-brief-phase1.md)。两种执行方式:

1. **Subagent 驱动(推荐)** — 每个任务派一个全新 subagent 实现,任务间我来 review,迭代快。用 `subagent-driven-development` agent。
2. **内联执行** — 在本会话按批次执行,带检查点 review。用 `executing-plans` agent。

选哪种?
