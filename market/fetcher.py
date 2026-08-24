"""行情抓取层:akshare 薄封装(第三方库以参数 ak 注入,便于测试)。

板块资金流近似口径(设计文档 5):
  main_net_inflow_est   = akshare "今日主力净流入-净额"(=超大+大单,akshare 已合并)
  retail_net_inflow_est = "今日中单净流入-净额" + "今日小单净流入-净额"
两者均为估算、非真实机构/散户逐日买卖数据。

akshare 列名易变:在边界做列缺失容错——缺列时该字段填 None 并抛/记 MarketError,
不静默给错数。成交额/成交量为 best-effort(板块名称接口不一定含),缺则 None。

国内 Windows 环境访问新浪等数据源常遇 SSL 证书问题(公司网络/代理);
在模块加载时全局禁用 SSL 验证(personal research tool,不面向外部用户)。
"""

from __future__ import annotations

import urllib3
urllib3.disable_warnings()

import requests as _requests

_orig_send = _requests.Session.send

def _send_skip_verify(self, request, **kwargs):
    kwargs["verify"] = False
    return _orig_send(self, request, **kwargs)

_requests.Session.send = _send_skip_verify  # type: ignore[assignment]

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


def _pick(row: dict, keys: list[str]):
    """取第一个存在的列(接口列名易变,给多个候选)。都不在返回 None。"""
    for k in keys:
        if k in row:
            return row[k]
    return None


def _sector_type_label(sector_type: str) -> str:
    """把 akshare 的 sector_type 参数(如 '行业资金流')归一成入库标签('行业'/'概念'/'地域')。"""
    if "概念" in sector_type:
        return "概念"
    if "地域" in sector_type:
        return "地域"
    return "行业"


def fetch_sector_fund_flow(trade_date: str, ak=None, sector_type: str = "行业资金流") -> list[dict]:
    """板块资金流排名 → 四级净额 + 合成口径行。ak 缺省时懒加载真实 akshare。

    四级净额(超大/大/中/小)优先;若接口只给合并的"今日主力净流入-净额"(=超大+大)则 fallback:
    main 用合并列、四级置 None,不崩(记 warning)。retail 需中+小,缺则 None。
    """
    if ak is None:
        import akshare as ak  # noqa: PLC0415
    df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type=sector_type)
    recs = _records(df)
    label = _sector_type_label(sector_type)
    out: list[dict] = []
    for r in recs:
        _require(r, ["名称", "今日涨跌幅"], "板块资金流")
        super_large = _pick(r, ["今日超大单净流入-净额"])
        large = _pick(r, ["今日大单净流入-净额"])
        medium = _pick(r, ["今日中单净流入-净额"])
        small = _pick(r, ["今日小单净流入-净额"])
        merged_main = _pick(r, ["今日主力净流入-净额"])
        if super_large is not None and large is not None:
            main_est = (super_large or 0) + (large or 0)
        else:
            main_est = merged_main  # fallback:四级缺失用合并主力列
        retail_est = None
        if medium is not None or small is not None:
            retail_est = (medium or 0) + (small or 0)
        out.append({
            "trade_date": trade_date,
            "sector": r["名称"],
            "sector_type": label,
            "pct_change": r["今日涨跌幅"],
            "turnover": None,
            "volume": None,
            "super_large_net": super_large,
            "large_net": large,
            "medium_net": medium,
            "small_net": small,
            "main_net_inflow_est": main_est,
            "retail_net_inflow_est": retail_est,
            "source": "akshare",
            "fetched_at": _now_iso(),
        })
    return out


