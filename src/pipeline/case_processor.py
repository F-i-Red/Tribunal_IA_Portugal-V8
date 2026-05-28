"""
CaseProcessor V8 — pipeline completo com:
  • QualificadorJuridicoAgent + multi-query RAG (5 queries)
  • AssistenteAgent (voz da vítima)
  • TEDH integrado na Defesa e nos Juízes Garantista/Equilibrado
  • Juízes paralelos com saída estruturada JSON
  • DeliberacaoAgent (1-2 rondas configuráveis)
  • SinteseJudicialAgent (maioria + voto de vencido)
  • ConsistenciaAgent com leitura estruturada (sem regex frágil)
  • Orçamento de contexto dinâmico (sem truncações fixas)
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..agents import (
    QualificadorJuridicoAgent, QualificacaoJuridica,
    DetetiveAgent, AssistenteAgent, AcusacaoAgent, DefesaAgent,
    JuizAgent, DeliberacaoAgent, SinteseJudicialAgent,
    ConsistenciaAgent, TEDHAgent, InstrucaoAgent, PDFExtractorAgent,
    DecisaoEstruturada,
)
from ..utils.brain import get_brain
from ..utils.config import get_config
from ..utils.logger import get_logger
from .instancias import InstanciaJudicial, detectar_instancia


# ── Estado do caso ────────────────────────────────────────────────────
@dataclass
class EstadoCaso:
    caso_original: str = ""
    caso_anonimizado: str = ""
    entidades_anonimizadas: List = field(default_factory=list)
    instancia: Optional[InstanciaJudicial] = None
    erros: List[str] = field(default_factory=list)

    # V8: qualificação jurídica antes do RAG
    qualificacao: Optional[QualificacaoJuridica] = None

    # Contextos RAG separados por finalidade
    ctx_rag_geral: str = ""
    ctx_rag_tedh: str = ""
    ctx_rag_defesa: str = ""   # inclui TEDH para defesa
    ctx_rag_acusacao: str = ""

    # Instrução
    perguntas_instrucao: Dict = field(default_factory=dict)
    respostas_instrucao: Dict = field(default_factory=dict)
    ctx_instrucao: str = ""

    # Agentes narrativos
    detetive: str = ""
    assistente: str = ""        # NOVO V8
    acusacao: str = ""
    defesa: str = ""

    # V8: decisões estruturadas (rascunho inicial)
    decisao_rig: Optional[DecisaoEstruturada] = None
    decisao_gar: Optional[DecisaoEstruturada] = None
    decisao_equ: Optional[DecisaoEstruturada] = None

    # V8: decisões após deliberação (finais)
    decisao_rig_final: Optional[DecisaoEstruturada] = None
    decisao_gar_final: Optional[DecisaoEstruturada] = None
    decisao_equ_final: Optional[DecisaoEstruturada] = None

    # V8: síntese judicial (maioria + voto vencido)
    sintese_judicial: str = ""

    # Consistência e TEDH
    consistencia: str = ""
    grau_incerteza: str = "N/A"
    analise_tedh: str = ""

    # Ata final
    ata_final: str = ""

    # Metadados
    duracao_total_s: float = 0.0
    custo_total_usd: float = 0.0
    trace_id: str = ""

    # Compatibilidade V7 — texto das sentenças
    @property
    def sentenca_rigorosa(self) -> str:
        d = self.decisao_rig_final or self.decisao_rig
        return d.to_texto_ata() if d else ""

    @property
    def sentenca_garantista(self) -> str:
        d = self.decisao_gar_final or self.decisao_gar
        return d.to_texto_ata() if d else ""

    @property
    def sentenca_equilibrada(self) -> str:
        d = self.decisao_equ_final or self.decisao_equ
        return d.to_texto_ata() if d else ""


# ── Processador principal ─────────────────────────────────────────────
class CaseProcessor:

    def __init__(self) -> None:
        self.config = get_config()
        self.logger = get_logger()
        self.brain = get_brain()

        # Instanciar agentes
        self.qualificador = QualificadorJuridicoAgent(self.brain, self.logger)
        self.detetive_ag   = DetetiveAgent(self.brain, self.logger)
        self.assistente_ag = AssistenteAgent(self.brain, self.logger)
        self.acusacao_ag   = AcusacaoAgent(self.brain, self.logger)
        self.defesa_ag     = DefesaAgent(self.brain, self.logger)
        self.deliberacao_ag = DeliberacaoAgent(self.brain, self.logger)
        self.sintese_ag    = SinteseJudicialAgent(self.brain, self.logger)
        self.consistencia_ag = ConsistenciaAgent(self.brain, self.logger)
        self.tedh_ag       = TEDHAgent(self.brain, self.logger)
        self.instrucao_ag  = InstrucaoAgent(self.brain, self.logger)
        self.pdf_ag        = PDFExtractorAgent(self.brain, self.logger)

        # Juízes com temperaturas configuradas
        self.juiz_rig = JuizAgent(self.brain, self.logger, "rigoroso",
                                   self.config.temp_juiz_rigoroso)
        self.juiz_gar = JuizAgent(self.brain, self.logger, "garantista",
                                   self.config.temp_juiz_garantista)
        self.juiz_equ = JuizAgent(self.brain, self.logger, "equilibrado",
                                   self.config.temp_juiz_equilibrado)

        # RAG
        self._rag = None

    # ── RAG ───────────────────────────────────────────────────────────
    def _obter_rag(self):
        if self._rag is None:
            from ..rag import MotorRAG
            cfg = self.config
            self._rag = MotorRAG(
                pasta_raiz=Path("."),
                modo=cfg.rag_modo,
                embedding_modelo=cfg.rag_embedding_modelo,
                reranker_modelo=cfg.rag_reranker_modelo,
                usar_reranking=cfg.rag_reranking,
                reranker_backend=cfg.rag_reranker_backend,
                cohere_api_key=cfg.cohere_api_key,
                top_k=cfg.rag_top_k,
                top_n=cfg.rag_top_n,
            )
            n = self._rag.indexar()
            self.logger.info("rag_indexado", n_fragmentos=n)
        return self._rag

    def _pesquisar_rag(self, query: str, instancia: Optional[InstanciaJudicial] = None,
                        top_n: Optional[int] = None) -> Tuple[str, List]:
        rag = self._obter_rag()
        codigo = instancia.codigo if instancia else None
        n = top_n or self.config.rag_top_n
        frags = rag.pesquisar(query, instancia=codigo, top_n=n)
        if not frags:
            return "", []
        ctx = "\n\n".join(
            f"[{f.tipo.upper()} | {f.fonte} | rel={f.relevancia:.2f}]\n{f.conteudo[:600]}"
            for f in frags
        )
        self.logger.log_rag(query, len(frags), frags[0].relevancia)
        return ctx, frags

    def _pesquisar_rag_multi(self, queries: List[str],
                              instancia: Optional[InstanciaJudicial] = None) -> Dict[str, str]:
        """Multi-query RAG: pesquisa separada para factos, normas, precedentes, TEDH, atenuantes."""
        resultados: Dict[str, str] = {}
        labels = ["factos", "normas", "precedentes", "tedh", "atenuantes"]
        rag = self._obter_rag()
        codigo = instancia.codigo if instancia else None

        for i, query in enumerate(queries[:6]):
            label = labels[i] if i < len(labels) else f"q{i}"
            try:
                frags = rag.pesquisar(query, instancia=codigo,
                                       top_n=self.config.rag_top_n)
                if frags:
                    ctx = "\n\n".join(
                        f"[{f.tipo.upper()} | {f.fonte}]\n{f.conteudo[:500]}"
                        for f in frags
                    )
                    resultados[label] = ctx
                    self.logger.log_rag(query, len(frags), frags[0].relevancia)
            except Exception as e:
                self.logger.warning(f"rag_multi query '{label}' falhou: {e}")
        return resultados

    def _montar_ctx_rag(self, resultados: Dict[str, str],
                         keys: List[str]) -> str:
        partes = []
        for k in keys:
            if k in resultados and resultados[k]:
                partes.append(f"=== CONTEXTO: {k.upper()} ===\n{resultados[k]}")
        return "\n\n".join(partes)

    # ── Anonimização ──────────────────────────────────────────────────
    def _anonimizar(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("anonimizar")
        if not self.config.anonimizar_entidades:
            estado.caso_anonimizado = estado.caso_original
            return estado
        try:
            from ..utils.anonymizer import PortugueseLegalAnonymizer
            anon = PortugueseLegalAnonymizer()
            resultado = anon.anonymize(estado.caso_original)
            estado.caso_anonimizado = resultado.text
            estado.entidades_anonimizadas = resultado.items
            self.logger.log_anonymization(
                len(resultado.items),
                list({e.label for e in resultado.items})
            )
        except Exception as e:
            self.logger.warning(f"Anonimização falhou: {e} — usando texto original")
            estado.caso_anonimizado = estado.caso_original
        return estado

    # ── Instrução ─────────────────────────────────────────────────────
    def _instrucao(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("instrucao")
        try:
            ctx, _ = self._pesquisar_rag(
                estado.caso_anonimizado[:500], estado.instancia
            )
            perguntas = self.instrucao_ag.executar(
                estado.caso_anonimizado, estado.instancia, ctx
            )
            estado.perguntas_instrucao = perguntas
        except Exception as e:
            self.logger.error(f"Instrução falhou: {e}")
            estado.erros.append(f"instrucao: {e}")
        return estado

    # ── Qualificador jurídico (NOVO V8) ───────────────────────────────
    def _qualificar(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("qualificador_juridico")
        if not self.config.rag_multi_query:
            # Fallback: usa texto bruto como query única
            q = QualificacaoJuridica(
                queries_rag=[estado.caso_anonimizado[:400]]
            )
            estado.qualificacao = q
            return estado
        try:
            estado.qualificacao = self.qualificador.executar(
                estado.caso_anonimizado, estado.instancia
            )
        except Exception as e:
            self.logger.error(f"Qualificador falhou: {e}")
            estado.qualificacao = QualificacaoJuridica(
                queries_rag=[estado.caso_anonimizado[:400]]
            )
            estado.erros.append(f"qualificador: {e}")
        return estado

    # ── RAG multi-query (NOVO V8) ──────────────────────────────────────
    def _rag_multi(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("rag_multi")
        queries = (estado.qualificacao.queries_rag
                   if estado.qualificacao else [estado.caso_anonimizado[:400]])
        try:
            resultados = self._pesquisar_rag_multi(queries, estado.instancia)
            # Contexto geral (todos menos TEDH)
            estado.ctx_rag_geral = self._montar_ctx_rag(
                resultados, ["factos", "normas", "precedentes", "atenuantes"]
            )
            # Contexto TEDH separado
            estado.ctx_rag_tedh = resultados.get("tedh", "")
            # Contexto defesa: geral + TEDH
            estado.ctx_rag_defesa = (
                estado.ctx_rag_geral
                + ("\n\n" + estado.ctx_rag_tedh if estado.ctx_rag_tedh else "")
            )
            # Contexto acusação: geral
            estado.ctx_rag_acusacao = estado.ctx_rag_geral

            if not estado.ctx_rag_geral:
                # Fallback BM25 simples
                ctx, _ = self._pesquisar_rag(queries[0], estado.instancia)
                estado.ctx_rag_geral = ctx
                estado.ctx_rag_acusacao = ctx
                estado.ctx_rag_defesa = ctx
        except Exception as e:
            self.logger.error(f"RAG multi falhou: {e}")
            estado.erros.append(f"rag_multi: {e}")
        return estado

    # ── Detetive ──────────────────────────────────────────────────────
    def _detetive(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("detetive")
        ctx_instrucao = ""
        if estado.respostas_instrucao:
            ctx_instrucao = "\n\nRESPOSTAS À INSTRUÇÃO:\n" + "\n".join(
                f"Q: {q}\nR: {r}"
                for q, r in estado.respostas_instrucao.items()
            )
        try:
            estado.detetive = self.detetive_ag.executar(
                estado.caso_anonimizado,
                ctx_instrucao,
                estado.ctx_rag_geral,
                estado.instancia,
            )
        except Exception as e:
            self.logger.error(f"Detetive falhou: {e}")
            estado.detetive = f"[ERRO detetive: {e}]"
            estado.erros.append(f"detetive: {e}")
        return estado

    # ── Assistente / Vítima (NOVO V8) ─────────────────────────────────
    def _assistente(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("assistente")
        if not self.config.assistente_enabled:
            return estado
        try:
            estado.assistente = self.assistente_ag.executar(
                estado.caso_anonimizado,
                estado.detetive,
                estado.ctx_rag_geral,
                estado.instancia,
            )
        except Exception as e:
            self.logger.error(f"Assistente falhou: {e}")
            estado.assistente = ""
            estado.erros.append(f"assistente: {e}")
        return estado

    # ── Acusação ──────────────────────────────────────────────────────
    def _acusacao(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("acusacao")
        try:
            estado.acusacao = self.acusacao_ag.executar(
                estado.caso_anonimizado,
                estado.detetive,
                estado.ctx_rag_acusacao,
                estado.instancia,
            )
        except Exception as e:
            self.logger.error(f"Acusação falhou: {e}")
            estado.acusacao = f"[ERRO acusação: {e}]"
            estado.erros.append(f"acusacao: {e}")
        return estado

    # ── Defesa (com TEDH integrado) ───────────────────────────────────
    def _defesa(self, estado: EstadoCaso,
                 intervencao_utilizador: Optional[str] = None) -> EstadoCaso:
        self.logger.set_agent("defesa")
        try:
            estado.defesa = self.defesa_ag.executar(
                estado.caso_anonimizado,
                estado.detetive,
                estado.acusacao,
                estado.ctx_rag_defesa,
                estado.instancia,
                ctx_tedh=estado.ctx_rag_tedh,
                intervencao_utilizador=intervencao_utilizador,
            )
        except Exception as e:
            self.logger.error(f"Defesa falhou: {e}")
            estado.defesa = f"[ERRO defesa: {e}]"
            estado.erros.append(f"defesa: {e}")
        return estado

    # ── Juízes (paralelo ou sequencial) ──────────────────────────────
    def _executar_juiz(self, juiz: JuizAgent, estado: EstadoCaso) -> DecisaoEstruturada:
        return juiz.executar(
            estado.caso_anonimizado,
            estado.detetive,
            estado.acusacao,
            estado.defesa,
            estado.instancia,
            estado.ctx_rag_geral,
            ctx_tedh=estado.ctx_rag_tedh,
            assistente=estado.assistente,
        )

    def _juizes(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("juizes")
        juizes = [
            (self.juiz_rig, "decisao_rig"),
            (self.juiz_gar, "decisao_gar"),
            (self.juiz_equ, "decisao_equ"),
        ]

        if self.config.paralelismo:
            self.logger.info("juizes_paralelo")
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
                futuros = {
                    ex.submit(self._executar_juiz, j, estado): attr
                    for j, attr in juizes
                }
                for fut in concurrent.futures.as_completed(futuros):
                    attr = futuros[fut]
                    try:
                        setattr(estado, attr, fut.result())
                    except Exception as e:
                        self.logger.error(f"Juiz {attr} falhou: {e}")
                        estado.erros.append(f"{attr}: {e}")
        else:
            for juiz, attr in juizes:
                try:
                    setattr(estado, attr, self._executar_juiz(juiz, estado))
                except Exception as e:
                    self.logger.error(f"Juiz {attr} falhou: {e}")
                    estado.erros.append(f"{attr}: {e}")
        return estado

    # ── Deliberação (NOVO V8) ─────────────────────────────────────────
    def _deliberacao(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("deliberacao")
        if not self.config.deliberacao_enabled:
            estado.decisao_rig_final = estado.decisao_rig
            estado.decisao_gar_final = estado.decisao_gar
            estado.decisao_equ_final = estado.decisao_equ
            return estado

        decisoes_actuais = {
            "rigoroso": estado.decisao_rig,
            "garantista": estado.decisao_gar,
            "equilibrado": estado.decisao_equ,
        }

        for ronda in range(1, self.config.deliberacao_rondas + 1):
            self.logger.info("deliberacao_ronda", ronda=ronda)
            novas: Dict[str, DecisaoEstruturada] = {}
            alteracoes = 0

            for perfil, minha in decisoes_actuais.items():
                if minha is None:
                    continue
                outras = [d for p, d in decisoes_actuais.items()
                          if p != perfil and d is not None]
                try:
                    nova = self.deliberacao_ag.executar_ronda(
                        estado.instancia, perfil, minha, outras, ronda
                    )
                    novas[perfil] = nova
                    if not nova.manteve_posicao:
                        alteracoes += 1
                except Exception as e:
                    self.logger.error(f"Deliberação {perfil} r{ronda}: {e}")
                    novas[perfil] = minha

            self.logger.log_deliberacao(ronda, alteracoes)
            decisoes_actuais = novas
            if alteracoes == 0:
                self.logger.info("deliberacao_consenso", ronda=ronda)
                break

        estado.decisao_rig_final = decisoes_actuais.get("rigoroso", estado.decisao_rig)
        estado.decisao_gar_final = decisoes_actuais.get("garantista", estado.decisao_gar)
        estado.decisao_equ_final = decisoes_actuais.get("equilibrado", estado.decisao_equ)
        return estado

    # ── Síntese Judicial (NOVO V8) ─────────────────────────────────────
    def _sintese(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("sintese_judicial")
        if not self.config.sintese_judicial_enabled:
            return estado
        decisoes = [d for d in [
            estado.decisao_rig_final,
            estado.decisao_gar_final,
            estado.decisao_equ_final,
        ] if d is not None]
        if len(decisoes) < 2:
            return estado
        try:
            estado.sintese_judicial = self.sintese_ag.executar(
                estado.instancia, decisoes
            )
        except Exception as e:
            self.logger.error(f"Síntese falhou: {e}")
            estado.erros.append(f"sintese: {e}")
        return estado

    # ── Consistência ──────────────────────────────────────────────────
    def _consistencia(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("consistencia")
        if not self.config.consistencia_check:
            return estado
        d_rig = estado.decisao_rig_final or estado.decisao_rig
        d_gar = estado.decisao_gar_final or estado.decisao_gar
        d_equ = estado.decisao_equ_final or estado.decisao_equ
        if all([d_rig, d_gar, d_equ]):
            try:
                estado.consistencia, estado.grau_incerteza = (
                    self.consistencia_ag.executar_estruturado(
                        estado.instancia, d_rig, d_gar, d_equ
                    )
                )
            except Exception as e:
                self.logger.error(f"Consistência estruturada falhou: {e}")
                # Fallback legacy
                try:
                    estado.consistencia = self.consistencia_ag.executar(
                        estado.instancia,
                        d_rig.to_texto_ata() if d_rig else "",
                        d_gar.to_texto_ata() if d_gar else "",
                        d_equ.to_texto_ata() if d_equ else "",
                    )
                    estado.grau_incerteza = self.consistencia_ag._extrair_grau(
                        estado.consistencia
                    )
                except Exception as e2:
                    estado.erros.append(f"consistencia: {e2}")
        return estado

    # ── TEDH standalone ───────────────────────────────────────────────
    def _tedh_standalone(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("tedh")
        if not estado.ctx_rag_tedh:
            return estado
        try:
            estado.analise_tedh = self.tedh_ag.executar(
                estado.instancia,
                estado.caso_anonimizado[:600],
                estado.ctx_rag_tedh,
            )
        except Exception as e:
            self.logger.error(f"TEDH falhou: {e}")
            estado.erros.append(f"tedh: {e}")
        return estado

    # ── Composição da ata final ───────────────────────────────────────
    def _compor_ata(self, estado: EstadoCaso) -> EstadoCaso:
        self.logger.set_agent("ata")
        inst = estado.instancia
        cfg = self.config
        partes: List[str] = []

        def sec(titulo: str, conteudo: str) -> None:
            if conteudo and conteudo.strip():
                partes.append(f"\n{'═'*70}\n{titulo}\n{'═'*70}\n{conteudo.strip()}")

        partes.append(
            f"TRIBUNAL IA — PORTUGAL V8\n"
            f"{'━'*70}\n"
            f"Tribunal: {inst.nome}\n"
            f"Matéria: {inst.materia}\n"
            f"Diploma: {inst.diploma_principal}\n"
            f"Trace ID: {estado.trace_id}\n"
            f"Duração: {estado.duracao_total_s:.1f}s\n"
            f"Custo estimado: ${estado.custo_total_usd:.4f} USD\n"
        )

        if estado.qualificacao:
            q = estado.qualificacao
            normas = "; ".join(
                f"{n.get('diploma','?')} art.{n.get('artigo','?')}"
                for n in q.normas_candidatas[:5]
            )
            sec("QUALIFICAÇÃO JURÍDICA PRELIMINAR",
                f"{q.qualificacao_provisoria}\nNormas candidatas: {normas}\n"
                f"Flags: {', '.join(q.flags) or 'nenhuma'}")

        sec("RELATÓRIO DE INSTRUÇÃO (Detetive)", estado.detetive)

        if estado.assistente:
            sec("VOZ DO ASSISTENTE / VÍTIMA", estado.assistente)

        sec(f"ALEGAÇÕES DA ACUSAÇÃO ({inst.termo_mp})", estado.acusacao)
        sec(f"ALEGAÇÕES DA DEFESA ({inst.termo_defesa})", estado.defesa)

        # Decisões com metadados estruturados
        for label, dec in [
            ("SENTENÇA — JUIZ RIGOROSO", estado.decisao_rig_final or estado.decisao_rig),
            ("SENTENÇA — JUIZ GARANTISTA", estado.decisao_gar_final or estado.decisao_gar),
            ("SENTENÇA — JUIZ EQUILIBRADO", estado.decisao_equ_final or estado.decisao_equ),
        ]:
            if dec:
                header = (f"[Decisão: {dec.decisao} | "
                          f"Sanção: {dec.sancao_proposta} | "
                          f"Confiança: {dec.confianca:.0%} | "
                          f"Incerteza: {dec.grau_incerteza_factual} | "
                          f"TEDH: {dec.conformidade_tedh}]")
                if not dec.manteve_posicao and dec.razao_alteracao:
                    header += f"\n[Posição revista após deliberação: {dec.razao_alteracao}]"
                sec(label, header + "\n\n" + dec.to_texto_ata())

        if estado.sintese_judicial:
            sec("SÍNTESE JUDICIAL — DECISÃO DO COLECTIVO "
                "(maioria + voto de vencido)", estado.sintese_judicial)

        if estado.consistencia:
            sec(f"RELATÓRIO DE CONSISTÊNCIA E INCERTEZA "
                f"(Grau: {estado.grau_incerteza})", estado.consistencia)

        if estado.analise_tedh:
            sec("ANÁLISE DE CONFORMIDADE COM O TEDH", estado.analise_tedh)

        if estado.erros:
            sec("AVISOS / ERROS DO PIPELINE",
                "\n".join(f"• {e}" for e in estado.erros))

        partes.append(
            f"\n{'━'*70}\n"
            "⚠️  AVISO LEGAL: Este documento é gerado por IA para apoio cognitivo.\n"
            "Não tem efeitos jurídicos vinculativos. Não substitui magistrado.\n"
            "Versão: Tribunal IA Portugal V8\n"
        )

        estado.ata_final = "\n".join(partes)
        return estado

    # ── Guardar ata ───────────────────────────────────────────────────
    def _guardar_ata(self, estado: EstadoCaso) -> None:
        if not self.config.guardar_atas:
            return
        try:
            from ..auditoria import get_auditoria
            auditoria = get_auditoria()
            auditoria.registar(estado.ata_final, estado.trace_id)
        except Exception as e:
            self.logger.warning(f"Auditoria falhou: {e}")
            # Fallback: guardar ficheiro directamente
            try:
                pasta = self.config.pasta_atas
                pasta.mkdir(parents=True, exist_ok=True)
                nome = f"ata_{estado.trace_id}_{int(time.time())}.txt"
                (pasta / nome).write_text(estado.ata_final, encoding="utf-8")
            except Exception as e2:
                self.logger.error(f"Guardar ata directo falhou: {e2}")

    # ── Pipeline principal ────────────────────────────────────────────
    def processar(
        self,
        case_description: str,
        instancia_codigo: Optional[str] = None,
        respostas_instrucao: Optional[Dict] = None,
        intervencao_utilizador: Optional[str] = None,
        defesa_previa: Optional[str] = None,
        progresso_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> EstadoCaso:

        t0 = time.time()
        estado = EstadoCaso(caso_original=case_description)
        estado.trace_id = self.logger.start_case(case_description)

        # Detectar instância
        inst_codigo = instancia_codigo or ""
        estado.instancia = (
            detectar_instancia(inst_codigo) if inst_codigo
            else detectar_instancia(case_description)
        )

        # Passos do pipeline V8
        passos = [
            ("Anonimizar",             lambda e: self._anonimizar(e)),
            ("Qualificar juridicamente",lambda e: self._qualificar(e)),
            ("RAG multi-query",         lambda e: self._rag_multi(e)),
            ("Instrução",               lambda e: self._instrucao(e)),
            ("Detetive",                lambda e: self._detetive(e)),
            ("Assistente/Vítima",       lambda e: self._assistente(e)),
            ("Acusação",                lambda e: self._acusacao(e)),
            ("Defesa",                  lambda e: self._defesa(e, intervencao_utilizador)),
            ("Juízes (3 perfis)",        lambda e: self._juizes(e)),
            ("Deliberação",             lambda e: self._deliberacao(e)),
            ("Síntese Judicial",        lambda e: self._sintese(e)),
            ("Consistência",            lambda e: self._consistencia(e)),
            ("Análise TEDH",            lambda e: self._tedh_standalone(e)),
            ("Compor Ata",              lambda e: self._compor_ata(e)),
        ]

        total = len(passos)
        for i, (nome_passo, fn) in enumerate(passos, 1):
            if progresso_cb:
                progresso_cb(nome_passo, i, total)
            self.logger.info(f"passo_{i}", nome=nome_passo)
            try:
                estado = fn(estado)
            except Exception as e:
                self.logger.error(f"Passo '{nome_passo}' falhou: {e}")
                estado.erros.append(f"{nome_passo}: {e}")

        estado.duracao_total_s = time.time() - t0
        estado.custo_total_usd = self.brain.get_cost_stats()["total_cost_usd"]
        self._guardar_ata(estado)
        self.logger.info("case_done",
                          duracao=estado.duracao_total_s,
                          erros=len(estado.erros),
                          grau_incerteza=estado.grau_incerteza)
        return estado

    # ── Processar com instrução interactiva ───────────────────────────
    def processar_com_instrucao(
        self,
        case_description: str,
        instancia_codigo: Optional[str] = None,
        progresso_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[EstadoCaso, Dict]:
        """Fase 1: retorna perguntas de instrução para o utilizador responder."""
        t0 = time.time()
        estado = EstadoCaso(caso_original=case_description)
        estado.trace_id = self.logger.start_case(case_description)
        inst_codigo = instancia_codigo or ""
        estado.instancia = (
            detectar_instancia(inst_codigo) if inst_codigo
            else detectar_instancia(case_description)
        )
        estado = self._anonimizar(estado)
        estado = self._qualificar(estado)
        estado = self._rag_multi(estado)
        estado = self._instrucao(estado)
        estado.duracao_total_s = time.time() - t0
        return estado, estado.perguntas_instrucao

    def continuar_apos_instrucao(
        self,
        estado: EstadoCaso,
        respostas: Dict,
        intervencao_utilizador: Optional[str] = None,
        progresso_cb: Optional[Callable[[str, int, int], None]] = None,
    ) -> EstadoCaso:
        """Fase 2: continua o pipeline após o utilizador responder às perguntas."""
        estado.respostas_instrucao = respostas or {}
        t1 = time.time()

        passos_cont = [
            ("Detetive",         lambda e: self._detetive(e)),
            ("Assistente/Vítima",lambda e: self._assistente(e)),
            ("Acusação",         lambda e: self._acusacao(e)),
            ("Defesa",           lambda e: self._defesa(e, intervencao_utilizador)),
            ("Juízes (3 perfis)", lambda e: self._juizes(e)),
            ("Deliberação",      lambda e: self._deliberacao(e)),
            ("Síntese Judicial", lambda e: self._sintese(e)),
            ("Consistência",     lambda e: self._consistencia(e)),
            ("Análise TEDH",     lambda e: self._tedh_standalone(e)),
            ("Compor Ata",       lambda e: self._compor_ata(e)),
        ]

        total = len(passos_cont)
        for i, (nome_passo, fn) in enumerate(passos_cont, 1):
            if progresso_cb:
                progresso_cb(nome_passo, i, total)
            try:
                estado = fn(estado)
            except Exception as e:
                self.logger.error(f"Passo '{nome_passo}' falhou: {e}")
                estado.erros.append(f"{nome_passo}: {e}")

        estado.duracao_total_s += time.time() - t1
        estado.custo_total_usd = self.brain.get_cost_stats()["total_cost_usd"]
        self._guardar_ata(estado)
        return estado


# ── LangGraph (quando disponível) ─────────────────────────────────────
def criar_grafo_langgraph(processor: CaseProcessor):
    """
    Cria o grafo LangGraph V8 com os novos nós.
    Fallback automático se LangGraph não estiver instalado.
    """
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        return None

    import typing
    from typing import TypedDict

    class GrafoEstado(TypedDict, total=False):
        caso_original: str
        caso_anonimizado: str
        instancia: Any
        qualificacao: Any
        ctx_rag_geral: str
        ctx_rag_tedh: str
        ctx_rag_defesa: str
        ctx_rag_acusacao: str
        perguntas_instrucao: dict
        respostas_instrucao: dict
        ctx_instrucao: str
        detetive: str
        assistente: str
        acusacao: str
        defesa: str
        decisao_rig: Any
        decisao_gar: Any
        decisao_equ: Any
        decisao_rig_final: Any
        decisao_gar_final: Any
        decisao_equ_final: Any
        sintese_judicial: str
        consistencia: str
        grau_incerteza: str
        analise_tedh: str
        ata_final: str
        erros: list
        trace_id: str

    def _wrap(fn_name: str):
        fn = getattr(processor, f"_{fn_name}")
        def node(state: GrafoEstado) -> GrafoEstado:
            e = EstadoCaso(**{k: v for k, v in state.items()
                              if hasattr(EstadoCaso, k)})
            e = fn(e)
            return {**state, **{k: getattr(e, k)
                                for k in state if hasattr(e, k)}}
        node.__name__ = fn_name
        return node

    grafo = StateGraph(GrafoEstado)
    nos = [
        "anonimizar", "qualificar", "rag_multi", "instrucao",
        "detetive", "assistente", "acusacao", "defesa",
        "juizes", "deliberacao", "sintese", "consistencia",
        "tedh_standalone", "compor_ata",
    ]
    for n in nos:
        grafo.add_node(n, _wrap(n))

    grafo.set_entry_point("anonimizar")
    for i in range(len(nos) - 1):
        grafo.add_edge(nos[i], nos[i + 1])
    grafo.add_edge(nos[-1], END)

    return grafo.compile()

# ═════════════════════════════════════════════════════════════════════
# COMPATIBILIDADE V6 → V8
# Adiciona ao final do case_processor.py existente.
# Não altera nada acima — apenas acrescenta CaseResult e 3 métodos.
# ═════════════════════════════════════════════════════════════════════
import hashlib as _hashlib


class CaseResult:
    """
    Adaptador V6 → V8.
    Envolve EstadoCaso com a interface que o app.py espera:
    result.case_id, result.detetive_report, result.process(), etc.
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

    @property
    def doc_hash(self) -> str:
        return _hashlib.sha256(
            (self._e.ata_final or "").encode("utf-8")
        ).hexdigest()[:16]

    # ── Campos que não existem na V8 (devolvem vazios) ────────────────
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

    # ── Fallback para atributos desconhecidos ─────────────────────────
    def __getattr__(self, name):
        return getattr(self._e, name, None)


