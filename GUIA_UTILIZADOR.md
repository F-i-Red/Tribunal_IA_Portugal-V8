# 📖 Guia do Utilizador — Tribunal IA Portugal V8

## Parte 1️⃣: Onde estão os meus processos?

### Localização dos Ficheiros

Todos os processos são guardados em:
```
src/historico/data/
├── indice.json          ← Índice searchable de todos os casos
└── {case_id}.json       ← Cada caso tem seu ficheiro JSON
```

**Exemplo:**
```
src/historico/data/
├── indice.json
├── a1b2c3d4-e5f6-47g8-h9i0-j1k2l3m4n5o6.json
├── b2c3d4e5-f6g7-48h9-i0j1-k2l3m4n5o6p7.json
└── ...
```

---

### 📋 O que está no `indice.json`?

É um ficheiro JSON com lista de todos os processos. Exemplo:

```json
[
  {
    "id": "a1b2c3d4-e5f6-47g8-h9i0-j1k2l3m4n5o6",
    "timestamp": "2026-05-28T14:30:00+00:00",
    "instancia_codigo": "TRP",
    "instancia_nome": "Tribunal da Relação do Porto",
    "resumo": "Caso de roubo qualificado contra cidadão português...",
    "dispositivo": "CONDENADO a 8 anos de prisão...",
    "grau_incerteza": "BAIXO",
    "custo_usd": 0.47,
    "modelo": "openrouter/free",
    "n_entidades_anonimizadas": 12,
    "ata_path": "output_atas/a1b2c3d4-ata.pdf"
  },
  {
    "id": "b2c3d4e5-f6g7-48h9-i0j1-k2l3m4n5o6p7",
    "timestamp": "2026-05-28T11:15:30+00:00",
    "instancia_codigo": "TLC",
    "instancia_nome": "Tribunal de Trabalho de Lisboa",
    ...
  }
]
```

---

## 🔍 Parte 2️⃣: Como procurar meu processo?

### Via **Streamlit (Web - Mais Fácil)**

1. **Inicia a interface:**
   ```bash
   streamlit run app.py
   ```

2. **Vai ao passo "Histórico"**
   - Clica em `⏱️ Histórico de casos`

3. **Pesquisa pelo número do processo:**
   ```
   ┌─────────────────────────────────┐
   │ 🔍 Pesquisa por número/resumo:  │
   │ [____________________]          │
   │ 🔎 Filtrar por instância        │
   │ [Tribunal da Relação do Porto▼] │
   └─────────────────────────────────┘
   ```

4. **Aparece a lista:**
   ```
   ✅ 5 processos encontrados

   📋 Processo #1
   └─ ID: a1b2c3d4-e5f6-47g8-h9i0-j1k2l3m4n5o6
   └─ Data: 28 Mai 2026, 14:30
   └─ Instância: Tribunal da Relação do Porto
   └─ Resumo: Caso de roubo qualificado contra...
   └─ Resultado: CONDENADO | Confiança: 89%
   └─ [📄 Ver Ata] [📥 Descarregar PDF]

   📋 Processo #2
   └─ ...
   ```

5. **Clica em "Ver Ata"** para abrir o documento completo

---

### Via **FastAPI (REST API - Para Programadores)**

#### 1. **Listar todos os casos**
```bash
curl http://localhost:8000/v1/historico
```

**Response:**
```json
{
  "total": 5,
  "casos": [
    {
      "id": "a1b2c3d4-e5f6-47g8-h9i0-j1k2l3m4n5o6",
      "timestamp": "2026-05-28T14:30:00+00:00",
      "instancia": "Tribunal da Relação do Porto",
      "resumo": "Caso de roubo qualificado..."
    }
  ]
}
```

#### 2. **Pesquisar por palavra-chave**
```bash
curl "http://localhost:8000/v1/historico?query=roubo"
```

#### 3. **Pesquisar por instância**
```bash
curl "http://localhost:8000/v1/historico?instancia=TRP"
```

#### 4. **Obter um processo específico**
```bash
curl http://localhost:8000/v1/resultado/a1b2c3d4-e5f6-47g8-h9i0-j1k2l3m4n5o6
```

**Response:**
```json
{
  "id": "a1b2c3d4-e5f6-47g8-h9i0-j1k2l3m4n5o6",
  "caso": "Descrição do caso...",
  "decisoes": {
    "rigorosa": { "resultado": "CONDENADO", "pena": "8 anos" },
    "garantista": { "resultado": "ABSOLVIÇÃO PARCIAL", "pena": "3 anos suspenso" },
    "equilibrada": { "resultado": "CONDENADO", "pena": "5 anos" }
  },
  "ata_path": "output_atas/a1b2c3d4-ata.pdf"
}
```

---

### 📂 Directamente em Ficheiro (Para Curiosos)

Se quiseres abrir manualmente:

```powershell
# Windows
cd src\historico\data
type indice.json | jq .  # ou usar um editor de texto

# Linux/Mac
cat src/historico/data/indice.json | jq .
```

---

## ⚙️ Parte 3️⃣: Passos para Correr o Tribunal

### **Pré-requisitos (Uma Vez)**

```bash
# 1. Clonar/descarregar o projeto
cd tribunal_v8

# 2. Criar ambiente virtual
python -m venv venv

# Ativar
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# 3. Instalar dependências
pip install -r requirements.txt

# 4. Gerar chaves seguras (MUITO IMPORTANTE)
python gerar_chaves.py
# Output:
# ✅ API_SECRET_KEY: sk-abc123xyz789...
# ✅ AUDIT_ENCRYPTION_KEY: fernet-abcd1234...
# Guarda estes valores no .env

# 5. Criar arquivo .env
# Windows:
copy .env.example .env

# Linux/Mac:
cp .env.example .env

# Edita .env e adiciona:
# - OPENROUTER_API_KEY=sk_live_...  (se usares OpenRouter)
# - API_SECRET_KEY=sk-abc123xyz789...
# - AUDIT_ENCRYPTION_KEY=fernet-abcd1234...
```

