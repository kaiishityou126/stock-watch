# 雪球模块改造("抓博主"→"抓注意力")实现计划

> **给执行者(agentic worker):** 请用 `subagent-driven-development` agent(推荐)或 `executing-plans` agent 逐任务实现本计划。每步用 checkbox(`- [ ]`)跟踪。
>
> **本项目当前未初始化 git**,所有“提交 / checkpoint”步骤均为**可选**;不做 git 时以“跑通全量测试”作为该任务完成的标志即可。

**目标:** 把雪球模块从"按 user_id 抓指定博主时间线 + 评论"改造为"抓全站注意力信号"——**热股榜(数字硬指标,每日快照时间序列)+ 7x24 快讯(短文本佐料)**,并接入每日简报让 LLM 输出"注意力向哪些板块流动 + 催化剂 + 反指风险"。

**架构:** 复用现有 `XueqiuClient` 的 session/重试退避/401·403·429 分类/cookie 刷新骨架,新增两个取数方法 + 两张 SQLite 表(`xq_hot_rank` / `xq_livenews`);CLI `crawl` 改为一次跑热股榜 upsert + 7x24 增量;`report` 读两日热榜算环比 + 当日快讯,`prompt` 加"雪球注意力"段。旧 `user_timeline` 按博主抓取代码**保留不删、不接入**。

**技术栈:** Python 3.10+、requests、Playwright(登录态)、SQLite(`data/app.db`)、unittest。约定:中文注释/docstring、`from __future__ import annotations` + 内置泛型、统一 CLI `python -m <包> <动词>`、无兼容 shim、不擅自加错误兜底。

**唯一依据:** [docs/superpowers/specs/2026-07-17-xueqiu-attention-redesign-design.md](../specs/2026-07-17-xueqiu-attention-redesign-design.md)(第 3–9 节)。

**全局验证命令(每个含测试的任务末尾都要跑):**
```powershell
python -m unittest discover -s tests
```

---

## 文件结构(改动清单)

| 文件 | 动作 | 职责 |
|---|---|---|
| `xueqiu/crawler.py` | 修改 | 加端点常量 `HOT_RANK_URL`/`LIVENEWS_URL`、市场映射 `_HOT_MARKET_TYPE`、方法 `fetch_hot_rank`/`fetch_livenews`、纯函数 `slim_hot_rank`/`slim_livenews`、小工具 `_to_float`。旧 `fetch_timeline`/`fetch_full_text`/`fetch_comments`/`slim_xq_post`/`select_xq_comments` **不动**。 |
| `xueqiu/storage.py` | 修改 | `SCHEMA` 追加 `xq_hot_rank`/`xq_livenews` 两表 + 索引;加方法 `upsert_hot_rank`/`insert_livenews`/`livenews_exists`/`count_hot_rank`/`count_livenews`。旧表与方法不动。 |
| `xueqiu/cli.py` | 修改 | 重写 `cmd_crawl`:热股榜 upsert + 7x24 增量翻页 + 熔断。旧 `crawl_one`/`_uids` 保留(不再被调用)。 |
| `report/reader.py` | 修改 | `collect()` 增读 `xq_hot_rank`(今日+前一有数据日)+ `xq_livenews`(当日窗口);沿用表缺失容错。 |
| `report/prompt.py` | 修改 | 加纯函数 `compute_hot_rank_changes`、格式化 `_fmt_xq_attention`;`build_prompt` 插入"雪球注意力"段;`SYSTEM` 补交叉验证/免责措辞。 |
| `weibo/cli.py` | 修改 | `DEFAULTS` 追加 `xq_hot_rank_size`/`xq_hot_rank_market`/`xq_livenews_max_pages`(共享 config)。 |
| `config.example.json` | 修改 | 新增三个 `xq_*` 键;旧博主抓取键保留并注明"一期停用"。 |
| `README.md` | 修改 | 更新雪球配置表 + `crawl` 语义说明。 |
| `tests/test_xueqiu_crawler.py` | 修改 | 加 `slim_hot_rank`/`slim_livenews` 及 `fetch_*` 解析(monkeypatch `_get_json`)测试。 |
| `tests/test_xueqiu_storage.py` | 修改 | 加热榜当天 upsert 幂等/去重、快讯 id 去重、`livenews_exists` 测试。 |
| `tests/test_report_reader.py` | 修改 | seed 两日热榜 + 快讯,验证 collect 返回;补 hot_rank/livenews 表缺失容错。 |
| `tests/test_report_prompt.py` | 修改 | `compute_hot_rank_changes`(新上榜/跃升/退潮)+ `build_prompt` 含"雪球注意力"段。 |

**关键设计约束(贯穿全计划):**
- `slim_*` 用防御式 `.get()` 取字段;真实字段名以 **Task 0 联网实测**为准,实测后回来校对键名。
- 热榜 `rank` 由**列表下标 +1** 得到(接口按热度倒序返回)。
- `xq_hot_rank` 用 `(date, symbol)` 主键 → 当天重复跑天然幂等,无需增量水位。
- `xq_livenews` 用 `id` 主键 `INSERT OR IGNORE` 去重;CLI 翻页时遇已入库 id 停。
- reader 不感知交易日历:热榜"前一日"取库里 `date <= trade_date` 的**倒数第二个有数据日期**。

---

## Task 0:联网实测热股榜 + 7x24 端点(**阻塞项 — 必须最先做**)

> ⚠️ 公司网防火墙拦雪球,**此步必须在能联网的环境**(家里 PC / 云服务器)完成。热股榜是整套方案地基,端点失效则全盘改走退路。**在字段名未实测确认前,不要认为 Task 2 里 slim 函数的键名是最终版。**

**文件:**
- 临时验证脚本(用完即删,不进仓库):`scripts/_probe_xq.py`(或直接 REPL)
- 产出:把实测到的真实 JSON 字段名记到本任务下方的“实测结论”里,供 Task 2 校对

- [ ] **Step 1: 登录取新鲜 cookie**

```powershell
python -m xueqiu login
```
浏览器扫码 / 账号密码登录成功后关闭,登录态落到 `.xq_profile/`。

- [ ] **Step 2: 写一次性探测脚本命中两个端点并打印结构**

