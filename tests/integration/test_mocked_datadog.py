import pytest
from unittest.mock import Mock, patch
import json
from src.infrastructure.datadog.managers.slo import SLOManager
from src.infrastructure.datadog.repository import DatadogRepository


class TestMockedDatadogIntegration:
    """Integration tests with mocked Datadog API."""
    
    @pytest.fixture
    def mock_datadog_client(self):
        """Mock Datadog API client."""
        with patch('src.infrastructure.datadog.managers.slo.ServiceLevelObjectivesApi') as mock_api:
            mock_api_instance = Mock()
            mock_api.return_value = mock_api_instance
            
            # Mock API responses
            mock_create_response = Mock()
            mock_create_response.to_dict.return_value = {
                "data": {
                    "id": "test-slo-id-123",
                    "attributes": {"name": "test-slo"}
                }
            }
            mock_api_instance.create_slo.return_value = mock_create_response
            
            mock_search_response = Mock()
            mock_search_response.to_dict.return_value = {
                "data": {
                    "attributes": {
                        "slos": [
                            {
                                "data": {
                                    "id": "existing-slo-id",
                                    "attributes": {
                                        "name": "existing-slo",
                                        "slo_type": "metric",
                                        "thresholds": [{"target": 99.0}]
                                    }
                                }
                            }
                        ]
                    }
                }
            }
            mock_api_instance.search_slo.return_value = mock_search_response
            
            yield mock_api_instance
    
    def test_slo_lifecycle(self, mock_datadog_client):
        """Test complete SLO lifecycle with mocked API."""
        # Create SLOManager
        manager = SLOManager(
            api_key="test-key",
            app_key="test-app-key"
        )
        
        # Create SLO
        result = manager.create_service_level_objective(
            name="test-slo",
            type="metric",
            thresholds=[{"timeframe": "30d", "target": 99.9}],
            description="Integration test SLO"
        )
        
        assert result["success"] is True
        assert result["slo_id"] == "test-slo-id-123"
        mock_datadog_client.create_slo.assert_called_once()
        
        # Search SLOs
        search_result = manager.search_slos_by_service("test-service")
        assert "slos_count" in search_result
        assert search_result["slos_count"] == 1
        mock_datadog_client.search_slo.assert_called_once()
    
    def test_repository_integration(self):
        """Test DatadogRepository integration with mocked manager."""
        with patch('src.infrastructure.datadog.repository.DatadogManagerFactory') as mock_factory:
            # Mock manager
            mock_manager = Mock()
            mock_manager.search_slos_by_service.return_value = {
                "data": {
                    "attributes": {
                        "slos": [{
                            "id": "test-id",
                            "name": "test-slo",
                            "slo_type": "metric",
                            "thresholds": [{"target": 99.9}],
                            "timeframe": "30d",
                            "all_tags": ["env:test"]
                        }]
                    }
                }
            }
            
            mock_factory_instance = Mock()
            mock_factory_instance.create_manager.return_value = mock_manager
            mock_factory.return_value = mock_factory_instance
            
            # Create repository
            repo = DatadogRepository(
                api_key="test-key",
                app_key="test-key"
            )
            
            # Get SLOs
            slos = repo.get_service_slos("test-service")
            
            assert len(slos) == 1
            assert slos[0].name == "test-slo"
            mock_manager.search_slos_by_service.assert_called_once_with(
                "test-service", page_size=20, page_number=0
            )