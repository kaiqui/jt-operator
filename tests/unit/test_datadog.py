import pytest
from unittest.mock import patch, Mock, AsyncMock
import json
from src.infrastructure.datadog.repository import DatadogRepository
from src.infrastructure.datadog.managers.slo import SLOManager
from src.domain.models import SLO, SLOType, SLOTimeframe
import sys
from tests.mock_kopf import MockKopf
sys.modules['kopf'] = MockKopf()

class TestDatadogRepository:
    """Test Datadog repository with mocked API."""
    
    @pytest.fixture
    def mock_slo_manager(self):
        """Mock SLOManager."""
        mock_manager = Mock(spec=SLOManager)
        
        # Mock search response
        mock_search_response = {
            "data": {
                "attributes": {
                    "slos": [{
                        "id": "test-slo-id",
                        "name": "test-slo",
                        "slo_type": "metric",
                        "thresholds": [{"target": 99.9, "warning": 99.0}],
                        "timeframe": "30d",
                        "description": "Test SLO",
                        "all_tags": ["env:test"],
                        "query": {"numerator": "test", "denominator": "test"}
                    }]
                }
            }
        }
        mock_manager.search_slos_by_service.return_value = mock_search_response
        
        # Mock create response
        mock_create_response = {
            "data": {
                "id": "new-slo-id",
                "attributes": {"name": "new-slo"}
            }
        }
        mock_manager.create_service_level_objective.return_value = {
            "success": True,
            "slo_id": "new-slo-id",
            "response": mock_create_response
        }
        
        # Mock update response
        mock_manager.update_service_level_objective.return_value = {
            "success": True
        }
        return mock_manager
    
    @pytest.fixture
    def datadog_repo(self, mock_slo_manager):
        """Create DatadogRepository with mocked factory."""
        with patch('src.infrastructure.datadog.repository.DatadogManagerFactory') as mock_factory:
            mock_factory_instance = Mock()
            mock_factory_instance.create_manager.return_value = mock_slo_manager
            mock_factory.return_value = mock_factory_instance
            
            repo = DatadogRepository(
                api_key="test-api-key",
                app_key="test-app-key",
                site="datadoghq.com"
            )
            
            # Replace the factory's manager with our mock
            repo.factory = mock_factory_instance
            
            return repo
    
    def test_get_service_slos(self, datadog_repo, mock_slo_manager):
        """Test getting SLOs for a service."""
        slos = datadog_repo.get_service_slos("test-service")
        
        assert len(slos) == 1
        assert slos[0].name == "test-slo"
        assert slos[0].slo_type == SLOType.METRIC
        assert slos[0].target_threshold == 99.9
        
        # Verify manager was called
        mock_slo_manager.search_slos_by_service.assert_called_once_with(
            "test-service"
        )
    
    def test_create_slo(self, datadog_repo, mock_slo_manager):
        """Test creating a SLO."""
        slo = SLO(
            name="new-slo",
            service_name="test-service",
            slo_type=SLOType.METRIC,
            target_threshold=99.9,
            warning_threshold=99.0,
            timeframe=SLOTimeframe.THIRTY_DAYS,
            description="New SLO",
            tags=["env:test"]
        )
        
        slo_id = datadog_repo.create_slo(slo)
        
        assert slo_id == "new-slo-id"
        mock_slo_manager.create_service_level_objective.assert_called_once()
    
    def test_update_slo(self, datadog_repo, mock_slo_manager):
        """Test updating a SLO."""
        slo = SLO(
            name="updated-slo",
            service_name="test-service",
            slo_type=SLOType.METRIC,
            target_threshold=99.95,  # Updated value
            warning_threshold=99.5,
            timeframe=SLOTimeframe.THIRTY_DAYS,
            tags=["env:test", "updated:true"]
        )
        
        success = datadog_repo.update_slo_apps("test-slo-id", slo)
        
        assert success is True
        mock_slo_manager.update_service_level_objective.assert_called_once()
    
    def test_extract_slo_id_from_response(self, datadog_repo):
        """Test SLO ID extraction from various response formats."""
        
        # Test format 1: direct id in response
        response1 = {"slo_id": "test-id-1"}
        id1 = datadog_repo._extract_slo_id_from_response(response1)
        assert id1 == "test-id-1"
        
        # Test format 2: data array
        response2 = {
            "data": [{"id": "test-id-2"}]
        }
        id2 = datadog_repo._extract_slo_id_from_response(response2)
        assert id2 == "test-id-2"
        
        # Test format 3: data object
        response3 = {
            "data": {"id": "test-id-3"}
        }
        id3 = datadog_repo._extract_slo_id_from_response(response3)
        assert id3 == "test-id-3"
        
        # Test format 4: nested response
        response4 = {
            "response": {
                "data": {"id": "test-id-4"}
            }
        }
        id4 = datadog_repo._extract_slo_id_from_response(response4)
        assert id4 == "test-id-4"
        
        # Test unknown format
        response5 = {"unknown": "format"}
        id5 = datadog_repo._extract_slo_id_from_response(response5)
        assert id5 is None


class TestSLOManager:
    """Test SLOManager with mocked Datadog API."""
    
    @pytest.fixture
    def slo_manager(self):
        """Create SLOManager with mocked API client."""
        with patch('src.infrastructure.datadog.managers.slo.ServiceLevelObjectivesApi') as mock_api:
            mock_api_instance = Mock()
            mock_api.return_value = mock_api_instance
            
            manager = SLOManager(
                api_key="test-api-key",
                app_key="test-app-key"
            )
            
            # Verify API was initialized
            assert manager.slo_api == mock_api_instance
            return manager
    
    def test_create_slo_with_thresholds(self, slo_manager):
        """Test SLO creation with provided thresholds."""
        with patch.object(slo_manager, 'execute_with_retry') as mock_execute:
            mock_response = Mock()
            mock_response.to_dict.return_value = {"data": {"id": "test-id"}}
            mock_execute.return_value = mock_response
            
            thresholds = [{
                "timeframe": "30d",
                "target": 99.9,
                "warning": 99.0
            }]
            
            result = slo_manager.create_service_level_objective(
                name="test-slo",
                type="metric",
                thresholds=thresholds,
                description="Test SLO"
            )
            
            assert result["success"] is True
            assert result["slo_id"] == "test-id"
            mock_execute.assert_called_once()
    
    def test_search_slos_by_service(self, slo_manager):
        """Test searching SLOs by service."""
        with patch.object(slo_manager, 'execute_with_retry') as mock_execute:
            mock_response = Mock()
            mock_response.to_dict.return_value = {
                "data": {
                    "attributes": {
                        "slos": [{"data": {"id": "test-id", "attributes": {"name": "test-slo"}}}]
                    }
                }
            }
            mock_execute.return_value = mock_response
            
            result = slo_manager.search_slos_by_service("test-service")
            
            assert "service_filter" in result
            assert result["service_filter"] == "test-service"
            assert "slos_count" in result
            mock_execute.assert_called_once()