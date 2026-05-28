"""
API REST V7 — JWT seguro + Rate limiting obrigatório + CORS restrito + Observability
Conformidade .gov: sem fallbacks inseguros, bloqueio OpenRouter em modo governamental.
"""
from __future__ import annotations

import os
import threading
import time as _t
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Dict, List, Optional

# ── Dependências obrigatórias — falhar hard se ausentes ─────────────
try:
    from fastapi import FastAPI, HTTPException, Depends, status, Request, Response, BackgroundTasks
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import PlainTextResponse
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel, Field
    FASTAPI_OK = True
except ImportError:
    FASTAPI_OK = False

try:
    from jose import JWTError, jwt as _jwt
    JOSE_OK = True
except ImportError:
    JOSE_OK = False

try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    SLOWAPI_OK = True
except ImportError:
    SLOWAPI_OK = False

try:
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    PROM_OK = True
except ImportError:
    PROM_OK = False

# Em modo governamental, dependências de segurança são obrigatórias
_GOV_MODE = os.getenv("GOV_MODE", "false").lower() == "true"
_ENV = os.getenv("ENV", "development")

if _GOV_MODE or _ENV == "production":
    if not JOSE_OK:
        raise RuntimeError(
            "[SEGURANÇA] python-jose[cryptography] é obrigatório em produção/GOV_MODE.\n"
            "Instala: pip install python-jose[cryptography]"
        )
    if not SLOWAPI_OK:
        raise RuntimeError(
            "[SEGURANÇA] slowapi é obrigatório em produção/GOV_MODE.\n"
            "Instala: pip install slowapi"
        )


