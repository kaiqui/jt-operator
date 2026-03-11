# logging_config.py
import logging
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, MutableMapping, Optional

import structlog
from structlog.typing import Processor


def configure_logging(level: int = logging.INFO) -> None:
    # Processors que vamos usar para logs "estrangeiros" (std lib)
    foreign_pre_chain: list[Processor] = [
        # move campos de `extra` do LogRecord para o event dict
        structlog.stdlib.ExtraAdder(),
        # adiciona nível (level=...)
        structlog.processors.add_log_level,
        # timestamp ISO
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    # Formatter que converte LogRecord -> structlog event -> JSON
    processor_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),  # output final
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
            structlog.contextvars.merge_contextvars,  # se usar contextvars para trace ids
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


class JsonLogFormatter(logging.Formatter):
    def add_fields(
        self,
        log_record: Dict[str, Any],
        record: logging.LogRecord,
        message_dict: Optional[Dict[str, Any]],
    ) -> None:
        log_record["timestamp"] = datetime.now(timezone.utc).isoformat()
        log_record["level"] = record.levelname
        log_record["function"] = record.funcName

        if message_dict:
            log_record.update(message_dict)

        if record.exc_info:
            try:
                log_record["stack_trace"] = traceback.format_exception(*record.exc_info)
            except Exception:
                log_record["stack_trace"] = str(record.exc_info)

    def format(self, record: logging.LogRecord) -> str:
        import json

        log_record: Dict[str, Any] = {}
        self.add_fields(log_record, record, None)
        log_record["message"] = record.getMessage()
        return json.dumps(log_record)


def ensure_json_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    # Remove existing handlers safely
    try:
        for h in list(getattr(root, "handlers", [])):
            root.removeHandler(h)
    except TypeError:
        # handlers may not be iterable in mocked environments
        root.removeHandler(None)  # type: ignore[arg-type]

    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.setLevel(level)
    root.addHandler(handler)

    logging.captureWarnings(True)


def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    ensure_json_logging()
    log = logging.getLogger(name)
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    log.setLevel(numeric_level)
    return log


class OperatorLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        extra = dict(self.extra or {})
        extra.update(kwargs.get("extra", {}))
        kwargs["extra"] = extra
        return msg, kwargs


def get_logger(
    name: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> logging.Logger:
    log = logging.getLogger(name)
    if context is not None:
        return OperatorLoggerAdapter(log, context)  # type: ignore[return-value]
    return log
