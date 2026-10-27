# CLAUDE.md

Orientação para o Claude Code neste repositório.

## O que é este repositório

Entrega do desafio "Regra é regra: o assistente virtual do Residencial Aurora" (MBA Full Cycle). Assistente com **Google ADK** exposto por uma **API FastAPI** em `http://localhost:8000`, que garante as regras do condomínio **no código**, não no prompt.

- **Spec governante:** `context/intent/desafio-residencial-aurora.md` (cópia formatada do enunciado). O `README.md` atual é o enunciado original e **será substituído** pelo README da entrega (seções **Arquitetura**, **Garantias**, **Como rodar**).
- **Decisões:** `context/decisions/ADR-NNN-*.md` (ADR-001 a ADR-005). Leia antes de mudar toolchain, storage, topologia, modelo ou restauração.
- `context/` está no `.gitignore` (material de trabalho local, não vai para o fork).

> Filosofia: **o modelo decide o caminho, o código decide o que é permitido.**

## Regras invioláveis

- `dados/*.json` e `dados/regulamento.md` **nunca são alterados** (o avaliador compara com o repositório base). São só o estado inicial.
- `google-adk==2.2.0` fixado exatamente. Não atualizar sem uma nova ADR.
- Nenhuma chave versionada: `.env` fica fora do Git; `.env.example` só com **nomes** de variáveis.
- O contrato da API (caminhos, campos, status) é seguido **exatamente** como na spec; a correção é feita por ele.
- A entrega final fica na `main` do fork `gustcoder/mba-ia-desafio-criacao-agente`. O trabalho é feito em `feature/aurora-agent-challenge`, com commits por fase, e entra na `main` por PR no final.
- O `core.autocrlf=true` global faz os arquivos no disco terem CRLF, enquanto o Git guarda LF. Todo código que lê `dados/` (principalmente o recorte de capítulos do `regulamento.md`) deve tolerar `\r\n`.

## Toolchain e comandos

- uv (`~/.local/bin/uv`) + Python 3.12 (`.python-version`). Dependências em `pyproject.toml`, `uv.lock` versionado.
- `uv sync`: instala.
- `uv run aurora-restore` (`src/aurora/restore.py`): apaga o banco (sessões e dados) e recarrega reservas e visitantes de `dados/` (ADR-005). Rode com a API parada.
- `uv run aurora-api` (planejado): sobe o uvicorn em `localhost:8000`. O startup chama `banco.inicializar()`, que cria o schema e só semeia um banco nunca semeado.
- `uv run adk web`: útil para inspecionar transferências, tool calls e confirmações durante o desenvolvimento.

### Variáveis de ambiente (`.env.example`)

| Variável | Uso | Vazio = |
|---|---|---|
| `GOOGLE_API_KEY` | Chave do Google AI Studio | obrigatória |
| `GOOGLE_GENAI_USE_VERTEXAI` | Mantém o AI Studio | falso |
| `AURORA_MODEL` | Modelo de todos os agentes | `gemini-2.5-flash` |
| `AURORA_DB_PATH` | Arquivo SQLite | `var/aurora.db` |

Trate variável vazia como ausente (`os.getenv(X) or padrão`).

## Arquitetura decidida

**Storage (ADR-002):** um SQLite único com as tabelas do `DatabaseSessionService` (`sqlite+aiosqlite`, exige `sqlalchemy[asyncio]` por causa do greenlet) e as tabelas de domínio.

- `src/aurora/config.py`: carrega o `.env` da raiz e define `DB_PATH`, `SESSOES_DB_URL` e `MODELO`.
- `src/aurora/banco.py`: `SCHEMA`, `conectar()` (autocommit, `busy_timeout`, FKs), `transacao()` (`BEGIN IMMEDIATE`), `restaurar()` e `inicializar()`.
  - Tabelas `apartamentos`, `areas`, `reservas` (com `status` ativa/cancelada; cancelar não apaga a linha), `visitantes` (UNIQUE `(apartamento, nome, data)`), `sequencias` e `meta`.
  - O índice `ux_reservas_ativa_area_data` (UNIQUE parcial `WHERE status='ativa'`) é a Garantia 5.
- `src/aurora/repositorio.py`: única porta de acesso aos dados. Toda função de apartamento recebe `apartamento` do chamador e filtra no SQL.
  - Erros esperados herdam de `ErroDeDominio` (`AreaInexistente`, `DataInvalida`, `DataOcupada`, `NomeInvalido`), com mensagens que não revelam o dono da reserva.
  - `criar_reserva` converte o `IntegrityError` do índice em `DataOcupada`.
  - Os códigos seguem `RSV-NNNN` a partir do maior código de `dados/reservas.json` (o primeiro novo é `RSV-4822`), com o contador incrementado na mesma transação do INSERT.

