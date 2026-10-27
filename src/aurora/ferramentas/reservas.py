"""Tools do especialista de reservas."""

from dataclasses import asdict
from typing import Any

from google.adk.tools import FunctionTool, ToolContext

from aurora import repositorio as repo
from aurora.ferramentas.sessao import apartamento_da_sessao, aprovado_pelo_morador, erro
from aurora.repositorio import DataOcupada, ErroDeDominio


async def listar_areas() -> dict[str, Any]:
    """Lista as áreas comuns que podem ser reservadas.

    Returns:
        Para cada área: id (usado nas outras tools), nome, taxa em reais e se gera cobrança.
    """
    areas = await repo.listar_areas()
    return {"areas": [{**asdict(a), "gera_cobranca": a.gera_cobranca} for a in areas]}


async def verificar_disponibilidade(area: str, data: str) -> dict[str, Any]:
    """Informa se uma área comum está livre numa data. Não informa quem reservou.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    try:
        livre = await repo.area_disponivel(area, data)
    except ErroDeDominio as e:
        return erro(e)
    return {"area": area, "data": data, "disponivel": livre}


async def listar_minhas_reservas(tool_context: ToolContext) -> dict[str, Any]:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    try:
        reservas = await repo.listar_reservas(apartamento_da_sessao(tool_context))
    except ErroDeDominio as e:
        return erro(e)
    return {"reservas": [asdict(r) for r in reservas]}


async def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Reserva uma área comum para o apartamento do morador desta conversa.

    Confere a disponibilidade sozinha. Áreas com taxa geram cobrança: o sistema pede a
    aprovação do morador pelo aplicativo antes de gravar. Áreas sem taxa são reservadas
    na hora.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    try:
        area_reservada = await repo.obter_area(area)
        repo.validar_data(data)
        # Garantia 1 dentro da própria tool: sem aprovação, área com taxa nunca é gravada,
        # mesmo que a execução chegue aqui por outro caminho.
        if area_reservada.gera_cobranca and not aprovado_pelo_morador(tool_context):
            if not await repo.area_disponivel(area, data):
                raise DataOcupada(area, data)
            return erro("Reserva com cobrança só é gravada depois da aprovação no aplicativo.")
        reserva = await repo.criar_reserva(apartamento_da_sessao(tool_context), area, data)
    except ErroDeDominio as e:
        return erro(e)
    return {"status": "reservada", **asdict(reserva), "taxa": area_reservada.taxa}


async def _reserva_gera_cobranca(area: str, data: str, **_: Any) -> bool:
    """Garantia 1: o código decide quando pedir confirmação — área com taxa > 0.

    Pedido inválido ou data já ocupada não pede confirmação: a tool roda e só devolve o
    erro, sem gravar. A exclusividade continua sendo decidida no INSERT (Garantia 5).
    """
    try:
        area_reservada = await repo.obter_area(area)
        repo.validar_data(data)
        return area_reservada.gera_cobranca and await repo.area_disponivel(area, data)
    except ErroDeDominio:
        return False


async def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Cancela uma reserva do apartamento do morador desta conversa. Não pede confirmação.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data da reserva no formato AAAA-MM-DD.
    """
    try:
        reserva = await repo.cancelar_reserva(apartamento_da_sessao(tool_context), area, data)
    except ErroDeDominio as e:
        return erro(e)
    if reserva is None:
        return {
            "status": "nao_encontrada",
            "mensagem": "O seu apartamento não tem reserva ativa dessa área nessa data.",
        }
    return {"status": "cancelada", **asdict(reserva)}


async def cancelar_reserva_por_codigo(codigo: str, tool_context: ToolContext) -> dict[str, Any]:
    """Cancela, pelo código, uma reserva do apartamento do morador desta conversa.

    Args:
        codigo: código da reserva (ex.: RSV-1234).
    """
    try:
        reserva = await repo.cancelar_reserva_por_codigo(
            apartamento_da_sessao(tool_context), codigo
        )
    except ErroDeDominio as e:
        return erro(e)
    if reserva is None:
        return {
            "status": "nao_encontrada",
            "mensagem": "O seu apartamento não tem reserva ativa com esse código.",
        }
    return {"status": "cancelada", **asdict(reserva)}


FERRAMENTAS = [
    listar_areas,
    verificar_disponibilidade,
    listar_minhas_reservas,
    FunctionTool(reservar_area, require_confirmation=_reserva_gera_cobranca),
    cancelar_reserva,
    cancelar_reserva_por_codigo,
]
