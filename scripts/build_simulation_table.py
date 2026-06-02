#!/usr/bin/env python3
"""
模拟盘验证表生成脚本 v3
修复：
- 验证周期 = verify_date - date（从 predictions.json 直接读取，固定天数）
- 非交易日自动顺延到最近一个交易日
- 美股使用 $ 符号，A股/港股使用 ¥
- 预期涨幅区间提取改进
- 新增"最终涨跌幅区间"列
"""

import json
import subprocess
import sys
import os
import re
from datetime import datetime, timedelta

SCRIPT = "/Users/luominyi/.workbuddy/plugins/marketplaces/cb_teams_marketplace/plugins/finance-data/skills/westock-data/scripts/index.js"
PRED_PATH = "/Users/luominyi/WorkBuddy/2026-05-28-19-34-18/my-trendradar/output/predictions/predictions.json"

# 股票列表：(westock代码, 名称)
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
    ("usFUTU",    "富途控股"),
    ("usTIGR.OQ", "老虎证券"),
    ("usNVDA",    "英伟达"),
    ("usMU",      "美光科技"),
]

# 股票分类：{名称: (赛道, 市场)}
# 市场：A股(沪市/深市/科创板) / 美股 / 港股
# 赛道：半导体芯片 / 光通信 / AI软件 / 能源石化 / 电动车 / 金融科技 / 机器人工控
STOCK_META = {
    "北方华创":  ("半导体芯片", "A股"),
    "中微公司":  ("半导体芯片", "A股"),
    "沪硅产业":  ("半导体芯片", "A股"),
    "兆易创新":  ("半导体芯片", "A股"),
    "英伟达":    ("半导体芯片", "美股"),
    "美光科技":  ("半导体芯片", "美股"),
    "中际旭创":  ("光通信",     "A股"),
    "新易盛":    ("光通信",     "A股"),
    "金山办公":  ("AI软件",     "A股"),
    "用友网络":  ("AI软件",     "A股"),
    "神州数码":  ("AI软件",     "A股"),
    "拓维信息":  ("AI软件",     "A股"),
    "申昊科技":  ("机器人工控", "A股"),
    "亿嘉和":    ("机器人工控", "A股"),
    "中国石油":  ("能源石化",   "A股"),
    "中油资本":  ("能源石化",   "A股"),
    "中国石化":  ("能源石化",   "A股"),
    "精达股份":  ("能源石化",   "A股"),
    "比亚迪":    ("电动车",     "A股"),
    "新国都":    ("金融科技",   "A股"),
    "富途控股":  ("金融科技",   "美股"),
    "老虎证券":  ("金融科技",   "美股"),
}

STOCK_ALIASES = {
    "北方华创":   ["北方华创"],
    "中微公司":   ["中微公司", "中微"],
    "沪硅产业":   ["沪硅产业"],
    "中国石油":   ["中国石油", "中石油", "石油"],
    "中油资本":   ["中油资本"],
    "新国都":    ["新国都"],
    "兆易创新":   ["兆易创新"],
    "中际旭创":   ["中际旭创", "旭创"],
    "新易盛":    ["新易盛"],
    "申昊科技":   ["申昊科技"],
    "亿嘉和":    ["亿嘉和"],
    "神州数码":   ["神州数码"],
    "拓维信息":   ["拓维信息", "拓维"],
    "金山办公":   ["金山办公", "WPS"],
    "用友网络":   ["用友网络", "用友"],
    "中国石化":   ["中国石化", "中石化"],
    "精达股份":   ["精达股份", "精达"],
    "比亚迪":     ["比亚迪"],
    "富途控股":   ["富途"],
    "老虎证券":   ["老虎证券", "老虎"],
    "英伟达":    ["英伟达", "Nvidia", "NVDA"],
    "美光科技":   ["美光", "Micron"],
}


def run_westock(args: list) -> str:
    result = subprocess.run(
        ["node", SCRIPT] + args,
        capture_output=True, text=True, timeout=20,
        cwd=os.path.dirname(SCRIPT)
    )
    return result.stdout.strip()


