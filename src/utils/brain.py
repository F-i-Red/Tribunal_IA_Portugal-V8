"""
TribunalBrain V5 — Corrigido para conformidade .gov
CORRECÇÕES:
  - Bloqueio de OpenRouter em GOV_MODE (soberania de dados RGPD)
  - Suporte a mTLS para Ollama em produção
  - Aviso explícito quando backend envia dados para fora da UE
  - Circuit breaker mantido; retry adaptativo mantido
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from .config import get_config, PAID_MODELS
from .logger import get_logger
from ..cache import get_cache


@dataclass
class LLMResponse:
    content: str
    model: str
    tokens_input: int
    tokens_output: int
    duration_ms: float
    cost_usd: float
    cached: bool = False
    backend: str = "openrouter"


class CircuitBreakerOpen(Exception):
    pass


class TribunalBrain:
    def __init__(self) -> None:
        self.config = get_config()
        self.logger = get_logger()
        self.cache = get_cache()
        self._lock = threading.Lock()
        self._failures = 0
        self._failure_threshold = 8
        self._circuit_open = False
        self._last_failure_time: Optional[float] = None
        self._circuit_timeout = 60.0
        self._total_cost = 0.0
        self._total_calls = 0
        self._total_tokens_in = 0
        self._total_tokens_out = 0

        # Aviso em arranque se backend vai enviar dados para fora da UE
        if not self.config.usar_ollama:
            self.logger.warning(
                "⚠️  AVISO DE PRIVACIDADE: backend=openrouter envia dados para servidores "
                "fora da UE. Certifica-te de que a anonimização está activa "
                "(ANONIMIZAR_ENTIDADES=true) e que existe DPA com OpenRouter."
            )

    # ── Circuit breaker ──────────────────────────────────────────────
    def _check_circuit(self) -> None:
        with self._lock:
            if self._circuit_open:
                elapsed = time.time() - (self._last_failure_time or 0)
                if elapsed > self._circuit_timeout:
                    self._circuit_open = False
                    self._failures = 0
                    self.logger.info("Circuit breaker resetado")
                else:
                    raise CircuitBreakerOpen(
                        f"Muitas falhas. Aguarda {int(self._circuit_timeout - elapsed)}s."
                    )

    def _record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._circuit_open = False

    def _record_failure(self, reason: str) -> None:
        with self._lock:
            self._failures += 1
            self._last_failure_time = time.time()
            self.logger.error(
                f"Falha API ({self._failures}/{self._failure_threshold}): {reason}"
            )
            if self._failures >= self._failure_threshold:
                self._circuit_open = True
                self.logger.warning("Circuit breaker ABERTO")

    # ── Interface pública ────────────────────────────────────────────
    def call(
        self,
        messages: List[Dict],
        system_prompt: Optional[str] = None,
        temperature: float = 0.15,
        max_tokens: int = 1500,
        usar_cache: bool = True,
    ) -> LLMResponse:
        self._check_circuit()

        # Cache lookup
        prompt_key = (messages[0].get("content", "") if messages else "")
        if usar_cache and self.config.cache_enabled:
            cached = self.cache.get(prompt_key, system_prompt, self.config.modelo_activo)
            if cached:
                self.logger.info("cache_hit")
                return LLMResponse(
                    content=cached.response,
                    model=cached.model,
                    tokens_input=cached.tokens_input,
                    tokens_output=cached.tokens_output,
                    duration_ms=0.0,
                    cost_usd=0.0,
                    cached=True,
                    backend="cache",
                )

        try:
            if self.config.usar_ollama:
                resultado = self._chamar_ollama(messages, system_prompt, temperature, max_tokens)
            else:
                resultado = self._chamar_openrouter_com_retry(
                    messages, system_prompt, temperature, max_tokens
                )
        except Exception as e:
            self._record_failure(str(e))
            raise RuntimeError(f"LLM falhou: {e}") from e

        self._record_success()

        with self._lock:
            self._total_cost += resultado.cost_usd
            self._total_calls += 1
            self._total_tokens_in += resultado.tokens_input
            self._total_tokens_out += resultado.tokens_output

        if usar_cache and self.config.cache_enabled:
            self.cache.put(
                prompt_key, resultado.content, system_prompt,
                self.config.modelo_activo,
                resultado.tokens_input, resultado.tokens_output, resultado.cost_usd,
            )

        return resultado

    # ── Ollama (com suporte a mTLS para produção governamental) ──────
    def _chamar_ollama(
        self,
        messages: List[Dict],
        system_prompt: Optional[str],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        start = time.time()
        msgs: List[Dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(messages)

        payload = {
            "model": self.config.ollama_modelo,
            "messages": msgs,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        # CORRIGIDO: suporte a mTLS para comunicação segura com Ollama em produção
        client_kwargs: Dict[str, Any] = {"timeout": self.config.request_timeout}
        if self.config.usar_mtls_ollama:
            client_kwargs["cert"] = (
                self.config.ollama_mtls_cert,
                self.config.ollama_mtls_key,
            )
            if self.config.ollama_mtls_ca:
                client_kwargs["verify"] = self.config.ollama_mtls_ca
            self.logger.info("Ollama: mTLS activo")

        try:
            with httpx.Client(**client_kwargs) as client:
                resp = client.post(
                    f"{self.config.ollama_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as e:
            raise RuntimeError(
                f"Ollama não está em execução em {self.config.ollama_url}. "
                "Inicia com: ollama serve"
            ) from e

        duration_ms = (time.time() - start) * 1000
        content = data.get("message", {}).get("content", "[Resposta vazia]")
        tin = data.get("prompt_eval_count", 0)
        tout = data.get("eval_count", 0)

        self.logger.log_api_call(self.config.ollama_modelo, tin, tout, duration_ms)
        return LLMResponse(
            content=content,
            model=self.config.ollama_modelo,
            tokens_input=tin,
            tokens_output=tout,
            duration_ms=duration_ms,
            cost_usd=0.0,
            backend="ollama",
        )

    # ── OpenRouter com retry ─────────────────────────────────────────
    def _chamar_openrouter_com_retry(
        self,
        messages: List[Dict],
        system_prompt: Optional[str],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        # CORRIGIDO: bloqueio em GOV_MODE — dados de cidadãos não saem da UE
        if self.config.gov_mode:
            raise RuntimeError(
                "[SOBERANIA DE DADOS] GOV_MODE=true não permite chamadas para OpenRouter.\n"
                "Configura BACKEND=ollama com modelo local na infraestrutura governamental."
            )

        attempts = max(self.config.max_retries, 6 if self.config.is_free_model else 3)
        ultimo_erro: Optional[Exception] = None

        for tentativa in range(1, attempts + 1):
            try:
                return self._chamar_openrouter(
                    messages, system_prompt, temperature, max_tokens
                )
            except httpx.HTTPStatusError as e:
                codigo = e.response.status_code
                if codigo == 401:
                    raise RuntimeError("Chave API inválida (401)") from e
                elif codigo == 402:
                    raise RuntimeError("Sem créditos OpenRouter (402)") from e
                elif codigo == 400:
                    raise RuntimeError(
                        f"Modelo inválido (400): {self.config.modelo}"
                    ) from e
                elif codigo in (429, 503, 502):
                    espera = min(
                        60 * tentativa if self.config.is_free_model else 20 * tentativa,
                        300,
                    )
                    self.logger.warning(
                        f"HTTP {codigo} — aguardando {espera}s "
                        f"(tentativa {tentativa}/{attempts})"
                    )
                    time.sleep(espera)
                    ultimo_erro = e
                else:
                    time.sleep(5 * tentativa)
                    ultimo_erro = e
            except httpx.TimeoutException as e:
                self.logger.warning(f"Timeout — tentativa {tentativa}/{attempts}")
                time.sleep(10 * tentativa)
                ultimo_erro = e
            except httpx.ConnectError as e:
                time.sleep(5 * tentativa)
                ultimo_erro = e

        raise RuntimeError(
            f"Esgotadas {attempts} tentativas: {ultimo_erro}"
        ) from ultimo_erro

    def _chamar_openrouter(
        self,
        messages: List[Dict],
        system_prompt: Optional[str],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        # Verificação de segurança inline (dupla protecção)
        if self.config.gov_mode:
            raise RuntimeError(
                "[SOBERANIA DE DADOS] OpenRouter bloqueado em GOV_MODE."
            )

        start = time.time()
        headers = {
            "Authorization": f"Bearer {self.config.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://tribunal-ia.gov.pt",
            "X-Title": "Tribunal IA Portugal V7",
        }

        msgs: List[Dict] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.extend(messages)

        timeout = max(
            self.config.request_timeout,
            240 if self.config.is_free_model else 120,
        )

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.config.modelo,
                    "messages": msgs,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        duration_ms = (time.time() - start) * 1000

        content = "[Resposta vazia]"
        try:
            msg = data["choices"][0]["message"]
            content = (
                msg.get("content")
                or msg.get("reasoning")
                or msg.get("text")
                or "[Resposta vazia]"
            )
            if not isinstance(content, str):
                content = str(content)
        except (KeyError, IndexError, TypeError):
            pass

        usage = data.get("usage", {})
        tin = usage.get("prompt_tokens", 0)
        tout = usage.get("completion_tokens", 0)
        modelo_real = data.get("model", self.config.modelo)
        cost = self._calcular_custo(tin, tout, modelo_real)

        self.logger.log_api_call(modelo_real, tin, tout, duration_ms)
        return LLMResponse(
            content=content,
            model=modelo_real,
            tokens_input=tin,
            tokens_output=tout,
            duration_ms=duration_ms,
            cost_usd=cost,
            backend="openrouter",
        )

    def _calcular_custo(self, tin: int, tout: int, modelo: str) -> float:
        if self.config.is_free_model or "free" in modelo.lower():
            return 0.0
        ip, op = PAID_MODELS.get(modelo, (1.0, 3.0))
        return (tin * ip + tout * op) / 1_000_000

    # ── Estatísticas ─────────────────────────────────────────────────
    def get_cost_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_calls": self._total_calls,
                "total_cost_usd": round(self._total_cost, 6),
                "total_cost_eur": round(self._total_cost * 0.92, 6),
                "total_tokens_in": self._total_tokens_in,
                "total_tokens_out": self._total_tokens_out,
                "modelo": self.config.modelo_activo,
                "backend": self.config.backend,
                "is_free": self.config.is_free_model,
                "gov_mode": self.config.gov_mode,
            }

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._circuit_open = False
            self._last_failure_time = None
            self._total_cost = 0.0
            self._total_calls = 0
            self._total_tokens_in = 0
            self._total_tokens_out = 0


_brain: Optional[TribunalBrain] = None
_brain_lock = threading.Lock()


def get_brain() -> TribunalBrain:
    global _brain
    with _brain_lock:
        if _brain is None:
            _brain = TribunalBrain()
    return _brain


def reset_brain() -> None:
    global _brain
    with _brain_lock:
        _brain = None
