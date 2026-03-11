import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from src.infrastructure.slack.repository import SlackRepository
from src.application.services.slack_service import SlackNotificationService


class TestMockedSlackIntegration:
    @pytest.fixture
    def mock_slack_clients(self):
        with patch(
            "src.infrastructure.slack.repository.AsyncWebClient"
        ) as mock_web_client:
            with patch(
                "src.infrastructure.slack.repository.AsyncWebhookClient"
            ) as mock_webhook_client:
                # Mock bot client
                mock_bot = AsyncMock()
                mock_bot.chat_postMessage.return_value = {"ok": True}
                mock_web_client.return_value = mock_bot

                # Mock webhook client
                mock_webhook = AsyncMock()
                mock_webhook.send.return_value = Mock(status_code=200)
                mock_webhook_client.return_value = mock_webhook

                yield {"bot": mock_bot, "webhook": mock_webhook}

    @pytest.mark.asyncio
    async def test_slack_repository_integration(self, mock_slack_clients):
        # Create repository
        repo = SlackRepository(
            bot_token="test-token",
            webhook_url="https://hooks.slack.com/test",
            default_channel="#test",
        )

        # Initialize
        await repo.initialize()
        assert repo._initialized is True

        # Send notification
        from src.domain.slack_models import (
            SlackNotification,
            NotificationSeverity,
            NotificationChannel,
        )

        notification = SlackNotification(
            title="Integration Test",
            message="Test message",
            severity=NotificationSeverity.INFO,
            channel=NotificationChannel.OPERATIONAL,
        )

        success = await repo.send_notification(notification)

        assert success is True
        # Should try webhook first
        mock_slack_clients["webhook"].send.assert_called_once()

    @pytest.mark.asyncio
    async def test_slack_service_integration(self):
        # Mock notifier
        mock_notifier = AsyncMock()
        mock_notifier.send_notification.return_value = True
        mock_notifier.initialize.return_value = None

        # Create service
        service = SlackNotificationService(mock_notifier)

        # Initialize
        await service.initialize()
        assert service._initialized is True

        # Send notification
        from src.domain.slack_models import NotificationSeverity, NotificationChannel

        success = await service.send_notification(
            title="Service Test",
            message="Test from service",
            severity=NotificationSeverity.WARNING,
            channel=NotificationChannel.ALERTS,
        )

        assert success is True
        mock_notifier.send_notification.assert_called_once()

        # Test connection
        success = await service.test_connection()
        assert success is True
