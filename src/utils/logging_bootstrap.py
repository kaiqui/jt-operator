from src.utils.json_logger import setup_logger


def init_logging() -> None:
    # Root do operador
    setup_logger("controller", level="INFO")
    setup_logger("SLOService", level="INFO")
    setup_logger("DatadogRepository", level="INFO")
    setup_logger("SLOManager", level="INFO")
    setup_logger("DeploymentsController", level="INFO")
    setup_logger("ServiceController", level="INFO")
