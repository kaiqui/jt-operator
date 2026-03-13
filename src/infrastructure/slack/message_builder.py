from datetime import datetime
from typing import Dict, Any, List, Optional
from src.domain.slack_models import SlackMessageTemplate, NotificationSeverity


class SlackMessageBuilder:
    @staticmethod
    def create_blocks(
        title: str,
        message: str,
        severity: NotificationSeverity,
        template: SlackMessageTemplate,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        metadata = metadata or {}
        blocks = []

        # Header with emoji based on severity
        emoji_map = {
            NotificationSeverity.INFO: "ℹ️",
            NotificationSeverity.WARNING: "⚠️",
            NotificationSeverity.ERROR: "❌",
            NotificationSeverity.CRITICAL: "🚨",
        }

        emoji = emoji_map.get(severity, "📢")

        # Header block
        blocks.append(
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{emoji} {title}",
                    "emoji": True,
                },
            }
        )

        # Divider
        blocks.append({"type": "divider"})

        # Message block
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": message[: template.max_message_length],
                },
            }
        )

        # Context block with metadata
        context_elements: List[Dict[str, Any]] = []

        if template.include_timestamp and metadata.get("timestamp"):
            context_elements.append(
                {"type": "mrkdwn", "text": f"*Timestamp:* {metadata['timestamp']}"}
            )

        if template.include_cluster_info and metadata.get("cluster_name"):
            context_elements.append(
                {"type": "mrkdwn", "text": f"*Cluster:* {metadata['cluster_name']}"}
            )

        if template.include_namespace and metadata.get("namespace"):
            context_elements.append(
                {"type": "mrkdwn", "text": f"*Namespace:* {metadata['namespace']}"}
            )

        if metadata.get("operator"):
            context_elements.append(
                {"type": "mrkdwn", "text": f"*Operator:* {metadata['operator']}"}
            )

        if context_elements:
            context_block: Dict[str, Any] = {
                "type": "context",
                "elements": context_elements,
            }
            blocks.append(context_block)

        return blocks

    @staticmethod
    def create_attachments(
        message: str,
        severity: NotificationSeverity,
        template: SlackMessageTemplate,
        additional_fields: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, Any]]:
        color = template.color_map.get(severity, "#cccccc")

        attachment: Dict[str, Any] = {
            "color": color,
            "text": message[: template.max_message_length],
            "ts": datetime.utcnow().timestamp() if template.include_timestamp else None,
            "fields": additional_fields or [],
        }

        return [attachment]