def parse_kline_markdown(text: str) -> list:
    """解析 westock-data kline 输出的 Markdown 表格，返回完整行数据"""
    rows = []
    in_table = False
    for line in text.split("\n"):
        line = line.strip()
        if "date" in line and "last" in line and "|" in line:
            in_table = True
            continue
        if in_table:
            if line.startswith("|") and "---" not in line:
                parts = [c.strip() for c in line.split("|")]
                if len(parts) >= 6:
                    try:
                        dt    = parts[1]
                        opn   = float(parts[2]) if parts[2] else 0
                        lst   = float(parts[3]) if parts[3] else 0
                        high  = float(parts[4]) if parts[4] else 0
                        low   = float(parts[5]) if parts[5] else 0
                        rows.append({"date": dt, "open": opn, "high": high, "low": low, "last": lst})
                    except (ValueError, IndexError):
                        pass
            elif not line.startswith("|"):
                in_table = False
    return rows


def find_start_row(rows: list, target_date: str) -> dict | None:
    """
    找 target_date 或之后最近一个交易日的行。
    若 target_date 非交易日，自动顺延到最近的交易日。
    """
    if not rows:
        return None
    sorted_rows = sorted(rows, key=lambda x: x["date"])
    for r in sorted_rows:
        if r["date"] >= target_date:
            return r
    return sorted_rows[-1]


def calc_trading_days_fixed(start: str, verify: str) -> int:
    """
    计算验证周期 = verify_date - date（固定天数，跳过周末）
    这是预判录入时就定好的天数，不是用最新日期减。
    """
    try:
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(verify, "%Y-%m-%d")
        days = 0
        cur = s
        while cur <= e:
            if cur.weekday() < 5:
                days += 1
            cur += timedelta(days=1)
        return max(days, 1)
    except Exception:
        return 0


def get_expected_range(preds: list, stock_name: str) -> str:
    """从预判内容中提取预期涨幅：有明确数字则显示区间，否则显示方向（上涨/下跌）"""
    aliases = STOCK_ALIASES.get(stock_name, [stock_name])
    for p in preds:
        content = p.get("content", "")
        if not any(a in content for a in aliases):
            continue
        # 模式1：X%~Y% 区间
        m = re.search(r"(\d+\.?\d*)\s*%?\s*[~～]\s*(\d+\.?\d*)\s*%", content)
        if m:
            return f"{float(m.group(1)):.0f}%~{float(m.group(2)):.0f}%"
        # 模式2：超过X% / 上涨X% / 涨幅X%
        singles = re.findall(r"(?:超过|上涨|涨幅|涨)\s*(\d+\.?\d*)\s*%", content)
        if singles:
            return f"+{float(singles[0]):.0f}%以上"
        # 模式3：兜底，抓所有百分数
        all_nums = re.findall(r"(\d+\.?\d*)\s*%", content)
        nums = sorted(set(float(n) for n in all_nums))
        if len(nums) >= 2:
            return f"{nums[0]:.0f}%~{nums[-1]:.0f}%"
        elif len(nums) == 1:
            return f"±{nums[0]:.0f}%"
        # 无明确数字 → 判断方向关键词
        if any(kw in content for kw in ["上涨", "涨", "看多", "看涨", "升", "反弹", "上行", "突破"]):
            return "📈 预期上涨"
        if any(kw in content for kw in ["下跌", "跌", "看空", "看跌", "降", "回落", "下行", "破位"]):
            return "📉 预期下跌"
        return "↔ 方向不明"
    return "—"


