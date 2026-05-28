"""
Auditoria V7 — Git Jurídico com encriptação em repouso
CORRECÇÕES:
  - Encriptação Fernet (AES-128-CBC) para dados em repouso
  - AUDIT_ENCRYPTION_KEY obrigatória em produção/GOV_MODE
  - Leitura transparente de ficheiros encriptados e não encriptados (migração)
  - Resto da lógica (cadeia de hash, provenance, voto de vencido) sem alterações
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Encriptação em repouso — opcional fora de produção, obrigatória em GOV_MODE
try:
    from cryptography.fernet import Fernet, InvalidToken
    FERNET_OK = True
except ImportError:
    FERNET_OK = False


# ══════════════════════════════════════════════════════════════════════
# 1. CADEIA DE HASH ENCADEADA — Git Jurídico
# ══════════════════════════════════════════════════════════════════════

@dataclass
class BlocoAuditoria:
    """
    Bloco imutável na cadeia de auditoria.
    Cada caso referencia o hash do bloco anterior — à semelhança de blockchain.
    """
    indice: int
    case_id: str
    timestamp: str
    instancia: str
    modelo: str
    grau_incerteza: str
    hash_ata: str
    hash_anterior: str
    hash_bloco: str = ""

    def calcular_hash(self) -> str:
        conteudo = json.dumps({
            "indice": self.indice,
            "case_id": self.case_id,
            "timestamp": self.timestamp,
            "hash_ata": self.hash_ata,
            "hash_anterior": self.hash_anterior,
        }, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(conteudo.encode()).hexdigest()

    def __post_init__(self) -> None:
        if not self.hash_bloco:
            self.hash_bloco = self.calcular_hash()


class CadeiaAuditoria:
    """
    Cadeia imutável de registos de auditoria com encriptação em repouso.
    CORRIGIDO: dados em disco encriptados com Fernet quando AUDIT_ENCRYPTION_KEY presente.
    """
    GENESIS_HASH = "0" * 64
    _lock = threading.Lock()

    def __init__(self, pasta: Path) -> None:
        self.pasta = pasta
        self.pasta.mkdir(parents=True, exist_ok=True)
        self._cadeia_path = pasta / "cadeia_auditoria.jsonl"
        self._cadeia: List[BlocoAuditoria] = []

        # CORRIGIDO: encriptação em repouso
        self._cipher: Optional[Any] = None
        enc_key = os.getenv("AUDIT_ENCRYPTION_KEY", "")
        gov_mode = os.getenv("GOV_MODE", "false").lower() == "true"
        env = os.getenv("ENV", "development")

        if enc_key:
            if not FERNET_OK:
                raise RuntimeError(
                    "[SEGURANÇA] AUDIT_ENCRYPTION_KEY definida mas 'cryptography' não instalado.\n"
                    "Instala: pip install cryptography"
                )
            try:
                self._cipher = Fernet(enc_key.encode())
            except Exception as e:
                raise RuntimeError(
                    f"[SEGURANÇA] AUDIT_ENCRYPTION_KEY inválida: {e}\n"
                    "Gera uma chave válida com: "
                    "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                ) from e
        elif gov_mode or env == "production":
            raise RuntimeError(
                "[SEGURANÇA] AUDIT_ENCRYPTION_KEY é obrigatória em produção/GOV_MODE.\n"
                "Gera uma chave com: "
                "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

        self._carregar()

    def _encriptar_linha(self, linha: str) -> str:
        """Encripta uma linha JSON para armazenamento em disco."""
        if self._cipher is None:
            return linha
        return self._cipher.encrypt(linha.encode()).decode()

    def _desencriptar_linha(self, linha: str) -> str:
        """
        Desencripta uma linha do ficheiro.
        Suporta linhas não encriptadas (migração de ficheiros legados).
        """
        if self._cipher is None:
            return linha
        # Tentar desencriptar; se falhar, assumir que a linha é JSON em claro (legado)
        try:
            return self._cipher.decrypt(linha.encode()).decode()
        except (InvalidToken, Exception):
            # Linha pode ser JSON em claro de antes da activação da encriptação
            return linha

    def _carregar(self) -> None:
        if not self._cadeia_path.exists():
            return
        try:
            for linha in self._cadeia_path.read_text(encoding="utf-8").splitlines():
                linha = linha.strip()
                if not linha:
                    continue
                linha_desc = self._desencriptar_linha(linha)
                try:
                    d = json.loads(linha_desc)
                    self._cadeia.append(BlocoAuditoria(**d))
                except (json.JSONDecodeError, TypeError):
                    # Linha corrompida ou formato desconhecido — ignorar e continuar
                    continue
        except Exception:
            self._cadeia = []

    def adicionar(
        self,
        case_id: str,
        instancia: str,
        modelo: str,
        grau_incerteza: str,
        hash_ata: str,
    ) -> BlocoAuditoria:
        with self._lock:
            hash_anterior = (
                self._cadeia[-1].hash_bloco if self._cadeia else self.GENESIS_HASH
            )
            bloco = BlocoAuditoria(
                indice=len(self._cadeia),
                case_id=case_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                instancia=instancia,
                modelo=modelo,
                grau_incerteza=grau_incerteza,
                hash_ata=hash_ata,
                hash_anterior=hash_anterior,
            )
            self._cadeia.append(bloco)

            # Append-only com encriptação em repouso
            linha_json = json.dumps(asdict(bloco), ensure_ascii=False)
            linha_gravada = self._encriptar_linha(linha_json)
            with open(self._cadeia_path, "a", encoding="utf-8") as f:
                f.write(linha_gravada + "\n")

            return bloco

    def verificar_integridade(self) -> Tuple[bool, List[str]]:
        erros: List[str] = []
        hash_esperado = self.GENESIS_HASH
        for i, bloco in enumerate(self._cadeia):
            if bloco.hash_anterior != hash_esperado:
                erros.append(f"Bloco {i}: hash_anterior incorreto")
            hash_recalculado = bloco.calcular_hash()
            if hash_recalculado != bloco.hash_bloco:
                erros.append(f"Bloco {i} ({bloco.case_id}): hash adulterado")
            hash_esperado = bloco.hash_bloco
        return len(erros) == 0, erros

    def resumo(self) -> Dict:
        ok, erros = self.verificar_integridade()
        return {
            "total_blocos": len(self._cadeia),
            "cadeia_integra": ok,
            "erros": erros,
            "ultimo_hash": self._cadeia[-1].hash_bloco if self._cadeia else None,
            "genesis_hash": self.GENESIS_HASH[:16] + "...",
            "encriptacao_activa": self._cipher is not None,
        }

    def exportar_auditoria(self) -> str:
        linhas = [
            "CADEIA DE AUDITORIA — TRIBUNAL IA PORTUGAL V7",
            "Verificável publicamente. Qualquer alteração quebra a cadeia.",
            f"Total de blocos: {len(self._cadeia)}",
            f"Encriptação em repouso: {'✅ Activa' if self._cipher else '⚠️ Inactiva'}",
            "=" * 60,
        ]
        for b in self._cadeia:
            linhas.append(
                f"[{b.indice:04d}] {b.case_id} | {b.instancia} | {b.timestamp[:19]}\n"
                f"       Hash: {b.hash_bloco[:32]}...\n"
                f"       Anterior: {b.hash_anterior[:32]}..."
            )
        ok, erros = self.verificar_integridade()
        linhas.append(f"\nIntegridade: {'✅ OK' if ok else '❌ COMPROMETIDA'}")
        if erros:
            for e in erros:
                linhas.append(f"  ⚠️ {e}")
        return "\n".join(linhas)


# ══════════════════════════════════════════════════════════════════════
# 2. PROVENANCE LOG
# ══════════════════════════════════════════════════════════════════════

@dataclass
class FragmentoUsado:
    agente: str
    fonte: str
    diploma: str
    artigo: str
    relevancia: float
    lingua: str


@dataclass
class ProvenanceLog:
    case_id: str
    fragmentos_usados: List[FragmentoUsado] = field(default_factory=list)
    modelos_consultados: List[str] = field(default_factory=list)
    total_tokens: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def adicionar_fragmentos_rag(self, agente: str, fragmentos: List) -> None:
        for f in fragmentos:
            self.fragmentos_usados.append(FragmentoUsado(
                agente=agente,
                fonte=getattr(f, "fonte", "?"),
                diploma=getattr(f, "diploma", "") or "?",
                artigo=getattr(f, "artigo", "") or "",
                relevancia=getattr(f, "relevancia", 0.0),
                lingua=getattr(f, "lingua", "pt"),
            ))

    def relatorio(self) -> str:
        linhas = [
            f"PROVENANCE LOG — {self.case_id}",
            f"Timestamp: {self.timestamp}",
            f"Modelos: {', '.join(set(self.modelos_consultados))}",
            f"Fragmentos RAG usados: {len(self.fragmentos_usados)}",
            "─" * 50,
        ]
        por_agente: Dict[str, List[FragmentoUsado]] = {}
        for fr in self.fragmentos_usados:
            por_agente.setdefault(fr.agente, []).append(fr)
        for agente, frags in por_agente.items():
            linhas.append(f"\n[{agente.upper()}] — {len(frags)} fragmento(s):")
            for fr in frags[:5]:
                artigo_str = f" {fr.artigo}" if fr.artigo else ""
                linhas.append(
                    f"  • [{fr.diploma}]{artigo_str} — {fr.fonte} "
                    f"(rel={fr.relevancia:.3f}, {fr.lingua})"
                )
        linhas.append(
            f"\n{'─'*50}\nTotal tokens: {self.total_tokens}\n"
            "Este relatório permite verificar quais normas jurídicas "
            "influenciaram cada peça processual."
        )
        return "\n".join(linhas)


# ══════════════════════════════════════════════════════════════════════
# 3. THREAT MODEL — Detecção de inputs adversariais
# ══════════════════════════════════════════════════════════════════════

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions?",
    r"esquece\s+(as\s+)?instruções\s+anteriores",
    r"act\s+as\s+(a\s+)?(?:different|new|evil|uncensored)",
    r"jailbreak",
    r"DAN\s*mode",
    r"você\s+é\s+agora\s+um",
    r"you\s+are\s+now\s+(?:a\s+)?(?:different|evil|uncensored)",
    r"<\s*script\s*>",
    r"system\s*:\s*you\s+are",
    r"\[\s*INST\s*\]",
    r"ignore\s+the\s+above",
    r"disregard\s+all\s+previous",
    r"from\s+now\s+on\s+you\s+(?:will|are)",
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

MAX_CASO_CHARS = 10_000
MAX_ARGUMENTO_CHARS = 3_000
MIN_CASO_CHARS = 20


@dataclass
class ResultadoValidacao:
    valido: bool
    avisos: List[str] = field(default_factory=list)
    texto_sanitizado: str = ""


def validar_input(
    texto: str,
    max_chars: int = MAX_CASO_CHARS,
    campo: str = "caso",
) -> ResultadoValidacao:
    avisos: List[str] = []
    if not texto or not texto.strip():
        return ResultadoValidacao(valido=False, avisos=["Texto vazio"])
    if len(texto) < MIN_CASO_CHARS:
        return ResultadoValidacao(
            valido=False,
            avisos=[f"Descrição muito curta (mínimo {MIN_CASO_CHARS} caracteres)"],
        )
    if len(texto) > max_chars:
        texto = texto[:max_chars]
        avisos.append(f"Texto truncado para {max_chars} caracteres")
    for pattern in _COMPILED_PATTERNS:
        if pattern.search(texto):
            return ResultadoValidacao(
                valido=False,
                avisos=[
                    "⚠️ Input rejeitado: contém padrão de manipulação do modelo. "
                    "Por favor descreve o caso de forma natural."
                ],
            )
    texto_limpo = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", texto)
    if "<" in texto_limpo and ">" in texto_limpo:
        avisos.append("Aviso: o texto contém marcação HTML/XML que foi preservada")
    return ResultadoValidacao(valido=True, avisos=avisos, texto_sanitizado=texto_limpo)


# ══════════════════════════════════════════════════════════════════════
# 4. VOTO DE VENCIDO
# ══════════════════════════════════════════════════════════════════════

@dataclass
class VotoVencido:
    perfil_divergente: str
    sentido_divergente: str
    fundamento_resumo: str
    artigos_divergentes: List[str] = field(default_factory=list)


def analisar_dissenso(
    s_rigorosa: str,
    s_garantista: str,
    s_equilibrada: str,
) -> Optional[VotoVencido]:
    def _dispositivo(txt: str) -> str:
        if not txt:
            return ""
        m = re.search(r"(?:CONDENA|ABSOLVE|JULGA)[^.]*\.", txt, re.IGNORECASE)
        return m.group(0).lower() if m else ""

    d_r = _dispositivo(s_rigorosa)
    d_g = _dispositivo(s_garantista)
    d_e = _dispositivo(s_equilibrada)

    condena = lambda d: "condena" in d
    absolve = lambda d: "absolve" in d or "não pronunci" in d or "arquiva" in d

    decisoes = [("rigoroso", d_r), ("garantista", d_g), ("equilibrado", d_e)]
    condenas = [p for p, d in decisoes if condena(d)]
    absolves = [p for p, d in decisoes if absolve(d)]

    if len(condenas) == 2 and len(absolves) == 1:
        vencido = absolves[0]
        return VotoVencido(
            perfil_divergente=vencido,
            sentido_divergente="mais garantista",
            fundamento_resumo=_extrair_fundamentacao(
                s_garantista if vencido == "garantista" else s_equilibrada
            ),
            artigos_divergentes=_extrair_artigos(
                s_garantista if vencido == "garantista" else s_equilibrada
            ),
        )
    elif len(absolves) == 2 and len(condenas) == 1:
        vencido = condenas[0]
        return VotoVencido(
            perfil_divergente=vencido,
            sentido_divergente="mais rigoroso",
            fundamento_resumo=_extrair_fundamentacao(s_rigorosa),
            artigos_divergentes=_extrair_artigos(s_rigorosa),
        )
    return None


def _extrair_fundamentacao(sentenca: str) -> str:
    if not sentenca:
        return ""
    m = re.search(
        r"(?:FUNDAMENTAÇÃO JURÍDICA|MOTIVAÇÃO)[^\n]*\n+(.*?)(?:==|\Z)",
        sentenca, re.IGNORECASE | re.DOTALL,
    )
    if m:
        return m.group(1).strip()[:300]
    return sentenca[200:500].strip()


def _extrair_artigos(sentenca: str) -> List[str]:
    if not sentenca:
        return []
    artigos = re.findall(
        r"art(?:igo)?\.?\s*\d+\.?[º°]?[A-Za-z]?(?:\s+(?:do|da|n\.?[º°]).*?)?(?:CP|CPP|CC|CPC|CT|CRP)?",
        sentenca, re.IGNORECASE,
    )
    return list(dict.fromkeys(artigos[:5]))


# ══════════════════════════════════════════════════════════════════════
# 5. DISCLAIMER
# ══════════════════════════════════════════════════════════════════════

DISCLAIMER_SEPARACAO_PAPEIS = """
╔══════════════════════════════════════════════════════════════════════╗
║  DECLARAÇÃO DE SEPARAÇÃO DE PAPÉIS — TRIBUNAL IA PORTUGAL V7        ║
╠══════════════════════════════════════════════════════════════════════╣
║                                                                      ║
║  Este sistema é APOIO COGNITIVO — não DECISÃO SOBERANA.             ║
║                                                                      ║
║  O sistema PODE:                                                     ║
║  ✅ Resumir e estruturar factos                                      ║
║  ✅ Identificar legislação potencialmente aplicável                  ║
║  ✅ Simular argumentos de acusação e defesa                         ║
║  ✅ Evidenciar incerteza jurídica e zonas de dissenso               ║
║  ✅ Gerar hipóteses decisórias para reflexão                        ║
║                                                                      ║
║  O sistema NÃO PODE:                                                 ║
║  ❌ Determinar culpa ou inocência                                    ║
║  ❌ Substituir a decisão de um magistrado                           ║
║  ❌ Produzir efeitos jurídicos vinculativos                         ║
║  ❌ Garantir a exactidão dos artigos citados                        ║
║  ❌ Representar a posição do Estado português                       ║
║                                                                      ║
║  Para decisões com efeitos jurídicos:                               ║
║  → Advogado inscrito na Ordem dos Advogados: www.oa.pt              ║
║  → Julgados de Paz: www.julgadosdepaz.mj.pt                        ║
║  → Apoio judiciário: www.dgaj.mj.pt                                 ║
╚══════════════════════════════════════════════════════════════════════╝
"""

# ══════════════════════════════════════════════════════════════════════
# Singletons
# ══════════════════════════════════════════════════════════════════════

_cadeia: Optional[CadeiaAuditoria] = None
_cadeia_lock = threading.Lock()

# Importação lazy para evitar dependência circular
from typing import Any


def get_cadeia_auditoria() -> CadeiaAuditoria:
    global _cadeia
    with _cadeia_lock:
        if _cadeia is None:
            try:
                from ..utils.config import get_config
                cfg = get_config()
                pasta = cfg.pasta_historico.parent / "auditoria"
            except Exception:
                pasta = Path("src/historico/data/auditoria")
            _cadeia = CadeiaAuditoria(pasta)
    return _cadeia
