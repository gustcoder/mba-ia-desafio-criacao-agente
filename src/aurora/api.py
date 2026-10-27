"""API HTTP do assistente (contrato em context/intent/desafio-residencial-aurora.md)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from google.adk.sessions import DatabaseSessionService
from google.genai import errors as genai_errors

from aurora import banco, config, esquemas
from aurora import repositorio as repo
from aurora.agentes import app as app_adk
from aurora.assistente import (
    ApartamentoInexistente,
    Assistente,
    ConfirmacaoInexistente,
    Resultado,
    SessaoInexistente,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(api: FastAPI) -> AsyncIterator[None]:
    # Garantia 3: subir a API nunca apaga nada; só cria o schema e semeia um banco novo.
    if await banco.inicializar():
        logger.info("Banco novo semeado a partir de dados/.")
    sessoes = DatabaseSessionService(db_url=config.SESSOES_DB_URL)
    api.state.assistente = Assistente(app_adk, sessoes)
    try:
        yield
    finally:
        await sessoes.close()


api = FastAPI(title="Assistente do Residencial Aurora", lifespan=lifespan)


def _assistente(request: Request) -> Assistente:
    return request.app.state.assistente


AssistenteDep = Annotated[Assistente, Depends(_assistente)]


@api.exception_handler(SessaoInexistente)
async def _sessao_inexistente(_: Request, e: SessaoInexistente) -> JSONResponse:
    return JSONResponse({"detail": "Sessão não encontrada."}, status.HTTP_404_NOT_FOUND)


@api.exception_handler(ConfirmacaoInexistente)
async def _confirmacao_inexistente(_: Request, e: ConfirmacaoInexistente) -> JSONResponse:
    return JSONResponse(
        {"detail": "Não existe confirmação pendente com esse id nesta sessão."},
        status.HTTP_409_CONFLICT,
    )


@api.exception_handler(ApartamentoInexistente)
async def _apartamento_inexistente(_: Request, e: ApartamentoInexistente) -> JSONResponse:
    return JSONResponse(
        {"detail": "Apartamento não existe."}, status.HTTP_422_UNPROCESSABLE_CONTENT
    )


@api.exception_handler(genai_errors.APIError)
async def _erro_do_modelo(_: Request, e: genai_errors.APIError) -> JSONResponse:
    if e.code == 429:
        logger.warning("Cota do modelo esgotada mesmo após as novas tentativas: %s", e.message)
        return JSONResponse(
            {"detail": "Limite de uso do modelo atingido. Tente novamente em instantes."},
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    logger.exception("Erro na chamada ao modelo")
    return JSONResponse({"detail": "Erro ao consultar o modelo."}, status.HTTP_502_BAD_GATEWAY)


def _resposta(resultado: Resultado) -> esquemas.RespostaAssistente:
    return esquemas.RespostaAssistente(
        resposta=resultado.resposta,
        confirmacoes_pendentes=[
            esquemas.ConfirmacaoPendente(id=p.id, acao=p.acao, detalhes=p.detalhes)
            for p in resultado.confirmacoes_pendentes
        ],
    )


_404 = {404: {"model": esquemas.Erro}}


@api.post("/sessoes", status_code=status.HTTP_201_CREATED, response_model=esquemas.SessaoCriada)
async def criar_sessao(corpo: esquemas.CriarSessao, assistente: AssistenteDep):
    return esquemas.SessaoCriada(session_id=await assistente.criar_sessao(corpo.apartamento))


@api.post(
    "/sessoes/{session_id}/mensagens", response_model=esquemas.RespostaAssistente, responses=_404
)
async def enviar_mensagem(session_id: str, corpo: esquemas.Mensagem, assistente: AssistenteDep):
    return _resposta(await assistente.enviar_mensagem(session_id, corpo.texto))


@api.post(
    "/sessoes/{session_id}/confirmacoes",
    response_model=esquemas.RespostaAssistente,
    responses={**_404, 409: {"model": esquemas.Erro}},
)
async def responder_confirmacao(
    session_id: str, corpo: esquemas.RespostaConfirmacao, assistente: AssistenteDep
):
    return _resposta(
        await assistente.responder_confirmacao(session_id, corpo.id, corpo.confirmado)
    )


@api.get("/sessoes/{session_id}/eventos", responses=_404)
async def listar_eventos(session_id: str, assistente: AssistenteDep) -> list[dict[str, Any]]:
    eventos = await assistente.eventos(session_id)
    return [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in eventos]


# Rotas de verificação: leem o banco direto, sem passar pelo modelo.


@api.get("/apartamentos/{numero}/reservas", response_model=list[esquemas.Reserva])
async def reservas_do_apartamento(numero: str):
    return [asdict(r) for r in await repo.listar_reservas(numero)]


@api.get("/apartamentos/{numero}/visitantes", response_model=list[esquemas.Visitante])
async def visitantes_do_apartamento(numero: str):
    return [asdict(v) for v in await repo.listar_visitantes(numero)]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    uvicorn.run(api, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
