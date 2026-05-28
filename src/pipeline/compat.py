# ══════════════════════════════════════════════════════════════════════
# CAMADA DE COMPATIBILIDADE V6 → V8
# Adiciona CaseResult, process(), gerar_perguntas_instrucao(), _rag_ctx()
# para que o app.py (V6) continue a funcionar sem alterações.
# ══════════════════════════════════════════════════════════════════════
import hashlib as _hashlib


class CaseResult:
    """
    Adaptador de compatibilidade V6 → V8.
    Envolve EstadoCaso com a interface que o app.py espera.
    """
    def __init__(self, estado: EstadoCaso, modelo: str = "", backend: str = ""):
        self._e = estado
        self.modelo_usado = modelo
        self.backend_usado = backend

    # ── Identidade ────────────────────────────────────────────────────
    @property
    def case_id(self) -> str:
        return self._e.trace_id

    @property
    def trace_id(self) -> str:
        return self._e.trace_id

    @property
    def instancia_codigo(self) -> str:
        return self._e.instancia.codigo if self._e.instancia else "?"

    @property
    def instancia_nome(self) -> str:
        return self._e.instancia.nome if self._e.instancia else "?"

    # ── Peças processuais ─────────────────────────────────────────────
    @property
    def detetive_report(self) -> str:
        return self._e.detetive

    @property
    def acusacao(self) -> str:
        return self._e.acusacao

    @property
    def defesa(self) -> str:
        return self._e.defesa

    @property
    def sentenca_rigorosa(self) -> str:
        return self._e.sentenca_rigorosa

    @property
    def sentenca_garantista(self) -> str:
        return self._e.sentenca_garantista

    @property
    def sentenca_equilibrada(self) -> str:
        return self._e.sentenca_equilibrada

    @property
    def relatorio_consistencia(self) -> str:
        return self._e.consistencia

    @property
    def analise_tedh(self) -> str:
        return self._e.analise_tedh

    @property
    def sintese_judicial(self) -> str:
        return self._e.sintese_judicial

    @property
    def ata_final(self) -> str:
        return self._e.ata_final

    # ── Métricas ──────────────────────────────────────────────────────
    @property
    def grau_incerteza(self) -> str:
        return self._e.grau_incerteza

    @property
    def custo_total_usd(self) -> float:
        return self._e.custo_total_usd

    @property
    def duracao_s(self) -> float:
        return self._e.duracao_total_s

    @property
    def entities_found(self) -> list:
        return self._e.entidades_anonimizadas

    # ── Hash do documento ─────────────────────────────────────────────
    @property
    def doc_hash(self) -> str:
        txt = (self._e.ata_final or "").encode("utf-8")
        return _hashlib.sha256(txt).hexdigest()[:16]

    # ── Campos que não existem na V8 (compatibilidade) ───────────────
    @property
    def pdf_bytes(self):
        return None

    @property
    def validacao_citacoes(self) -> str:
        return ""

    # ── Voto de vencido ───────────────────────────────────────────────
    @property
    def voto_vencido(self):
        try:
            from ..auditoria import analisar_dissenso
            return analisar_dissenso(
                self._e.sentenca_rigorosa,
                self._e.sentenca_garantista,
                self._e.sentenca_equilibrada,
            )
        except Exception:
            return None

    # ── Fallback genérico para atributos desconhecidos ────────────────
    def __getattr__(self, name: str):
        return getattr(self._e, name, None)


# ── Métodos de compatibilidade adicionados ao CaseProcessor ──────────

def _compat_process(
    self,
    case_description: str,
    instancia_codigo: Optional[str] = None,
    dados_instrucao: Optional[Dict] = None,
    gerar_pdf: bool = True,
    pdf_docs_extraidos: Optional[List] = None,
    intervencao_utilizador: Optional[str] = None,
    defesa_pre_gerada: Optional[str] = None,
) -> "CaseResult":
    """
    Alias de compatibilidade V6: chama processar() e devolve CaseResult.
    O app.py continua a usar proc.process(...) sem alterações.
    """
    # Converter dados_instrucao do formato V6 para respostas simples
    respostas: Dict[str, str] = {}
    if dados_instrucao:
        for rid, rd in dados_instrucao.get("respostas", {}).items():
            if isinstance(rd, dict):
                respostas[rid] = rd.get("resposta", "")
            else:
                respostas[rid] = str(rd)

    # Se houver PDF docs, acrescentar ao caso
    desc_final = case_description
    if pdf_docs_extraidos:
        extras = "\n\n".join(str(d) for d in pdf_docs_extraidos if d)
        if extras:
            desc_final = f"{case_description}\n\n--- DOCUMENTOS ANEXOS ---\n{extras}"

    # Se houver defesa pré-gerada, colocar como intervenção do utilizador
    if defesa_pre_gerada and not intervencao_utilizador:
        intervencao_utilizador = defesa_pre_gerada

    estado = self.processar(
        case_description=desc_final,
        instancia_codigo=instancia_codigo,
        respostas_instrucao=respostas or None,
        intervencao_utilizador=intervencao_utilizador,
    )

    stats = self.brain.get_cost_stats()
    return CaseResult(estado, stats.get("modelo", ""), stats.get("backend", ""))


def _compat_gerar_perguntas_instrucao(
    self,
    case_description: str,
    instancia_codigo: Optional[str] = None,
) -> Dict:
    """Alias de compatibilidade V6 → processar_com_instrucao()."""
    _, perguntas = self.processar_com_instrucao(case_description, instancia_codigo)
    return perguntas


def _compat_rag_ctx(
    self,
    texto: str,
    instancia: Optional[str] = None,
) -> str:
    """
    Alias de compatibilidade V6.
    Devolve contexto RAG como string simples (sem lista de fragmentos).
    """
    from .instancias import INSTANCIAS, detectar_instancia_por_keywords
    inst_obj = None
    if instancia:
        inst_obj = INSTANCIAS.get(instancia) or INSTANCIAS.get(
            detectar_instancia_por_keywords(instancia)
        )
    ctx, _ = self._pesquisar_rag(texto[:500], inst_obj)
    return ctx


# Injectar métodos no CaseProcessor sem alterar a classe original
CaseProcessor.process = _compat_process
CaseProcessor.gerar_perguntas_instrucao = _compat_gerar_perguntas_instrucao
CaseProcessor._rag_ctx = _compat_rag_ctx