创建 `scripts/_probe_xq.py`:

```python
"""一次性端点探测(用完即删,勿提交)。打印热股榜/7x24 真实 JSON 结构。"""
from __future__ import annotations

import json

from xueqiu import login as login_mod
from xueqiu.crawler import XueqiuClient, HOT_RANK_URL, LIVENEWS_URL, HOST

cookie = login_mod.get_cookie_header(".xq_profile")
client = XueqiuClient(cookie, 1.5, 3.5, refresh_cookie=lambda: login_mod.get_cookie_header(".xq_profile"))

print("===== 热股榜 =====")
hot = client._get_json(HOT_RANK_URL, {"size": 10, "_type": 10, "type": 10}, referer=f"{HOST}/")
print(json.dumps(hot, ensure_ascii=False, indent=2)[:3000])

print("===== 7x24 快讯 =====")
news = client._get_json(LIVENEWS_URL, {"since_id": -1, "max_id": -1, "count": 5}, referer=f"{HOST}/")
print(json.dumps(news, ensure_ascii=False, indent=2)[:3000])
```

> 说明:`HOT_RANK_URL`/`LIVENEWS_URL` 常量在 Task 2 Step 1 才加入 `xueqiu/crawler.py`。若想在 Task 2 之前先探,把两个 URL 直接内联进脚本即可:
> `HOT_RANK_URL = "https://stock.xueqiu.com/v5/stock/hot_stock/list.json"`、`LIVENEWS_URL = "https://xueqiu.com/statuses/livenews/list.json"`、`HOST = "https://xueqiu.com"`。

- [ ] **Step 3: 运行并记录真实字段**

```powershell
python scripts/_probe_xq.py
```

对照记录以下关键点(填进下方“实测结论”):
1. 热股榜列表在哪个键下?(预期 `data.items`,可能是 `data.list` / 顶层 `items`)
2. 每条的**代码字段**名(预期 `code` 如 `SH600000`;也可能 `symbol`)、**名称字段**(预期 `name`)、**热度值字段**(预期 `value`;也可能 `hot`/`heat`)。
3. `type`/`_type` 的沪深/港/美取值(预期沪深=10,港/美待确认)。
4. 7x24 列表键(预期 `items`)、每条 `id`、时间字段(预期 `created_at` 毫秒)、正文字段(预期 `text` 或 `description`,含 HTML)、翻页游标(预期 `next_max_id` 或 `next_id`)。

- [ ] **Step 4: 判定端点可用性 + 选路**

- **可用** → 继续 Task 1;在 Task 2 的 slim 函数里把键名校对成实测值。
- **热股榜失效**(404 / 结构大变 / 需强登录)→ **切退路**:改用 `adata` 东财人气榜 / 同花顺热度榜(现成库)。退路方案另起一份设计再实现;本计划 Task 2–7 的 `xq_hot_rank` 表结构与 reader/prompt 接入**保持不变**,只把 crawler 的 `fetch_hot_rank` 换成 adata 封装(仍产出 `slim_hot_rank` 的行结构)。
- **7x24 失效** → 一期可先只上热股榜(硬指标已是主力信号),快讯段留空由 prompt 容错(见 Task 5)。

- [ ] **Step 5: 删除探测脚本**

```powershell
Remove-Item scripts/_probe_xq.py
```

**实测结论(2026-07-17 已联网实测,公司网即可访问雪球):**
```
Bootstrap:GET https://xueqiu.com 只发 acw_tc(无 token);须再 GET https://xueqiu.com/hq
         (行情页)才种匿名 token(xq_a_token/xq_id_token/xq_r_token/xqat/u/cookiesu)。
         公开榜单/快讯无需登录!缺 token 端点返回 HTTP 400,error_code=400016。

热股榜列表键:data.items   代码字段:code(=symbol,如 SZ300308)   名称字段:name
热度值字段:value(float)   另有 increment(热度增量)/rank_change(排名变化)——注意力异动核心,建议入库
涨跌:percent/current/chg   沪深/港/美 type:10/(港美待测,沿用映射)

7x24 列表键:items   id:int   时间字段:created_at(毫秒 epoch)   正文字段:text(纯文本,无 HTML)
翻页游标键:next_max_id(把它当下一页 max_id)   另有 next_id/status_id/target

端点可用性:热股榜 [可用]   7x24 [可用]   选路:[原端点]
```

---

## Task 1:storage 新增两表 + 读写方法

**文件:**
- Modify: `xueqiu/storage.py`(`SCHEMA` 追加两表;新增 5 个方法)
- Test: `tests/test_xueqiu_storage.py`

- [ ] **Step 1: 写失败测试(热榜当天 upsert 幂等 + 快讯 id 去重 + livenews_exists)**

在 `tests/test_xueqiu_storage.py` 末尾(`if __name__` 之前)追加:

```python
def _hot(date: str, symbol: str, rank: int, hot: float = 1.0) -> dict:
    return {
        "date": date, "symbol": symbol, "name": symbol + "名",
        "rank": rank, "hot_value": hot, "market_type": "沪深", "fetched_at": "now",
    }


def _news(nid: str, ts: int) -> dict:
    return {
        "id": nid, "created_ts": ts, "created_at": "", "text": "利好消息",
        "raw_json": "{}", "fetched_at": "now",
    }


class TestXueqiuAttentionStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "app.db")
        self.store = XueqiuStorage(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_hot_rank_same_day_idempotent(self):
        # 同 (date,symbol) 重复跑:只更新不新增,rank 取最新值
        self.store.upsert_hot_rank([_hot("20260717", "SH600000", 5)])
        self.store.upsert_hot_rank([_hot("20260717", "SH600000", 2)])
        self.assertEqual(self.store.count_hot_rank(), 1)
        row = self.store.conn.execute(
            "SELECT rank FROM xq_hot_rank WHERE date=? AND symbol=?", ("20260717", "SH600000")
        ).fetchone()
        self.assertEqual(row["rank"], 2)

    def test_hot_rank_multi_day_kept(self):
        # 不同日期同一只票 → 两行(时间序列)
        self.store.upsert_hot_rank([_hot("20260716", "SH600000", 3)])
        self.store.upsert_hot_rank([_hot("20260717", "SH600000", 1)])
        self.assertEqual(self.store.count_hot_rank(), 2)

    def test_livenews_id_dedup(self):
        self.store.insert_livenews([_news("n1", 100), _news("n1", 100)])
        self.assertEqual(self.store.count_livenews(), 1)

    def test_livenews_exists(self):
        self.assertFalse(self.store.livenews_exists("n1"))
        self.store.insert_livenews([_news("n1", 100)])
        self.assertTrue(self.store.livenews_exists("n1"))
```

