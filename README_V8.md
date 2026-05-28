# Tribunal IA Portugal — V8

## Alterações face à V7

### Novos Agentes
| Agente | Função |
|--------|--------|
| `QualificadorJuridicoAgent` | Identifica normas candidatas e gera 5 queries RAG específicas antes de qualquer pesquisa |
| `AssistenteAgent` | Voz independente da vítima/assistente — separada do MP, com pedido de indemnização civil |
| `DeliberacaoAgent` | Conduz 1-2 rondas de deliberação onde cada juiz revê a sua posição após ver as dos outros |
| `SinteseJudicialAgent` | Produz decisão de maioria (2/3) + voto de vencido formal fundamentado |

### Melhorias ao Pipeline
- **Multi-query RAG**: 5 queries paralelas (factos, normas, precedentes, TEDH, atenuantes) em vez de 1 query com texto bruto truncado
- **TEDH integrado**: contexto TEDH disponível para a Defesa e para os Juízes Garantista e Equilibrado *antes* da sentença, não apenas como apêndice
- **Saídas estruturadas JSON**: todos os juízes produzem JSON com decisão, normas citadas, confiança (0-1), grau de incerteza factual, conformidade TEDH
- **Deliberação iterativa**: juízes independentes em rascunho, depois vêem-se e podem rever posição (só por argumento jurídico, nunca por cedência social)
- **Consistência sem regex frágil**: lê directamente os campos `decisao`, `confianca`, `pontos_incertos` do JSON estruturado
- **Temperaturas diferenciadas**: rigoroso=0.10, garantista=0.20, equilibrado=0.15 (produz divergência real)
- **Orçamento de contexto dinâmico**: cada agente recebe o contexto relevante para ele, não um corte fixo de 500/800 chars

### Compatibilidade com V7
- Todos os ficheiros V7 não modificados permanecem inalterados
- As propriedades `sentenca_rigorosa`, `sentenca_garantista`, `sentenca_equilibrada` mantêm-se no `EstadoCaso`
- O modo `imperativo` e `langgraph` continuam suportados
- Todas as variáveis de ambiente V7 são compatíveis; as V8 são opcionais com defaults

### Novos parâmetros .env
```
RAG_MULTI_QUERY=true
RAG_N_QUERIES=5
DELIBERACAO_ENABLED=true
DELIBERACAO_RONDAS=1
ASSISTENTE_ENABLED=true
SINTESE_JUDICIAL_ENABLED=true
TEMP_JUIZ_RIGOROSO=0.10
TEMP_JUIZ_GARANTISTA=0.20
TEMP_JUIZ_EQUILIBRADO=0.15
```

### Ficheiros modificados
```
src/agents/__init__.py        ← novos agentes + DecisaoEstruturada
src/pipeline/case_processor.py ← pipeline completo reescrito
src/prompts/__init__.py        ← novos prompts + TEDH integrado na defesa
src/utils/config.py            ← novas opções V8
src/utils/logger.py            ← métodos adicionais
src/rag/motor.py               ← pesquisar_multi()
requirements.txt               ← inalterado (sem novas dependências)
.env.example                   ← novas variáveis documentadas
```