# ── Métodos adicionados ao CaseProcessor ─────────────────────────────

def _cp_process(
    self,
    case_description: str,
    instancia_codigo: Optional[str] = None,
    dados_instrucao: Optional[Dict] = None,
    gerar_pdf: bool = True,
    pdf_docs_extraidos: Optional[List] = None,
    intervencao_utilizador: Optional[str] = None,
    defesa_pre_gerada: Optional[str] = None,
) -> "CaseResult":
    """Alias process() → processar() com devolução de CaseResult."""
    respostas: Dict[str, str] = {}
    if dados_instrucao:
        for rid, rd in dados_instrucao.get("respostas", {}).items():
            respostas[rid] = rd.get("resposta", "") if isinstance(rd, dict) else str(rd)

    desc = case_description
    if pdf_docs_extraidos:
        extras = "\n\n".join(str(d) for d in pdf_docs_extraidos if d)
        if extras:
            desc = f"{case_description}\n\n--- DOCUMENTOS ANEXOS ---\n{extras}"

    if defesa_pre_gerada and not intervencao_utilizador:
        intervencao_utilizador = defesa_pre_gerada

    estado = self.processar(
        case_description=desc,
        instancia_codigo=instancia_codigo,
        respostas_instrucao=respostas or None,
        intervencao_utilizador=intervencao_utilizador,
    )
    stats = self.brain.get_cost_stats()
    return CaseResult(estado, stats.get("modelo", ""), stats.get("backend", ""))


def _cp_gerar_perguntas_instrucao(
    self,
    case_description: str,
    instancia_codigo: Optional[str] = None,
) -> Dict:
    """Alias gerar_perguntas_instrucao() → processar_com_instrucao()."""
    _, perguntas = self.processar_com_instrucao(case_description, instancia_codigo)
    return perguntas


def _cp_rag_ctx(self, texto: str, instancia: Optional[str] = None) -> str:
    """Alias _rag_ctx() para o modo contraditório do app.py."""
    from .instancias import INSTANCIAS, detectar_instancia_por_keywords
    inst_obj = None
    if instancia:
        inst_obj = INSTANCIAS.get(instancia) or INSTANCIAS.get(
            detectar_instancia_por_keywords(instancia)
        )
    ctx, _ = self._pesquisar_rag(texto[:500], inst_obj)
    return ctx


# Injectar os métodos na classe (no final do módulo, após a definição da classe)
CaseProcessor.process = _cp_process
CaseProcessor.gerar_perguntas_instrucao = _cp_gerar_perguntas_instrucao
CaseProcessor._rag_ctx = _cp_rag_ctx
