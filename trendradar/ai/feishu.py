# coding=utf-8
"""
飞书集成模块

功能：
1. 群机器人推送（带签名验证的富文本卡片消息）
2. 多维表格写入预判数据
"""

import hashlib
import hmac
import base64
import json
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class FeishuClient:
    """飞书客户端"""

    def __init__(
        self,
        app_id: str = "",
        app_secret: str = "",
        webhook_url: str = "",
        webhook_secret: str = "",
        bitable_app_token: str = "",
        bitable_table_id: str = "",
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.webhook_url = webhook_url
        self.webhook_secret = webhook_secret
        self.bitable_app_token = bitable_app_token
        self.bitable_table_id = bitable_table_id
        self._token = ""
        self._token_expire = 0

    @property
    def is_configured(self) -> bool:
        """检查是否配置了至少一个功能"""
        return bool(self.webhook_url) or bool(self.bitable_app_token and self.app_id)

    @property
    def webhook_configured(self) -> bool:
        return bool(self.webhook_url)

    @property
    def bitable_configured(self) -> bool:
        return bool(self.bitable_app_token and self.bitable_table_id and self.app_id and self.app_secret)

    def _get_token(self) -> str:
        """获取 tenant_access_token（带缓存）"""
        if self._token and time.time() < self._token_expire:
            return self._token

        if not self.app_id or not self.app_secret:
            return ""

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
        except Exception as e:
            print(f"[飞书] 获取token失败: {e}")

        return ""

    def _sign_webhook(self) -> tuple:
        """生成 Webhook 签名"""
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{self.webhook_secret}"
        hmac_code = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(
            base64.b64encode(hmac_code).decode("utf-8")
        )
        return timestamp, sign

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 群机器人推送
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def send_analysis_card(self, result: Any) -> bool:
        """
        发送商业机会分析卡片消息

        Args:
            result: AIAnalysisResult 对象

        Returns:
            是否发送成功
        """
        if not self.webhook_configured:
            return False

        # 构建卡片内容
        elements = []

        # 信号雷达
        if result.signal_radar:
            elements.append({
                "tag": "markdown",
                "content": f"**📡 信号雷达**\n{self._format_for_card(result.signal_radar)}"
            })

        # 机会深度分析
        if result.opportunity_analysis:
            elements.append({
                "tag": "markdown",
                "content": f"**🎯 机会深度分析**\n{self._format_for_card(result.opportunity_analysis)}"
            })

        # 华为产业链
        if result.huawei_chain:
            elements.append({
                "tag": "markdown",
                "content": f"**🔗 华为产业链**\n{self._format_for_card(result.huawei_chain)}"
            })

        # 验证路径
        if result.verification_path:
            elements.append({
                "tag": "markdown",
                "content": f"**🔍 验证路径**\n{self._format_for_card(result.verification_path)}"
            })

        # 预判追踪
        if result.predictions:
            elements.append({
                "tag": "markdown",
                "content": f"**🔮 预判追踪**\n{self._format_for_card(result.predictions)}"
            })

        # 行动建议
        if result.action_suggestions:
            elements.append({
                "tag": "markdown",
                "content": f"**⚡ 行动建议**\n{self._format_for_card(result.action_suggestions)}"
            })

        if not elements:
            return False

        # 卡片消息
        card = {
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": "AI 商业机会分析"
                },
                "template": "blue"
            },
            "elements": elements
        }

        msg = {
            "msg_type": "interactive",
            "card": card
        }

        return self._send_webhook(msg)

    def send_text(self, text: str) -> bool:
        """发送纯文本消息"""
        if not self.webhook_configured:
            return False

        msg = {
            "msg_type": "text",
            "content": {"text": text}
        }
        return self._send_webhook(msg)

    def _send_webhook(self, msg: dict) -> bool:
        """发送 Webhook 消息（带签名）"""
        url = self.webhook_url
        if self.webhook_secret:
            timestamp, sign = self._sign_webhook()
            url = f"{url}?timestamp={timestamp}&sign={sign}"

        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )

        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
            if resp.get("code") == 0 or resp.get("StatusCode") == 0:
                print("[飞书] 推送成功")
                return True
            else:
                print(f"[飞书] 推送失败: {resp.get('msg', 'unknown')}")
                return False
        except Exception as e:
            print(f"[飞书] 推送异常: {e}")
            return False

    @staticmethod
    def _format_for_card(text: str) -> str:
        """格式化文本用于飞书卡片"""
        if not text:
            return ""
        # 限制长度，避免卡片过长
        if len(text) > 1500:
            text = text[:1500] + "..."
        # 替换换行
        text = text.replace("\\n", "\n")
        return text

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 多维表格写入
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def write_predictions(self, predictions: List[Dict]) -> bool:
        """
        将预判写入飞书多维表格

        Args:
            predictions: 预判列表，每条含 date/content/confidence/signal_type/verify_date/status

        Returns:
            是否写入成功
        """
        if not self.bitable_configured:
            return False

        token = self._get_token()
        if not token:
            print("[飞书] 获取token失败，跳过多维表格写入")
            return False

        # 转换为飞书多维表格格式
        records = []
        for p in predictions:
            # 日期转换（飞书日期字段用毫秒时间戳）
            date_ts = self._date_to_timestamp(p.get("date", ""))
            verify_ts = self._date_to_timestamp(p.get("verify_date", ""))

            record = {
                "fields": {
                    "预判内容": p.get("content", ""),
                    "判断依据": p.get("basis", ""),
                    "日期": date_ts,
                    "信号类型": p.get("signal_type", "P"),
                    "置信度": p.get("confidence", "中"),
                    "验证日期": verify_ts,
                    "状态": p.get("status", "待验证"),
                }
            }
            records.append(record)

        if not records:
            return True

        # 批量写入
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{self.bitable_app_token}/tables/{self.bitable_table_id}/records/batch_create"

        data = json.dumps({"records": records}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            resp = json.loads(urllib.request.urlopen(req, timeout=15).read())
            if resp.get("code") == 0:
                count = len(resp.get("data", {}).get("records", []))
                print(f"[飞书] 已写入 {count} 条预判到多维表格")
                return True
            else:
                print(f"[飞书] 多维表格写入失败: {resp.get('msg', 'unknown')}")
                return False
        except Exception as e:
            print(f"[飞书] 多维表格写入异常: {e}")
            return False

    @staticmethod
    def _date_to_timestamp(date_str: str) -> int:
        """将日期字符串转为毫秒时间戳"""
        if not date_str:
            return int(time.time() * 1000)

        try:
            # 尝试 YYYY-MM-DD 格式
            dt = time.strptime(date_str, "%Y-%m-%d")
            return int(time.mktime(dt) * 1000)
        except ValueError:
            pass

        try:
            # 尝试 YYYY-MM-DD HH:MM:SS 格式
            dt = time.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            return int(time.mktime(dt) * 1000)
        except ValueError:
            return int(time.time() * 1000)


def create_feishu_client_from_env() -> FeishuClient:
    """从环境变量创建飞书客户端"""
    import os

    return FeishuClient(
        app_id=os.environ.get("FEISHU_APP_ID", ""),
        app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
        webhook_url=os.environ.get("FEISHU_WEBHOOK_URL", ""),
        webhook_secret=os.environ.get("FEISHU_WEBHOOK_SECRET", ""),
        bitable_app_token=os.environ.get("FEISHU_BITABLE_APP_TOKEN", ""),
        bitable_table_id=os.environ.get("FEISHU_BITABLE_TABLE_ID", ""),
    )
