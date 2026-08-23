# -*- mode: python ; coding: utf-8 -*-
"""
Especificación de PyInstaller para SnapContext (v1.5.0).

Genera un ejecutable único ``dist/snapcontext.exe`` con el código y las
dependencias ligeras incluidas:

  - núcleo (google-generativeai, openai)
  - menú interactivo (questionary)      → extra `interactive`
  - interfaz web (fastapi + uvicorn)    → extra `web`

Los extras pesados (sentence-transformers/torch y tree-sitter) quedan
EXCLUIDOS por defecto: SnapContext degrada elegantemente sin ellos
(fallback a `ast`, aviso en la búsqueda semántica). Para generar una
versión "full" que los incluya::

    $env:SNAPCONTEXT_EXE_FULL = "1"   # Windows
    pyinstaller snapcontext.spec

Uso normal::

    pyinstaller snapcontext.spec
"""
import os

FULL = os.environ.get("SNAPCONTEXT_EXE_FULL", "").strip() in ("1", "true", "yes")

block_cipher = None

hiddenimports = [
    # Núcleo CLI
    "google.generativeai",
    "openai",
    # Menú interactivo (--provider)
    "questionary",
    "prompt_toolkit",
    # Interfaz web (--web)
    "web.app",
    "fastapi",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
]

if FULL:
    hiddenimports += [
        "sentence_transformers",
        "tree_sitter",
        "tree_sitter_languages",
    ]

# Excluidos siempre: no se usan desde el ejecutable.
excludes = ["tkinter", "matplotlib", "IPython", "jupyter"]

a = Analysis(
    ["snapcontext.py"],
    pathex=[],
    binaries=[],
    datas=[
        # Interfaz web: se sirve desde sys._MEIPASS/web/static (ver web/app.py).
        ("web/static/index.html", "web/static"),
    ],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="snapcontext",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,               # CLI interactiva: mantiene la consola
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
