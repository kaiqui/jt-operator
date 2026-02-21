# logging_config.py
import logging
import structlog
from typing import Optional

def configure_logging(level: int = logging.INFO) -> None:
    """
    Configure logging so that:
    - chamadas via logging.getLogger(...).info(..., extra={...}) preservam os extras
    - saída final é JSON (JSONRenderer)
    - código que usar structlog também funciona corretamente
    """

    # Processors que vamos usar para logs "estrangeiros" (std lib)
    foreign_pre_chain = [
        # move campos de `extra` do LogRecord para o event dict
        structlog.stdlib.ExtraAdder(),
        # adiciona nível (level=...)
        structlog.processors.add_log_level,
        # timestamp ISO
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    # Formatter que converte LogRecord -> structlog event -> JSON
    processor_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),   # output final
        foreign_pre_chain=foreign_pre_chain,
    )

    # Remove handlers antigos para evitar saída duplicada / texto cru
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setFormatter(processor_formatter)
    root.setLevel(level)
    root.addHandler(handler)

    # Opcional: encaminhar warnings do módulo warnings para logging
    logging.captureWarnings(True)

    # Configure structlog para quem usar structlog.get_logger
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,    # se usar contextvars para trace ids
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )

def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    API compatível com seu código atual.
    Use: self.logger = get_logger('nome_da_classe')
    """
    return logging.getLogger(name)
