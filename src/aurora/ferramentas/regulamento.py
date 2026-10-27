"""Tool do especialista do regulamento: lê um capítulo por vez de dados/regulamento.md."""

import re
from dataclasses import dataclass
from functools import cache
from typing import Any

from aurora import config


@dataclass(frozen=True)
class Capitulo:
    numero: int
    titulo: str
    texto: str


@cache
def capitulos() -> tuple[Capitulo, ...]:
    texto = config.REGULAMENTO_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    partes = re.split(r"(?m)^## ", texto)[1:]  # descarta o título do documento
    resultado = []
    for numero, parte in enumerate(partes, start=1):
        titulo, _, corpo = parte.partition("\n")
        resultado.append(Capitulo(numero, titulo.strip(), corpo.strip()))
    return tuple(resultado)


def indice() -> str:
    """Só os títulos, para o especialista escolher o capítulo; nenhum texto de artigo."""
    return "\n".join(f"{c.numero}. {c.titulo}" for c in capitulos())


def ler_capitulo(numero: int) -> dict[str, Any]:
    """Devolve o texto completo de um único capítulo do regulamento interno.

    Args:
        numero: número do capítulo no índice (1 a 14).
    """
    for capitulo in capitulos():
        if capitulo.numero == numero:
            return {"capitulo": capitulo.titulo, "texto": capitulo.texto}
    return {"status": "erro", "mensagem": f"Capítulo {numero} não existe; use 1 a {len(capitulos())}."}


FERRAMENTAS = [ler_capitulo]
