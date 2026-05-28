"""
Agentes V8 — novos: QualificadorJuridicoAgent, AssistenteAgent,
DeliberacaoAgent, SinteseJudicialAgent.
JuizAgent: temperaturas por perfil + saída estruturada JSON.
ConsistenciaAgent: lê decisões estruturadas.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..utils.brain import TribunalBrain
from ..utils.logger import TribunalLogger
from ..prompts import Prompts
from ..pipeline.instancias import InstanciaJudicial


# ── Estrutura de decisão ──────────────────────────────────────────────
@dataclass
class DecisaoEstruturada:
    """Saída estruturada de um JuizAgent após deliberação."""
    perfil: str
    relatorio: str = ""
    factos_provados: List[str] = field(default_factory=list)
    factos_nao_provados: List[str] = field(default_factory=list)
    motivacao: str = ""
    fundamentacao_juridica: str = ""
    normas_citadas: List[str] = field(default_factory=list)
    decisao: str = "INDETERMINADO"
    sancao_proposta: str = "N/A"
    custas: str = ""
    confianca: float = 0.5
    grau_incerteza_factual: str = "Médio"
    pontos_incertos: List[str] = field(default_factory=list)
    conformidade_tedh: str = "N/A"
    nota_cidadao: str = ""
    manteve_posicao: bool = True
    razao_alteracao: Optional[str] = None
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "perfil": self.perfil,
            "relatorio": self.relatorio,
            "factos_provados": self.factos_provados,
            "factos_nao_provados": self.factos_nao_provados,
            "motivacao": self.motivacao,
            "fundamentacao_juridica": self.fundamentacao_juridica,
            "normas_citadas": self.normas_citadas,
            "decisao": self.decisao,
            "sancao_proposta": self.sancao_proposta,
            "custas": self.custas,
            "confianca": self.confianca,
            "grau_incerteza_factual": self.grau_incerteza_factual,
            "pontos_incertos": self.pontos_incertos,
            "conformidade_tedh": self.conformidade_tedh,
            "nota_cidadao": self.nota_cidadao,
            "manteve_posicao": self.manteve_posicao,
            "razao_alteracao": self.razao_alteracao,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any], perfil: str = "") -> "DecisaoEstruturada":
        obj = cls(perfil=d.get("perfil", perfil))
        for k in ["relatorio", "motivacao", "fundamentacao_juridica",
                  "decisao", "sancao_proposta", "custas",
                  "grau_incerteza_factual", "conformidade_tedh", "nota_cidadao"]:
            if k in d:
                setattr(obj, k, str(d[k]))
        for k in ["factos_provados", "factos_nao_provados",
                  "normas_citadas", "pontos_incertos"]:
            if k in d and isinstance(d[k], list):
                setattr(obj, k, [str(x) for x in d[k]])
        if "confianca" in d:
            try:
                obj.confianca = float(d["confianca"])
            except (ValueError, TypeError):
                obj.confianca = 0.5
        if "manteve_posicao" in d:
            obj.manteve_posicao = bool(d["manteve_posicao"])
        if "razao_alteracao" in d:
            obj.razao_alteracao = d["razao_alteracao"]
        return obj

    def to_texto_ata(self) -> str:
        """Formata a decisão para inclusão na ata."""
        linhas = [
            f"Decisão: {self.decisao}",
            f"Sanção: {self.sancao_proposta}",
            "",
            "FACTOS PROVADOS:",
        ]
        for fp in self.factos_provados:
            linhas.append(f"  • {fp}")
        linhas += ["", "FACTOS NÃO PROVADOS:"]
        for fn in self.factos_nao_provados:
            linhas.append(f"  • {fn}")
        linhas += ["", "MOTIVAÇÃO:", self.motivacao, "",
                   "FUNDAMENTAÇÃO JURÍDICA:", self.fundamentacao_juridica, "",
                   f"Normas citadas: {', '.join(self.normas_citadas)}",
                   f"Confiança: {self.confianca:.0%}",
                   f"Incerteza factual: {self.grau_incerteza_factual}",
                   f"Conformidade TEDH: {self.conformidade_tedh}", "",
                   "NOTA PARA O CIDADÃO:", self.nota_cidadao]
        if not self.manteve_posicao and self.razao_alteracao:
            linhas += ["", f"[Posição revista após deliberação: {self.razao_alteracao}]"]
        return "\n".join(linhas)


@dataclass
class QualificacaoJuridica:
    qualificacao_provisoria: str = ""
    normas_candidatas: List[Dict] = field(default_factory=list)
    queries_rag: List[str] = field(default_factory=list)
    instancia_sugerida: str = ""
    flags: List[str] = field(default_factory=list)
    raw: str = ""


# ── Base agent ────────────────────────────────────────────────────────
class BaseAgent:
    nome: str = "base"

    def __init__(self, brain: TribunalBrain, logger: TribunalLogger) -> None:
        self.brain = brain
        self.logger = logger

    def _call(self, user_content: str, system_prompt: str,
               temperature: float = 0.15, max_tokens: int = 1600) -> str:
        self.logger.set_agent(self.nome)
        try:
            resp = self.brain.call(
                messages=[{"role": "user", "content": user_content}],
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = resp.content.strip()
            return content if content else f"[{self.nome.upper()}: resposta vazia]"
        except Exception as e:
            self.logger.error(f"Agente {self.nome}: {e}")
            raise

    def _parse_json_safe(self, raw: str, contexto: str = "") -> Dict:
        """Extracção robusta de JSON — 3 estratégias progressivas."""
        t = raw.strip()
        t = re.sub(r"```(?:json)?\s*", "", t)
        t = re.sub(r"```", "", t).strip()

        extractors = [
            lambda s: s,
            lambda s: s[s.find("{"):s.rfind("}") + 1],
            lambda s: (re.search(r"\{.*\}", s, re.DOTALL).group(0)
                       if re.search(r"\{.*\}", s, re.DOTALL) else s),
        ]
        for ex in extractors:
            try:
                candidate = ex(t)
                if candidate and candidate.strip().startswith("{"):
                    return json.loads(candidate)
            except (json.JSONDecodeError, ValueError, AttributeError):
                continue

        self.logger.warning(f"{contexto} JSON parse falhou. Preview: {raw[:200]}")
        return {}


# ── Qualificador Jurídico (NOVO V8) ───────────────────────────────────
class QualificadorJuridicoAgent(BaseAgent):
    nome = "qualificador_juridico"

    def executar(self, case_text: str,
                  inst: InstanciaJudicial) -> QualificacaoJuridica:
        system = Prompts.qualificador_juridico(inst)
        user = (f"Caso para qualificação jurídica:\n\n{case_text[:2000]}\n\n"
                "IMPORTANTE: Responde APENAS com JSON válido.")
        raw = self._call(user, system, temperature=0.10, max_tokens=1000)
        d = self._parse_json_safe(raw, "qualificador_juridico")

        q = QualificacaoJuridica(raw=raw)
        q.qualificacao_provisoria = d.get("qualificacao_provisoria", "")
        q.normas_candidatas = d.get("normas_candidatas", [])
        q.instancia_sugerida = d.get("instancia_sugerida", inst.codigo)
        q.flags = d.get("flags", [])
        queries = d.get("queries_rag", [])
        if isinstance(queries, list) and queries:
            q.queries_rag = [str(q_) for q_ in queries if q_][:6]
        if not q.queries_rag:
            q.queries_rag = [case_text[:400]]

        self.logger.info(
            "qualificacao_ok",
            n_normas=len(q.normas_candidatas),
            n_queries=len(q.queries_rag),
            flags=q.flags,
        )
        return q


# ── Detetive ──────────────────────────────────────────────────────────
class DetetiveAgent(BaseAgent):
    nome = "detetive"

    def executar(self, case_text: str, ctx_instrucao: str,
                  ctx_rag: str, inst: InstanciaJudicial) -> str:
        user = f"CASO:\n{case_text}{ctx_instrucao}"
        return self._call(user, Prompts.detetive(inst, ctx_rag),
                          temperature=0.1, max_tokens=1600)


# ── Assistente / Vítima (NOVO V8) ─────────────────────────────────────
class AssistenteAgent(BaseAgent):
    nome = "assistente"

    def executar(self, case_text: str, detetive: str,
                  ctx_rag: str, inst: InstanciaJudicial) -> str:
        user = (f"CASO:\n{case_text[:1200]}\n\n"
                f"RELATÓRIO DE INSTRUÇÃO:\n{detetive[:800]}")
        return self._call(user, Prompts.assistente(inst, ctx_rag),
                          temperature=0.15, max_tokens=1200)


# ── Acusação ──────────────────────────────────────────────────────────
class AcusacaoAgent(BaseAgent):
    nome = "acusacao"

    def executar(self, case_text: str, detetive: str,
                  ctx_rag: str, inst: InstanciaJudicial) -> str:
        user = (f"CASO:\n{case_text}\n\n"
                f"RELATÓRIO DE INSTRUÇÃO:\n{detetive[:1000]}")
        return self._call(user, Prompts.acusacao(inst, ctx_rag),
                          temperature=0.15, max_tokens=1400)


# ── Defesa ────────────────────────────────────────────────────────────
class DefesaAgent(BaseAgent):
    nome = "defesa"

    def executar(self, case_text: str, detetive: str, acusacao: str,
                  ctx_rag: str, inst: InstanciaJudicial,
                  ctx_tedh: str = "",
                  intervencao_utilizador: Optional[str] = None) -> str:
        user = (f"CASO:\n{case_text}\n\n"
                f"INSTRUÇÃO:\n{detetive[:800]}\n\n"
                f"ACUSAÇÃO:\n{acusacao[:800]}")
        if intervencao_utilizador:
            system = Prompts.defesa_contraditorio(inst, ctx_rag, intervencao_utilizador)
        else:
            system = Prompts.defesa(inst, ctx_rag, ctx_tedh)
        return self._call(user, system, temperature=0.15, max_tokens=1600)


# ── Juiz com saída estruturada (V8) ───────────────────────────────────
class JuizAgent(BaseAgent):
    TEMPERATURAS = {
        "rigoroso": 0.10,
        "garantista": 0.20,
        "equilibrado": 0.15,
    }

    def __init__(self, brain: TribunalBrain, logger: TribunalLogger,
                  perfil: str,
                  temp_override: Optional[float] = None) -> None:
        super().__init__(brain, logger)
        self.perfil = perfil
        self.nome = f"juiz_{perfil}"
        self._temperature = (temp_override
                             if temp_override is not None
                             else self.TEMPERATURAS.get(perfil, 0.15))

    def executar(self, case_text: str, detetive: str, acusacao: str,
                  defesa: str, inst: InstanciaJudicial, ctx_rag: str,
                  ctx_tedh: str = "",
                  assistente: str = "") -> DecisaoEstruturada:
        # Orçamento de contexto dinâmico (total ~3000 tokens de entrada)
        assistente_sec = (f"\n\nVOZ DO ASSISTENTE/VÍTIMA:\n{assistente[:600]}"
                          if assistente else "")
        user = (f"CASO:\n{case_text[:1000]}\n\n"
                f"INSTRUÇÃO:\n{detetive[:700]}\n\n"
                f"ACUSAÇÃO:\n{acusacao[:600]}\n\n"
                f"DEFESA:\n{defesa[:600]}"
                f"{assistente_sec}")

        system = Prompts.juiz_estruturado(inst, self.perfil, ctx_rag, ctx_tedh)
        raw = self._call(user, system,
                         temperature=self._temperature, max_tokens=2000)

        d = self._parse_json_safe(raw, f"juiz_{self.perfil}")
        if not d:
            # Fallback: decisão mínima com texto bruto
            dec = DecisaoEstruturada(perfil=self.perfil, raw_text=raw)
            dec.motivacao = raw[:800]
            dec.decisao = "INDETERMINADO"
            dec.confianca = 0.5
            return dec

        dec = DecisaoEstruturada.from_dict(d, self.perfil)
        dec.raw_text = raw
        return dec


# ── Deliberação (NOVO V8) ─────────────────────────────────────────────
class DeliberacaoAgent(BaseAgent):
    """Conduz rondas de deliberação entre os três juízes."""
    nome = "deliberacao"

    TEMPERATURAS_DELIB = {
        "rigoroso": 0.08,    # mais conservador na deliberação
        "garantista": 0.15,
        "equilibrado": 0.12,
    }

    def executar_ronda(self, inst: InstanciaJudicial, perfil: str,
                        minha_decisao: DecisaoEstruturada,
                        outras_decisoes: List[DecisaoEstruturada],
                        ronda: int) -> DecisaoEstruturada:
        outras_dicts = [d.to_dict() for d in outras_decisoes]
        system = Prompts.deliberacao(
            inst, perfil,
            minha_decisao.to_dict(),
            outras_dicts,
            ronda,
        )
        raw = self._call(
            f"Ronda de deliberação {ronda} — perfil {perfil.upper()}.",
            system,
            temperature=self.TEMPERATURAS_DELIB.get(perfil, 0.10),
            max_tokens=2000,
        )
        d = self._parse_json_safe(raw, f"deliberacao_{perfil}_r{ronda}")
        if not d:
            # Mantém posição se JSON falhar
            minha_decisao.manteve_posicao = True
            return minha_decisao

        nova = DecisaoEstruturada.from_dict(d, perfil)
        nova.raw_text = raw
        if not nova.manteve_posicao:
            self.logger.info(f"deliberacao_revisao",
                             perfil=perfil, ronda=ronda,
                             razao=nova.razao_alteracao)
        return nova


# ── Síntese Judicial (NOVO V8) ─────────────────────────────────────────
class SinteseJudicialAgent(BaseAgent):
    nome = "sintese_judicial"

    def executar(self, inst: InstanciaJudicial,
                  decisoes: List[DecisaoEstruturada]) -> str:
        decisoes_dicts = [d.to_dict() for d in decisoes]
        system = Prompts.sintese_judicial(inst, decisoes_dicts)
        return self._call(
            "Redige a síntese judicial com maioria e voto de vencido.",
            system, temperature=0.10, max_tokens=2200,
        )


# ── Consistência ──────────────────────────────────────────────────────
class ConsistenciaAgent(BaseAgent):
    nome = "consistencia"

    def executar_estruturado(self, inst: InstanciaJudicial,
                              d_rig: DecisaoEstruturada,
                              d_gar: DecisaoEstruturada,
                              d_equ: DecisaoEstruturada) -> tuple:
        """Versão V8: lê decisões estruturadas — parsing fiável."""
        system = Prompts.consistencia_estruturada(
            inst, d_rig.to_dict(), d_gar.to_dict(), d_equ.to_dict()
        )
        rel = self._call(
            "Produz o relatório de consistência e incerteza.",
            system, temperature=0.1, max_tokens=1400,
        )
        # Extrair grau da primeira linha que o mencione
        grau = self._extrair_grau(rel)
        # Fallback: usar média das confiancas
        if grau == "N/A":
            media = (d_rig.confianca + d_gar.confianca + d_equ.confianca) / 3
            grau = ("Baixo" if media > 0.75 else
                    "Médio" if media > 0.55 else
                    "Alto" if media > 0.35 else "Muito Alto")
        return rel, grau

    def executar(self, inst: InstanciaJudicial,
                  s_rigorosa: str, s_garantista: str, s_equilibrada: str) -> str:
        """Versão legacy — compatibilidade com texto livre."""
        system = Prompts.consistencia(inst, s_rigorosa, s_garantista, s_equilibrada)
        return self._call(
            "Produz o relatório de consistência e incerteza.",
            system, temperature=0.1, max_tokens=1400,
        )

    def _extrair_grau(self, texto: str) -> str:
        padrao = re.compile(
            r"(?:GRAU DE INCERTEZA GLOBAL|Grau Global|Grau de Incerteza)[:\s]+"
            r"(Baixo|Médio|Alto|Muito\s+Alto)",
            re.IGNORECASE,
        )
        m = padrao.search(texto)
        if m:
            return m.group(1).strip().title()
        # Segunda tentativa
        for grau in ["Muito Alto", "Alto", "Médio", "Baixo"]:
            if grau.lower() in texto.lower():
                return grau
        return "N/A"


# ── TEDH ──────────────────────────────────────────────────────────────
class TEDHAgent(BaseAgent):
    nome = "tedh"

    def executar(self, inst: InstanciaJudicial, caso_pt: str,
                  ctx_tedh: str, lingua: str = "pt") -> str:
        system = Prompts.analise_tedh(inst, caso_pt, ctx_tedh, lingua)
        user_msg = ("Analyse this case in light of ECtHR jurisprudence."
                    if lingua == "en"
                    else "Analisa este caso à luz da jurisprudência do TEDH.")
        return self._call(user_msg, system, temperature=0.1, max_tokens=1200)


# ── Contraditório Feedback ────────────────────────────────────────────
class ContraditórioFeedbackAgent(BaseAgent):
    nome = "contraditorio_feedback"

    def executar(self, inst: InstanciaJudicial, argumento: str,
                  acusacao: str, detetive: str) -> str:
        system = Prompts.contraditorio_feedback(inst, argumento, acusacao, detetive)
        return self._call(
            f"Argumento do advogado de defesa:\n{argumento}",
            system, temperature=0.1, max_tokens=900,
        )


# ── Instrução ─────────────────────────────────────────────────────────
class InstrucaoAgent(BaseAgent):
    nome = "instrucao"

    def executar(self, case_text: str, inst: InstanciaJudicial,
                  ctx_rag: str) -> Dict:
        system = Prompts.instrucao(inst, ctx_rag)
        user_msg = (f"Caso para instrução:\n\n{case_text}\n\n"
                    "IMPORTANTE: Responde APENAS com JSON válido. "
                    "Começa com { e termina com }.")
        raw = self._call(user_msg, system, temperature=0.1, max_tokens=1200)
        return self._parse_instrucao(raw)

    def _parse_instrucao(self, raw: str) -> Dict:
        d = self._parse_json_safe(raw, "instrucao")
        if d and "perguntas" in d and len(d["perguntas"]) > 0:
            return d

        # Extracção de perguntas a partir de texto natural
        perguntas = []
        for i, linha in enumerate(raw.split("\n"), 1):
            linha = linha.strip()
            if len(linha) > 20 and linha.endswith("?"):
                perguntas.append({
                    "id": f"q{i}", "texto": linha,
                    "categoria": "FACTOS", "importancia": "relevante",
                    "aceita_documentos": False,
                    "razao": "Extraída da resposta do modelo",
                })
        if perguntas:
            return {"introducao": "O juiz solicita os seguintes esclarecimentos.",
                    "perguntas": perguntas[:7]}

        raise ValueError(f"InstrucaoAgent: JSON inválido. Preview: {raw[:200]}")


# ── PDF Extractor ──────────────────────────────────────────────────────
class PDFExtractorAgent(BaseAgent):
    nome = "pdf_extractor"

    def executar(self, conteudo_pdf: str,
                  tipo_doc: str = "documento jurídico") -> str:
        return self._call(
            f"Extrai a informação:\n\n{conteudo_pdf[:4000]}",
            Prompts.pdf_extraction(conteudo_pdf, tipo_doc),
            temperature=0.05, max_tokens=1000,
        )