- [ ] **Step 2: 运行确认失败**

```powershell
python -m unittest tests.test_xueqiu_storage -v
```
预期:`AttributeError: 'XueqiuStorage' object has no attribute 'upsert_hot_rank'`(或 `count_hot_rank` 等)。

- [ ] **Step 3: 在 `SCHEMA` 末尾(闭合的 `"""` 之前)追加两表**

打开 `xueqiu/storage.py`,在 `xq_crawl_state` 表定义之后、`SCHEMA` 字符串结束的 `"""` 之前插入:

```sql

CREATE TABLE IF NOT EXISTS xq_hot_rank (
    date        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    name        TEXT,
    rank        INTEGER,
    hot_value   REAL,
    market_type TEXT,
    fetched_at  TEXT,
    PRIMARY KEY(date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_xq_hot_rank_symbol_date ON xq_hot_rank(symbol, date);

CREATE TABLE IF NOT EXISTS xq_livenews (
    id         TEXT PRIMARY KEY,
    created_ts INTEGER,
    created_at TEXT,
    text       TEXT,
    raw_json   TEXT,
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_xq_livenews_ts ON xq_livenews(created_ts DESC);
```

- [ ] **Step 4: 在 `count_comments` 方法之后追加 5 个方法**

在 `xueqiu/storage.py` 类的最后(`count_comments` 之后)追加:

```python
    # ---- 热股榜(每日快照,(date,symbol) 幂等)----
    def upsert_hot_rank(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO xq_hot_rank (date, symbol, name, rank, hot_value, market_type, fetched_at) "
                "VALUES (:date,:symbol,:name,:rank,:hot_value,:market_type,:fetched_at) "
                "ON CONFLICT(date, symbol) DO UPDATE SET name=excluded.name, rank=excluded.rank, "
                "hot_value=excluded.hot_value, market_type=excluded.market_type, "
                "fetched_at=excluded.fetched_at",
                rows,
            )

    def count_hot_rank(self) -> int:
        return self.conn.execute("SELECT COUNT(*) n FROM xq_hot_rank").fetchone()["n"]

    # ---- 7x24 快讯(id 去重)----
    def insert_livenews(self, rows: list[dict]) -> None:
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT OR IGNORE INTO xq_livenews (id, created_ts, created_at, text, raw_json, fetched_at) "
                "VALUES (:id,:created_ts,:created_at,:text,:raw_json,:fetched_at)",
                rows,
            )

    def livenews_exists(self, news_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM xq_livenews WHERE id = ?", (news_id,)
        ).fetchone() is not None

    def count_livenews(self) -> int:
        return self.conn.execute("SELECT COUNT(*) n FROM xq_livenews").fetchone()["n"]
```

- [ ] **Step 5: 运行确认通过**

```powershell
python -m unittest tests.test_xueqiu_storage -v
```
预期:全部 PASS。

- [ ] **Step 6:(可选)git checkpoint**

```powershell
# 项目未初始化 git 时跳过此步
git add xueqiu/storage.py tests/test_xueqiu_storage.py
git commit -m "feat(xueqiu): add xq_hot_rank/xq_livenews tables and accessors"
```

---

## Task 2:crawler 新增 slim 纯函数 + fetch 方法

**文件:**
- Modify: `xueqiu/crawler.py`(加常量、`_to_float`、`slim_hot_rank`、`slim_livenews`、`XueqiuClient.fetch_hot_rank`、`XueqiuClient.fetch_livenews`)
- Test: `tests/test_xueqiu_crawler.py`

> 前置:Task 0 已实测字段。若实测键名与下方代码不一致,以实测为准修改 `.get()` 里的键名与 `_HOT_MARKET_TYPE` 数值。

- [ ] **Step 1: 写失败测试(slim 裁剪 + fetch 解析,monkeypatch 掉网络)**

在 `tests/test_xueqiu_crawler.py` 顶部导入行改为:

```python
from xueqiu.crawler import (
    XueqiuClient,
    slim_hot_rank,
    slim_livenews,
    slim_xq_post,
    select_xq_comments,
    xq_ms_to_epoch,
)
```

在 `if __name__` 之前追加:

```python
class TestSlimHotRank(unittest.TestCase):
    def test_rank_from_index_and_fields(self):
        items = [
            {"code": "SH600000", "name": "浦发银行", "value": 12345.0},
            {"code": "SZ000001", "name": "平安银行", "value": 6789.0},
        ]
        rows = slim_hot_rank(items, "沪深", "20260717")
        self.assertEqual(rows[0]["symbol"], "SH600000")
        self.assertEqual(rows[0]["rank"], 1)          # 下标 +1
        self.assertEqual(rows[1]["rank"], 2)
        self.assertEqual(rows[0]["hot_value"], 12345.0)
        self.assertEqual(rows[0]["market_type"], "沪深")
        self.assertEqual(rows[0]["date"], "20260717")

    def test_skip_items_without_code(self):
        rows = slim_hot_rank([{"name": "无代码"}], "沪深", "20260717")
        self.assertEqual(rows, [])


class TestSlimLivenews(unittest.TestCase):
    def test_html_stripped_and_ts(self):
        it = {"id": 77, "created_at": 1700000000000, "text": "<p>央行降准 <a>网页链接</a></p>"}
        row = slim_livenews(it)
        self.assertEqual(row["id"], "77")
        self.assertEqual(row["created_ts"], 1700000000)
        self.assertIn("央行降准", row["text"])
        self.assertNotIn("<p>", row["text"])

    def test_uses_description_when_no_text(self):
        row = slim_livenews({"id": 1, "created_at": 1700000000000, "description": "<b>公告</b>"})
        self.assertIn("公告", row["text"])
        self.assertNotIn("<b>", row["text"])


class _StubClient(XueqiuClient):
    """构造真实 client(不发网络),把 _get_json 换成返回预置 JSON。"""
    def __init__(self, canned: dict):
        super().__init__("k=v", 0.0, 0.0)
        self._canned = canned

    def _get_json(self, url, params, referer=None):
        return self._canned


class TestFetchParsing(unittest.TestCase):
    def test_fetch_hot_rank_extracts_items(self):
        client = _StubClient({"data": {"items": [{"code": "SH600000", "name": "浦发", "value": 1.0}]}})
        items = client.fetch_hot_rank(size=10, market_type="沪深")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "SH600000")

    def test_fetch_livenews_returns_rows_and_cursor(self):
        client = _StubClient({
            "items": [{"id": 5, "created_at": 1700000000000, "text": "<p>快讯</p>"}],
            "next_max_id": 4,
        })
        rows, next_max_id = client.fetch_livenews(max_id=-1, count=15)
        self.assertEqual(rows[0]["id"], "5")
        self.assertNotIn("<p>", rows[0]["text"])
        self.assertEqual(next_max_id, 4)
```

