from typing import Dict, List, Optional, Any

from datadog_api_client.v1.api.service_level_objectives_api import ServiceLevelObjectivesApi
from datadog_api_client.v1.model.service_level_objective_request import (
    ServiceLevelObjectiveRequest
)

from src.infrastructure.datadog.client import DatadogClientBase
from src.utils.json_logger import get_logger


class SLOManager(DatadogClientBase):
    
    
    def __init__(self, **kwargs):
        
        super().__init__(**kwargs)

        # IMPORTANTE: Importar aqui para garantir que está correto        
        # Verificar se api_client está definido
        if not hasattr(self, 'api_client'):
            self.logger.error("api_client não está definido no DatadogClientBase!")
            raise AttributeError("api_client não está definido")
        
        # Criar instância da API
        self.slo_api = ServiceLevelObjectivesApi(self.api_client)
        
        # Verificar se update_slo é um método
        if not hasattr(self.slo_api, 'update_slo'):
            self.logger.error("ServiceLevelObjectivesApi não tem método update_slo!")
            raise AttributeError("ServiceLevelObjectivesApi não tem método update_slo")
        
        if not callable(self.slo_api.update_slo):
            self.logger.error("slo_api.update_slo não é callable!")
            raise TypeError("slo_api.update_slo não é callable")
        
        self.logger = get_logger(self.__class__.__name__)
        self.logger.info(
            "SLOManager inicializado",
            extra={
                "slo_api_type": type(self.slo_api).__name__,
                "has_update_slo": hasattr(self.slo_api, 'update_slo'),
                "update_slo_callable": callable(self.slo_api.update_slo)
            }
        )

    def create_service_level_objective(
        self,
        name: str,
        type: str = "metric",
        thresholds: Optional[List[Dict[str, Any]]] = None,
        timeframe: str = "30d",
        target_threshold: float = 95.0,
        warning_threshold: Optional[float] = None,
        tags: Optional[List[str]] = None,
        description: str = "",
        query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        self.logger.info(
            "Criando SLO - Parâmetros recebidos",
            extra={
                "slo_name": name,
                "thresholds_provided": thresholds is not None,
                "target_threshold": target_threshold,
                "warning_threshold": warning_threshold,
                "timeframe": timeframe
            }
        )
        
        try:
            # Se thresholds foi fornecido, usar como está
            if thresholds is not None:
                self.logger.info(
                    "Usando thresholds fornecidos",
                    extra={"thresholds": str(thresholds)}
                )
            else:
                # Construir thresholds automaticamente
                self.logger.info(
                    "Construindo thresholds automaticamente",
                    extra={
                        "target": target_threshold,
                        "warning": warning_threshold
                    }
                )
                
                # ESTRUTURA CORRETA para a API do Datadog
                threshold_data = {
                    "timeframe": timeframe,
                    "target": float(target_threshold)
                }
                
                # Adiciona warning apenas se for fornecido
                if warning_threshold is not None:
                    threshold_data["warning"] = float(warning_threshold)
                
                thresholds = [threshold_data]
            
            # Log dos thresholds que serão enviados
            self.logger.info(
                "Thresholds final para envio",
                extra={
                    "thresholds": thresholds,
                    "thresholds_count": len(thresholds),
                    "first_threshold_keys": list(thresholds[0].keys()) if thresholds else []
                }
            )
            
            # Verificar estrutura dos thresholds
            for i, threshold in enumerate(thresholds):
                if "warning" in threshold and not isinstance(threshold["warning"], (int, float)):
                    self.logger.warning(
                        "Threshold warning não é numérico, convertendo",
                        extra={
                            "threshold_index": i,
                            "warning_value": threshold["warning"],
                            "warning_type": type(threshold["warning"]).__name__
                        }
                    )
                    try:
                        threshold["warning"] = float(threshold["warning"])
                    except (ValueError, TypeError):
                        self.logger.error(
                            "Não foi possível converter warning para float",
                            extra={"warning": threshold["warning"]}
                        )
                        del threshold["warning"]  # Remove se não puder converter
            
            slo_data = {
                "name": name,
                "type": type,
                "thresholds": thresholds,
                "tags": tags or [],
                "description": description
            }
            
            if type == "metric" and query:
                slo_data["query"] = query
            
            self.logger.info(
                "Dados do SLO preparados",
                extra={
                    "slo_name": name,
                    "thresholds": str(thresholds)
                }
            )
            
            # Log completo do payload
            import json
            self.logger.info(
                "Payload completo do SLO",
                extra={
                    "payload": json.dumps(slo_data, indent=2, default=str)
                }
            )
            
            response = self.execute_with_retry(
                self.slo_api.create_slo,
                body=ServiceLevelObjectiveRequest(**slo_data)
            )

            # DEBUG: Log a resposta completa
            self.logger.info(
                "Resposta da API do Datadog",
                extra={
                    "response_type": type(response).__name__,
                    "response_attrs": dir(response)[:20] if hasattr(response, '__dir__') else [],
                    "response_str": str(response)[:1000]
                }
            )
                
            # Extrai ID de forma segura
            slo_id = self._extract_slo_id(response)
            
            self.logger.info(
                "SLO criado com sucesso",
                extra={
                    "slo_name": name,
                    "slo_id": slo_id,
                    "slo_type": type
                }
            )
            
            return {
                "success": True,
                "slo_id": slo_id,
                "slo_name": name,
                "response": response.to_dict() if hasattr(response, 'to_dict') else str(response),
                "raw_response": str(response)
            }
            
        except Exception:
            self.logger.exception(
                "Erro ao criar SLO",
                extra={
                    "slo_name": name,
                    "thresholds": str(thresholds) if 'thresholds' in locals() else "N/A",
                    # "thresholds_type": type(thresholds).__name__ if 'thresholds' in locals() else "N/A"
                }
            )
            raise
    
    def create_time_slice_slo_simple(
        self,
        name: str,
        description: str,
        query: str = "trace.servlet.request{env:prod}",
        tags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        self.logger.info(
            "Criando SLO time-slice simples",
            extra={"name": name, "query": query}
        )
        
        try:
            from datadog_api_client.v1.model.service_level_objective_request import (
                ServiceLevelObjectiveRequest
            )
            from datadog_api_client.v1.model.slo_type import SLOType
            from datadog_api_client.v1.model.slo_time_slice_spec import SLOTimeSliceSpec
            from datadog_api_client.v1.model.slo_time_slice_condition import SLOTimeSliceCondition
            from datadog_api_client.v1.model.slo_time_slice_query import SLOTimeSliceQuery
            from datadog_api_client.v1.model.slo_time_slice_comparator import SLOTimeSliceComparator
            from datadog_api_client.v1.model.slo_formula import SLOFormula
            from datadog_api_client.v1.model.formula_and_function_metric_query_definition import (
                FormulaAndFunctionMetricQueryDefinition
            )
            from datadog_api_client.v1.model.formula_and_function_metric_data_source import (
                FormulaAndFunctionMetricDataSource
            )
            from datadog_api_client.v1.model.slo_threshold import SLOThreshold
            from datadog_api_client.v1.model.slo_timeframe import SLOTimeframe
            
            body = ServiceLevelObjectiveRequest(
                type=SLOType.TIME_SLICE,
                description=description,
                name=name,
                sli_specification=SLOTimeSliceSpec(
                    time_slice=SLOTimeSliceCondition(
                        query=SLOTimeSliceQuery(
                            formulas=[
                                SLOFormula(
                                    formula="query1",
                                ),
                            ],
                            queries=[
                                FormulaAndFunctionMetricQueryDefinition(
                                    data_source=FormulaAndFunctionMetricDataSource.METRICS,
                                    name="query1",
                                    query=query,
                                ),
                            ],
                        ),
                        comparator=SLOTimeSliceComparator.GREATER,
                        threshold=5.0,
                    ),
                ),
                tags=tags or ["env:prod"],
                thresholds=[
                    SLOThreshold(
                        target=97.0,
                        target_display="97.0",
                        timeframe=SLOTimeframe.SEVEN_DAYS,
                        warning=98.0,
                        warning_display="98.0",
                    ),
                ],
                timeframe=SLOTimeframe.SEVEN_DAYS,
                target_threshold=97.0,
                warning_threshold=98.0,
            )
            
            response = self.execute_with_retry(
                self.slo_api.create_slo,
                body=body
            )
            
            slo_id = self._extract_slo_id(response)
            
            self.logger.info(
                "SLO time-slice criado com sucesso",
                extra={
                    "slo_name": name,
                    "slo_id": slo_id
                }
            )
            
            return {
                "success": True,
                "slo_id": slo_id,
                "slo_name": name,
                "response": response.to_dict() if hasattr(response, 'to_dict') else str(response)
            }
            
        except Exception:
            self.logger.exception(
                "Erro ao criar SLO time-slice",
                extra={
                    "slo_name": name,
                }
            )
            raise
    
    def update_service_level_objective(
        self,
        slo_id: str,
        name: str,
        type: str,
        thresholds: List[Dict[str, Any]],
        tags: List[str],
        description: str = "",
        query: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Atualiza um SLO existente no Datadog.
        """
        self.logger.info(
            "Atualizando SLO no Datadog",
            extra={
                "slo_id": slo_id,
                "slo_name": name,
                "slo_type": type
            }
        )

        try:
            # LINHA 1: Primeira coisa a executar
            self.logger.info("DEBUG: Início do método update_service_level_objective")
            
            # Verificar estrutura dos thresholds - LINHA 2
            self.logger.info("DEBUG: Antes do for loop de thresholds")
            for i, threshold in enumerate(thresholds):
                self.logger.warning(f"DEBUG: Processando threshold {i}")
                if "warning" in threshold and not isinstance(threshold["warning"], (int, float)):
                    self.logger.warning(
                        "Threshold warning não é numérico, convertendo",
                        extra={
                            "threshold_index": i,
                            "warning_value": threshold["warning"],
                            "warning_type": type(threshold["warning"]).__name__
                        }
                    )
                    try:
                        threshold["warning"] = float(threshold["warning"])
                    except (ValueError, TypeError):
                        self.logger.error(
                            "Não foi possível converter warning para float",
                            extra={"warning": threshold["warning"]}
                        )
                        del threshold["warning"]

            # Log dos thresholds - LINHA 3
            self.logger.info("DEBUG: Antes de logar thresholds")
            self.logger.info(
                "Thresholds para atualização",
                extra={
                    "thresholds": thresholds,
                    "slo_id": slo_id
                }
            )

            # IMPORTANTE: Verificar se slo_api.update_slo é callable - LINHA 4
            self.logger.info("DEBUG: Antes de verificar callable")
            if not callable(self.slo_api.update_slo):
                self.logger.error(
                    "self.slo_api.update_slo não é um método!",
                    extra={
                        "type": type(self.slo_api.update_slo).__name__,
                        "value": str(self.slo_api.update_slo)[:100]
                    }
                )
                raise TypeError(f"self.slo_api.update_slo não é um método, é {type(self.slo_api.update_slo)}")

            # Usar ServiceLevelObjective para atualização - LINHA 5
            self.logger.info("DEBUG: Antes de importar ServiceLevelObjective")
            from datadog_api_client.v1.model.service_level_objective import ServiceLevelObjective
            
            slo_data = {
                "name": name,
                "type": type,
                "thresholds": thresholds,
                "tags": tags,
                "description": description
            }

            if type == "metric" and query:
                slo_data["query"] = query

            # Criar objeto ServiceLevelObjective - LINHA 6
            self.logger.info("DEBUG: Antes de criar ServiceLevelObjective")
            body = ServiceLevelObjective(**slo_data)

            # Log do body - LINHA 7
            self.logger.info("DEBUG: Antes de logar body")
            self.logger.info(
                "Body para atualização do SLO",
                extra={
                    "slo_id": slo_id,
                    "slo_name": name
                }
            )

            # DEBUG: Log da função que será chamada - LINHA 8
            self.logger.info("DEBUG: Antes de logar função")
            self.logger.info(
                "Chamando update_slo",
                extra={
                    "func": str(self.slo_api.update_slo),
                    "slo_id": slo_id,
                    "slo_name": name
                }
            )

            # Chamar a API - LINHA 9
            self.logger.info("DEBUG: Antes de chamar update_slo")
            response = self.slo_api.update_slo(
                slo_id=slo_id,
                body=body
            )

            self.logger.info("DEBUG: Após chamar update_slo")

            updated_id = self._extract_slo_id(response)

            self.logger.info(
                "SLO atualizado com sucesso",
                extra={
                    "slo_id": updated_id,
                    "slo_name": name
                }
            )

            return {
                "success": True,
                "slo_id": updated_id,
                "response": response.to_dict() if hasattr(response, "to_dict") else str(response)
            }

        except Exception as error:
            self.logger.exception(
                "Erro DETALHADO ao atualizar SLO no Datadog",
                extra={
                    "slo_id": slo_id,
                    "error_type": type(error).__name__,
                    "error_args": getattr(error, 'args', 'N/A'),
                    "traceback": self._get_full_traceback(error)
                }
            )
            raise

    def _get_full_traceback(self, error=None):
        
        import traceback
        if error:
            return ''.join(traceback.format_exception(type(error), error, error.__traceback__))
        else:
            return traceback.format_exc()

    def get_slo(self, slo_id: str) -> Dict[str, Any]:
        
        self.logger.info("Buscando SLO", extra={"slo_id": slo_id})
        
        try:
            response = self.execute_with_retry(
                self.slo_api.get_slo,
                slo_id=slo_id
            )
            
            return response.to_dict()
            
        except Exception:
            self.logger.exception(
                "Erro ao buscar SLO",
                extra={
                    "slo_id": slo_id,
                }
            )
            raise
    
    def list_slos(self, **kwargs) -> List[Dict[str, Any]]:
        
        self.logger.info("Listando SLOs")
        
        try:
            response = self.execute_with_retry(
                self.slo_api.list_slos,
                **kwargs
            )
            
            slos = response.data if hasattr(response, 'data') else []
            
            self.logger.info(
                "SLOs listados",
                extra={"count": len(slos)}
            )
            
            return [slo.to_dict() for slo in slos]
            
        except Exception:
            self.logger.exception(
                "Erro ao listar SLOs"
            )
            raise
    
    def search_slos(
        self,
        query: Optional[str] = None,
        page_size: int = 20,
        page_number: int = 0,
        **kwargs
    ) -> Dict[str, Any]:
        self.logger.info(
            "Buscando SLOs",
            extra={
                "query": query,
                "page_size": page_size,
                "page_number": page_number
            }
        )
        
        try:
            response = self.execute_with_retry(
                self.slo_api.search_slo,
                query=query,
                page_size=page_size,
                page_number=page_number,
                **kwargs
            )
            
            # Processa resposta de forma segura
            return self._process_search_response(response)
            
        except Exception:
            self.logger.exception(
                "Erro ao buscar SLOs",
                extra={
                    "query": query,
                }
            )
            raise
    
    def search_slos_by_service(
        self,
        service_name: str,
        page_size: int = 20,
        page_number: int = 0
    ) -> Dict[str, Any]:
        query = f"service:{service_name}"
        result = self.search_slos(
            query=query,
            page_size=page_size,
            page_number=page_number
        )
        
        result["service_filter"] = service_name
        result["service_query"] = query
        
        return result
    
    def delete_slo(self, slo_id: str) -> Dict[str, Any]:
        self.logger.info("Deletando SLO", extra={"slo_id": slo_id})
        
        try:
            response = self.execute_with_retry(
                self.slo_api.delete_slo,
                slo_id=slo_id
            )
            
            self.logger.info(
                "SLO deletado",
                extra={"slo_id": slo_id}
            )
            
            return {
                "success": True,
                "slo_id": slo_id,
                "response": response.to_dict() if hasattr(response, 'to_dict') else str(response)
            }
            
        except Exception:
            self.logger.exception(
                "Erro ao deletar SLO",
                extra={
                    "slo_id": slo_id,
                }
            )
            raise
    
    def _extract_slo_id(self, response: Any) -> str:
        
        try:
            # Se for um objeto da API do Datadog
            if hasattr(response, 'to_dict'):
                response_dict = response.to_dict()
                self.logger.info(
                    "Resposta convertida para dict",
                    extra={"response_dict": response_dict}
                )
                
                # Tenta obter o ID de várias formas
                if 'data' in response_dict:
                    data = response_dict['data']
                    
                    # Formato 1: lista de objetos
                    if isinstance(data, list) and len(data) > 0:
                        item = data[0]
                        if isinstance(item, dict) and 'id' in item:
                            return str(item['id'])
                        elif hasattr(item, 'id'):
                            return str(item.id)
                    
                    # Formato 2: objeto direto
                    elif isinstance(data, dict) and 'id' in data:
                        return str(data['id'])
                
                # Tenta diretamente no response_dict
                elif 'id' in response_dict:
                    return str(response_dict['id'])
            
            # Se for um dicionário Python
            elif isinstance(response, dict):
                self.logger.info(
                    "Resposta já é dict",
                    extra={"response_keys": list(response.keys())}
                )
                
                # Procura por 'id' em vários níveis
                if 'id' in response:
                    return str(response['id'])
                
                if 'data' in response:
                    data = response['data']
                    
                    # Formato 1: lista de objetos
                    if isinstance(data, list) and len(data) > 0:
                        item = data[0]
                        if isinstance(item, dict) and 'id' in item:
                            return str(item['id'])
                    
                    # Formato 2: objeto direto
                    elif isinstance(data, dict) and 'id' in data:
                        return str(data['id'])
            
            # Fallback: procura em atributos do objeto
            if hasattr(response, 'data') and hasattr(response.data, 'id'):
                return str(response.data.id)
            elif hasattr(response, 'id'):
                return str(response.id)
            
        except Exception:
            self.logger.exception(
                f"Erro ao extrair SLO ID: ",
                extra={
                    "response_type": type(response).__name__,
                    "response_attrs": dir(response)[:10] if hasattr(response, '__dir__') else []
                }
            )
        
        # Se não encontrou, retorna "unknown"
        return "unknown"
    
    def _process_search_response(self, response: Any) -> Dict[str, Any]:
        
        try:
            response_dict = response.to_dict()
        except Exception:
            # Fallback para extração manual
            response_dict = {"data": {"attributes": {"slos": []}}}
        
        data = response_dict.get("data", {})
        attributes = data.get("attributes", {})
        slos_data = attributes.get("slos", [])
        
        # Processa cada SLO
        slos = []
        for slo_item in slos_data:
            if isinstance(slo_item, dict) and "data" in slo_item:
                slo_data = slo_item["data"]
                slo_info = {
                    "id": slo_data.get("id"),
                    "type": slo_data.get("type"),
                    "name": slo_data.get("attributes", {}).get("name"),
                    "description": slo_data.get("attributes", {}).get("description"),
                    "slo_type": slo_data.get("attributes", {}).get("slo_type"),
                    "all_tags": slo_data.get("attributes", {}).get("all_tags", []),
                    "thresholds": slo_data.get("attributes", {}).get("thresholds", []),
                    "query": slo_data.get("attributes", {}).get("query", {}),
                }
                slos.append(slo_info)
        
        return {
            "data": {
                "attributes": {
                    "slos": slos
                }
            },
            "total_count": len(slos),
            "slos_count": len(slos)
        }