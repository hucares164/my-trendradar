#!/usr/bin/env python3
# coding=utf-8
"""
sync_simulation_to_feishu.py
----------------------------
将模拟盘验证数据同步到飞书多维表格。

数据源：
- A 股 → 腾讯财经公开 API（境内外均可访问）
- 美股 → Yahoo Finance v8 API（GitHub Actions 可用；本地测试可自动降级）

无外部 pip 依赖，纯 Python 标准库，可在 GitHub Actions 上独立运行。

配置（环境变量）：
  FEISHU_APP_ID             飞书应用 App ID
  FEISHU_APP_SECRET         飞书应用 App Secret
  FEISHU_BITABLE_APP_TOKEN  多维表格 App Token
  FEISHU_SIM_TABLE_ID       模拟盘专用表格 Table ID
"""

import json
import os
import sys
import re
import urllib.request
import time
import calendar
from datetime import datetime, timedelta
from typing import Optional


# ═══════════════════════════════════════════════
# 飞书多维表格客户端（纯标准库，零外部依赖）
# ═══════════════════════════════════════════════

class FeishuBitableClient:
    """轻量飞书多维表格客户端"""

    def __init__(self, app_id: str, app_secret: str, app_token: str, table_id: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self._token = ""
        self._token_expire = 0.0

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expire:
            return self._token
        data = json.dumps({
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
            if resp.get("code") == 0:
                self._token = resp["tenant_access_token"]
                self._token_expire = time.time() + resp.get("expire", 7188) - 60
                return self._token
            print(f"[飞书] 获取 token 失败: code={resp.get('code')} msg={resp.get('msg')}", file=sys.stderr)
        except Exception as e:
            print(f"[飞书] token 请求异常: {e}", file=sys.stderr)
        print("[飞书] 无法获取 tenant_access_token，退出", file=sys.stderr)
        sys.exit(1)

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    def _base_url(self) -> str:
        return f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.app_token}/tables/{self.table_id}"

    def list_all_record_ids(self) -> list:
        ids = []
        page_token = ""
        while True:
            url = f"{self._base_url()}/records?page_size=100"
            if page_token:
                url += f"&page_token={page_token}"
            req = urllib.request.Request(url, headers=self._auth_headers())
            try:
                resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
                if resp.get("code") != 0:
                    print(f"[飞书] list_records 失败: {resp.get('msg')}", file=sys.stderr)
                    break
                for item in resp.get("data", {}).get("items", []):
                    ids.append(item["record_id"])
                has_more = resp.get("data", {}).get("has_more", False)
                page_token = resp.get("data", {}).get("page_token", "")
                if not has_more:
                    break
            except Exception as e:
                print(f"[飞书] list_records 异常: {e}", file=sys.stderr)
                break
        return ids

    def batch_create(self, records: list) -> bool:
        chunk_size = 100
        success = True
        for i in range(0, len(records), chunk_size):
            chunk = records[i: i + chunk_size]
            url = f"{self._base_url()}/records/batch_create"
            data = json.dumps({"records": chunk}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=self._auth_headers(), method="POST")
            try:
                resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
                if resp.get("code") != 0:
                    print(f"[飞书] batch_create 失败: {resp.get('msg')}", file=sys.stderr)
                    success = False
                else:
                    count = len(resp.get("data", {}).get("records", []))
                    print(f"[飞书] 已写入 {count} 条", file=sys.stderr)
            except Exception as e:
                print(f"[飞书] batch_create 异常: {e}", file=sys.stderr)
                success = False
        return success

    def batch_delete(self, record_ids: list) -> bool:
        chunk_size = 100
        success = True
        for i in range(0, len(record_ids), chunk_size):
            chunk = record_ids[i: i + chunk_size]
            url = f"{self._base_url()}/records/batch_delete"
            data = json.dumps({"records": chunk}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=self._auth_headers(), method="POST")
            try:
                resp = json.loads(urllib.request.urlopen(req, timeout=20).read())
                if resp.get("code") != 0:
                    print(f"[飞书] batch_delete 失败: {resp.get('msg')}", file=sys.stderr)
                    success = False
                else:
                    print(f"[飞书] 已删除 {len(chunk)} 条记录", file=sys.stderr)
            except Exception as e:
                print(f"[飞书] batch_delete 异常: {e}", file=sys.stderr)
                success = False
        return success

    # 期望字段顺序：（名称, 飞书字段类型）
    #   1=文本  2=数字  5=日期
    DESIRED_FIELDS = [
        ("股票名称",        1),
        ("起始日期",        5),
        ("验证周期",        1),
        ("起始总价(×100股)", 2),
        ("累计盈亏(100股)",  2),
        ("预期方向/涨幅",   1),
        ("当前涨跌幅",      1),
        ("首日涨跌幅",      1),
        ("前3日涨跌幅",     1),
        ("前5日涨跌幅",     1),
        ("赛道",            1),    # 半导体芯片 / 光通信 / AI软件 等
        ("🔗网页版",        1),
    ]

    # 网页版模拟盘地址
    SIM_WEB_URL = "https://htmlpreview.github.io/?https://raw.githubusercontent.com/hucares164/my-trendradar/master/output/simulation/simulation_table.html"

    def _list_fields_raw(self) -> list:
        """获取字段原始列表 [{field_id, field_name, type}, ...]"""
        url = f"{self._base_url()}/fields"
        req = urllib.request.Request(url, headers=self._auth_headers())
        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
            if resp.get("code") == 0:
                return resp.get("data", {}).get("items", [])
            print(f"[飞书] get_fields: code={resp.get('code')} msg={resp.get('msg')}", file=sys.stderr)
        except Exception as e:
            print(f"[飞书] get_fields 异常: {e}", file=sys.stderr)
        return []

    def ensure_table_fields(self) -> list:
        """获取当前字段名称列表（兼容旧接口）"""
        fields_raw = self._list_fields_raw()
        fields = [f["field_name"] for f in fields_raw]
        print(f"[飞书] 当前表字段: {fields}", file=sys.stderr)
        return fields

    def reorder_fields_if_needed(self) -> bool:
        """
        检测字段顺序是否与期望一致；不一致则删除全部字段后按期望顺序重建。
        返回 True 表示执行了重建（调用方应确保表中无旧数据）。
        """
        current_fields = self._list_fields_raw()
        current_names = [f["field_name"] for f in current_fields]
        desired_names = [name for name, _ in self.DESIRED_FIELDS]

        # 同时检查名称顺序和字段类型
        names_match = current_names == desired_names
        types_match = all(
            f["type"] == self.DESIRED_FIELDS[i][1]
            for i, f in enumerate(current_fields)
            if i < len(self.DESIRED_FIELDS)
        )

        if names_match and types_match:
            print("[飞书] 字段顺序与类型均已正确，无需重排", file=sys.stderr)
            return False

        print(f"[飞书] ⚠️ 字段顺序不一致，开始重建...", file=sys.stderr)
        print(f"  当前: {current_names}", file=sys.stderr)
        print(f"  期望: {desired_names}", file=sys.stderr)

        # 第一步：删除非主字段（倒序，主字段不可删除）
        primary_field_id = current_fields[0]["field_id"] if current_fields else ""
        for f in reversed(current_fields):
            if f["field_id"] == primary_field_id:
                print(f"  ⊘ 跳过主字段（不可删除）: {f['field_name']}", file=sys.stderr)
                continue
            field_id = f["field_id"]
            del_url = f"{self._base_url()}/fields/{field_id}"
            try:
                req = urllib.request.Request(del_url, headers=self._auth_headers(), method="DELETE")
                resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
                if resp.get("code") == 0:
                    print(f"  ✓ 已删除: {f['field_name']}", file=sys.stderr)
                else:
                    print(f"  ✗ 删除失败 {f['field_name']}: {resp.get('msg')}", file=sys.stderr)
            except Exception as e:
                print(f"  ✗ 删除异常 {f['field_name']}: {e}", file=sys.stderr)

        # 第二步：按期望顺序重建（跳过已存在的主字段）
        existing_names = {f["field_name"] for f in self._list_fields_raw()}
        for name, ftype in self.DESIRED_FIELDS:
            if name in existing_names:
                print(f"  ⊘ 跳过已存在: {name}", file=sys.stderr)
                continue
            create_url = f"{self._base_url()}/fields"
            data = json.dumps({"field_name": name, "type": ftype}).encode("utf-8")
            try:
                req = urllib.request.Request(create_url, data=data, headers=self._auth_headers(), method="POST")
                resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
                if resp.get("code") == 0:
                    print(f"  ✓ 已创建: {name}", file=sys.stderr)
                else:
                    print(f"  ✗ 创建失败 {name}: {resp.get('msg')}", file=sys.stderr)
            except Exception as e:
                print(f"  ✗ 创建异常 {name}: {e}", file=sys.stderr)

        print("[飞书] 字段重排完成", file=sys.stderr)
        return True


# ═══════════════════════════════════════════════
# 行情数据获取（双数据源：腾讯 A 股 + Yahoo 美股）
# ═══════════════════════════════════════════════

# Yahoo 符号映射: westock 代码 → Yahoo ticker
YAHOO_MAP = {
    "usNVDA":    "NVDA",
    "usFUTU":    "FUTU",
    "usTIGR.OQ": "TIGR",
    "usMU":      "MU",
}

# 新浪美股符号映射: westock 代码 → 新浪 symbol
SINA_US_MAP = {
    "usFUTU":    "futu",
    "usTIGR.OQ": "tigr",
    "usNVDA":    "nvda",
    "usMU":      "mu",
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _fetch_kline_tencent(code: str, limit: int) -> list:
    """腾讯财经 A 股 K 线 API。返回 [{date, open, high, low, last}, ...]"""
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,{limit},qfq"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.qq.com"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if resp.get("code") != 0:
            return []
        klines = resp.get("data", {}).get(code, {}).get("qfqday") or []
        result = []
        for row in klines:
            if len(row) >= 5:
                # [date, open, close, high, low, volume]
                result.append({
                    "date": row[0],
                    "open": float(row[1]),
                    "last": float(row[2]),
                    "high": float(row[3]),
                    "low":  float(row[4]),
                })
        return result
    except Exception as e:
        print(f"  [腾讯] {code} 获取失败: {e}", file=sys.stderr)
        return []


def _fetch_kline_yahoo(symbol: str, limit: int) -> list:
    """Yahoo Finance v8 K 线 API。返回 [{date, open, high, low, last}, ...]"""
    # 根据 limit 选择 range（Yahoo 用 range 参数，不是 limit）
    if limit <= 30:
        rng = "1mo"
    elif limit <= 90:
        rng = "3mo"
    elif limit <= 180:
        rng = "6mo"
    else:
        rng = "1y"

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
        result = resp.get("chart", {}).get("result", [])
        if not result:
            return []

        r = result[0]
        timestamps = r.get("timestamp", [])
        quotes = r.get("indicators", {}).get("quote", [{}])[0]
        opens  = quotes.get("open", [])
        highs  = quotes.get("high", [])
        lows   = quotes.get("low", [])
        closes = quotes.get("close", [])

        rows = []
        for i in range(len(timestamps)):
            o = opens[i] if opens[i] is not None else 0
            h = highs[i] if highs[i] is not None else 0
            l = lows[i]  if lows[i]  is not None else 0
            c = closes[i] if closes[i] is not None else 0
            if c == 0:
                continue
            dt = time.strftime("%Y-%m-%d", time.gmtime(timestamps[i]))
            rows.append({"date": dt, "open": o, "high": h, "low": l, "last": c})
        return rows[-limit:] if len(rows) > limit else rows
    except Exception as e:
        print(f"  [Yahoo] {symbol} 获取失败: {e}", file=sys.stderr)
        return []


def _fetch_kline_sina_us(symbol: str, limit: int) -> list:
    """新浪财经美股 K 线 API（境内可用）。返回 [{date, open, high, low, last}, ...]"""
    url = f"http://stock.finance.sina.com.cn/usstock/api/json_v2.php/US_MinKService.getDailyK?symbol={symbol}&num={limit}&type=d"
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    try:
        raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
        rows = json.loads(raw)
        result = []
        for r in rows:
            result.append({
                "date": r["d"],
                "open": float(r["o"]),
                "high": float(r["h"]),
                "low":  float(r["l"]),
                "last": float(r["c"]),
            })
        return result
    except Exception as e:
        print(f"  [新浪] {symbol} 获取失败: {e}", file=sys.stderr)
        return []


def fetch_kline(code: str, limit: int = 60) -> list:
    """
    统一 K 线获取入口：
    - A 股 (sh/sz 开头) → 腾讯财经
    - 美股 (us 开头)    → Yahoo Finance → 新浪财经 → 腾讯兜底
    """
    is_us = code.startswith("us")

    if not is_us:
        return _fetch_kline_tencent(code, limit)

    # 美股：Yahoo → 新浪 → 腾讯
    yahoo_symbol = YAHOO_MAP.get(code, code.replace("us", "").replace(".OQ", ""))
    rows = _fetch_kline_yahoo(yahoo_symbol, limit)
    if rows:
        print(f"  [数据源] Yahoo Finance ({len(rows)} 条)", file=sys.stderr)
        return rows

    # Yahoo 失败 → 新浪（境内可用）
    sina_symbol = SINA_US_MAP.get(code, yahoo_symbol.lower())
    rows = _fetch_kline_sina_us(sina_symbol, limit)
    if rows:
        print(f"  [数据源] 新浪财经 ({len(rows)} 条)", file=sys.stderr)
        return rows

    # 都失败 → 腾讯
    print(f"  [数据源] Yahoo/新浪 不可用，降级到腾讯", file=sys.stderr)
    return _fetch_kline_tencent(code, limit)


# ═══════════════════════════════════════════════
# 数据计算逻辑
# ═══════════════════════════════════════════════

PRED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "output", "predictions", "predictions.json"
)

STOCK_ALIASES = {
    "中际旭创": ["中际旭创", "旭创"],
    "新易盛":   ["新易盛"],
    "中微公司": ["中微公司", "中微"],
    "北方华创": ["北方华创", "北方"],
    "沪硅产业": ["沪硅产业", "沪硅"],
    "中国石油": ["中国石油", "中石油"],
    "中油资本": ["中油资本"],
    "新国都":   ["新国都"],
    "兆易创新": ["兆易创新", "兆易"],
    "申昊科技": ["申昊科技"],
    "亿嘉和":   ["亿嘉和"],
    "神州数码": ["神州数码"],
    "拓维信息": ["拓维信息", "拓维"],
    "金山办公": ["金山办公", "金山"],
    "用友网络": ["用友网络", "用友"],
    "中国石化": ["中国石化", "中石化"],
    "精达股份": ["精达股份"],
    "比亚迪":   ["比亚迪", "BYD"],
}

STOCKS = [
    ("sz002371",  "北方华创"),
    ("sh688012",  "中微公司"),
    ("sh688126",  "沪硅产业"),
    ("sh601857",  "中国石油"),
    ("sz000617",  "中油资本"),
    ("sz300130",  "新国都"),
    ("sh603986",  "兆易创新"),
    ("sz300308",  "中际旭创"),
    ("sz300502",  "新易盛"),
    ("sz300853",  "申昊科技"),
    ("sh603666",  "亿嘉和"),
    ("sz000034",  "神州数码"),
    ("sz002261",  "拓维信息"),
    ("sh688111",  "金山办公"),
    ("sh600588",  "用友网络"),
    ("sh600028",  "中国石化"),
    ("sh600577",  "精达股份"),
    ("sz002594",  "比亚迪"),
]

# 股票赛道分类：{名称: 赛道}
STOCK_SECTOR = {
    "北方华创":  "半导体芯片",
    "中微公司":  "半导体芯片",
    "沪硅产业":  "半导体芯片",
    "兆易创新":  "半导体芯片",
    "中际旭创":  "光通信",
    "新易盛":    "光通信",
    "金山办公":  "AI软件",
    "用友网络":  "AI软件",
    "神州数码":  "AI软件",
    "拓维信息":  "AI软件",
    "申昊科技":  "机器人工控",
    "亿嘉和":    "机器人工控",
    "中国石油":  "能源石化",
    "中油资本":  "能源石化",
    "中国石化":  "能源石化",
    "精达股份":  "能源石化",
    "比亚迪":    "电动车",
    "新国都":    "金融科技",
}


def find_price_on_or_after(rows: list, target_date: str) -> Optional[dict]:
    """找 target_date 或之后最近的交易日；若都在之前则取最后一条"""
    if not rows:
        return None
    sorted_rows = sorted(rows, key=lambda x: x["date"])
    for r in sorted_rows:
        if r["date"] >= target_date:
            return r
    return sorted_rows[-1]


def calc_trading_days(start: str, end: str) -> int:
    """两个日期之间的日历天数（含起止日，跳过周末）"""
    try:
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end,   "%Y-%m-%d")
        days = 0
        cur = s
        while cur <= e:
            if cur.weekday() < 5:
                days += 1
            cur += timedelta(days=1)
        return max(days, 1)
    except Exception:
        return 0


def get_first_date_and_verify_date(preds: list, stock_name: str):
    """从预判数据中找到该股票首次出现的 date 和 verify_date"""
    aliases = STOCK_ALIASES.get(stock_name, [stock_name])
    for p in sorted(preds, key=lambda x: x.get("date", "")):
        content = p.get("content", "")
        if any(a in content for a in aliases):
            return p.get("date", ""), p.get("verify_date", "")
    return "", ""


def get_expected_range(preds: list, stock_name: str) -> str:
    """从预判内容提取预期：有数字→区间，否则→方向关键词"""
    aliases = STOCK_ALIASES.get(stock_name, [stock_name])
    for p in preds:
        content = p.get("content", "")
        if not any(a in content for a in aliases):
            continue
        m = re.search(r"(\d+\.?\d*)\s*%?\s*[~～]\s*(\d+\.?\d*)\s*%", content)
        if m:
            return f"{float(m.group(1)):.0f}%~{float(m.group(2)):.0f}%"
        singles = re.findall(r"(?:超过|上涨|涨幅|涨)\s*(\d+\.?\d*)\s*%", content)
        if singles:
            return f"+{float(singles[0]):.0f}%以上"
        all_nums = re.findall(r"(\d+\.?\d*)\s*%", content)
        nums = sorted(set(float(n) for n in all_nums))
        if len(nums) >= 2:
            return f"{nums[0]:.0f}%~{nums[-1]:.0f}%"
        elif len(nums) == 1:
            return f"±{nums[0]:.0f}%"
        if any(kw in content for kw in ["上涨", "看多", "看涨", "升", "反弹", "上行", "突破"]):
            return "📈 预期上涨"
        if any(kw in content for kw in ["下跌", "看空", "看跌", "降", "回落", "下行", "破位"]):
            return "📉 预期下跌"
        return "↔ 方向不明"
    return "—"


def build_daily_change_sequence(rows: list, start_date: str, verify_date: str) -> str:
    """
    每日涨跌文字序列。
    格式：06-02: 📈+1.20%  06-03: 📉-0.85%  ...
    起始日为基准不出条目。验证日期过后追加【验证已结束】。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    end_date = verify_date if verify_date < today else today

    sorted_rows = sorted(rows, key=lambda x: x["date"])
    window = [r for r in sorted_rows if r["date"] >= start_date and r["date"] <= end_date]

    if len(window) < 2:
        if today <= start_date:
            return "等待开盘…"
        return "数据不足"

    parts = []
    for j in range(1, len(window)):
        prev = window[j - 1]["last"]
        cur  = window[j]["last"]
        chg  = (cur - prev) / prev * 100 if prev else 0
        sign = "📈" if chg >= 0 else "📉"
        dt   = window[j]["date"][5:]
        parts.append(f"{dt}: {sign}{chg:+.2f}%")

    seq = "  ".join(parts)
    if today > verify_date:
        seq += "  【验证已结束】"
    return seq


def calc_5day_change(rows: list, start_date: str) -> str:
    """
    前 5 个交易日的涨跌幅（从起始日开始计，不足 5 日则为空）。
    返回格式如 '+3.21%' 或 '—'。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    sorted_rows = sorted(rows, key=lambda x: x["date"])
    window = [r for r in sorted_rows if r["date"] >= start_date and r["date"] <= today]

    # 跳过起始日，从第 1 日开始计数
    trading_days = len(window) - 1 if len(window) > 0 else 0
    if trading_days < 5:
        return "—"

    # 取第 5 个交易日（0-based index 从 1 开始，第 5 天是 window[5]）
    # window[0] 是起始日，window[1] 是第 1 日，...，window[5] 是第 5 日
    if len(window) <= 5:
        return "—"

    day5_price = window[5]["last"]
    start_price = window[0]["last"]
    chg = (day5_price - start_price) / start_price * 100 if start_price else 0
    pct_sign = "+" if chg >= 0 else ""
    return f"{pct_sign}{chg:.2f}%"


def calc_first_day_change(rows: list, start_date: str) -> str:
    """首个交易日的涨跌幅（不足1日返回 —）"""
    today = datetime.now().strftime("%Y-%m-%d")
    sorted_rows = sorted(rows, key=lambda x: x["date"])
    window = [r for r in sorted_rows if r["date"] >= start_date and r["date"] <= today]
    if len(window) < 2:
        return "—"
    day1_price = window[1]["last"]
    start_price = window[0]["last"]
    chg = (day1_price - start_price) / start_price * 100 if start_price else 0
    pct_sign = "+" if chg >= 0 else ""
    return f"{pct_sign}{chg:.2f}%"


def calc_3day_change(rows: list, start_date: str) -> str:
    """前 3 个交易日的累计涨跌幅（不足 3 日返回 —）"""
    today = datetime.now().strftime("%Y-%m-%d")
    sorted_rows = sorted(rows, key=lambda x: x["date"])
    window = [r for r in sorted_rows if r["date"] >= start_date and r["date"] <= today]
    if len(window) < 4:
        return "—"
    day3_price = window[3]["last"]
    start_price = window[0]["last"]
    chg = (day3_price - start_price) / start_price * 100 if start_price else 0
    pct_sign = "+" if chg >= 0 else ""
    return f"{pct_sign}{chg:.2f}%"


def date_to_timestamp(date_str: str) -> int:
    """YYYY-MM-DD → 毫秒时间戳（UTC，避免时区偏移）"""
    if not date_str:
        return int(time.time() * 1000)
    try:
        dt = time.strptime(date_str, "%Y-%m-%d")
        return calendar.timegm(dt) * 1000
    except ValueError:
        return int(time.time() * 1000)


# ═══════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════

def main():
    # ── 1. 环境变量 ──────────────────────────────
    app_id     = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    app_token  = os.environ.get("FEISHU_BITABLE_APP_TOKEN", "")
    table_id   = os.environ.get("FEISHU_SIM_TABLE_ID", "")

    if not all([app_id, app_secret, app_token, table_id]):
        print("❌ 缺少必要环境变量", file=sys.stderr)
        print("   FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_BITABLE_APP_TOKEN / FEISHU_SIM_TABLE_ID",
              file=sys.stderr)
        sys.exit(1)

    client = FeishuBitableClient(app_id, app_secret, app_token, table_id)

    # 确保字段顺序正确（不一致时重建，旧数据自动清除）
    client.reorder_fields_if_needed()
    client.ensure_table_fields()

    # ── 2. 读取预判数据 ──────────────────────────
    if not os.path.exists(PRED_PATH):
        print(f"❌ 预判文件不存在: {PRED_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(PRED_PATH, "r", encoding="utf-8") as f:
        pred_data = json.load(f)
    preds = pred_data.get("predictions", [])
    print(f"[数据] 共 {len(preds)} 条预判", file=sys.stderr)

    # ── 3. 拉取行情 & 计算指标 ───────────────────
    results = []
    today = datetime.now().strftime("%Y-%m-%d")

    for code, name in STOCKS:
        print(f"\n处理 {name}({code})...", file=sys.stderr)

        first_date, verify_date = get_first_date_and_verify_date(preds, name)
        if not first_date or not verify_date:
            print(f"  ⚠️  预判数据中未找到 {name}，跳过", file=sys.stderr)
            continue

        days_needed = (datetime.now() - datetime.strptime(first_date, "%Y-%m-%d")).days + 10
        limit = max(days_needed, 30)

        rows = fetch_kline(code, limit)
        if not rows:
            print(f"  ⚠️  K 线数据为空，跳过", file=sys.stderr)
            continue

        start_row = find_price_on_or_after(rows, first_date)
        if not start_row:
            print(f"  ⚠️  找不到起始价格，跳过", file=sys.stderr)
            continue

        start_price = start_row["last"]
        start_date  = start_row["date"]

        latest_row   = sorted(rows, key=lambda x: x["date"])[-1]
        latest_price = latest_row["last"]
        latest_date  = latest_row["date"]

        shares         = 100
        start_amount   = start_price * shares
        current_amount = latest_price * shares
        pnl            = current_amount - start_amount
        pct            = (latest_price - start_price) / start_price * 100 if start_price else 0
        trading_days   = calc_trading_days(start_date, verify_date)
        expected       = get_expected_range(preds, name)
        five_day_chg   = calc_5day_change(rows, start_date)
        first_day_chg  = calc_first_day_change(rows, start_date)
        three_day_chg  = calc_3day_change(rows, start_date)

        if today > verify_date:
            status = "已结束"
        elif today >= start_date:
            status = "验证中"
        else:
            status = "待开始"

        print(f"  ✓  ¥{start_price:.2f}→¥{latest_price:.2f} | {pct:+.2f}% | 首日:{first_day_chg} 3日:{three_day_chg} 5日:{five_day_chg} | {status}", file=sys.stderr)

        results.append({
            "code":           code,
            "name":           name,
            "start_date":     start_date,
            "verify_date":    verify_date,
            "start_amount":   start_amount,
            "pnl":            pnl,
            "pct":            pct,
            "trading_days":   trading_days,
            "expected":       expected,
            "first_day_chg":  first_day_chg,
            "three_day_chg":  three_day_chg,
            "five_day_chg":   five_day_chg,
            "status":         status,
        })

    if not results:
        print("\n❌ 无有效股票数据，退出", file=sys.stderr)
        sys.exit(1)

    # ── 4. 清空旧记录 ────────────────────────────
    print("\n[飞书] 清空旧记录...", file=sys.stderr)
    old_ids = client.list_all_record_ids()
    if old_ids:
        client.batch_delete(old_ids)

    # ── 5. 构建写入记录 ──────────────────────────
    records = []
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 首行元信息
    records.append({
        "fields": {
            "股票名称":            f"📊 更新于 {updated_at}",
            "起始日期":            date_to_timestamp(today),
            "验证周期":           "元信息",
            "起始总价(×100股)":    0.0,
            "累计盈亏(100股)":     0.0,
            "预期方向/涨幅":       "—",
            "当前涨跌幅":          "—",
            "首日涨跌幅":          "—",
            "前3日涨跌幅":         "—",
            "前5日涨跌幅":         "—",
            "赛道":               "—",
            "🔗网页版":            FeishuBitableClient.SIM_WEB_URL,
        }
    })

    for r in results:
        pct_sign = "+" if r["pct"] >= 0 else ""
        sector = STOCK_SECTOR.get(r["name"], "—")

        records.append({
            "fields": {
                "股票名称":            r["name"],
                "起始日期":            date_to_timestamp(r["start_date"]),
                "验证周期":            f"{r['trading_days']} 天",
                "起始总价(×100股)":    round(r["start_amount"], 2),
                "累计盈亏(100股)":     round(r["pnl"], 2),
                "预期方向/涨幅":       r["expected"],
                "当前涨跌幅":          f"{pct_sign}{r['pct']:.2f}%",
                "首日涨跌幅":          r["first_day_chg"],
                "前3日涨跌幅":         r["three_day_chg"],
                "前5日涨跌幅":         r["five_day_chg"],
                "赛道":               sector,
                "🔗网页版":            FeishuBitableClient.SIM_WEB_URL,
            }
        })

    # ── 6. 批量写入 ──────────────────────────────
    print(f"\n[飞书] 写入 {len(records)} 条记录（含首行元信息）...", file=sys.stderr)
    success = client.batch_create(records)

    if success:
        print(f"\n✅ 飞书多维表格同步完成！共 {len(results)} 只股票", file=sys.stderr)
    else:
        print("\n❌ 部分记录写入失败", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
