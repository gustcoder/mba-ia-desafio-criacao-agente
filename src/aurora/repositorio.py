"""Leitura e gravação de reservas e visitantes.

Toda função que toca dados de um apartamento recebe o `apartamento` de quem
chama (as tools passam o valor do state da sessão) e filtra por ele no SQL.
Nenhuma função devolve de quem é uma reserva de outro apartamento.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date

from aurora.banco import conectar, transacao


@dataclass(frozen=True)
class Area:
    id: str
    nome: str
    taxa: float

    @property
    def gera_cobranca(self) -> bool:
        return self.taxa > 0


@dataclass(frozen=True)
class Reserva:
    codigo: str
    area: str
    data: str


@dataclass(frozen=True)
class Visitante:
    nome: str
    data: str


class ErroDeDominio(Exception):
    """Erro esperado, com mensagem segura para chegar à conversa."""


class AreaInexistente(ErroDeDominio):
    def __init__(self, area: str):
        super().__init__(f"Área '{area}' não existe.")


class DataInvalida(ErroDeDominio):
    def __init__(self, data: str):
        super().__init__(f"Data '{data}' inválida; use o formato AAAA-MM-DD.")


class DataOcupada(ErroDeDominio):
    def __init__(self, area: str, data: str):
        super().__init__(f"A área '{area}' já está reservada em {data}.")


class NomeInvalido(ErroDeDominio):
    def __init__(self):
        super().__init__("Informe o nome do visitante.")


def validar_data(data: str) -> str:
    try:
        return date.fromisoformat(data).isoformat()
    except (TypeError, ValueError):
        raise DataInvalida(data) from None


def validar_nome(nome: str) -> str:
    nome = " ".join((nome or "").split())
    if not nome:
        raise NomeInvalido()
    return nome


# --- Áreas e apartamentos -------------------------------------------------


async def listar_areas() -> list[Area]:
    async with conectar() as conn:
        cursor = await conn.execute("SELECT id, nome, taxa FROM areas ORDER BY nome")
        return [Area(**dict(r)) for r in await cursor.fetchall()]


async def obter_area(area: str) -> Area:
    async with conectar() as conn:
        cursor = await conn.execute("SELECT id, nome, taxa FROM areas WHERE id = ?", (area,))
        linha = await cursor.fetchone()
    if linha is None:
        raise AreaInexistente(area)
    return Area(**dict(linha))


async def apartamento_existe(numero: str) -> bool:
    async with conectar() as conn:
        cursor = await conn.execute("SELECT 1 FROM apartamentos WHERE numero = ?", (numero,))
        return await cursor.fetchone() is not None


# --- Sessões --------------------------------------------------------------


async def registrar_sessao(session_id: str, apartamento: str) -> None:
    async with transacao() as conn:
        await conn.execute(
            "INSERT INTO sessoes (session_id, apartamento) VALUES (?, ?)", (session_id, apartamento)
        )


async def apartamento_da_sessao(session_id: str) -> str | None:
    async with conectar() as conn:
        cursor = await conn.execute(
            "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
        )
        linha = await cursor.fetchone()
    return linha["apartamento"] if linha else None


# --- Reservas --------------------------------------------------------------


async def area_disponivel(area: str, data: str) -> bool:
    """Só diz se a data está livre; nunca de quem é a reserva (Garantia 2)."""
    await obter_area(area)
    data = validar_data(data)
    async with conectar() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
            (area, data),
        )
        return await cursor.fetchone() is None


async def listar_reservas(apartamento: str) -> list[Reserva]:
    async with conectar() as conn:
        cursor = await conn.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND status = 'ativa' ORDER BY data, area",
            (apartamento,),
        )
        return [Reserva(**dict(r)) for r in await cursor.fetchall()]


async def criar_reserva(apartamento: str, area: str, data: str) -> Reserva:
    """Grava a reserva. A exclusividade vem do índice UNIQUE parcial, no próprio INSERT.

    Uma conferência prévia não protegeria contra outra gravação simultânea; por
    isso a violação do índice é o que decide e vira DataOcupada (Garantia 5).
    """
    await obter_area(area)
    data = validar_data(data)
    try:
        async with transacao() as conn:
            cursor = await conn.execute(
                "UPDATE sequencias SET valor = valor + 1 WHERE nome = 'reserva' RETURNING valor"
            )
            (numero,) = await cursor.fetchone()
            codigo = f"RSV-{numero:04d}"
            await conn.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                (codigo, apartamento, area, data),
            )
    except sqlite3.IntegrityError as erro:
        if "reservas.area, reservas.data" in str(erro):
            raise DataOcupada(area, data) from None
        raise
    return Reserva(codigo=codigo, area=area, data=data)


async def cancelar_reserva(apartamento: str, area: str, data: str) -> Reserva | None:
    """Cancela a reserva ativa do PRÓPRIO apartamento. None se ele não tem essa reserva."""
    data = validar_data(data)
    async with transacao() as conn:
        cursor = await conn.execute(
            "UPDATE reservas SET status = 'cancelada', cancelada_em = CURRENT_TIMESTAMP"
            " WHERE apartamento = ? AND area = ? AND data = ? AND status = 'ativa'"
            " RETURNING codigo, area, data",
            (apartamento, area, data),
        )
        linha = await cursor.fetchone()
    return Reserva(**dict(linha)) if linha else None


async def cancelar_reserva_por_codigo(apartamento: str, codigo: str) -> Reserva | None:
    """Cancela pelo código, só se a reserva for do PRÓPRIO apartamento."""
    async with transacao() as conn:
        cursor = await conn.execute(
            "UPDATE reservas SET status = 'cancelada', cancelada_em = CURRENT_TIMESTAMP"
            " WHERE apartamento = ? AND codigo = ? AND status = 'ativa'"
            " RETURNING codigo, area, data",
            (apartamento, codigo.strip().upper()),
        )
        linha = await cursor.fetchone()
    return Reserva(**dict(linha)) if linha else None


# --- Visitantes --------------------------------------------------------------


async def listar_visitantes(apartamento: str) -> list[Visitante]:
    async with conectar() as conn:
        cursor = await conn.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome",
            (apartamento,),
        )
        return [Visitante(**dict(r)) for r in await cursor.fetchall()]


async def autorizar_visitante(apartamento: str, nome: str, data: str) -> Visitante:
    """Registra a autorização. Repetir a mesma (nome, data) não duplica."""
    nome = validar_nome(nome)
    data = validar_data(data)
    async with transacao() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
            (apartamento, nome, data),
        )
    return Visitante(nome=nome, data=data)
