"""Dados da sessão que as tools usam: o apartamento do morador e a confirmação."""

from typing import Any

from google.adk.tools import ToolContext

from aurora.repositorio import ErroDeDominio

CHAVE_APARTAMENTO = "apartamento"


class SessaoSemApartamento(ErroDeDominio):
    """A sessão não foi criada pela API (POST /sessoes) e não tem apartamento.

    Acontece, por exemplo, numa sessão nova aberta direto no `adk web`: as tools
    recusam a operação em vez de escolher um apartamento.
    """


def apartamento_da_sessao(tool_context: ToolContext) -> str:
    """Apartamento do morador autenticado, gravado no state só na criação da sessão.

    Garantia 2: é a única origem do apartamento usado pelas tools. Nenhuma tool
    recebe apartamento como argumento, então o modelo não tem como escolher outro.
    """
    apartamento = tool_context.state.get(CHAVE_APARTAMENTO)
    # A sessão é criada com user_id = apartamento; as duas origens precisam bater.
    if not apartamento or apartamento != tool_context.user_id:
        raise SessaoSemApartamento(
            "Esta sessão não tem apartamento: crie a sessão pela API (POST /sessoes)."
        )
    return str(apartamento)


def aprovado_pelo_morador(tool_context: ToolContext) -> bool:
    """True só quando esta execução é a retomada de uma confirmação aprovada.

    A aprovação chega como FunctionResponse de `adk_request_confirmation`, que só a
    rota POST /sessoes/{id}/confirmacoes produz; texto do morador nunca vira aprovação.
    """
    confirmacao = tool_context.tool_confirmation
    return bool(confirmacao and confirmacao.confirmed)


def erro(e: ErroDeDominio | str) -> dict[str, Any]:
    return {"status": "erro", "mensagem": str(e)}
