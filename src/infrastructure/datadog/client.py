from typing import Optional, Any

from datadog_api_client import ApiClient, Configuration
from datadog_api_client.exceptions import ApiException

from src.utils.json_logger import get_logger
from src.settings import settings


class DatadogClientBase:
    
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        app_key: Optional[str] = None,
        site: Optional[str] = None,
        timeout: int = 60,
        max_retries: int = 3,
        enable_debug: bool = False
    ):
        self.logger = get_logger(self.__class__.__name__)
        
        # Carrega configurações
        self.api_key = api_key or settings.datadog_api_key
        self.app_key = app_key or settings.datadog_app_key
        self.site = site or settings.datadog_site
        
        # Validação
        if not self.api_key:
            raise ValueError("API Key do Datadog é obrigatória")
        
        self.timeout = timeout
        self.max_retries = max_retries
        self.enable_debug = enable_debug
        
        # Configuração
        self.configuration = self._create_configuration()
        self.api_client = self._create_api_client()
        
        self.logger.info(
            "Cliente Datadog inicializado",
            extra={"site": self.site, "has_app_key": bool(self.app_key)}
        )
    
    def _create_configuration(self) -> Configuration:
        
        configuration = Configuration()
        
        # Autenticação
        configuration.api_key["apiKeyAuth"] = self.api_key
        if self.app_key:
            configuration.api_key["appKeyAuth"] = self.app_key
        
        # Site
        configuration.server_variables["site"] = self.site
        
        # Configurações avançadas
        configuration.retries = self.max_retries
        configuration.timeout = self.timeout
        
        return configuration
    
    def _create_api_client(self) -> ApiClient:
        
        return ApiClient(self.configuration)
    
    def validate_connection(self) -> bool:
        self.logger.debug("Validando conexão com Datadog")
        
        try:
            from datadog_api_client.v1.api.authentication_api import AuthenticationApi
            api = AuthenticationApi(self.api_client)
            response = api.validate()
            
            self.logger.debug(
                "Conexão com Datadog validada",
                extra={"response": str(response)}
            )
            
            return True
            
        except ApiException as e:
            self.logger.error(
                "Falha na validação da conexão com Datadog",
                extra={
                    "status": e.status,
                    "reason": e.reason,
                    "body": e.body
                }
            )
            return False
    
    def execute_with_retry(self, func, *args, **kwargs) -> Any:
        import time
        
        for attempt in range(self.max_retries + 1):
            try:
                # DEBUG: Log da função que será executada
                if attempt == 0:
                    self.logger.info(
                        "Executando função com retry",
                        extra={
                            "func_type": type(func).__name__,
                            "func_str": str(func),
                            "args_count": len(args),
                            "kwargs_keys": list(kwargs.keys())
                        }
                    )
                
                return func(*args, **kwargs)
                    
            except ApiException as e:
                if e.status == 429 or e.status >= 500:
                    if attempt < self.max_retries:
                        delay = (2 ** attempt) + 1
                        self.logger.warning(
                            f"Tentativa {attempt + 1} falhou, retentando em {delay}s",
                            extra={
                                "status": e.status,
                                "reason": e.reason,
                                "attempt": attempt + 1
                            }
                        )
                        time.sleep(delay)
                        continue
                
                raise
                
            except Exception:
                self.logger.exception(
                    "Erro inesperado na execução",
                    extra={
                        "attempt": attempt + 1,
                    }
                )
                if attempt == self.max_retries:
                    raise
        
        raise Exception("Todas as tentativas falharam")