- [ ] **Step 2: 运行确认失败**

```powershell
python -m unittest tests.test_xueqiu_crawler -v
```
预期:`ImportError: cannot import name 'slim_hot_rank'`。

- [ ] **Step 3: 加端点常量 + 市场映射(在现有 `COMMENTS_URL` 常量之后)**

在 `xueqiu/crawler.py` 的 `COMMENTS_URL = HOST + "/statuses/v3/comments.json"` 之后追加:

```python
# ---- 注意力信号端点(一期新增;字段以联网实测为准)----
HOT_RANK_URL = "https://stock.xueqiu.com/v5/stock/hot_stock/list.json"
LIVENEWS_URL = HOST + "/statuses/livenews/list.json"
# 市场标签 → 接口 type 数值(⚠️ 港/美取值待 Task 0 实测确认)
_HOT_MARKET_TYPE = {"沪深": 10, "港": 12, "美": 11}
```

- [ ] **Step 4: 加 `_to_float` 小工具(在 `_xq_pics` 函数之后)**

```python
def _to_float(v) -> float | None:
    """热度值可能是数字/字符串/缺失;转不了返回 None。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
```

- [ ] **Step 5: 加两个 fetch 方法(在 `XueqiuClient.fetch_comments` 之后)**

```python
    # ---- 热股榜(每日快照)----
    def fetch_hot_rank(self, size: int, market_type: str) -> list[dict]:
        """拉热股榜,返回原始 item 列表(按热度倒序)。"""
        t = _HOT_MARKET_TYPE.get(market_type, 10)
        data = self._get_json(HOT_RANK_URL, {"size": size, "_type": t, "type": t}, referer=f"{HOST}/")
        d = data.get("data") or {}
        items = d.get("items") or d.get("list") or data.get("items") or []
        return items if isinstance(items, list) else []

    # ---- 7x24 快讯(max_id 游标翻页)----
    def fetch_livenews(self, max_id: int, count: int) -> tuple[list[dict], int]:
        """拉一页快讯,返回 (slim 行列表, 下一页 max_id)。下一页游标为 0 表示无更多。"""
        data = self._get_json(
            LIVENEWS_URL, {"since_id": -1, "max_id": max_id, "count": count}, referer=f"{HOST}/"
        )
        items = data.get("items") or data.get("list") or []
        rows = [slim_livenews(it) for it in items if isinstance(it, dict)]
        next_max_id = int(data.get("next_max_id") or data.get("next_id") or 0)
        return [r for r in rows if r["id"]], next_max_id
```

- [ ] **Step 6: 加两个 slim 纯函数(在 `slim_xq_post` 之后)**

```python
def slim_hot_rank(items: list[dict], market_type: str, date_str: str) -> list[dict]:
    """热股榜 item 列表 → xq_hot_rank 行;rank 由列表下标 +1(接口按热度倒序)。"""
    ts = now_iso()
    rows: list[dict] = []
    for i, it in enumerate(items):
        code = str(it.get("code") or it.get("symbol") or "").strip()
        if not code:
            continue
        rows.append(
            {
                "date": date_str,
                "symbol": code,
                "name": it.get("name") or "",
                "rank": i + 1,
                "hot_value": _to_float(it.get("value", it.get("hot_value"))),
                "market_type": market_type,
                "fetched_at": ts,
            }
        )
    return rows


def slim_livenews(it: dict) -> dict:
    """7x24 快讯 item → xq_livenews 行(HTML 裁净)。"""
    raw = it.get("text") or it.get("description") or ""
    return {
        "id": str(it.get("id", "")),
        "created_ts": xq_ms_to_epoch(it.get("created_at")),
        "created_at": xq_ms_to_iso(it.get("created_at")),
        "text": html_to_text(raw),
        "raw_json": json.dumps(it, ensure_ascii=False),
        "fetched_at": ts_now(),
    }
```

> 注意:上面 `slim_livenews` 用 `ts_now()` 是笔误占位——实际请用现有的 `now_iso()`。把 `"fetched_at": ts_now(),` 改成 `"fetched_at": now_iso(),`。(此提示确保你不要引入不存在的函数。)

- [ ] **Step 7: 运行确认通过**

```powershell
python -m unittest tests.test_xueqiu_crawler -v
```
预期:全部 PASS。若 `_StubClient` 构造时报网络错,确认 `XueqiuClient.__init__` 只建 session(不发请求),`min_delay/max_delay=0.0` 使重试 sleep 为 0——本测试不会触发 `_get_json` 真实逻辑(已被覆盖)。

- [ ] **Step 8:(可选)git checkpoint**

```powershell
git add xueqiu/crawler.py tests/test_xueqiu_crawler.py
git commit -m "feat(xueqiu): add hot_rank/livenews fetch + slim functions"
```

---

## Task 3:CLI `crawl` 改为跑热股榜 + 7x24

