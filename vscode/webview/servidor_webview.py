#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lanzador del chat web de SnapContext para la extensión VS Code.

Arranca el servidor FastAPI existente (``web/app.py``) en un puerto concreto.
La extensión lo invoca como::

    python vscode/webview/servidor_webview.py --puerto 8765 --directorio <ws>

y muestra ``http://localhost:<puerto>`` en una webview. Es un adaptador fino:
toda la lógica vive en ``web/app.py`` / ``snapcontext.py``.
"""

import argparse
import sys
from pathlib import Path

# Permite ejecutar desde el repo sin instalar: añade la raíz al sys.path.
RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Servidor web de SnapContext para la webview de VS Code.")
    parser.add_argument("--puerto", type=int, default=8765)
    parser.add_argument("--directorio", default=".",
                        help="Directorio del proyecto a servir.")
    argumentos = parser.parse_args()

    import os
    os.chdir(argumentos.directorio)

    from web.app import arrancar_servidor  # import diferido
    print(f"[snapcontext-webview] http://localhost:{argumentos.puerto}",
          flush=True)
    arrancar_servidor(puerto=argumentos.puerto)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
