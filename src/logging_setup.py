import sys
from loguru import logger

from .logging_context import ORDER_LOG_FIELDS, METRIC_LOG_FIELDS, patch_record


def build_log_document(record) -> dict:
    """Shape a loguru record into the ELK document.

    Order fields are read from `record["extra"]`, where `patch_record` (the
    loguru patcher) put them at emit time.
    """
    doc = {
        "@timestamp": record["time"].isoformat(),
        "level": record["level"].name,
        "message": record["message"],
        "module": record["module"],
        "function": record["function"],
        "line": record["line"],
        "namespace": "VCCUS-CoinAutomation",
    }
    for key in ORDER_LOG_FIELDS + METRIC_LOG_FIELDS:
        value = record["extra"].get(key)
        if value not in (None, ""):
            doc[key] = value
    return doc


def setup_logging(log_dir: str, es_uri: str = "", es_username: str = "",
                  es_password: str = "", es_index_format: str = "coin-automation-logs"):
    logger.remove()
    # Only sets the patcher — handlers are left alone since `handlers` is not passed.
    logger.configure(patcher=patch_record)

    log_format = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {module}:{function}:{line} | {message}"

    logger.add(
        f"{log_dir}/coin-automation.log",
        rotation="10 MB",
        retention="30 days",
        level="DEBUG",
        format=log_format,
        enqueue=True,
    )

    logger.add(sys.stderr, level="INFO", format=log_format)

    if es_uri:
        try:
            from elasticsearch import Elasticsearch
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            class ElasticSearchSink:
                def __init__(self, es_uri, username, password, index_format):
                    self.es = Elasticsearch(
                        es_uri,
                        basic_auth=(username, password),
                        verify_certs=False,
                    )
                    self.index_format = index_format

                def __call__(self, message):
                    record = message.record
                    doc = build_log_document(record)
                    index_name = record["time"].strftime(f"{self.index_format}-%Y.%m.%d")
                    try:
                        self.es.index(index=index_name, document=doc)
                    except Exception:
                        pass

            logger.add(
                ElasticSearchSink(es_uri, es_username, es_password, es_index_format),
                level="INFO",
                enqueue=True,
            )
            logger.info("ELK logging initialized")
        except ImportError:
            logger.warning("elasticsearch package not installed, skipping ELK logging")
        except Exception as e:
            logger.warning(f"ELK logging setup failed: {e}")

    logger.info("Logging initialized")