**文件:**
- Modify: `xueqiu/cli.py`(重写 `cmd_crawl`;更新模块 docstring 与 import;旧 `crawl_one`/`_uids` 保留不删)

> 无独立 CLI 单测(与现状一致:项目无 `test_xueqiu_cli`)。验证靠“模块可导入 + `--help` 正常 + 全量测试不回归”,联网冒烟为可选。

- [ ] **Step 1: 更新 import(顶部)**

把 `xueqiu/cli.py` 顶部的 crawler import 改为(新增 `slim_hot_rank`,`slim_livenews`,`fetch_*` 经 client 调用不需导入):

```python
from .crawler import (
    XueqiuClient,
    now_iso,
    select_xq_comments,
    slim_hot_rank,
    slim_xq_post,
)
```

并在文件顶部 import 区确保有 `from datetime import datetime`(计算当天日期用):

```python
from datetime import datetime
```

- [ ] **Step 2: 更新模块 docstring(反映新语义)**

把文件顶部 docstring 里 `crawl` 两行说明替换为:

```
命令:
  python -m xueqiu login                    登录(首次 / 失效时)
  python -m xueqiu crawl                     抓当天热股榜(快照 upsert)+ 7x24 快讯(增量)
                                             --mode 参数保留仅为 CLI 契约一致;对热榜无意义,
                                             增量语义仅作用于 7x24。

一期已从“按博主抓时间线+评论”改为“抓全站注意力信号”。旧 user_timeline 相关代码
(crawl_one / _uids)保留但不再接入每日流程(未来三期大V策略提炼可能复用)。
```

- [ ] **Step 3: 重写 `cmd_crawl`(整段替换现有 `cmd_crawl`)**

把现有 `def cmd_crawl(args): ...` 整个函数体替换为:

```python
def _crawl_hot_rank(client: XueqiuClient, store: XueqiuStorage, cfg: dict) -> int:
    """抓当天热股榜并 upsert,返回入库行数。"""
    date_str = datetime.now().strftime("%Y%m%d")
    items = client.fetch_hot_rank(cfg["xq_hot_rank_size"], cfg["xq_hot_rank_market"])
    rows = slim_hot_rank(items, cfg["xq_hot_rank_market"], date_str)
    store.upsert_hot_rank(rows)
    return len(rows)


def _crawl_livenews(client: XueqiuClient, store: XueqiuStorage, cfg: dict) -> int:
    """按 max_id 翻页抓 7x24,遇已入库 id 或达页数上限即停,返回新增条数。"""
    max_pages = int(cfg["xq_livenews_max_pages"])
    max_id = -1
    total_new = 0
    for _ in range(max_pages):
        rows, next_max_id = client.fetch_livenews(max_id, count=15)
        if not rows:
            break
        fresh: list[dict] = []
        hit_seen = False
        for r in rows:
            if store.livenews_exists(r["id"]):
                hit_seen = True
                break
            fresh.append(r)
        store.insert_livenews(fresh)
        total_new += len(fresh)
        if hit_seen or not next_max_id:
            break
        max_id = next_max_id
    return total_new


def cmd_crawl(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    logger = obs.get_logger("xueqiu", cfg["log_dir"])

    if obs.is_paused("xueqiu", cfg["log_dir"]):
        reason = obs.paused_reason("xueqiu", cfg["log_dir"])
        obs.log(logger, logging.WARNING, "熔断", f"检测到熔断 flag({reason}),本轮跳过")
        raise SystemExit(f"雪球抓取已熔断:{reason}。处理后删除 logs/xueqiu.paused 再跑")

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
    hot_n = news_n = 0

    for label, fn in (("热股榜", _crawl_hot_rank), ("7x24 快讯", _crawl_livenews)):
        try:
            n = fn(client, store, cfg)
            consecutive_auth_fail = 0
            if label == "热股榜":
                hot_n = n
            else:
                news_n = n
            obs.log(logger, logging.INFO, "抓取", f"{label} 入库 {n} 条")
            print(f"{label}:入库 {n} 条")
        except AuthError as e:
            consecutive_auth_fail += 1
            obs.log(logger, logging.ERROR, "抓取", f"{label} 登录失效({consecutive_auth_fail}/{threshold}):{e}")
            print(f"[error] {label} 登录失效:{e}", file=sys.stderr)
            if consecutive_auth_fail >= threshold:
                obs.set_paused("xueqiu", f"连续 {consecutive_auth_fail} 次 401/403", cfg["log_dir"])
                obs.log(logger, logging.ERROR, "熔断", f"连续 {consecutive_auth_fail} 次登录失效,已熔断停止本轮")
                print("[error] 连续登录失效超阈值,已熔断,停止本轮抓取", file=sys.stderr)
                break
        except RuntimeError as e:
            obs.log(logger, logging.ERROR, "抓取", f"{label} 抓取中断:{e}")
            print(f"[error] {label} 抓取中断:{e}", file=sys.stderr)

    hc, lc = store.count_hot_rank(), store.count_livenews()
    store.close()
    print(
        f"\n完成:本次热股榜 {hot_n} 条、7x24 新增 {news_n} 条;"
        f"库内累计热榜行 {hc}、快讯 {lc} → {cfg['db_path']}"
    )
```

> 说明:`crawl_one` / `_uids` 保留在文件里不删除,但 `cmd_crawl` 不再调用它们(设计要求保留旧代码)。它们仍引用 `cfg["xq_max_pages"]` 等键——这些键仍在 `DEFAULTS` 里(Task 6 不删),故导入与静态引用不会报错。

- [ ] **Step 4: 验证模块可导入 + `--help` 正常**

```powershell
python -c "import xueqiu.cli"
python -m xueqiu crawl --help
```
预期:无报错;`--help` 显示 `--mode {full,incremental}` 与 `--config`。

- [ ] **Step 5: 全量测试不回归**

```powershell
python -m unittest discover -s tests
```
预期:全绿(CLI 改动不被单测直接覆盖,主要确认没破坏 import 链)。

- [ ] **Step 6:(可选,需联网)真实冒烟**

```powershell
python -m xueqiu crawl
```
预期:打印“热股榜:入库 N 条 / 7x24:入库 M 条”,`data/app.db` 出现两表数据。失效则回 Task 0 选路。

