"""Ponto de entrada do `adk web`, a interface de desenvolvimento do ADK.

O carregador do ADK procura `app` em `<pasta>/agent.py`. Aqui só reexportamos o
mesmo App usado pela API. O passo a passo está em docs/TESTE-MANUAL.md.
"""

from aurora.agentes import app

__all__ = ["app"]
