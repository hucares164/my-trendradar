#!/usr/bin/env python3
"""
模拟盘验证表生成脚本 v5
新增：
- 「信号类型」「置信度」从预判数据同步
- 「1日/3日/5日/10日/20日涨跌」（交易日维度）
- 「信息补充」：起始日期后新增的相关预判内容
- 重命名字段：首日→1日、前3日→3日、前5日→5日、当前涨跌幅→当前累计涨跌幅
"""

import json
import subprocess
import sys
import os
import re
from datetime import datetime, timedelta

SCRIPT = "/Users/luominyi/.workbuddy/plugins/marketplaces/cb_teams_marketplace/plugins/finance-data/skills/westock-data/scripts/index.js"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
PRED_PATH = os.path.join(REPO_ROOT, "output", "predictions", "predictions.json")

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
]

# 股票赛道分类：{名称: 赛道}
# 赛道：半导体芯片 / 光通信 / AI软件 / 能源石化 / 电动车 / 金融科技 / 机器人工控
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


def get_signal_info(preds: list, stock_name: str) -> tuple:
    """
    从预判数据中提取：信号类型、置信度。
    返回 (signal_type, confidence)，未匹配返回 ("—", "—")
    """
    aliases = STOCK_ALIASES.get(stock_name, [stock_name])
    for p in preds:
        content = p.get("content", "")
        if any(a in content for a in aliases):
            return p.get("signal_type", "—"), p.get("confidence", "—")
    return "—", "—"


def get_info_supplement(preds: list, stock_name: str, first_date: str) -> str:
    """
    扫描起始日期之后新增的、提及该股票的预判内容。
    拼接为多条信息补充，用「｜」分隔。
    """
    aliases = STOCK_ALIASES.get(stock_name, [stock_name])
    supplements = []
    for p in sorted(preds, key=lambda x: x.get("date", "")):
        content = p.get("content", "")
        pdate = p.get("date", "")
        # 只取起始日期之后的预判
        if pdate <= first_date:
            continue
        if any(a in content for a in aliases):
            # 截取关键内容（去除过长文本）
            short = content[:80] + "…" if len(content) > 80 else content
            supplements.append(f"[{pdate}] {short}")
    return " ｜ ".join(supplements) if supplements else "—"


def calc_nday_change(all_rows: list, start_date: str, n: int) -> str:
    """
    前 N 个交易日的累计涨跌幅（从起始日开始计）。
    N=1: 第1个交易日; N=3: 第3个交易日; N=5: 第5个交易日...
    不足 N 个交易日返回 "—"
    """
    today = datetime.now().strftime("%Y-%m-%d")
    sorted_rows = sorted(all_rows, key=lambda x: x["date"])
    window = [r for r in sorted_rows if r["date"] >= start_date and r["date"] <= today]

    if len(window) <= n:
        return "—"

    day_price = window[n]["last"]
    start_price = window[0]["last"]
    chg = (day_price - start_price) / start_price * 100 if start_price else 0
    pct_sign = "+" if chg >= 0 else ""
    return f"{pct_sign}{chg:.2f}%"


def calc_1day_change(all_rows: list, start_date: str) -> str:
    """第1个交易日的涨跌幅"""
    return calc_nday_change(all_rows, start_date, 1)


def calc_3day_change(all_rows: list, start_date: str) -> str:
    """前3个交易日累计涨跌幅"""
    return calc_nday_change(all_rows, start_date, 3)


def calc_5day_change(all_rows: list, start_date: str) -> str:
    """前5个交易日累计涨跌幅"""
    return calc_nday_change(all_rows, start_date, 5)


def calc_10day_change(all_rows: list, start_date: str) -> str:
    """前10个交易日累计涨跌幅"""
    return calc_nday_change(all_rows, start_date, 10)


