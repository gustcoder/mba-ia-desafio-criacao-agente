"""Modelos Pydantic do contrato da API."""

from typing import Any

from pydantic import BaseModel, Field


class CriarSessao(BaseModel):
    apartamento: str = Field(min_length=1, examples=["101"])


class SessaoCriada(BaseModel):
    session_id: str


class Mensagem(BaseModel):
    texto: str = Field(min_length=1, examples=["Quero reservar o salão de festas para 2030-04-20"])


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


class ConfirmacaoPendente(BaseModel):
    id: str
    acao: str
    detalhes: dict[str, Any]


class RespostaAssistente(BaseModel):
    resposta: str
    confirmacoes_pendentes: list[ConfirmacaoPendente]


class Reserva(BaseModel):
    codigo: str
    area: str
    data: str


class Visitante(BaseModel):
    nome: str
    data: str


class Erro(BaseModel):
    detail: str