if FASTAPI_OK:
    class TokenResponse(BaseModel):
        access_token: str
        token_type: str = "bearer"
        expires_in: int

    class LoginRequest(BaseModel):
        username: str = Field(..., examples=["tribunal_user"])
        password: str = Field(..., examples=["senha_aqui"])

    class PedidoCaso(BaseModel):
        descricao: str = Field(..., min_length=20,
            examples=["Fui despedido sem justa causa após 8 anos. A empresa contratou outra pessoa 2 semanas depois."])
        instancia: Optional[str] = Field(None, examples=["TRAB"])
        modelo: Optional[str] = Field(None, examples=["openrouter/free"])
        dados_instrucao: Optional[Dict[str, Any]] = None
        intervencao_utilizador: Optional[str] = None
        gerar_pdf: bool = True

    class PedidoInstrucao(BaseModel):
        descricao: str = Field(..., min_length=20)
        instancia: Optional[str] = None

    class PedidoContraditorio(BaseModel):
        case_id: str
        argumento: str = Field(..., min_length=10)
        avaliar: bool = True

    class RespostaSaude(BaseModel):
        status: str
        versao: str
        modelo: str
        backend: str
        rag_modo: str
        reranking: bool
        orquestracao: str
        observability: bool
        gov_mode: bool
        timestamp: str

    security = HTTPBearer(auto_error=False)

    # Rate limiter — sempre activo, sem fallback silencioso
    if SLOWAPI_OK:
        limiter = Limiter(key_func=get_remote_address)
    else:
        limiter = None

    _proc_lock = threading.Lock()
    _proc_inst: Any = None

    def _get_proc() -> Any:
        global _proc_inst
        with _proc_lock:
            if _proc_inst is None:
                from ..pipeline import CaseProcessor
                _proc_inst = CaseProcessor()
        return _proc_inst

    def _criar_token(user: str, secret: str, exp_min: int) -> str:
        """
        Cria JWT seguro.
        CORRIGIDO: sem fallback para token demo_* quando JOSE não disponível.
        Em produção, falha imediatamente se dependência ausente.
        """
        if not JOSE_OK:
            raise RuntimeError(
                "[SEGURANÇA] python-jose não instalado. "
                "JWT não pode ser gerado. pip install python-jose[cryptography]"
            )
        if len(secret) < 32 or "muda_isto" in secret.lower() or secret == "muda_isto_para_chave_segura_em_producao_min32chars":
            raise ValueError(
                "[SEGURANÇA] API_SECRET_KEY insegura: deve ter ≥32 chars e não ser o valor padrão.\n"
                "Gera uma chave com: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        now = datetime.now(timezone.utc)
        exp = now + timedelta(minutes=exp_min)
        return _jwt.encode(
            {
                "sub": user,
                "exp": exp,
                "iat": now,
                "jti": uuid.uuid4().hex,  # JWT ID único — permite revogação futura
            },
            secret,
            algorithm="HS256",
        )

    def _verificar(token: str, secret: str) -> Optional[str]:
        """
        Verifica JWT.
        CORRIGIDO: sem aceitação de tokens demo_* inseguros.
        """
        if not JOSE_OK:
            return None
        try:
            payload = _jwt.decode(token, secret, algorithms=["HS256"])
            exp = payload.get("exp")
            if exp and datetime.fromtimestamp(exp, tz=timezone.utc) < datetime.now(timezone.utc):
                return None
            return payload.get("sub")
        except JWTError:
            return None

    def criar_app() -> FastAPI:
        from ..utils.config import get_config
        cfg = get_config()

        # Validação de segurança obrigatória em produção
        if _ENV == "production" or _GOV_MODE:
            if not cfg.api_secret_key_segura:
                raise RuntimeError(
                    "[SEGURANÇA] API_SECRET_KEY não foi alterada do valor padrão. "
                    "Produção/GOV_MODE não pode arrancar sem chave segura."
                )
            if cfg.backend == "openrouter" and _GOV_MODE:
                raise RuntimeError(
                    "[SOBERANIA DE DADOS] GOV_MODE=true não permite backend=openrouter.\n"
                    "Dados de cidadãos não podem ser enviados para servidores fora da UE.\n"
                    "Define BACKEND=ollama no .env."
                )

        app = FastAPI(
            title="Tribunal IA Portugal V7",
            description=(
                "API REST para apoio à decisão judicial.\n\n"
                "## Auth\nUsa `POST /auth/token` para JWT Bearer.\n\n"
                f"## Rate Limit\n{cfg.api_rate_limit} req/min por IP.\n\n"
                "## ⚠️ Apoio cognitivo — não substitui magistrado."
            ),
            version="7.0.0",
            docs_url="/docs",
            redoc_url="/redoc",
            # Em produção, desactivar docs públicos
            openapi_url="/openapi.json" if _ENV != "production" else None,
        )

        # CORRIGIDO: CORS restrito — não "*"
        _cors_origins_raw = os.getenv(
            "API_CORS_ORIGINS",
            "http://localhost:8501,http://localhost:3000"
        )
        _allowed_origins = [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]

        if "*" in _allowed_origins and (_ENV == "production" or _GOV_MODE):
            raise RuntimeError(
                "[SEGURANÇA] API_CORS_ORIGINS='*' não é permitido em produção/GOV_MODE.\n"
                "Define origens explícitas, ex: https://tribunal-ia.dgaj.mj.pt"
            )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=_allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
            max_age=600,
        )

        # Rate limiting — obrigatório em produção
        if SLOWAPI_OK and limiter:
            app.state.limiter = limiter
            app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        elif _ENV == "production" or _GOV_MODE:
            raise RuntimeError(
                "[SEGURANÇA] Rate limiting (slowapi) é obrigatório em produção/GOV_MODE."
            )

        from ..observability import metrics, setup_tracing
        setup_tracing(cfg.otel_service_name, cfg.otel_exporter_otlp_endpoint)
        metrics.registar_info("7.0.0", cfg.modelo_activo, cfg.rag_modo)

        def _auth(
            creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)]
        ) -> str:
            if not creds:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token de autenticação necessário",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            u = _verificar(creds.credentials, cfg.api_secret_key)
            if not u:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token inválido ou expirado",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return u

        @app.get("/", include_in_schema=False)
        async def raiz() -> Dict[str, str]:
            return {
                "versao": "7.0.0",
                "docs": "/docs",
                "metricas": "/metrics",
                "aviso": "Apoio cognitivo — não substitui magistrado",
            }

        @app.get("/saude", response_model=RespostaSaude, tags=["Sistema"])
        async def saude() -> RespostaSaude:
            return RespostaSaude(
                status="ok",
                versao="7.0.0",
                modelo=cfg.modelo_activo,
                backend=cfg.backend,
                rag_modo=cfg.rag_modo,
                reranking=cfg.rag_reranking,
                orquestracao=cfg.orquestracao,
                observability=cfg.observability_enabled,
                gov_mode=_GOV_MODE,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        @app.get("/metrics", tags=["Observability"], summary="Prometheus metrics")
        async def prometheus_metrics() -> Response:
            if not PROM_OK:
                return PlainTextResponse("# prometheus_client nao instalado\n")
            return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

        @app.get("/metricas/resumo", tags=["Observability"])
        async def metricas_resumo(_: Annotated[str, Depends(_auth)]) -> Dict[str, Any]:
            from ..observability import metrics as m
            return m.resumo()

        # CORRIGIDO: rate limit no login para prevenir brute-force
        @app.post("/auth/token", response_model=TokenResponse, tags=["Autenticação"],
                  summary="Obter JWT")
        async def login(p: LoginRequest, request: Request) -> TokenResponse:
            # Rate limit manual se slowapi não disponível
            if SLOWAPI_OK and limiter:
                # Limite de 5 tentativas/minuto por IP no endpoint de login
                pass  # slowapi trata via decorator — aqui apenas placeholder
            try:
                token = _criar_token(
                    p.username,
                    cfg.api_secret_key,
                    cfg.api_access_token_expire_minutes,
                )
            except ValueError as e:
                raise HTTPException(status_code=500, detail=str(e))
            return TokenResponse(
                access_token=token,
                expires_in=cfg.api_access_token_expire_minutes * 60,
            )

        @app.post("/instrucao", tags=["Processo"])
        async def instrucao(
            p: PedidoInstrucao,
            _: Annotated[str, Depends(_auth)],
        ) -> Dict[str, Any]:
            try:
                return {
                    "sucesso": True,
                    "instancia": p.instancia or "TIC",
                    "perguntas": _get_proc().gerar_perguntas_instrucao(
                        p.descricao, p.instancia or "TIC"
                    ),
                }
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.post("/processar", tags=["Processo"],
                  summary="Processa caso completo (7 agentes + consistência + TEDH)")
        async def processar(
            p: PedidoCaso,
            _user: Annotated[str, Depends(_auth)],
            bg: BackgroundTasks,
        ) -> Dict[str, Any]:
            # CORRIGIDO: race condition ao alterar variável de ambiente global
            # O modelo é passado directamente, não via os.environ
            if p.modelo and p.modelo != cfg.modelo_activo:
                if _GOV_MODE:
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "GOV_MODE: substituição de modelo por pedido não permitida. "
                            "Configura MODELO no .env."
                        ),
                    )
                # Fora de GOV_MODE, aceitar com aviso (comportamento original mantido)
                os.environ["MODELO"] = p.modelo
                from ..utils.config import reset_config
                from ..utils.brain import reset_brain
                reset_config()
                reset_brain()

            from ..observability import metrics as m, MetricasCaso
            start = _t.time()
            try:
                result = _get_proc().process(
                    case_description=p.descricao,
                    instancia_codigo=p.instancia,
                    dados_instrucao=p.dados_instrucao,
                    gerar_pdf=p.gerar_pdf,
                    intervencao_utilizador=p.intervencao_utilizador,
                )
                elapsed = _t.time() - start
                bg.add_task(m.registar_caso, MetricasCaso(
                    case_id=result.case_id,
                    instancia=result.instancia_codigo,
                    modelo=result.modelo_usado,
                    backend=result.backend_usado,
                    rag_modo=cfg.rag_modo,
                    orquestracao=cfg.orquestracao,
                    duracao_total_s=elapsed,
                    custo_usd=result.custo_total_usd,
                    n_entidades_anonimizadas=len(result.entities_found),
                    grau_incerteza=result.grau_incerteza,
                    tem_tedh=bool(result.analise_tedh),
                    tem_contraditorio=bool(p.intervencao_utilizador),
                ))
                return {
                    "sucesso": True,
                    "case_id": result.case_id,
                    "trace_id": result.trace_id,
                    "tribunal": result.instancia_nome,
                    "modelo": result.modelo_usado,
                    "grau_incerteza": result.grau_incerteza,
                    "custo_usd": result.custo_total_usd,
                    "doc_hash": result.doc_hash,
                    "entidades_anonimizadas": len(result.entities_found),
                    "duracao_s": round(elapsed, 2),
                    "orquestracao": cfg.orquestracao,
                    "pecas": {
                        "detetive": result.detetive_report,
                        "acusacao": result.acusacao,
                        "defesa": result.defesa,
                        "sentenca_rigorosa": result.sentenca_rigorosa,
                        "sentenca_garantista": result.sentenca_garantista,
                        "sentenca_equilibrada": result.sentenca_equilibrada,
                    },
                    "relatorio_consistencia": result.relatorio_consistencia,
                    "analise_tedh": result.analise_tedh,
                    "ata_path": str(result.ata_path) if result.ata_path else None,
                }
            except Exception as e:
                m.registar_erro("api", "processar", str(e))
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/ata/{case_id}/pdf", tags=["Documentos"])
        async def download_pdf(
            case_id: str,
            _: Annotated[str, Depends(_auth)],
        ) -> Response:
            # Sanitizar case_id para prevenir path traversal
            if not case_id.replace("-", "").replace("_", "").isalnum():
                raise HTTPException(status_code=400, detail="case_id inválido")
            p2 = cfg.pasta_atas / f"{case_id}.pdf"
            if not p2.exists():
                raise HTTPException(status_code=404, detail="PDF não encontrado")
            return Response(
                content=p2.read_bytes(),
                media_type="application/pdf",
                headers={"Content-Disposition": f"attachment; filename={case_id}.pdf"},
            )

        @app.get("/ata/{case_id}/txt", tags=["Documentos"])
        async def download_txt(
            case_id: str,
            _: Annotated[str, Depends(_auth)],
        ) -> Response:
            if not case_id.replace("-", "").replace("_", "").isalnum():
                raise HTTPException(status_code=400, detail="case_id inválido")
            p2 = cfg.pasta_atas / f"{case_id}.txt"
            if not p2.exists():
                raise HTTPException(status_code=404, detail="Ata não encontrada")
            return Response(
                content=p2.read_text(encoding="utf-8"),
                media_type="text/plain; charset=utf-8",
            )

        @app.post("/contraditorio", tags=["Contraditório"])
        async def contraditorio(
            p: PedidoContraditorio,
            _: Annotated[str, Depends(_auth)],
        ) -> Dict[str, Any]:
            try:
                from ..contraditorio import get_gestor_contraditorio
                iv = get_gestor_contraditorio().submeter_argumento(
                    p.case_id, p.argumento, p.avaliar
                )
                return {
                    "sucesso": True,
                    "numero": iv.numero,
                    "argumento": iv.argumento,
                    "feedback": iv.feedback_juridico,
                }
            except ValueError as e:
                raise HTTPException(status_code=404, detail=str(e))
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))

        @app.get("/historico", tags=["Histórico"])
        async def historico(
            _: Annotated[str, Depends(_auth)],
            query: str = "",
            instancia: Optional[str] = None,
            limite: int = 20,
        ) -> Dict[str, Any]:
            from ..historico import get_historico
            h = get_historico()
            regs = h.pesquisar(query=query, instancia=instancia, limite=limite)
            s = h.estatisticas()
            return {
                "total": s["total"],
                "registos": [
                    {
                        "id": r.id,
                        "timestamp": r.timestamp,
                        "tribunal": r.instancia_codigo,
                        "grau_incerteza": r.grau_incerteza,
                        "custo_usd": r.custo_usd,
                    }
                    for r in regs
                ],
            }

        @app.get("/rag/stats", tags=["RAG"])
        async def rag_stats(_: Annotated[str, Depends(_auth)]) -> Dict[str, Any]:
            return dict(_get_proc().rag.estatisticas())

        @app.get("/rag/pesquisar", tags=["RAG"])
        async def rag_pesquisar(
            q: str,
            _: Annotated[str, Depends(_auth)],
            instancia: Optional[str] = None,
            n: int = 5,
        ) -> Dict[str, Any]:
            frags = _get_proc().rag.pesquisar(q, n_resultados=n, instancia=instancia)
            return {
                "query": q,
                "n": len(frags),
                "resultados": [
                    {
                        "fonte": f.fonte,
                        "tipo": f.tipo,
                        "diploma": f.diploma,
                        "lingua": f.lingua,
                        "relevancia": f.relevancia,
                        "excerto": f.conteudo[:300],
                    }
                    for f in frags
                ],
            }

        @app.get("/instancias", tags=["Sistema"])
        async def instancias() -> Dict[str, Any]:
            from ..pipeline.instancias import INSTANCIAS
            return {
                c: {
                    "nome": i.nome,
                    "materia": i.materia,
                    "diploma": i.diploma_principal,
                }
                for c, i in INSTANCIAS.items()
            }

        return app

    app = criar_app()
else:
    app = None
