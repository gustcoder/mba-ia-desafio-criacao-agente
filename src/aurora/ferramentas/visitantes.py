"""Tools do especialista de visitantes."""

from dataclasses import asdict
from typing import Any

from google.adk.tools import FunctionTool, ToolContext

from aurora import repositorio as repo
from aurora.ferramentas.sessao import apartamento_da_sessao, aprovado_pelo_morador, erro
from aurora.repositorio import ErroDeDominio


async def listar_meus_visitantes(tool_context: ToolContext) -> dict[str, Any]:
    """Lista os visitantes autorizados do apartamento do morador desta conversa."""
    visitantes = await repo.listar_visitantes(apartamento_da_sessao(tool_context))
    return {"visitantes": [asdict(v) for v in visitantes]}


async def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Autoriza a entrada de um visitante no prédio numa data.

    Liberar acesso sempre exige a aprovação do morador pelo aplicativo antes de gravar,
    mesmo que ele diga na conversa que já confirmou.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    try:
        repo.validar_nome(nome)
        repo.validar_data(data)
        # Garantia 1 dentro da própria tool: sem aprovação, nada é gravado.
        if not aprovado_pelo_morador(tool_context):
            return erro("A entrada do visitante só é liberada depois da aprovação no aplicativo.")
        visitante = await repo.autorizar_visitante(apartamento_da_sessao(tool_context), nome, data)
    except ErroDeDominio as e:
        return erro(e)
    return {"status": "autorizado", **asdict(visitante)}


def _visitante_exige_confirmacao(nome: str, data: str, **_: Any) -> bool:
    """Garantia 1: todo pedido válido de liberar acesso pede confirmação.

    Pedido inválido não pede: a tool roda e só devolve o erro, sem gravar.
    """
    try:
        repo.validar_nome(nome)
        repo.validar_data(data)
    except ErroDeDominio:
        return False
    return True


FERRAMENTAS = [
    listar_meus_visitantes,
    FunctionTool(autorizar_visitante, require_confirmation=_visitante_exige_confirmacao),
]