- [ ] **Step 7:(可选)git checkpoint**

```powershell
git add xueqiu/cli.py
git commit -m "feat(xueqiu): crawl fetches hot_rank + livenews instead of blogger timelines"
```

---

## Task 4:report `reader.collect()` 读两日热榜 + 当日快讯

**文件:**
- Modify: `report/reader.py`(`collect()` 增查询 + 返回新键)
- Test: `tests/test_report_reader.py`

- [ ] **Step 1: 写失败测试(seed 两日热榜 + 快讯,验证 collect 返回)**

在 `tests/test_report_reader.py` 的 `setUp` 里,`executescript` 之后、`c.commit()` 之前追加建表 + seed:

```python
        c.executescript(
            "CREATE TABLE xq_hot_rank(date TEXT, symbol TEXT, name TEXT, rank INTEGER, "
            "hot_value REAL, market_type TEXT, fetched_at TEXT, PRIMARY KEY(date,symbol));"
            "CREATE TABLE xq_livenews(id TEXT PRIMARY KEY, created_ts INTEGER, created_at TEXT, "
            "text TEXT, raw_json TEXT, fetched_at TEXT);"
        )
        # 两日热榜:20260716(前一日) + 20260717(今日,= trade_date)
        c.execute("INSERT INTO xq_hot_rank VALUES('20260716','SH600000','浦发',3,10.0,'沪深','now')")
        c.execute("INSERT INTO xq_hot_rank VALUES('20260717','SH600000','浦发',1,20.0,'沪深','now')")
        c.execute("INSERT INTO xq_hot_rank VALUES('20260717','SZ300750','宁德',2,15.0,'沪深','now')")
        c.execute("INSERT INTO xq_livenews VALUES('n1',?, '', '宁德时代扩产', '{}', 'now')", (now,))
        c.execute("INSERT INTO xq_livenews VALUES('n2',?, '', '三天前旧闻', '{}', 'now')", (old,))
```

在 `TestReportReader` 里新增测试方法:

```python
    def test_collect_reads_hot_rank_two_days_and_news(self):
        data = collect(self.db, lookback_hours=24, trade_date="20260717")
        today = {r["symbol"]: r["rank"] for r in data["xq_hot_rank_today"]}
        prev = {r["symbol"]: r["rank"] for r in data["xq_hot_rank_prev"]}
        self.assertEqual(today["SH600000"], 1)
        self.assertEqual(today["SZ300750"], 2)
        self.assertEqual(prev["SH600000"], 3)          # 前一日
        self.assertNotIn("SZ300750", prev)             # 前一日无宁德
        news_texts = [n["text"] for n in data["xq_livenews"]]
        self.assertIn("宁德时代扩产", news_texts)
        self.assertNotIn("三天前旧闻", news_texts)      # 超 lookback
```

并在 `test_collect_tolerates_missing_tables` 末尾追加两断言:

```python
        self.assertEqual(data["xq_hot_rank_today"], [])
        self.assertEqual(data["xq_livenews"], [])
```

- [ ] **Step 2: 运行确认失败**

```powershell
python -m unittest tests.test_report_reader -v
```
预期:`KeyError: 'xq_hot_rank_today'`。

- [ ] **Step 3: 修改 `collect()`(在现有 `dragon = _rows(...)` 之后、`finally` 之前加查询;`return` 加三个键)**

在 `report/reader.py` 的 `dragon = _rows(...)` 之后追加:

```python
        # 热股榜:取库里 date<=trade_date 的倒数两个有数据日期(今日 + 前一有数据日),供算环比
        hot_dates = _rows(
            conn,
            "SELECT DISTINCT date FROM xq_hot_rank WHERE date <= ? ORDER BY date DESC LIMIT 2",
            (trade_date,),
        )
        dates = [r["date"] for r in hot_dates]
        hot_sql = (
            "SELECT symbol, name, rank, hot_value, market_type FROM xq_hot_rank "
            "WHERE date = ? ORDER BY rank"
        )
        hot_today = _rows(conn, hot_sql, (dates[0],)) if len(dates) >= 1 else []
        hot_prev = _rows(conn, hot_sql, (dates[1],)) if len(dates) >= 2 else []
        livenews = _rows(
            conn,
            "SELECT id, created_ts, created_at, text FROM xq_livenews WHERE created_ts >= ? "
            "ORDER BY created_ts DESC",
            (cutoff,),
        )
```

把 `return` 字典改为(追加三键):

```python
    return {
        "date": trade_date,
        "weibo_posts": weibo,
        "xq_posts": xq,
        "sectors": sectors,
        "dragon_tiger": dragon,
        "xq_hot_rank_today": hot_today,
        "xq_hot_rank_prev": hot_prev,
        "xq_livenews": livenews,
    }
```

- [ ] **Step 4: 运行确认通过**

```powershell
python -m unittest tests.test_report_reader -v
```
预期:全部 PASS(含表缺失容错——`_rows` 已吞 `no such table`)。

- [ ] **Step 5:(可选)git checkpoint**

```powershell
git add report/reader.py tests/test_report_reader.py
git commit -m "feat(report): reader collects xq_hot_rank two days + livenews"
```

---

## Task 5:prompt 加"雪球注意力"段 + 环比判定

**文件:**
- Modify: `report/prompt.py`(加 `compute_hot_rank_changes`、`_fmt_xq_attention`;`build_prompt` 插段;`SYSTEM` 补措辞)
- Test: `tests/test_report_prompt.py`

- [ ] **Step 1: 写失败测试(环比判定 + build_prompt 含段)**

先看现有 `tests/test_report_prompt.py` 的 import 与用例风格,然后在 `if __name__` 之前追加:

```python
from report.prompt import compute_hot_rank_changes, build_prompt


class TestHotRankChanges(unittest.TestCase):
    def test_new_jumped_dropped(self):
        today = [
            {"symbol": "SZ300750", "name": "宁德", "rank": 1},   # 新上榜(prev 无)
            {"symbol": "SH600000", "name": "浦发", "rank": 2},   # 跃升 12→2
            {"symbol": "SH601398", "name": "工商", "rank": 3},   # 持平附近
        ]
        prev = [
            {"symbol": "SH600000", "name": "浦发", "rank": 14},
            {"symbol": "SH601398", "name": "工商", "rank": 4},
            {"symbol": "SZ000002", "name": "万科", "rank": 5},    # 退潮(今日无)
        ]
        chg = compute_hot_rank_changes(today, prev, top_n=20, jump=10)
        new_syms = {r["symbol"] for r in chg["new"]}
        jumped_syms = {r["symbol"] for r in chg["jumped"]}
        dropped_syms = {r["symbol"] for r in chg["dropped"]}
        self.assertIn("SZ300750", new_syms)
        self.assertIn("SH600000", jumped_syms)      # 上升 12 名 ≥ jump=10
        self.assertNotIn("SH601398", jumped_syms)   # 只升 1 名
        self.assertIn("SZ000002", dropped_syms)     # 掉出今日榜

    def test_build_prompt_has_attention_section(self):
        data = {
            "date": "20260717",
            "weibo_posts": [], "xq_posts": [], "sectors": [], "dragon_tiger": [],
            "xq_hot_rank_today": [{"symbol": "SZ300750", "name": "宁德", "rank": 1, "hot_value": 20.0}],
            "xq_hot_rank_prev": [],
            "xq_livenews": [{"text": "宁德时代扩产", "created_at": ""}],
        }
        system, user = build_prompt(data)
        self.assertIn("雪球注意力", user)
        self.assertIn("宁德", user)
        self.assertIn("宁德时代扩产", user)
```

- [ ] **Step 2: 运行确认失败**

```powershell
python -m unittest tests.test_report_prompt -v
```
预期:`ImportError: cannot import name 'compute_hot_rank_changes'`。

- [ ] **Step 3: 加环比纯函数 + 格式化函数(在 `_fmt_dragon` 之后)**

```python
def compute_hot_rank_changes(
    today: list[dict], prev: list[dict], top_n: int = 20, jump: int = 10
) -> dict:
    """对比今日 vs 前一日热榜,分出:new(新上榜)/ jumped(排名跃升 ≥jump)/ dropped(掉出今日榜)。

    rank 越小越靠前;仅在 top_n 内判定 new/jumped。返回 {'new':[], 'jumped':[], 'dropped':[]}。
    """
    prev_rank = {r["symbol"]: r["rank"] for r in prev}
    today_syms = {r["symbol"] for r in today}
    new_faces: list[dict] = []
    jumped: list[dict] = []
    for r in today:
        if r["rank"] > top_n:
            continue
        pr = prev_rank.get(r["symbol"])
        if pr is None:
            new_faces.append(r)
        elif pr - r["rank"] >= jump:
            jumped.append({**r, "prev_rank": pr})
    dropped = [r for r in prev if r["rank"] <= top_n and r["symbol"] not in today_syms]
    return {"new": new_faces, "jumped": jumped, "dropped": dropped}


def _fmt_xq_attention(
    today: list[dict], prev: list[dict], livenews: list[dict], top_n: int = 20
) -> str:
    if not today and not livenews:
        return "【雪球注意力】(今日无热榜/快讯数据)\n"
    lines = ["【雪球注意力】(热榜=全站讨论热度聚合;只是注意力信号,需与行情/资金流交叉验证,非买卖依据):"]
    if today:
        head = [f"{r.get('name') or r.get('symbol')}(#{r['rank']})" for r in today[:top_n]]
        lines.append(f"- 热榜 Top{min(top_n, len(today))}:" + "、".join(head))
        chg = compute_hot_rank_changes(today, prev, top_n=top_n)
        if chg["new"]:
            lines.append("- ↑新上榜:" + "、".join(r.get("name") or r["symbol"] for r in chg["new"]))
        if chg["jumped"]:
            lines.append(
                "- ↑排名跃升:"
                + "、".join(f"{r.get('name') or r['symbol']}({r['prev_rank']}→{r['rank']})" for r in chg["jumped"])
            )
        if chg["dropped"]:
            lines.append("- ↓退潮:" + "、".join(r.get("name") or r["symbol"] for r in chg["dropped"]))
    if livenews:
        lines.append(f"- 当日 7x24 快讯 {len(livenews)} 条,代表性摘录:")
        for n in livenews[:15]:
            t = (n.get("text") or "").replace("\n", " ").strip()
            if len(t) > 80:
                t = t[:80] + "…"
            if t:
                lines.append(f"  · {t}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: `SYSTEM` 补一句交叉验证/免责**

把 `SYSTEM` 字符串末尾追加一句:

```python
    "雪球热榜/快讯反映的是市场注意力(讨论热度),对未来收益预测性弱、多为同步或滞后信号;"
    "请把它当作'注意力异动假设',务必与当日行情/资金流交叉印证,并明确提示反指风险。"
```

- [ ] **Step 5: `build_prompt` 插入"雪球注意力"段**

把 `build_prompt` 里 `parts` 列表中 `_fmt_texts("雪球讨论", ...)` 那一行之后插入:

```python
        _fmt_xq_attention(
            data.get("xq_hot_rank_today") or [],
            data.get("xq_hot_rank_prev") or [],
            data.get("xq_livenews") or [],
        ),
```

并把结尾产出说明调整为提及注意力流向(把最后那段 `"请产出《每日观察简报》:..."` 替换为):

```python
        "请产出《每日观察简报》:先一句总览;再按板块列要点(涉及哪些股/板块、观点倾向、当日表现);"
        "结合雪球注意力信号点明'注意力正在向哪些板块/个股流动、可能的催化剂或新叙事、以及反指风险';"
        "最后一句风险提示。控制在 600 字内。所有热度类信号须标注需交叉验证、非买卖建议。",
