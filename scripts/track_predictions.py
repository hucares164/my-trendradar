#!/usr/bin/env python3
# coding=utf-8
"""
预判追踪脚本

从 AI 分析结果中提取预判，保存到追踪文件，
并支持月度偏差分析。

用法：
  1. 提取预判：  python scripts/track_predictions.py extract --result-file <path> --date <YYYY-MM-DD>
  2. 月度分析：  python scripts/track_predictions.py analyze --month <YYYY-MM>
  3. 查看历史：  python scripts/track_predictions.py list [--limit N]
"""

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
PREDICTIONS_FILE = PROJECT_ROOT / "output" / "predictions" / "predictions.json"


def load_predictions() -> dict:
    """加载预判追踪数据"""
    if PREDICTIONS_FILE.exists():
        with open(PREDICTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"predictions": [], "last_updated": ""}


def save_predictions(data: dict):
    """保存预判追踪数据"""
    PREDICTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(PREDICTIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_predictions_from_text(text: str, date_str: str) -> list:
    """
    从 AI 分析的 predictions 字段中提取结构化预判

    预判格式：预判内容[置信度:高/中/低|验证日:YYYY-MM-DD]
    """
    if not text:
        return []

    predictions = []
    # 匹配格式：内容[置信度:高/中/低|验证日:YYYY-MM-DD]
    pattern = r'([^\n\[]+?)\s*\[置信度[:：]\s*(高|中|低)\s*[|｜]\s*验证日[:：]\s*(\d{4}-\d{2}-\d{2})\]'
    matches = re.findall(pattern, text)

    for content, confidence, verify_date in matches:
        content = content.strip().strip("1. 2. 3. ").strip()
        if not content:
            continue

        # 确定信号类型（从内容推断）
        signal_type = "P"  # 默认
        if any(kw in content for kw in ["高管", "CEO", "管理层", "股权", "违规", "退市", "处罚"]):
            signal_type = "M"
        elif any(kw in content for kw in ["业绩", "营收", "利润", "订单", "毛利率"]):
            signal_type = "PEAD"
        elif any(kw in content for kw in ["政策", "新规", "补贴", "准入", "监管"]):
            signal_type = "P"
        elif any(kw in content for kw in ["供需", "缺货", "涨价", "产能", "扩产"]):
            signal_type = "S"
        elif any(kw in content for kw in ["地缘", "制裁", "冲突", "外交", "贸易"]):
            signal_type = "G"
        else:
            signal_type = "F"

        predictions.append({
            "date": date_str,
            "content": content,
            "confidence": confidence,
            "signal_type": signal_type,
            "verify_date": verify_date,
            "status": "pending",  # pending / hit / miss / expired
            "actual_result": "",
            "deviation_reason": "",
        })

    return predictions


def cmd_extract(args):
    """从 AI 分析结果中提取预判"""
    if len(args) < 2:
        print("用法: track_predictions.py extract --result-file <path> --date <YYYY-MM-DD>")
        sys.exit(1)

    result_file = None
    date_str = datetime.now().strftime("%Y-%m-%d")

    i = 0
    while i < len(args):
        if args[i] == "--result-file" and i + 1 < len(args):
            result_file = args[i + 1]
            i += 2
        elif args[i] == "--date" and i + 1 < len(args):
            date_str = args[i + 1]
            i += 2
        else:
            i += 1

    if not result_file:
        # 尝试从最新的输出文件中读取
        output_dir = PROJECT_ROOT / "output" / "news"
        if output_dir.exists():
            db_files = sorted(output_dir.glob("*.json"), reverse=True)
            if db_files:
                result_file = str(db_files[0])
                print(f"[追踪] 自动使用最新数据文件: {result_file}")

    if not result_file or not Path(result_file).exists():
        print("[追踪] 错误: 未找到结果文件")
        sys.exit(1)

    # 读取 AI 分析结果
    with open(result_file, "r", encoding="utf-8") as f:
        result_data = json.load(f)

    # 从 AI 分析结果中提取 predictions 字段
    predictions_text = ""
    if isinstance(result_data, dict):
        # 尝试多种路径
        predictions_text = (
            result_data.get("predictions", "")
            or result_data.get("ai_analysis", {}).get("predictions", "")
            or ""
        )

    if not predictions_text:
        print("[追踪] 未在结果文件中找到 predictions 字段")
        return

    # 提取预判
    new_predictions = extract_predictions_from_text(predictions_text, date_str)

    if not new_predictions:
        print("[追踪] 未能从文本中提取到结构化预判")
        return

    # 加载现有数据并追加
    data = load_predictions()
    data["predictions"].extend(new_predictions)
    save_predictions(data)

    print(f"[追踪] 成功提取 {len(new_predictions)} 条预判")
    for p in new_predictions:
        print(f"  - {p['content'][:50]}... [{p['confidence']}|{p['verify_date']}]")


def cmd_analyze(args):
    """月度偏差分析"""
    if len(args) < 2:
        print("用法: track_predictions.py analyze --month <YYYY-MM>")
        sys.exit(1)

    month_str = None
    i = 0
    while i < len(args):
        if args[i] == "--month" and i + 1 < len(args):
            month_str = args[i + 1]
            i += 2
        else:
            i += 1

    if not month_str:
        month_str = datetime.now().strftime("%Y-%m")

    data = load_predictions()
    predictions = data.get("predictions", [])

    # 筛选该月的预判
    month_predictions = [p for p in predictions if p["date"].startswith(month_str)]

    if not month_predictions:
        print(f"[分析] {month_str} 无预判记录")
        return

    # 统计分析
    total = len(month_predictions)
    hit = sum(1 for p in month_predictions if p["status"] == "hit")
    miss = sum(1 for p in month_predictions if p["status"] == "miss")
    expired = sum(1 for p in month_predictions if p["status"] == "expired")
    pending = sum(1 for p in month_predictions if p["status"] == "pending")

    # 按信号类型统计
    signal_stats = {}
    for p in month_predictions:
        st = p["signal_type"]
        if st not in signal_stats:
            signal_stats[st] = {"total": 0, "hit": 0, "miss": 0}
        signal_stats[st]["total"] += 1
        if p["status"] == "hit":
            signal_stats[st]["hit"] += 1
        elif p["status"] == "miss":
            signal_stats[st]["miss"] += 1

    # 按置信度统计
    conf_stats = {}
    for p in month_predictions:
        c = p["confidence"]
        if c not in conf_stats:
            conf_stats[c] = {"total": 0, "hit": 0, "miss": 0}
        conf_stats[c]["total"] += 1
        if p["status"] == "hit":
            conf_stats[c]["hit"] += 1
        elif p["status"] == "miss":
            conf_stats[c]["miss"] += 1

    # 偏差原因分析
    deviation_reasons = {}
    for p in month_predictions:
        if p["deviation_reason"]:
            reason = p["deviation_reason"]
            deviation_reasons[reason] = deviation_reasons.get(reason, 0) + 1

    # 输出报告
    print(f"\n{'='*60}")
    print(f"  预判追踪月度偏差分析 - {month_str}")
    print(f"{'='*60}\n")

    print(f"总预判数: {total}")
    print(f"  命中: {hit}  失误: {miss}  过期未验证: {expired}  待验证: {pending}")
    if total > 0 and (hit + miss) > 0:
        print(f"  命中率: {hit/(hit+miss)*100:.1f}%")

    print(f"\n--- 按信号类型 ---")
    for st, stats in sorted(signal_stats.items()):
        rate = f"{stats['hit']/max(stats['hit']+stats['miss'],1)*100:.0f}%" if (stats['hit']+stats['miss']) > 0 else "N/A"
        print(f"  {st}类: {stats['total']}条, 命中率 {rate}")

    print(f"\n--- 按置信度 ---")
    for c, stats in sorted(conf_stats.items()):
        rate = f"{stats['hit']/max(stats['hit']+stats['miss'],1)*100:.0f}%" if (stats['hit']+stats['miss']) > 0 else "N/A"
        print(f"  {c}置信: {stats['total']}条, 命中率 {rate}")

    if deviation_reasons:
        print(f"\n--- 偏差原因分布 ---")
        for reason, count in sorted(deviation_reasons.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}次")

    print(f"\n{'='*60}")


def cmd_list(args):
    """查看预判历史"""
    limit = 20
    if len(args) >= 2 and args[0] == "--limit":
        try:
            limit = int(args[1])
        except ValueError:
            pass

    data = load_predictions()
    predictions = data.get("predictions", [])

    if not predictions:
        print("[追踪] 暂无预判记录")
        return

    print(f"\n最近 {min(limit, len(predictions))} 条预判:\n")
    for p in predictions[-limit:]:
        status_icon = {"pending": "⏳", "hit": "✅", "miss": "❌", "expired": "⌛"}.get(p["status"], "?")
        print(f"{status_icon} [{p['date']}] {p['content'][:60]}")
        print(f"   信号:{p['signal_type']} 置信:{p['confidence']} 验证日:{p['verify_date']}")
        if p["actual_result"]:
            print(f"   实际: {p['actual_result'][:60]}")
        print()


def cmd_auto_extract():
    """
    自动提取模式：从 TrendRadar 运行结果中提取预判
    供 GitHub Actions workflow 调用
    """
    date_str = datetime.now().strftime("%Y-%m-%d")

    # 查找最新的分析结果文件
    output_dir = PROJECT_ROOT / "output" / "news"
    if not output_dir.exists():
        print("[追踪] 输出目录不存在，跳过")
        return

    # 从 HTML 报告或数据库中提取 AI 分析结果
    # 这里尝试从最新生成的文件中读取
    db_files = sorted(output_dir.glob("*.json"), reverse=True)
    if not db_files:
        print("[追踪] 未找到结果文件，跳过")
        return

    result_file = str(db_files[0])
    print(f"[追踪] 使用文件: {result_file}")

    try:
        with open(result_file, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        print(f"[追踪] 读取文件失败: {e}")
        return

    # 尝试从文件内容中提取 predictions
    # 首先尝试 JSON 解析
    try:
        result_data = json.loads(content)
        predictions_text = ""
        if isinstance(result_data, dict):
            predictions_text = result_data.get("predictions", "")
    except json.JSONDecodeError:
        # 非 JSON 格式，尝试从文本中搜索预判部分
        # 查找 "预判追踪" 之后的文本
        match = re.search(r'预判追踪.*?\n(.*)', content, re.DOTALL)
        predictions_text = match.group(1) if match else ""

    if not predictions_text:
        print("[追踪] 未在结果中找到预判内容")
        return

    # 提取预判
    new_predictions = extract_predictions_from_text(predictions_text, date_str)

    if not new_predictions:
        print("[追踪] 未能提取到结构化预判")
        return

    # 加载现有数据并追加
    data = load_predictions()

    # 去重：避免同一天重复提取
    existing_contents = {(p["date"], p["content"][:50]) for p in data["predictions"]}
    unique_new = [
        p for p in new_predictions
        if (p["date"], p["content"][:50]) not in existing_contents
    ]

    if not unique_new:
        print("[追踪] 今日预判已存在，跳过重复提取")
        return

    data["predictions"].extend(unique_new)
    save_predictions(data)

    print(f"[追踪] 成功提取 {len(unique_new)} 条新预判（总计 {len(data['predictions'])} 条）")


def cmd_update_status(args):
    """手动更新预判状态"""
    if len(args) < 3:
        print("用法: track_predictions.py update <序号> <hit/miss/expired> [实际结果]")
        return

    try:
        idx = int(args[0]) - 1  # 用户看到的序号从1开始
    except ValueError:
        print("序号必须是数字")
        return

    status = args[1]
    actual_result = args[2] if len(args) > 2 else ""

    if status not in ("hit", "miss", "expired"):
        print("状态必须是 hit/miss/expired")
        return

    data = load_predictions()
    predictions = data.get("predictions", [])

    if idx < 0 or idx >= len(predictions):
        print(f"序号超出范围（1-{len(predictions)}）")
        return

    predictions[idx]["status"] = status
    if actual_result:
        predictions[idx]["actual_result"] = actual_result

    # 如果失误，提示填写偏差原因
    if status == "miss":
        print("请输入偏差原因（信号误判/传导链断裂/外部黑天鹅/时间窗口误判）:")
        reason = input().strip()
        if reason:
            predictions[idx]["deviation_reason"] = reason

    save_predictions(data)
    print(f"[追踪] 已更新第 {idx+1} 条预判状态为 {status}")


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  track_predictions.py extract --result-file <path> --date <YYYY-MM-DD>")
        print("  track_predictions.py auto                           # 自动从最新结果提取")
        print("  track_predictions.py analyze --month <YYYY-MM>      # 月度偏差分析")
        print("  track_predictions.py list [--limit N]               # 查看历史")
        print("  track_predictions.py update <序号> <状态> [结果]    # 更新状态")
        sys.exit(1)

    command = sys.argv[1]
    args = sys.argv[2:]

    if command == "extract":
        cmd_extract(args)
    elif command == "auto":
        cmd_auto_extract()
    elif command == "analyze":
        cmd_analyze(args)
    elif command == "list":
        cmd_list(args)
    elif command == "update":
        cmd_update_status(args)
    else:
        print(f"未知命令: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