def _fetch_industry_ths(trade_date: str, ak) -> list[dict]:
    """同花顺行业板块汇总 → 涨跌幅 + 成交额/量 + 主力净流入(只有合计,不拆四级)。

    东财 push2his 域名被限流时用此兜底。同花顺返回 90 个申万一级行业,单位:亿。
    缺四级净额拆分,无概念板块数据。

    DataFrame 列位(按位置取,避免字符串编码差异):
      [0]序号 [1]板块 [2]涨跌幅 [3]总成交额 [4]总成交量 [5]主力净流入
      [6]上涨家数 [7]下跌家数 [8]均价 [9]领涨股 [10]领涨股-最新价 [11]领涨股-涨跌幅
    """
    df = ak.stock_board_industry_summary_ths()
    recs = _records(df)
    out: list[dict] = []
    for r in recs:
        vals = list(r.values())
        # 按位置取,THS 单位是「亿」→ 转为元保持与东财一致
        def _val(idx):
            v = vals[idx] if idx < len(vals) else None
            return float(v) if isinstance(v, (int, float)) else None
        out.append({
            "trade_date": trade_date,
            "sector": vals[1] if len(vals) > 1 else None,
            "sector_type": "行业",
            "pct_change": _val(2),
            "turnover": (_val(3) * 1e8) if _val(3) is not None else None,
            "volume": (_val(4) * 1e8) if _val(4) is not None else None,
            "super_large_net": None,    # 同花顺不拆四级
            "large_net": None,
            "medium_net": None,
            "small_net": None,
            "main_net_inflow_est": (_val(5) * 1e8) if _val(5) is not None else None,
            "retail_net_inflow_est": None,
            "source": "akshare-ths",
            "fetched_at": _now_iso(),
        })
    return out


def _fetch_sector_quote(ak, sector_type: str = "行业资金流") -> dict[str, dict]:
    """板块成交额/成交量(best-effort),返回 {板块名: {turnover, volume}}。

    按 sector_type 选对应板块行情接口,保证板块名与资金流接口口径一致(否则合并匹配不上):
      行业 → stock_board_industry_name_em;概念 → stock_board_concept_name_em。
    地域/未知类型无对应板块行情接口 → 返回 {}(turnover/volume 保持 None)。
    接口不可用/缺列也返回 {},不阻断资金流主数据。
    """
    if "概念" in sector_type:
        fn = getattr(ak, "stock_board_concept_name_em", None)
    elif "行业" in sector_type:
        fn = getattr(ak, "stock_board_industry_name_em", None)
    else:
        fn = None
    if fn is None:
        return {}
    try:
        recs = _records(fn())
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
    quote = _fetch_sector_quote(ak, sector_type)
    for row in rows:
        q = quote.get(row["sector"])
        if q:
            row["turnover"] = q.get("turnover")
            row["volume"] = q.get("volume")
    return rows


def fetch_all_sectors(trade_date: str, ak=None) -> list[dict]:
    """顶层入口:行业 + 概念板块全量(地域丢弃),合并返回。cli 层调这个。

    行业优先走同花顺 akshare;概念优先走同花顺 Playwright(带 chameleon token 翻页);
    各自失败时回退东财(家庭网络可能通,公司网络通常不通)。"""
    if ak is None:
        import akshare as ak  # noqa: PLC0415
    out: list[dict] = []

    # 行业:同花顺优先(不依赖东财 push2his,每日 90 个行业一次调完)
    try:
        out.extend(_fetch_industry_ths(trade_date, ak=ak))
    except Exception:  # noqa: BLE001
        try:
            out.extend(fetch_snapshot(trade_date, ak=ak, sector_type="行业资金流"))
        except Exception:  # noqa: BLE001
            pass

    # 概念:同花顺 Playwright 优先 → 东财兜底
    concept_rows = _fetch_concept_ths(trade_date)
    if concept_rows:
        out.extend(concept_rows)
    else:
        try:
            out.extend(fetch_snapshot(trade_date, ak=ak, sector_type="概念资金流"))
        except Exception:  # noqa: BLE001
            pass

    return out


