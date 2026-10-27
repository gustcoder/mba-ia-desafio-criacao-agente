"""Execução do assistente para a API: sessões, mensagens e confirmações.

Fica entre as rotas HTTP e o Runner do ADK. É aqui que a Garantia 1 encontra o
protocolo do ADK: as confirmações pendentes são lidas dos eventos persistidos e a
resposta do morador vira um FunctionResponse de `adk_request_confirmation`.
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from google.adk.apps import App
from google.adk.events import Event
from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService, Session
from google.genai import types

from aurora import repositorio as repo
from aurora.ferramentas.sessao import CHAVE_APARTAMENTO

logger = logging.getLogger(__name__)


class SessaoInexistente(Exception):
    pass


class ApartamentoInexistente(Exception):
    pass


class ConfirmacaoInexistente(Exception):
    """Não há confirmação pendente com esse id nesta sessão."""


@dataclass(frozen=True)
class ConfirmacaoPendente:
    id: str
    acao: str
    detalhes: dict[str, Any]
    id_chamada_original: str


@dataclass
class Resultado:
    resposta: str
    confirmacoes_pendentes: list[ConfirmacaoPendente] = field(default_factory=list)


def confirmacoes_pendentes(eventos: list[Event]) -> list[ConfirmacaoPendente]:
    """Pedidos `adk_request_confirmation` dos eventos que ainda não têm resposta.

    Como vem dos eventos persistidos, a lista sobrevive ao reinício da API, e um id
    respondido deixa de estar pendente no mesmo instante em que a resposta é gravada.
    """
    pedidos: dict[str, ConfirmacaoPendente] = {}
    respondidos: set[str] = set()
    for evento in eventos:
        for chamada in evento.get_function_calls():
            if chamada.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME and chamada.id:
                original = (chamada.args or {}).get("originalFunctionCall", {})
                pedidos[chamada.id] = ConfirmacaoPendente(
                    id=chamada.id,
                    acao=original.get("name", ""),
                    detalhes=dict(original.get("args", {})),
                    id_chamada_original=original.get("id", ""),
                )
        for resposta in evento.get_function_responses():
            if resposta.id:
                respondidos.add(resposta.id)
    return [p for id_, p in pedidos.items() if id_ not in respondidos]


def _texto(evento: Event) -> str:
    if evento.author == "user" or not evento.content or not evento.content.parts:
        return ""
    return "".join(p.text for p in evento.content.parts if p.text and not p.thought)


class Assistente:
    def __init__(self, app: App, session_service: BaseSessionService):
        self._app_name = app.name
        self._sessoes = session_service
        self._runner = Runner(app=app, session_service=session_service)
        # Serializa as execuções de uma mesma sessão (o ADK não aceita duas
        # gravações simultâneas na mesma sessão). Sessões diferentes rodam em paralelo.
        self._travas: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def criar_sessao(self, apartamento: str) -> str:
        """Garantia 2: o apartamento é fixado aqui, uma única vez, e nada o altera depois."""
        if not await repo.apartamento_existe(apartamento):
            raise ApartamentoInexistente(apartamento)
        sessao = await self._sessoes.create_session(
            app_name=self._app_name,
            user_id=apartamento,
            state={CHAVE_APARTAMENTO: apartamento},
        )
        await repo.registrar_sessao(sessao.id, apartamento)
        return sessao.id

    async def _obter_sessao(self, session_id: str) -> Session:
        apartamento = await repo.apartamento_da_sessao(session_id)
        sessao = apartamento and await self._sessoes.get_session(
            app_name=self._app_name, user_id=apartamento, session_id=session_id
        )
        if not sessao:
            raise SessaoInexistente(session_id)
        return sessao

    async def eventos(self, session_id: str) -> list[Event]:
        return (await self._obter_sessao(session_id)).events

    async def enviar_mensagem(self, session_id: str, texto: str) -> Resultado:
        async with self._travas[session_id]:
            sessao = await self._obter_sessao(session_id)
            mensagem = types.Content(role="user", parts=[types.Part(text=texto)])
            return await self._executar(sessao, mensagem)

    async def responder_confirmacao(
        self, session_id: str, confirmacao_id: str, confirmado: bool
    ) -> Resultado:
        """Garantia 1: só aceita um id pendente nesta sessão; qualquer outro é recusado.

        O próprio ADK reexecutaria a tool se a mesma resposta fosse reenviada, então a
        recusa de ids já respondidos é o que garante que a ação executa uma única vez.
        """
        async with self._travas[session_id]:
            sessao = await self._obter_sessao(session_id)
            pendentes = {p.id: p for p in confirmacoes_pendentes(sessao.events)}
            pendente = pendentes.get(confirmacao_id)
            if pendente is None:
                raise ConfirmacaoInexistente(confirmacao_id)
            resposta = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=confirmacao_id,
                            name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                            response={"confirmed": confirmado},
                        )
                    )
                ],
            )
            resultado = await self._executar(sessao, resposta)
            await self._conferir_retomada(session_id, pendente)
            return resultado

    async def _executar(self, sessao: Session, mensagem: types.Content) -> Resultado:
        textos = []
        async for evento in self._runner.run_async(
            user_id=sessao.user_id, session_id=sessao.id, new_message=mensagem
        ):
            if texto := _texto(evento):
                textos.append(texto)
        eventos = (await self._obter_sessao(sessao.id)).events
        return Resultado(
            resposta="\n".join(textos).strip(),
            confirmacoes_pendentes=confirmacoes_pendentes(eventos),
        )

    async def _conferir_retomada(self, session_id: str, pendente: ConfirmacaoPendente) -> None:
        """Detecta a falha silenciosa: a resposta foi aceita mas a tool não foi retomada."""
        eventos = await self.eventos(session_id)
        # A chamada original já tem uma resposta anterior (o "aguardando confirmação");
        # só conta a que vier depois da resposta do morador.
        indice_resposta = next(
            i
            for i, e in enumerate(eventos)
            if e.author == "user" and any(r.id == pendente.id for r in e.get_function_responses())
        )
        retomou = any(
            r.id == pendente.id_chamada_original
            for e in eventos[indice_resposta + 1 :]
            for r in e.get_function_responses()
        )
        if not retomou:
            logger.error(
                "Confirmação %s respondida, mas a tool %s não foi retomada.",
                pendente.id,
                pendente.acao,
            )