**Agentes (ADR-003, topologia híbrida):**

| Agente | Acionamento | Responsabilidade |
|---|---|---|
| `aurora` (principal) | root | Coordena e roteia. Sem tools de dados e **sem regulamento nas instruções**. |
| `reservas` | `sub_agent` (transferência) | Disponibilidade, listar, criar e cancelar reservas do apto da sessão. Taxa > 0 exige confirmação. |
| `visitantes` | `sub_agent` (transferência) | Listar e autorizar visitantes do apto da sessão. Autorizar sempre exige confirmação. |
| `regulamento` | `AgentTool` do principal | Consulta capítulos do `regulamento.md`. Roda em sessão interna em memória, e só a resposta final entra nos eventos. |

**Modelo (ADR-004):** `gemini-2.5-flash` por padrão, via `AURORA_MODEL`.

## As cinco garantias: onde morar e armadilhas

1. **Confirmação (cobrança/acesso):** `FunctionTool(require_confirmation=...)` ou `tool_context.request_confirmation(hint, payload)`, e a decisão de pedir confirmação é do código (taxa > 0, visitante). A API monta `confirmacoes_pendentes` a partir dos eventos `adk_request_confirmation` (`google/adk/flows/llm_flows/functions.py`) e responde com um `FunctionResponse` de `adk_request_confirmation` com `{"confirmed": bool}`. Um `id` que não está pendente **na sessão** (inexistente ou já respondido) recebe `409` sem executar nada. Texto do morador ("já confirmei") nunca aprova.
   - ⚠️ **Armadilha silenciosa:** a rota aceita, nada dá erro e a ação não executa. A resposta tem que chegar **ao agente que pediu** (`reservas`/`visitantes`), e quem decide isso é o Runner (topologia, `disallow_transfer_*`, `App(resumability_config=...)`, serviço de sessão). Valide **com `DatabaseSessionService` e depois de reiniciar a API**, não só em memória ou no `adk web`.
2. **Sessão = apartamento:** o apto é gravado no `state` na criação da sessão (`POST /sessoes`), e as tools leem `tool_context.state`. **Nenhuma tool recebe apartamento como parâmetro do modelo**, nem mesmo validado. A checagem de disponibilidade devolve só livre/ocupada, sem código, apto ou nome. O cancelamento só atua sobre reservas do apto da sessão, e as mensagens de erro não vazam dados alheios (nem `RSV-4821` nem "302").
3. **Reinício:** sessões em `DatabaseSessionService` e dados no mesmo SQLite. O startup da API **não** restaura nem limpa nada.
4. **Regulamento consultado:** a tool devolve só o(s) capítulo(s) pertinente(s) (o arquivo tem 14 capítulos `## Capítulo ...`), e o especialista roda via `AgentTool` com sessão isolada. Nenhum evento da sessão persistida pode conter trechos de outros capítulos.
5. **Concorrência:** a exclusividade fica na constraint do banco, no momento do INSERT, não numa leitura prévia. O `IntegrityError` vira resposta normal (`200`, "data já ocupada"), nunca `500`.

Regras de negócio extras: quadra (taxa 0) não pede confirmação; cancelar a própria reserva não pede confirmação; o código da reserva nunca repete, nem o de uma cancelada.

## Contrato da API (resumo)

| Método | Rota | Status |
|---|---|---|
| POST | `/sessoes` `{"apartamento"}` → `{"session_id"}` | 201 |
| POST | `/sessoes/{id}/mensagens` `{"texto"}` → `{"resposta","confirmacoes_pendentes":[{"id","acao","detalhes"}]}` | 200 / 404 |
| POST | `/sessoes/{id}/confirmacoes` `{"id","confirmado"}` → mesmo formato | 200 / 409 / 404 |
| GET | `/sessoes/{id}/eventos` → todos os eventos, em ordem, com conteúdo completo | 200 / 404 |
| GET | `/apartamentos/{n}/reservas` → `[{"codigo","area","data"}]` (só ativas) | 200 |
| GET | `/apartamentos/{n}/visitantes` → `[{"nome","data"}]` | 200 |

`resposta` pode ser `""` quando a execução para esperando confirmação. `confirmacoes_pendentes` lista **todas** as pendentes da sessão (lista vazia quando não há). Para `detalhes` de reserva use `area`/`data`, e para visitante `nome`/`data`.

## Convenções

- Código em `src/aurora/`, com modelos Pydantic para request e response da API. Domínio e mensagens ao morador em português.
- Fluxo do avaliador (passos 1 a 15 na spec) é o teste de aceitação. Testes automatizados estão fora de escopo, mas valide manualmente com `curl`, inclusive o passo 14 (duas aprovações simultâneas com `&`).