```

- [ ] **Step 6: 运行确认通过**

```powershell
python -m unittest tests.test_report_prompt -v
```
预期:全部 PASS。

- [ ] **Step 7:(可选)git checkpoint**

```powershell
git add report/prompt.py tests/test_report_prompt.py
git commit -m "feat(report): prompt adds xueqiu attention section with rank-delta"
```

---

## Task 6:配置 + README

**文件:**
- Modify: `weibo/cli.py`(`DEFAULTS` 加三键)
- Modify: `config.example.json`(加三键 + 旧键注明停用)
- Modify: `README.md`(雪球配置表 + crawl 语义)

- [ ] **Step 1: `DEFAULTS` 追加三键**

在 `weibo/cli.py` 的 `DEFAULTS` 里、`"xq_comment_scope": "author_only",` 之后追加:

```python
    # ── 雪球注意力(一期)──
    "xq_hot_rank_size": 100,        # 热股榜取前 N
    "xq_hot_rank_market": "沪深",    # 沪深 / 港 / 美
    "xq_livenews_max_pages": 5,     # 7x24 单次最多翻几页
```

> 旧键 `xq_max_pages`/`xq_max_comment_pages`/`xq_comment_scope` 保留在 `DEFAULTS`(供停用的 `crawl_one` 引用),不删除。

- [ ] **Step 2: `config.example.json` 更新雪球段**

把现有雪球段替换为(旧博主键保留 + 注明一期停用 + 新增三键):

```jsonc
  "xueqiu_bloggers": [
    { "user_id": "1234567", "note": "一期已停用(按博主抓取);保留供未来三期大V策略提炼" }
  ],
  "xq_profile_dir": ".xq_profile",
  "xq_hot_rank_size": 100,
  "xq_hot_rank_market": "沪深",
  "xq_livenews_max_pages": 5,
  "xq_max_pages": 200,
  "xq_max_comment_pages": 5,
  "xq_comment_scope": "author_only",
```

> 注:`config.example.json` 是 JSON 不是 JSONC,`//` 注释不能进真实文件——上面的 `jsonc` 只是展示;写入 `config.example.json` 时**去掉行内注释**,`note` 字段用于说明。

- [ ] **Step 3: README 雪球配置表 + 用法更新**

在 `README.md` 配置块的雪球段,把 `xq_max_pages`/`xq_max_comment_pages`/`xq_comment_scope` 三行替换为:

```jsonc
  // ── 雪球(一期:抓全站注意力信号,不再按博主抓)──
  "xq_hot_rank_size": 100,        // 热股榜取前 N(每日快照,构成注意力时间序列)
  "xq_hot_rank_market": "沪深",    // 沪深 / 港 / 美
  "xq_livenews_max_pages": 5,     // 7x24 快讯单次最多翻几页(增量:遇已入库 id 停)
  // 下列旧键一期停用,保留供未来三期大 V 策略提炼:
  "xq_max_pages": 200, "xq_max_comment_pages": 5, "xq_comment_scope": "author_only",
```

把功能简介第 10 行 `- **雪球抓取**...抓雪球博主的帖子 + 评论。` 改为:

```
- **雪球抓取**(`xueqiu`)—— 一期抓全站**注意力信号**:热股榜(每日快照,数字硬指标)+ 7x24 快讯(短文本佐料);不再按博主抓时间线。
```

把“怎么跑”里雪球两行(`python -m xueqiu crawl --mode full` / `--mode incremental`)统一说明改为:

```
python -m xueqiu crawl                   # 抓当天热股榜(快照 upsert)+ 7x24 快讯(增量);--mode 保留仅为契约一致,对热榜无意义
```

- [ ] **Step 4: 验证配置可加载 + 全量测试**

```powershell
python -c "from weibo.cli import DEFAULTS; assert 'xq_hot_rank_size' in DEFAULTS and 'xq_livenews_max_pages' in DEFAULTS; print('ok')"
python -m unittest discover -s tests
```
预期:打印 `ok`;测试全绿。

- [ ] **Step 5:(可选)git checkpoint**

```powershell
git add weibo/cli.py config.example.json README.md
git commit -m "chore: add xq attention config keys, deprecate blogger-crawl keys in docs"
```

---

## Task 7:全量回归 + 收尾

- [ ] **Step 1: 跑完整测试套件**

```powershell
python -m unittest discover -s tests
```
预期:全部 PASS(含新增 hot_rank/livenews/reader/prompt 用例 + 原有 31 项不回归)。

- [ ] **Step 2: 确认关键模块可导入**

```powershell
python -c "import xueqiu.cli, report.reader, report.prompt; print('imports ok')"
```

- [ ] **Step 3:(可选,需联网)端到端冒烟**

```powershell
python -m xueqiu crawl
python -m report build --date 20260717
```
预期:crawl 入库两表;`report build` 生成的简报正文含"雪球注意力"段与免责措辞。

- [ ] **Step 4:(可选)最终 git checkpoint**

```powershell
git add -A
git commit -m "feat(xueqiu): attention-signal redesign (hot_rank + livenews) end-to-end"
```

---

## 附:自查结论(计划作者已核对)

- **设计覆盖**:设计第 3–9 节逐条落到 Task 0–7(端点→Task0;storage→Task1;crawler→Task2;cli→Task3;reader→Task4;prompt→Task5;配置→Task6;测试分散在 Task1/2/4/5;实现顺序与设计第 9 节一致)。
- **阻塞项**:端点联网实测已置于 Task 0 最前,含失效退路(adata / 仅热榜)。
- **类型一致**:`slim_hot_rank(items, market_type, date_str)`、`slim_livenews(it)`、`fetch_hot_rank(size, market_type)->list[dict]`、`fetch_livenews(max_id, count)->tuple[list,int]`、`compute_hot_rank_changes(today, prev, top_n, jump)->dict{new,jumped,dropped}` 在各任务/测试中签名统一;reader 返回键 `xq_hot_rank_today`/`xq_hot_rank_prev`/`xq_livenews` 与 prompt 消费键一致。
- **占位符**:Task 2 Step 6 故意埋了 `ts_now()` 笔误并紧跟纠正提示(改回 `now_iso()`),确保执行者不引入不存在函数;其余步骤均给出可直接粘贴的完整代码。
- **项目约定**:中文注释、`from __future__ import annotations` 已在各文件顶部、无兼容 shim、旧 `user_timeline` 按设计保留、未擅自加错误兜底(仅沿用既有 `_rows` 表缺失容错与熔断)。
- **git 可选**:项目未初始化 git,所有 commit 步骤标注"可选",完成判据为测试全绿。
