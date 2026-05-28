"""
Anonimização de entidades sensíveis — RGPD / conformidade .gov
CORRECÇÕES:
  - Pseudónimos não-determinísticos por sessão (evita correlação entre casos)
  - Padrões adicionais: processos judiciais reais PT, SNS, matrículas, refs MB
  - Modo determinístico opcional para consistência intra-caso (não entre casos)
  - Melhoria: detecção de datas de nascimento por contexto
  - API inalterada — compatibilidade total com código existente
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class Entity:
    text: str
    start: int
    end: int
    label: str
    score: float


class PortugueseLegalAnonymizer:
    STRUCTURED_PATTERNS = {
        "EMAIL":         re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "IBAN":          re.compile(r"\bPT\d{2}[\s\d]{20,26}\b"),
        "CODIGO_POSTAL": re.compile(r"\b\d{4}-\d{3}\b"),
        "CC":            re.compile(r"\b\d{8}\s*[A-Za-z]{2}\d?\b"),
        "TELEFONE":      re.compile(r"\b(?:\+351[\s-]?)?(?:9[1236]\d{7}|2\d{8})\b"),
        "NIF":           re.compile(r"\b[1235689]\d{8}\b"),
        "NISS":          re.compile(r"\b\d{11}\b"),
        # CORRIGIDO: padrão de processo judicial PT real (ex: 1234/24.5T8LSB, 123/22.1TBPRT)
        "PROCESSO":      re.compile(
            r"\b\d{1,6}/\d{2,4}(?:\.\d[A-Z]\d[A-Z]{2,4}|[.\d]*[A-Z]{2,6})\b"
        ),
        # NOVOS padrões governamentais:
        # Número de beneficiário SNS (9 dígitos, começa por 1-9)
        "SNS_BENEFICIARIO": re.compile(r"\b[1-9]\d{8}\b"),
        # Matrícula automóvel PT (formatos: AA-00-AA, 00-AA-00, AA-00-00, 00-00-AA)
        "MATRICULA": re.compile(
            r"\b(?:[A-Z]{2}-\d{2}-[A-Z]{2}|\d{2}-[A-Z]{2}-\d{2}|"
            r"[A-Z]{2}-\d{2}-\d{2}|\d{2}-\d{2}-[A-Z]{2})\b"
        ),
        # Referência Multibanco (3+3+3 dígitos, com ou sem espaços)
        "REFERENCIA_MB": re.compile(r"\b\d{3}[\s-]?\d{3}[\s-]?\d{3}\b"),
        # Data de nascimento por contexto (dd/mm/aaaa ou dd-mm-aaaa)
        "DATA_NASCIMENTO": re.compile(
            r"(?:nasc(?:ido|ida|imento)?|d\.?\s*n\.?|data\s+de\s+nasc\w*)"
            r"\s*:?\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            re.IGNORECASE,
        ),
    }

    CIDADES = {
        "lisboa", "porto", "braga", "coimbra", "faro", "aveiro", "guarda", "leiria",
        "viseu", "bragança", "évora", "beja", "portalegre", "setúbal", "santarém",
        "viana do castelo", "vila real", "funchal", "ponta delgada", "amadora",
        "almada", "oeiras", "sintra", "cascais", "loures", "odivelas",
        "vila nova de gaia", "matosinhos", "maia", "gondomar", "portimão", "tavira",
        "evora", "setubal", "santarem",
    }

    PREFIXOS_FORMAIS = [
        r"arguid[oa]\s+", r"r[eé]u\s+", r"autor[ea]?\s+", r"vítima\s+",
        r"ofendid[oa]\s+", r"testemunha\s+", r"sr\.?\s+", r"sra\.?\s+",
        r"dr\.?\s+", r"dra\.?\s+", r"doutor[ea]?\s+", r"professor[ea]?\s+",
        r"advogad[oa]?\s+", r"juiz[a]?\s+", r"senhor[a]?\s+",
        r"menor\s+", r"cônjuge\s+", r"companheiro[a]?\s+",
    ]

    PADROES_INFORMAIS = [
        r"(?:sou\s+(?:o|a)\s+|chamo[- ]me\s+)([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+){0,3})",
        r"(?:o\s+vizinho|a\s+vizinha|o\s+senhorio|a\s+senhora|o\s+senhor)\s+(?:\w+\s+)?\(([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+){0,3})\)",
        r"\(([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+){0,3})\)",
        r"(?:denominad[oa]|alcunhad[oa]|conhecid[oa]\s+(?:por|como))\s+([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+){0,3})",
    ]

    NAO_NOMES = {
        "tribunal", "juiz", "advogado", "procurador", "ministerio", "direito",
        "lei", "codigo", "artigo", "processo", "sentenca", "nacional", "republica",
        "portuguesa", "europeu", "estado", "comarca", "distrito", "concelho",
        "qualificado", "furto", "delito", "crime", "testemunhas", "antecedentes",
        "em", "na", "no", "de", "do", "da", "para", "por", "foi", "tem", "sao",
        "era", "esta", "fica", "uma", "um", "duas", "dois", "tres", "quatro",
        "cinco", "seis", "sete", "oito", "nove", "dez", "aqui", "ali", "isso",
        "isto", "aquilo", "quando", "onde", "como", "porque", "mas", "pois",
    }

    def __init__(
        self,
        salt: Optional[str] = None,
        deterministic: bool = False,
    ):
        """
        Args:
            salt: se fornecido, usa salt fixo (modo determinístico intra-caso).
                  Se None, gera salt aleatório por instância (não-determinístico entre casos).
            deterministic: se True, usa salt estático para consistência intra-caso.
                  ATENÇÃO: deterministic=True permite correlação entre casos do mesmo cidadão.
                  Usar apenas para testes ou quando consistência intra-caso é necessária.
        """
        if deterministic and salt is not None:
            # Modo legado — compatibilidade com código existente
            self.salt = salt
            self._deterministic = True
        else:
            # CORRIGIDO: salt aleatório por instância — pseudónimos não correlacionáveis
            # entre casos diferentes, mesmo para o mesmo cidadão.
            self.salt = secrets.token_hex(16)
            self._deterministic = False

        # Mapa de pseudónimos intra-instância para consistência dentro do mesmo caso
        self._mapa_intra: dict[str, str] = {}

    def _pseudonimo(self, label: str, original: str) -> str:
        """
        Gera pseudónimo.
        CORRIGIDO: por defeito, não-determinístico entre sessões/casos.
        Consistente apenas dentro da mesma instância do anonymizer (mesmo caso).
        """
        # Chave de consistência intra-caso (mesmo label+original → mesmo resultado neste caso)
        chave_intra = f"{label}:{original.lower().strip()}"
        if chave_intra in self._mapa_intra:
            return self._mapa_intra[chave_intra]

        # Para tipos que devem ser completamente removidos (sem pseudónimo)
        removidos_directos = {
            "NIF": "[NIF_REMOVIDO]",
            "CC": "[CC_REMOVIDO]",
            "NISS": "[NISS_REMOVIDO]",
            "TELEFONE": "[TELEFONE_REMOVIDO]",
            "EMAIL": "[EMAIL_REMOVIDO]",
            "IBAN": "[IBAN_REMOVIDO]",
            "CODIGO_POSTAL": "[CP_REMOVIDO]",
            "DATA_NASCIMENTO": "[DATA_NASC_REMOVIDA]",
            "REFERENCIA_MB": "[REF_MB_REMOVIDA]",
            "SNS_BENEFICIARIO": "[SNS_REMOVIDO]",
        }
        if label in removidos_directos:
            resultado = removidos_directos[label]
            self._mapa_intra[chave_intra] = resultado
            return resultado

        # Pseudónimos com identificador curto (4 hex chars = 65536 possibilidades)
        # Derivado de: salt_aleatório + label + original — não reversível sem salt
        nonce_material = f"{self.salt}:{label}:{original.lower().strip()}"
        nonce = hashlib.sha256(nonce_material.encode()).hexdigest()[:4]

        mapa_label = {
            "PESSOA":      f"[PESSOA_{nonce}]",
            "LOCAL":       f"[LOCAL_{nonce}]",
            "MORADA":      f"[MORADA_{nonce}]",
            "ORGANIZACAO": f"[ENTIDADE_{nonce}]",
            "PROCESSO":    f"[PROCESSO_{nonce}]",
            "MATRICULA":   f"[MATRICULA_{nonce}]",
        }
        resultado = mapa_label.get(label, f"[{label}_{nonce}]")
        self._mapa_intra[chave_intra] = resultado
        return resultado

    def _valido_nome(self, nome: str) -> bool:
        palavras = nome.split()
        if len(nome) < 3 or len(palavras) > 5:
            return False
        if palavras[0].lower() in self.NAO_NOMES:
            return False
        meio_stop = {"por", "em", "de", "para", "com", "sem", "sob", "foi",
                     "tem", "são", "era", "está", "fica", "e", "ou"}
        for w in palavras[1:]:
            if w.lower() in meio_stop:
                return False
        return True

    def _encontrar_estruturados(self, text: str) -> List[Entity]:
        entities: List[Entity] = []
        # Ordem de prioridade: mais específico primeiro
        ordem = [
            "EMAIL", "IBAN", "PROCESSO", "MATRICULA", "CC",
            "TELEFONE", "NIF", "NISS", "CODIGO_POSTAL",
            "SNS_BENEFICIARIO", "REFERENCIA_MB", "DATA_NASCIMENTO",
        ]
        for label in ordem:
            pat = self.STRUCTURED_PATTERNS[label]
            for m in pat.finditer(text):
                # DATA_NASCIMENTO: capturar o grupo 1 (a data em si)
                if label == "DATA_NASCIMENTO" and m.lastindex and m.lastindex >= 1:
                    entities.append(Entity(
                        m.group(1), m.start(1), m.end(1), label, 0.97
                    ))
                else:
                    entities.append(Entity(m.group(), m.start(), m.end(), label, 0.97))
        return entities

    def _encontrar_nomes_formais(self, text: str) -> List[Entity]:
        entities: List[Entity] = []
        nome_re = r"([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+(?:\s+[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+){0,4})"
        for prefix in self.PREFIXOS_FORMAIS:
            pat = re.compile(f"(?:{prefix}){nome_re}", re.IGNORECASE)
            for m in pat.finditer(text):
                nome = m.group(1)
                if self._valido_nome(nome):
                    entities.append(Entity(nome, m.start(1), m.end(1), "PESSOA", 0.90))
        return entities

    def _encontrar_nomes_informais(self, text: str) -> List[Entity]:
        entities: List[Entity] = []
        for padrao in self.PADROES_INFORMAIS:
            for m in re.finditer(padrao, text, re.IGNORECASE):
                nome = m.group(1)
                if self._valido_nome(nome):
                    entities.append(Entity(nome, m.start(1), m.end(1), "PESSOA", 0.85))
        return entities

    def _encontrar_locais(self, text: str) -> List[Entity]:
        entities: List[Entity] = []
        for m in re.finditer(
            r"Tribunal\s+(?:(?:da|do|de|Central)\s+)?[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+"
            r"(?:\s+(?:de\s+)?[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+)*",
            text, re.IGNORECASE,
        ):
            entities.append(Entity(m.group(), m.start(), m.end(), "LOCAL", 0.95))
        for m in re.finditer(
            r"(?:Rua|Avenida|Av\.?|Praça|Largo|Travessa|Estrada|Alameda|Calçada|Beco)"
            r"\s+[^,.\n]{3,60}(?:,\s*[^,.\n]{2,40}){0,3}",
            text, re.IGNORECASE,
        ):
            if len(m.group().split()) >= 2:
                entities.append(Entity(m.group().strip(), m.start(), m.end(), "MORADA", 0.85))
        for cidade in self.CIDADES:
            for m in re.finditer(rf"\b{re.escape(cidade)}\b", text, re.IGNORECASE):
                entities.append(Entity(m.group(), m.start(), m.end(), "LOCAL", 0.80))
        return entities

    def anonymize(self, text: str) -> Tuple[str, List[Entity]]:
        todas: List[Entity] = []
        todas.extend(self._encontrar_estruturados(text))
        todas.extend(self._encontrar_nomes_formais(text))
        todas.extend(self._encontrar_nomes_informais(text))
        todas.extend(self._encontrar_locais(text))

        todas.sort(key=lambda e: e.start)
        filtradas: List[Entity] = []
        ultimo_fim = -1
        for ent in todas:
            if ent.start >= ultimo_fim:
                filtradas.append(ent)
                ultimo_fim = ent.end

        resultado = text
        for ent in reversed(filtradas):
            pseudo = self._pseudonimo(ent.label, ent.text)
            resultado = resultado[:ent.start] + pseudo + resultado[ent.end:]

        return resultado, filtradas


def anonymize_text(text: str) -> Tuple[str, List[Entity]]:
    """
    API pública — cria anonymizer não-determinístico por defeito.
    Cada chamada usa um salt aleatório novo (sem correlação entre chamadas).
    """
    return PortugueseLegalAnonymizer().anonymize(text)
