from .case_processor import CaseProcessor, EstadoCaso
from .instancias import (
    INSTANCIAS, InstanciaJudicial,
    listar_instancias_menu, detectar_instancia_por_keywords,
)
from .compat import CaseResult   # ← adaptador de compatibilidade V6→V8

__all__ = [
    "CaseProcessor", "CaseResult", "EstadoCaso",
    "INSTANCIAS", "InstanciaJudicial",
    "listar_instancias_menu", "detectar_instancia_por_keywords",
