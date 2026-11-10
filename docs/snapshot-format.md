# 行情快照格式 v1

通过界面的“下载完整快照”查看可用的合成样例。真实数据必须明确来源、日期、口径，不可把合成数据改名成真实行情。

最小结构：

```json
{
  "schema_version": 1,
  "meta": {
    "trade_date": "2026-10-23",
    "mode": "import",
    "source": "用户提供的行情来源名称",
    "fetched_at": "2026-10-23T17:00:00+08:00",
    "currency": "CNY",
    "calendar": ["2026-10-23"],
    "calendar_source": "所用交易日历来源",
    "universe": "实际覆盖范围",
    "expected_count": 1,
    "complete": false,
    "notes": ["本样例只有一根日线，不能产生完整技术评分"]
  },
  "stocks": [{
    "code": "600000.SH",
    "name": "请填写真实证券名称",
    "board": "主板",
    "list_date": "1999-11-10",
    "is_st": false,
    "bars": [{
      "date": "2026-10-23",
      "open": 10.0,
      "high": 10.5,
      "low": 9.9,
      "close": 10.2,
      "pre_close": 10.0,
      "pct_chg": 2.0,
      "volume_shares": 10000000,
      "amount_yuan": 102000000,
      "turnover_rate": 3.1,
      "adj_factor": 1.0,
      "up_limit": 11.0,
      "down_limit": 9.0
    }],
    "limit": {"status": "N", "consecutive": 0},
    "previous_limit": {},
    "verification": {"status": "single_source"},
    "announcements": []
  }],
  "sectors": [{
    "id": "example-industry",
    "name": "示例分类",
    "type": "industry",
    "classification": "提供成分的供应商与分类版本",
    "asof_date": "2026-10-23",
    "members": ["600000.SH"],
    "complete": false
  }]
}
```

**上面的价格是格式说明用的虚构值，不是600000的真实行情。** 完整评分需要至少60根有效日线和可用复权数据；MACD至少120根。每个板块至少5只可用成分，并通过完整度检查。

所有日期是上海市场日期；bars升序、不能重复或晚于分析日。calendar必须包含所有日线日期及分析日。行情为元/股，成交量为股，成交额为元，百分比字段3代表3%。adj_factor须为正数；缺失填写null，不能随意填1。up_limit/down_limit是当日真实价格限制，不能统一按固定比例虚构。

`limit.status`：U收盘涨停、D收盘跌停、Z触板后收盘未封、N确认未触板；不清楚留空。`consecutive`必须是连续收盘涨停数，不能将N天M板的M填入。`fd_amount`如提供，单位为元，并在meta中注明15:00时点及来源。

`sectors`必须使用当日保存的成分关系；industry与theme分别比较。`complete`是提供者对成分完整性的声明，软件还会检查缺失代码、报价和可评分数量。

独立参考收盘CSV：

```csv
code,close,date
600000.SH,真实收盘价,2026-10-23
```

界面选择来源名称与日期后导入。仅核对同日收盘价，结果为matched或conflict。未核对的字段不会被当成已验证。
