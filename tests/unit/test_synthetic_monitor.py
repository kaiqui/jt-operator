from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


class TestSyntheticSiteHealthChecker:
    @pytest.mark.asyncio
    async def test_check_returns_healthy_on_http_200(self):
        mock_response = MagicMock(status_code=200)

        with patch(
            "src.infrastructure.synthetic.site_health.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get.return_value = mock_response
            mock_client_cls.return_value.__aenter__.return_value = mock_http

            from src.infrastructure.synthetic.site_health import (
                SyntheticSiteHealthChecker,
            )

            checker = SyntheticSiteHealthChecker(
                monitor_name="jeitto-homepage",
                target_url="https://jeitto.com.br",
                timeout_seconds=5.0,
            )

            result = await checker.check()

            assert result.is_healthy is True
            assert result.status_code == 200
            assert result.target_host == "jeitto.com.br"
            assert result.reason == "HTTP 200"

    @pytest.mark.asyncio
    async def test_check_returns_unhealthy_on_timeout(self):
        with patch(
            "src.infrastructure.synthetic.site_health.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_http = AsyncMock()
            mock_http.get.side_effect = httpx.TimeoutException("timeout")
            mock_client_cls.return_value.__aenter__.return_value = mock_http

            from src.infrastructure.synthetic.site_health import (
                SyntheticSiteHealthChecker,
            )

            checker = SyntheticSiteHealthChecker(
                monitor_name="jeitto-homepage",
                target_url="https://jeitto.com.br",
                timeout_seconds=1.0,
            )

            result = await checker.check()

            assert result.is_healthy is False
            assert result.status_code is None
            assert result.reason == "timeout"


class TestSyntheticSiteMetricsManager:
    @patch("src.infrastructure.datadog.managers.synthetic_metrics.ApiClient")
    @patch("src.infrastructure.datadog.managers.synthetic_metrics.MetricsApi")
    def test_send_check_result_submits_health_and_latency(
        self, mock_metrics_api_cls, mock_api_client_cls
    ):
        mock_api = MagicMock()
        mock_metrics_api_cls.return_value = mock_api
        mock_api_client_cls.return_value.__enter__ = lambda s: MagicMock()
        mock_api_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        from src.infrastructure.datadog.managers.synthetic_metrics import (
            SyntheticSiteMetricsManager,
        )

        manager = SyntheticSiteMetricsManager(
            api_key="fake-key", app_key=None, site="datadoghq.com"
        )

        result = manager.send_check_result(
            {
                "monitor_name": "jeitto-homepage",
                "target_url": "https://jeitto.com.br",
                "target_host": "jeitto.com.br",
                "is_healthy": True,
                "response_time_ms": 123.45,
                "status_code": 200,
                "reason": "HTTP 200",
                "checked_at": 1234567890,
            }
        )

        assert result is True
        mock_api.submit_metrics.assert_called_once()
        payload = mock_api.submit_metrics.call_args.kwargs["body"]
        assert len(payload.series) == 2
        assert payload.series[0].metric == manager.HEALTH_METRIC_NAME
        assert payload.series[1].metric == manager.LATENCY_METRIC_NAME


class TestSyntheticMonitorController:
    @pytest.mark.asyncio
    async def test_skips_when_no_target_url(self):
        with patch(
            "src.controllers.synthetic_monitor_controller.settings"
        ) as mock_settings:
            mock_settings.synthetic_monitor_name = "jeitto-homepage"
            mock_settings.synthetic_monitor_url = ""
            mock_settings.synthetic_monitor_timeout_seconds = 5.0

            from src.controllers.synthetic_monitor_controller import (
                run_synthetic_site_check,
            )

            await run_synthetic_site_check()

    @pytest.mark.asyncio
    async def test_full_cycle_healthy(self):
        mock_result = MagicMock()
        mock_result.is_healthy = True
        mock_result.status_code = 200
        mock_result.response_time_ms = 98.2
        mock_result.reason = "HTTP 200"
        mock_result.to_dict.return_value = {
            "monitor_name": "jeitto-homepage",
            "target_url": "https://jeitto.com.br",
            "target_host": "jeitto.com.br",
            "is_healthy": True,
            "response_time_ms": 98.2,
            "status_code": 200,
            "reason": "HTTP 200",
            "checked_at": 1234567890,
        }

        with (
            patch(
                "src.controllers.synthetic_monitor_controller.settings"
            ) as mock_settings,
            patch(
                "src.controllers.synthetic_monitor_controller.SyntheticSiteHealthChecker"
            ) as mock_checker_cls,
            patch(
                "src.controllers.synthetic_monitor_controller.SyntheticSiteMetricsManager"
            ) as mock_metrics_cls,
        ):
            mock_settings.synthetic_monitor_name = "jeitto-homepage"
            mock_settings.synthetic_monitor_url = "https://jeitto.com.br"
            mock_settings.synthetic_monitor_timeout_seconds = 5.0
            mock_settings.datadog_api_key = "fake-key"
            mock_settings.datadog_app_key = None
            mock_settings.datadog_site = "datadoghq.com"

            mock_checker_cls.return_value.check = AsyncMock(return_value=mock_result)
            mock_metrics_instance = MagicMock()
            mock_metrics_cls.return_value = mock_metrics_instance

            from src.controllers.synthetic_monitor_controller import (
                run_synthetic_site_check,
            )

            await run_synthetic_site_check()

            mock_metrics_instance.send_check_result.assert_called_once_with(
                mock_result.to_dict.return_value
            )

    @pytest.mark.asyncio
    async def test_skips_metrics_when_no_api_key(self):
        mock_result = MagicMock()
        mock_result.is_healthy = False
        mock_result.status_code = 503
        mock_result.response_time_ms = 250.0
        mock_result.reason = "HTTP 503"

        with (
            patch(
                "src.controllers.synthetic_monitor_controller.settings"
            ) as mock_settings,
            patch(
                "src.controllers.synthetic_monitor_controller.SyntheticSiteHealthChecker"
            ) as mock_checker_cls,
            patch(
                "src.controllers.synthetic_monitor_controller.SyntheticSiteMetricsManager"
            ) as mock_metrics_cls,
        ):
            mock_settings.synthetic_monitor_name = "jeitto-homepage"
            mock_settings.synthetic_monitor_url = "https://jeitto.com.br"
            mock_settings.synthetic_monitor_timeout_seconds = 5.0
            mock_settings.datadog_api_key = None

            mock_checker_cls.return_value.check = AsyncMock(return_value=mock_result)

            from src.controllers.synthetic_monitor_controller import (
                run_synthetic_site_check,
            )

            await run_synthetic_site_check()

            mock_metrics_cls.assert_not_called()
