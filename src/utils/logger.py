"""
Logger estruturado V8 — structlog + fallback stdlib
Adicionados: set_agent, start_case, log_anonymization, log_rag, log_deliberacao
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any, List, Optional

try:
    import structlog
    STRUCTLOG_OK = True
except ImportError:
    STRUCTLOG_OK = False

_logger_inst: Optional["TribunalLogger"] = None
_logger_lock = threading.Lock()


class TribunalLogger:
    def __init__(self) -> None:
        level_str = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_str, logging.INFO)
        if STRUCTLOG_OK:
            structlog.configure(
                wrapper_class=structlog.make_filtering_bound_logger(level),
                logger_factory=structlog.PrintLoggerFactory(),
            )
            self._log = structlog.get_logger("tribunal_ia")
        else:
            logging.basicConfig(
                level=level,
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            )
            self._log = logging.getLogger("tribunal_ia")
        self._current_agent: str = "pipeline"
        self._current_trace: str = ""

    def info(self, msg: str, **kw: Any) -> None:
        if STRUCTLOG_OK:
            self._log.info(msg, agent=self._current_agent, **kw)
        else:
            self._log.info(f"[{self._current_agent}] {msg}")

    def warning(self, msg: str, **kw: Any) -> None:
        if STRUCTLOG_OK:
            self._log.warning(msg, agent=self._current_agent, **kw)
        else:
            self._log.warning(f"[{self._current_agent}] {msg}")

    def error(self, msg: str, **kw: Any) -> None:
        if STRUCTLOG_OK:
            self._log.error(msg, agent=self._current_agent, **kw)
        else:
            self._log.error(f"[{self._current_agent}] {msg}")

    def debug(self, msg: str, **kw: Any) -> None:
        if STRUCTLOG_OK:
            self._log.debug(msg, agent=self._current_agent, **kw)
        else:
            self._log.debug(f"[{self._current_agent}] {msg}")

    def set_agent(self, nome: str) -> None:
        self._current_agent = nome

    def start_case(self, case_description: str) -> str:
        trace_id = str(uuid.uuid4())[:12]
        self._current_trace = trace_id
        self.info("case_start", trace_id=trace_id,
                  desc_len=len(case_description))
        return trace_id

    def log_anonymization(self, n_entities: int, tipos: List[str]) -> None:
        self.info("anonymization", n_entities=n_entities, tipos=tipos)

    def log_rag(self, query: str, n_frags: int, top_relevancia: float) -> None:
        self.info("rag_query", query_len=len(query), n_frags=n_frags,
                  top_relevancia=round(top_relevancia, 3))

    def log_deliberacao(self, ronda: int, alteracoes: int) -> None:
        self.info("deliberacao", ronda=ronda, alteracoes_posicao=alteracoes)

    def log_api_call(self, modelo: str, tokens_in: int,
                     tokens_out: int, duration_ms: float) -> None:
        self.info("api_call", modelo=modelo, tokens_in=tokens_in,
                  tokens_out=tokens_out, duration_ms=round(duration_ms, 1))


def get_logger() -> TribunalLogger:
    global _logger_inst
    with _logger_lock:
        if _logger_inst is None:
            _logger_inst = TribunalLogger()
    return _logger_inst
