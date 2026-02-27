import traceback
from typing import List, Optional, Dict, Any

from src.domain.models import ServiceDefinition, SLO, SLOType, SLOTimeframe
from src.domain.github_models import DatadogProfilingMetrics
from src.application.ports.datadog_port import DatadogPort
from src.infrastructure.datadog.factory import DatadogManagerFactory
from src.utils.json_logger import get_logger


class DatadogRepository(DatadogPort):
    
    
    def __init__(self, api_key: str, app_key: Optional[str] = None, site: str = "datadoghq.com"):
        self.logger = get_logger(self.__class__.__name__)
        self.factory = DatadogManagerFactory(api_key, app_key, site)
    
    def get_service_definition(self, service_name: str) -> Optional[ServiceDefinition]:
        self.logger.info(
            "Buscando definição de serviço",
            extra={"service_name": service_name}
        )
        
        try:
            manager = self.factory.create_manager("service_definition")
            response = manager.get_single_service_definition(service_name)
            
            if not response:
                return None
            
            # Converte resposta para domínio
            data = response.get("data", {})
            attributes = data.get("attributes", {})
            schema = attributes.get("schema", {})
            
            return ServiceDefinition(
                dd_service=schema.get("dd_service"),
                description=schema.get("description"),
                team=schema.get("team"),
                tier=schema.get("tier"),
                tags=schema.get("tags", []),
                contacts=schema.get("contacts", []),
                links=schema.get("links", []),
                integrations=schema.get("integrations", {}),
                schema_version=schema.get("schema_version", "v2.2")
            )
            
        except Exception:
            self.logger.exception(
                "Erro ao buscar definição de serviço",
                extra={
                    "service_name": service_name,
                    
                    "response": response if "response" in locals() else None
                }
            )
            return None
    
    def get_service_slos(self, service_name: str) -> List[SLO]:
        self.logger.info(
            "Buscando SLOs do serviço",
            extra={"service_name": service_name}
        )
        
        try:
            manager = self.factory.create_manager("slo")
            response = manager.search_slos_by_service(service_name)
            
            slos = []
            slos_data = response.get("data", {}).get("attributes", {}).get("slos", [])
            
            for slo_data in slos_data:
                try:
                    slo = SLO(
                        name=slo_data.get("name"),
                        service_name=service_name,
                        slo_type=SLOType(slo_data.get("slo_type")),
                        target_threshold=self._extract_target_threshold(slo_data),
                        warning_threshold=self._extract_warning_threshold(slo_data),
                        timeframe=SLOTimeframe(slo_data.get("timeframe", "30d")),
                        description=slo_data.get("description"),
                        tags=slo_data.get("all_tags", []),
                        query=slo_data.get("query"),
                        thresholds=slo_data.get("thresholds", []),
                        slo_id=slo_data.get("id"),
                        created_at=slo_data.get("created_at"),
                        modified_at=slo_data.get("modified_at")
                    )
                    slos.append(slo)
                except Exception:
                    self.logger.warning(
                        "Erro ao converter SLO",
                        extra={
                            "slo_data": slo_data,
                            
                            "response": response if "response" in locals() else None
                        }
                    )
            
            self.logger.info(
                "SLOs encontrados",
                extra={
                    "service_name": service_name,
                    "count": len(slos)
                }
            )
            
            return slos
            
        except Exception:
            self.logger.exception(
                "Erro ao buscar SLOs do serviço",
                extra={
                    "service_name": service_name,
                    
                    "response": response if "response" in locals() else None
                }
            )
            return []
    
    def create_slo(self, slo: SLO) -> Optional[str]:
        self.logger.info(
            "Criando SLO",
            extra={
                "slo_name": slo.name,
                "service_name": slo.service_name,
                "slo_type": slo.slo_type.value
            }
        )
        
        try:
            manager = self.factory.create_manager("slo")
            
            if slo.slo_type == SLOType.TIME_SLICE:
                # Cria SLO time-slice
                response = manager.create_time_slice_slo_simple(
                    name=slo.name,
                    description=slo.description or "",
                    query=slo.query.get("query", "") if slo.query else "",
                    tags=slo.tags
                )
            else:
                # CORREÇÃO: Sempre usar os thresholds construídos corretamente no SLOService
                # Não recriar thresholds aqui
                if not slo.thresholds:
                    # Fallback: construir threshold correto
                    threshold_data = {
                        "timeframe": slo.timeframe.value,
                        "target": float(slo.target_threshold) if slo.target_threshold else 99.9
                    }
                    
                    if hasattr(slo, 'warning_threshold') and slo.warning_threshold:
                        threshold_data["warning"] = float(slo.warning_threshold)
                    
                    thresholds = [threshold_data]
                else:
                    thresholds = slo.thresholds
                
                # Log detalhado para debug
                self.logger.debug(
                    "Thresholds para criação do SLO",
                    extra={
                        "thresholds": str(thresholds),
                        # "thresholds_type": type(thresholds).__name__,
                        "slo_name": slo.name
                    }
                )
                
                # Cria SLO métrico - NÃO passar target_threshold e warning_threshold separadamente
                # Deixar o SLOManager usar os thresholds fornecidos
                response = manager.create_service_level_objective(
                    name=slo.name,
                    type=slo.slo_type.value,
                    thresholds=thresholds,  # Usar thresholds construídos
                    timeframe=slo.timeframe.value,
                    target_threshold=float(slo.target_threshold) if slo.target_threshold else None,
                    warning_threshold=float(slo.warning_threshold) if slo.warning_threshold else None,
                    tags=slo.tags,
                    description=slo.description or "",
                    query=slo.query
                )
            
            slo_id = self._extract_slo_id_from_response(response)
            
            if slo_id:
                self.logger.info(
                    "SLO criado com sucesso",
                    extra={
                        "slo_id": slo_id,
                        "slo_name": slo.name
                    }
                )
            
            return slo_id
            
        except Exception:
            full_stack_trace = traceback.format_exc()
            self.logger.exception(
                "Erro ao criar SLO",
                extra={
                    "slo_name": slo.name,
                    
                    "stack_trace": full_stack_trace,
                    "slo_data": {
                        "name": slo.name,
                        "type": slo.slo_type.value,
                        "target_threshold": getattr(slo, 'target_threshold', 'N/A'),
                        "warning_threshold": getattr(slo, 'warning_threshold', 'N/A'),
                        "timeframe": getattr(slo.timeframe, 'value', 'N/A') if hasattr(slo, 'timeframe') else 'N/A',
                        "thresholds": str(slo.thresholds) if hasattr(slo, 'thresholds') else 'N/A'
                    }
                }
            )
            return None
    
    def update_slo_apps(self, slo_id: str, slo: SLO) -> bool:
        self.logger.info(
            "Atualizando SLO",
            extra={
                "slo_id": slo_id,
                "slo_name": slo.name,
                "service": slo.service_name
            }
        )

        try:
            manager = self.factory.create_manager("slo")

            # Use os thresholds do SLO
            thresholds = slo.thresholds
            if not thresholds:
                # Construir threshold se não existir
                threshold_data = {
                    "timeframe": slo.timeframe.value,
                    "target": float(slo.target_threshold) if slo.target_threshold else 99.9
                }
                if hasattr(slo, 'warning_threshold') and slo.warning_threshold:
                    threshold_data["warning"] = float(slo.warning_threshold)
                thresholds = [threshold_data]

            result = manager.update_service_level_objective(
                slo_id=slo_id,
                name=slo.name,
                type=slo.slo_type.value,
                thresholds=thresholds,
                tags=slo.tags,
                description=slo.description or "",
                query=slo.query
            )

            return bool(result.get("success"))

        except Exception:
            self.logger.exception(
                "Erro ao atualizar SLO",
                extra={
                    "slo_id": slo_id
                }
            )
            return False
    
    def _extract_target_threshold(self, slo_data: Dict[str, Any]) -> float:
        
        thresholds = slo_data.get("thresholds", [])
        if thresholds:
            return float(thresholds[0].get("target", 99.9))
        return 99.9
    
    def _extract_warning_threshold(self, slo_data: Dict[str, Any]) -> float:
        
        thresholds = slo_data.get("thresholds", [])
        if thresholds and "warning" in thresholds[0]:
            return float(thresholds[0].get("warning", 99.0))
        return 99.0
    
    def _extract_slo_id_from_response(self, response: Dict[str, Any]) -> Optional[str]:
        try:
            # Primeiro, verifica se a resposta já tem slo_id
            if isinstance(response, dict) and "slo_id" in response:
                slo_id = response["slo_id"]
                if slo_id != "unknown":
                    return str(slo_id)
            
            # Verifica se tem campo 'response' (resposta do SLOManager)
            if isinstance(response, dict) and "response" in response:
                api_response = response["response"]
                
                # Se for string, tenta converter
                if isinstance(api_response, str):
                    try:
                        import json
                        api_response = json.loads(api_response)
                    except Exception:
                        self.logger.exception(
                            "Falha ao converter resposta da API de string para dict",
                            extra={
                                "response": api_response
                            }
                        )
                        pass
                
                # Processa a resposta da API
                if isinstance(api_response, dict):
                    if "data" in api_response:
                        data = api_response["data"]
                        
                        # Formato: {"data": [{"id": "xxx", ...}]}
                        if isinstance(data, list) and len(data) > 0:
                            first_item = data[0]
                            if isinstance(first_item, dict) and "id" in first_item:
                                return str(first_item["id"])
                        
                        # Formato: {"data": {"id": "xxx", ...}}
                        elif isinstance(data, dict) and "id" in data:
                            return str(data["id"])
            
            # Verifica direto na resposta (caso seja a resposta da API)
            if isinstance(response, dict) and "data" in response:
                data = response["data"]
                
                # Formato: {"data": [{"id": "xxx", ...}]}
                if isinstance(data, list) and len(data) > 0:
                    first_item = data[0]
                    if isinstance(first_item, dict) and "id" in first_item:
                        return str(first_item["id"])
                
                # Formato: {"data": {"id": "xxx", ...}}
                elif isinstance(data, dict) and "id" in data:
                    return str(data["id"])
            
            # Última tentativa: busca recursivamente por 'id'
            import json
            response_str = json.dumps(response, default=str)
            
            # Tenta encontrar padrão de ID
            import re
            id_pattern = r'"id"\s*:\s*"([a-f0-9]+)"'
            matches = re.findall(id_pattern, response_str)
            if matches:
                return str(matches[0])
            
            return None
            
        except Exception:
            self.logger.exception(
                "Falha ao extrair SLO ID",
                extra={
                    "response": str(response)[:500]
                }
            )
            return None

    def get_container_metrics(
        self,
        deployment_name: str,
        namespace: str,
        lookback_hours: int = 1,
    ) -> Optional[DatadogProfilingMetrics]:
        """
        Retorna métricas de profiling (CPU / memória) de um Deployment.
        Delega para DatadogMetricsManager.
        Retorna None se não houver dados ou em caso de erro.
        """
        try:
            manager = self.factory.create_manager("metrics")
            return manager.get_container_metrics(deployment_name, namespace, lookback_hours)
        except Exception:
            self.logger.exception(
                "Erro ao buscar métricas de container",
                extra={"deployment": deployment_name, "namespace": namespace},
            )
            return None
