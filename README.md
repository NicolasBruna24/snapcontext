# SnapContext

![v6.34.11](https://img.shields.io/badge/version-6.34.11-blue.svg)
[![PyPI](https://badge.fury.io/py/snapcontext.svg)](https://pypi.org/project/snapcontext/)
[![CI](https://img.shields.io/github/actions/workflow/status/NicolasBruna24/snapcontext/ci.yml?branch=main&label=tests)](https://github.com/NicolasBruna24/snapcontext/actions)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Plataformas](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macOS-lightgrey.svg)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

> **SnapContext** es un asistente de IA con contexto automático para desarrollo:
> detecta el tipo de proyecto, selecciona los archivos relevantes con IA, ejecuta
> tareas con su editor propio (o Aider), planifica trabajos complejos y aprende
> del proyecto mediante una memoria persistente (`CLAUDE.md`).

- **Proveedores**: Gemini · Claude (Anthropic) · Ollama (local) · DeepSeek · Groq · OpenAI-compatible
- **Arquitectura**: orquestador + agentes (Contexto / Editor / Tester)
- **Seguridad**: permisos con confirmaciones (`~/.snapcontext/permisos.json`), sandboxing Docker y validación de rutas

## 🚀 Quick Start (30 segundos)

```bash
pip install snapcontext
snapcontext --init                      # asistente inicial: proveedor + API key
snapcontext "describe este proyecto"    # primera consulta
```

Con tu proyecto detectado automáticamente, ya puedes pedir tareas:

```bash
snapcontext "el botón de pago no actualiza el total" --test-loop
snapcontext --plan "migrar los componentes de clases a hooks"
```

## ✨ Características

| Área | Detalle |
|------|---------|
| 🧠 Motor ReAct | Razonamiento dinámico con herramientas (por defecto desde v5.2.0) |
| 🤖 Multi-agente | Arquitecto, Programador, QA Tester adversarial y Supervisor, en paralelo |
| 🐳 Sandbox Docker | Único en su categoría: todo comando peligroso se ejecuta en contenedor |
| 👁️ Visión + Navegador | Capturas de pantalla y navegación web con Playwright |
| 🌍 Omnicanalidad | Discord, Telegram, web y TUI inmersiva (Textual) |
| 🔌 MCP nativo | Herramientas DB, API y Browser, con marketplace de servidores |
| 🕸️ Graph RAG + LSP | Grafo de dependencias del código y análisis con servidores LSP |
| ⚡ XPU (Intel Arc) | Soporte experimental para aceleración local Intel |
| 🗺️ Planificador | Planes multi-paso con dependencias, paralelismo y modo autónomo |
| 🧩 Hooks y plugins | Ciclo de vida, scripts personalizados y `snapcontext plugin` |
| 📝 Git profundo | Commits por paso, diffs interactivos y `snapcontext revert` |
| 💾 Memoria persistente | SQLite + skills + curador proactivo (`CLAUDE.md`) |

## 📦 Instalación

```bash
# Linux / macOS (one-liner)
curl -fsSL https://raw.githubusercontent.com/NicolasBruna24/snapcontext/main/install.sh | sh
```

```powershell
# Windows (PowerShell)
irm https://raw.githubusercontent.com/NicolasBruna24/snapcontext/main/install.ps1 | iex
```

O manualmente:

```bash
pip install snapcontext                 # base
pip install "snapcontext[db]"           # bases de datos (PostgreSQL/MySQL)
pip install "snapcontext[embeddings]"   # búsqueda semántica local (opcional)
pip install "snapcontext[anthropic]"    # Claude
pip install "snapcontext[lsp]"          # servidores LSP de Python (--lsp)
pip install "snapcontext[web]"          # interfaz web (--web)
pip install aider-chat                  # ediciones de código (opcional)
```

```bash
snapcontext --init                      # asistente inicial + API key
```

También hay instalador `.exe` para Windows sin Python, y una extensión para
VS Code (carpeta `vscode/`).


## 🔧 Configuración

Todo vive en `~/.snapcontext/`:

- `config.json` — proveedor, modelo, claves API y preferencias.
- `permisos.json` — acciones aprobadas/rechazadas por el usuario.
- `CLAUDE.md` — memoria del proyecto (se genera con `--init-claude`).

### Variables de entorno

| Variable | Función |
|----------|---------|
| `SNAPCONTEXT_PROVIDER` | Proveedor por defecto (`gemini`, `anthropic`, `ollama`, …) |
| `SNAPCONTEXT_MODELO` | Modelo por defecto |
| `SNAPCONTEXT_MODO_DEFAULT` | Modo de operación inicial |
| `SNAPCONTEXT_MOSTRAR_RAZONAMIENTO` | Mostrar razonamiento del modelo |
| `SNAPCONTEXT_SANDBOX` | `1` fuerza sandbox Docker; `0` lo desactiva |
| `SNAPCONTEXT_SANDBOX_IMAGE` | Imagen Docker del sandbox |
| `SNAPCONTEXT_MULTI_AGENT` | Activa el flujo multi-agente |
| `SNAPCONTEXT_LSP` | Activa el análisis LSP |
| `SNAPCONTEXT_GRAPH_RAG` | Activa el Graph RAG |
| `SNAPCONTEXT_INDEX_BG` | `0` desactiva la indexación del grafo en segundo plano |
| `SNAPCONTEXT_PROMPT_CACHING` | Prompt caching por capas |
| `SNAPCONTEXT_COMANDO_TEST` | Comando de pruebas del bucle agéntico |
| `SNAPCONTEXT_MARKETPLACE_INDEX` | Índice del marketplace MCP |

Claves API (también configurables con `--init`): `GEMINI_API_KEY`,
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `DEEPSEEK_API_KEY`, `GROQ_API_KEY`.

## 🛡️ Robustez (degradación elegante)

Si el servidor LSP no está disponible o falla, SnapContext **degrada
automáticamente** a búsqueda por expresiones regulares (y embeddings
sintácticos ligeros) sin interrumpir el flujo ni mostrar tracebacks. La
indexación del **Graph RAG se ejecuta en segundo plano**, así que el CLI
arranca al instante y las consultas usan el modo degradado hasta que el grafo
termina de indexarse. Más detalles en [`docs/GRAPH_RAG_LSP.md`](docs/GRAPH_RAG_LSP.md).

## 🧭 Comandos

| Modo | Comando |
|------|---------|
| Tarea | `snapcontext "<consulta>"` (+ `--test-loop`) |
| Editor propio | `--editor propio` (por defecto, con backups automáticos) |
| Aider (opcional) | `--editor aider` |
| Chat interactivo | `--chat` |
| Planificador | `--plan "<tarea>"` |
| Autónomo | `--plan "<tarea>" --auto` |
| TUI inmersiva | `--tui` |
| Web | `--web` (alias `interactive`) |
| API REST | `--api` |
| Vista previa / revisión | `--vista-previa` · alias `review` |
| Memoria e historial | `--init-claude` · `--historial` / `--historial-limpiar` |
| Diagnóstico | `--diagnostico` · `--benchmark` |

Alias: `fix` (= `--test-loop`) · `review` · `server` (= `--server-loop`).

Subcomandos: `snapcontext plugin` · `snapcontext hook list` ·
`snapcontext revert <step>` · `snapcontext curador estado` ·
`snapcontext discord setup` · `snapcontext telegram setup` ·
`snapcontext github setup`.

### Casos de uso rápidos

```bash
# Flutter: detecta pubspec.yaml y ejecuta flutter test en bucle
snapcontext "el widget de login no muestra errores" --test-loop

# React/Node con Claude
snapcontext --provider anthropic "el formulario no valida el email"

# Python: añade tests y genera memoria del proyecto
snapcontext "añade tests para el módulo de pagos"
snapcontext --init-claude

# CI / automatización sin claves ni preguntas
snapcontext --plan "corregir tests rotos" --local --no-confirmar --auto
```

## 🧩 Módulos y Funcionalidades

### 🧠 Motor ReAct (por defecto)

El agente razona en bucle: elige herramientas (leer archivo, buscar código,
navegar, consultar el grafo…), observa el resultado y ajusta el plan. El
razonamiento se puede mostrar con `--mostrar-razonamiento`.

### 🤖 Multi-agente en paralelo

Flujo con Arquitecto (plan técnico), Programador (ediciones), QA Tester
adversarial (pruebas de hasta 2 iteraciones) y Supervisor. Los sub-agentes
independientes se ejecutan con un pool paralelo (`--multi-agent`).

### 🐳 Sandboxing con Docker (único)

Detecta comandos peligrosos (`_es_comando_peligroso`) y los ejecuta dentro de
un contenedor efímero (`--sandbox`) o en una sesión persistente por proyecto
(`--sandbox-session`). Autocuración: si la sesión se corrompe, se recrea.

### 👁️ Visión y navegador

Capturas de pantalla de la app (Flutter/web) y navegación con Playwright para
verificar cambios visualmente o extraer contenido.

### 🌍 Omnicanalidad

Gateways para **Discord**, **Telegram**, **web** (`--web`) y **TUI**
(`--tui`), todos sobre el mismo orquestador.

### 🔌 MCP nativo

Herramientas MCP integradas: bases de datos (`mcp_tools_db`), APIs HTTP
(`mcp_tools_api`) y navegador (`mcp_tools_browser`), más un marketplace de
servidores MCP (`snapcontext plugin` / índice configurable).

### 🕸️ Graph RAG y LSP

Grafo de dependencias del código (parser universal + tree-sitter) para
contexto expandido, y servidores LSP para símbolos, referencias y tipos — la
misma tecnología de Cursor y Claude Code.

### ⚡ XPU (Intel Arc)

Soporte experimental de aceleración Intel vía OpenVINO/onnx para embeddings y
modelos locales (`--xpu-model`, `--xpu-max-tokens`, `--xpu-temperature`).

### 🗺️ Planificador de tareas

`--plan` genera un plan multi-paso con dependencias, lo confirma el usuario y
se ejecuta paso a paso (o en paralelo). Modo autónomo con `--auto`: reintentos
automáticos (3 por paso) y respetando siempre `permisos.json`.

### 🧩 Hooks y plugins

Scripts personalizados en eventos del ciclo de vida (`snapcontext hook list`,
manifiesto `hooks.json`) y paquetes de plugins instalables.

### 📝 Git profundo

Commit por paso del plan, diffs interactivos antes de aplicar, backups
automáticos del editor y deshacer con `snapcontext revert <step>`.

### 💾 Memoria y aprendizaje

Memoria SQLite de decisiones, skills dinámicos, curador proactivo (daemon
opcional) y `CLAUDE.md` como memoria del proyecto.

## ⚖️ Comparativa

| Característica | SnapContext | Claude Code | Aider | Hermes |
|----------------|:-----------:|:-----------:|:-----:|:------:|
| Contexto automático del proyecto | ✅ | ✅ | ⚠️ manual (repo-map) | ⚠️ |
| Sandbox Docker para comandos | ✅ | ❌ | ❌ | ⚠️ |
| Planificador multi-paso con dependencias | ✅ | ✅ | ❌ | ✅ |
| Modo autónomo con reintentos | ✅ | ✅ | ❌ | ⚠️ |
| Multi-agente (QA adversarial) | ✅ | ⚠️ | ❌ | ✅ |
| Omnicanalidad (Discord/Telegram/web/TUI) | ✅ | ❌ | ❌ | ⚠️ |
| MCP nativo + marketplace | ✅ | ✅ | ❌ | ⚠️ |
| Graph RAG + LSP | ✅ | ✅ | ⚠️ | ❌ |
| Modelos 100% locales (Ollama) | ✅ | ❌ | ✅ | ✅ |
| Git profundo (revert por paso) | ✅ | ❌ | ⚠️ | ❌ |
| Visión + navegador integrados | ✅ | ⚠️ | ❌ | ⚠️ |
| Open source (MIT) | ✅ | ❌ | ✅ | ✅ |


## 🛠️ Instalación para desarrollo

```bash
git clone https://github.com/NicolasBruna24/snapcontext.git
cd snapcontext
pip install -e ".[dev]"     # dependencias de desarrollo (ruff, mypy, pytest-cov)

# Calidad de código (Fase 10)
python -m ruff check .      # linting
python -m ruff format .     # formateo
python -m mypy .            # tipos (configuración básica)
python -m pytest            # tests + cobertura

# Umbral estricto de cobertura (objetivo: 60%)
python -m pytest --cov --cov-fail-under=60
```

El proyecto se refactoriza por fases desde un monolito (`snapcontext.py`)
hacia módulos independientes: `seguridad`, `instalador`, `presentacion`,
`configuracion`, `planificador`, `permisos`, `hooks` y `mcp_tools`.

## 🤝 Contribuir

Las contribuciones son bienvenidas. Si tienes una idea, abre un issue o envía
un pull request.

1. Haz un fork del proyecto.
2. Crea tu rama de características (`git checkout -b feature/nueva-funcionalidad`).
3. Asegúrate de que pasan `ruff check .` y `pytest`.
4. Haz push a la rama y abre el pull request.

## ❓ FAQ

**¿Necesito una API key?**
No obligatoriamente: `--local` usa heurísticas y modelos locales (Ollama)
sin clave. Para proveedores en la nube, `snapcontext --init` configura y
prueba la clave.

**¿Qué modelos locales funcionan?**
Cualquiera servido por Ollama. Con modelos pequeños se activa automáticamente
`--modelo-ligero` y los prompts se adaptan.

**¿Es seguro dejarlo trabajar solo?**
Las acciones se confirman según `~/.snapcontext/permisos.json`, los comandos
peligrosos van al sandbox Docker y las escrituras validan que la ruta esté
dentro del proyecto. Con `--auto` se respetan los permisos guardados.

**¿Windows, Linux y macOS?**
Sí, los tres. Hay instaladores one-liner y `.exe` para Windows.

## 🗺️ Roadmap

- Limpieza de hallazgos de `ruff`/`mypy` restantes y subida de cobertura (Fase 10c).
- Extracción completa del orquestador y de los gateways de omnicanalidad.
- Mejoras del Graph RAG multi-repositorio.
- Estabilización de XPU en más hardware Intel.

## 📜 Historial de cambios

Todas las versiones y sus cambios están en [`CHANGELOG.md`](CHANGELOG.md).

## 💖 Support the Development

If **snapcontext** has saved you time, optimized your local workflow, or if
you want to support an independent student engineer building the future of
local AI agents, please consider [sponsoring the
project](https://github.com/sponsors/NicolasBruna24). Your sponsorship
directly funds cloud evaluation tokens, testing on alternative hardware
architectures (like Intel XPU), and speeds up the development roadmap.

## 📄 Licencia

MIT. Open-source y libre de usarlo, estudiarlo y mejorarlo.

