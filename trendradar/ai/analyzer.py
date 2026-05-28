# coding=utf-8
"""
AI 分析器模块

调用 AI 大模型对热点新闻进行深度分析
基于 LiteLLM 统一接口，支持 100+ AI 提供商
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trendradar.ai.client import AIClient
from trendradar.ai.prompt_loader import load_prompt_template


@dataclass
class AIAnalysisResult:
    """AI 分析结果 - 商业机会深度分析"""
    # 商业机会分析 6 大板块
    signal_radar: str = ""                # 信号雷达：新闻分类标注+传导链
    opportunity_analysis: str = ""       # 机会深度分析：矩阵+PEST+价值链
    huawei_chain: str = ""               # 华为产业链分析：环节分组+企业影响
    verification_path: str = ""          # 验证路径：MVP+竞争格局
    predictions: str = ""                # 预判追踪：3月预判+置信度
    action_suggestions: str = ""         # 行动建议：具体可执行建议
    standalone_summaries: Dict[str, str] = field(default_factory=dict)  # 独立展示区概括 {源ID: 概括}

    # 基础元数据
    raw_response: str = ""               # 原始响应
    success: bool = False                # 是否成功
    skipped: bool = False                # 是否因无内容跳过（非失败）
    error: str = ""                      # 错误信息

    # 新闻数量统计
    total_news: int = 0                  # 总新闻数（热榜+RSS）
    analyzed_news: int = 0               # 实际分析的新闻数
    max_news_limit: int = 0              # 分析上限配置值
    hotlist_count: int = 0               # 热榜新闻数
    rss_count: int = 0                   # RSS 新闻数
    ai_mode: str = ""                    # AI 分析使用的模式 (daily/current/incremental)


class AIAnalyzer:
    """AI 分析器"""

    def __init__(
        self,
        ai_config: Dict[str, Any],
        analysis_config: Dict[str, Any],
        get_time_func: Callable,
        debug: bool = False,
    ):
        """
        初始化 AI 分析器

        Args:
            ai_config: AI 模型配置（LiteLLM 格式）
            analysis_config: AI 分析功能配置（language, prompt_file 等）
            get_time_func: 获取当前时间的函数
            debug: 是否开启调试模式
        """
        self.ai_config = ai_config
        self.analysis_config = analysis_config
        self.get_time_func = get_time_func
        self.debug = debug

        # 创建 AI 客户端（基于 LiteLLM）
        self.client = AIClient(ai_config)

        # 验证配置
        valid, error = self.client.validate_config()
        if not valid:
            print(f"[AI] 配置警告: {error}")

        # 从分析配置获取功能参数
        self.max_news = analysis_config.get("MAX_NEWS_FOR_ANALYSIS", 50)
        self.include_rss = analysis_config.get("INCLUDE_RSS", True)
        self.include_rank_timeline = analysis_config.get("INCLUDE_RANK_TIMELINE", False)
        self.include_standalone = analysis_config.get("INCLUDE_STANDALONE", False)
        self.language = analysis_config.get("LANGUAGE", "Chinese")

        # 加载提示词模板
        self.system_prompt, self.user_prompt_template = load_prompt_template(
            analysis_config.get("PROMPT_FILE", "ai_analysis_prompt.txt"),
            label="AI",
        )

    def analyze(
        self,
        stats: List[Dict],
        rss_stats: Optional[List[Dict]] = None,
        report_mode: str = "daily",
        report_type: str = "当日汇总",
        platforms: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        standalone_data: Optional[Dict] = None,
    ) -> AIAnalysisResult:
        """
        执行 AI 分析

        Args:
            stats: 热榜统计数据
            rss_stats: RSS 统计数据
            report_mode: 报告模式
            report_type: 报告类型
            platforms: 平台列表
            keywords: 关键词列表

        Returns:
            AIAnalysisResult: 分析结果
        """
        
        # 打印配置信息方便调试
        model = self.ai_config.get("MODEL", "unknown")
        api_key = self.client.api_key or ""
        api_base = self.ai_config.get("API_BASE", "")
        masked_key = f"{api_key[:5]}******" if len(api_key) >= 5 else "******"
        model_display = model.replace("/", "/\u200b") if model else "unknown"

        print(f"[AI] 模型: {model_display}")
        print(f"[AI] Key : {masked_key}")

        if api_base:
            print(f"[AI] 接口: 存在自定义 API 端点")

        timeout = self.ai_config.get("TIMEOUT", 120)
        max_tokens = self.ai_config.get("MAX_TOKENS", 5000)
        print(f"[AI] 参数: timeout={timeout}, max_tokens={max_tokens}")

        if not self.client.api_key:
            return AIAnalysisResult(
                success=False,
                error="未配置 AI API Key，请在 config.yaml 或环境变量 AI_API_KEY 中设置"
            )

        # 准备新闻内容并获取统计数据
        news_content, rss_content, hotlist_total, rss_total, analyzed_count = self._prepare_news_content(stats, rss_stats)
        total_news = hotlist_total + rss_total

        if not news_content and not rss_content:
            return AIAnalysisResult(
                success=False,
                skipped=True,
                error="本轮无新增热点内容，跳过 AI 分析",
                total_news=total_news,
                hotlist_count=hotlist_total,
                rss_count=rss_total,
                analyzed_news=0,
                max_news_limit=self.max_news
            )

        # 构建提示词
        current_time = self.get_time_func().strftime("%Y-%m-%d %H:%M:%S")

        # 提取关键词
        if not keywords:
            keywords = [s.get("word", "") for s in stats if s.get("word")] if stats else []

        # 使用安全的字符串替换，避免模板中其他花括号（如 JSON 示例）被误解析
        user_prompt = self.user_prompt_template
        user_prompt = user_prompt.replace("{report_mode}", report_mode)
        user_prompt = user_prompt.replace("{report_type}", report_type)
        user_prompt = user_prompt.replace("{current_time}", current_time)
        user_prompt = user_prompt.replace("{news_count}", str(hotlist_total))
        user_prompt = user_prompt.replace("{rss_count}", str(rss_total))
        user_prompt = user_prompt.replace("{platforms}", ", ".join(platforms) if platforms else "多平台")
        user_prompt = user_prompt.replace("{keywords}", ", ".join(keywords[:20]) if keywords else "无")
        user_prompt = user_prompt.replace("{news_content}", news_content)
        user_prompt = user_prompt.replace("{rss_content}", rss_content)
        user_prompt = user_prompt.replace("{language}", self.language)

        # 构建独立展示区内容
        standalone_content = ""
        if self.include_standalone and standalone_data:
            standalone_content = self._prepare_standalone_content(standalone_data)
        user_prompt = user_prompt.replace("{standalone_content}", standalone_content)

        if self.debug:
            print("\n" + "=" * 80)
            print("[AI 调试] 发送给 AI 的完整提示词")
            print("=" * 80)
            if self.system_prompt:
                print("\n--- System Prompt ---")
                print(self.system_prompt)
            print("\n--- User Prompt ---")
            print(user_prompt)
            print("=" * 80 + "\n")

        # 调用 AI API（使用 LiteLLM）
        try:
            response = self._call_ai(user_prompt)
            result = self._parse_response(response)

            # JSON 解析失败时的重试兜底（仅重试一次）
            if result.error and "JSON 解析错误" in result.error:
                print(f"[AI] JSON 解析失败，尝试让 AI 修复...")
                retry_result = self._retry_fix_json(response, result.error)
                if retry_result and retry_result.success and not retry_result.error:
                    print("[AI] JSON 修复成功")
                    retry_result.raw_response = response
                    result = retry_result
                else:
                    print("[AI] JSON 修复失败，使用原始文本兜底")

            # 如果配置未启用 standalone 分析，强制清空
            if not self.include_standalone:
                result.standalone_summaries = {}

            # 填充统计数据
            result.total_news = total_news
            result.hotlist_count = hotlist_total
            result.rss_count = rss_total
            result.analyzed_news = analyzed_count
            result.max_news_limit = self.max_news

            # 保存预判数据到追踪文件
            if result.success and result.predictions:
                pred_list = self._save_predictions(result.predictions, current_time)
                # 写入飞书多维表格
                if pred_list:
                    self._write_predictions_to_feishu(pred_list)

            return result
        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)

            # 截断过长的错误消息
            if len(error_msg) > 200:
                error_msg = error_msg[:200] + "..."
            friendly_msg = f"AI 分析失败 ({error_type}): {error_msg}"

            return AIAnalysisResult(
                success=False,
                error=friendly_msg
            )

    def _prepare_news_content(
        self,
        stats: List[Dict],
        rss_stats: Optional[List[Dict]] = None,
    ) -> tuple:
        """
        准备新闻内容文本（增强版）

        热榜新闻包含：来源、标题、排名范围、时间范围、出现次数
        RSS 包含：来源、标题、发布时间

        Returns:
            tuple: (news_content, rss_content, hotlist_total, rss_total, analyzed_count)
        """
        news_lines = []
        rss_lines = []
        news_count = 0
        rss_count = 0

        # 计算总新闻数
        hotlist_total = sum(len(s.get("titles", [])) for s in stats) if stats else 0
        rss_total = sum(len(s.get("titles", [])) for s in rss_stats) if rss_stats else 0

        # 热榜内容
        if stats:
            for stat in stats:
                word = stat.get("word", "")
                titles = stat.get("titles", [])
                if word and titles:
                    news_lines.append(f"\n**{word}** ({len(titles)}条)")
                    for t in titles:
                        if not isinstance(t, dict):
                            continue
                        title = t.get("title", "")
                        if not title:
                            continue

                        # 来源
                        source = t.get("source_name", t.get("source", ""))

                        # 构建行
                        if source:
                            line = f"- [{source}] {title}"
                        else:
                            line = f"- {title}"

                        # 始终显示简化格式：排名范围 + 时间范围 + 出现次数
                        ranks = t.get("ranks", [])
                        if ranks:
                            min_rank = min(ranks)
                            max_rank = max(ranks)
                            rank_str = f"{min_rank}" if min_rank == max_rank else f"{min_rank}-{max_rank}"
                        else:
                            rank_str = "-"

                        first_time = t.get("first_time", "")
                        last_time = t.get("last_time", "")
                        time_str = self._format_time_range(first_time, last_time)

                        appear_count = t.get("count", 1)

                        line += f" | 排名:{rank_str} | 时间:{time_str} | 出现:{appear_count}次"

                        # 开启完整时间线时，额外添加轨迹
                        if self.include_rank_timeline:
                            rank_timeline = t.get("rank_timeline", [])
                            timeline_str = self._format_rank_timeline(rank_timeline)
                            line += f" | 轨迹:{timeline_str}"

                        news_lines.append(line)

                        news_count += 1
                        if news_count >= self.max_news:
                            break
                if news_count >= self.max_news:
                    break

        # RSS 内容（仅在启用时构建）
        if self.include_rss and rss_stats:
            remaining = self.max_news - news_count
            for stat in rss_stats:
                if rss_count >= remaining:
                    break
                word = stat.get("word", "")
                titles = stat.get("titles", [])
                if word and titles:
                    rss_lines.append(f"\n**{word}** ({len(titles)}条)")
                    for t in titles:
                        if not isinstance(t, dict):
                            continue
                        title = t.get("title", "")
                        if not title:
                            continue

                        # 来源
                        source = t.get("source_name", t.get("feed_name", ""))

                        # 发布时间
                        time_display = t.get("time_display", "")

                        # 构建行：[来源] 标题 | 发布时间
                        if source:
                            line = f"- [{source}] {title}"
                        else:
                            line = f"- {title}"
                        if time_display:
                            line += f" | {time_display}"
                        rss_lines.append(line)

                        rss_count += 1
                        if rss_count >= remaining:
                            break

        news_content = "\n".join(news_lines) if news_lines else ""
        rss_content = "\n".join(rss_lines) if rss_lines else ""
        total_count = news_count + rss_count

        return news_content, rss_content, hotlist_total, rss_total, total_count

    def _call_ai(self, user_prompt: str) -> str:
        """调用 AI API（使用 LiteLLM）"""
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        return self.client.chat(messages)

    def _retry_fix_json(self, original_response: str, error_msg: str) -> Optional[AIAnalysisResult]:
        """
        JSON 解析失败时，请求 AI 修复 JSON（仅重试一次）

        使用轻量 prompt，不重复原始分析的 system prompt，节省 token。

        Args:
            original_response: AI 原始响应（JSON 格式有误）
            error_msg: JSON 解析的错误信息

        Returns:
            修复后的分析结果，失败时返回 None
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "你是一个 JSON 修复助手。用户会提供一段格式有误的 JSON 和错误信息，"
                    "你需要修复 JSON 格式错误并返回正确的 JSON。\n"
                    "常见问题：字符串值内的双引号未转义、缺少逗号、字符串未正确闭合等。\n"
                    "只返回纯 JSON，不要包含 markdown 代码块标记（如 ```json）或任何说明文字。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"以下 JSON 解析失败：\n\n"
                    f"错误：{error_msg}\n\n"
                    f"原始内容：\n{original_response}\n\n"
                    f"请修复以上 JSON 中的格式问题（如值中的双引号改用中文引号「」或转义 \\\"、"
                    f"缺少逗号、不完整的字符串等），保持原始内容语义不变，只修复格式。"
                    f"直接返回修复后的纯 JSON。"
                ),
            },
        ]

        try:
            response = self.client.chat(messages)
            return self._parse_response(response)
        except Exception as e:
            print(f"[AI] 重试修复 JSON 异常: {type(e).__name__}: {e}")
            return None

    def _format_time_range(self, first_time: str, last_time: str) -> str:
        """格式化时间范围（简化显示，只保留时分）"""
        def extract_time(time_str: str) -> str:
            if not time_str:
                return "-"
            # 尝试提取 HH:MM 部分
            if " " in time_str:
                parts = time_str.split(" ")
                if len(parts) >= 2:
                    time_part = parts[1]
                    if ":" in time_part:
                        return time_part[:5]  # HH:MM
            elif ":" in time_str:
                return time_str[:5]
            # 处理 HH-MM 格式
            result = time_str[:5] if len(time_str) >= 5 else time_str
            if len(result) == 5 and result[2] == '-':
                result = result.replace('-', ':')
            return result

        first = extract_time(first_time)
        last = extract_time(last_time)

        if first == last or last == "-":
            return first
        return f"{first}~{last}"

    def _format_rank_timeline(self, rank_timeline: List[Dict]) -> str:
        """格式化排名时间线"""
        if not rank_timeline:
            return "-"

        parts = []
        for item in rank_timeline:
            time_str = item.get("time", "")
            if len(time_str) == 5 and time_str[2] == '-':
                time_str = time_str.replace('-', ':')
            rank = item.get("rank")
            if rank is None:
                parts.append(f"0({time_str})")
            else:
                parts.append(f"{rank}({time_str})")

        return "→".join(parts)

    def _prepare_standalone_content(self, standalone_data: Dict) -> str:
        """
        将独立展示区数据转为文本，注入 AI 分析 prompt

        Args:
            standalone_data: 独立展示区数据 {"platforms": [...], "rss_feeds": [...]}

        Returns:
            格式化的文本内容
        """
        lines = []

        # 热榜平台
        for platform in standalone_data.get("platforms", []):
            platform_id = platform.get("id", "")
            platform_name = platform.get("name", platform_id)
            items = platform.get("items", [])
            if not items:
                continue

            lines.append(f"### [{platform_name}]")
            for item in items:
                title = item.get("title", "")
                if not title:
                    continue

                line = f"- {title}"

                # 排名信息
                ranks = item.get("ranks", [])
                if ranks:
                    min_rank = min(ranks)
                    max_rank = max(ranks)
                    rank_str = f"{min_rank}" if min_rank == max_rank else f"{min_rank}-{max_rank}"
                    line += f" | 排名:{rank_str}"

                # 时间范围
                first_time = item.get("first_time", "")
                last_time = item.get("last_time", "")
                if first_time:
                    time_str = self._format_time_range(first_time, last_time)
                    line += f" | 时间:{time_str}"

                # 出现次数
                count = item.get("count", 1)
                if count > 1:
                    line += f" | 出现:{count}次"

                # 排名轨迹（如果启用）
                if self.include_rank_timeline:
                    rank_timeline = item.get("rank_timeline", [])
                    if rank_timeline:
                        timeline_str = self._format_rank_timeline(rank_timeline)
                        line += f" | 轨迹:{timeline_str}"

                lines.append(line)
            lines.append("")

        # RSS 源
        for feed in standalone_data.get("rss_feeds", []):
            feed_id = feed.get("id", "")
            feed_name = feed.get("name", feed_id)
            items = feed.get("items", [])
            if not items:
                continue

            lines.append(f"### [{feed_name}]")
            for item in items:
                title = item.get("title", "")
                if not title:
                    continue

                line = f"- {title}"
                published_at = item.get("published_at", "")
                if published_at:
                    line += f" | {published_at}"

                lines.append(line)
            lines.append("")

        return "\n".join(lines)

    def _parse_response(self, response: str) -> AIAnalysisResult:
        """解析 AI 响应"""
        result = AIAnalysisResult(raw_response=response)

        if not response or not response.strip():
            result.error = "AI 返回空响应"
            return result

        # 提取 JSON 文本（去掉 markdown 代码块标记）
        json_str = response

        if "```json" in response:
            parts = response.split("```json", 1)
            if len(parts) > 1:
                code_block = parts[1]
                end_idx = code_block.find("```")
                if end_idx != -1:
                    json_str = code_block[:end_idx]
                else:
                    json_str = code_block
        elif "```" in response:
            parts = response.split("```", 2)
            if len(parts) >= 2:
                json_str = parts[1]

        json_str = json_str.strip()
        if not json_str:
            result.error = "提取的 JSON 内容为空"
            result.core_trends = response[:500] + "..." if len(response) > 500 else response
            result.success = True
            return result

        # 第一步：标准 JSON 解析
        data = None
        parse_error = None

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            parse_error = e

        # 第二步：json_repair 本地修复
        if data is None:
            try:
                from json_repair import repair_json
                repaired = repair_json(json_str, return_objects=True)
                if isinstance(repaired, dict):
                    data = repaired
                    print("[AI] JSON 本地修复成功（json_repair）")
            except Exception:
                pass

        # 两步都失败，记录错误（后续由 analyze 方法的重试机制处理）
        if data is None:
            if parse_error:
                error_context = json_str[max(0, parse_error.pos - 30):parse_error.pos + 30] if json_str and parse_error.pos else ""
                result.error = f"JSON 解析错误 (位置 {parse_error.pos}): {parse_error.msg}"
                if error_context:
                    result.error += f"，上下文: ...{error_context}..."
            else:
                result.error = "JSON 解析失败"
            # 兜底：使用已提取的 json_str（不含 markdown 标记），避免推送中出现 ```json
            result.signal_radar = json_str[:500] + "..." if len(json_str) > 500 else json_str
            result.success = True
            return result

        # 解析成功，提取字段
        try:
            # 新版6大板块
            result.signal_radar = data.get("signal_radar", "")
            result.opportunity_analysis = data.get("opportunity_analysis", "")
            result.huawei_chain = data.get("huawei_chain", "")
            result.verification_path = data.get("verification_path", "")
            result.predictions = data.get("predictions", "")
            result.action_suggestions = data.get("action_suggestions", "")

            # 兼容旧版字段（如果 AI 返回旧格式）
            if not result.signal_radar:
                result.signal_radar = data.get("core_trends", "")
            if not result.opportunity_analysis:
                result.opportunity_analysis = data.get("sentiment_controversy", "")
            if not result.verification_path:
                result.verification_path = data.get("signals", "")
            if not result.predictions:
                result.predictions = data.get("outlook_strategy", "")

            # 解析独立展示区概括
            summaries = data.get("standalone_summaries", {})
            if isinstance(summaries, dict):
                result.standalone_summaries = {
                    str(k): str(v) for k, v in summaries.items()
                }

            result.success = True
        except (KeyError, TypeError, AttributeError) as e:
            result.error = f"字段提取错误: {type(e).__name__}: {e}"
            result.signal_radar = json_str[:500] + "..." if len(json_str) > 500 else json_str
            result.success = True

        return result

    def _save_predictions(self, predictions_text: str, current_time: str) -> list:
        """
        从 AI 分析的 predictions 字段中提取结构化预判，
        保存到追踪文件中。

        预判格式：预判内容[置信度:高/中/低|验证日:YYYY-MM-DD]

        Returns:
            提取到的新预判列表
        """
        if not predictions_text:
            return []

        # 匹配格式：内容[置信度:高/中/低|验证日:YYYY-MM-DD|依据:判断依据和逻辑链路]
        # 依据部分为可选，兼容旧格式（不含依据）
        pattern = r'([^\n\[]+?)\s*\[置信度[:：]\s*(高|中|低)\s*[|｜]\s*验证日[:：]\s*(\d{4}-\d{2}-\d{2})\s*(?:[|｜]\s*依据[:：]\s*([^\]]+?))?\]'
        matches = re.findall(pattern, predictions_text)

        if not matches:
            return []

        date_str = current_time[:10] if current_time else datetime.now().strftime("%Y-%m-%d")
        new_predictions = []

        for content, confidence, verify_date, basis in matches:
            content = content.strip()
            # 去除序号前缀
            content = re.sub(r'^\d+\.\s*', '', content).strip()
            if not content:
                continue

            # 推断信号类型
            signal_type = "P"
            if any(kw in content for kw in ["高管", "CEO", "管理层", "股权", "违规", "退市", "处罚", "辞职"]):
                signal_type = "M"
            elif any(kw in content for kw in ["业绩", "营收", "利润", "订单", "毛利率", "财报"]):
                signal_type = "PEAD"
            elif any(kw in content for kw in ["政策", "新规", "补贴", "准入", "监管", "法规"]):
                signal_type = "P"
            elif any(kw in content for kw in ["供需", "缺货", "涨价", "产能", "扩产", "停产"]):
                signal_type = "S"
            elif any(kw in content for kw in ["地缘", "制裁", "冲突", "外交", "贸易", "封锁"]):
                signal_type = "G"
            else:
                signal_type = "F"

            new_predictions.append({
                "date": date_str,
                "content": content,
                "confidence": confidence,
                "signal_type": signal_type,
                "verify_date": verify_date,
                "basis": (basis or "").strip(),
                "status": "pending",
                "actual_result": "",
                "deviation_reason": "",
            })

        if not new_predictions:
            return

        # 保存到追踪文件
        pred_dir = Path("output/predictions")
        pred_dir.mkdir(parents=True, exist_ok=True)
        pred_file = pred_dir / "predictions.json"

        # 加载现有数据
        existing = {"predictions": [], "last_updated": ""}
        if pred_file.exists():
            try:
                with open(pred_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        # 去重：避免同一天重复提取
        existing_keys = {(p.get("date", ""), p.get("content", "")[:50]) for p in existing.get("predictions", [])}
        unique_new = [
            p for p in new_predictions
            if (p["date"], p["content"][:50]) not in existing_keys
        ]

        if not unique_new:
            return []

        existing["predictions"].extend(unique_new)
        existing["last_updated"] = current_time

        with open(pred_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)

        print(f"[AI] 已保存 {len(unique_new)} 条预判到追踪文件（总计 {len(existing['predictions'])} 条）")
        return unique_new

    def _write_predictions_to_feishu(self, predictions: list):
        """将预判写入飞书多维表格"""
        try:
            from trendradar.ai.feishu import create_feishu_client_from_env
            client = create_feishu_client_from_env()
            if client.bitable_configured:
                # 转换状态为中文
                for p in predictions:
                    status_map = {"pending": "待验证", "hit": "命中", "miss": "失误", "expired": "过期"}
                    p["status"] = status_map.get(p.get("status", ""), "待验证")
                client.write_predictions(predictions)
        except Exception as e:
            print(f"[AI] 飞书多维表格写入跳过: {e}")
