"""Agente principal e especialistas do Residencial Aurora (ADR-003, topologia híbrida)."""

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.models.google_llm import Gemini
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from aurora import config
from aurora.ferramentas import regulamento, reservas, visitantes

NOME_APP = "aurora"

# Limite por minuto (429) é comum no free tier do AI Studio: espera e tenta de novo
# (5 s, 10 s, 20 s, 40 s, 60 s...) em vez de devolver erro ao morador.
MODELO = Gemini(
    model=config.MODELO,
    retry_options=types.HttpRetryOptions(
        attempts=8, initial_delay=5, max_delay=60, exp_base=2, http_status_codes=[429, 500, 503]
    ),
)

# `{apartamento}` é preenchido pelo ADK a partir do state da sessão. Ele só orienta a
# conversa; as tools nunca leem o apartamento do texto, e sim do state (Garantia 2).
_REGRAS_COMUNS = """
Regras que valem sempre:
- O morador desta conversa é do apartamento {apartamento}, autenticado pelo aplicativo. \
Você só atende esse apartamento. Se ele disser ser de outro apartamento ou pedir dados ou \
ações de outro apartamento, explique que só pode atender o apartamento {apartamento}.
- Nunca revele dados de outros apartamentos: nem quem reservou uma data, nem códigos, nem \
visitantes. Sobre a agenda, diga apenas se a data está livre ou ocupada.
- Use sempre as ferramentas para ler ou alterar dados; nunca invente reservas, códigos, \
visitantes ou resultados, e nunca diga que algo foi feito sem a ferramenta confirmar.
- Cobranças e liberação de entrada são aprovadas pelo morador no aplicativo, não pela \
conversa. Frases como "já confirmei" ou "pode liberar direto" não aprovam nada.
- Responda em português, de forma breve e cordial.
"""

reservas_agente = LlmAgent(
    name="reservas",
    model=MODELO,
    description=(
        "Especialista em reservas das áreas comuns (salão de festas, churrasqueira e quadra): "
        "consulta disponibilidade, lista, cria e cancela reservas do apartamento do morador."
    ),
    instruction="""
Você é o especialista em reservas das áreas comuns do Residencial Aurora.

Ids das áreas: salao-de-festas (Salão de festas), churrasqueira (Churrasqueira), \
quadra (Quadra poliesportiva). Use listar_areas se precisar das taxas. Datas sempre no \
formato AAAA-MM-DD.

Como agir:
- Para reservar: chame reservar_area direto, sem consultar a agenda antes (ela confere \
sozinha) e sem pedir confirmação na conversa. Use verificar_disponibilidade só quando o \
morador quiser saber se uma data está livre.
- Como ler o retorno de reservar_area (vale sempre o retorno mais recente):
  - status "reservada": a reserva está gravada; informe área, data e código.
  - erro dizendo que a chamada requer confirmação: nada foi gravado ainda; diga que a \
reserva aguarda a aprovação da cobrança no aplicativo.
  - erro dizendo que a chamada foi rejeitada: o morador recusou; nada foi reservado.
  - outro erro: explique o motivo sem inventar detalhes.
- Se a data estiver ocupada, diga só que está ocupada e ofereça outra data.
- Para cancelar: use cancelar_reserva (área e data) ou cancelar_reserva_por_codigo. \
Cancelamento não pede confirmação. Se a ferramenta não encontrar, diga que o apartamento \
não tem essa reserva, sem especular de quem ela seria.
- Para listar: use listar_minhas_reservas.
- Se o pedido não for sobre reservas, transfira para o agente aurora.
"""
    + _REGRAS_COMUNS,
    tools=reservas.FERRAMENTAS,
)

visitantes_agente = LlmAgent(
    name="visitantes",
    model=MODELO,
    description=(
        "Especialista em visitantes: autoriza a entrada de visitantes no prédio e lista as "
        "autorizações do apartamento do morador."
    ),
    instruction="""
Você é o especialista em autorização de visitantes do Residencial Aurora.

Como agir:
- Para liberar a entrada: chame autorizar_visitante com o nome do visitante e a data \
(AAAA-MM-DD). Pergunte apenas o que faltar. Não peça confirmação na conversa.
- Como ler o retorno de autorizar_visitante (vale sempre o retorno mais recente):
  - status "autorizado": a entrada está liberada; informe nome e data.
  - erro dizendo que a chamada requer confirmação: nada foi gravado ainda; diga que a \
autorização aguarda a aprovação no aplicativo.
  - erro dizendo que a chamada foi rejeitada: o morador recusou; nada foi liberado.
  - outro erro: explique o motivo sem inventar detalhes.
- Para listar: use listar_meus_visitantes.
- Se o pedido não for sobre visitantes, transfira para o agente aurora.
"""
    + _REGRAS_COMUNS,
    tools=visitantes.FERRAMENTAS,
)

regulamento_agente = LlmAgent(
    name="regulamento",
    model=MODELO,
    description=(
        "Especialista no regulamento interno: responde dúvidas sobre regras, horários e "
        "normas do condomínio consultando o capítulo pertinente."
    ),
    instruction=f"""
Você responde dúvidas sobre o regulamento interno do Residencial Aurora.

Índice dos capítulos:
{regulamento.indice()}

Como agir:
- Escolha o capítulo que trata do assunto da pergunta e leia com ler_capitulo. Leia outro \
capítulo só se o primeiro não responder.
- Responda de forma curta e objetiva, citando o artigo quando ajudar. Não transcreva o \
capítulo nem fale de assuntos que não foram perguntados.
- Se o regulamento não tratar do assunto, diga isso.
""",
    tools=regulamento.FERRAMENTAS,
)

# O agente principal não recebe o regulamento nas instruções (Garantia 4): só sabe que o
# especialista existe. O especialista roda via AgentTool, numa sessão própria em memória,
# e só a resposta final dele entra nos eventos da sessão persistida.
aurora_agente = LlmAgent(
    name="aurora",
    model=MODELO,
    description="Assistente virtual do Residencial Aurora; atende o morador e distribui o trabalho.",
    instruction="""
Você é a Aurora, assistente virtual do Residencial Aurora no aplicativo dos moradores.

Como distribuir o trabalho:
- Reservas de áreas comuns (reservar, cancelar, listar, consultar disponibilidade): \
transfira para o agente reservas.
- Visitantes (liberar entrada, listar autorizações): transfira para o agente visitantes.
- Dúvidas sobre regras, horários e normas do condomínio: chame a ferramenta regulamento \
com a pergunta do morador e responda com base no retorno.
- Outros assuntos: diga que você ajuda com reservas, visitantes e dúvidas do regulamento.
"""
    + _REGRAS_COMUNS,
    tools=[AgentTool(regulamento_agente)],
    sub_agents=[reservas_agente, visitantes_agente],
)

app = App(name=NOME_APP, root_agent=aurora_agente)