def calc_20day_change(all_rows: list, start_date: str) -> str:
    """前20个交易日累计涨跌幅"""
    return calc_nday_change(all_rows, start_date, 20)


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

    def _pct_class(val_str: str) -> str:
        """给定涨跌幅字符串，返回 CSS class"""
        if not val_str or val_str == "—":
            return ""
        try:
            v = float(val_str.replace("%", "").replace("+", ""))
            return "pnl-positive" if v >= 0 else "pnl-negative"
        except (ValueError, TypeError):
            return ""

    rows_html = ""
    for r in results:
        code   = r["code"]
        pnl_class = "pnl-positive" if r["pnl"] >= 0 else "pnl-negative"
        pnl_sign  = "+" if r["pnl"] >= 0 else ""
        pct_sign  = "+" if r["pct"] >= 0 else ""

        # 涨跌幅颜色
        day1_class  = _pct_class(r.get("day1_chg", "—"))
        day3_class  = _pct_class(r.get("day3_chg", "—"))
        day5_class  = _pct_class(r.get("day5_chg", "—"))
        day10_class = _pct_class(r.get("day10_chg", "—"))
        day20_class = _pct_class(r.get("day20_chg", "—"))

        # 赛道标签
        sector = STOCK_SECTOR.get(r["name"], "—")
        s_color = SECTOR_COLORS.get(sector, "#636e72")
        sector_tag = f'<span class="tag" style="background:{s_color}">{sector}</span>'

        # 置信度标签
        conf = r.get("confidence", "—")
        conf_colors = {"高": "#27ae60", "中": "#f39c12", "低": "#e74c3c"}
        conf_color = conf_colors.get(conf, "#95a5a6")
        conf_tag = f'<span class="tag" style="background:{conf_color}">{conf}</span>' if conf != "—" else "—"

        rows_html += f"""
        <tr>
            <td>{r['name']}<br><small>{code}</small><br>{sector_tag}</td>
            <td>{r['start_date']}</td>
            <td><b>{r.get('signal_type', '—')}</b></td>
            <td>{conf_tag}</td>
            <td>{r['trading_days']}天<br><small>→ {r['verify_date']}</small></td>
            <td>{r['expected']}</td>
            <td><b>¥{r['start_amount']:.2f}</b></td>
            <td class="{pnl_class}">{pnl_sign}{r['pnl']:.2f}<br><small>{pct_sign}{r['pct']:.2f}%</small></td>
            <td class="{day1_class}">{r.get('day1_chg', '—')}</td>
            <td class="{day3_class}">{r.get('day3_chg', '—')}</td>
            <td class="{day5_class}">{r.get('day5_chg', '—')}</td>
            <td class="{day10_class}">{r.get('day10_chg', '—')}</td>
            <td class="{day20_class}">{r.get('day20_chg', '—')}</td>
            <td class="{pnl_class}">{pct_sign}{r['pct']:.2f}%</td>
            <td>{sector_tag}</td>
            <td><a href="{FeishuBitableClient.SIM_WEB_URL if False else '#'}" target="_blank">🔗</a></td>
            <td style="max-width:240px;font-size:11px;white-space:normal">{r.get('info_supplement', '—')}</td>
        </tr>"""

    # ... actually let me redo this with the proper URL
    sim_url = "https://htmlpreview.github.io/?https://raw.githubusercontent.com/hucares164/my-trendradar/master/output/simulation/simulation_table.html"

    # Rebuild rows_html with proper URL
    rows_html = ""
    for r in results:
        code   = r["code"]
        pnl_class = "pnl-positive" if r["pnl"] >= 0 else "pnl-negative"
        pnl_sign  = "+" if r["pnl"] >= 0 else ""
        pct_sign  = "+" if r["pct"] >= 0 else ""

        day1_class  = _pct_class(r.get("day1_chg", "—"))
        day3_class  = _pct_class(r.get("day3_chg", "—"))
        day5_class  = _pct_class(r.get("day5_chg", "—"))
        day10_class = _pct_class(r.get("day10_chg", "—"))
        day20_class = _pct_class(r.get("day20_chg", "—"))

        sector = STOCK_SECTOR.get(r["name"], "—")
        s_color = SECTOR_COLORS.get(sector, "#636e72")
        sector_tag = f'<span class="tag" style="background:{s_color}">{sector}</span>'

        conf = r.get("confidence", "—")
        conf_colors = {"高": "#27ae60", "中": "#f39c12", "低": "#e74c3c"}
        conf_color = conf_colors.get(conf, "#95a5a6")
        conf_tag = f'<span class="tag" style="background:{conf_color}">{conf}</span>' if conf != "—" else "—"

        rows_html += f"""
        <tr>
            <td>{r['name']}<br><small>{code}</small><br>{sector_tag}</td>
            <td>{r['start_date']}</td>
            <td><b>{r.get('signal_type', '—')}</b></td>
            <td>{conf_tag}</td>
            <td>{r['trading_days']}天<br><small>→ {r['verify_date']}</small></td>
            <td>{r['expected']}</td>
            <td><b>¥{r['start_amount']:.2f}</b></td>
            <td class="{pnl_class}">{pnl_sign}{r['pnl']:.2f}<br><small>{pct_sign}{r['pct']:.2f}%</small></td>
            <td class="{day1_class}">{r.get('day1_chg', '—')}</td>
            <td class="{day3_class}">{r.get('day3_chg', '—')}</td>
            <td class="{day5_class}">{r.get('day5_chg', '—')}</td>
            <td class="{day10_class}">{r.get('day10_chg', '—')}</td>
            <td class="{day20_class}">{r.get('day20_chg', '—')}</td>
            <td class="{pnl_class}">{pct_sign}{r['pct']:.2f}%</td>
            <td>{sector_tag}</td>
            <td><a href="{sim_url}" target="_blank">🔗</a></td>
            <td style="max-width:240px;font-size:11px;white-space:normal">{r.get('info_supplement', '—')}</td>
        </tr>"""

    total_pnl = sum(r["pnl"] for r in results)
    pnl_tag   = "pos" if total_pnl >= 0 else "neg"

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>模拟盘验证表 v5</title>
<style>
    body       {{ font-family: -apple-system, 'PingFang SC', sans-serif; background: #f5f6fa; margin: 0; padding: 20px; }}
    h1         {{ color: #333; margin-bottom: 4px; }}
    .subtitle  {{ color: #888; font-size: 13px; margin-bottom: 16px; }}
    .table-wrap {{ overflow-x: auto; }}
    table      {{ border-collapse: collapse; width: 100%; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1); font-size: 11.5px; }}
    th         {{ background: #2c3e50; color: white; padding: 8px 6px; text-align: left; font-size: 11px; white-space: nowrap; }}
    td         {{ padding: 6px 6px; border-bottom: 1px solid #eee; font-size: 11px; }}
    tr:hover   {{ background: #f8f9ff; }}
    .pnl-positive {{ color: #e74c3c; font-weight: bold; }}
    .pnl-negative {{ color: #27ae60; font-weight: bold; }}
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
<h1>📊 模拟盘验证表 v5</h1>
<p class="subtitle">生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M")} ｜ 数据来源: westock-data ｜ 每支股票模拟买入 100 股</p>
<div class="summary">
    <div class="card"><h3>追踪股票</h3><div class="val">{len(results)}</div></div>
    <div class="card"><h3>盈利数</h3><div class="val pos">{sum(1 for r in results if r['pnl'] >= 0)}</div></div>
    <div class="card"><h3>亏损数</h3><div class="val neg">{sum(1 for r in results if r['pnl'] < 0)}</div></div>
    <div class="card"><h3>总盈亏</h3><div class="val {pnl_tag}">¥{total_pnl:+.2f}</div></div>
</div>
<div class="table-wrap">
<table>
    <thead>
        <tr>
            <th>股票</th><th>起始日期</th><th>信号类型</th><th>置信度</th><th>验证周期</th>
            <th>预期方向/涨幅</th><th>起始总价<br>(×100股)</th><th>累计盈亏<br>(100股)</th>
            <th>1日涨跌</th><th>3日涨跌</th><th>5日涨跌</th><th>10日涨跌</th><th>20日涨跌</th>
            <th>当前累计<br>涨跌幅</th><th>赛道</th><th>🔗</th><th>信息补充</th>
        </tr>
    </thead>
    <tbody>
        {rows_html}
    </tbody>
</table>
</div>
<p class="footer">■ 红色=涨(盈) ｜ ■ 绿色=跌(亏) ｜ 非交易日自动顺延 ｜ 全A股（¥） ｜ N日涨跌 = 第N个交易日累计涨跌幅 ｜ 验证周期 = verify_date - date（固定天数）</p>
</body>
</html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def main():
    with open(PRED_PATH) as f:
        data = json.load(f)
    preds = data.get("predictions", [])

    # 建立：股票名称 → 首次 date + verify_date + signal_type + confidence（取最早出现的那条预判）
    stock_dates = {}
    stock_signals = {}
    for p in preds:
        content = p.get("content", "")
        d       = p.get("date", "")
        vd      = p.get("verify_date", "")
        st      = p.get("signal_type", "—")
        cf      = p.get("confidence", "—")
        for name, aliases in STOCK_ALIASES.items():
            if any(a in content for a in aliases):
                if name not in stock_dates:
                    stock_dates[name] = (d, vd)
                    stock_signals[name] = (st, cf)

    results = []

    for code, name in STOCKS:
        if name not in stock_dates:
            print(f"⚠️  {name} 在 predictions.json 中无匹配预判，跳过", file=sys.stderr)
            continue

        first_date, verify_date = stock_dates[name]
        signal_type, confidence = stock_signals.get(name, ("—", "—"))
        if not verify_date:
            print(f"⚠️  {name} 无 verify_date，跳过", file=sys.stderr)
            continue

        print(f"处理: {name} ({code})，首次: {first_date}，验证: {verify_date}，信号: {signal_type}，置信度: {confidence}...", file=sys.stderr)

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

        shares        = 100
        start_amount  = round(start_price * shares, 2)
        current_amount = round(latest_price * shares, 2)
        pnl  = round(current_amount - start_amount, 2)
        pct  = round((latest_price - start_price) / start_price * 100, 2) if start_price else 0

        trading_days = calc_trading_days_fixed(first_date, verify_date)
        expected     = get_expected_range(preds, name)

        # N 日涨跌
        day1_chg  = calc_1day_change(rows, start_date)
        day3_chg  = calc_3day_change(rows, start_date)
        day5_chg  = calc_5day_change(rows, start_date)
        day10_chg = calc_10day_change(rows, start_date)
        day20_chg = calc_20day_change(rows, start_date)

        # 信息补充
        info_supplement = get_info_supplement(preds, name, first_date)

        results.append({
            "code":           code,
            "name":           name,
            "first_date":     first_date,
            "start_date":     start_date,
            "verify_date":    verify_date,
            "signal_type":    signal_type,
            "confidence":     confidence,
            "start_price":    start_price,
            "latest_date":    latest_date,
            "latest_price":   latest_price,
            "shares":         shares,
            "start_amount":   start_amount,
            "current_amount": current_amount,
            "pnl":            pnl,
            "pct":            pct,
            "trading_days":   trading_days,
            "expected":       expected,
            "day1_chg":       day1_chg,
            "day3_chg":       day3_chg,
            "day5_chg":       day5_chg,
            "day10_chg":      day10_chg,
            "day20_chg":      day20_chg,
            "info_supplement": info_supplement,
            "all_rows":       rows,
        })
        print(f"  ✓  起始={start_date} ¥{start_price:.2f} → 最新={latest_date} ¥{latest_price:.2f}，"
              f"涨跌={pct:+.2f}%，PNL=¥{pnl:+.2f}，"
              f"1日={day1_chg}，3日={day3_chg}，5日={day5_chg}，10日={day10_chg}，20日={day20_chg}", file=sys.stderr)

    # 输出汇总
    print("\n" + "=" * 80)
    print("模拟盘验证结果")
    print("=" * 80)
    for r in results:
        code   = r["code"]
        arrow  = "📈" if r["pnl"] >= 0 else "📉"
        print(f'{arrow} {r["name"]}({code}) | 信号:{r["signal_type"]} 置信度:{r["confidence"]} '
              f'| 起始:{r["start_date"]} ¥{r["start_price"]:.2f} '
              f'| 最新:{r["latest_date"]} ¥{r["latest_price"]:.2f} '
              f'| 100股: ¥{r["start_amount"]:.0f}→¥{r["current_amount"]:.0f} '
              f'| PNL: ¥{r["pnl"]:+.2f} ({r["pct"]:+.2f}%) '
              f'| 验证:{r["trading_days"]}天 | 预期:{r["expected"]}'
              f'| 1日:{r["day1_chg"]} | 3日:{r["day3_chg"]} | 5日:{r["day5_chg"]} | 10日:{r["day10_chg"]} | 20日:{r["day20_chg"]}')

    # 保存 CSV
    csv_path = os.path.join(REPO_ROOT, "output", "simulation", "simulation_table.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("股票代码,股票名称,首次预判日期,起始日期,验证周期(天),信号类型,置信度,"
                "起始总价(×100股),累计盈亏(100股),当前累计涨跌幅,预期涨幅区间,"
                "1日涨跌,3日涨跌,5日涨跌,10日涨跌,20日涨跌,信息补充\n")
        for r in results:
            pct_sign = "+" if r["pct"] >= 0 else ""
            f.write(f'{r["code"]},{r["name"]},{r["first_date"]},{r["start_date"]},{r["trading_days"]},'
                    f'{r["signal_type"]},{r["confidence"]},'
                    f'{r["start_amount"]},{r["pnl"]},{pct_sign}{r["pct"]:.2f}%,'
                    f'{r["expected"]},'
                    f'{r["day1_chg"]},{r["day3_chg"]},{r["day5_chg"]},{r["day10_chg"]},{r["day20_chg"]},'
                    f'"{r.get("info_supplement", "—")}"\n')
    print(f"\nCSV 已保存: {csv_path}", file=sys.stderr)

    # 生成 HTML
    html_path = os.path.join(REPO_ROOT, "output", "simulation", "simulation_table.html")
    gen_html(results, html_path)
    print(f"HTML 已保存: {html_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