def calc_5day_change(all_rows: list, start_date: str) -> str:
    """
    前 5 个交易日的涨跌幅（从起始日开始计，不足 5 日则为空）。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    sorted_rows = sorted(all_rows, key=lambda x: x["date"])
    window = [r for r in sorted_rows if r["date"] >= start_date and r["date"] <= today]

    trading_days = len(window) - 1 if len(window) > 0 else 0
    if trading_days < 5 or len(window) <= 5:
        return "—"

    day5_price = window[5]["last"]
    start_price = window[0]["last"]
    chg = (day5_price - start_price) / start_price * 100 if start_price else 0
    pct_sign = "+" if chg >= 0 else ""
    return f"{pct_sign}{chg:.2f}%"


def gen_mini_bar(code: str, start_date: str, verify_date: str, all_rows: list) -> str:
    """
    生成从 start_date 到 min(今天, verify_date) 的每日涨跌柱状图。
    all_rows 是已拉取的 K 线数据（避免重复请求）。
    - 每根柱 = 当日收盘 vs 前日收盘的涨跌幅
    - 起始日是基准，从下一个交易日开始出柱
    - 验证日期已过则截止到 verify_date
    """
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        end_date = min(verify_date, today)   # 截止日期：验证日 or 今天，取早的

        # 从 start_date 前一天开始（需要前一日收盘价作为基准），过滤到 end_date
        sorted_rows = sorted(all_rows, key=lambda x: x["date"])
        # 找 start_date 当天（作为第0根基准，不出柱）
        # 如果 start_date 非交易日已顺延，这里直接用 start_date（已是真实交易日）
        window = [r for r in sorted_rows if r["date"] >= start_date and r["date"] <= end_date]

        if len(window) < 2:
            # 数据太少，只有起始日一条，无法出柱
            return '<div class="bar-container"><span style="color:#ccc;font-size:11px">等待数据…</span></div>'

        bars = []
        for j in range(1, len(window)):
            prev = window[j - 1]["last"]
            cur  = window[j]["last"]
            chg  = (cur - prev) / prev * 100 if prev else 0
            height = min(abs(chg) * 4, 36)   # 缩放系数4，最高36px
            height = max(height, 2)           # 最低2px，保证可见
            cls = "bar-pos" if chg >= 0 else "bar-neg"
            dt  = window[j]["date"]
            bars.append(f'<div class="bar {cls}" style="height:{height:.1f}px" title="{dt}: {chg:+.2f}%"></div>')

        if not bars:
            return "—"
        return '<div class="bar-container">' + "".join(bars) + "</div>"
    except Exception:
        return "—"


def gen_html(results: list, path: str):
    # 赛道颜色映射
    SECTOR_COLORS = {
        "半导体芯片": "#6c5ce7",
        "光通信":     "#00b894",
        "AI软件":     "#0984e3",
        "机器人工控": "#e17055",
        "能源石化":   "#fdcb6e",
        "电动车":     "#00cec9",
        "金融科技":   "#fd79a8",
    }
    # 市场标签颜色
    MKT_COLORS = {
        "A股": "#d63031",
        "美股": "#0984e3",
        "港股": "#6c5ce7",
    }

    rows_html = ""
    for r in results:
        code   = r["code"]
        is_us = code.startswith("us")
        sym    = "$" if is_us else "¥"
        pnl_class = "pnl-positive" if r["pnl"] >= 0 else "pnl-negative"
        pnl_sign  = "+" if r["pnl"] >= 0 else ""
        pct_sign  = "+" if r["pct"] >= 0 else ""
        bar_html  = gen_mini_bar(code, r["start_date"], r["verify_date"], r["all_rows"])
        # 解析前5日涨跌幅数值用于颜色判断
        five_day_chg_val = None
        try:
            five_day_str = r.get('five_day_chg', '—')
            if five_day_str and five_day_str != '—' and '%' in five_day_str:
                five_day_chg_val = float(five_day_str.replace('%', '').replace('+', ''))
        except (ValueError, TypeError):
            pass
        if five_day_chg_val is not None and five_day_chg_val >= 0:
            five_day_class = "pnl-positive"
        elif five_day_chg_val is not None:
            five_day_class = "pnl-negative"
        else:
            five_day_class = ""

        # 赛道 & 市场标签
        sector, market = STOCK_META.get(r["name"], ("—", "—"))
        s_color = SECTOR_COLORS.get(sector, "#636e72")
        m_color = MKT_COLORS.get(market, "#636e72")
        sector_tag = f'<span class="tag" style="background:{s_color}">{sector}</span>'
        market_tag = f'<span class="tag" style="background:{m_color}">{market}</span>'

        rows_html += f"""
        <tr>
            <td>{r['name']}<br><small>{code}</small><br>{market_tag}{sector_tag}</td>
            <td>{r['start_date']}</td>
            <td>{r['trading_days']}天<br><small>→ {r['verify_date']}</small></td>
            <td><b>{sym}{r['start_amount']:.2f}</b></td>
            <td class="{pnl_class}">{pnl_sign}{r['pnl']:.2f}<br><small>{pct_sign}{r['pct']:.2f}%</small></td>
            <td>{r['expected']}</td>
            <td class="{pnl_class}">{pct_sign}{r['pct']:.2f}%</td>
            <td class="{five_day_class}">{r.get('five_day_chg', '—')}</td>
            <td>{bar_html}</td>
        </tr>"""

    total_pnl = sum(r["pnl"] for r in results)
    pnl_tag   = "pos" if total_pnl >= 0 else "neg"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>模拟盘验证表 v3</title>
<style>
    body       {{ font-family: -apple-system, 'PingFang SC', sans-serif; background: #f5f6fa; margin: 0; padding: 20px; }}
    h1         {{ color: #333; margin-bottom: 4px; }}
    .subtitle  {{ color: #888; font-size: 13px; margin-bottom: 16px; }}
    table      {{ border-collapse: collapse; width: 100%; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
    th         {{ background: #2c3e50; color: white; padding: 9px 11px; text-align: left; font-size: 12.5px; }}
    td         {{ padding: 7px 11px; border-bottom: 1px solid #eee; font-size: 12.5px; }}
    tr:hover   {{ background: #f8f9ff; }}
    .pnl-positive {{ color: #e74c3c; font-weight: bold; }}
    .pnl-negative {{ color: #27ae60; font-weight: bold; }}
    .bar-container {{ display: flex; align-items: flex-end; gap: 2px; height: 38px; }}
    .bar       {{ width: 6px; border-radius: 2px 2px 0 0; min-height: 2px; }}
    .bar-pos   {{ background: #e74c3c; }}
    .bar-neg   {{ background: #27ae60; }}
    .summary   {{ display: flex; gap: 16px; margin: 16px 0; flex-wrap: wrap; }}
    .card      {{ background: white; padding: 14px 22px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); min-width: 110px; }}
    .card h3   {{ margin: 0; color: #999; font-size: 11.5px; font-weight: normal; }}
    .card .val {{ font-size: 26px; font-weight: bold; margin: 2px 0 0; }}
    .val.pos   {{ color: #e74c3c; }}
    .val.neg   {{ color: #27ae60; }}
    .footer    {{ margin-top: 18px; color: #aaa; font-size: 12px; }}
    .tag       {{ display: inline-block; font-size: 10px; color: white; padding: 1px 5px; border-radius: 3px; margin: 1px 1px 0 0; font-weight: normal; }}
</style>
</head>
<body>
<h1>📊 模拟盘验证表</h1>
<p class="subtitle">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M")} ｜ 数据来源: westock-data ｜ 每支股票模拟买入 100 股</p>
<div class="summary">
    <div class="card"><h3>追踪股票</h3><div class="val">{len(results)}</div></div>
    <div class="card"><h3>盈利数</h3><div class="val pos">{sum(1 for r in results if r['pnl'] >= 0)}</div></div>
    <div class="card"><h3>亏损数</h3><div class="val neg">{sum(1 for r in results if r['pnl'] < 0)}</div></div>
    <div class="card"><h3>总盈亏</h3><div class="val {pnl_tag}">¥{total_pnl:+.2f}</div></div>
</div>
<table>
    <thead>
        <tr>
            <th>股票</th><th>起始日期</th><th>验证周期</th><th>起始总价<br>(×100股)</th>
            <th>累计盈亏<br>(100股)</th><th>预期方向/涨幅</th><th>当前涨跌幅</th><th>前5日涨跌幅</th><th>每日涨跌</th>
        </tr>
    </thead>
    <tbody>
        {rows_html}
    </tbody>
</table>
<p class="footer">■ 红色=涨(盈) ｜ ■ 绿色=跌(亏) ｜ 非交易日自动顺延 ｜ 美股报价货币为美元($) ｜ 验证周期 = verify_date - date（固定天数）</p>
</body>
</html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    with open(PRED_PATH) as f:
        data = json.load(f)
    preds = data.get("predictions", [])

    # 建立：股票名称 -> 首次 date + verify_date（取最早出现的那条预判）
    stock_dates = {}
    for p in preds:
        content = p.get("content", "")
        d       = p.get("date", "")
        vd      = p.get("verify_date", "")
        for name, aliases in STOCK_ALIASES.items():
            if any(a in content for a in aliases):
                if name not in stock_dates:
                    stock_dates[name] = (d, vd)

    results = []

    for code, name in STOCKS:
        if name not in stock_dates:
            print(f"⚠️  {name} 在 predictions.json 中无匹配预判，跳过", file=sys.stderr)
            continue

        first_date, verify_date = stock_dates[name]
        if not verify_date:
            print(f"⚠️  {name} 无 verify_date，跳过", file=sys.stderr)
            continue

        is_us = code.startswith("us")
        sym = "$" if is_us else "¥"
        print(f"处理: {name} ({code})，首次: {first_date}，验证: {verify_date}...", file=sys.stderr)

        stdout = run_westock(["kline", code, "--period", "day", "--limit", "120", "--fq", "qfq"])
        rows = parse_kline_markdown(stdout)
        if not rows:
            print(f"  ⚠️  无K线数据，跳过", file=sys.stderr)
            continue

        start_row = find_start_row(rows, first_date)
        if not start_row:
            print(f"  ⚠️  找不到起始数据，跳过", file=sys.stderr)
            continue

        start_price = start_row["last"]
        start_date  = start_row["date"]

        rows_desc   = sorted(rows, key=lambda x: x["date"], reverse=True)
        latest_row  = rows_desc[0]
        latest_price = latest_row["last"]
        latest_date  = latest_row["date"]

        # 最终涨跌幅：最新价相比起始价的涨幅（单一值）
        final_pct   = (latest_price - start_price) / start_price * 100 if start_price else 0
        final_range = f"{final_pct:+.2f}%"

        shares        = 100
        start_amount  = round(start_price * shares, 2)
        current_amount = round(latest_price * shares, 2)
        pnl  = round(current_amount - start_amount, 2)
        pct  = round((latest_price - start_price) / start_price * 100, 2) if start_price else 0

        # 验证周期 = verify_date - first_date（固定天数）
        trading_days = calc_trading_days_fixed(first_date, verify_date)
        expected     = get_expected_range(preds, name)
        five_day_chg = calc_5day_change(rows, start_date)

        results.append({
            "code":          code,
            "name":          name,
            "first_date":    first_date,
            "start_date":    start_date,
            "verify_date":   verify_date,
            "start_price":   start_price,
            "latest_date":   latest_date,
            "latest_price":  latest_price,
            "shares":        shares,
            "start_amount":   start_amount,
            "current_amount": current_amount,
            "pnl":           pnl,
            "pct":           pct,
            "trading_days":  trading_days,
            "expected":      expected,
            "final_range":   final_range,
            "five_day_chg":  five_day_chg,
            "all_rows":      rows,
        })
        print(f"  ✓  起始={start_date} {sym}{start_price:.2f} → 最新={latest_date} {sym}{latest_price:.2f}，"
              f"涨跌={pct:+.2f}%，PNL={sym}{pnl:+.2f}，"
              f"验证周期={trading_days}天，区间={final_range}", file=sys.stderr)

    # 输出汇总
    print("\n" + "=" * 80)
    print("模拟盘验证结果")
    print("=" * 80)
    for r in results:
        code   = r["code"]
        is_us  = code.startswith("us")
        sym    = "$" if is_us else "¥"
        arrow  = "📈" if r["pnl"] >= 0 else "📉"
        print(f'{arrow} {r["name"]}({code}) | 起始:{r["start_date"]} {sym}{r["start_price"]:.2f} '
              f'| 最新:{r["latest_date"]} {sym}{r["latest_price"]:.2f} '
              f'| 100股: {sym}{r["start_amount"]:.0f}→{sym}{r["current_amount"]:.0f} '
              f'| PNL: {sym}{r["pnl"]:+.2f} ({r["pct"]:+.2f}%) '
              f'| 验证:{r["trading_days"]}天 | 预期:{r["expected"]} | 区间:{r["final_range"]}')

    # 保存 CSV
    csv_path = "/Users/luominyi/WorkBuddy/2026-05-28-19-34-18/my-trendradar/output/simulation/simulation_table.csv"
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("股票代码,股票名称,首次预判日期,起始日期,验证周期(天),起始总价(×100股),累计盈亏(100股),累计涨幅,预期涨幅区间,前5日涨跌幅\n")
        for r in results:
            f.write(f'{r["code"]},{r["name"]},{r["first_date"]},{r["start_date"]},{r["trading_days"]},'
                    f'{r["start_amount"]},{r["pnl"]},{r["pct"]},'
                    f'{r["expected"]},{r["five_day_chg"]}\n')
    print(f"\nCSV 已保存: {csv_path}", file=sys.stderr)

    # 生成 HTML
    html_path = "/Users/luominyi/WorkBuddy/2026-05-28-19-34-18/my-trendradar/output/simulation/simulation_table.html"
    gen_html(results, html_path)
    print(f"HTML 已保存: {html_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
