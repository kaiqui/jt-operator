import pytest
import asyncio
from unittest.mock import patch, Mock, AsyncMock
from src.infrastructure.slack.repository import SlackRepository
from src.application.services.slack_service import SlackNotificationService
from src.domain.slack_models import (
    SlackNotification,
    NotificationSeverity,
    NotificationChannel,
)
import sys
from tests.mock_kopf import MockKopf

sys.modules["kopf"] = MockKopf()


class TestSlackRepository:
    @pytest.fixture
    def slack_repo(self):
        with patch(
            "src.infrastructure.slack.repository.AsyncWebClient"
        ) as mock_web_client:
            with patch(
                "src.infrastructure.slack.repository.AsyncWebhookClient"
            ) as mock_webhook_client:
                # Mock clients
                mock_bot = AsyncMock()
                mock_web_client.return_value = mock_bot

                mock_webhook = AsyncMock()
                mock_webhook.send.return_value = Mock(status_code=200)
                mock_webhook_client.return_value = mock_webhook

                repo = SlackRepository(
                    bot_token="test-token",
                    webhook_url="https://hooks.slack.com/test",
                    default_channel="#test",
                    enabled=True,
                )

                # Manually set clients since initialize is mocked
                repo._bot_client = mock_bot
                repo._webhook_client = mock_webhook
                repo._initialized = True

                return repo

    @pytest.fixture
    def sample_notification(self):
        return SlackNotification(
            title="Test Notification",
            message="Test message content",
            severity=NotificationSeverity.INFO,
            channel=NotificationChannel.OPERATIONAL,
            namespace="default",
            pod_name="test-pod",
            metadata={"custom": "data"},
        )

    @pytest.mark.asyncio
    async def test_send_notification_via_webhook(self, slack_repo, sample_notification):
        # Mock the webhook send
        slack_repo._webhook_client.send.return_value = Mock(status_code=200)

        success = await slack_repo.send_notification(sample_notification)

        assert success is True
        slack_repo._webhook_client.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_notification_via_bot(self, slack_repo, sample_notification):
        # Make webhook fail
        slack_repo._webhook_client.send.return_value = Mock(status_code=500)

        # Mock bot success
        slack_repo._bot_client.chat_postMessage.return_value = {"ok": True}

        success = await slack_repo.send_notification(sample_notification)

        assert success is True
        slack_repo._webhook_client.send.assert_called_once()
        slack_repo._bot_client.chat_postMessage.assert_called_once()

    def test_should_send_filtering(self, slack_repo):
        notification = SlackNotification(
            title="Test",
            message="Test",
            severity=NotificationSeverity.INFO,
            channel=NotificationChannel.OPERATIONAL,
        )

        # Test enabled and initialized
        slack_repo.enabled = True
        slack_repo._initialized = True
        assert slack_repo._should_send(notification) is True

        # Test disabled
        slack_repo.enabled = False
        assert slack_repo._should_send(notification) is False
        slack_repo.enabled = True

        # Test not initialized
        slack_repo._initialized = False
        assert slack_repo._should_send(notification) is False
        slack_repo._initialized = True

        # Test disabled severity
        slack_repo.enabled_severities = [NotificationSeverity.WARNING]
        assert slack_repo._should_send(notification) is False
        slack_repo.enabled_severities = [NotificationSeverity.INFO]

        # Test disabled channel
        slack_repo.enabled_channels = [NotificationChannel.ALERTS]
        assert slack_repo._should_send(notification) is False

    def test_health_check(self, slack_repo):
        health = slack_repo.health_check()

        assert "enabled" in health
        assert "initialized" in health
        assert "has_bot_client" in health
        assert "has_webhook_client" in health
        assert health["enabled"] is True
        assert health["initialized"] is True


class TestSlackNotificationService:
    @pytest.fixture
    def mock_notifier(self):
        mock = AsyncMock()
        mock.send_notification.return_value = True
        mock.send_kopf_event.return_value = True
        mock.health_check.return_value = {"status": "ok"}
        mock.initialize.return_value = None
        mock.shutdown.return_value = None
        return mock

    @pytest.fixture
    def slack_service(self, mock_notifier):
        return SlackNotificationService(mock_notifier)

    @pytest.mark.asyncio
    async def test_send_notification(self, slack_service, mock_notifier):
        # Initialize first so the service is enabled
        slack_service._initialized = True
        mock_notifier.send_notification.return_value = True

        success = await slack_service.send_notification(
            title="Test",
            message="Test message",
            severity=NotificationSeverity.INFO,
            channel=NotificationChannel.OPERATIONAL,
        )

        assert success is True
        mock_notifier.send_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_service(self, slack_service, mock_notifier):
        success = await slack_service.initialize()

        assert success is True
        mock_notifier.initialize.assert_called_once()
        assert slack_service._initialized is True

    @pytest.mark.asyncio
    async def test_test_connection(self, slack_service, mock_notifier):
        # Service needs to be initialized first
        slack_service._initialized = True

        success = await slack_service.test_connection()

        assert success is True
        mock_notifier.send_notification.assert_called_once()

    def test_service_status(self, slack_service):
        slack_service._initialized = True
        status = slack_service.get_status()

        assert status["enabled"] is True
        assert status["initialized"] is True
        assert status["notifier_available"] is True

    def test_is_enabled(self, slack_service):
        slack_service._initialized = True
        assert slack_service.is_enabled() is True

        slack_service._initialized = False
        assert slack_service.is_enabled() is False

        slack_service.notifier = None
        assert slack_service.is_enabled() is False
