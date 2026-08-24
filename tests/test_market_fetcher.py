"""行情抓取层单测:注入假 akshare(返回 list[dict] 模拟 DataFrame.to_dict('records')),
验证板块资金流近似口径映射(主力=主力净流入-净额;散户=中单+小单)与列缺失容错。"""
from __future__ import annotations

import unittest

from market import fetcher


class _FakeDF:
    def __init__(self, records: list[dict]):
        self._records = records
        self.columns = list(records[0].keys()) if records else []
        self.empty = len(records) == 0

    def to_dict(self, orient: str):
        assert orient == "records"
        return self._records


class _FakeAk:
    def stock_board_industry_summary_ths(self):
        # 同花顺行业汇总(模拟)。fetcher 按列位置取值([3]总成交量/万手 [4]总成交额/亿
        # [5]净流入/亿),键序必须与真实接口列序一致,不能调换
        return _FakeDF([
            {"序号": 1, "板块": "半导体", "涨跌幅": 7.2,
             "总成交量": 1379.52, "总成交额": 1694.82, "主力净流入": -34.29,
             "上涨家数": 182, "下跌家数": 1, "均价": 81.4,
             "领涨股": "紫光国微", "领涨股-最新价": 82.14, "领涨股-涨跌幅": 20.0},
        ])

    def stock_sector_fund_flow_rank(self, indicator, sector_type):
        # 概念类型返回不同板块名,便于验证行业/概念双跑合并
        if "概念" in sector_type:
            return _FakeDF([{
                "名称": "AI概念", "今日涨跌幅": 3.3,
                "今日超大单净流入-净额": 8.0e7, "今日大单净流入-净额": 4.0e7,
                "今日中单净流入-净额": -5.0e7, "今日小单净流入-净额": -3.0e7,
            }])
        return _FakeDF([{
            "名称": "半导体", "今日涨跌幅": 2.1,
            "今日超大单净流入-净额": 6.0e7, "今日大单净流入-净额": 4.0e7,
            "今日主力净流入-净额": 1.0e8,
            "今日中单净流入-净额": -3.0e7,
            "今日小单净流入-净额": -1.0e7,
        }])

    def stock_board_industry_name_em(self):
        return _FakeDF([{"板块名称": "半导体", "成交额": 5.0e9, "成交量": 1.2e8}])

    def stock_board_concept_name_em(self):
        return _FakeDF([{"板块名称": "AI概念", "成交额": 7.0e9, "成交量": 2.0e8}])

    def stock_zh_index_daily_tx(self, symbol):
        # 腾讯日线:返回多行 date/open/close/high/low/amount,取最近一行按 date 匹配
        data = {
            "sh000001": [
                {"date": "2026-07-15", "open": 3080.0, "close": 3085.0, "high": 3095.0, "low": 3070.0, "amount": 2.9e11, "volume": 2.9e10},
                {"date": "2026-07-16", "open": 3085.0, "close": 3100.0, "high": 3110.0, "low": 3080.0, "amount": 3.0e11, "volume": 3.0e10},
            ],
            "sz399001": [
                {"date": "2026-07-15", "open": 13000.0, "close": 13050.0, "high": 13100.0, "low": 12950.0, "amount": 4.0e11, "volume": 3.5e10},
                {"date": "2026-07-16", "open": 13050.0, "close": 13100.0, "high": 13150.0, "low": 13000.0, "amount": 4.2e11, "volume": 3.6e10},
            ],
            "sz399006": [
                {"date": "2026-07-15", "open": 2090.0, "close": 2117.0, "high": 2120.0, "low": 2080.0, "amount": 0.9e11, "volume": 0.9e10},
                {"date": "2026-07-16", "open": 2117.0, "close": 2100.0, "high": 2130.0, "low": 2090.0, "amount": 1.0e11, "volume": 1.0e10},
            ],
        }
        return _FakeDF(data.get(symbol, []))

    def index_global_spot_em(self):
        return _FakeDF([
            {"名称": "恒生指数", "最新价": 18000.0, "涨跌幅": 1.2},
            {"名称": "纳斯达克", "最新价": 16000.0, "涨跌幅": -0.3},
            {"名称": "其它海外", "最新价": 1.0, "涨跌幅": 0.0},
        ])

    def stock_market_activity_legu(self):
        return _FakeDF([
            {"item": "上涨", "value": 3000},
            {"item": "下跌", "value": 1500},
            {"item": "真实涨停", "value": 42},
            {"item": "真实跌停", "value": 6},
        ])

    def stock_margin_sse(self, start_date=None, end_date=None):
        # 沪:区间查询。真实接口不保证行序(实测最新在前),故意按新→旧排,
        # 实现必须按日期列排序后取最新——直接取末行会拿到旧的 8.0e11
        return _FakeDF([{"信用交易日期": "20260716", "融资融券余额": 8.2e11},
                        {"信用交易日期": "20260710", "融资融券余额": 8.0e11}])

    def stock_margin_szse(self, date=None):
        # 深:T+1 单日披露——trade_date 当日(20260716)未出 → 空;回探前一日命中
        if date == "20260716":
            return _FakeDF([])
        return _FakeDF([{"信用交易日期": "20260715", "融资融券余额": 7.8e11}])

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
        self.assertEqual(r["sector_type"], "行业")
        self.assertAlmostEqual(r["pct_change"], 2.1)
        self.assertAlmostEqual(r["super_large_net"], 6.0e7)
        self.assertAlmostEqual(r["large_net"], 4.0e7)
        self.assertAlmostEqual(r["medium_net"], -3.0e7)
        self.assertAlmostEqual(r["small_net"], -1.0e7)
        self.assertAlmostEqual(r["main_net_inflow_est"], 1.0e8)   # 超大+大
        self.assertAlmostEqual(r["retail_net_inflow_est"], -4.0e7)  # 中+小

    def test_fund_flow_fallback_merged_main(self):
        # 四级缺失、只有合并主力列 → main 用合并列,四级为 None,不崩
        class _Ak:
            def stock_sector_fund_flow_rank(self, indicator, sector_type):
                return _FakeDF([{"名称": "半导体", "今日涨跌幅": 1.0,
                                 "今日主力净流入-净额": 5.0e7,
                                 "今日中单净流入-净额": -2.0e7, "今日小单净流入-净额": -1.0e7}])
        r = fetcher.fetch_sector_fund_flow("20260716", ak=_Ak())[0]
        self.assertIsNone(r["super_large_net"])
        self.assertAlmostEqual(r["main_net_inflow_est"], 5.0e7)
        self.assertAlmostEqual(r["retail_net_inflow_est"], -3.0e7)

    def test_fund_flow_nan_falls_back_to_merged(self):
        # 超大/大单缺格时 pandas 给 NaN 而非 None:NaN 要归 None,
        # 否则绕过判断把可用的合并主力净流入也顶掉
        nan = float("nan")

        class _AkNan:
            def stock_sector_fund_flow_rank(self, indicator, sector_type):
                return _FakeDF([{"名称": "半导体", "今日涨跌幅": 1.0,
                                 "今日超大单净流入-净额": nan, "今日大单净流入-净额": nan,
                                 "今日主力净流入-净额": 5.0e7,
                                 "今日中单净流入-净额": nan, "今日小单净流入-净额": -1.0e7}])
        r = fetcher.fetch_sector_fund_flow("20260716", ak=_AkNan())[0]
        self.assertIsNone(fetcher._to_float(nan))
        self.assertIsNone(r["super_large_net"])
        self.assertIsNone(r["large_net"])
        self.assertAlmostEqual(r["main_net_inflow_est"], 5.0e7)   # 回退合并列,不是 NaN
        self.assertAlmostEqual(r["retail_net_inflow_est"], -1.0e7)

    def test_all_sectors_all_paths_fail_raises(self):
        # 同花顺+东财四条抓取路径全失败要抛错走 cli 的 ERROR,不能静默返回空当成功
        class _AkDown:
            def stock_board_industry_summary_ths(self):
                raise ConnectionError("ths down")
            def stock_sector_fund_flow_rank(self, indicator, sector_type):
                raise ConnectionError("em down")
        _orig_concept = fetcher._fetch_concept_ths
        fetcher._fetch_concept_ths = lambda td: []  # type: ignore[attr-defined]
        try:
            with self.assertRaises(fetcher.MarketError):
                fetcher.fetch_all_sectors("20260716", ak=_AkDown())
        finally:
            fetcher._fetch_concept_ths = _orig_concept  # type: ignore[attr-defined]

    def test_snapshot_merges_quote(self):
        rows = fetcher.fetch_snapshot("20260716", ak=_FakeAk(), sector_type="行业资金流")
        r = rows[0]
        self.assertAlmostEqual(r["turnover"], 5.0e9)
        self.assertAlmostEqual(r["volume"], 1.2e8)
        self.assertEqual(r["trade_date"], "20260716")

    def test_fetch_all_sectors(self):
        # mock 掉 Playwright 同花顺概念抓取(测试环境不需要浏览器)
        _orig_concept = fetcher._fetch_concept_ths
        fetcher._fetch_concept_ths = lambda td: []  # type: ignore[attr-defined]
        try:
            rows = fetcher.fetch_all_sectors("20260716", ak=_FakeAk())
        finally:
            fetcher._fetch_concept_ths = _orig_concept  # type: ignore[attr-defined]

        by_type = {r["sector_type"]: r for r in rows}
        self.assertIn("行业", by_type)
        self.assertIn("概念", by_type)
        # 行业走同花顺
        ind = by_type["行业"]
        self.assertEqual(ind["sector"], "半导体")
        self.assertAlmostEqual(ind["pct_change"], 7.2)
        self.assertAlmostEqual(ind["turnover"], 1.69482e11)  # 1694.82 亿 → 元
        self.assertAlmostEqual(ind["volume"], 1.37952e7)  # 1379.52 万手 → 手
        self.assertAlmostEqual(ind["main_net_inflow_est"], -3.429e9)  # -34.29 亿 → 元
        self.assertEqual(ind["source"], "akshare-ths")
        self.assertIsNone(ind["super_large_net"])  # THS 不拆四级
        # 概念走东财(Playwright mock 返回空,回退东财 FakeAk)
        self.assertEqual(by_type["概念"]["sector"], "AI概念")

    def test_fetch_index(self):
        # 注入假腾讯 qt 行情数据。按字段下标构造,与 _parse_qt 取位一一对应:
        # [3]=最新价 [31]=涨跌额 [32]=涨跌幅% [37]=成交额/万元(A 股行才有意义);
        # sz 两指数返回 None → 成交额回填优雅缺省保持 None
        _orig_qt_raw = fetcher._qt_raw
        _orig_qt_multi = fetcher._qt_raw_multi

        def _build(fields: dict[int, str], total: int) -> str:
            parts = [""] * total
            for i, v in fields.items():
                parts[i] = v
            return "~".join(parts)

        data = {
            "sh000001": 'v_sh000001="' + _build(
                {0: "100", 1: "上证指数", 2: "000001", 3: "3105.0",
                 30: "20260716150000", 31: "15.0", 32: "0.49",
                 37: "52000000"}, 80) + '";',
            "hkHSI": 'v_hkHSI="' + _build(
                {0: "100", 1: "恒生指数", 2: "HSI", 3: "18000.0",
                 31: "215.8", 32: "0.56"}, 78) + '";',
            "usDJI": 'v_usDJI="' + _build(
                {0: "200", 1: "道琼斯", 2: ".DJI", 3: "35000.0",
                 31: "100.0", 32: "0.29"}, 71) + '";',
            "usIXIC": 'v_usIXIC="' + _build(
                {0: "200", 1: "纳斯达克", 2: ".IXIC", 3: "16000.0",
                 31: "-50.0", 32: "-0.31"}, 71) + '";',
            "usINX": 'v_usINX="' + _build(
                {0: "200", 1: "标普500", 2: ".INX", 3: "4500.0",
                 31: "10.0", 32: "0.22"}, 71) + '";',
        }

        def _fake_qt(code: str, timeout: int = 10) -> str | None:
            return data.get(code)

        def _fake_qt_multi(codes, timeout: int = 10):
            return {c: data[c] for c in codes if c in data}

        try:
            fetcher._qt_raw = _fake_qt  # type: ignore[attr-defined]
            fetcher._qt_raw_multi = _fake_qt_multi  # type: ignore[attr-defined]
            rows = fetcher.fetch_index("20260716", ak=_FakeAk())
            # 整包回填失败 → A 股成交额全部保持 None(不产半边成交额)
            fetcher._qt_raw_multi = lambda codes, timeout=10: {}  # type: ignore[attr-defined]
            rows_no_qt = fetcher.fetch_index("20260716", ak=_FakeAk())
        finally:
            fetcher._qt_raw = _orig_qt_raw  # type: ignore[attr-defined]
            fetcher._qt_raw_multi = _orig_qt_multi  # type: ignore[attr-defined]

        by_code = {r["index_code"]: r for r in rows}
        # A 股三大指数(腾讯日线;成交额由 qt 回填,sz 未命中回填为 None)
        self.assertIn("sh000001", by_code)
        sh = by_code["sh000001"]
        self.assertAlmostEqual(sh["close"], 3100.0)
        self.assertAlmostEqual(sh["pct_change"], 0.49)  # (3100-3085)/3085
        self.assertAlmostEqual(sh["volume"], 3.0e11)  # 日线第 6 列实为成交量
        self.assertAlmostEqual(sh["turnover"], 5.2e11)  # qt[37]=52000000 万 × 1e4
        self.assertEqual(sh["source"], "akshare-tx")
        self.assertIn("sz399006", by_code)
        self.assertIn("sz399001", by_code)
        self.assertIsNone(by_code["sz399001"]["turnover"])
        self.assertIsNone(
            {r["index_code"]: r for r in rows_no_qt}["sh000001"]["turnover"])
        # 海外:腾讯 qt(恒生/道琼斯/纳斯达克/标普)
        self.assertIn("hk_hsi", by_code)
        self.assertAlmostEqual(by_code["hk_hsi"]["close"], 18000.0)
        self.assertAlmostEqual(by_code["hk_hsi"]["pct_change"], 0.56)
        self.assertEqual(by_code["hk_hsi"]["source"], "tencent-qt")
        self.assertIsNone(by_code["hk_hsi"]["turnover"])
        self.assertIsNone(by_code["hk_hsi"]["volume"])
        self.assertIn("us_dji", by_code)
        self.assertAlmostEqual(by_code["us_dji"]["close"], 35000.0)
        self.assertIn("us_ixic", by_code)
        self.assertAlmostEqual(by_code["us_ixic"]["close"], 16000.0)
        self.assertIn("us_spx", by_code)
        self.assertAlmostEqual(by_code["us_spx"]["close"], 4500.0)
        # 日经/韩国兜底(东财 FakeAk 不返回,所以不应该出现)
        self.assertNotIn("jp_n225", by_code)
        self.assertNotIn("kr_kospi", by_code)

    def test_fetch_sentiment(self):
        s = fetcher.fetch_sentiment("20260716", ak=_FakeAk())
        self.assertEqual(s["up_count"], 3000)
        self.assertEqual(s["down_count"], 1500)
        self.assertEqual(s["limit_up_count"], 42)
        self.assertEqual(s["limit_down_count"], 6)
        self.assertAlmostEqual(s["up_down_ratio"], 2.0)
        self.assertAlmostEqual(s["margin_balance"], 8.2e11 + 7.8e11)  # 沪最新 + 深最新

    def test_margin_requires_both_legs(self):
        # 深腿拿不到时不出"沪-only 半边和"——那会被 upsert 的 COALESCE 固化成常驻错值
        class _AkSzDown(_FakeAk):
            def stock_margin_szse(self, date=None):
                return _FakeDF([])
        s = fetcher.fetch_sentiment("20260716", ak=_AkSzDown())
        self.assertIsNone(s["margin_balance"])

    def test_quote_source_follows_sector_type(self):
        # 概念资金流应改用概念板块行情接口取成交额/量,而非行业接口
        q = fetcher._fetch_sector_quote(_FakeAk(), sector_type="概念资金流")
        self.assertIn("AI概念", q)
        self.assertAlmostEqual(q["AI概念"]["turnover"], 7.0e9)
        # 地域类型无对应接口 → 空
        self.assertEqual(fetcher._fetch_sector_quote(_FakeAk(), sector_type="地域资金流"), {})

    def test_dragon_tiger_mapping(self):
        rows = fetcher.fetch_dragon_tiger("20260716", ak=_FakeAk())
        self.assertEqual(rows[0]["stock_code"], "000001")
        self.assertEqual(rows[0]["reason"], "日涨幅偏离值达7%")

    def test_dragon_tiger_no_data_returns_empty(self):
        # 无数据日期(盘前/休市/披露延迟)akshare 抛 TypeError → 空结果,不崩
        class _AkNoData:
            def stock_lhb_detail_em(self, start_date, end_date):
                raise TypeError("'NoneType' object is not subscriptable")
        self.assertEqual(fetcher.fetch_dragon_tiger("20260816", ak=_AkNoData()), [])

    def test_dragon_tiger_real_failure_propagates(self):
        # 真故障(网络/接口改版)仍抛出,由 cli 层记 ERROR,不能吞成空
        class _AkDown:
            def stock_lhb_detail_em(self, start_date, end_date):
                raise ConnectionError("network down")
        with self.assertRaises(ConnectionError):
            fetcher.fetch_dragon_tiger("20260816", ak=_AkDown())


if __name__ == "__main__":
    unittest.main()
