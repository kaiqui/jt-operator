import pytest
from unittest.mock import patch, Mock
from src.settings import Settings, SlackSettings


class TestSettings:
    """Test settings loading and validation."""
    
    def test_settings_initialization(self):
        """Test that settings can be initialized."""
        settings = Settings()
        assert settings is not None
        assert hasattr(settings, 'slack')
        assert hasattr(settings, 'kubernetes_namespace')
        assert hasattr(settings, 'datadog_api_key')
    
    def test_slack_settings_defaults(self):
        """Test Slack settings defaults."""
        slack_settings = SlackSettings()
        assert slack_settings.enabled is True
        assert slack_settings.default_channel == "#titlis-notifications"
        assert slack_settings.rate_limit_per_minute == 60
        assert slack_settings.timeout_seconds == 10.0
    
    @patch.dict('os.environ', {
        'SLACK_ENABLED': 'false',
        'SLACK_DEFAULT_CHANNEL': '#test',
        'DD_API_KEY': 'test-key'
    })
    def test_settings_from_env(self):
        """Test settings loaded from environment variables."""
        settings = Settings()
        assert settings.slack.enabled is False
        assert settings.slack.default_channel == "#test"
        assert settings.datadog_api_key == "test-key"
    
    def test_settings_singleton(self):
        """Test that settings is a singleton."""
        from src.settings import settings as singleton1
        from src.settings import settings as singleton2
        assert singleton1 is singleton2
    
    def test_slack_validation_aliases(self):
        """Test that Slack settings use correct validation aliases."""
        slack_settings = SlackSettings()
        # Verify that fields have validation_alias set
        assert hasattr(SlackSettings.__fields__['enabled'], 'field_info')
        field_info = SlackSettings.__fields__['enabled'].field_info
        assert field_info.validation_alias == "SLACK_ENABLED"