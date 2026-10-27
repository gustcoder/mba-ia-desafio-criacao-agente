"""`uv run aurora-restore`: volta o condomínio ao estado de dados/ e apaga as sessões.

Rode com a API parada.
"""

import asyncio

from aurora import banco, config


def main() -> None:
    contagem = asyncio.run(banco.restaurar())
    print(f"Banco restaurado em {config.DB_PATH} (sessões apagadas).")
    for tabela, total in contagem.items():
        print(f"  {tabela}: {total}")


if __name__ == "__main__":
    main()