---

### **Opção A: Via Interface Web (Streamlit) — RECOMENDADO**

```bash
# Terminal
streamlit run app.py

# Abre automaticamente em: http://localhost:8501
# Se não abrir, vai manualmente a esse URL
```

**Passos na interface:**
1. ✏️ **Passo 1: Caso Judicial** — Copia o texto do teu processo
2. 📚 **Passo 2: Documentos** — Upload de ficheiros legais (opcional)
3. 🔍 **Passo 3: Instrução** — Análise automática de provas
4. 💬 **Passo 4: Contraditório** — Avalia argumentos do teu lado
5. ⚖️ **Passo 5: Processo** — Os 3 juízes analisam
6. 📋 **Passo 6: Resultado** — Vê as 3 decisões + ata final
7. ⏱️ **Histórico** — Consulta processos anteriores

---

### **Opção B: Via API REST (Para Integrações) — PARA DESENVOLVEDORES**

**Terminal 1: Inicia o servidor API**
```bash
python api_server.py --host 0.0.0.0 --port 8000
# Output:
# ✅ Uvicorn running on http://0.0.0.0:8000
# 📚 Documentação: http://localhost:8000/docs
```

**Terminal 2: Faz um pedido**

```bash
# 1. Autentica-te (obter token)
curl -X POST http://localhost:8000/v1/auth \
  -H "Content-Type: application/json" \
  -d '{"user": "admin", "password": "admin"}'

# Response: {"access_token": "eyJhbGc...", "token_type": "bearer"}

# 2. Processa um novo caso
curl -X POST http://localhost:8000/v1/processar \
  -H "Authorization: Bearer eyJhbGc..." \
  -H "Content-Type: application/json" \
  -d '{
    "caso": "Roubo qualificado contra cidadão português em Lisboa...",
    "instancia": "TLC"
  }'

# Response: {"case_id": "a1b2c3d4-...", "status": "processing"}

# 3. Verifica o resultado
curl http://localhost:8000/v1/resultado/a1b2c3d4-... \
  -H "Authorization: Bearer eyJhbGc..."
```

---

### **Opção C: Modo Local (Soberano) — Para Dados Sensíveis**

Se querem que NENHUM dado saia do computador:

```bash
# 1. Descarregar Ollama (modelo local)
# Vai a: https://ollama.ai e descarrega

# 2. Correr o Ollama
ollama serve

# 3. Terminal novo — Descarrega um modelo português
ollama pull mistral  # ou outro

# 4. Edita .env
ENV=production
GOV_MODE=true
BACKEND=ollama
OLLAMA_URL=http://localhost:11434

# 5. Inicia Tribunal
streamlit run app.py
# Tudo funciona localmente, sem enviar dados para cloud
```

---

## 🚀 Fluxo Rápido (Resumido)

```
┌──────────────────────────────────────────────────────┐
│  1. pip install -r requirements.txt                  │
│  2. python gerar_chaves.py                           │
│  3. Edita .env com as chaves geradas                 │
│  4. streamlit run app.py                             │
│  5. Abre http://localhost:8501 no browser            │
│  6. Escreve o teu caso judicial                      │
│  7. Clica "Processar"                                │
│  8. Espera ~3-5 minutos (primeira vez mais lento)    │
│  9. Vê as 3 decisões + ata final em PDF              │
│  10. Futuros processos aparecem em "Histórico"       │
└──────────────────────────────────────────────────────┘
```

---

## 📊 Tempos Esperados

| Etapa | Tempo | Notas |
|-------|-------|-------|
| Setup inicial | 5 min | Pip install + gerar chaves |
| Primeira execução | 2-5 min | Carrega embeddings (slow) |
| Execuções seguintes | 1-2 min | Usa cache |
| Deliberação (se ativa) | +30 seg | Juízes revisam posições |

---

## 🔐 Questões de Segurança

### Se usar na Cloud (Production)
```bash
# Obrigatório:
ENV=production
API_SECRET_KEY=sk-abc123... (min 32 chars)
AUDIT_ENCRYPTION_KEY=fernet-...

# E se quiser GDPR/offline:
GOV_MODE=true
BACKEND=ollama  # não OpenRouter
```

### Se usar Localmente (Educação)
```bash
# Simples:
ENV=development
BACKEND=openrouter  # ou ollama
# Sem GDPR enforcement
```

---

## 🆘 Troubleshooting

### ❌ "ModuleNotFoundError: No module named 'streamlit'"
```bash
pip install -r requirements.txt
```

### ❌ "OPENROUTER_API_KEY not found"
1. Vai a https://openrouter.ai
2. Cria conta
3. Copia a chave
4. Adiciona ao `.env`: `OPENROUTER_API_KEY=sk_live_...`

### ❌ "Embedding model download failed"
1. Primeira vez é lento (560MB)
2. Espera 2-5 minutos na primeira execução
3. Depois fica em cache

### ❌ "Ollama connection refused"
1. Verifica se `ollama serve` está ativo
2. Verifica `OLLAMA_URL` no `.env` (default: `http://localhost:11434`)

---

## 📞 Suporte

- **Documentação completa**: Ver `FLUXO_TRIBUNAL_V8.md`
- **Configuração avançada**: Ver `src/utils/config.py`
- **API Docs interativa**: `http://localhost:8000/docs` (depois de iniciar API)
- **Logs detalhados**: `logs/` (procura por errors)

---

**Versão**: V8 (28 Mai 2026)
**Última atualização**: 2026-05-28
