"""Conexão, schema e carga inicial do banco SQLite do condomínio.

O mesmo arquivo guarda as tabelas do DatabaseSessionService do ADK
(sessions, events, app_states, user_states, adk_internal_metadata),
criadas pelo próprio ADK, e as tabelas de domínio abaixo.
"""

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from aurora import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS apartamentos (
    numero  TEXT PRIMARY KEY,
    morador TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS areas (
    id   TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    taxa REAL NOT NULL CHECK (taxa >= 0)
);

-- Cancelar não apaga a linha: o código continua ocupado e nunca é reutilizado.
CREATE TABLE IF NOT EXISTS reservas (
    codigo       TEXT PRIMARY KEY,
    apartamento  TEXT NOT NULL REFERENCES apartamentos(numero),
    area         TEXT NOT NULL REFERENCES areas(id),
    data         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'ativa' CHECK (status IN ('ativa', 'cancelada')),
    criada_em    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cancelada_em TEXT
);

-- Garantia 5: no máximo uma reserva ATIVA por área e data, valendo no instante do INSERT.
CREATE UNIQUE INDEX IF NOT EXISTS ux_reservas_ativa_area_data
    ON reservas (area, data) WHERE status = 'ativa';

CREATE INDEX IF NOT EXISTS ix_reservas_apartamento ON reservas (apartamento);

CREATE TABLE IF NOT EXISTS visitantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL REFERENCES apartamentos(numero),
    nome        TEXT NOT NULL,
    data        TEXT NOT NULL,
    criado_em   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (apartamento, nome, data)
);

-- Apartamento de cada sessão do ADK, fixado na criação (POST /sessoes).
CREATE TABLE IF NOT EXISTS sessoes (
    session_id  TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL REFERENCES apartamentos(numero),
    criada_em   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Contador dos códigos de reserva, incrementado na mesma transação do INSERT.
CREATE TABLE IF NOT EXISTS sequencias (
    nome  TEXT PRIMARY KEY,
    valor INTEGER NOT NULL
);
"""

BUSY_TIMEOUT_MS = 5000


@asynccontextmanager
async def conectar(caminho: Path | None = None) -> AsyncIterator[aiosqlite.Connection]:
    """Abre uma conexão em modo autocommit; transações são abertas explicitamente."""
    conn = await aiosqlite.connect(caminho or config.DB_PATH, isolation_level=None)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        await conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    finally:
        await conn.close()


@asynccontextmanager
async def transacao(caminho: Path | None = None) -> AsyncIterator[aiosqlite.Connection]:
    """Transação de escrita com BEGIN IMMEDIATE (reserva o lock de escrita na abertura)."""
    async with conectar(caminho) as conn:
        await conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            await conn.execute("ROLLBACK")
            raise
        else:
            await conn.execute("COMMIT")


def _ler_json(nome: str) -> list[dict]:
    return json.loads((config.DADOS_DIR / nome).read_text(encoding="utf-8"))


def _maior_numero_de_codigo(codigos: list[str]) -> int:
    numeros = [int(m.group()) for c in codigos if (m := re.search(r"\d+", c))]
    return max(numeros, default=0)


async def _semear(conn: aiosqlite.Connection) -> dict[str, int]:
    apartamentos = _ler_json("apartamentos.json")
    areas = _ler_json("areas.json")
    reservas = _ler_json("reservas.json")
    visitantes = _ler_json("visitantes.json")

    await conn.executemany(
        "INSERT INTO apartamentos (numero, morador) VALUES (:numero, :morador)", apartamentos
    )
    await conn.executemany("INSERT INTO areas (id, nome, taxa) VALUES (:id, :nome, :taxa)", areas)
    await conn.executemany(
        "INSERT INTO reservas (codigo, apartamento, area, data)"
        " VALUES (:codigo, :apartamento, :area, :data)",
        reservas,
    )
    await conn.executemany(
        "INSERT INTO visitantes (apartamento, nome, data) VALUES (:apartamento, :nome, :data)",
        visitantes,
    )
    # Novos códigos continuam a numeração a partir do maior código existente.
    await conn.execute(
        "INSERT INTO sequencias (nome, valor) VALUES ('reserva', ?)",
        (_maior_numero_de_codigo([r["codigo"] for r in reservas]),),
    )
    await conn.execute(
        "INSERT INTO meta (chave, valor) VALUES ('semeado_em', CURRENT_TIMESTAMP)"
    )
    return {
        "apartamentos": len(apartamentos),
        "areas": len(areas),
        "reservas": len(reservas),
        "visitantes": len(visitantes),
    }


async def _criar_schema(conn: aiosqlite.Connection) -> None:
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.executescript(SCHEMA)


async def restaurar(caminho: Path | None = None) -> dict[str, int]:
    """Apaga o banco inteiro (sessões e dados) e recarrega o estado de dados/."""
    caminho = caminho or config.DB_PATH
    for arquivo in (caminho, *(caminho.with_name(caminho.name + s) for s in ("-wal", "-shm"))):
        arquivo.unlink(missing_ok=True)
    caminho.parent.mkdir(parents=True, exist_ok=True)

    async with conectar(caminho) as conn:
        await _criar_schema(conn)
    async with transacao(caminho) as conn:
        return await _semear(conn)


async def inicializar(caminho: Path | None = None) -> bool:
    """Garante o schema e semeia só se o banco nunca foi semeado. Nunca apaga nada.

    Retorna True quando semeou agora.
    """
    caminho = caminho or config.DB_PATH
    caminho.parent.mkdir(parents=True, exist_ok=True)
    async with conectar(caminho) as conn:
        await _criar_schema(conn)
    async with transacao(caminho) as conn:
        cursor = await conn.execute("SELECT 1 FROM meta WHERE chave = 'semeado_em'")
        if await cursor.fetchone():
            return False
        await _semear(conn)
        return True
