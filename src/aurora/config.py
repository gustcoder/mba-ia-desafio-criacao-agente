"""Configuração lida do ambiente (.env na raiz do projeto)."""

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ / ".env")

DADOS_DIR = RAIZ / "dados"
REGULAMENTO_PATH = DADOS_DIR / "regulamento.md"

MODELO = os.getenv("AURORA_MODEL") or "gemini-2.5-flash"


def _caminho_banco() -> Path:
    caminho = Path(os.getenv("AURORA_DB_PATH") or "var/aurora.db")
    return caminho if caminho.is_absolute() else RAIZ / caminho


DB_PATH = _caminho_banco()

# Mesmo arquivo para o DatabaseSessionService do ADK e para as tabelas de domínio.
SESSOES_DB_URL = f"sqlite+aiosqlite:///{DB_PATH}"