def _fetch_concept_ths(trade_date: str) -> list[dict]:
    """同花顺概念板块资金流(Playwright 拿 chameleon token → requests 翻页)。

    URL: data.10jqka.com.cn/funds/gnzjl/
    Python requests 直接被 401+chameleon JS 拦截;用 Playwright headless
    打开页面等 JS 执行完毕,从 cookie 提取 hexin-v token,再用 requests 翻页。
    失败返回空列表,上层走东财兜底。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    rows: list[dict] = []

    try:
        with sync_playwright() as pw:
            # 同花顺 Nginx 检测 headless 浏览器并拦截,必须用 headed + 反检测
            # token 在浏览器关闭后立即失效,所以翻页也在浏览器内 JS 完成
            browser = pw.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--window-position=-32000,-32000",
                ],
            )
            page = browser.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page.goto(
                "https://data.10jqka.com.cn/funds/gnzjl/",
                wait_until="networkidle",
                timeout=30000,
            )
            page.wait_for_selector("table.m-table tbody tr", timeout=20000)
            # 等 chameleon cookie 写入
            page.wait_for_function(
                "document.cookie.includes('v=')", timeout=10000
            )

            def _extract_table():
                """从当前页面提取概念数据。"""
                return page.evaluate("""() => {
                    const rows = [];
                    document.querySelectorAll('table.m-table tbody tr').forEach(tr => {
                        const tds = tr.querySelectorAll('td');
                        if (tds.length >= 7) {
                            const name = tds[1].textContent.trim();
                            if (name && name !== '行业') {
                                rows.push({
                                    name: name,
                                    pct_change: tds[3].textContent.trim(),
                                    net: tds[6].textContent.trim(),
                                });
                            }
                        }
                    });
                    return rows;
                }""")

            all_page_rows: list[dict] = []
            seen: set = set()
            max_pages = 12

            for pn in range(1, max_pages + 1):
                # 等待当前页表格渲染
                try:
                    page.wait_for_selector("table.m-table tbody tr", timeout=10000)
                except Exception:  # noqa: BLE001
                    break

                page_rows = _extract_table()
                new_count = 0
                for r in page_rows:
                    name = r.get("name", "")
                    if name and name not in seen:
                        seen.add(name)
                        all_page_rows.append(r)
                        new_count += 1

                if new_count < 50:
                    break  # 最后一页

                # 点击"下一页"
                if pn >= max_pages:
                    break
                try:
                    next_btn = page.locator("a.changePage:has-text('下一页')")
                    if not next_btn.is_visible():
                        break
                    next_btn.click()
                    page.wait_for_timeout(1500)  # 等 AJAX 加载
                except Exception:  # noqa: BLE001
                    break

            browser.close()

            for r in all_page_rows:
                pct = _to_float((r.get("pct_change") or "").replace("%", ""))
                net_raw = _to_float(r.get("net"))
                rows.append({
                    "trade_date": trade_date,
                    "sector": r.get("name"),
                    "sector_type": "概念",
                    "pct_change": pct,
                    "turnover": None,
                    "volume": None,
                    "super_large_net": None,
                    "large_net": None,
                    "medium_net": None,
                    "small_net": None,
                    "main_net_inflow_est": net_raw * 1e8 if net_raw is not None else None,
                    "retail_net_inflow_est": None,
                    "source": "ths-playwright",
                    "fetched_at": _now_iso(),
                })

    except Exception:  # noqa: BLE001
        pass

    return rows


def _parse_ths_table(html: str) -> list[dict]:
    """解析同花顺概念资金流 HTML 表格 → list[dict]。"""
    import re
    out: list[dict] = []
    # 匹配 <tr>...</tr> 中的 <td> 内容
    tr_pattern = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL)
    td_pattern = re.compile(r"<td[^>]*>(.*?)</td>", re.DOTALL)
    tag_strip = re.compile(r"<[^>]+>")

    for tr_m in tr_pattern.finditer(html):
        tds = td_pattern.findall(tr_m.group(1))
        if len(tds) < 7:
            continue
        # 去 HTML 标签 + 空白
        cells = [tag_strip.sub("", td).strip() for td in tds]
        if not cells[1] or cells[1] == "行业":  # 跳过表头/空行
            continue
        out.append({
            "name": cells[1],
            "pct_change": cells[3],
            "net": cells[6],  # 净额(亿)
        })
    return out


# A 股三大指数(腾讯日线接口代码)
A_INDEXES = [
    ("sh000001", "上证综指"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
]
# 海外指数:腾讯 qt 实时行情(gtimg.cn,无日经/韩国)
OVERSEAS_QT = [
    ("hkHSI",     "hk_hsi",  "恒生指数"),
    ("usDJI",     "us_dji",  "道琼斯"),
    ("usIXIC",    "us_ixic", "纳斯达克"),
    ("usINX",     "us_spx",  "标普500"),
]
# 日经/韩国用东财全球接口兜底(best-effort,同 push2his 域名可能不通)
OVERSEAS_FALLBACK_KEYS = ["日经", "韩国"]
OVERSEAS_FALLBACK_MAP = {
    "日经": ("jp_n225", "日经225"),
    "韩国": ("kr_kospi", "韩国KOSPI"),
}


def _qt_raw(code: str, timeout: int = 10) -> str | None:
    """腾讯 qt 实时行情 HTTP 调用(提取为函数便于测试注入)。"""
    import requests as _r
    try:
        resp = _r.get(f"http://qt.gtimg.cn/q={code}", timeout=timeout)
        if resp.status_code == 200:
            return resp.text
    except Exception:  # noqa: BLE001
        pass
    return None


def _parse_qt(raw: str) -> dict | None:
    """解析腾讯 qt 行情行 'v_CODE="field1~field2~..."' → dict。失败返回 None。"""
    try:
        inner = raw.split('"')[1]
    except IndexError:
        return None
    parts = inner.split("~")
    if len(parts) < 33:
        return None
    return {
        "close": _to_float(parts[3]),
        "pct_change": _to_float(parts[32]),
        "fetched_at": _now_iso(),
    }


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_index(trade_date: str, ak=None) -> list[dict]:
    """大盘指数快照:A 股三大(开/收/高/低/涨跌/额)+ 海外四(仅收/涨)+ 日经/韩国兜底。

    A 股走腾讯日线接口;海外走腾讯 qt 实时行情(gtimg.cn);
    日经 225 / 韩国 KOSPI 腾讯不覆盖,用东财全球接口兜底(best-effort)。
    ⚠️ 美股为隔夜收盘,与当日 A 股不同步,口径由下游简报标注。
    """
    if ak is None:
        import akshare as ak  # noqa: PLC0415
    out: list[dict] = []

    # A 股:腾讯日线(逐指数调用,每条拿最近两行算 pct_change)
    for icode, iname in A_INDEXES:
        try:
            df = ak.stock_zh_index_daily_tx(symbol=icode)
        except Exception:  # noqa: BLE001
            continue
        if df.empty:
            continue
        recs = _records(df)
        last = recs[-1]
        # 腾讯接口日期格式 "2026-07-30",trade_date 是 "20260730",统一比较
        last_date = str(last.get("date", "")).replace("-", "")
        if last_date != trade_date:
            continue
        pct_change = None
        if len(recs) >= 2:
            prev_close = recs[-2].get("close")
            cur_close = last.get("close")
            if prev_close and cur_close and prev_close != 0:
                pct_change = round((cur_close - prev_close) / prev_close * 100, 2)
        out.append({
            "trade_date": trade_date, "index_code": icode, "index_name": iname,
            "close": last.get("close"),
            "pct_change": pct_change,
            "turnover": last.get("amount"),   # 腾讯字段名 amount=成交额
            "volume": last.get("volume"),
            "source": "akshare-tx", "fetched_at": _now_iso(),
        })

    # 海外:腾讯 qt 实时行情(恒生/道琼斯/纳斯达克/标普)
    for qt_code, int_code, int_name in OVERSEAS_QT:
        raw = _qt_raw(qt_code)
        if not raw:
            continue
        d = _parse_qt(raw)
        if d and d["close"] is not None:
            out.append({
                "trade_date": trade_date,
                "index_code": int_code, "index_name": int_name,
                "close": d["close"],
                "pct_change": d["pct_change"],
                "turnover": None, "volume": None,
                "source": "tencent-qt", "fetched_at": d["fetched_at"],
            })

    # 日经/韩国兜底:东财全球指数实时快照(按名称子串匹配)
    try:
        grecs = _records(ak.index_global_spot_em())
    except Exception:  # noqa: BLE001
        grecs = []
    for r in grecs:
        name = str(_pick(r, ["名称"]) or "")
        for key in OVERSEAS_FALLBACK_KEYS:
            if key not in name:
                continue
            icode, iname = OVERSEAS_FALLBACK_MAP[key]
            out.append({
                "trade_date": trade_date, "index_code": icode, "index_name": iname,
                "close": _pick(r, ["最新价", "收盘价", "收盘"]),
                "pct_change": _pick(r, ["涨跌幅"]),
                "turnover": None, "volume": None,
                "source": "akshare", "fetched_at": _now_iso(),
            })
            break
    return out


def _legu_to_dict(recs: list[dict]) -> dict:
    """stock_market_activity_legu 是 item/value 键值行,转成 {item: value} 便于取。"""
    d: dict = {}
    for r in recs:
        k = _pick(r, ["item", "项目", "名称"])
        v = _pick(r, ["value", "值", "数值"])
        if k is not None:
            d[str(k)] = v
    return d


def _to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _margin_balance(ak) -> float | None:
    """两融余额 = 沪 + 深 最新一日融资融券余额(T+1 慢数据),best-effort 缺则 None。"""
    total = 0.0
    got = False
    for fn_name in ("stock_margin_sse", "stock_margin_szse"):
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            recs = _records(fn())
        except Exception:  # noqa: BLE001
            continue
        if not recs:
            continue
        last = recs[-1]
        val = _pick(last, ["融资融券余额", "融资融券余额(元)"])
        if val is not None:
            try:
                total += float(val)
                got = True
            except (TypeError, ValueError):
                pass
    return total if got else None


def fetch_sentiment(trade_date: str, ak=None) -> dict:
    """市场情绪(硬指标腿):涨跌家数/涨跌停(乐咕活跃度)+ 两融余额。一天一行。

    total_turnover 由 cli 层用大盘指数汇总注入(此处置 None,避免额外重接口);
    这里只出家数 + 涨跌停 + up_down_ratio + margin_balance。
    """
    if ak is None:
        import akshare as ak  # noqa: PLC0415
    legu = _legu_to_dict(_records(ak.stock_market_activity_legu()))
    up = _to_int(_pick(legu, ["上涨"]))
    down = _to_int(_pick(legu, ["下跌"]))
    limit_up = _to_int(_pick(legu, ["真实涨停", "涨停"]))
    limit_down = _to_int(_pick(legu, ["真实跌停", "跌停"]))
    ratio = round(up / down, 3) if up is not None and down else None
    return {
        "trade_date": trade_date,
        "up_count": up,
        "down_count": down,
        "limit_up_count": limit_up,
        "limit_down_count": limit_down,
        "up_down_ratio": ratio,
        "total_turnover": None,
        "margin_balance": _margin_balance(ak),
        "fetched_at": _now_iso(),
    }


def fetch_dragon_tiger(trade_date: str, ak=None) -> list[dict]:
    """龙虎榜明细。inst_seat_count 需另接口拆解席位,一期先置 None(留二期补)。

    无数据日期(盘前/休市/披露延迟)akshare 抛 TypeError/AttributeError,如
    'NoneType' object is not subscriptable → 当空结果返回 [](预期无数据,cli 记 info);
    网络/接口真故障(ConnectionError/KeyError 等)照抛,由 cli 记 ERROR。
    """
    if ak is None:
        import akshare as ak  # noqa: PLC0415
    import json as _json
    try:
        df = ak.stock_lhb_detail_em(start_date=trade_date, end_date=trade_date)
    except (TypeError, AttributeError):
        return []
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
