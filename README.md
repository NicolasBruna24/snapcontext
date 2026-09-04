# SnapContext

![v6.20.0](https://img.shields.io/badge/version-6.22.0-blue.svg)
[![PyPI](https://badge.fury.io/py/snapcontext.svg)](https://pypi.org/project/snapcontext/)
[![CI](https://img.shields.io/github/actions/workflow/status/NicolasBruna24/snapcontext/ci.yml?branch=main&label=tests)](https://github.com/NicolasBruña24/snapcontext/actions)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Plataformas](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macOS-lightgrey.svg)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

**SnapContext** es un asistente de IA con contexto automático para desarrollo:
detecta el tipo de proyecto, selecciona los archivos relevantes con IA, ejecuta
tareas con Aider, planifica trabajos complejos y aprende del proyecto mediante
una memoria persistente (`CLAUDE.md`).

- **Proveedores**: Gemini · Claude (Anthropic) · Ollama (local) · DeepSeek · Groq
- **Arquitectura**: orquestador + agentes (Contexto / Editor / Tester)
- **Seguridad**: permisos con confirmaciones (`~/.snapcontext/permisos.json`)

## ⚡ Mejora de rendimiento (v6.9.0)

SnapContext v6.9.0 es más rápido y ligero sin cambiar el comportamiento:

| Mejora | Detalle |
|---|---|
| **Inicio lazy** | Los imports pesados (`sentence-transformers`, `tree-sitter`, `graph_rag`, `multi_agent`, ...) se cargan bajo demanda. `--help` pasa de ~1.2s a <0.3s. |
| **Cache de embeddings** | SQLite en `~/.snapcontext/embeddings.db` (tabla `embeddings(archivo, hash, embedding BLOB)`). Los archivos sin cambios reutilizan su embedding (≈80% más rápido en re-escaneos). |
| **Graph RAG incremental** | El grafo se cachea en `~/.snapcontext/graph_cache.pkl` con un fingerprint por archivo; solo se reparsean los archivos modificados (≈70% más rápido). |
| **Fuzzy matching optimizado** | Contexto limitado a 20 líneas (`MAX_CONTEXTO_DIFUSO_LINEAS`) y resincronización de bloques con `difflib.get_close_matches` (≈50% más rápido en archivos grandes). |
| **Worker sin polling** | La cola de tareas usa `threading.Event` en lugar de `time.sleep`: CPU idle ≈0% y despertar instantáneo al encolar. |
| **Historial ReAct limitado** | Máximo 20 iteraciones (configurable vía `REACT_MAX_HISTORIAL`); al superarlo se comprime con un resumen LLM (≈30% menos memoria en sesiones largas). |

### Medir el rendimiento con `--benchmark`

```bash
python snapcontext.py --benchmark          # benchmark del proyecto actual
python snapcontext.py --benchmark ruta/    # benchmark de otro directorio
```

Ejemplo de salida (tabla con `rich`, no requiere API key):

```
⚡ Benchmark de rendimiento — SnapContext 6.9.0
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Fase                                 ┃ Tiempo (s) ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Inicio (import + CLI)                │     0.1902 │
│ CLI (crear_parser)                   │     0.0141 │
│ Escaneo de archivos                  │     0.0527 │
│ Selección (embeddings/heurística)    │     0.1015 │
│ Generación de plan (prompt+contexto) │     0.0021 │
│ Edición (fuzzy matching)             │     0.0089 │
│ Detección de pruebas                 │     0.0007 │
│ Tiempo total                         │     0.3704 │
└──────────────────────────────────────┴────────────┘
```

> Las cachés son opcionales y degradan silenciosamente si no se pueden
> escribir. No almacenan código fuente, solo hashes, fingerprints y vectores.


## 🌐 Navegador y multimodalidad (v6.10.0)

SnapContext puede **ver la interfaz de tu aplicación en ejecución** y depurar
errores visuales de forma autónoma (CSS roto, componentes que no renderizan,
formularios que fallan, ...).

## 🧠 Prompt Caching (v6.16.0)

SnapContext envía el mensaje del sistema (con las herramientas MCP) y la memoria
del proyecto (`CLAUDE.md`) en cada interacción. Con **Prompt Caching**, los
proveedores compatibles mantienen estos bloques **en caché durante la sesión**,
reduciendo drásticamente el coste (hasta ~70% en sesiones largas) y la latencia.

### ¿Qué hace?

- **Anthropic (Claude)** y **DeepSeek** soportan la marca
  `cache_control: {"type": "ephemeral"}`. SnapContext la añade a:
  1. El mensaje del sistema (el primero de la lista).
  2. Los mensajes con definiciones de herramientas MCP.
  3. El mensaje con la memoria del proyecto (`CLAUDE.md` / `SNAPCONTEXT.md`).
- **Gemini, Groq y Ollama** no soportan caching: se envían los mensajes tal cual
  (sin marcas), manteniendo la compatibilidad total.
- Las marcas **no modifican el contenido** de los mensajes: solo añaden la
  instrucción de caché.

### Activación (por defecto activado)

```bash
snapcontext "revisar login" --provider anthropic     # activado por defecto
snapcontext "revisar login" --no-prompt-caching      # desactivar
snapcontext --chat --no-prompt-caching               # desactivar en el chat
```

También se puede controlar con:

```bash
SNAPCONTEXT_PROMPT_CACHING=0 snapcontext "revisar login"   # desactivar
SNAPCONTEXT_PROMPT_CACHING=1 snapcontext "revisar login"   # activar
```

O desde `~/.snapcontext/config.json`:

```json
{
  "provider": "anthropic",
  "prompt_caching": false
}
```

### Métricas de caché (v6.16.0)

Con `--depurar`, SnapContext muestra estimaciones de tokens cacheados por
categoría al enviar cada petición:

```bash
snapcontext "revisar login" --provider anthropic --depurar
# ℹ Prompt Caching activado (anthropic): sistema (145 tokens), herramientas (230 tokens), CLAUDE.md (890 tokens)
```

Esto permite ver cuánto se está ahorrando en cada llamada sin necesidad de
inspeccionar los logs del proveedor.

> **Nota**: el Prompt Caching solo se aplica cuando el proveedor lo soporta y está
> activado; para proveedores sin soporte o con caching desactivado el flujo es
> idéntico al anterior. Al inicio de la sesión verás
> `🧠 Prompt Caching activado para <proveedor>` (o `no soportado`).


## 🖥️ TUI inmersiva (v6.17.0)

SnapContext incluye una **interfaz de terminal interactiva (TUI)** basada
en [Textual](https://github.com/Textualize/textual), que complementa el CLI
tradicional y la interfaz web con un centro de control inmersivo.

> **v6.17.0**: la TUI se consolida como el modo inmersivo recomendado (`--tui`),
> con arranque optimizado, mensaje de inicio explícito y compatibilidad total
> con el flujo ReAct y el editor propio (no rompe el CLI sin `--tui`).

```bash
pip install "snapcontext[tui]"        # instalar la dependencia opcional
snapcontext --tui                     # abrir la TUI (sin consulta)
snapcontext --tui "revisar login"     # abrir la TUI y lanzar el agente
```

### ¿Qué incluye?

- **Pestañas**:
  - 📜 **Logs**: salida en tiempo real del agente, coloreada por nivel
    (info / warning / error) e integrada con el flujo ReAct
    (💭 Pensamiento → ⚡ Acción → 👁 Observación).
  - 🎛️ **Control**: estado del agente (inactivo / pensando / ejecutando /
    esperando / error), contador de pasos, tiempo total y botones de
    Pausar / Reanudar / Cancelar.
  - 🔀 **Diffs**: visor de los cambios propuestos por el editor propio
    (con `--mostrar-diff`), coloreados (+ verde / - rojo / @@ cabeceras).
- **Árbol de archivos** colapsable del proyecto actual (`DirectoryTree`).
- **Atajos**: `Ctrl+Q` salir · `Ctrl+L` limpiar logs · `Ctrl+D` limpiar diffs ·
  `Ctrl+T` mostrar/ocultar el árbol.

### Arquitectura (modular, sin bloquear al agente)

- El agente (`react_agent.py`, editor propio) envía eventos a una cola
  (`tui_hub.py`, `put_nowait`); si la TUI está inactiva o la cola llena, el
  evento se descarta: **coste ~0 para el CLI tradicional**.
- La app (`tui_app.py`) drena la cola con un temporizador asíncrono
  (máx. 200 eventos/tick) sin bloquear la UI.
- El agente corre en un hilo demonio; la TUI es solo presentación.

### Sin Textual instalado

```text
❌ Textual no está instalado. Instala el grupo opcional:
    pip install snapcontext[tui]
```

> **Nota**: el CLI sin `--tui` funciona exactamente igual que antes; la TUI es
> un modo adicional y Textual es una dependencia opcional (grupo `[tui]`).


## 📝 Git profundo (v6.20.0)

SnapContext crea **commits atómicos por paso** con mensajes generados por IA
(Conventional Commits) y permite **revertir** cualquier paso:

```bash
# Commits automáticos tras cada paso del plan (activado por defecto)
snapcontext --plan "añadir login" --git-commit

# Desactivar commits automáticos
snapcontext --plan "añadir login" --no-git-commit

# Mensaje de commit manual (en lugar de IA)
snapcontext --plan "añadir login" --git-mensaje "feat: login social"

# Revertir un paso (por id de la tabla 'pasos' en la BD)
snapcontext revert 3
snapcontext revert            # revierte el último paso commiteado
snapcontext --plan "..." --git-revert 5   # variante con flag
```

Cada commit se registra en la tabla `pasos` de `~/.snapcontext/memoria.db`
(hash, descripción, tipo y fecha). El revert usa `git revert --no-commit`
y registra el resultado; si hay conflictos sugiere `git mergetool`. Los
mensajes generados se saneían para no filtrar secretos (claves API, tokens).

## 🤖 Sub-agentes dinámicos (v6.20.0)

El equipo multi-agente puede delegar sub-tareas en **sub-agentes
especializados** que se instancian bajo demanda, con contexto aislado y
ejecución en paralelo controlada.

```bash
snapcontext --multi-agent --sub-agents "añade exportación a PDF"
snapcontext --multi-agent --sub-agents --max-parallel 5 "mejora el rendimiento"
snapcontext --sub-agente-listar                  # v6.20.0: lista sub-agentes
snapcontext --sub-agente-nuevo auditor "revisa seguridad"   # v6.20.0: nuevo rol
```

### Roles predefinidos

| Rol | Especialidad | Herramientas |
|-----|--------------|--------------|
| `scout` | Leer documentación, explorar código y APIs | `leer_archivo`, `buscar_codigo`, `api_inspect`, `browser_get_text` |
| `debugger` | Analizar errores y logs, diagnosticar causas | `leer_archivo`, `buscar_codigo`, `ejecutar_comando`, `ejecutar_pruebas` |
| `frontender` | Revisar CSS/HTML/interfaz | `leer_archivo`, `buscar_codigo`, `editar_archivo`, `browser_*` |
| `tester` | Ejecutar pruebas aisladas e informar | `ejecutar_pruebas`, `ejecutar_comando`, `leer_archivo` |
| `reviewer` (nuevo v6.20.0) | Revisar PRs, diffs e impacto | `leer_archivo`, `buscar_codigo`, `ejecutar_comando`, `ejecutar_pruebas` |
| `documentador` | Generar/actualizar README, CLAUDE.md | `leer_archivo`, `buscar_codigo`, `editar_archivo` |

### v6.20.0 — registro e invocación dinámica

- **`sub_agent.SubAgentRegistry`**: registro consultable (`registrar`,
  `obtener`, `listar`) con sub-agentes por defecto (**scout, debugger,
  reviewer, documentador**). Se pueden registrar roles nuevos sin tocar el
  código (plugins / `--sub-agente-nuevo`).
- **`sub_agent_prompts.py`**: módulo canónico con los prompts de sistema de
  cada rol por defecto.
- **Invocación bajo demanda desde el Supervisor** (`Supervisor.invocar_sub_agente(nombre, consulta)`).
- **Herramienta ReAct `invocar_sub_agente(nombre, consulta)`**: el agente
  ReAct puede delegar una tarea especializada en un sub-agente con contexto
  aislado (disponible por defecto; los sub-agentes no pueden anidarse, ya que
  el rol filtra sus herramientas).

### Cómo funciona

- **Contexto aislado**: cada sub-agente tiene su propio historial ReAct; no
  comparte contexto con el agente principal ni con otros sub-agentes.
- **Detección automática**: el Supervisor analiza el plan del Arquitecto
  (palabras clave como "documentación", "error", "pruebas", "CSS"...) y
  delega los pasos aplicables al rol adecuado.
- **Paralelismo**: los sub-agentes corren en hilos con un semáforo
  (`--max-parallel`, por defecto 3). También pueden encolarse como tareas
  asíncronas de la cola v6.8.0 (`sub_agent.encolar_sub_agente`).
- **Comunicación**: los resultados se publican en el `Buzon` compartido
  (`resultado_sub_agente`, `sub_tarea_completada`) y cada sub-agente puede
  recibir mensajes previos a su ejecución (`enviar_mensaje`).
- **Seguridad**: cada rol solo ve sus herramientas (mínimo privilegio) y
  respeta los mismos permisos y confirmaciones que el agente principal.

> **Nota**: sin `--sub-agents` el pipeline multi-agente es idéntico al de
> siempre; la funcionalidad es 100 % opcional.


## 🔗 LSP e indexación global (v6.14.0)

SnapContext ahora se conecta a **Language Server Protocol (LSP)** para dar al
agente una comprensión profunda del código: resolución de definiciones,
referencias globales y tipos — la misma tecnología que usan Cursor, Claude Code
y los editores modernos.

```bash
# Activar el LSP (lanza el servidor perezosamente, solo al usarlo)
snapcontext --lsp "refactoriza la función parsear_datos"

# También con la variable de entorno
SNAPCONTEXT_LSP=1 snapcontext "arregla el bug del parser"
```

### Herramientas nuevas para el agente

| Herramienta | Descripción |
|-------------|-------------|
| `lsp_definicion(archivo, linea, columna)` | Resuelve dónde se define un símbolo (aunque esté importado de otro módulo). |
| `lsp_referencias(archivo, linea, columna)` | Encuentra **todas** las referencias a una función/clase en el proyecto. |
| `lsp_tipo(archivo, linea, columna)` | Devuelve el tipo de una variable/expresión (hover). |

### Servidores soportados (detectados con `shutil.which`)

| Lenguaje | Comando del servidor |
|----------|---------------------|
| Python | `pyright-langserver` o `pylsp` |
| JavaScript/TypeScript | `typescript-language-server` |
| Go | `gopls` |
| Rust | `rust-analyzer` |
| Java | `jdtls` |
| C# | `OmniSharp` |

### Caché de consultas

- **En memoria** (por sesión) y **persistente** en `~/.snapcontext/lsp_cache.db`
  (SQLite).
- Clave: `(archivo, linea, columna, tipo_consulta)`.
- Invalidación automática por **hash del contenido** del archivo: si cambia,
  la entrada caduca y se vuelve a consultar.

### Arquitectura

- `lsp_client.py`: cliente JSON-RPC puro sobre stdio (**sin dependencias
  obligatorias**); `CacheLSP` y `LSPClient` con contexto (`with`), timeouts y
  cierre ordenado (`shutdown` → `exit` → kill).
- Inicio **perezoso** (singleton): el servidor solo se lanza en la primera
  consulta, nunca al abrir la sesión.
- Integrado en el agente ReAct y en los sub-agentes dinámicos (v6.13.0) cuando
  `--lsp` está activo.
- Si el servidor no está instalado o falla: mensaje claro y **continúa sin
  LSP** (nunca bloquea la tarea).

```bash
# Servidor LSP de Python opcional (grupo [lsp])
pip install "snapcontext[lsp]"
```



### Instalación (opcional)

```bash
pip install 'snapcontext[browser]'
playwright install chromium
```

### Uso

```bash
# Activar el modo navegador para la tarea del agente (headless por defecto)
snapcontext --browser "revisa la UI en http://localhost:3000 y arregla el botón de login"

# Con interfaz visible (para depurar en vivo)
snapcontext --browser --browser-headed "comprueba que el dashboard renderiza bien"
```

Con `--browser` el agente ReAct dispone de estas herramientas MCP:

| Herramienta | Descripción |
|---|---|
| `browser_abrir(url, wait_for?, timeout?)` | Abre una URL (espera opcional a un selector). |
| `browser_screenshot(url?, full_page?, selector?)` | Captura de pantalla en base64 PNG (página completa o elemento). |
| `browser_click(selector)` / `browser_type(selector, texto)` | Interacción: clic y escritura en campos. |
| `browser_get_text(selector)` | Extrae el texto de un elemento. |
| `browser_analizar_imagen(imagen_base64, pregunta)` | Análisis visual con modelos de visión (Gemini 2.5 Pro, Claude 3.7 Sonnet+). |
| `browser_cerrar()` | Cierra el navegador y libera recursos. |

Notas:
- El navegador es **headless por defecto** (seguridad; no interfiere con tu
  escritorio) y se mantiene **persistente durante toda la tarea** (una única
  instancia; si muere, se reinicia automáticamente).
- El análisis visual (`browser_analizar_imagen`) requiere un modelo con
  visión; con otros modelos devuelve un error claro en lugar de fallar.
- Sin `--browser`, Playwright nunca se importa (carga perezosa) y el agente
  no dispone de herramientas de navegador.



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
pip install aider-chat                  # ediciones de código
snapcontext --init                      # asistente inicial + API key
```

## 🔍 Manejo de contexto inteligente (v6.1.0)

Los modelos locales (p. ej. `deepseek-r1:14b`, con 4096 tokens de contexto)
fallan constantemente al recibir archivos grandes. Con el editor propio
(`--editor propio`), SnapContext ahora gestiona el contexto automáticamente:

1. **Estima los tokens** del archivo antes de enviarlo (`tiktoken` si está
   disponible; si no, la regla práctica 1 token ≈ 4 caracteres).
2. Si supera `--max-context-tokens` (3000 por defecto), **extrae solo el bloque
   relevante** (la función/clase mencionada en la tarea, detectada con
   tree-sitter o regex) y lo envía junto a un resumen AST del resto:

   ```text
   ℹ Archivo grande (4458 tokens). Usando contexto selectivo (bloque: 'parsear_archivo')...
   ```

3. Si el proveedor falla por límite de contexto, **reintenta con el archivo
   completo** (el modelo real puede tener más contexto del estimado) y, si
   vuelve a fallar, **pasa a la siguiente estrategia**
   (AST → Parche → Sobrescritura).
4. Opcionalmente, con `--editor-fallback`, si el editor propio no puede editar
   un archivo **invoca Aider automáticamente** (si está instalado):

   ```text
   ⚠ El editor propio no pudo editar este archivo. Usando Aider como fallback...
   ```

### Flags

```bash
# Límite de tokens por petición (por defecto 3000)
snapcontext "renombra la función parsear_archivo" --editor propio --max-context-tokens 2000

# Fallback automático a Aider cuando el editor propio falla
snapcontext "arregla el parser" --editor propio --editor-fallback
```

El comportamiento es **opcional y compatible hacia atrás**: sin flags se usa
el umbral por defecto (3000 tokens) y no se invoca Aider.

## 🤖 Multi-agentes en paralelo (v6.0.0)

Para tareas de desarrollo complejas, SnapContext puede coordinar un **equipo de
agentes especializados** bajo un Supervisor:

| Rol | Descripción |
| --- | --- |
| 🧠 **Arquitecto** | Diseña la solución y genera un plan de alto nivel (objetivo, módulos y archivos a tocar) con la IA. |
| 💻 **Programador** | Implementa el código con el **editor propio** (cadena AST → Parche → Sobrescritura). |
| 🧪 **Tester** | Ejecuta las pruebas (detección automática de v5.3.0) y devuelve éxito/fallo + salida. |
| 🤖 **Supervisor** | Coordina el flujo: Arquitecto → Programador → Tester, con **bucle de realimentación** si una prueba falla. |

Los agentes se comunican a través de un **buzón de mensajes** (cola thread-safe)
y, por ahora, trabajan en *pipeline* (secuencial con transferencia de estado).

### Uso

```bash
snapcontext --multi-agent "añadir autenticación con Google"
export SNAPCONTEXT_MULTI_AGENT=1        # activar por defecto (PowerShell: $env:SNAPCONTEXT_MULTI_AGENT="1")
```

En modo interactivo el Supervisor muestra el plan y pide confirmación; con
`--auto` (`snapcontext --multi-agent --auto "…"`) ejecuta sin preguntar. El
modo es **opcional**: `--plan`, ReAct y el flujo clásico siguen intactos.

## 🧭 Verificación temprana del directorio (v5.6.0)

Al inicio, si el directorio actual no parece ser la raíz de un proyecto (sin
`package.json`, `go.mod`, `pyproject.toml`, `src/`, `tests/`, …), SnapContext
muestra un aviso y (en modo interactivo) te deja **continuar**, ejecutar la
**demo** o **salir**. Usa `--no-validar-proyecto` para saltarte este aviso en
directorios que no siguen la estructura típica.

```bash
snapcontext "tarea" --no-validar-proyecto
```

> Para empezar, ejecuta SnapContext en la raíz de tu proyecto o usa `--demo`
> para probar sin proyecto.

## 🐳 Sandboxing con Docker (v4.3.0)

Ejecuta los comandos de SnapContext (bucle de pruebas, `execute_command`,
pasos `ejecutar` del planificador, plugins…) dentro de un **contenedor Docker
aislado**. Ideal cuando no se confía en el código o necesitas dependencias
específicas.

### Uso básico

```bash
# Todo el trabajo de comandos dentro del contenedor
snapcontext "arreglar el checkout" --test-loop --sandbox

# Con imagen personalizada y comando de preparación
snapcontext "añadir tests" --test-loop --sandbox \
    --sandbox-imagen python:3.11-slim \
    --sandbox-comando "pip install pytest"

# En el planificador: pasos "ejecutar" y MCP con comandos usan el sandbox;
# "editar" y "consultar" siguen en el host.
snapcontext "migrar a pytest" --plan --sandbox

# Imagen por defecto sin flag:
export SNAPCONTEXT_SANDBOX_IMAGE=ubuntu:22.04   # PowerShell: $env:SNAPCONTEXT_SANDBOX_IMAGE="..."
```

### Cómo funciona

- **Detección**: `_docker_disponible()` comprueba `docker --version` (binario)
  y `docker info` (daemon activo).
- **Ejecución**: cada comando se lanza como
  `docker run --rm -v "<proyecto>:/workspace" -w /workspace -e GEMINI_API_KEY … <imagen> sh -c "<comando>"`.
- **Montaje**: el directorio del proyecto se monta en `/workspace`.
- **Variables de entorno**: las claves (`*_API_KEY`, `OLLAMA_URL`, …) se pasan
  al contenedor automáticamente.
- **Preparación**: `--sandbox-comando` antepone un comando de setup
  (`apt update && apt install -y …`) antes del comando principal.

### Qué corre dentro y fuera del sandbox

| Dentro del contenedor | Fuera (host) |
| --- | --- |
| Bucle de pruebas (`--test-loop`) | Herramientas de solo lectura (`grep`, `read_file`, `list_files`, `ast`) |
| MCP `execute_command` (+ background) | Pasos `editar` y `consultar` del planificador |
| Pasos `ejecutar` del planificador | Selección de contexto, chat con la IA |
| Plugins y herramientas de usuario | |

### Errores y logs

- Cada comando sandboxeado registra `ℹ [sandbox] Ejecutando en contenedor: …`.
- Si un comando falla dentro del contenedor se muestra su salida y código como
  siempre.
- Si Docker no está disponible y usaste `--sandbox` explícitamente,
  SnapContext falla con un mensaje claro; sin el flag nunca cambia nada
  (compatibilidad total).

## 🎮 Gateway de Omnicanalidad: Discord (v4.5.0)

Igual que Telegram, SnapContext puede recibir **Slash Commands** de Discord,
procesarlos con el motor interno y responder en el mismo canal. 100 % local
(self-hosted): solo `httpx` + `cryptography`, sin `discord.py`.

### Configuración

```bash
# 1) https://discord.com/developers/applications → tu app:
#    - General Information: copia PUBLIC KEY y APPLICATION ID.
#    - Bot: crea el bot y copia el TOKEN.
snapcontext discord setup --public-key <KEY> --app-id <ID> --token <BOT_TOKEN>

# 2) Expón el servidor (ngrok o VPS) y apunta el endpoint en el portal:
ngrok http 8001                      # si usas `snapcontext --api`
#  General Information → INTERACTIONS ENDPOINT URL:
#      https://<tu-dominio>/webhook/discord
#  Discord lo verifica con un PING; respondemos {"type": 1} automáticamente.

# 3) Arranca el servidor:
snapcontext --api        # o: snapcontext --web
```

Variables de entorno equivalentes (prioridad sobre `config.json`):
`DISCORD_PUBLIC_KEY`, `DISCORD_APPLICATION_ID`, `DISCORD_BOT_TOKEN`,
`DISCORD_WEBHOOK_URL` (webhook estándar de canal, alternativa).

### Comandos Slash soportados

- `/start` · `/help` → mensaje de bienvenida.
- `/snap <tarea>` → ejecuta el pipeline completo.
- `/fix <tarea>` → bucle de pruebas (equivale a `snapcontext fix`).
- `/plan <tarea>` → planificador (`snapcontext --plan`).

### Detalles técnicos

- **Verificación de firma Ed25519**: headers `X-Signature-Ed25519` /
  `X-Signature-Timestamp`; firma inválida → `401`. Sin `DISCORD_PUBLIC_KEY`
  → `503`.
- **Respuesta diferida**: el endpoint responde `{"type": 5}` (<3 s) y el
  agente trabaja con `asyncio.create_task`; el resultado llega como follow-up
  vía `/webhooks/{app_id}/{interaction_token}` (válido 15 min).
- **Mensajes largos**: >2000 caracteres se envían como archivo `.txt`.
- Módulo: `discord_gateway.py` · Endpoint: `web/app.py` (`/webhook/discord`).

## 📱 Gateway de Omnicanalidad: Telegram (v4.4.0)

SnapContext puede recibir mensajes por **Telegram**, procesarlos con su motor
de IA y responder en el mismo chat.

### Configuración

```bash
# 1) Crea un bot con @BotFather y configura token + webhook:
snapcontext telegram setup --token 123456:ABC-DEF --webhook-url https://mi-dominio.ngrok.io
snapcontext telegram estado              # ver configuración actual

# 2) Arranca el servidor (API o web); el webhook queda expuesto en:
#    POST /webhook/telegram
snapcontext --api        # o: snapcontext --web
```

También vale con variables de entorno (prioridad sobre `config.json`):
`TELEGRAM_BOT_TOKEN` y `TELEGRAM_WEBHOOK_URL`.

### Uso desde Telegram

- `/start` → mensaje de bienvenida.
- `arregla el login` → ejecuta el pipeline completo y responde con el resultado.
- `/fix <tarea>` → igual que `snapcontext fix` (bucle de pruebas).
- `/plan <tarea>` → igual que `snapcontext --plan`.

### Detalles técnicos

- **Webhook asíncrono**: el endpoint responde `200 OK` de inmediato (Telegram
  corta a los ~30 s) y procesa el mensaje con `asyncio.create_task`; sin token
  configurado responde `503`.
- **Motor interno**: la consulta se ejecuta con la API interna del núcleo
  (`flujo_principal` / `_ejecutar_planificador`) en un executor, nunca por
  subprocess del CLI.
- **Mensajes largos**: si la salida supera los 4096 caracteres de Telegram, se
  envía un resumen + la salida completa como documento `.txt`.
- Módulo: `telegram_gateway.py` · Endpoint: `web/app.py` (`/webhook/telegram`).

## 🧰 Instalación y onboarding sin fricción (v3.1.x)

Objetivo: instalar SnapContext y empezar a usarlo en menos de 5 minutos.

### Modo offline con Ollama por defecto
Si no hay ninguna API key (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
`DEEPSEEK_API_KEY`, `GROQ_API_KEY`, …), SnapContext usa **Ollama
automáticamente** eligiendo el modelo más ligero disponible (`llama3.2`,
`phi3`, …). Si tampoco hay Ollama, muestra un mensaje claro:

> No se encontró una API key ni Ollama. Puedes instalar Ollama desde
> <https://ollama.com> o configurar una API key con `snapcontext --init`.

### `snapcontext --diagnostico`
Revisa tu instalación con resumen en colores (verde OK / amarillo aviso /
rojo error):
- Versión de Python y si está en el PATH.
- Instalación de SnapContext (`python -m snapcontext --version`).
- Dependencias opcionales (`questionary`, `fastapi`, `sentence-transformers`, …).
- Estado del PATH (si `snapcontext` es accesible como comando).
- Memoria: integridad de la base SQLite y número de skills.
Cada problema incluye su solución sugerida.

### `snapcontext --reparar`
Arregla instalaciones rotas:
- Limpia entornos corruptos de `uv` (carpetas vacías en `~/.snapcontext`).
- Reinstala SnapContext con `pip`.
- Recrea la base SQLite si está corrupta (respaldando la anterior).
- Si el PATH no incluye la carpeta de scripts, ejecuta `--setup-path`.

### Tutorial y asistente mejorados
- `snapcontext --bienvenida`: tutorial interactivo de primeros pasos.
- **(v3.1.1)** `snapcontext` sin argumentos muestra una ayuda resumida y
  amigable con ejemplos (código 0), en lugar de un error.
- **(v3.1.1)** En el primer uso se ejecuta la bienvenida automáticamente:
  se registra en `~/.snapcontext/estado.json` (`"primer_uso": false`) para
  no repetirla; `--bienvenida` explícito permite volver a verla.
- `snapcontext --init` ahora también pregunta si quieres configurar Ollama
  (ofreciendo abrir <https://ollama.com>), crear un proyecto de prueba y
  ejecutar el tutorial interactivo.

### Instalador gráfico para Windows
El instalador NSIS (`installer.nsi`, generado con
`scripts/empaquetar_exe.ps1`) ahora:
- Detecta Python y guía al usuario (abre python.org si falta).
- Añade una sección opcional "Instalar/actualizar con pip" que deja el
  comando `snapcontext` disponible en cualquier terminal.
- Sigue añadiendo la carpeta al PATH y creando accesos directos.

### Extensión VS Code en TypeScript (v3.2.0)
La extensión vive en `vscode/` y está escrita en TypeScript:
- `vscode/src/extension.ts` — código fuente tipado (modo `strict`).
- `vscode/tsconfig.json` — ES2020, commonjs, salida en `out/`.
- Compilar: `cd vscode && npm install && npm run compile` (genera
  `out/extension.js`, que es el entry point del manifiesto).
- Empaquetar: `vscode/scripts/empaquetar.ps1` / `.sh` (compilan antes de
  ejecutar `vsce package`).

### Editor propio refinado (v3.3.0)
- **Detección de lenguaje robusta**: extensión + heurística por contenido
  (shebang, patrones de código) para proyectos mixtos.
- **Manejo de conflictos en parches**: validación previa del contenido del
  archivo y resolución automática línea a línea si `git apply`/`patch`
  fallan (con aviso de aplicación parcial); ya no se sobrescribe a ciegas.
- **Prompt de edición mejorado**: lenguaje, tamaño, resumen AST con
  posiciones y reglas de precisión (estilo conservado, cambios mínimos).
- **Aprendizaje de patrones**: las ediciones exitosas se guardan como skills
  (`editor-<patrón>`) y su estrategia se reutiliza automáticamente en
  tareas similares.

## 🚀 Inicio rápido (5 minutos)

```bash
# 1. Tarea directa con editor integrado propio
snapcontext "arregla el botón de pago" --editor propio

# 2. Planificador autónomo
snapcontext plan "añadir validación al formulario" --auto

# 3. Chat interactivo
snapcontext --chat

# 4. Interfaz web completa
snapcontext interactive  # o snapcontext --web
```

Auto-detección de proyecto (Flutter, Node/React, Python, Go, Rust…) que ajusta
carpetas, extensiones y comandos de test por defecto.

## 🛠️ Editor Integrado Propio (v2.2.0)

SnapContext incluye su propio editor integrado con soporte de parches unificados
y edición basada en AST (Árbol Sintáctico) para refactorizaciones precisas:
- **`--editor propio`** (por defecto desde v4.1.0): Aplica cambios directamente y crea copias de seguridad automáticas en `~/.snapcontext/backups/`.
- **Robustez transaccional (v4.6.0)**: edición multiarchivo atómica con rollback automático si algún archivo falla, backup obligatorio (la edición aborta si el backup falla) y fuzzy matching (`difflib.SequenceMatcher`) que tolera comentarios/espacios cambiados al aplicar parches.
- **Escalabilidad e impacto (v4.7.0)**: análisis previo de impacto con el grafo de dependencias (advierte de archivos afectados, permite añadirlos a la edición o abortar; con `--auto` solo avisa) y contexto selectivo para archivos grandes (>600 líneas): solo se envía al modelo el resumen AST y los bloques relevantes, nunca el archivo completo.
- **CLI profesional (v4.8.0)**: nueva capa `ui.py` sobre [Rich](https://github.com/Textualize/rich) — banner con tabla de comandos, barras de progreso en el escaneo, tabla de impacto por dependencias, diffs coloreados y prompts estilizados. Silencioso automático con `--auto` y degradación elegante sin `rich`.
- **`--modo-edicion {auto,parche,sobrescribir,ast}`**:
  - `auto` (por defecto): Genera un parche unificado (`git apply` / `patch`), pero
    para refactorizaciones estructurales intenta primero la edición **AST** y cae a
    sobrescritura si nada es aplicable.
  - `ast`: Edita comprendiendo la estructura sintáctica del archivo (Python con
    `ast`, otros lenguajes con `tree-sitter` si está instalado). Soporta renombrar
    símbolos, insertar imports y recibir el código completo resultante.
  - `parche`: Fuerza la aplicación de parches unificados para ediciones precisas.
  - `sobrescribir`: Sobrescribe el archivo completo.
- **`--editor aider`**: Mantiene el flujo de edición con Aider para máxima compatibilidad.

### Editor propio (modo por defecto desde v4.1.0)

Desde la v4.1.0, **el editor propio es el modo por defecto**, reduciendo la
dependencia de Aider sin eliminarla (`--editor aider` sigue disponible).

**Cadena de auto-reparación**: para cada archivo se intentan las estrategias en
orden — **AST → Parche → Sobrescritura** — informando cuál está en uso:

```
ℹ Editor propio: usando estrategia AST para 'modulo.py'...
ℹ Editor propio: usando estrategia PARCHE para 'modulo.py'...
```

Si todas fallan, verás un error claro con las estrategias intentadas, el motivo
y la sugerencia de probar con `--editor aider`. El fallo queda registrado en
`~/.snapcontext/logs/editor_fallos.log` para depuración.

**Resolución interactiva de conflictos**: si un parche no se aplica limpiamente,
se ofrece elegir entre `[a]plicar de todas formas`, `[v]er el diff`,
`[r]eintentar con el proveedor de IA` o `[c]ancelar conservando el original`.
En modo `--auto` no se pregunta: se salta automáticamente a la siguiente
estrategia. Funciona igual desde la CLI que desde `--chat`.

### 🐳 Persistencia de Docker por sesión (v6.4.0)

Por defecto cada comando se ejecuta en un contenedor efímero
(`docker run --rm`), lo que impide flujos que compartan estado entre comandos.
Con `--sandbox-session`, SnapContext mantiene **un único contenedor vivo**
durante toda la tarea:

```bash
# Plan de varios pasos con un solo contenedor (npm install → npm test comparten estado)
python snapcontext.py --auto --sandbox "npm install && npm test" --sandbox-session "actualiza la dependencia X y ejecuta los tests"

# Bucle ReAct con sesión persistente
python snapcontext.py --react "instala pytest y arregla los tests" --sandbox --sandbox-session
```

Cómo funciona:

- La sesión se crea de forma **perezosa** en el primer comando
  (`docker run -d --name snap-session-<id> -v "<proyecto>:/workspace"
  -w /workspace <imagen> tail -f /dev/null`) y se reutiliza para **todos**
  los comandos posteriores mediante `docker exec`.
- El id se guarda en `~/.snapcontext/session_id.txt`.
- Al finalizar la tarea (éxito, aborto o error), e incluso al pulsar
  `Ctrl+C`, la sesión se **destruye automáticamente** para no dejar
  contenedores huérfanos.
- Si una ejecución anterior dejó huérfanos, límpialos con:
  `python snapcontext.py --sandbox-session-clean` (pedirá confirmación; en
  `--auto` los elimina sin preguntar).
- Compatibilidad: sin `--sandbox-session` el comportamiento es exactamente el
  de siempre (`docker run --rm` por comando). El contenedor de sesión monta
  **solo** el directorio del proyecto en `/workspace`.

### 📝 Mejora del editor de parches (v6.3.0)

El editor de parches es ahora mucho más tolerante gracias al **fuzzy matching real**
y a la **resolución automática de conflictos**. Cuando `git apply`/`patch` fallan y
la resolución incremental no encuentra una coincidencia exacta, se prueban, en
orden y solo cuando la anterior falla:

1. **Coincidencia exacta** cerca de la posición declarada del hunk.
2. **Coincidencia por variantes**: tolera reindentaciones y comentarios
   añadidos/eliminados (espacios colapsados y sin comentario final `#`/`//`).
3. **Emparejamiento difuso real** con `difflib.SequenceMatcher`: busca la mayor
   similitud de las líneas de contexto (ratio medio ≥ 0.85, ≥ 0.90 por línea),
   tolerando comentarios, espacios y **variables renombradas**.
4. **Resincronización a nivel de bloque**: si nada encaja, busca en todo el archivo
   la ventana más parecida al bloque original (≥ 0.80) y la reemplaza conservando
   las líneas de contexto locales del usuario.

Estos umbrales (`UMBRAL_DIFUSO_HUNKS`, `UMBRAL_DIFUSO_LINEA`,
`UMBRAL_DIFUSO_BLOQUE`) solo se consultan cuando un hunk falla: el caso normal no
paga el coste de `SequenceMatcher`. Si un hunk sigue sin encajar, la operación se
aborta **sin tocar nada** (todo-o-nada) y verás un mensaje claro con una sugerencia
accionable.

> **`--mostrar-diff`**: muestra el diff propuesto (coloreado) antes de aplicarlo y
> pregunta si aplicarlo (`[a]`), cancelarlo (`[c]`) o editarlo manualmente (`[e]`).
> En modo `--auto` muestra el diff sin bloquear. Sin el flag el parche se aplica
> sin preguntar, exactamente como siempre.

```bash
# Revisar cada parche antes de aplicarlo
snapcontext "añadir endpoint de métricas" --editor propio --mostrar-diff

# Comportamiento por defecto: aplica sin preguntar
snapcontext "añadir endpoint de métricas" --editor propio
```

**Optimizado para modelos locales (Ollama)**: si el proveedor es Ollama, el
editor usa automáticamente **prompts concisos** en sus tres estrategias (menos
contexto e instrucciones directas). También puedes forzarlos en cualquier
proveedor con `--modelo-ligero` si sabes que usas un modelo pequeño.

```bash
# Edición precisa mediante diffs unificados
snapcontext "añadir endpoint de métricas" --editor propio

# Forzar modo parche
snapcontext "corregir tipado en login" --editor propio --modo-edicion parche

# Refactorización estructural con AST
snapcontext "renombrar la variable 'usuario' a 'cuenta'" --editor propio --modo-edicion ast

# Validación de sintaxis desactivada (comportamiento previo a v3.4.0)
snapcontext "corregir tipado" --editor propio --no-validar-sintaxis
```

### Validación de sintaxis antes de guardar (v3.4.0)

Cuando uses `--editor propio`, SnapContext **valida la sintaxis** del código que
ha generado la IA **antes de escribirlo en disco**. Si la validación falla, el
cambio se rechaza y se le envía el error al proveedor para que lo corrija,
repitiéndose hasta `--max-intentos-validacion` intentos (por defecto 3). Si tras
esos intentos el código sigue roto, se **cancela la edición** y el archivo queda
intacto.

- Se detecta el lenguaje del archivo y se usa el validador adecuado:
  Python (`py_compile`), JavaScript/TypeScript (`node --check`), Dart
  (`dart analyze` → `dart format`), Go (`go build -n` → `gofmt -e`), Rust
  (`rustc --parse-only`), Java (`javac -Xlint:none`), C/C++ (`gcc`/`clang
  -fsyntax-only`). Si no hay validador o comando disponible, se omite la
  validación (solo aviso en logs con `--depurar`).
- La validación se hace sobre un **archivo temporal**, nunca toca el original.
- Flag `--validar` (por defecto activado) / `--no-validar-sintaxis` para
  desactivarla. Nota: el nombre usa `--no-validar-sintaxis` porque
  `--no-validar` ya es alias de `--iniciar-proyecto`.

```bash
# Desactivar la validación por completo (comportamiento anterior)
snapcontext "..." --editor propio --no-validar-sintaxis

# Ajustar el número de reintentos
snapcontext "..." --editor propio --max-intentos-validacion 5
```

### 🧠 Asesor de código proactivo (v3.5.0)

SnapContext no solo ejecuta órdenes: también **analiza tu código por su cuenta**
y te sugiere mejoras (refactorizaciones, deuda técnica, optimizaciones).

```bash
# Análisis informativo (nunca modifica código)
snapcontext --asesor
snapcontext --sugerir          # alias

# Aplicar automáticamente las mejoras seguras (renombrar símbolos);
# cada cambio se valida antes de guardarse y se descarta si rompe la sintaxis
snapcontext --asesor-auto

# Ajustar la sensibilidad del detector de funciones largas (defecto: 20)
snapcontext --asesor --asesor-umbral 30
```

**Qué detecta:**

- 🔴 **Prioridad alta**: `except:` desnudos (capturan todo sin querer).
- 🟡 **Prioridad media**: funciones largas (> 20 líneas), clases con demasiadas
  responsabilidades (> 10 métodos), bloques de código duplicados entre archivos,
  comparaciones `== None` y patrones obsoletos (`.has_key()`, Python 2).
- 🔵 **Prioridad baja**: nombres poco descriptivos (`d`, `tmp`...) con una
  sugerencia concreta de renombrado.

Cada sugerencia incluye descripción, ubicación `archivo:línea`, solución
propuesta y prioridad. Los umbrales se personalizan en
`~/.snapcontext/config.json`:

```json
{ "asesor": { "funcion_larga": 30, "clase_metodos": 15, "duplicado_lineas": 8 } }
```

El asesor también está disponible:

- **En el chat**: comando `/asesor` (o `/sugerir`) dentro de `--chat`.
- **En el planificador**: paso `{"accion": "asesor", "descripcion": "..."}` —
  cada sugerencia se presenta para aceptarla o rechazarla individualmente.
- **En la web**: acción *Asesor* que muestra las sugerencias en un panel.

### 🛡️ Seguridad y rendimiento en el asesor (v4.2.0)

Con el nuevo flag `--asesor-profundo`, el asesor añade dos análisis extra a
las heurísticas básicas de siempre:

```bash
snapcontext --asesor-profundo          # heurísticas + seguridad 🔒 + rendimiento ⚡
snapcontext --asesor                   # solo heurísticas básicas (como v3.5.0)
```

**🔒 Vulnerabilidades detectadas** (heurísticas propias, sin dependencias
externas como bandit):

| Vulnerabilidad | Ejemplo |
|---|---|
| Inyección SQL | `"SELECT ... WHERE id = " + user_id` |
| Command injection | `os.system("ping " + ip)`, `subprocess(..., shell=True)` |
| Path traversal | `open("../" + filename)` |
| Hardcoded secrets | `API_KEY = "sk-..."` en el código |
| eval/exec inseguros | `eval(user_input)` |
| XSS | `elemento.innerHTML = entrada` |

**⚡ Rendimiento**: bucles anidados O(n²), `range(len(...))`, concatenación de
cadenas con `+=` dentro de bucles, consultas N+1 del ORM y lectura completa de
archivos grandes en memoria.

Cada hallazgo incluye descripción, `archivo:línea`, solución propuesta y
prioridad, integrado con el resto de sugerencias del asesor.

También disponibles por separado:

```bash
# En el chat
snapcontext --chat     # luego: /seguridad  ·  /rendimiento

# En el planificador
{"accion": "seguridad", "descripcion": "auditar el módulo de pagos"}
{"accion": "rendimiento", "descripcion": "optimizar consultas"}
```

### 🔌 API pública (v3.6.0)

SnapContext expone una **API REST** para interactuar de forma programática
(desde scripts, CI/CD u otros sistemas). Requiere las dependencias web:

```bash
pip install snapcontext[web]

# Arrancar la API en http://127.0.0.1:8001 (docs en /docs y /redoc)
snapcontext --api

# Opciones de configuración
snapcontext --api --api-puerto 9000 --api-host 0.0.0.0 --api-token mi-clave

# Generar (y guardar) una API key segura sin arrancar el servidor
snapcontext --api-generate-key
```

**Autenticación**: todos los endpoints exigen la API key en el header
`X-API-Key` (o como query param `api_key`), salvo `/health`, `/docs` y
`/redoc`. Si no hay clave configurada, al arrancar `--api` se genera una
automáticamente y se guarda en `~/.snapcontext/config.json` (`"api_key"`).

| Endpoint | Método | Descripción |
|---|---|---|
| `/api/v1/health` | GET | Estado del servidor (público). |
| `/api/v1/query` | POST | Consulta asíncrona → `202` + `task_id`. |
| `/api/v1/plan` | POST | Plan asíncrono → `202` + `task_id`. |
| `/api/v1/chat` | POST | Mensaje al proveedor de IA (síncrono). |
| `/api/v1/skills` | GET | Skills aprendidas (`?archivados=true`). |
| `/api/v1/daemon` | POST | Daemon: `{"accion": "estado"\|"iniciar"\|"detener"}`. |
| `/api/v1/tasks/{task_id}` | GET | Estado de una tarea asíncrona. |

**Ejemplo de uso:**

```bash
CLAVE=$(python -c "import json;print(json.load(open('~/.snapcontext/config.json'))['api_key'])")

# Lanzar una consulta (no bloquea)
curl -X POST http://127.0.0.1:8001/api/v1/query \
     -H "X-API-Key: $CLAVE" -H "Content-Type: application/json" \
     -d '{"consulta": "revisar el login", "directorio": "."}'
# → {"task_id": "...", "estado": "pendiente", "url": "/api/v1/tasks/..."}

# Consultar el estado hasta que esté completada
curl -H "X-API-Key: $CLAVE" http://127.0.0.1:8001/api/v1/tasks/<task_id>

# Chat síncrono con contexto conversacional
curl -X POST http://127.0.0.1:8001/api/v1/chat \
     -H "X-API-Key: $CLAVE" -H "Content-Type: application/json" \
     -d '{"mensaje": "¿qué hace este proyecto?"}'
```

### 🧩 Ecosistema de plugins (v4.0.0)

La comunidad puede extender SnapContext con **plugins**: carpetas en
`~/.snapcontext/plugins/<nombre>/` con un `plugin.json` y sus scripts.
Las herramientas que exponen se integran automáticamente en el sistema MCP.

**Estructura de un plugin:**

```
~/.snapcontext/plugins/saludos/
├── plugin.json     # metadatos + herramientas + permisos
├── saluda.py       # script(s): args JSON por stdin → JSON por stdout
└── README.md
```

```json
{
  "nombre": "saludos", "version": "1.0.0", "autor": "tu-usuario",
  "descripcion": "Herramientas de ejemplo",
  "permisos": ["archivos"], "habilitado": true,
  "herramientas": [
    { "nombre": "saludos_hola", "descripcion": "Saluda",
      "script": "saluda.py", "requiere_permiso": false,
      "parametros": {"nombre": "str"} }
  ]
}
```

**Gestión desde la CLI:**

```bash
snapcontext plugin list                    # listar plugins y herramientas
snapcontext plugin create mi-plugin        # generar estructura básica
snapcontext plugin install usuario/repo    # instalar desde GitHub
snapcontext plugin install ./mi-plugin     # instalar desde carpeta local
snapcontext plugin disable mi-plugin       # deshabilitar sin borrar
snapcontext plugin enable mi-plugin
snapcontext plugin update mi-plugin        # reinstalar desde su origen
snapcontext plugin remove mi-plugin        # desinstalar
```

**Seguridad**: antes de instalar una fuente externa se pide confirmación,
mostrando autor, versión y **permisos declarados** (`archivos`, `red`,
`red_escrita`, `ejecucion`, `entorno`). Las herramientas se ejecutan por
subproceso con timeout de 120 s. Un sandbox opcional con Docker está previsto
para versiones futuras.

**En el chat**: `/plugin` lista los plugins; para ejecutar una herramienta:
`/plugin saludos.saludos_hola '{"nombre": "Ada"}'`.

**En la web**: acciones `plugins`, `plugin_install` y `plugin_remove`
emiten el evento `{"tipo": "plugins", ...}` para el panel correspondiente.

**Publicar un plugin en el repositorio de comunidad**: crea un repositorio
público en GitHub cuyo contenido sea la carpeta del plugin (con `plugin.json`
en la raíz, rama `main`) y añádelo al índice del repo
[NicolasBruna24/snapcontext-plugins](https://github.com/NicolasBruna24/snapcontext-plugins).
A partir de ahí cualquiera podrá hacer `snapcontext plugin install <nombre>`.
Sin plugins instalados, SnapContext funciona exactamente igual que siempre.

## 📦 Marketplace MCP (v6.22.0)

Capa de ecosistema sobre el sistema de plugins: un **repositorio central**
(`index.json` en [NicolasBruna24/snapcontext-plugins](https://github.com/NicolasBruna24/snapcontext-plugins))
permite **buscar e instalar plugins por nombre**, con caché local de 1 hora
(`~/.snapcontext/marketplace_cache.json`) y degradación a caché caducada si no
hay red (el marketplace nunca rompe el flujo).

```bash
snapcontext plugin search slack          # buscar en el marketplace
snapcontext plugin install mi-plugin     # instalar por nombre (resuelve contra el índice)
snapcontext plugin install https://github.com/usuario/repo   # o por URL/ruta
snapcontext plugin list                  # listar + estado (habilitado/deshabilitado)
snapcontext plugin enable|disable mi-plugin
snapcontext plugin update mi-plugin      # o `update` a secas para todos
snapcontext plugin uninstall mi-plugin   # alias de `remove`
```

**Formato del manifest** (`plugin.json`): `name`, `version`, `description`,
`author`, `repository`, `tools` (nombre, descripción, `input` con tipos),
`dependencies` (se instalan con `pip install --user`; si fallan, el plugin se
deshabilita por seguridad) y `scripts` opcionales.

**Seguridad y rendimiento**: las dependencias se instalan en modo usuario, la
carga de plugins es perezosa e idempotente (no duplica herramientas ya
registradas) y sin plugins instalados el comportamiento es idéntico al de
siempre. El índice puede apuntarse a un catálogo privado con la variable de
entorno `SNAPCONTEXT_MARKETPLACE_INDEX`.


## 🔗 Hooks / lifecycle events (v6.22.0)

Sistema de **eventos del ciclo de vida** que permite a los plugins y scripts
personalizados interceptar y modificar el comportamiento del agente en puntos
clave de la ejecución, sin tocar el núcleo de SnapContext.

**Eventos disponibles**:

| Evento | Cuándo se dispara | Recibe |
|---|---|---|
| `before_tool_use` | Antes de ejecutar una herramienta MCP | nombre, argumentos |
| `after_tool_use` | Después de ejecutar una herramienta MCP | nombre, args, resultado |
| `before_plan_step` | Antes de un paso del planificador | paso |
| `after_plan_step` | Después de un paso del planificador | paso, resultado |
| `session_start` | Al iniciar una sesión (ReAct/planificador) | consulta, modo |
| `session_end` | Al finalizar una sesión | resultado, éxito |
| `before_react_iteration` | Antes de cada iteración ReAct | consulta, estado |
| `after_react_iteration` | Después de cada iteración ReAct | consulta, estado, resultado |

Los hooks se registran desde plugins (sección `hooks` del `plugin.json`) o
colocando scripts en `~/.snapcontext/hooks/` con la convención
`<evento>[__<prioridad>].py` (ej: `before_tool_use.py`, `after_plan_step__10.py`).

```json
{
  "name": "mi-plugin",
  "hooks": {
    "before_tool_use": "scripts/before_tool.py",
    "after_plan_step": "scripts/after_plan.py",
    "session_start": "scripts/session_start.sh"
  }
}
```

Los scripts Python deben exportar `ejecutar(contexto)`; los shell reciben el
contexto por stdin (JSON) y pueden responder con JSON. Un hook puede devolver
`{"abort": True, "razon": "..."}` para detener la ejecución.

```bash
snapcontext --hook-list          # lista hooks registrados y sale
snapcontext --no-hooks           # desactiva todos los hooks (por defecto: activos)
```

**Seguridad**: los hooks de shell se ejecutan con confirmación del usuario en
modo interactivo; los de plugins respetan `permisos.json`. Sin hooks
registrados, el comportamiento es idéntico al de siempre.


## 🧭 Modos y alias

| Modo | Comando |
|------|---------|
| Tarea | `snapcontext "<consulta>"` (+ `--test-loop`) |
| Editor propio | `--editor propio` (por defecto desde v4.1.0, con backups automáticos) |
| Aider (opcional) | `--editor aider` (flujo clásico con Aider) |
| Prompts para modelos pequeños | `--modelo-ligero` (automático con Ollama) |
| Vista previa / revisión | `--vista-previa` · alias `review` |
| Chat interactivo | `--chat` |
| Planificador | `--plan "<tarea>"` |
| Autónomo | `--plan "<tarea>" --auto` |
| Demo / Web | `--demo` · `--web` (alias `interactive`) |
| Memoria e historial | `--init-claude` · `--historial` / `--historial-limpiar` |

Alias: `fix` (= `--test-loop`) · `review` · `server` (= `--server-loop`).

## 🤖 Modo autónomo (v0.17.0)

Añade `--auto` al planificador para ejecución sin supervisión:

```bash
snapcontext --plan "actualizar dependencias y pasar tests" --auto --no-confirmar
snapcontext --plan "refactorizar el módulo de pagos" --auto   # con permisos guardados
```

- Salta la confirmación del plan y el menú paso a paso.
- Cada paso fallido se **reintenta automáticamente hasta 3 veces**; si sigue
  fallando, continúa con el siguiente y lo refleja en el resumen final.
- **Sigue respetando `~/.snapcontext/permisos.json`**: los tipos marcados como
  "nunca" se deniegan sin preguntar. Con `--no-confirmar` no hay diferencia
  adicional (todas las confirmaciones ya están desactivadas).

## 🧩 Extensión VS Code (v0.16.0)

SnapContext se integra en VS Code como extensión nativa (`vscode/`), reutilizando la interfaz web y el orquestador existentes.

**Instalación** (requiere Node.js y `snapcontext` instalado en el Python del sistema):

```powershell
./vscode/scripts/empaquetar.ps1          # genera el .vsix
code --install-extension vscode/snapcontext-vscode-1.0.0.vsix
```

**Comandos** (paleta de comandos, prefijo `SnapContext:`):

| Comando | Descripción |
|---------|-------------|
| *Abrir chat* | Arranca el servidor web y muestra la interfaz en una webview |
| *Ejecutar consulta* | Pide la consulta y la ejecuta con el workspace abierto |
| *Planificar* | Ejecuta `--plan` mostrando el progreso en el canal de salida |
| *Configurar API key* | Guarda la clave en los settings del workspace |
| *Añadir al contexto* | Clic derecho en archivos del explorador → contexto visual |

**Configuración** (`settings.json`): `snapcontext.pythonPath`, `snapcontext.provider`, `snapcontext.apiKey`, `snapcontext.confirmar`.

Los logs del orquestador aparecen en tiempo real en el canal de salida **"SnapContext Output"**, con el workspace abierto como directorio del proyecto. La webview del chat reutiliza `web/static/index.html` (copia en `vscode/webview/`) servida por `servidor_webview.py`.

## 📄 Memoria de proyecto (v0.15.0)

SnapContext usa un archivo **`CLAUDE.md`** (o `SNAPCONTEXT.md`) en la raíz del
proyecto como memoria persistente: objetivo, tecnologías, estructura,
convenciones y comandos útiles. Se carga automáticamente al inicio de
cualquier modo y se inyecta como contexto del agente (chat, planificador).

Genera la memoria inicial con:

```bash
snapcontext --init-claude     # escanea el proyecto y la redacta con IA
```

Sin conexión o sin API key, genera una plantilla básica offline que puedes
completar a mano. Tras tareas/planes exitosos, SnapContext propone (con tu
confirmación) actualizar la memoria con lo aprendido.

En el chat: `/claude` muestra el contenido; `/context` muestra memoria +
archivos en contexto.

## 🛠 Herramientas MCP (v0.14.0)

El agente puede usar herramientas externas con resultados estructurados:

| Herramienta | Descripción | Permiso |
|-------------|-------------|---------|
| `grep` | Buscar patrón en el código (ripgrep `rg` preferente — ultrarrápido y respeta `.gitignore`; fallback a grep/findstr) | no |
| `read_file` | Leer archivo completo o rango de líneas | no |
| `list_files` | Listar archivos con filtro de extensión | no |
| `ast` | Extraer imports/clases/funciones de un `.py` | no |
| `ast_avanzado` (v1.4.0) | Análisis sintáctico multi-lenguaje con tree-sitter: funciones, clases, imports y llamadas. Fallback a `ast` (solo Python). Extra opcional: `pip install snapcontext[mcp_avanzado]` | no |
| `semantic_search` (v1.4.0) | Búsqueda semántica por embeddings integrada en MCP; el agente la usa automáticamente como contexto. Requiere `pip install snapcontext[embeddings]` | no |
| `git_status` | Rama actual y cambios sin commitear | no |
| `git_diff` | Diff sin commitear | no |
| `execute_command` | Ejecutar cualquier comando shell | **sí** |

En el chat:

```text
/tools                          # listar herramientas disponibles
/tool grep login                # forzar una búsqueda
/tool read_file lib/login.dart  # leer un archivo
/tool execute_command flutter test   # pide confirmación (🔒)
```

Además, mensajes como *"busca donde se usa checkout"* o *"¿cuál es el estado de
git?"* hacen que el agente ejecute automáticamente las herramientas de lectura
pertinentes y use su salida como contexto para responder. Puedes definir tus
propias herramientas en `~/.snapcontext/mcp_tools.json`:

```json
{"tools": [{"nombre": "build", "descripcion": "Compilar el proyecto",
            "comando": "npm run build", "requiere_permiso": true}]}
```

El planificador (`--plan`) también usa estas herramientas de solo lectura para
explorar el proyecto antes de proponer pasos.

## 🔒 Permisos y confirmaciones (v0.13.0)

Por defecto SnapContext **pide permiso** antes de acciones sensibles:

- Pasos del planificador (`--plan`): editar, ejecutar y consultar.
- Comandos `/run` y `/edit` del chat.

Pregunta `¿Permitir esta acción? (s/n/t/a)` con estas opciones:

| Tecla | Efecto |
|-------|--------|
| `s`   | Permitir solo esta vez |
| `n`   | Saltar esta acción |
| `t`   | Permitir **todas** las acciones de este tipo (se recuerda) |
| `a`   | No permitir ninguna de este tipo (se recuerda) |

Las preferencias se guardan en `~/.snapcontext/permisos.json`. Para volver a
que pregunte, borra ese archivo o usa `--init`. Para modo automático (CI,
scripts) desactiva todas las preguntas con `--no-confirmar`:

```bash
snapcontext --plan "tarea" --no-confirmar
```

## 🗺️ Planificador de tareas (v0.12.0)

`snapcontext --plan "añadir login con Google"` convierte SnapContext en un agente:

1. El proveedor de IA descompone la tarea en pasos JSON:
   ```json
   {"descripcion": "...", "accion": "editar|ejecutar|consultar|mcp",
    "archivos": ["..."], "comando": "...",
    "dependencias": [1, 2],
    "condicion": "archivo_existe('src/main.py')"}
   ```
2. Los pasos se muestran numerados y se pide confirmación.
3. Cada paso se ejecuta secuencialmente: **editar** usa el pipeline completo
   (`_planificar` + `_bucle_test`), **ejecutar** lanza comandos shell,
   **consultar** pregunta al proveedor y **mcp** (v2.3.0) ejecuta una
   herramienta MCP guardando su resultado en el contexto del plan.
4. Tras cada paso eliges: continuar / reintentar / saltar / abortar. Al final
   hay un resumen que se guarda en `historial.json`.

### Dependencias, condiciones y paralelismo (v1.4.0)

- **Dependencias**: un paso con `"dependencias": [1, 3]` solo se ejecuta si los
  pasos 1 y 3 tuvieron éxito. Si alguno falló o se saltó, el paso queda como
  `saltado`.
- **Condiciones**: campo `"condicion"` con una de estas funciones:
  - `archivo_existe('src/main.py')`
  - `archivo_contiene('src/main.py', 'def main')`
  - `comando_exito('flutter test')`
  - `variable_existe('mi_variable')` *(v2.3.0)*

  Además, desde v2.3.0 acepta **comparaciones dinámicas** con resultados
  de pasos previos o variables dejadas en el contexto (por pasos `mcp`):

  ```json
  {"condicion": "pasos[0].resultado == 'ok'"}
  {"condicion": "resultados.mi_variable != ''"}
  ```

  Si la condición es falsa, el paso se salta (con aviso) y el plan continúa.
### Pasos MCP y contexto dinámico (v2.3.0)

Un paso con `"accion": "mcp"` ejecuta una herramienta MCP del registro
(`grep`, `read_file`, `list_files`, `ast`, `git_status`, `git_diff`,
`execute_command`, ...) usando los campos `"herramienta"` y `"args"`.
El resultado queda disponible para los pasos posteriores:

```json
 {"descripcion": "buscar referencias", "accion": "mcp",
  "herramienta": "grep", "args": {"patron": "def main"},
  "variable": "coincidencias"}
 {"descripcion": "usar resultado", "accion": "ejecutar",
  "comando": "echo {{coincidencias}}"}
```

- El resultado siempre se guarda también como `{{resultado}}`; si el paso
  define `"variable"`, se guarda bajo ese nombre.
- `execute_command` soporta `background=true` (devuelve un `pid`
  consultable con `execute_command_status`) y `capture_output=false` para
  mostrar la salida en tiempo real.

## 🧠 Aprendizaje autónomo: memoria SQLite, skills y curador (v3.0.0)

SnapContext ya no solo ejecuta tareas: **aprende de ellas**. Toda la
memoria avanzada vive en una base de datos SQLite (`~/.snapcontext/memoria.db`; sin dependencias externas: `sqlite3` viene con Python).

### Skills: procedimientos reutilizables

- Al terminar una tarea con `--plan` **con éxito**, SnapContext extrae
  los pasos clave y guarda un *skill* (procedimiento reutilizable) en
  la tabla `skills`, junto a su contexto (archivos, comandos) y
  metadatos (fecha, usos, fallos, confiabilidad).
- Cuando repites una tarea similar, busca el skill más parecido con
  similitud semántica (embeddings; fallback Jaccard/contención sin
  dependencias) y lo reutiliza como punto de partida.
- Si el skill falla, se penaliza y queda marcado para revisión; si
  tiene éxito repetidamente (3+ usos sin fallos) se marca como
  **confiable** y se prioriza.

Comandos útiles:

```bash
snapcontext --skills          # lista los skills aprendidos
snapcontext --curador         # ejecuta una pasada del curador
snapcontext --daemon          # daemon: curador + cola de skills
snapcontext --daemon-intervalo 24   # curador cada 24 h
snapcontext --plan "..." --sin-aprendizaje  # desactiva el aprendizaje
```

Ejemplo de aprendizaje:

```bash
snapcontext --plan "migrar pagos a stripe" --auto --no-confirmar
# → al terminar (código 0): '[aprendizaje] Nuevo skill guardado:
#    migrar-pagos-a-stripe'
snapcontext --plan "migrar pagos a stripe ya" --auto --no-confirmar
# → reutiliza y refuerza el skill existente (sube su confiabilidad)
```

### Curador autónomo

- Archiva skills sin uso durante más de 30 días.
- Fusiona skills casi idénticos (similitud ≥ 0.90), conservando el más
  usado y sumando sus estadísticas.
- Notifica por CLI los skills con baja confiabilidad (< 0.4).
- Se ejecuta manualmente (`--curador`) o periódicamente vía daemon.

### Daemon (`--daemon`)

Proceso en segundo plano que cada minuto sondea la memoria: ejecuta el
curador cuando vence el intervalo configurado (`--daemon-intervalo`,
168 h por defecto = semanal) y procesa la cola de skills pendientes
(tabla `cola`) para ejecutarlos sin intervención. La CLI y el chat
encolan skills con `_cola_encolar()`; el agente `AgenteAprendizaje`
(en `agentes.py`) expone toda esta memoria al orquestador.

- **Paralelismo**: con `--paralelo N` (junto a `--auto`) se ejecutan hasta N
  pasos independientes a la vez; los logs llevan el identificador `[paso N]`. Desde v2.3.0 el planificador también respeta las dependencias dinámicas: un paso cuya condición usa una variable que otro paso aún no ha producido espera a que esté disponible:

  ```bash
  snapcontext --plan "tarea grande" --auto --paralelo 3 --no-confirmar
  ```

Opciones git:

```bash
snapcontext --plan "migrar a null-safety" --branch fix/null-safety   # rama nueva
snapcontext --plan "..." --no-git-commit                             # sin commits por paso
snapcontext --plan "..."                                             # commits 'paso: ...' activados
```

## 🤖 Curador Proactivo (v5.0.0)

A partir de v5.0.0, el curador de skills no solo **limpia**: también
**refactoriza de forma autónoma** (estilo Hermes). Un motor en segundo plano
evalúa la calidad de cada skill y mejora los prompts sin intervención humana.

### Cómo funciona

1. **Métricas**: cada ejecución de un skill registra éxito/fallo, tokens
   consumidos y tiempo (medias móviles) en SQLite (`exitos`, `fallos`,
   `tokens_promedio`, `tiempo_promedio_ms`, `ultimo_uso`, `version`).
2. **Evaluación**: un skill es *candidato* si tiene ≥ 3 usos y su tasa de
   fallos supera el 20 % o su consumo promedio de tokens supera el umbral.
3. **Refactorización**: se pide al LLM (tu proveedor por defecto) que reescriba
   el prompt para que sea más claro, conciso y robusto.
4. **Prueba en sandbox**: el candidato se valida con la suite de pruebas
   ejecutada dentro del sandbox Docker de v4.3.0 (`--sandbox`).
5. **Adopción**: solo si pasa las pruebas Y reduce tokens se guarda la nueva
   versión; la anterior queda archivada en la tabla `historial_skills`.
   **Nunca se toca el código del usuario**, solo los prompts de los skills.

### Control manual

```bash
snapcontext curador estado       # métricas: usos, fallos, tokens, candidatos
snapcontext curador ejecutar     # pasada manual del motor
snapcontext curador desactivar   # apagar el curador proactivo (persistente)
snapcontext curador activar      # reactivarlo
```

### Daemon automático

Un hilo demonio ejecuta una pasada cada `CURADOR_INTERVALO_HORAS` horas
(6 por defecto). No bloquea nunca el CLI y se puede desactivar con
`CURADOR_DAEMON=0`. En modo interactivo pregunta antes de aplicar cambios;
en `--auto` aplica directamente.

## 🧠 Modo ReAct — Razonamiento Dinámico (default desde v5.2.0)

A partir de **v5.2.0, ReAct (razonamiento dinámico) es el modo por defecto**
para cualquier consulta. El planificador estático se mantiene como **`--plan`
modo legacy** para scripts/integraciones que lo requieran.

```bash
snapcontext "arregla el login de Google y pasa los tests"          # ← ReAct
snapcontext "refactoriza modulo.py" --auto --react-max-iter 25    # ← ReAct
snapcontext --plan "migrar a pytest"   # ← planificador estático (legacy)
```

> `snapcontext "tarea"` **sin flags** ejecuta ReAct por defecto. Usa `--plan`
> para el planificador estático (ver `tests/test_react_510.py` →
> `test_sin_flags_ejecuta_react`).

A diferencia del planificador `--plan` —que genera una lista estática de pasos
en JSON y los ejecuta en orden—, el motor **ReAct** es dinámico: tras **cada
acción** el agente **observa** el resultado real (stdout, código de retorno,
diff…) y **decide** el siguiente paso, adaptándose en tiempo real.

### Cómo funciona

1. El agente envía al LLM un prompt de sistema con las herramientas y la tarea.
2. El LLM responde con **JSON estricto**: `{"pensamiento", "accion",
   "argumentos"}`. Si el JSON no es válido, se reintenta con un prompt
   correctivo hasta 3 veces.
3. La acción se ejecuta con una herramienta real y su salida se convierte en
   una observación legible que vuelve al historial.
4. Cuando la tarea está lista, el agente emite `"accion": "finalizar"` con un
   resumen (código de salida 0).

### Herramientas

| Acción | Descripción |
|---|---|
| `editar_archivo(ruta, contenido)` | Editor propio (con copia de seguridad); devuelve el diff aplicado |
| `ejecutar_pruebas(archivo?)` | Comando de pruebas (auto-detectado o `SNAPCONTEXT_COMANDO_TEST`) |
| `buscar_codigo(patron)` | Búsqueda regex sobre archivos de texto |
| `ejecutar_comando(comando)` | Shell; **respeta `--sandbox`** automáticamente |
| `leer_archivo(ruta)` | Lectura truncada a 8 KB |

### Seguridad y control

- Las rutas se validan: siempre dentro del proyecto (nada de `../../etc/...`).
- `--sandbox`: todos los comandos de shell corren en el contenedor Docker.
- Máx. 15 iteraciones (`--react-max-iter N`) para evitar bucles infinitos.
- En modo interactivo pregunta antes de cada acción (continuar/abortar/saltar);
  con `--auto` aplica todo sin preguntar.

### Gestión de contexto

Si el historial crece demasiado (> ~8000 tokens estimados, configurable con
`REACT_UMBRAL_RESUMEN_TOKENS`), el agente pide al LLM que **resuma** la
conversación y continúa desde ese resumen, manteniendo el coste bajo control.


> ### 📜 Modo legacy `--plan` (planificador estático)
> `snapcontext --plan "tarea"` descompone la tarea en pasos JSON con IA
> y los ejecuta secuencialmente (continuar/reintentar/saltar). Útil cuando
> necesitas un orden de ejecución predefinido o mantener compatibilidad con
> scripts.

## ✨ Novedades v0.10.0
### Notificaciones

Si tienes configurado Telegram (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`) o
Discord (`DISCORD_WEBHOOK_URL`), recibirás:
*"🔧 Skill 'refactorizar_api' mejorado (v2). Tokens reducidos un 30 %."*


- **Claude (Anthropic) como proveedor de IA**: `snapcontext "..." --provider anthropic`
  (requiere `ANTHROPIC_API_KEY` y `pip install snapcontext[anthropic]`). Modelo por
  defecto: `claude-3-5-sonnet-20241022`.
- **Modo chat interactivo**: `snapcontext --chat` abre un REPL con los comandos
  `/salir`, `/archivos`, `/limpiar`, `/seleccion <consulta>`, `/asesor`,
  `/plugin`,
  `/provider <proveedor>`, `/historial` y `/ayuda`. Cualquier otro texto se envía
  al proveedor actual manteniendo la conversación.
- **Memoria persistente**: cada tarea se guarda en `~/.snapcontext/historial.json`
  (fecha, consulta, archivos, resultado y duración). Consulta con
  `snapcontext --historial` y borra con `snapcontext --historial-limpiar`.
- **Base para el agente autónomo**: nuevas utilidades `_leer_archivo(ruta)` y
  `_ejecutar_comando(comando, directorio)` disponibles para el chat y futuros
  planificadores.
- **Chat como REPL de comandos de agente**: desde `--chat` puedes ejecutar
  `/run <comando>` (shell), `/read <archivo>`, `/explore <tema>` (búsqueda con
  rg/grep/findstr), `/fix | /review | /server <mensaje>` (alias del pipeline),
  `/edit <archivo>` (VSCode/nano/notepad/$EDITOR), `/context` (contexto actual),
  `/search <consulta>` (búsqueda semántica de archivos), `/buscar <consulta>`
  (alias de /search), `/grafo` (grafo de dependencias en ASCII),
  `/dependencias <archivo>` (imports y dependencias inversas), `/save` (guarda la
  sesión en historial.json) además de los comandos previos.
  Los comandos largos (`/run`, `/explore`, `/fix`, `/review`, `/server`) se
  ejecutan en un hilo separado para no bloquear el chat.

## 📚 Casos de uso

### Flutter

```bash
snapcontext "el botón de pago no actualiza el total" --test-loop
snapcontext fix "el widget de login no muestra errores"
snapcontext review "revisa la navegación del carrito"
```

Detecta `pubspec.yaml`, escanea `lib/` y `test/`, y ejecuta `flutter test` en
bucle hasta que pasen.

### React / Node

```bash
snapcontext --provider anthropic "el formulario no valida el email"
snapcontext plan "migrar componentes a hooks" --branch refactor/hooks --auto
```

Detecta `package.json`, escanea `src/`, y respeta las convenciones anotadas en
tu `CLAUDE.md`.

### Python

```bash
snapcontext "añade tests para el módulo de pagos"
snapcontext --init-claude      # genera la memoria del proyecto
snapcontext --chat             # /tool ast pagos.py · /tool git_status
```

Detecta `pyproject.toml` / `requirements.txt` y usa `pytest` como test por
defecto.

### CI / automatización

```bash
snapcontext --plan "corregir tests rotos" --local --no-confirmar --auto
```

Sin claves (`--local` usa heurística local) y sin preguntas interactivas.

## 🤝 Contribuciones

Las contribuciones son bienvenidas. Si tienes una idea, abre un issue o envía un pull request.

1. Haz un fork del proyecto.
2. Crea tu rama de características (`git checkout -b feature/nueva-funcionalidad`).
3. Haz commit de tus cambios (`git commit -m 'Añadir nueva funcionalidad'`).
4. Haz push a la rama (`git push origin feature/nueva-funcionalidad`).
5. Abre un Pull Request.
   
Combina lo mejor de dos mundos:

- **Aider**: eficiencia, control y una integración Git impecable.
- **Gestión automática de contexto** (estilo Claude Code): no hace falta
  decirle `/add archivo` — SnapContext averigua los archivos por ti.

Le pasas una tarea en lenguaje natural y SnapContext:

1. **Escanea** el repositorio (por defecto las carpetas `lib/` y `supabase/`)
   y encuentra los archivos más relacionados con tu consulta.
2. **El proveedor de IA** (Gemini, Ollama local, DeepSeek o Groq) selecciona
   los archivos más relevantes.
3. **Aider** recibe esos archivos y la consulta original, y hace los cambios
   en el código (con commits automáticos en Git).

```
$ snapcontext "el botón de pago no funciona"
ℹ Repositorio: C:\...\marketplace-productos-locales
ℹ Escaneando el repositorio para encontrar candidatos...
ℹ 24 candidato(s) relevante(s) localmente.
ℹ Seleccionando con Gemini (gemini-2.5-flash)...

✔ Archivos seleccionados (3):
   • lib/pages/pago/pago_page.dart
   • lib/models/pedido.dart
   • supabase/functions/procesar-pago-mp/index.ts

ℹ Ejecutando Aider...
✔ Aider terminó correctamente.
```

---

## Novedades (v0.9.0)

- **Modo demo (`--demo`)**: muestra el valor de SnapContext en ~1 minuto, sin
  necesidad de API key ni Aider. Crea un proyecto de prueba temporal, muestra
  la selección de archivos (`--vista-previa --local`) y ejecuta el bucle de
  pruebas completo con un editor de demostración offline.

---

## Novedades (v0.8.0)

- **Auto-detección del tipo de proyecto**: ajusta carpetas y extensiones por
  defecto según el proyecto detectado (Flutter, Node, Python, Go, Rust, Kotlin,
  Swift) de forma transparente.
- **Alias / atajos de comandos**: `fix`, `review`, `server` e `interactive`.
- **Interfaz web en tiempo real**: spinner/barra de progreso, cronómetro,
  contadores de archivos escaneados/seleccionados y nuevos eventos coloreados.

---

## Novedades (v0.6.0)

Qué hay de nuevo en esta versión:

- **Corrección crítica en `--init`**: ahora usa correctamente `questionary.password()` para ingresar claves API con Groq y DeepSeek, solucionando un bug anterior que impedía la configuración de estos proveedores.
- **Manejo mejorado de errores de configuración**: si `~/.snapcontext/config.json` está corrupto o no existe, se muestra un aviso claro en lugar de fallar silenciosamente.
- **Actualización a versión 0.6.0**: todos los metadatos (pyproject.toml, README.md) reflejan la nueva versión.
- **Mejoras de instalación**: scripts de Linux y Windows actualizados con mensajes claros y fallbacks correctos a `pip` si `uv` no está disponible.

Qué hay de nuevo en esta versión:

- **Validación de carpeta de proyecto**: comprueba al iniciar que existe una
  carpeta típica (`lib/`, `src/`, `supabase/`, `app/`, `packages/`, `backend/`)
  y avisa si no (sale con código 1).
- **Menú interactivo de proveedor** (`questionary` con flechas y Enter):
  elige Gemini, Ollama, DeepSeek o Groq sin escribir `--provider`.
- **Configuración persistente**: guarda tu proveedor/modelo favorito en
  `~/.snapcontext/config.json` y lo reutiliza en las siguientes ejecuciones.
- **Auto-detección de modelos de Ollama**: con `ollama list`, muestra los
  modelos locales disponibles para elegir.
- **Asistente de configuración inicial (`--init`)**: guía el alta de claves API,
  proveedor y modelo favorito, y una prueba opcional de conexión.

---

## 📦 Instalación con el instalador .exe (Windows, sin Python) — v1.5.0

Desde la **v1.5.0**, en Windows puedes instalar SnapContext **sin necesidad de
Python** con el instalador gráfico:

1. Descarga **`SnapContext-Setup-<versión>.exe`** desde
   [GitHub Releases](https://github.com/NicolasBruna24/snapcontext/releases).
2. Ejecútalo: instala en `%LOCALAPPDATA%\Programs\SnapContext`, añade la
   carpeta al **PATH del usuario** y (opcional) crea accesos directos en el
   **Menú Inicio** y el **Escritorio**.
3. Abre una terminal nueva y comprueba:

   ```powershell
   snapcontext --version
   snapcontext --demo          # demo sin API key ni Aider
   ```

El instalador incluye un **desinstalador** (Panel de control → Aplicaciones o
`Uninstall.exe` en la carpeta de instalación); tu configuración
(`~\.snapcontext`) se conserva.

> **Notas:** el `.exe` incluye las dependencias ligeras (Gemini/OpenAI,
> menú interactivo e interfaz web). Los extras pesados (embeddings con
> `sentence-transformers` y análisis con `tree-sitter`) quedan fuera para
> mantenerlo ligero; si los necesitas, instala la versión Python con
> `pip install snapcontext[embeddings,mcp_avanzado]`. Aider (modo edición)
> se instala aparte: `pip install aider-chat`.

### Regenerar el instalador (mantenedores)

Requisitos: Python 3.9+ con las dependencias del proyecto, `pip install
pyinstaller` y [NSIS](https://nsis.sourceforge.io) en el PATH.

```powershell
.\scripts\empaquetar_exe.ps1          # genera dist\SnapContext-Setup-<versión>.exe
.\scripts\empaquetar_exe.ps1 -Full    # incluye sentence-transformers + tree-sitter
```

El script ejecuta `pyinstaller snapcontext.spec`, verifica que el ejecutable
responde a `--version` y después compila `installer.nsi` con `makensis`.

---

## Instalación rápida (one-liner)

**Linux / macOS:**

```bash
# Instalar desde GitHub Pages (recomendado)
curl -LsSf https://NicolasBruna24.github.io/snapcontext/install.sh | sh

# Fallback: instalan directamente desde el repositorio raw si GitHub Pages falla
curl -LsSf https://raw.githubusercontent.com/NicolasBruna24/snapcontext/main/install.sh | sh
```

**Windows (PowerShell):**

```powershell
# Instalar desde GitHub Pages (recomendado)
powershell -ExecutionPolicy ByPass -c "irm https://NicolasBruna24.github.io/snapcontext/install.ps1 | iex"

# Fallback: instalan directamente desde el repositorio raw si GitHub Pages falla  
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/NicolasBruna24/snapcontext/main/install.ps1 | iex"
```

Los scripts detectan automáticamente tu sistema, verifican Python 3.9+, instalan `uv` (gestor rápido de paquetes Python) si no está presente, y finalmente instalan SnapContext. Al terminar:

- ✅ **Windows**: El comando `snapcontext` se añadirá automáticamente al PATH del usuario permanentemente (variable `Path`). Solo necesitas reiniciar tu terminal o ejecutar `refreshenv`.
- ✅ **Linux/macOS**: pip registra el ejecutable en el PATH del usuario de forma automática sin requerir permisos especiales.

> 📝 En Windows, si prefieres una instalación manual paso a paso o instalaste directamente con `pip install snapcontext` (sin usar el one-liner), puedes ejecutar:
> 
> ```powershell
> snapcontext --setup-path
> ```
> 
> Esto añadirá automáticamente la carpeta de SnapContext al PATH del usuario.


---

## Requisitos

| Herramienta | Para qué | Instalación |
|---|---|---|
| Python 3.9+ | ejecutar SnapContext | [python.org](https://python.org) |
| `google-generativeai` | proveedor Gemini (por defecto) | `pip install google-generativeai` |
| `openai` | DeepSeek, Groq y Ollama (API compatible OpenAI) | `pip install openai` |
| `questionary` (opcional) | menú interactivo de selección de proveedor | `pip install "snapcontext[interactive]"` |
| Clave de un proveedor | `GEMINI_API_KEY`, `DEEPSEEK_API_KEY` o `GROQ_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `aider-chat` | hacer las modificaciones | `pip install aider-chat` |
| `flutter` (opcional) | bucle de pruebas `--test-loop` | [flutter.dev](https://flutter.dev) |

> Aider usa su propia configuración de modelo y API keys (variables `AIDER_*`
> o el fichero `.env` del proyecto). Configura Aider una vez y SnapContext lo
> reutilizará.

---

## Instalación

```bash
# 1. Clonar / copiar el proyecto y entrar en él
cd SnapContext

# 2. (Recomendado) entorno virtual
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 3. Instalar en modo editable → expone el comando "snapcontext" globalmente
pip install -e .

# 3b. (Opcional) Menú interactivo para elegir proveedor con flechas ↑↓
#     (si no lo instalas, SnapContext usa Gemini por defecto con un aviso)
pip install "snapcontext[interactive]"     # o simplemente:  pip install questionary

# 4. Dependencia externa (Aider arrastra más paquetes por eso va aparte)
pip install aider-chat

# 5. Configurar la clave del proveedor que vayas a usar
#    PowerShell:
$env:GEMINI_API_KEY = "tu_clave"        # o DEEPSEEK_API_KEY / GROQ_API_KEY
#    Linux/Mac:
export GEMINI_API_KEY=tu_clave
```

---

## Uso

```bash
# Ejemplo básico: escaneo + IA + Aider
snapcontext "el botón de pago no funciona"

# Selección de proveedor: sin --provider ni --local, aparecerá un menú
# interactivo (↑↓ + Enter) para elegir entre Gemini, Ollama, DeepSeek y Groq.
# Requiere questionary (ver instalación); sin él se usa Gemini con un aviso.
snapcontext "revisar el login"

# Elegir proveedor y modelo directamente (sin menú interactivo)
snapcontext "..." --provider groq
snapcontext "..." --provider deepseek --model deepseek-reasoner
snapcontext "..." --provider ollama --model qwen2.5   # Ollama local

# Modo offline (sin IA): no muestra menú interactivo
snapcontext "..." --local

# Modo experto: revisar/añadir/eliminar archivos antes de Aider
snapcontext "revisar pago" --experto

# Si el proyecto usa carpetas distintas a lib/ y supabase/
snapcontext "arreglar login" --carpetas src migrations

# Solo ver qué archivos elegiría (sin tocar código)
snapcontext "revisar carrito" --vista-previa

# Bucle agéntico: ejecutar flutter test tras Aider y repetir si falla
snapcontext "añadir validación al formulario" --test-loop

# Cambiar el número de archivos que recibe Aider
snapcontext "agregar índice a pedidos" --max-archivos 4

# Opciones extra para Aider (modelo, etc.)
snapcontext "..." --aider-opciones "--model sonnet --no-auto-commits"

# Modo offline (sin Gemini) por si solo quieres la heurística local
snapcontext "..." --local

# Interfaz web (FastAPI + WebSockets) con logs en tiempo real
snapcontext --web              # http://localhost:8000
snapcontext --web --web-puerto 8123

# Alias / atajos para tareas frecuentes
snapcontext fix "el botón de pago no funciona"    # = --test-loop
snapcontext review "revisar el login"             # = --vista-previa --experto
snapcontext server "iniciar servidor"             # = --server-loop
snapcontext interactive                           # = --web

# Demo: valor de SnapContext en ~1 min, sin API key
snapcontext --demo
```
### Auto-detección del tipo de proyecto

SnapContext detecta automáticamente el tipo de proyecto buscando archivos clave
en la raíz resuelta por `--directorio` (o el directorio actual):

| Archivo clave               | Tipo       | Carpetas por defecto que escanea            |
|-----------------------------|------------|----------------------------------------------|
| `pubspec.yaml`              | `flutter`  | `lib`, `test`, `web`                         |
| `package.json`              | `node`     | `src`, `backend`, `frontend`, `lib`          |
| `requirements.txt` / `pyproject.toml` | `python` | `src`, `app`, `lib`, `tests`, `scripts` |
| `go.mod`                    | `go`       | `cmd`, `internal`, `pkg`                     |
| `Cargo.toml`                | `rust`     | `src`, `tests`                               |
| `build.gradle`              | `kotlin`   | `app/src/main/kotlin`, `app/src/test/kotlin` |
| `Podfile`                   | `swift`    | `Sources`, `Tests`                           |

Además ajusta las **extensiones** consideradas en el escaneo (por ejemplo,
`.dart` para Flutter, `.js`/`.ts`/`.jsx`/`.tsx` para Node, `.py` para Python…).

La detección es **transparente**: no imprime nada salvo que uses `--depurar`.
Si no se detecta ningún tipo, se mantiene el comportamiento actual (`lib/`,
`supabase/`, …). Si pasas `--carpetas` manualmente, tu valor tiene prioridad.

### Alias / atajos de comandos

Para tareas frecuentes existen subcomandos cortos (siempre se pueden combinar
con el resto de flags):

| Comando                          | Equivale a                                            |
|----------------------------------|-------------------------------------------------------|
| `snapcontext fix "mensaje"`      | `snapcontext "mensaje" --test-loop`                   |
| `snapcontext review "mensaje"`   | `snapcontext "mensaje" --vista-previa --experto`      |
| `snapcontext server "mensaje"`   | `snapcontext "mensaje" --server-loop`                 |
| `snapcontext interactive`        | `snapcontext --web`                                   |

Si el primer argumento no coincide con ninguno de estos alias, se trata como
consulta (comportamiento habitual).

### 🚀 Empezar un proyecto desde cero

¿Carpeta vacía? No hay problema. Desde la **v1.3.0** SnapContext puede trabajar
en carpetas nuevas o casi vacías:

```bash
mkdir mi-app && cd mi-app
snapcontext "crear la estructura inicial de un backend con FastAPI" --iniciar-proyecto
```

- `--iniciar-proyecto` (alias `--no-validar`): omite por completo la validación
  de carpeta. Ideal para empezar un proyecto desde cero. Muestra el aviso:
  `⚠️ Modo iniciar-proyecto: se omite la validación de carpeta. Asegúrate de estar en el directorio correcto.`
- `--local`: trabaja sin IA y también desactiva la validación:

  ```bash
  snapcontext "listar archivos del proyecto" --local --vista-previa
  ```

- `--directorio <ruta>` explícito: si indicas la carpeta a mano, solo se muestra
  un aviso (no se bloquea).
- Además, desde v1.3.0 la validación acepta **carpetas típicas vacías**
  (`lib/`, `src/`, `supabase/`, `app/`, `packages/`, `backend/`), **archivos de
  código vacíos** en la raíz (`main.py`, `main.dart`, `index.js`, ...) y
  **archivos de configuración vacíos** (`pubspec.yaml`, `package.json`,
  `pyproject.toml`, ...). Basta con crear uno para que SnapContext te deje
  trabajar.

SnapContext puede crear archivos desde cero: Aider escribe los ficheros nuevos
que la tarea requiera, incluso en una carpeta que antes estaba vacía.

### Validación de carpeta de proyecto

Al iniciar, SnapContext comprueba que el directorio tenga indicios de ser un
proyecto (v1.3.0): alguna carpeta típica (`lib/`, `src/`, `supabase/`, `app/`,
`packages/`, `backend/`) —aunque esté vacía—, algún archivo de código en la
raíz —aunque esté vacío— o algún archivo de configuración típico
(`pubspec.yaml`, `package.json`, `requirements.txt`, `go.mod`, `Cargo.toml`,
`setup.py`, `pyproject.toml`). Si no encuentra nada:

- Con `--iniciar-proyecto`, `--local` o `--directorio <ruta>` explícito → solo
  avisa y continúa.
- Sin ninguno de esos flags → error con sugerencias (código 1):

```
⚠️ No se detectó una carpeta de proyecto típica (lib/, src/, supabase/, etc.).
Si estás empezando un proyecto desde cero, usa --iniciar-proyecto para saltar esta validación.
O usa --local para trabajar sin IA (también desactiva la validación).
También puedes indicar la carpeta con --directorio <ruta>.
```

### Menú interactivo de proveedor (↑↓ + Enter)

Si ejecutas `snapcontext "consulta"` **sin** `--provider` ni `--local`, SnapContext
te pregunta si quieres elegir el proveedor y, si aceptas, muestra un menú con las
flechas ↑/↓ y Enter:

```text
🤖 Selecciona el proveedor de IA:
  ❯ Gemini (Google)
    Ollama (local)
    DeepSeek (API)
    Groq (API)
```

Usa la librería **questionary** (`pip install snapcontext[interactive]`). Si no
está instalada, se muestra un aviso y se usa **Gemini por defecto**. Para evitarlo,
pasa siempre `--provider` o `--local`.

### Configuración persistente

SnapContext guarda tus preferencias (proveedor favorito, modelo y claves API de
los proveedores que configures) en `~/.snapcontext/config.json`:

```json
{
  "provider": "groq",
  "model": "llama-3.3-70b-versatile",
  "prompt_caching": true,
  "api_keys": {
    "gemini": "AIza...",
    "groq": "gsk_..."
  }
}
```

- **Primera vez** (sin configuración): pregunta si quieres elegir proveedor con el
  menú interactivo y ofrece guardarlo como predeterminado.
- **Siguientes veces**: usa directamente el proveedor guardado, sin menú.
- **`--provider ...` (y opcional `--model ...`)**: usa ese proveedor y sobrescribe
  la preferencia guardada.
- **`--no-persist`**: ignora la configuración guardada y fuerza el menú interactivo
  (no guarda nada).
- También respeta la variable de entorno `SNAPCONTEXT_PROVIDER` si no hay
  configuración guardada.

**Cómo editarlo:** abre `~/.snapcontext/config.json` con cualquier editor, cambia
`provider` y `model`, y guarda. La próxima ejecución lo lee (la carpeta se crea
automáticamente con `--init` o al elegir "guardar").

### Asistente de configuración inicial (`--init`)

Para no configurar claves y proveedor a mano, existe un asistente guiado:

```bash
snapcontext --init
```

Flujo del asistente:

1. Pide la **clave de API de Gemini** (campo oculto).
2. Pregunta si quieres configurar otros proveedores (Groq, DeepSeek) y guarda
   sus claves.
3. Te deja elegir el **proveedor y modelo favorito** con el menú interactivo.
4. Guarda todo en `~/.snapcontext/config.json`.
5. (Opcional) Prueba la conexión con la API del proveedor elegido.

Si ya existe una configuración, pregunta si quieres **sobrescribirla**.
Si `questionary` no está instalada, se muestra el aviso: `pip install
questionary` (o `pip install snapcontext[interactive]`).

`--init` es independiente: no necesita consulta, ni escaneo, ni Aider; solo
configura y sale (el resto de flags sigue funcionando igual).

### Auto-detección de modelos de Ollama

Si eliges **Ollama** (en el menú o como preferencia guardada) sin pasar
`--model`, SnapContext ejecuta `ollama list` en segundo plano, listo los modelos
locales y te deja elegir con las flechas:

```text
🤖 Selecciona el modelo de Ollama:
  ❯ llama3.2
    qwen2.5
```

- Los modelos se parsean de la primera columna de la salida de `ollama list`.
- Si `ollama` no está instalado o no hay modelos, se avisa y se ofrece volver al
  menú de proveedores o usar **Gemini** por defecto.
- Si pasas `--provider ollama --model <nombre>` por CLI, se omite la detección
  y se usa directamente ese modelo.

### Opciones principales

| Opción | Por defecto | Descripción |
|---|---|---|
| `consulta` | *(opcional con `--init`)* | La tarea a resolver, entre comillas |
| `--init` | off | Asistente de configuración inicial (claves + proveedor/modelo) |
| `--directorio` | `.` | Repositorio (detecta la raíz Git automáticamente) |
| `--carpetas` | `lib supabase` | Carpetas a escanear |
| `--max-archivos` | `3` | Archivos que recibe Aider |
| `--candidatos` | `80` | Candidatos que se envían al proveedor de IA |
| `--provider` | *(sin por defecto)* | Proveedor que selecciona archivos (`gemini`, `ollama`, `deepseek`, `groq`). Si no se indica (ni `--local`) se usa el guardado en `~/.snapcontext/config.json` o un menú interactivo |
| `--no-persist` | off | Ignora la configuración guardada y fuerza el menú interactivo |
| `--model` (alias `--modelo`) | según proveedor | Modelo del proveedor (o `SNAPCONTEXT_MODELO`) |
| `--local` | off | Selección sin IA (modo offline / pruebas). También desactiva la validación de carpeta |
| `--iniciar-proyecto` (alias `--no-validar`) | off | Omite la validación de carpeta: permite trabajar en carpetas vacías al empezar un proyecto desde cero |
| `--vista-previa` | off | Mostrar la selección y salir |
| `--experto` (alias `--expert`) | off | Revisar/añadir/eliminar archivos antes de Aider |
| `--aider-opciones` | `""` | Flags extra para Aider |
| `--test-loop` | off | Bucle agéntico Aider → pruebas → reparar |
| `--server-loop` | off | Bucle agéntico con `flutter run`, modo automático (reintenta y pregunta s/n) |
| `--manual-loop` | off | Bucle agéntico con `flutter run`, modo manual (usuario decide cada paso) |
| `--max-intentos` | `3` | Intentos máximos de `--server-loop` |
| `--dispositivo` | `web-server` | Plataforma/dispositivo de `flutter run` |
| `--url-defecto` | `http://localhost:5000` | URL para abrir el navegador si Flutter no reporta una |
| `--comando-test` | *auto* | Comando del bucle de pruebas (se detecta automáticamente según el lenguaje; ej. `pytest`, `go test ./...`, `flutter test`) |
| `--max-iteraciones` | `3` | Iteraciones máximas del bucle |
| `--validar` | on | Valida la sintaxis del código antes de guardar en el editor propio |
| `--no-validar-sintaxis` | off | Desactiva la validación de sintaxis del editor propio |
| `--max-intentos-validacion` | `3` | Intentos de validación de sintaxis antes de cancelar la edición |
| `--asesor` (`--sugerir`) | off | Asesor proactivo: analiza y sugiere mejoras sin modificar código |
| `--asesor-auto` | off | Aplica automáticamente las mejoras seguras del asesor |
| `--asesor-umbral` | `20` | Líneas máximas por función para el asesor |
| `--api` (`--api-server`) | off | Arranca la API REST pública (v3.6.0) en el puerto 8001 |
| `--api-puerto` | `8001` | Puerto de la API |
| `--api-host` | `127.0.0.1` | Host de escucha de la API |
| `--api-token` | — | API key de los endpoints `/api/v1/*`; por defecto usa/genera la de config.json |
| `--api-generate-key` | off | Genera y guarda una API key segura sin arrancar el servidor |

> **Nota:** el flag negativo de validación de sintaxis se llama `--no-validar-sintaxis`
> porque `--no-validar` ya existe como alias de `--iniciar-proyecto`.

---

## Cómo funciona por dentro

```
 consulta ──▶ [1] Escaneo local        ◀── git ls-files / os.walk
                     │
                     ▼
    candidatos ordenados (heurística: ruta + contenido)
                     │
                     ▼
      [2] Proveedor IA (JSON) ─▶ 3 archivos más relevantes
      (Gemini | Ollama | DeepSeek | Groq)
                     │
                     ▼
 [3] Aider --yes --file A --file B --message "consulta"
                     │
                     ▼
 [4] (opcional) verificación: flutter test / flutter run ─ si falla, Aider arregla
                     │                                     │
                     └── pasa ─────────────── fin (aprobado)
```

**El escaneo** usa `git ls-files -c -o --exclude-standard` (respeta `.gitignore`
e incluye archivos nuevos), con caída automática a recorrer el árbol si no hay
Git. Luego puntúa cada archivo: coincidencias en la ruta (un nombre de archivo
como `pago_page.dart` pesa más que el directorio `pagos/`) y coincidencias en
las primeras líneas del contenido (con tildes normalizadas: `botón` → `boton`).

**El proveedor de IA** recibe la lista de candidatos y responde en JSON (con
validación y fallback si el modelo no devuelve rutas válidas). Gemini usa
`google.generativeai`; DeepSeek, Groq y Ollama usan la librería `openai` (sus
APIs son compatibles con la de OpenAI). El modo `--local` usa solo la
heurística local, sin llamar a ningún proveedor.

---

## Bucle agéntico (`--test-loop`)

El modo `--test-loop` implementa el paso 4 de la arquitectura:
`Aider → flutter test → si falla, Aider recibe el error real y lo arregla`,
hasta un máximo de `--max-iteraciones`.

```bash
snapcontext "arreglar el flujo de pago" --test-loop --max-iteraciones 5
```

Este es el punto natural para extender SnapContext: por ejemplo, añadir
`flutter analyze`, linters o más herramientas dentro de
`ejecutar_bucle_test()`.

---

## 🛡️ Sandboxing inteligente

Desde **v5.4.0**, SnapContext ya no necesita que actives `--sandbox` para
protegerte: analiza cada comando y activa el contenedor Docker **solo cuando
es necesario**.

### Cómo funciona

1. Antes de ejecutar cualquier comando (planificador, ReAct `ejecutar_comando`,
   bucle de pruebas, plugins, MCP), SnapContext lo evalúa con
   `sandbox_utils.es_comando_peligroso()` (regex compiladas, muy rápido).
2. Si el comando es peligroso → se ejecuta dentro del sandbox Docker
   automáticamente: `🔒 Comando potencialmente peligroso detectado.
   Ejecutando en sandbox Docker.`
3. Si es peligroso pero Docker no está disponible → se advierte y, en modo
   interactivo, se pregunta si continuar; en `--auto` se **aborta** el comando.
4. Los comandos seguros se ejecutan directamente, sin fricción.

### Patrones detectados (extensibles en `sandbox_utils.py`)

- Borrado masivo: `rm -rf /`, `rm -rf ~`, `rm -rf .`, `--no-preserve-root`.
- Discos: `dd if=/of=`, `mkfs*`, `fdisk`, `wipefs`, `parted`.
- Descarga y ejecución: `curl ... | sh`, `wget ... | bash`, `... | sudo bash`.
- Permisos: `chmod 777 /`, `chmod -R 777`, `chown -R`.
- Fork bomb `:(){ :|:& };:`, `sudo` + comando destructivo.
- Escritura en dispositivos de bloque (`> /dev/sda`; `> /dev/null` es inocuo).
- Terminación de procesos: `kill -9`, `pkill`.

### Control por el usuario

| Mecanismo | Efecto |
|---|---|
| `--sandbox` | Fuerza el contenedor para **todos** los comandos (como en v4.3.0). |
| `--no-sandbox` | Desactiva todo el sandbox, incluso ante comandos peligrosos (prioridad máxima). |
| `SNAPCONTEXT_SANDBOX=1` | Sandbox siempre activo (equivale a `--sandbox`, sin error si falta Docker). |
| `SNAPCONTEXT_SANDBOX=0` | Equivale a `--no-sandbox`. |

Prioridad: `--no-sandbox` / `SNAPCONTEXT_SANDBOX=0` > `--sandbox` >
`SNAPCONTEXT_SANDBOX=1` > detección automática.

```bash
# Modo por defecto: solo los comandos peligrosos van al contenedor
snapcontext "limpiar el proyecto"

# Opt-out total (scripts de CI, confianza total)
snapcontext "tarea" --no-sandbox

# Siempre sandbox
SNAPCONTEXT_SANDBOX=1 snapcontext "tarea"
```

## 🧪 Detección automática de pruebas

Desde **v5.3.0**, SnapContext detecta automáticamente cómo correr las pruebas
de tu proyecto: escanea el directorio raíz, identifica el lenguaje/framework y
usa el comando de test adecuado **sin que tengas que escribir nada**.

Esto hace que tanto el **agente ReAct** (herramienta `ejecutar_pruebas`) como
el planificador (**`--test-loop`**) ejecuten los tests por sí solos.

### Lenguajes / frameworks soportados

| Archivo(s) en la raíz                | Lenguaje detectado      | Comando de test             |
|--------------------------------------|-------------------------|-----------------------------|
| `go.mod`                             | Go                      | `go test ./...`             |
| `Cargo.toml` / `Cargo.lock`          | Rust                    | `cargo test`                |
| `pom.xml`                            | Java (Maven)            | `mvn test`                  |
| `build.gradle`                       | Java (Gradle)           | `gradle test`               |
| `requirements.txt`                   | Python (pytest)         | `pytest`                    |
| `pyproject.toml` (con `pytest`)      | Python (pytest)         | `pytest`                    |
| `pyproject.toml` (sin pytest) / `setup.py` | Python (unittest) | `python -m unittest discover` |
| `package.json`                       | Node (npm)              | `npm test`                  |
| `yarn.lock` (con `package.json`)     | Node (yarn)             | `yarn test`                 |
| `pubspec.yaml`                       | Flutter                 | `flutter test`              |
| `*.csproj` (en la raíz)              | .NET                    | `dotnet test`               |
| `Gemfile`                            | Ruby                    | `bundle exec rspec`         |
| `mix.exs`                            | Elixir                  | `mix test`                  |

### Cómo funciona

1. `snapcontext.py` (vía `detector_tests.py`, sin dependencias externas) mira
   los archivos clave **solo en la raíz** (rápido y ligero).
2. Si el usuario pasa `--comando-test "..."`, **siempre se usa ese** (con
   compatibilidad hacia atrás); el detector solo actúa cuando no se da un
   comando explícito.
3. Si no se detecta nada, se usa `flutter test` como último recurso (el
   comportamiento histórico). En el agente ReAct, si nada funciona se devuelve
   un error claro pidiendo que especifiques el comando manualmente.

### Uso

```bash
# Antes: había que saber y escribir el comando.
snapcontext "hacer pasar los tests" --test-loop --comando-test "pytest"

# Ahora: se detecta solo según el lenguaje del proyecto.
snapcontext "hacer pasar los tests" --test-loop
```

### Extender el detector

Para añadir un lenguaje nuevo, edita `detector_tests.py`:

- Añade una entrada en `_LENGUAJES` con su `comando` y `estructura`.
- Registra su archivo identificador en `_DETECCION_POR_ARCHIVO` (o en
  `_REGLAS_CONTENIDO` si depende del contenido).

---

## Modo experto (`--experto` / `--expert`)

Con `--experto`, antes de ejecutar Aider SnapContext **te deja revisar y
editar la lista de archivos** que la IA seleccionó:

```bash
snapcontext "revisar el flujo de pago" --experto
```

1. Tras la selección pregunta: `¿Quieres revisar los archivos seleccionados?
   (s/n)`.
   - `n` → ejecuta Aider directamente (comportamiento normal).
   - `s` → abre el menú experto.
2. En el menú se muestran los archivos **numerados** y las opciones:

   ```
   ── Modo experto ─────────────────────────
     [1] lib/pago/pago_page.dart
     [2] lib/models/pedido.dart
     [3] supabase/functions/procesar-pago-mp/index.ts
   Opciones: [a]gregar   [e]liminar   [l]impiar   [c]ontinuar
   ```

   | Opción | Qué hace |
   |---|---|
   | `a` | Pide una ruta y la añade (se valida que exista y esté dentro del repo) |
   | `e` | Elimina por índice (fuera de rango se rechaza) |
   | `l` | Vacía la lista (con confirmación) |
   | `c` | Usa la lista final y ejecuta Aider |

3. Con `c` (continuar) se muestran los archivos finales, se los pasa a Aider
   y se ejecuta cualquiera de los bucles elegidos (`--test-loop`,
   `--server-loop`, etc.)

---

## Bucle agéntico con servidor (`--server-loop` / `--manual-loop`)

En vez de solo probar, SnapContext **lanza la app** con `flutter run` en
segundo plano, detecta que el servidor arrancó (buscando `Running on`,
`Synced`, `served at`, etc., o la URL real) y deja verificar la app.

**Modo automático (`--server-loop`):**

```bash
snapcontext "arreglar la pantalla de pago" --server-loop
snapcontext "..." --server-loop --max-intentos 5      # reintentos (por defecto 3)
snapcontext "..." --server-loop --dispositivo chrome  # abrir Chrome
```

1. Aider edita los archivos.
2. Se lanza `flutter run` (por defecto `-d web-server --web-port 5000`).
3. Si el servidor **arranca** → pregunta `¿Quieres probar la app
   manualmente? (s/n)`; con `s` abre el navegador con la URL real y espera a
   que pulses Enter. Termina con éxito.
4. Si el servidor **falla** → captura el error, se lo pasa a Aider
   (`Arregla este error: ...`) y reintenta, hasta `--max-intentos`.
5. Si se agotan los intentos → pregunta `¿Quieres cambiar a modo manual?
   (s/n)`; con `s` pasa a `--manual-loop`, con `n` termina con error.

**Modo manual (`--manual-loop`):**

```bash
snapcontext "revisar el flujo de login" --manual-loop
```

Tras cada intento (arranque o fallo) pregunta siempre
`¿La app funciona correctamente? (s/n)`. Si respondes `n`, te pide que
describas el error y esa descripción se pasa a Aider
(`Arregla este error: <tu texto>`), repitiendo el ciclo.

> El servidor se cierra solo al finalizar (o con Ctrl+C), sin dejar procesos
> huérfanos. Configura tu proyecto para que `flutter run` use web si pruebas la
> interfaz en el navegador.

---

## 🧠 Extensión para IntelliJ IDEA / PyCharm (v1.7.0)

SnapContext también vive dentro del ecosistema **JetBrains** (IntelliJ IDEA,
PyCharm, WebStorm…) con una extensión en Kotlin ubicada en `jetbrains/`.

### Funcionalidades

| Acción | Dónde | Qué hace |
|---|---|---|
| Ejecutar consulta… (`Alt+Shift+S`) | Tools → SnapContext | Pipeline completo de SnapContext sobre el proyecto abierto |
| Planificar tarea… | Tools → SnapContext | Lanza `--plan --auto` con la consulta |
| Corregir con bucle de pruebas… | Tools → SnapContext | Lanza la consulta con `--test-loop` |
| Abrir interfaz web | Tools → SnapContext | Arranca `--web` y abre `http://localhost:8000` al estar listo |
| Añadir al contexto | Menú contextual del explorador | Marca archivos prioritarios (equivalente a `/add`) |
| Limpiar archivos de contexto | Tools → SnapContext | Vacía el contexto marcado |

La salida se muestra en tiempo real en la herramienta inferior **«SnapContext»**
con barra de progreso cancelable.

### Instalación (desde código fuente)

Requisitos: **JDK 17+** y conexión a Internet (Gradle descarga el SDK de
IntelliJ Community la primera vez).

```bash
cd jetbrains
./gradlew buildPlugin        # genera build/distributions/snapcontext-jetbrains-1.7.0.zip
./gradlew runIde             # o prueba el plugin en un IDE sandbox
```

Para instalar el `.zip`: **Settings → Plugins → ⚙ → Install Plugin from Disk…**

### Configuración

**Settings → Tools → SnapContext**:

- **Comando**: cómo invocar SnapContext (`python -m snapcontext` por defecto;
  usa `snapcontext` si tienes el ejecutable/instalador .exe).
- **Proveedor**: equivalente a `--provider` (vacío = el guardado en config).
- **Confirmar**: desactívalo para pasar `--no-confirmar`.
- **Clave API**: opcional; se exporta como `GEMINI_API_KEY` (y equivalentes).

> Nota: el plugin llama a la CLI de SnapContext con `ProcessBuilder`; no
> requiere Python embebido ni dependencias adicionales.

---

## Interfaz Web (`--web`)

SnapContext incluye una interfaz web ligera sobre la **arquitectura de agentes**
para seguir en tiempo real lo que hacen el orquestador y los agentes (escaneo,
selección, Aider, pruebas, cierre) sin mirar la terminal.

**Requisito:** instala las dependencias opcionales:

```bash
pip install snapcontext[web]
#   o, en desarrollo: pip install -e '.[web]'
```

**Arranque** (bloquea hasta `Ctrl+C`):

```bash
snapcontext --web                      # http://localhost:8000
snapcontext --web --web-puerto 8123    # puerto personalizado
```

Después abre `http://localhost:8000` en el navegador. La página ofrece:

- **Campo consulta** + botón **Ejecutar** (también con `Enter`).
- **Logs en tiempo real**: panel que muestra cada evento `log` que emite el
  orquestador (info/aviso/error) vía WebSocket.
- **Resultados**: archivos seleccionados, avance de Aider y cierre de la tarea
  (éxito/error).
- Opciones rápidas: **Selección local (sin IA)**, **Vista previa**, **Bucle de
  pruebas** y campos opcionales de directorio y `--max-archivos`.

**Eventos que emite el orquestador** (visible en la web):

| Tipo | Significado |
|---|---|
| `log` | Línea de log del pipeline (info/aviso/error). |
| `selección` | Archivos elegidos por el agente de contexto. |
| `aider` | Inicio/fin de la ejecución de Aider (AgenteEditor). |
| `test` | Iteración de prueba del AgenteTester (`aider`/`prueba`, ok/falló). |
| `final` | Cierre de la ejecución con código de éxito/error. |

En la consola, si no hay dependencias web instaladas, `snapcontext --web`
muestra el mensaje: *"La interfaz web necesita dependencias opcionales…"* y sale
sin romper el resto de la CLI.

---

## 🎨 Interfaz web avanzada (v1.6.0)

La web añade un **editor de código**, un **grafo de dependencias** y un **panel
de acciones rápidas** para competir con interfaces como las de Claude Code. La
UI ahora se organiza en dos columnas: **editor + dependencias** (izquierda) y
**progreso + resultados** (derecha).

### 📝 Editor de código (Monaco)

- **Monaco Editor** (el mismo editor que VS Code) se carga desde CDN
  (`cdnjs`) y muestra el contenido de los archivos seleccionados con
  **resaltado de sintaxis** para Python, JavaScript, TypeScript, Dart, Go,
  Rust, Java, C/C++, C#, Swift, etc.
- **Edición en vivo** con guardado manual (botón *Guardar*) que escribe el
  archivo en el proyecto vía WebSocket.
- **Fallback limpio**: si Monaco no carga (sin red o CSP), se usa un
  `<textarea>` funcional, así la web sigue operando.
- **Abrir el archivo**:
  - Haz clic en cualquier archivo del panel *Resultados* → se abre en el editor.
  - Los nodos del grafo de dependencias también abren el archivo al hacer clic.
  - En `--chat` el comando `/edit <archivo>` sigue abriendo el editor externo;
    ahora además se puede abrir cada archivo directamente desde la web.

### 🔗 Visualización de dependencias

- Panel con pestaña **Dependencias** que dibuja un **grafo interactivo**
  (force-directed con **d3.js**) de los archivos del proyecto.
- Las aristas provienen de los **imports** reales del código (Python `import`,
  JS/TS `import`/`require`, Dart `import`, Go `import`, Rust `use`, …),
  resueltos contra los archivos del repositorio.
- **Interactivo (ampliado en v1.6.0)**: **zoom y pan** con la rueda
  (`d3.zoom`, 0.2–4×), arrastre de nodos, **clic para abrir** el archivo en el
  editor y **doble clic para resaltar rutas**: las conexiones directas del
  nodo se pintan en verde y el resto se atenúa (doble clic en el fondo limpia).
- **Filtros (v1.6.0)**: por **lenguaje** (select poblado con los lenguajes
  presentes en el proyecto) y por **nivel de dependencia** respecto al archivo
  abierto — «todos», «1 (directos)», «≤2», «≤3» (BFS).
- Tooltips por nodo con ruta completa, lenguaje y nivel.
- Si d3 no carga, se muestra una lista de `<origen → destino>`.

### ⚡ Panel de acciones rápidas

| Botón | Acción |
|---|---|
| `⚙ Fix` | Ejecuta el alias `fix` (bucle de pruebas) sobre la consulta. |
| `🔍 Review` | Ejecuta el alias `review` (vista previa + revisión experta). |
| `🧭 Plan` | Abre el planificador (`--plan`) con la consulta actual. |
| `▶ Run` | Ejecuta un comando personalizado (modal). Atajo: `Ctrl+R`. |
| `🧪 Tests` (v1.6.0) | Abre el modal de comandos pre-rellenado (`flutter test`) para lanzar la suite. |
| `🧠 Search` | Búsqueda semántica por embeddings (si el extra `embeddings` está instalado). |
| `🔎 Explorar` | Busca la consulta en el código (`rg`/`grep`/`findstr`). |

Todos los botones tienen **tooltip descriptivo** con lo que hacen.

### ✨ UX (v1.6.0)

- **Guardar con confirmación**: `💾 Guardar` o `Ctrl+S` pide confirmación antes
  de sobrescribir el archivo en disco.
- **Notificaciones toast** (info/ok/aviso/error) para selecciones, guardados,
  errores de conexión y acciones.
- **Historial de sesión**: las últimas tareas ejecutadas; clic para reutilizar
  la consulta en el campo principal.
- **Resultados mejorados**: ruta completa visible + botón «Abrir» por archivo.
- **Atajos de teclado**: `Ctrl+Enter` ejecutar · `Ctrl+S` guardar ·
  `Ctrl+D` pestaña Dependencias · `Ctrl+R`/`Ctrl+O` modal de comandos ·
  `Esc` cerrar modales.

### 🔌 Comunicación con el orquestador (WebSockets)

El servidor (`web/app.py`) amplía el protocolo del endpoint `/ws`:

- `archivo_seleccionado` → contenido + lenguaje para el editor.
- `archivo_guardado` → confirmación de escritura en disco.
- `dependencias_actualizadas` → nodos + enlaces del grafo.
- `semanticos` / `exploracion` → resultados de búsqueda.
- `accion_ejecutada` → cierre de cada acción rápida.
- `tarea` (o el `consulta` clásico) → pipeline del orquestador como siempre.

La CLI y la extensión VS Code **no se ven afectadas**; la webview de VS Code
sigue siendo una copia de `web/static/index.html` (`vscode/webview/`), y si el
entorno de la webview bloquea los CDN se usa el respaldo sin romper nada.

---

## 🧠 Búsqueda semántica con embeddings (v1.1.0)

SnapContext puede indexar tu proyecto con **embeddings semánticos locales**
usando `sentence-transformers` + `torch` (modelo `all-MiniLM-L6-v2`). Esto
permite buscar archivos por su significado —no solo por palabras clave—
mejorando drásticamente la selección de contexto y acercándose a la calidad
de Claude Code.

### Instalación

```bash
pip install "snapcontext[embeddings]"   # incluye sentence-transformers + torch
```

> **Opcional**: sin este extra, SnapContext funciona exactamente como v1.0.0
> (heurística + selección con IA). La búsqueda semántica se activa de forma
> automática solo cuando la librería está disponible.

### Cómo funciona

1. **Indexación**: al primer uso (o cuando cambia el proyecto), SnapContext
   escanea recursivamente los archivos de código (`.py`, `.js`, `.ts`,
   `.dart`, `.go`, `.rs`, …), respetando `.gitignore`, divide cada archivo en
   fragmentos de ~512 tokens y genera embeddings para cada uno.
2. **Caché persistente**: el índice se guarda en `~/.snapcontext/index/`.
   SnapContext almacena un **hash del proyecto** y, si detecta cambios,
   **reindexa automáticamente** (con aviso). Los archivos sin cambios reutilizan
   sus embeddings cacheados —solo se recomputan los fragmentos nuevos o
   modificados.
3. **Búsqueda**: en cada ejecución, SnapContext genera un embedding de la
   consulta, calcula la **similitud de coseno** con todos los fragmentos y
   prioriza los archivos más relevantes.

### Uso en el pipeline

Durante `snapcontext "<consulta>"` (o `--plan`, `--chat`, etc.), si está el
extra de embeddings y hay más candidatos que `max_archivos`, SnapContext:

1. Carga (o indexa) el proyecto.
2. Ejecuta `_seleccionar_archivos_con_embeddings()` para obtener los archivos
   más similares a la consulta (umbral configurable, por defecto 0.6).
3. **Reordena** los candidatos locales poniendo primero los archivos priorizados
   por embeddings, de modo que el proveedor de IA refine sobre un subconjunto
   de alta calidad.

Esto reduce drásticamente el número de llamadas al proveedor y mejora la
precisión de la selección final.

### Búsqueda desde el chat (`--chat`)

En modo chat interactivo, el comando `/search <consulta>` ejecuta una búsqueda
semántica y muestra los archivos más relevantes con su puntuación:

```
snapcontext --chat
/search botón de pago no funciona
📄 lib/pagos/pago_service.dart  (similitud: 0.89)
📄 lib/ui/pago_form.dart        (similitud: 0.76)
📄 test/pago_test.dart          (similitud: 0.65)
```

### Uso directo desde Python

```python
import snapcontext as sc

if sc._embeddings_disponibles():
    resultados = sc._buscar_semanticamente("gestión de usuarios", directorio=".")
    for r in resultados:
        print(r["archivo"], r["similitud"])

    archivos = sc._seleccionar_archivos_con_embeddings(
        "gestión de usuarios", directorio=".", max_archivos=3, umbral=0.6
    )
    print(archivos)
```

---

## Solución de problemas

| Problema | Solución |
|---|---|
| `No se encontró la librería 'google.generativeai'` | `pip install google-generativeai` |
| `No se encontró la librería 'openai'` | `pip install openai` |
| `No se encontró la variable de entorno GEMINI_API_KEY` | Configurar la clave del proveedor (ver Instalación) |
| Falta la clave de DeepSeek / Groq | Configurar `DEEPSEEK_API_KEY` / `GROQ_API_KEY` |
| `Error al llamar a Ollama` | Arrancar el servidor (`ollama serve`) y descargar el modelo (`ollama pull llama3.2`) |
| `No se encontró 'flutter'` | Instalar Flutter o revisar el PATH (bucle de servidor) |
| `--server-loop` no arranca la app | Ajustar `--dispositivo` (web-server / chrome / edge) y `--url-defecto` |
| `No se encontró el comando 'aider'` | `pip install aider-chat` |
| `No se encontraron archivos` | Revisar las carpetas escaneadas con `--carpetas` |
| Quieres probar sin gastar cuota API | `--local --vista-previa` |
| Errores de red / cuota en Gemini | Reintenta; el mensaje incluye el detalle |

---

## Extenderlo

El código está pensado como un **único script claro y comentado**. Puntos de
extensión fáciles:

- `escanear_repositorio()` → cambiar la heurística de relevancia local.
- `construir_prompt_seleccion()` → mejorar las instrucciones a Gemini.
- `ejecutar_bucle_test()` → añadir más herramientas al bucle agéntico.
- Constantes de configuración → ajustar el comportamiento por defecto.

---

## Variables de entorno

Ejemplo de configuración en **Linux/macOS** (`bash`/`zsh`):

```bash
export GEMINI_API_KEY="tu_clave"
export DEEPSEEK_API_KEY="tu_clave"
export GROQ_API_KEY="tu_clave"
export OLLAMA_URL="http://localhost:11434"
```

Ejemplo de configuración en **Windows** (PowerShell):

```powershell
$env:GEMINI_API_KEY = "tu_clave"
$env:DEEPSEEK_API_KEY = "tu_clave"
$env:GROQ_API_KEY = "tu_clave"
$env:OLLAMA_URL = "http://localhost:11434"
```

| Variable | Uso |
|---|---|
| `GEMINI_API_KEY` | Clave de Google AI Studio (proveedor `gemini`) |
| `DEEPSEEK_API_KEY` | Clave de la API de DeepSeek |
| `GROQ_API_KEY` | Clave de la API de Groq |
| `OLLAMA_URL` | URL del servidor Ollama (por defecto `http://localhost:11434`) |
| `OLLAMA_API_KEY` | Opcional, si tu servidor Ollama exige clave |
| `SNAPCONTEXT_PROVIDER` | Proveedor por defecto (opcional) |
| `SNAPCONTEXT_MODELO` | Modelo global por defecto (opcional) |
| `NO_COLOR` / `FORCE_COLOR` | Control de colores en la terminal |
| `AIDER_*` | Configuración heredada por Aider (KEY, model, etc.) |

---

## Compatibilidad y Permisos (Linux / macOS / Windows)

- **Windows**: Al instalar con `pip install snapcontext` o usando el one-liner (`install.ps1`), el instalador configura automáticamente el PATH del usuario permanentemente. Si instalaste manualmente sin usar el one-liner, ejecuta `snapcontext --setup-path` para añadir la carpeta al PATH.
- **Permisos de ejecución**: En Linux/macOS, al instalar con `pip install -e .` o `pip install snapcontext`, pip registra el ejecutable en el `PATH` del usuario de forma automática sin requerir permisos especiales. Si ejecutas `snapcontext.py` directamente como script en Unix, puedes asignarle permisos de ejecución con `chmod +x snapcontext.py`.
- **Servidor y Navegador**: En Linux y macOS, si `webbrowser.open()` no responde en entornos sin interfaz gráfica o con configuraciones personalizadas, SnapContext usa de forma automática los comandos nativos `xdg-open` (Linux) u `open` (macOS) como respaldo sin usar `shell=True`.
- **Manejo de Señales**: En Linux/macOS y Windows, la interrupción por teclado (`Ctrl+C` / `SIGINT`) o la señal de terminación (`SIGTERM` en Unix) capturan el evento, cierran limpiamente cualquier subproceso en segundo plano (como `flutter run`) y salen de forma ordenada con código `0`.

---

## Publicación en PyPI

SnapContext está preparado para publicarse en PyPI (el nombre `snapcontext`
está disponible; alternativas: `snapcontext-cli`, `snapcontext-tool`). Antes
de publicar, edita en `pyproject.toml` el campo `authors` (y `[project.urls]`)
con tus datos reales.

Sigue la guía completa en **[PUBLISHING.md](PUBLISHING.md)** (build + twine):

```bash
pip install build twine
python -m build
python -m twine check dist/*
python -m twine upload dist/*
pip install snapcontext   # verificar en un entorno limpio
```


---

## 🔗 Grafo de conocimiento (Graph RAG)

Desde **v5.5.0**, SnapContext puede combinar el análisis **AST** del proyecto con la **búsqueda semántica** (embeddings) para dar al agente una comprensión arquitectónica del código: no solo *qué archivos hablan del tema*, sino *también con quién se relacionan*.

### Cómo funciona

1. `graph_rag.py` construye un grafo con AST: **nodos** = archivos, funciones y clases; **aristas** = imports, llamadas a funciones y herencia.
2. El grafo se persiste en `~/.snapcontext/graph_cache.pkl` y solo se reconstruye cuando cambia algún `.py` (fingerprint por mtime + tamaño).
3. Cuando la búsqueda semántica devuelve archivos relevantes, el grafo añade hasta 3 archivos **relacionados** (quienes los importan y de quiénes dependen) al contexto del agente o del planificador.

### Activación

```bash
# Puntual
snapcontext "arreglar el checkout" --graph-rag

# Siempre activo
export SNAPCONTEXT_GRAPH_RAG=1
```

Solo Python en esta versión (tree-sitter para otros lenguajes llega en v5.6.0). El flag es completamente opcional: sin él, SnapContext se comporta igual que en 5.4.0.
---

## 🌐 Editor multi-lenguaje (Tree-sitter)

Desde **v5.6.0**, el editor propio (transaccional, fuzzy matching, análisis de
impacto y contexto selectivo) funciona también en proyectos **no Python**
gracias a [tree-sitter](https://tree-sitter.github.io/), un parser incremental
multi-lenguaje.

### Lenguajes soportados

| Extensión | Lenguaje |
|---|---|
| `.py` | Python (usa `ast` de la stdlib, como siempre) |
| `.js` / `.jsx` | JavaScript |
| `.ts` / `.tsx` | TypeScript |
| `.go` | Go |
| `.rs` | Rust |
| `.java` | Java |
| `.kt` | Kotlin |
| `.rb` | Ruby |
| `.php` | PHP |
| `.c` / `.h` | C |
| `.cpp` / `.hpp` / `.cc` | C++ |
| `.cs` | C# |
| `.swift` | Swift |
| `.dart` | Dart |

### Cómo funciona

1. **Detección** del lenguaje por extensión (`parser_universal.detectar_lenguaje_por_extension`).
2. **Parseo** con tree-sitter (`parsear_archivo`) para obtener el AST.
3. **Extracción** de funciones/clases/métodos (`extraer_nodos`) para el
   análisis de impacto y el contexto selectivo.
4. **Parche seguro** por bytes exactos del nodo (`aplicar_parche_arbol`).

La cadena de estrategias se mantiene intacta: **AST → Parche → Sobrescritura**.
Si tree-sitter no está instalado, el archivo está mal formado o el lenguaje no
está soportado, el editor cae elegantemente a las estrategias siguientes
(igual que en 5.5.0). Python **no** cambia: sigue usando el `ast` nativo.

### Instalación

```bash
pip install "snapcontext[editor_multilenguaje]"
# o directamente:
pip install tree-sitter tree-sitter-languages tree-sitter-language-pack
```


## 🙌 Agradecimientos

- **Aider** por su excelente motor de edición de código.
- **Google Gemini** por su generoso plan gratuito.
- **Ollama**, **DeepSeek** y **Groq** por sus modelos open-source.
- La comunidad open-source por las herramientas que hacen posible este proyecto.

## 🧠 Mostrar razonamiento del modelo

A partir de la versión 6.2.0, SnapContext puede mostrar el **razonamiento
interno (chain-of-thought)** del modelo antes de cada acción o respuesta, en
todos los modos principales (chat, planificador, editor propio y ReAct):

```bash
# Flag CLI
snapcontext "añade tests para el parser" --mostrar-razonamiento

# Variable de entorno (equivalente)
export SNAPCONTEXT_MOSTRAR_RAZONAMIENTO=1
```

Cómo funciona:

1. Si la respuesta del proveedor incluye un campo de razonamiento
   (`reasoning`, `thinking`, `chain_of_thought`, `reasoning_content`,
   `thoughts`) o bloques `<think>…</think>` (DeepSeek-R1 y otros modelos
   locales), se muestra en un panel antes del resultado.
2. Si el modelo no lo proporciona, se activa el **modo de dos pasos**: una
   llamada extra pide el razonamiento paso a paso y después se ejecuta la
   acción normal (con una advertencia, porque duplica las llamadas).
3. Los bloques `<think>…</think>` se eliminan del texto útil, de forma que no
   rompen el parseo de JSON del planificador ni los parches del editor.

El modo está **desactivado por defecto**: sin el flag ni la variable de
entorno, el flujo es idéntico al anterior y no añade ninguna llamada extra.

## 🌐 Omnicanalidad avanzada (v6.8.0)

SnapContext 6.8.0 introduce integración nativa con **GitHub Webhooks**, una **cola de tareas asíncronas persistente** (SQLite) y **notificaciones push** integradas con Telegram y Discord.

### 🐙 Integración con GitHub (`github_gateway.py`)
- **Validación de firmas HMAC SHA-256**: Verifica la autenticidad de los webhooks de GitHub en `POST /webhook/github` con tiempo constante.
- **Parseo y automatización**:
  - `pull_request` (opened, synchronize): Encola automáticamente una tarea `pr_review`.
  - `issues` (opened): Encola la planificación o resolución de incidencias (`plan`).
  - `push`: Ejecuta suites de pruebas automatizadas en segundo plano (`tests`).
- **Integración API REST de GitHub**: Permite consultar diffs (`obtener_pr_diff`) y publicar comentarios (`comentar_pr`) directamente en PRs e Issues.

#### Configuración de GitHub
```bash
# Configuración inicial de credenciales
snapcontext github setup --token ghp_xxxx --secret mi_secreto_webhook --webhook-url https://mi-dominio.com

# Registro del webhook en un repositorio de GitHub
snapcontext github webhook-registrar --repo owner/proyecto
```

### ⚡ Cola de Tareas Asíncronas y Worker (`task_queue.py`)
- Las tareas pesadas ya no bloquean la respuesta del webhook ni la CLI.
- Se persisten en la tabla SQLite `tareas` con estados `pendiente`, `ejecutando`, `completada`, `fallida`, `cancelada`.
- El daemon (`snapcontext --daemon`) o el worker en segundo plano consumen tareas y envían notificaciones push al finalizar.

### 💬 Nuevos Comandos en Telegram y Discord
- `/pr <numero>`: Encola la revisión asíncrona de un Pull Request.
- `/tests [rama]`: Encola la ejecución de pruebas en segundo plano.
- `/status`: Consulta el estado actual de las tareas recientes.
- `/cancel <id>`: Cancela una tarea en cola pendiente.

```
Usuario: /pr 42
SnapContext: 🔔 Tarea encolada (ID: 15) - Revisando PR #42 en segundo plano.
... (al finalizar)
SnapContext: ✅ Tarea 15 (pr_review) completada: Revisión de PR #42 completada con éxito.
```

## 🌐 Expansión de MCP (v6.7.0)

A partir de la versión 6.7.0, SnapContext expande su ecosistema MCP nativo con herramientas de **solo lectura** para interactuar con bases de datos relacionales e inspeccionar APIs externas.

### 🐘 Bases de Datos (`mcp_tools_db.py`)
Permite conectar a bases de datos y ejecutar consultas de lectura seguras integradas con el agente ReAct y el planificador:
- **`db_connect(url, driver=None)`**: Conexión perezosa a SQLite (nativo en Python), PostgreSQL (`psycopg2`) o MySQL (`pymysql`).
- **`db_query(consulta, auto=False)`**: Ejecuta consultas de **solo lectura** (`SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`, `PRAGMA`).
  - Bloquea estrictamente sentencias de modificación (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, etc.).
  - En modo interactivo solicita confirmación al usuario: `¿Ejecutar consulta: <consulta>? (s/n)`.
  - En modo `--auto` ejecuta sin preguntar (manteniendo la validación estricta de solo lectura).
- **`db_schema()`**: Obtiene la lista completa de tablas, columnas, tipos de datos y primary keys.
- **`db_disconnect()`**: Cierra la conexión activa y limpia el contexto de sesión.

#### Flags CLI para Bases de Datos
- `--db-url <url>`: URL de conexión (ej: `sqlite:///datos.db`, `postgresql://user:pass@localhost:5432/mi_db`, `mysql://user:pass@localhost:3306/mi_db`).
- `--db-driver <driver>`: Forzado opcional del driver (`sqlite`, `postgresql`, `mysql`).

```bash
# Conectar a SQLite y consultar esquemas / datos con ReAct
snapcontext "Analiza el esquema de la base de datos y dime cuántos usuarios hay" --db-url sqlite:///app.db

# Conectar a PostgreSQL
snapcontext "Verifica los pedidos pendientes" --db-url postgresql://postgres:secret@localhost:5432/ecommerce
```

### 🌐 APIs Externas (`mcp_tools_api.py`)
Permite inspeccionar endpoints REST y analizar respuestas:
- **`api_request(url, metodo="GET", headers={}, body="", timeout=15)`**: Realiza peticiones HTTP (`GET`, `POST`, `HEAD`, `OPTIONS`, etc.) mediante `httpx`.
  - Parsea respuestas JSON automáticamente.
  - Oculta cabeceras sensibles en las observaciones (`Authorization`, `Cookie`, `X-Api-Key`, etc.).
  - Aplica límite de seguridad al tamaño de cuerpo (`MAX_CUERPO = 200,000`).
- **`api_inspect(url, timeout=15)`**: Análisis rápido de salud y rendimiento (código HTTP, tiempo de respuesta en ms, tamaño en bytes, content-type y headers del servidor).

```bash
# Inspeccionar un endpoint y razonar sobre el resultado
snapcontext "Inspecciona https://api.github.com y dime qué headers de rate-limit devuelve" --react
```

## 🧠 Skills dinámicos (v6.6.0)

SnapContext ya no guarda solo pasos fijos: a partir de v6.6.0 el Curador
Proactivo extrae **reglas abstractas de arquitectura/estilo** de cada plan
exitoso y las reutiliza en futuras tareas.

### Cómo funciona
1. Tras un plan **totalmente exitoso** (de forma asíncrona, sin bloquear), se
   pide al LLM que resuma el plan como una regla: `patron`, `accion`,
   `archivos_afectados` y `dependencias`. Si el LLM falla, hay una heurística
   de respaldo (archivos más editados, confianza 0.6).
2. La regla se guarda en la tabla `reglas` de la base de datos SQLite. Si ya
   existía una regla similar (similitud ≥ 0.85), se **refuerza**: +0.05 de
   confianza y +1 uso, sin duplicar.
3. Las reglas con confianza > 0.8 se **inyectan automáticamente** en
   `CLAUDE.md`/`SNAPCONTEXT.md`, en la sección `## Reglas aprendidas`
   (idempotente: nunca se duplican líneas).
4. Antes de generar un plan, el planificador busca reglas cuyo patrón coincida
   con la tarea (similitud ≥ 0.45) y **enriquece el prompt** con las de mayor
   confianza, bajo el encabezado `REGLAS APRENDIDAS`.

### Flags
- `--skills-dinamicos` (activado por defecto): extracción y aplicación de
  reglas abstractas.
- `--sin-skills-dinamicos`: desactiva todo el sistema de reglas.
- `--inyectar-reglas`: fuerza la inyección de todas las reglas aprendidas en
  `CLAUDE.md` y termina (útil tras actualizar desde versiones anteriores).

Mensajes que verás:
```
🧠 Extrayendo regla abstracta del plan exitoso...
📝 Nueva regla aprendida: añadir endpoint de pagos (confianza: 1.0)
📄 Regla inyectada en CLAUDE.md
```

Seguridad: las reglas se sanitizan antes de inyectarse (sin bloques de código
` ``` ` ni caracteres de control, longitud máxima por campo), y nunca se
ejecutan: solo enriquecen prompts y documentación.

## 🌐 UI Web interactiva (v6.5.0)

Con `--web --web-interactive`, la interfaz web se convierte en un **centro de
control para agentes autónomos** (además de la web clásica, que sigue intacta):

```bash
snapcontext --web --web-interactive
# ℹ Interfaz web en http://localhost:8000  (Ctrl+C para salir)...
# ℹ 🌐 Interfaz web interactiva: http://localhost:8000/interactive
```

Abre `http://localhost:8000/interactive` en tu navegador y verás:

- **Timeline de ReAct**: cada ciclo Pensamiento → Acción → Observación llega en
  tiempo real por WebSocket (`/ws/interactive`) como una tarjeta coloreada
  (🟡 pensamiento, 🔵 acción, 🟢 observación, 🔴 error), con marca de tiempo
  relativa ("hace Xs") y detalles expandibles.
- **Diff viewer interactivo**: si el editor propio no puede aplicar un parche,
  se abre un modal con Monaco Editor en modo diff (original ⇆ propuesto).
  Puedes *Aceptar todo*, *Rechazar todo* o editar y *Aceptar líneas
  seleccionadas*; el resultado se envía de vuelta por WebSocket y se aplica de
  forma transaccional (solo tras tu confirmación explícita).
- **Panel de control**: estado del agente, contador de pasos, tiempo total y
  logs estructurados con niveles (info/warning/error).

Notas:
- El modo es **opt-in**: sin `--web-interactive`, `--web` funciona exactamente
  como antes.
- El hub (`web/interactive.py`) es no bloqueante: los eventos se descartan si
  la cola está llena, nunca ralentizan al agente.
- Los contenidos se validan y recortan (máx. 400k caracteres) antes de
  transportarse; el contenido aceptado en el diff viewer solo se escribe en el
  archivo objetivo.

## Licencia

MIT. Open-source y libre de usarlo, estudiarlo y mejorarlo.

![v6.20.0](https://img.shields.io/badge/version-6.22.0-blue.svg)
[![PyPI](https://badge.fury.io/py/snapcontext.svg)](https://pypi.org/project/snapcontext/)
[![CI](https://img.shields.io/github/actions/workflow/status/NicolasBruna24/snapcontext/ci.yml?branch=main&label=tests)](https://github.com/NicolasBruña24/snapcontext/actions)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Plataformas](https://img.shields.io/badge/platform-windows%20%7C%20linux%20%7C%20macOS-lightgrey.svg)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

**SnapContext** es un asistente de IA con contexto automático para desarrollo:
detecta el tipo de proyecto, selecciona los archivos relevantes con IA, ejecuta
tareas con Aider, planifica trabajos complejos y aprende del proyecto mediante
una memoria persistente (`CLAUDE.md`).

- **Proveedores**: Gemini · Claude (Anthropic) · Ollama (local) · DeepSeek · Groq
- **Arquitectura**: orquestador + agentes (Contexto / Editor / Tester)
- **Seguridad**: permisos con confirmaciones (`~/.snapcontext/permisos.json`)

## ⚡ Mejora de rendimiento (v6.9.0)

SnapContext v6.9.0 es más rápido y ligero sin cambiar el comportamiento:

| Mejora | Detalle |
|---|---|
| **Inicio lazy** | Los imports pesados (`sentence-transformers`, `tree-sitter`, `graph_rag`, `multi_agent`, ...) se cargan bajo demanda. `--help` pasa de ~1.2s a <0.3s. |
| **Cache de embeddings** | SQLite en `~/.snapcontext/embeddings.db` (tabla `embeddings(archivo, hash, embedding BLOB)`). Los archivos sin cambios reutilizan su embedding (≈80% más rápido en re-escaneos). |
| **Graph RAG incremental** | El grafo se cachea en `~/.snapcontext/graph_cache.pkl` con un fingerprint por archivo; solo se reparsean los archivos modificados (≈70% más rápido). |
| **Fuzzy matching optimizado** | Contexto limitado a 20 líneas (`MAX_CONTEXTO_DIFUSO_LINEAS`) y resincronización de bloques con `difflib.get_close_matches` (≈50% más rápido en archivos grandes). |
| **Worker sin polling** | La cola de tareas usa `threading.Event` en lugar de `time.sleep`: CPU idle ≈0% y despertar instantáneo al encolar. |
| **Historial ReAct limitado** | Máximo 20 iteraciones (configurable vía `REACT_MAX_HISTORIAL`); al superarlo se comprime con un resumen LLM (≈30% menos memoria en sesiones largas). |

### Medir el rendimiento con `--benchmark`

```bash
python snapcontext.py --benchmark          # benchmark del proyecto actual
python snapcontext.py --benchmark ruta/    # benchmark de otro directorio
```

Ejemplo de salida (tabla con `rich`, no requiere API key):

```
⚡ Benchmark de rendimiento — SnapContext 6.9.0
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Fase                                 ┃ Tiempo (s) ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Inicio (import + CLI)                │     0.1902 │
│ CLI (crear_parser)                   │     0.0141 │
│ Escaneo de archivos                  │     0.0527 │
│ Selección (embeddings/heurística)    │     0.1015 │
│ Generación de plan (prompt+contexto) │     0.0021 │
│ Edición (fuzzy matching)             │     0.0089 │
│ Detección de pruebas                 │     0.0007 │
│ Tiempo total                         │     0.3704 │
└──────────────────────────────────────┴────────────┘
```

> Las cachés son opcionales y degradan silenciosamente si no se pueden
> escribir. No almacenan código fuente, solo hashes, fingerprints y vectores.


## 🌐 Navegador y multimodalidad (v6.10.0)

SnapContext puede **ver la interfaz de tu aplicación en ejecución** y depurar
errores visuales de forma autónoma (CSS roto, componentes que no renderizan,
formularios que fallan, ...).

## 🧠 Prompt Caching (v6.16.0)

SnapContext envía el mensaje del sistema (con las herramientas MCP) y la memoria
del proyecto (`CLAUDE.md`) en cada interacción. Con **Prompt Caching**, los
proveedores compatibles mantienen estos bloques **en caché durante la sesión**,
reduciendo drásticamente el coste (hasta ~70% en sesiones largas) y la latencia.

### ¿Qué hace?

- **Anthropic (Claude)** y **DeepSeek** soportan la marca
  `cache_control: {"type": "ephemeral"}`. SnapContext la añade a:
  1. El mensaje del sistema (el primero de la lista).
  2. Los mensajes con definiciones de herramientas MCP.
  3. El mensaje con la memoria del proyecto (`CLAUDE.md` / `SNAPCONTEXT.md`).
- **Gemini, Groq y Ollama** no soportan caching: se envían los mensajes tal cual
  (sin marcas), manteniendo la compatibilidad total.
- Las marcas **no modifican el contenido** de los mensajes: solo añaden la
  instrucción de caché.

### Activación (por defecto activado)

```bash
snapcontext "revisar login" --provider anthropic     # activado por defecto
snapcontext "revisar login" --no-prompt-caching      # desactivar
snapcontext --chat --no-prompt-caching               # desactivar en el chat
```

También se puede controlar con:

```bash
SNAPCONTEXT_PROMPT_CACHING=0 snapcontext "revisar login"   # desactivar
SNAPCONTEXT_PROMPT_CACHING=1 snapcontext "revisar login"   # activar
```

O desde `~/.snapcontext/config.json`:

```json
{
  "provider": "anthropic",
  "prompt_caching": false
}
```

### Métricas de caché (v6.16.0)

Con `--depurar`, SnapContext muestra estimaciones de tokens cacheados por
categoría al enviar cada petición:

```bash
snapcontext "revisar login" --provider anthropic --depurar
# ℹ Prompt Caching activado (anthropic): sistema (145 tokens), herramientas (230 tokens), CLAUDE.md (890 tokens)
```

Esto permite ver cuánto se está ahorrando en cada llamada sin necesidad de
inspeccionar los logs del proveedor.

> **Nota**: el Prompt Caching solo se aplica cuando el proveedor lo soporta y está
> activado; para proveedores sin soporte o con caching desactivado el flujo es
> idéntico al anterior. Al inicio de la sesión verás
> `🧠 Prompt Caching activado para <proveedor>` (o `no soportado`).


## 🖥️ TUI inmersiva (v6.17.0)

SnapContext incluye una **interfaz de terminal interactiva (TUI)** basada
en [Textual](https://github.com/Textualize/textual), que complementa el CLI
tradicional y la interfaz web con un centro de control inmersivo.

> **v6.17.0**: la TUI se consolida como el modo inmersivo recomendado (`--tui`),
> con arranque optimizado, mensaje de inicio explícito y compatibilidad total
> con el flujo ReAct y el editor propio (no rompe el CLI sin `--tui`).

```bash
pip install "snapcontext[tui]"        # instalar la dependencia opcional
snapcontext --tui                     # abrir la TUI (sin consulta)
snapcontext --tui "revisar login"     # abrir la TUI y lanzar el agente
```

### ¿Qué incluye?

- **Pestañas**:
  - 📜 **Logs**: salida en tiempo real del agente, coloreada por nivel
    (info / warning / error) e integrada con el flujo ReAct
    (💭 Pensamiento → ⚡ Acción → 👁 Observación).
  - 🎛️ **Control**: estado del agente (inactivo / pensando / ejecutando /
    esperando / error), contador de pasos, tiempo total y botones de
    Pausar / Reanudar / Cancelar.
  - 🔀 **Diffs**: visor de los cambios propuestos por el editor propio
    (con `--mostrar-diff`), coloreados (+ verde / - rojo / @@ cabeceras).
- **Árbol de archivos** colapsable del proyecto actual (`DirectoryTree`).
- **Atajos**: `Ctrl+Q` salir · `Ctrl+L` limpiar logs · `Ctrl+D` limpiar diffs ·
  `Ctrl+T` mostrar/ocultar el árbol.

### Arquitectura (modular, sin bloquear al agente)

- El agente (`react_agent.py`, editor propio) envía eventos a una cola
  (`tui_hub.py`, `put_nowait`); si la TUI está inactiva o la cola llena, el
  evento se descarta: **coste ~0 para el CLI tradicional**.
- La app (`tui_app.py`) drena la cola con un temporizador asíncrono
  (máx. 200 eventos/tick) sin bloquear la UI.
- El agente corre en un hilo demonio; la TUI es solo presentación.

### Sin Textual instalado

```text
❌ Textual no está instalado. Instala el grupo opcional:
    pip install snapcontext[tui]
```

> **Nota**: el CLI sin `--tui` funciona exactamente igual que antes; la TUI es
> un modo adicional y Textual es una dependencia opcional (grupo `[tui]`).


## 📝 Git profundo (v6.20.0)

SnapContext crea **commits atómicos por paso** con mensajes generados por IA
(Conventional Commits) y permite **revertir** cualquier paso:

```bash
# Commits automáticos tras cada paso del plan (activado por defecto)
snapcontext --plan "añadir login" --git-commit

# Desactivar commits automáticos
snapcontext --plan "añadir login" --no-git-commit

# Mensaje de commit manual (en lugar de IA)
snapcontext --plan "añadir login" --git-mensaje "feat: login social"

# Revertir un paso (por id de la tabla 'pasos' en la BD)
snapcontext revert 3
snapcontext revert            # revierte el último paso commiteado
snapcontext --plan "..." --git-revert 5   # variante con flag
```

Cada commit se registra en la tabla `pasos` de `~/.snapcontext/memoria.db`
(hash, descripción, tipo y fecha). El revert usa `git revert --no-commit`
y registra el resultado; si hay conflictos sugiere `git mergetool`. Los
mensajes generados se saneían para no filtrar secretos (claves API, tokens).

## 🤖 Sub-agentes dinámicos (v6.20.0)

El equipo multi-agente puede delegar sub-tareas en **sub-agentes
especializados** que se instancian bajo demanda, con contexto aislado y
ejecución en paralelo controlada.

```bash
snapcontext --multi-agent --sub-agents "añade exportación a PDF"
snapcontext --multi-agent --sub-agents --max-parallel 5 "mejora el rendimiento"
snapcontext --sub-agente-listar                  # v6.20.0: lista sub-agentes
snapcontext --sub-agente-nuevo auditor "revisa seguridad"   # v6.20.0: nuevo rol
```

### Roles predefinidos

| Rol | Especialidad | Herramientas |
|-----|--------------|--------------|
| `scout` | Leer documentación, explorar código y APIs | `leer_archivo`, `buscar_codigo`, `api_inspect`, `browser_get_text` |
| `debugger` | Analizar errores y logs, diagnosticar causas | `leer_archivo`, `buscar_codigo`, `ejecutar_comando`, `ejecutar_pruebas` |
| `frontender` | Revisar CSS/HTML/interfaz | `leer_archivo`, `buscar_codigo`, `editar_archivo`, `browser_*` |
| `tester` | Ejecutar pruebas aisladas e informar | `ejecutar_pruebas`, `ejecutar_comando`, `leer_archivo` |
| `reviewer` (nuevo v6.20.0) | Revisar PRs, diffs e impacto | `leer_archivo`, `buscar_codigo`, `ejecutar_comando`, `ejecutar_pruebas` |
| `documentador` | Generar/actualizar README, CLAUDE.md | `leer_archivo`, `buscar_codigo`, `editar_archivo` |

### v6.20.0 — registro e invocación dinámica

- **`sub_agent.SubAgentRegistry`**: registro consultable (`registrar`,
  `obtener`, `listar`) con sub-agentes por defecto (**scout, debugger,
  reviewer, documentador**). Se pueden registrar roles nuevos sin tocar el
  código (plugins / `--sub-agente-nuevo`).
- **`sub_agent_prompts.py`**: módulo canónico con los prompts de sistema de
  cada rol por defecto.
- **Invocación bajo demanda desde el Supervisor** (`Supervisor.invocar_sub_agente(nombre, consulta)`).
- **Herramienta ReAct `invocar_sub_agente(nombre, consulta)`**: el agente
  ReAct puede delegar una tarea especializada en un sub-agente con contexto
  aislado (disponible por defecto; los sub-agentes no pueden anidarse, ya que
  el rol filtra sus herramientas).

### Cómo funciona

- **Contexto aislado**: cada sub-agente tiene su propio historial ReAct; no
  comparte contexto con el agente principal ni con otros sub-agentes.
- **Detección automática**: el Supervisor analiza el plan del Arquitecto
  (palabras clave como "documentación", "error", "pruebas", "CSS"...) y
  delega los pasos aplicables al rol adecuado.
- **Paralelismo**: los sub-agentes corren en hilos con un semáforo
  (`--max-parallel`, por defecto 3). También pueden encolarse como tareas
  asíncronas de la cola v6.8.0 (`sub_agent.encolar_sub_agente`).
- **Comunicación**: los resultados se publican en el `Buzon` compartido
  (`resultado_sub_agente`, `sub_tarea_completada`) y cada sub-agente puede
  recibir mensajes previos a su ejecución (`enviar_mensaje`).
- **Seguridad**: cada rol solo ve sus herramientas (mínimo privilegio) y
  respeta los mismos permisos y confirmaciones que el agente principal.

> **Nota**: sin `--sub-agents` el pipeline multi-agente es idéntico al de
> siempre; la funcionalidad es 100 % opcional.


## 🔗 LSP e indexación global (v6.14.0)

SnapContext ahora se conecta a **Language Server Protocol (LSP)** para dar al
agente una comprensión profunda del código: resolución de definiciones,
referencias globales y tipos — la misma tecnología que usan Cursor, Claude Code
y los editores modernos.

```bash
# Activar el LSP (lanza el servidor perezosamente, solo al usarlo)
snapcontext --lsp "refactoriza la función parsear_datos"

# También con la variable de entorno
SNAPCONTEXT_LSP=1 snapcontext "arregla el bug del parser"
```

### Herramientas nuevas para el agente

| Herramienta | Descripción |
|-------------|-------------|
| `lsp_definicion(archivo, linea, columna)` | Resuelve dónde se define un símbolo (aunque esté importado de otro módulo). |
| `lsp_referencias(archivo, linea, columna)` | Encuentra **todas** las referencias a una función/clase en el proyecto. |
| `lsp_tipo(archivo, linea, columna)` | Devuelve el tipo de una variable/expresión (hover). |

### Servidores soportados (detectados con `shutil.which`)

| Lenguaje | Comando del servidor |
|----------|---------------------|
| Python | `pyright-langserver` o `pylsp` |
| JavaScript/TypeScript | `typescript-language-server` |
| Go | `gopls` |
| Rust | `rust-analyzer` |
| Java | `jdtls` |
| C# | `OmniSharp` |

### Caché de consultas

- **En memoria** (por sesión) y **persistente** en `~/.snapcontext/lsp_cache.db`
  (SQLite).
- Clave: `(archivo, linea, columna, tipo_consulta)`.
- Invalidación automática por **hash del contenido** del archivo: si cambia,
  la entrada caduca y se vuelve a consultar.

### Arquitectura

- `lsp_client.py`: cliente JSON-RPC puro sobre stdio (**sin dependencias
  obligatorias**); `CacheLSP` y `LSPClient` con contexto (`with`), timeouts y
  cierre ordenado (`shutdown` → `exit` → kill).
- Inicio **perezoso** (singleton): el servidor solo se lanza en la primera
  consulta, nunca al abrir la sesión.
- Integrado en el agente ReAct y en los sub-agentes dinámicos (v6.13.0) cuando
  `--lsp` está activo.
- Si el servidor no está instalado o falla: mensaje claro y **continúa sin
  LSP** (nunca bloquea la tarea).

```bash
# Servidor LSP de Python opcional (grupo [lsp])
pip install "snapcontext[lsp]"
```



### Instalación (opcional)

```bash
pip install 'snapcontext[browser]'
playwright install chromium
```

### Uso

```bash
# Activar el modo navegador para la tarea del agente (headless por defecto)
snapcontext --browser "revisa la UI en http://localhost:3000 y arregla el botón de login"

# Con interfaz visible (para depurar en vivo)
snapcontext --browser --browser-headed "comprueba que el dashboard renderiza bien"
```

Con `--browser` el agente ReAct dispone de estas herramientas MCP:

| Herramienta | Descripción |
|---|---|
| `browser_abrir(url, wait_for?, timeout?)` | Abre una URL (espera opcional a un selector). |
| `browser_screenshot(url?, full_page?, selector?)` | Captura de pantalla en base64 PNG (página completa o elemento). |
| `browser_click(selector)` / `browser_type(selector, texto)` | Interacción: clic y escritura en campos. |
| `browser_get_text(selector)` | Extrae el texto de un elemento. |
| `browser_analizar_imagen(imagen_base64, pregunta)` | Análisis visual con modelos de visión (Gemini 2.5 Pro, Claude 3.7 Sonnet+). |
| `browser_cerrar()` | Cierra el navegador y libera recursos. |

Notas:
- El navegador es **headless por defecto** (seguridad; no interfiere con tu
  escritorio) y se mantiene **persistente durante toda la tarea** (una única
  instancia; si muere, se reinicia automáticamente).
- El análisis visual (`browser_analizar_imagen`) requiere un modelo con
  visión; con otros modelos devuelve un error claro en lugar de fallar.
- Sin `--browser`, Playwright nunca se importa (carga perezosa) y el agente
  no dispone de herramientas de navegador.



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
pip install aider-chat                  # ediciones de código
snapcontext --init                      # asistente inicial + API key
```

## 🔍 Manejo de contexto inteligente (v6.1.0)

Los modelos locales (p. ej. `deepseek-r1:14b`, con 4096 tokens de contexto)
fallan constantemente al recibir archivos grandes. Con el editor propio
(`--editor propio`), SnapContext ahora gestiona el contexto automáticamente:

1. **Estima los tokens** del archivo antes de enviarlo (`tiktoken` si está
   disponible; si no, la regla práctica 1 token ≈ 4 caracteres).
2. Si supera `--max-context-tokens` (3000 por defecto), **extrae solo el bloque
   relevante** (la función/clase mencionada en la tarea, detectada con
   tree-sitter o regex) y lo envía junto a un resumen AST del resto:

   ```text
   ℹ Archivo grande (4458 tokens). Usando contexto selectivo (bloque: 'parsear_archivo')...
   ```

3. Si el proveedor falla por límite de contexto, **reintenta con el archivo
   completo** (el modelo real puede tener más contexto del estimado) y, si
   vuelve a fallar, **pasa a la siguiente estrategia**
   (AST → Parche → Sobrescritura).
4. Opcionalmente, con `--editor-fallback`, si el editor propio no puede editar
   un archivo **invoca Aider automáticamente** (si está instalado):

   ```text
   ⚠ El editor propio no pudo editar este archivo. Usando Aider como fallback...
   ```

### Flags

```bash
# Límite de tokens por petición (por defecto 3000)
snapcontext "renombra la función parsear_archivo" --editor propio --max-context-tokens 2000

# Fallback automático a Aider cuando el editor propio falla
snapcontext "arregla el parser" --editor propio --editor-fallback
```

El comportamiento es **opcional y compatible hacia atrás**: sin flags se usa
el umbral por defecto (3000 tokens) y no se invoca Aider.

## 🤖 Multi-agentes en paralelo (v6.0.0)

Para tareas de desarrollo complejas, SnapContext puede coordinar un **equipo de
agentes especializados** bajo un Supervisor:

| Rol | Descripción |
| --- | --- |
| 🧠 **Arquitecto** | Diseña la solución y genera un plan de alto nivel (objetivo, módulos y archivos a tocar) con la IA. |
| 💻 **Programador** | Implementa el código con el **editor propio** (cadena AST → Parche → Sobrescritura). |
| 🧪 **Tester** | Ejecuta las pruebas (detección automática de v5.3.0) y devuelve éxito/fallo + salida. |
| 🤖 **Supervisor** | Coordina el flujo: Arquitecto → Programador → Tester, con **bucle de realimentación** si una prueba falla. |

Los agentes se comunican a través de un **buzón de mensajes** (cola thread-safe)
y, por ahora, trabajan en *pipeline* (secuencial con transferencia de estado).

### Uso

```bash
snapcontext --multi-agent "añadir autenticación con Google"
export SNAPCONTEXT_MULTI_AGENT=1        # activar por defecto (PowerShell: $env:SNAPCONTEXT_MULTI_AGENT="1")
```

En modo interactivo el Supervisor muestra el plan y pide confirmación; con
`--auto` (`snapcontext --multi-agent --auto "…"`) ejecuta sin preguntar. El
modo es **opcional**: `--plan`, ReAct y el flujo clásico siguen intactos.

## 🧭 Verificación temprana del directorio (v5.6.0)

Al inicio, si el directorio actual no parece ser la raíz de un proyecto (sin
`package.json`, `go.mod`, `pyproject.toml`, `src/`, `tests/`, …), SnapContext
muestra un aviso y (en modo interactivo) te deja **continuar**, ejecutar la
**demo** o **salir**. Usa `--no-validar-proyecto` para saltarte este aviso en
directorios que no siguen la estructura típica.

```bash
snapcontext "tarea" --no-validar-proyecto
```

> Para empezar, ejecuta SnapContext en la raíz de tu proyecto o usa `--demo`
> para probar sin proyecto.

## 🐳 Sandboxing con Docker (v4.3.0)

Ejecuta los comandos de SnapContext (bucle de pruebas, `execute_command`,
pasos `ejecutar` del planificador, plugins…) dentro de un **contenedor Docker
aislado**. Ideal cuando no se confía en el código o necesitas dependencias
específicas.

### Uso básico

```bash
# Todo el trabajo de comandos dentro del contenedor
snapcontext "arreglar el checkout" --test-loop --sandbox

# Con imagen personalizada y comando de preparación
snapcontext "añadir tests" --test-loop --sandbox \
    --sandbox-imagen python:3.11-slim \
    --sandbox-comando "pip install pytest"

# En el planificador: pasos "ejecutar" y MCP con comandos usan el sandbox;
# "editar" y "consultar" siguen en el host.
snapcontext "migrar a pytest" --plan --sandbox

# Imagen por defecto sin flag:
export SNAPCONTEXT_SANDBOX_IMAGE=ubuntu:22.04   # PowerShell: $env:SNAPCONTEXT_SANDBOX_IMAGE="..."
```

### Cómo funciona

- **Detección**: `_docker_disponible()` comprueba `docker --version` (binario)
  y `docker info` (daemon activo).
- **Ejecución**: cada comando se lanza como
  `docker run --rm -v "<proyecto>:/workspace" -w /workspace -e GEMINI_API_KEY … <imagen> sh -c "<comando>"`.
- **Montaje**: el directorio del proyecto se monta en `/workspace`.
- **Variables de entorno**: las claves (`*_API_KEY`, `OLLAMA_URL`, …) se pasan
  al contenedor automáticamente.
- **Preparación**: `--sandbox-comando` antepone un comando de setup
  (`apt update && apt install -y …`) antes del comando principal.

### Qué corre dentro y fuera del sandbox

| Dentro del contenedor | Fuera (host) |
| --- | --- |
| Bucle de pruebas (`--test-loop`) | Herramientas de solo lectura (`grep`, `read_file`, `list_files`, `ast`) |
| MCP `execute_command` (+ background) | Pasos `editar` y `consultar` del planificador |
| Pasos `ejecutar` del planificador | Selección de contexto, chat con la IA |
| Plugins y herramientas de usuario | |

### Errores y logs

- Cada comando sandboxeado registra `ℹ [sandbox] Ejecutando en contenedor: …`.
- Si un comando falla dentro del contenedor se muestra su salida y código como
  siempre.
- Si Docker no está disponible y usaste `--sandbox` explícitamente,
  SnapContext falla con un mensaje claro; sin el flag nunca cambia nada
  (compatibilidad total).

## 🎮 Gateway de Omnicanalidad: Discord (v4.5.0)

Igual que Telegram, SnapContext puede recibir **Slash Commands** de Discord,
procesarlos con el motor interno y responder en el mismo canal. 100 % local
(self-hosted): solo `httpx` + `cryptography`, sin `discord.py`.

### Configuración

```bash
# 1) https://discord.com/developers/applications → tu app:
#    - General Information: copia PUBLIC KEY y APPLICATION ID.
#    - Bot: crea el bot y copia el TOKEN.
snapcontext discord setup --public-key <KEY> --app-id <ID> --token <BOT_TOKEN>

# 2) Expón el servidor (ngrok o VPS) y apunta el endpoint en el portal:
ngrok http 8001                      # si usas `snapcontext --api`
#  General Information → INTERACTIONS ENDPOINT URL:
#      https://<tu-dominio>/webhook/discord
#  Discord lo verifica con un PING; respondemos {"type": 1} automáticamente.

# 3) Arranca el servidor:
snapcontext --api        # o: snapcontext --web
```

Variables de entorno equivalentes (prioridad sobre `config.json`):
`DISCORD_PUBLIC_KEY`, `DISCORD_APPLICATION_ID`, `DISCORD_BOT_TOKEN`,
`DISCORD_WEBHOOK_URL` (webhook estándar de canal, alternativa).

### Comandos Slash soportados

- `/start` · `/help` → mensaje de bienvenida.
- `/snap <tarea>` → ejecuta el pipeline completo.
- `/fix <tarea>` → bucle de pruebas (equivale a `snapcontext fix`).
- `/plan <tarea>` → planificador (`snapcontext --plan`).

### Detalles técnicos

- **Verificación de firma Ed25519**: headers `X-Signature-Ed25519` /
  `X-Signature-Timestamp`; firma inválida → `401`. Sin `DISCORD_PUBLIC_KEY`
  → `503`.
- **Respuesta diferida**: el endpoint responde `{"type": 5}` (<3 s) y el
  agente trabaja con `asyncio.create_task`; el resultado llega como follow-up
  vía `/webhooks/{app_id}/{interaction_token}` (válido 15 min).
- **Mensajes largos**: >2000 caracteres se envían como archivo `.txt`.
- Módulo: `discord_gateway.py` · Endpoint: `web/app.py` (`/webhook/discord`).

## 📱 Gateway de Omnicanalidad: Telegram (v4.4.0)

SnapContext puede recibir mensajes por **Telegram**, procesarlos con su motor
de IA y responder en el mismo chat.

### Configuración

```bash
# 1) Crea un bot con @BotFather y configura token + webhook:
snapcontext telegram setup --token 123456:ABC-DEF --webhook-url https://mi-dominio.ngrok.io
snapcontext telegram estado              # ver configuración actual

# 2) Arranca el servidor (API o web); el webhook queda expuesto en:
#    POST /webhook/telegram
snapcontext --api        # o: snapcontext --web
```

También vale con variables de entorno (prioridad sobre `config.json`):
`TELEGRAM_BOT_TOKEN` y `TELEGRAM_WEBHOOK_URL`.

### Uso desde Telegram

- `/start` → mensaje de bienvenida.
- `arregla el login` → ejecuta el pipeline completo y responde con el resultado.
- `/fix <tarea>` → igual que `snapcontext fix` (bucle de pruebas).
- `/plan <tarea>` → igual que `snapcontext --plan`.

### Detalles técnicos

- **Webhook asíncrono**: el endpoint responde `200 OK` de inmediato (Telegram
  corta a los ~30 s) y procesa el mensaje con `asyncio.create_task`; sin token
  configurado responde `503`.
- **Motor interno**: la consulta se ejecuta con la API interna del núcleo
  (`flujo_principal` / `_ejecutar_planificador`) en un executor, nunca por
  subprocess del CLI.
- **Mensajes largos**: si la salida supera los 4096 caracteres de Telegram, se
  envía un resumen + la salida completa como documento `.txt`.
- Módulo: `telegram_gateway.py` · Endpoint: `web/app.py` (`/webhook/telegram`).

## 🧰 Instalación y onboarding sin fricción (v3.1.x)

Objetivo: instalar SnapContext y empezar a usarlo en menos de 5 minutos.

### Modo offline con Ollama por defecto
Si no hay ninguna API key (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
`DEEPSEEK_API_KEY`, `GROQ_API_KEY`, …), SnapContext usa **Ollama
automáticamente** eligiendo el modelo más ligero disponible (`llama3.2`,
`phi3`, …). Si tampoco hay Ollama, muestra un mensaje claro:

> No se encontró una API key ni Ollama. Puedes instalar Ollama desde
> <https://ollama.com> o configurar una API key con `snapcontext --init`.

### `snapcontext --diagnostico`
Revisa tu instalación con resumen en colores (verde OK / amarillo aviso /
rojo error):
- Versión de Python y si está en el PATH.
- Instalación de SnapContext (`python -m snapcontext --version`).
- Dependencias opcionales (`questionary`, `fastapi`, `sentence-transformers`, …).
- Estado del PATH (si `snapcontext` es accesible como comando).
- Memoria: integridad de la base SQLite y número de skills.
Cada problema incluye su solución sugerida.

### `snapcontext --reparar`
Arregla instalaciones rotas:
- Limpia entornos corruptos de `uv` (carpetas vacías en `~/.snapcontext`).
- Reinstala SnapContext con `pip`.
- Recrea la base SQLite si está corrupta (respaldando la anterior).
- Si el PATH no incluye la carpeta de scripts, ejecuta `--setup-path`.

### Tutorial y asistente mejorados
- `snapcontext --bienvenida`: tutorial interactivo de primeros pasos.
- **(v3.1.1)** `snapcontext` sin argumentos muestra una ayuda resumida y
  amigable con ejemplos (código 0), en lugar de un error.
- **(v3.1.1)** En el primer uso se ejecuta la bienvenida automáticamente:
  se registra en `~/.snapcontext/estado.json` (`"primer_uso": false`) para
  no repetirla; `--bienvenida` explícito permite volver a verla.
- `snapcontext --init` ahora también pregunta si quieres configurar Ollama
  (ofreciendo abrir <https://ollama.com>), crear un proyecto de prueba y
  ejecutar el tutorial interactivo.

### Instalador gráfico para Windows
El instalador NSIS (`installer.nsi`, generado con
`scripts/empaquetar_exe.ps1`) ahora:
- Detecta Python y guía al usuario (abre python.org si falta).
- Añade una sección opcional "Instalar/actualizar con pip" que deja el
  comando `snapcontext` disponible en cualquier terminal.
- Sigue añadiendo la carpeta al PATH y creando accesos directos.

### Extensión VS Code en TypeScript (v3.2.0)
La extensión vive en `vscode/` y está escrita en TypeScript:
- `vscode/src/extension.ts` — código fuente tipado (modo `strict`).
- `vscode/tsconfig.json` — ES2020, commonjs, salida en `out/`.
- Compilar: `cd vscode && npm install && npm run compile` (genera
  `out/extension.js`, que es el entry point del manifiesto).
- Empaquetar: `vscode/scripts/empaquetar.ps1` / `.sh` (compilan antes de
  ejecutar `vsce package`).

### Editor propio refinado (v3.3.0)
- **Detección de lenguaje robusta**: extensión + heurística por contenido
  (shebang, patrones de código) para proyectos mixtos.
- **Manejo de conflictos en parches**: validación previa del contenido del
  archivo y resolución automática línea a línea si `git apply`/`patch`
  fallan (con aviso de aplicación parcial); ya no se sobrescribe a ciegas.
- **Prompt de edición mejorado**: lenguaje, tamaño, resumen AST con
  posiciones y reglas de precisión (estilo conservado, cambios mínimos).
- **Aprendizaje de patrones**: las ediciones exitosas se guardan como skills
  (`editor-<patrón>`) y su estrategia se reutiliza automáticamente en
  tareas similares.

## 🚀 Inicio rápido (5 minutos)

```bash
# 1. Tarea directa con editor integrado propio
snapcontext "arregla el botón de pago" --editor propio

# 2. Planificador autónomo
snapcontext plan "añadir validación al formulario" --auto

# 3. Chat interactivo
snapcontext --chat

# 4. Interfaz web completa
snapcontext interactive  # o snapcontext --web
```

Auto-detección de proyecto (Flutter, Node/React, Python, Go, Rust…) que ajusta
carpetas, extensiones y comandos de test por defecto.

## 🛠️ Editor Integrado Propio (v2.2.0)

SnapContext incluye su propio editor integrado con soporte de parches unificados
y edición basada en AST (Árbol Sintáctico) para refactorizaciones precisas:
- **`--editor propio`** (por defecto desde v4.1.0): Aplica cambios directamente y crea copias de seguridad automáticas en `~/.snapcontext/backups/`.
- **Robustez transaccional (v4.6.0)**: edición multiarchivo atómica con rollback automático si algún archivo falla, backup obligatorio (la edición aborta si el backup falla) y fuzzy matching (`difflib.SequenceMatcher`) que tolera comentarios/espacios cambiados al aplicar parches.
- **Escalabilidad e impacto (v4.7.0)**: análisis previo de impacto con el grafo de dependencias (advierte de archivos afectados, permite añadirlos a la edición o abortar; con `--auto` solo avisa) y contexto selectivo para archivos grandes (>600 líneas): solo se envía al modelo el resumen AST y los bloques relevantes, nunca el archivo completo.
- **CLI profesional (v4.8.0)**: nueva capa `ui.py` sobre [Rich](https://github.com/Textualize/rich) — banner con tabla de comandos, barras de progreso en el escaneo, tabla de impacto por dependencias, diffs coloreados y prompts estilizados. Silencioso automático con `--auto` y degradación elegante sin `rich`.
- **`--modo-edicion {auto,parche,sobrescribir,ast}`**:
  - `auto` (por defecto): Genera un parche unificado (`git apply` / `patch`), pero
    para refactorizaciones estructurales intenta primero la edición **AST** y cae a
    sobrescritura si nada es aplicable.
  - `ast`: Edita comprendiendo la estructura sintáctica del archivo (Python con
    `ast`, otros lenguajes con `tree-sitter` si está instalado). Soporta renombrar
    símbolos, insertar imports y recibir el código completo resultante.
  - `parche`: Fuerza la aplicación de parches unificados para ediciones precisas.
  - `sobrescribir`: Sobrescribe el archivo completo.
- **`--editor aider`**: Mantiene el flujo de edición con Aider para máxima compatibilidad.

### Editor propio (modo por defecto desde v4.1.0)

Desde la v4.1.0, **el editor propio es el modo por defecto**, reduciendo la
dependencia de Aider sin eliminarla (`--editor aider` sigue disponible).

**Cadena de auto-reparación**: para cada archivo se intentan las estrategias en
orden — **AST → Parche → Sobrescritura** — informando cuál está en uso:

```
ℹ Editor propio: usando estrategia AST para 'modulo.py'...
ℹ Editor propio: usando estrategia PARCHE para 'modulo.py'...
```

Si todas fallan, verás un error claro con las estrategias intentadas, el motivo
y la sugerencia de probar con `--editor aider`. El fallo queda registrado en
`~/.snapcontext/logs/editor_fallos.log` para depuración.

**Resolución interactiva de conflictos**: si un parche no se aplica limpiamente,
se ofrece elegir entre `[a]plicar de todas formas`, `[v]er el diff`,
`[r]eintentar con el proveedor de IA` o `[c]ancelar conservando el original`.
En modo `--auto` no se pregunta: se salta automáticamente a la siguiente
estrategia. Funciona igual desde la CLI que desde `--chat`.

### 🐳 Persistencia de Docker por sesión (v6.4.0)

Por defecto cada comando se ejecuta en un contenedor efímero
(`docker run --rm`), lo que impide flujos que compartan estado entre comandos.
Con `--sandbox-session`, SnapContext mantiene **un único contenedor vivo**
durante toda la tarea:

```bash
# Plan de varios pasos con un solo contenedor (npm install → npm test comparten estado)
python snapcontext.py --auto --sandbox "npm install && npm test" --sandbox-session "actualiza la dependencia X y ejecuta los tests"

# Bucle ReAct con sesión persistente
python snapcontext.py --react "instala pytest y arregla los tests" --sandbox --sandbox-session
```

Cómo funciona:

- La sesión se crea de forma **perezosa** en el primer comando
  (`docker run -d --name snap-session-<id> -v "<proyecto>:/workspace"
  -w /workspace <imagen> tail -f /dev/null`) y se reutiliza para **todos**
  los comandos posteriores mediante `docker exec`.
- El id se guarda en `~/.snapcontext/session_id.txt`.
- Al finalizar la tarea (éxito, aborto o error), e incluso al pulsar
  `Ctrl+C`, la sesión se **destruye automáticamente** para no dejar
  contenedores huérfanos.
- Si una ejecución anterior dejó huérfanos, límpialos con:
  `python snapcontext.py --sandbox-session-clean` (pedirá confirmación; en
  `--auto` los elimina sin preguntar).
- Compatibilidad: sin `--sandbox-session` el comportamiento es exactamente el
  de siempre (`docker run --rm` por comando). El contenedor de sesión monta
  **solo** el directorio del proyecto en `/workspace`.

### 📝 Mejora del editor de parches (v6.3.0)

El editor de parches es ahora mucho más tolerante gracias al **fuzzy matching real**
y a la **resolución automática de conflictos**. Cuando `git apply`/`patch` fallan y
la resolución incremental no encuentra una coincidencia exacta, se prueban, en
orden y solo cuando la anterior falla:

1. **Coincidencia exacta** cerca de la posición declarada del hunk.
2. **Coincidencia por variantes**: tolera reindentaciones y comentarios
   añadidos/eliminados (espacios colapsados y sin comentario final `#`/`//`).
3. **Emparejamiento difuso real** con `difflib.SequenceMatcher`: busca la mayor
   similitud de las líneas de contexto (ratio medio ≥ 0.85, ≥ 0.90 por línea),
   tolerando comentarios, espacios y **variables renombradas**.
4. **Resincronización a nivel de bloque**: si nada encaja, busca en todo el archivo
   la ventana más parecida al bloque original (≥ 0.80) y la reemplaza conservando
   las líneas de contexto locales del usuario.

Estos umbrales (`UMBRAL_DIFUSO_HUNKS`, `UMBRAL_DIFUSO_LINEA`,
`UMBRAL_DIFUSO_BLOQUE`) solo se consultan cuando un hunk falla: el caso normal no
paga el coste de `SequenceMatcher`. Si un hunk sigue sin encajar, la operación se
aborta **sin tocar nada** (todo-o-nada) y verás un mensaje claro con una sugerencia
accionable.

> **`--mostrar-diff`**: muestra el diff propuesto (coloreado) antes de aplicarlo y
> pregunta si aplicarlo (`[a]`), cancelarlo (`[c]`) o editarlo manualmente (`[e]`).
> En modo `--auto` muestra el diff sin bloquear. Sin el flag el parche se aplica
> sin preguntar, exactamente como siempre.

```bash
# Revisar cada parche antes de aplicarlo
snapcontext "añadir endpoint de métricas" --editor propio --mostrar-diff

# Comportamiento por defecto: aplica sin preguntar
snapcontext "añadir endpoint de métricas" --editor propio
```

**Optimizado para modelos locales (Ollama)**: si el proveedor es Ollama, el
editor usa automáticamente **prompts concisos** en sus tres estrategias (menos
contexto e instrucciones directas). También puedes forzarlos en cualquier
proveedor con `--modelo-ligero` si sabes que usas un modelo pequeño.

```bash
# Edición precisa mediante diffs unificados
snapcontext "añadir endpoint de métricas" --editor propio

# Forzar modo parche
snapcontext "corregir tipado en login" --editor propio --modo-edicion parche

# Refactorización estructural con AST
snapcontext "renombrar la variable 'usuario' a 'cuenta'" --editor propio --modo-edicion ast

# Validación de sintaxis desactivada (comportamiento previo a v3.4.0)
snapcontext "corregir tipado" --editor propio --no-validar-sintaxis
```

### Validación de sintaxis antes de guardar (v3.4.0)

Cuando uses `--editor propio`, SnapContext **valida la sintaxis** del código que
ha generado la IA **antes de escribirlo en disco**. Si la validación falla, el
cambio se rechaza y se le envía el error al proveedor para que lo corrija,
repitiéndose hasta `--max-intentos-validacion` intentos (por defecto 3). Si tras
esos intentos el código sigue roto, se **cancela la edición** y el archivo queda
intacto.

- Se detecta el lenguaje del archivo y se usa el validador adecuado:
  Python (`py_compile`), JavaScript/TypeScript (`node --check`), Dart
  (`dart analyze` → `dart format`), Go (`go build -n` → `gofmt -e`), Rust
  (`rustc --parse-only`), Java (`javac -Xlint:none`), C/C++ (`gcc`/`clang
  -fsyntax-only`). Si no hay validador o comando disponible, se omite la
  validación (solo aviso en logs con `--depurar`).
- La validación se hace sobre un **archivo temporal**, nunca toca el original.
- Flag `--validar` (por defecto activado) / `--no-validar-sintaxis` para
  desactivarla. Nota: el nombre usa `--no-validar-sintaxis` porque
  `--no-validar` ya es alias de `--iniciar-proyecto`.

```bash
# Desactivar la validación por completo (comportamiento anterior)
snapcontext "..." --editor propio --no-validar-sintaxis

# Ajustar el número de reintentos
snapcontext "..." --editor propio --max-intentos-validacion 5
```

### 🧠 Asesor de código proactivo (v3.5.0)

SnapContext no solo ejecuta órdenes: también **analiza tu código por su cuenta**
y te sugiere mejoras (refactorizaciones, deuda técnica, optimizaciones).

```bash
# Análisis informativo (nunca modifica código)
snapcontext --asesor
snapcontext --sugerir          # alias

# Aplicar automáticamente las mejoras seguras (renombrar símbolos);
# cada cambio se valida antes de guardarse y se descarta si rompe la sintaxis
snapcontext --asesor-auto

# Ajustar la sensibilidad del detector de funciones largas (defecto: 20)
snapcontext --asesor --asesor-umbral 30
```

**Qué detecta:**

- 🔴 **Prioridad alta**: `except:` desnudos (capturan todo sin querer).
- 🟡 **Prioridad media**: funciones largas (> 20 líneas), clases con demasiadas
  responsabilidades (> 10 métodos), bloques de código duplicados entre archivos,
  comparaciones `== None` y patrones obsoletos (`.has_key()`, Python 2).
- 🔵 **Prioridad baja**: nombres poco descriptivos (`d`, `tmp`...) con una
  sugerencia concreta de renombrado.

Cada sugerencia incluye descripción, ubicación `archivo:línea`, solución
propuesta y prioridad. Los umbrales se personalizan en
`~/.snapcontext/config.json`:

```json
{ "asesor": { "funcion_larga": 30, "clase_metodos": 15, "duplicado_lineas": 8 } }
```

El asesor también está disponible:

- **En el chat**: comando `/asesor` (o `/sugerir`) dentro de `--chat`.
- **En el planificador**: paso `{"accion": "asesor", "descripcion": "..."}` —
  cada sugerencia se presenta para aceptarla o rechazarla individualmente.
- **En la web**: acción *Asesor* que muestra las sugerencias en un panel.

### 🛡️ Seguridad y rendimiento en el asesor (v4.2.0)

Con el nuevo flag `--asesor-profundo`, el asesor añade dos análisis extra a
las heurísticas básicas de siempre:

```bash
snapcontext --asesor-profundo          # heurísticas + seguridad 🔒 + rendimiento ⚡
snapcontext --asesor                   # solo heurísticas básicas (como v3.5.0)
```

**🔒 Vulnerabilidades detectadas** (heurísticas propias, sin dependencias
externas como bandit):

| Vulnerabilidad | Ejemplo |
|---|---|
| Inyección SQL | `"SELECT ... WHERE id = " + user_id` |
| Command injection | `os.system("ping " + ip)`, `subprocess(..., shell=True)` |
| Path traversal | `open("../" + filename)` |
| Hardcoded secrets | `API_KEY = "sk-..."` en el código |
| eval/exec inseguros | `eval(user_input)` |
| XSS | `elemento.innerHTML = entrada` |

**⚡ Rendimiento**: bucles anidados O(n²), `range(len(...))`, concatenación de
cadenas con `+=` dentro de bucles, consultas N+1 del ORM y lectura completa de
archivos grandes en memoria.

Cada hallazgo incluye descripción, `archivo:línea`, solución propuesta y
prioridad, integrado con el resto de sugerencias del asesor.

También disponibles por separado:

```bash
# En el chat
snapcontext --chat     # luego: /seguridad  ·  /rendimiento

# En el planificador
{"accion": "seguridad", "descripcion": "auditar el módulo de pagos"}
{"accion": "rendimiento", "descripcion": "optimizar consultas"}
```

### 🔌 API pública (v3.6.0)

SnapContext expone una **API REST** para interactuar de forma programática
(desde scripts, CI/CD u otros sistemas). Requiere las dependencias web:

```bash
pip install snapcontext[web]

# Arrancar la API en http://127.0.0.1:8001 (docs en /docs y /redoc)
snapcontext --api

# Opciones de configuración
snapcontext --api --api-puerto 9000 --api-host 0.0.0.0 --api-token mi-clave

# Generar (y guardar) una API key segura sin arrancar el servidor
snapcontext --api-generate-key
```

**Autenticación**: todos los endpoints exigen la API key en el header
`X-API-Key` (o como query param `api_key`), salvo `/health`, `/docs` y
`/redoc`. Si no hay clave configurada, al arrancar `--api` se genera una
automáticamente y se guarda en `~/.snapcontext/config.json` (`"api_key"`).

| Endpoint | Método | Descripción |
|---|---|---|
| `/api/v1/health` | GET | Estado del servidor (público). |
| `/api/v1/query` | POST | Consulta asíncrona → `202` + `task_id`. |
| `/api/v1/plan` | POST | Plan asíncrono → `202` + `task_id`. |
| `/api/v1/chat` | POST | Mensaje al proveedor de IA (síncrono). |
| `/api/v1/skills` | GET | Skills aprendidas (`?archivados=true`). |
| `/api/v1/daemon` | POST | Daemon: `{"accion": "estado"\|"iniciar"\|"detener"}`. |
| `/api/v1/tasks/{task_id}` | GET | Estado de una tarea asíncrona. |

**Ejemplo de uso:**

```bash
CLAVE=$(python -c "import json;print(json.load(open('~/.snapcontext/config.json'))['api_key'])")

# Lanzar una consulta (no bloquea)
curl -X POST http://127.0.0.1:8001/api/v1/query \
     -H "X-API-Key: $CLAVE" -H "Content-Type: application/json" \
     -d '{"consulta": "revisar el login", "directorio": "."}'
# → {"task_id": "...", "estado": "pendiente", "url": "/api/v1/tasks/..."}

# Consultar el estado hasta que esté completada
curl -H "X-API-Key: $CLAVE" http://127.0.0.1:8001/api/v1/tasks/<task_id>

# Chat síncrono con contexto conversacional
curl -X POST http://127.0.0.1:8001/api/v1/chat \
     -H "X-API-Key: $CLAVE" -H "Content-Type: application/json" \
     -d '{"mensaje": "¿qué hace este proyecto?"}'
```

### 🧩 Ecosistema de plugins (v4.0.0)

La comunidad puede extender SnapContext con **plugins**: carpetas en
`~/.snapcontext/plugins/<nombre>/` con un `plugin.json` y sus scripts.
Las herramientas que exponen se integran automáticamente en el sistema MCP.

**Estructura de un plugin:**

```
~/.snapcontext/plugins/saludos/
├── plugin.json     # metadatos + herramientas + permisos
├── saluda.py       # script(s): args JSON por stdin → JSON por stdout
└── README.md
```

```json
{
  "nombre": "saludos", "version": "1.0.0", "autor": "tu-usuario",
  "descripcion": "Herramientas de ejemplo",
  "permisos": ["archivos"], "habilitado": true,
  "herramientas": [
    { "nombre": "saludos_hola", "descripcion": "Saluda",
      "script": "saluda.py", "requiere_permiso": false,
      "parametros": {"nombre": "str"} }
  ]
}
```

**Gestión desde la CLI:**

```bash
snapcontext plugin list                    # listar plugins y herramientas
snapcontext plugin create mi-plugin        # generar estructura básica
snapcontext plugin install usuario/repo    # instalar desde GitHub
snapcontext plugin install ./mi-plugin     # instalar desde carpeta local
snapcontext plugin disable mi-plugin       # deshabilitar sin borrar
snapcontext plugin enable mi-plugin
snapcontext plugin update mi-plugin        # reinstalar desde su origen
snapcontext plugin remove mi-plugin        # desinstalar
```

**Seguridad**: antes de instalar una fuente externa se pide confirmación,
mostrando autor, versión y **permisos declarados** (`archivos`, `red`,
`red_escrita`, `ejecucion`, `entorno`). Las herramientas se ejecutan por
subproceso con timeout de 120 s. Un sandbox opcional con Docker está previsto
para versiones futuras.

**En el chat**: `/plugin` lista los plugins; para ejecutar una herramienta:
`/plugin saludos.saludos_hola '{"nombre": "Ada"}'`.

**En la web**: acciones `plugins`, `plugin_install` y `plugin_remove`
emiten el evento `{"tipo": "plugins", ...}` para el panel correspondiente.

**Publicar un plugin en el repositorio de comunidad**: crea un repositorio
público en GitHub cuyo contenido sea la carpeta del plugin (con `plugin.json`
en la raíz, rama `main`) y añádelo al índice del repo
[NicolasBruna24/snapcontext-plugins](https://github.com/NicolasBruna24/snapcontext-plugins).
A partir de ahí cualquiera podrá hacer `snapcontext plugin install <nombre>`.
Sin plugins instalados, SnapContext funciona exactamente igual que siempre.

## 🧭 Modos y alias

| Modo | Comando |
|------|---------|
| Tarea | `snapcontext "<consulta>"` (+ `--test-loop`) |
| Editor propio | `--editor propio` (por defecto desde v4.1.0, con backups automáticos) |
| Aider (opcional) | `--editor aider` (flujo clásico con Aider) |
| Prompts para modelos pequeños | `--modelo-ligero` (automático con Ollama) |
| Vista previa / revisión | `--vista-previa` · alias `review` |
| Chat interactivo | `--chat` |
| Planificador | `--plan "<tarea>"` |
| Autónomo | `--plan "<tarea>" --auto` |
| Demo / Web | `--demo` · `--web` (alias `interactive`) |
| Memoria e historial | `--init-claude` · `--historial` / `--historial-limpiar` |

Alias: `fix` (= `--test-loop`) · `review` · `server` (= `--server-loop`).

## 🤖 Modo autónomo (v0.17.0)

Añade `--auto` al planificador para ejecución sin supervisión:

```bash
snapcontext --plan "actualizar dependencias y pasar tests" --auto --no-confirmar
snapcontext --plan "refactorizar el módulo de pagos" --auto   # con permisos guardados
```

- Salta la confirmación del plan y el menú paso a paso.
- Cada paso fallido se **reintenta automáticamente hasta 3 veces**; si sigue
  fallando, continúa con el siguiente y lo refleja en el resumen final.
- **Sigue respetando `~/.snapcontext/permisos.json`**: los tipos marcados como
  "nunca" se deniegan sin preguntar. Con `--no-confirmar` no hay diferencia
  adicional (todas las confirmaciones ya están desactivadas).

## 🧩 Extensión VS Code (v0.16.0)

SnapContext se integra en VS Code como extensión nativa (`vscode/`), reutilizando la interfaz web y el orquestador existentes.

**Instalación** (requiere Node.js y `snapcontext` instalado en el Python del sistema):

```powershell
./vscode/scripts/empaquetar.ps1          # genera el .vsix
code --install-extension vscode/snapcontext-vscode-1.0.0.vsix
```

**Comandos** (paleta de comandos, prefijo `SnapContext:`):

| Comando | Descripción |
|---------|-------------|
| *Abrir chat* | Arranca el servidor web y muestra la interfaz en una webview |
| *Ejecutar consulta* | Pide la consulta y la ejecuta con el workspace abierto |
| *Planificar* | Ejecuta `--plan` mostrando el progreso en el canal de salida |
| *Configurar API key* | Guarda la clave en los settings del workspace |
| *Añadir al contexto* | Clic derecho en archivos del explorador → contexto visual |

**Configuración** (`settings.json`): `snapcontext.pythonPath`, `snapcontext.provider`, `snapcontext.apiKey`, `snapcontext.confirmar`.

Los logs del orquestador aparecen en tiempo real en el canal de salida **"SnapContext Output"**, con el workspace abierto como directorio del proyecto. La webview del chat reutiliza `web/static/index.html` (copia en `vscode/webview/`) servida por `servidor_webview.py`.

## 📄 Memoria de proyecto (v0.15.0)

SnapContext usa un archivo **`CLAUDE.md`** (o `SNAPCONTEXT.md`) en la raíz del
proyecto como memoria persistente: objetivo, tecnologías, estructura,
convenciones y comandos útiles. Se carga automáticamente al inicio de
cualquier modo y se inyecta como contexto del agente (chat, planificador).

Genera la memoria inicial con:

```bash
snapcontext --init-claude     # escanea el proyecto y la redacta con IA
```

Sin conexión o sin API key, genera una plantilla básica offline que puedes
completar a mano. Tras tareas/planes exitosos, SnapContext propone (con tu
confirmación) actualizar la memoria con lo aprendido.

En el chat: `/claude` muestra el contenido; `/context` muestra memoria +
archivos en contexto.

## 🛠 Herramientas MCP (v0.14.0)

El agente puede usar herramientas externas con resultados estructurados:

| Herramienta | Descripción | Permiso |
|-------------|-------------|---------|
| `grep` | Buscar patrón en el código (ripgrep `rg` preferente — ultrarrápido y respeta `.gitignore`; fallback a grep/findstr) | no |
| `read_file` | Leer archivo completo o rango de líneas | no |
| `list_files` | Listar archivos con filtro de extensión | no |
| `ast` | Extraer imports/clases/funciones de un `.py` | no |
| `ast_avanzado` (v1.4.0) | Análisis sintáctico multi-lenguaje con tree-sitter: funciones, clases, imports y llamadas. Fallback a `ast` (solo Python). Extra opcional: `pip install snapcontext[mcp_avanzado]` | no |
| `semantic_search` (v1.4.0) | Búsqueda semántica por embeddings integrada en MCP; el agente la usa automáticamente como contexto. Requiere `pip install snapcontext[embeddings]` | no |
| `git_status` | Rama actual y cambios sin commitear | no |
| `git_diff` | Diff sin commitear | no |
| `execute_command` | Ejecutar cualquier comando shell | **sí** |

En el chat:

```text
/tools                          # listar herramientas disponibles
/tool grep login                # forzar una búsqueda
/tool read_file lib/login.dart  # leer un archivo
/tool execute_command flutter test   # pide confirmación (🔒)
```

Además, mensajes como *"busca donde se usa checkout"* o *"¿cuál es el estado de
git?"* hacen que el agente ejecute automáticamente las herramientas de lectura
pertinentes y use su salida como contexto para responder. Puedes definir tus
propias herramientas en `~/.snapcontext/mcp_tools.json`:

```json
{"tools": [{"nombre": "build", "descripcion": "Compilar el proyecto",
            "comando": "npm run build", "requiere_permiso": true}]}
```

El planificador (`--plan`) también usa estas herramientas de solo lectura para
explorar el proyecto antes de proponer pasos.

## 🔒 Permisos y confirmaciones (v0.13.0)

Por defecto SnapContext **pide permiso** antes de acciones sensibles:

- Pasos del planificador (`--plan`): editar, ejecutar y consultar.
- Comandos `/run` y `/edit` del chat.

Pregunta `¿Permitir esta acción? (s/n/t/a)` con estas opciones:

| Tecla | Efecto |
|-------|--------|
| `s`   | Permitir solo esta vez |
| `n`   | Saltar esta acción |
| `t`   | Permitir **todas** las acciones de este tipo (se recuerda) |
| `a`   | No permitir ninguna de este tipo (se recuerda) |

Las preferencias se guardan en `~/.snapcontext/permisos.json`. Para volver a
que pregunte, borra ese archivo o usa `--init`. Para modo automático (CI,
scripts) desactiva todas las preguntas con `--no-confirmar`:

```bash
snapcontext --plan "tarea" --no-confirmar
```

## 🗺️ Planificador de tareas (v0.12.0)

`snapcontext --plan "añadir login con Google"` convierte SnapContext en un agente:

1. El proveedor de IA descompone la tarea en pasos JSON:
   ```json
   {"descripcion": "...", "accion": "editar|ejecutar|consultar|mcp",
    "archivos": ["..."], "comando": "...",
    "dependencias": [1, 2],
    "condicion": "archivo_existe('src/main.py')"}
   ```
2. Los pasos se muestran numerados y se pide confirmación.
3. Cada paso se ejecuta secuencialmente: **editar** usa el pipeline completo
   (`_planificar` + `_bucle_test`), **ejecutar** lanza comandos shell,
   **consultar** pregunta al proveedor y **mcp** (v2.3.0) ejecuta una
   herramienta MCP guardando su resultado en el contexto del plan.
4. Tras cada paso eliges: continuar / reintentar / saltar / abortar. Al final
   hay un resumen que se guarda en `historial.json`.

### Dependencias, condiciones y paralelismo (v1.4.0)

- **Dependencias**: un paso con `"dependencias": [1, 3]` solo se ejecuta si los
  pasos 1 y 3 tuvieron éxito. Si alguno falló o se saltó, el paso queda como
  `saltado`.
- **Condiciones**: campo `"condicion"` con una de estas funciones:
  - `archivo_existe('src/main.py')`
  - `archivo_contiene('src/main.py', 'def main')`
  - `comando_exito('flutter test')`
  - `variable_existe('mi_variable')` *(v2.3.0)*

  Además, desde v2.3.0 acepta **comparaciones dinámicas** con resultados
  de pasos previos o variables dejadas en el contexto (por pasos `mcp`):

  ```json
  {"condicion": "pasos[0].resultado == 'ok'"}
  {"condicion": "resultados.mi_variable != ''"}
  ```

  Si la condición es falsa, el paso se salta (con aviso) y el plan continúa.
### Pasos MCP y contexto dinámico (v2.3.0)

Un paso con `"accion": "mcp"` ejecuta una herramienta MCP del registro
(`grep`, `read_file`, `list_files`, `ast`, `git_status`, `git_diff`,
`execute_command`, ...) usando los campos `"herramienta"` y `"args"`.
El resultado queda disponible para los pasos posteriores:

```json
 {"descripcion": "buscar referencias", "accion": "mcp",
  "herramienta": "grep", "args": {"patron": "def main"},
  "variable": "coincidencias"}
 {"descripcion": "usar resultado", "accion": "ejecutar",
  "comando": "echo {{coincidencias}}"}
```

- El resultado siempre se guarda también como `{{resultado}}`; si el paso
  define `"variable"`, se guarda bajo ese nombre.
- `execute_command` soporta `background=true` (devuelve un `pid`
  consultable con `execute_command_status`) y `capture_output=false` para
  mostrar la salida en tiempo real.

## 🧠 Aprendizaje autónomo: memoria SQLite, skills y curador (v3.0.0)

SnapContext ya no solo ejecuta tareas: **aprende de ellas**. Toda la
memoria avanzada vive en una base de datos SQLite (`~/.snapcontext/memoria.db`; sin dependencias externas: `sqlite3` viene con Python).

### Skills: procedimientos reutilizables

- Al terminar una tarea con `--plan` **con éxito**, SnapContext extrae
  los pasos clave y guarda un *skill* (procedimiento reutilizable) en
  la tabla `skills`, junto a su contexto (archivos, comandos) y
  metadatos (fecha, usos, fallos, confiabilidad).
- Cuando repites una tarea similar, busca el skill más parecido con
  similitud semántica (embeddings; fallback Jaccard/contención sin
  dependencias) y lo reutiliza como punto de partida.
- Si el skill falla, se penaliza y queda marcado para revisión; si
  tiene éxito repetidamente (3+ usos sin fallos) se marca como
  **confiable** y se prioriza.

Comandos útiles:

```bash
snapcontext --skills          # lista los skills aprendidos
snapcontext --curador         # ejecuta una pasada del curador
snapcontext --daemon          # daemon: curador + cola de skills
snapcontext --daemon-intervalo 24   # curador cada 24 h
snapcontext --plan "..." --sin-aprendizaje  # desactiva el aprendizaje
```

Ejemplo de aprendizaje:

```bash
snapcontext --plan "migrar pagos a stripe" --auto --no-confirmar
# → al terminar (código 0): '[aprendizaje] Nuevo skill guardado:
#    migrar-pagos-a-stripe'
snapcontext --plan "migrar pagos a stripe ya" --auto --no-confirmar
# → reutiliza y refuerza el skill existente (sube su confiabilidad)
```

### Curador autónomo

- Archiva skills sin uso durante más de 30 días.
- Fusiona skills casi idénticos (similitud ≥ 0.90), conservando el más
  usado y sumando sus estadísticas.
- Notifica por CLI los skills con baja confiabilidad (< 0.4).
- Se ejecuta manualmente (`--curador`) o periódicamente vía daemon.

### Daemon (`--daemon`)

Proceso en segundo plano que cada minuto sondea la memoria: ejecuta el
curador cuando vence el intervalo configurado (`--daemon-intervalo`,
168 h por defecto = semanal) y procesa la cola de skills pendientes
(tabla `cola`) para ejecutarlos sin intervención. La CLI y el chat
encolan skills con `_cola_encolar()`; el agente `AgenteAprendizaje`
(en `agentes.py`) expone toda esta memoria al orquestador.

- **Paralelismo**: con `--paralelo N` (junto a `--auto`) se ejecutan hasta N
  pasos independientes a la vez; los logs llevan el identificador `[paso N]`. Desde v2.3.0 el planificador también respeta las dependencias dinámicas: un paso cuya condición usa una variable que otro paso aún no ha producido espera a que esté disponible:

  ```bash
  snapcontext --plan "tarea grande" --auto --paralelo 3 --no-confirmar
  ```

Opciones git:

```bash
snapcontext --plan "migrar a null-safety" --branch fix/null-safety   # rama nueva
snapcontext --plan "..." --no-git-commit                             # sin commits por paso
snapcontext --plan "..."                                             # commits 'paso: ...' activados
```

## 🤖 Curador Proactivo (v5.0.0)

A partir de v5.0.0, el curador de skills no solo **limpia**: también
**refactoriza de forma autónoma** (estilo Hermes). Un motor en segundo plano
evalúa la calidad de cada skill y mejora los prompts sin intervención humana.

### Cómo funciona

1. **Métricas**: cada ejecución de un skill registra éxito/fallo, tokens
   consumidos y tiempo (medias móviles) en SQLite (`exitos`, `fallos`,
   `tokens_promedio`, `tiempo_promedio_ms`, `ultimo_uso`, `version`).
2. **Evaluación**: un skill es *candidato* si tiene ≥ 3 usos y su tasa de
   fallos supera el 20 % o su consumo promedio de tokens supera el umbral.
3. **Refactorización**: se pide al LLM (tu proveedor por defecto) que reescriba
   el prompt para que sea más claro, conciso y robusto.
4. **Prueba en sandbox**: el candidato se valida con la suite de pruebas
   ejecutada dentro del sandbox Docker de v4.3.0 (`--sandbox`).
5. **Adopción**: solo si pasa las pruebas Y reduce tokens se guarda la nueva
   versión; la anterior queda archivada en la tabla `historial_skills`.
   **Nunca se toca el código del usuario**, solo los prompts de los skills.

### Control manual

```bash
snapcontext curador estado       # métricas: usos, fallos, tokens, candidatos
snapcontext curador ejecutar     # pasada manual del motor
snapcontext curador desactivar   # apagar el curador proactivo (persistente)
snapcontext curador activar      # reactivarlo
```

### Daemon automático

Un hilo demonio ejecuta una pasada cada `CURADOR_INTERVALO_HORAS` horas
(6 por defecto). No bloquea nunca el CLI y se puede desactivar con
`CURADOR_DAEMON=0`. En modo interactivo pregunta antes de aplicar cambios;
en `--auto` aplica directamente.

## 🧠 Modo ReAct — Razonamiento Dinámico (default desde v5.2.0)

A partir de **v5.2.0, ReAct (razonamiento dinámico) es el modo por defecto**
para cualquier consulta. El planificador estático se mantiene como **`--plan`
modo legacy** para scripts/integraciones que lo requieran.

```bash
snapcontext "arregla el login de Google y pasa los tests"          # ← ReAct
snapcontext "refactoriza modulo.py" --auto --react-max-iter 25    # ← ReAct
snapcontext --plan "migrar a pytest"   # ← planificador estático (legacy)
```

> `snapcontext "tarea"` **sin flags** ejecuta ReAct por defecto. Usa `--plan`
> para el planificador estático (ver `tests/test_react_510.py` →
> `test_sin_flags_ejecuta_react`).

A diferencia del planificador `--plan` —que genera una lista estática de pasos
en JSON y los ejecuta en orden—, el motor **ReAct** es dinámico: tras **cada
acción** el agente **observa** el resultado real (stdout, código de retorno,
diff…) y **decide** el siguiente paso, adaptándose en tiempo real.

### Cómo funciona

1. El agente envía al LLM un prompt de sistema con las herramientas y la tarea.
2. El LLM responde con **JSON estricto**: `{"pensamiento", "accion",
   "argumentos"}`. Si el JSON no es válido, se reintenta con un prompt
   correctivo hasta 3 veces.
3. La acción se ejecuta con una herramienta real y su salida se convierte en
   una observación legible que vuelve al historial.
4. Cuando la tarea está lista, el agente emite `"accion": "finalizar"` con un
   resumen (código de salida 0).

### Herramientas

| Acción | Descripción |
|---|---|
| `editar_archivo(ruta, contenido)` | Editor propio (con copia de seguridad); devuelve el diff aplicado |
| `ejecutar_pruebas(archivo?)` | Comando de pruebas (auto-detectado o `SNAPCONTEXT_COMANDO_TEST`) |
| `buscar_codigo(patron)` | Búsqueda regex sobre archivos de texto |
| `ejecutar_comando(comando)` | Shell; **respeta `--sandbox`** automáticamente |
| `leer_archivo(ruta)` | Lectura truncada a 8 KB |

### Seguridad y control

- Las rutas se validan: siempre dentro del proyecto (nada de `../../etc/...`).
- `--sandbox`: todos los comandos de shell corren en el contenedor Docker.
- Máx. 15 iteraciones (`--react-max-iter N`) para evitar bucles infinitos.
- En modo interactivo pregunta antes de cada acción (continuar/abortar/saltar);
  con `--auto` aplica todo sin preguntar.

### Gestión de contexto

Si el historial crece demasiado (> ~8000 tokens estimados, configurable con
`REACT_UMBRAL_RESUMEN_TOKENS`), el agente pide al LLM que **resuma** la
conversación y continúa desde ese resumen, manteniendo el coste bajo control.


> ### 📜 Modo legacy `--plan` (planificador estático)
> `snapcontext --plan "tarea"` descompone la tarea en pasos JSON con IA
> y los ejecuta secuencialmente (continuar/reintentar/saltar). Útil cuando
> necesitas un orden de ejecución predefinido o mantener compatibilidad con
> scripts.

## ✨ Novedades v0.10.0
### Notificaciones

Si tienes configurado Telegram (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`) o
Discord (`DISCORD_WEBHOOK_URL`), recibirás:
*"🔧 Skill 'refactorizar_api' mejorado (v2). Tokens reducidos un 30 %."*


- **Claude (Anthropic) como proveedor de IA**: `snapcontext "..." --provider anthropic`
  (requiere `ANTHROPIC_API_KEY` y `pip install snapcontext[anthropic]`). Modelo por
  defecto: `claude-3-5-sonnet-20241022`.
- **Modo chat interactivo**: `snapcontext --chat` abre un REPL con los comandos
  `/salir`, `/archivos`, `/limpiar`, `/seleccion <consulta>`, `/asesor`,
  `/plugin`,
  `/provider <proveedor>`, `/historial` y `/ayuda`. Cualquier otro texto se envía
  al proveedor actual manteniendo la conversación.
- **Memoria persistente**: cada tarea se guarda en `~/.snapcontext/historial.json`
  (fecha, consulta, archivos, resultado y duración). Consulta con
  `snapcontext --historial` y borra con `snapcontext --historial-limpiar`.
- **Base para el agente autónomo**: nuevas utilidades `_leer_archivo(ruta)` y
  `_ejecutar_comando(comando, directorio)` disponibles para el chat y futuros
  planificadores.
- **Chat como REPL de comandos de agente**: desde `--chat` puedes ejecutar
  `/run <comando>` (shell), `/read <archivo>`, `/explore <tema>` (búsqueda con
  rg/grep/findstr), `/fix | /review | /server <mensaje>` (alias del pipeline),
  `/edit <archivo>` (VSCode/nano/notepad/$EDITOR), `/context` (contexto actual),
  `/search <consulta>` (búsqueda semántica de archivos), `/buscar <consulta>`
  (alias de /search), `/grafo` (grafo de dependencias en ASCII),
  `/dependencias <archivo>` (imports y dependencias inversas), `/save` (guarda la
  sesión en historial.json) además de los comandos previos.
  Los comandos largos (`/run`, `/explore`, `/fix`, `/review`, `/server`) se
  ejecutan en un hilo separado para no bloquear el chat.

## 📚 Casos de uso

### Flutter

```bash
snapcontext "el botón de pago no actualiza el total" --test-loop
snapcontext fix "el widget de login no muestra errores"
snapcontext review "revisa la navegación del carrito"
```

Detecta `pubspec.yaml`, escanea `lib/` y `test/`, y ejecuta `flutter test` en
bucle hasta que pasen.

### React / Node

```bash
snapcontext --provider anthropic "el formulario no valida el email"
snapcontext plan "migrar componentes a hooks" --branch refactor/hooks --auto
```

Detecta `package.json`, escanea `src/`, y respeta las convenciones anotadas en
tu `CLAUDE.md`.

### Python

```bash
snapcontext "añade tests para el módulo de pagos"
snapcontext --init-claude      # genera la memoria del proyecto
snapcontext --chat             # /tool ast pagos.py · /tool git_status
```

Detecta `pyproject.toml` / `requirements.txt` y usa `pytest` como test por
defecto.

### CI / automatización

```bash
snapcontext --plan "corregir tests rotos" --local --no-confirmar --auto
```

Sin claves (`--local` usa heurística local) y sin preguntas interactivas.

## 🤝 Contribuciones

Las contribuciones son bienvenidas. Si tienes una idea, abre un issue o envía un pull request.

1. Haz un fork del proyecto.
2. Crea tu rama de características (`git checkout -b feature/nueva-funcionalidad`).
3. Haz commit de tus cambios (`git commit -m 'Añadir nueva funcionalidad'`).
4. Haz push a la rama (`git push origin feature/nueva-funcionalidad`).
5. Abre un Pull Request.
   
Combina lo mejor de dos mundos:

- **Aider**: eficiencia, control y una integración Git impecable.
- **Gestión automática de contexto** (estilo Claude Code): no hace falta
  decirle `/add archivo` — SnapContext averigua los archivos por ti.

Le pasas una tarea en lenguaje natural y SnapContext:

1. **Escanea** el repositorio (por defecto las carpetas `lib/` y `supabase/`)
   y encuentra los archivos más relacionados con tu consulta.
2. **El proveedor de IA** (Gemini, Ollama local, DeepSeek o Groq) selecciona
   los archivos más relevantes.
3. **Aider** recibe esos archivos y la consulta original, y hace los cambios
   en el código (con commits automáticos en Git).

```
$ snapcontext "el botón de pago no funciona"
ℹ Repositorio: C:\...\marketplace-productos-locales
ℹ Escaneando el repositorio para encontrar candidatos...
ℹ 24 candidato(s) relevante(s) localmente.
ℹ Seleccionando con Gemini (gemini-2.5-flash)...

✔ Archivos seleccionados (3):
   • lib/pages/pago/pago_page.dart
   • lib/models/pedido.dart
   • supabase/functions/procesar-pago-mp/index.ts

ℹ Ejecutando Aider...
✔ Aider terminó correctamente.
```

---

## Novedades (v0.9.0)

- **Modo demo (`--demo`)**: muestra el valor de SnapContext en ~1 minuto, sin
  necesidad de API key ni Aider. Crea un proyecto de prueba temporal, muestra
  la selección de archivos (`--vista-previa --local`) y ejecuta el bucle de
  pruebas completo con un editor de demostración offline.

---

## Novedades (v0.8.0)

- **Auto-detección del tipo de proyecto**: ajusta carpetas y extensiones por
  defecto según el proyecto detectado (Flutter, Node, Python, Go, Rust, Kotlin,
  Swift) de forma transparente.
- **Alias / atajos de comandos**: `fix`, `review`, `server` e `interactive`.
- **Interfaz web en tiempo real**: spinner/barra de progreso, cronómetro,
  contadores de archivos escaneados/seleccionados y nuevos eventos coloreados.

---

## Novedades (v0.6.0)

Qué hay de nuevo en esta versión:

- **Corrección crítica en `--init`**: ahora usa correctamente `questionary.password()` para ingresar claves API con Groq y DeepSeek, solucionando un bug anterior que impedía la configuración de estos proveedores.
- **Manejo mejorado de errores de configuración**: si `~/.snapcontext/config.json` está corrupto o no existe, se muestra un aviso claro en lugar de fallar silenciosamente.
- **Actualización a versión 0.6.0**: todos los metadatos (pyproject.toml, README.md) reflejan la nueva versión.
- **Mejoras de instalación**: scripts de Linux y Windows actualizados con mensajes claros y fallbacks correctos a `pip` si `uv` no está disponible.

Qué hay de nuevo en esta versión:

- **Validación de carpeta de proyecto**: comprueba al iniciar que existe una
  carpeta típica (`lib/`, `src/`, `supabase/`, `app/`, `packages/`, `backend/`)
  y avisa si no (sale con código 1).
- **Menú interactivo de proveedor** (`questionary` con flechas y Enter):
  elige Gemini, Ollama, DeepSeek o Groq sin escribir `--provider`.
- **Configuración persistente**: guarda tu proveedor/modelo favorito en
  `~/.snapcontext/config.json` y lo reutiliza en las siguientes ejecuciones.
- **Auto-detección de modelos de Ollama**: con `ollama list`, muestra los
  modelos locales disponibles para elegir.
- **Asistente de configuración inicial (`--init`)**: guía el alta de claves API,
  proveedor y modelo favorito, y una prueba opcional de conexión.

---

## 📦 Instalación con el instalador .exe (Windows, sin Python) — v1.5.0

Desde la **v1.5.0**, en Windows puedes instalar SnapContext **sin necesidad de
Python** con el instalador gráfico:

1. Descarga **`SnapContext-Setup-<versión>.exe`** desde
   [GitHub Releases](https://github.com/NicolasBruna24/snapcontext/releases).
2. Ejecútalo: instala en `%LOCALAPPDATA%\Programs\SnapContext`, añade la
   carpeta al **PATH del usuario** y (opcional) crea accesos directos en el
   **Menú Inicio** y el **Escritorio**.
3. Abre una terminal nueva y comprueba:

   ```powershell
   snapcontext --version
   snapcontext --demo          # demo sin API key ni Aider
   ```

El instalador incluye un **desinstalador** (Panel de control → Aplicaciones o
`Uninstall.exe` en la carpeta de instalación); tu configuración
(`~\.snapcontext`) se conserva.

> **Notas:** el `.exe` incluye las dependencias ligeras (Gemini/OpenAI,
> menú interactivo e interfaz web). Los extras pesados (embeddings con
> `sentence-transformers` y análisis con `tree-sitter`) quedan fuera para
> mantenerlo ligero; si los necesitas, instala la versión Python con
> `pip install snapcontext[embeddings,mcp_avanzado]`. Aider (modo edición)
> se instala aparte: `pip install aider-chat`.

### Regenerar el instalador (mantenedores)

Requisitos: Python 3.9+ con las dependencias del proyecto, `pip install
pyinstaller` y [NSIS](https://nsis.sourceforge.io) en el PATH.

```powershell
.\scripts\empaquetar_exe.ps1          # genera dist\SnapContext-Setup-<versión>.exe
.\scripts\empaquetar_exe.ps1 -Full    # incluye sentence-transformers + tree-sitter
```

El script ejecuta `pyinstaller snapcontext.spec`, verifica que el ejecutable
responde a `--version` y después compila `installer.nsi` con `makensis`.

---

## Instalación rápida (one-liner)

**Linux / macOS:**

```bash
# Instalar desde GitHub Pages (recomendado)
curl -LsSf https://NicolasBruna24.github.io/snapcontext/install.sh | sh

# Fallback: instalan directamente desde el repositorio raw si GitHub Pages falla
curl -LsSf https://raw.githubusercontent.com/NicolasBruna24/snapcontext/main/install.sh | sh
```

**Windows (PowerShell):**

```powershell
# Instalar desde GitHub Pages (recomendado)
powershell -ExecutionPolicy ByPass -c "irm https://NicolasBruna24.github.io/snapcontext/install.ps1 | iex"

# Fallback: instalan directamente desde el repositorio raw si GitHub Pages falla  
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/NicolasBruna24/snapcontext/main/install.ps1 | iex"
```

Los scripts detectan automáticamente tu sistema, verifican Python 3.9+, instalan `uv` (gestor rápido de paquetes Python) si no está presente, y finalmente instalan SnapContext. Al terminar:

- ✅ **Windows**: El comando `snapcontext` se añadirá automáticamente al PATH del usuario permanentemente (variable `Path`). Solo necesitas reiniciar tu terminal o ejecutar `refreshenv`.
- ✅ **Linux/macOS**: pip registra el ejecutable en el PATH del usuario de forma automática sin requerir permisos especiales.

> 📝 En Windows, si prefieres una instalación manual paso a paso o instalaste directamente con `pip install snapcontext` (sin usar el one-liner), puedes ejecutar:
> 
> ```powershell
> snapcontext --setup-path
> ```
> 
> Esto añadirá automáticamente la carpeta de SnapContext al PATH del usuario.


---

## Requisitos

| Herramienta | Para qué | Instalación |
|---|---|---|
| Python 3.9+ | ejecutar SnapContext | [python.org](https://python.org) |
| `google-generativeai` | proveedor Gemini (por defecto) | `pip install google-generativeai` |
| `openai` | DeepSeek, Groq y Ollama (API compatible OpenAI) | `pip install openai` |
| `questionary` (opcional) | menú interactivo de selección de proveedor | `pip install "snapcontext[interactive]"` |
| Clave de un proveedor | `GEMINI_API_KEY`, `DEEPSEEK_API_KEY` o `GROQ_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `aider-chat` | hacer las modificaciones | `pip install aider-chat` |
| `flutter` (opcional) | bucle de pruebas `--test-loop` | [flutter.dev](https://flutter.dev) |

> Aider usa su propia configuración de modelo y API keys (variables `AIDER_*`
> o el fichero `.env` del proyecto). Configura Aider una vez y SnapContext lo
> reutilizará.

---

## Instalación

```bash
# 1. Clonar / copiar el proyecto y entrar en él
cd SnapContext

# 2. (Recomendado) entorno virtual
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 3. Instalar en modo editable → expone el comando "snapcontext" globalmente
pip install -e .

# 3b. (Opcional) Menú interactivo para elegir proveedor con flechas ↑↓
#     (si no lo instalas, SnapContext usa Gemini por defecto con un aviso)
pip install "snapcontext[interactive]"     # o simplemente:  pip install questionary

# 4. Dependencia externa (Aider arrastra más paquetes por eso va aparte)
pip install aider-chat

# 5. Configurar la clave del proveedor que vayas a usar
#    PowerShell:
$env:GEMINI_API_KEY = "tu_clave"        # o DEEPSEEK_API_KEY / GROQ_API_KEY
#    Linux/Mac:
export GEMINI_API_KEY=tu_clave
```

---

## Uso

```bash
# Ejemplo básico: escaneo + IA + Aider
snapcontext "el botón de pago no funciona"

# Selección de proveedor: sin --provider ni --local, aparecerá un menú
# interactivo (↑↓ + Enter) para elegir entre Gemini, Ollama, DeepSeek y Groq.
# Requiere questionary (ver instalación); sin él se usa Gemini con un aviso.
snapcontext "revisar el login"

# Elegir proveedor y modelo directamente (sin menú interactivo)
snapcontext "..." --provider groq
snapcontext "..." --provider deepseek --model deepseek-reasoner
snapcontext "..." --provider ollama --model qwen2.5   # Ollama local

# Modo offline (sin IA): no muestra menú interactivo
snapcontext "..." --local

# Modo experto: revisar/añadir/eliminar archivos antes de Aider
snapcontext "revisar pago" --experto

# Si el proyecto usa carpetas distintas a lib/ y supabase/
snapcontext "arreglar login" --carpetas src migrations

# Solo ver qué archivos elegiría (sin tocar código)
snapcontext "revisar carrito" --vista-previa

# Bucle agéntico: ejecutar flutter test tras Aider y repetir si falla
snapcontext "añadir validación al formulario" --test-loop

# Cambiar el número de archivos que recibe Aider
snapcontext "agregar índice a pedidos" --max-archivos 4

# Opciones extra para Aider (modelo, etc.)
snapcontext "..." --aider-opciones "--model sonnet --no-auto-commits"

# Modo offline (sin Gemini) por si solo quieres la heurística local
snapcontext "..." --local

# Interfaz web (FastAPI + WebSockets) con logs en tiempo real
snapcontext --web              # http://localhost:8000
snapcontext --web --web-puerto 8123

# Alias / atajos para tareas frecuentes
snapcontext fix "el botón de pago no funciona"    # = --test-loop
snapcontext review "revisar el login"             # = --vista-previa --experto
snapcontext server "iniciar servidor"             # = --server-loop
snapcontext interactive                           # = --web

# Demo: valor de SnapContext en ~1 min, sin API key
snapcontext --demo
```
### Auto-detección del tipo de proyecto

SnapContext detecta automáticamente el tipo de proyecto buscando archivos clave
en la raíz resuelta por `--directorio` (o el directorio actual):

| Archivo clave               | Tipo       | Carpetas por defecto que escanea            |
|-----------------------------|------------|----------------------------------------------|
| `pubspec.yaml`              | `flutter`  | `lib`, `test`, `web`                         |
| `package.json`              | `node`     | `src`, `backend`, `frontend`, `lib`          |
| `requirements.txt` / `pyproject.toml` | `python` | `src`, `app`, `lib`, `tests`, `scripts` |
| `go.mod`                    | `go`       | `cmd`, `internal`, `pkg`                     |
| `Cargo.toml`                | `rust`     | `src`, `tests`                               |
| `build.gradle`              | `kotlin`   | `app/src/main/kotlin`, `app/src/test/kotlin` |
| `Podfile`                   | `swift`    | `Sources`, `Tests`                           |

Además ajusta las **extensiones** consideradas en el escaneo (por ejemplo,
`.dart` para Flutter, `.js`/`.ts`/`.jsx`/`.tsx` para Node, `.py` para Python…).

La detección es **transparente**: no imprime nada salvo que uses `--depurar`.
Si no se detecta ningún tipo, se mantiene el comportamiento actual (`lib/`,
`supabase/`, …). Si pasas `--carpetas` manualmente, tu valor tiene prioridad.

### Alias / atajos de comandos

Para tareas frecuentes existen subcomandos cortos (siempre se pueden combinar
con el resto de flags):

| Comando                          | Equivale a                                            |
|----------------------------------|-------------------------------------------------------|
| `snapcontext fix "mensaje"`      | `snapcontext "mensaje" --test-loop`                   |
| `snapcontext review "mensaje"`   | `snapcontext "mensaje" --vista-previa --experto`      |
| `snapcontext server "mensaje"`   | `snapcontext "mensaje" --server-loop`                 |
| `snapcontext interactive`        | `snapcontext --web`                                   |

Si el primer argumento no coincide con ninguno de estos alias, se trata como
consulta (comportamiento habitual).

### 🚀 Empezar un proyecto desde cero

¿Carpeta vacía? No hay problema. Desde la **v1.3.0** SnapContext puede trabajar
en carpetas nuevas o casi vacías:

```bash
mkdir mi-app && cd mi-app
snapcontext "crear la estructura inicial de un backend con FastAPI" --iniciar-proyecto
```

- `--iniciar-proyecto` (alias `--no-validar`): omite por completo la validación
  de carpeta. Ideal para empezar un proyecto desde cero. Muestra el aviso:
  `⚠️ Modo iniciar-proyecto: se omite la validación de carpeta. Asegúrate de estar en el directorio correcto.`
- `--local`: trabaja sin IA y también desactiva la validación:

  ```bash
  snapcontext "listar archivos del proyecto" --local --vista-previa
  ```

- `--directorio <ruta>` explícito: si indicas la carpeta a mano, solo se muestra
  un aviso (no se bloquea).
- Además, desde v1.3.0 la validación acepta **carpetas típicas vacías**
  (`lib/`, `src/`, `supabase/`, `app/`, `packages/`, `backend/`), **archivos de
  código vacíos** en la raíz (`main.py`, `main.dart`, `index.js`, ...) y
  **archivos de configuración vacíos** (`pubspec.yaml`, `package.json`,
  `pyproject.toml`, ...). Basta con crear uno para que SnapContext te deje
  trabajar.

SnapContext puede crear archivos desde cero: Aider escribe los ficheros nuevos
que la tarea requiera, incluso en una carpeta que antes estaba vacía.

### Validación de carpeta de proyecto

Al iniciar, SnapContext comprueba que el directorio tenga indicios de ser un
proyecto (v1.3.0): alguna carpeta típica (`lib/`, `src/`, `supabase/`, `app/`,
`packages/`, `backend/`) —aunque esté vacía—, algún archivo de código en la
raíz —aunque esté vacío— o algún archivo de configuración típico
(`pubspec.yaml`, `package.json`, `requirements.txt`, `go.mod`, `Cargo.toml`,
`setup.py`, `pyproject.toml`). Si no encuentra nada:

- Con `--iniciar-proyecto`, `--local` o `--directorio <ruta>` explícito → solo
  avisa y continúa.
- Sin ninguno de esos flags → error con sugerencias (código 1):

```
⚠️ No se detectó una carpeta de proyecto típica (lib/, src/, supabase/, etc.).
Si estás empezando un proyecto desde cero, usa --iniciar-proyecto para saltar esta validación.
O usa --local para trabajar sin IA (también desactiva la validación).
También puedes indicar la carpeta con --directorio <ruta>.
```

### Menú interactivo de proveedor (↑↓ + Enter)

Si ejecutas `snapcontext "consulta"` **sin** `--provider` ni `--local`, SnapContext
te pregunta si quieres elegir el proveedor y, si aceptas, muestra un menú con las
flechas ↑/↓ y Enter:

```text
🤖 Selecciona el proveedor de IA:
  ❯ Gemini (Google)
    Ollama (local)
    DeepSeek (API)
    Groq (API)
```

Usa la librería **questionary** (`pip install snapcontext[interactive]`). Si no
está instalada, se muestra un aviso y se usa **Gemini por defecto**. Para evitarlo,
pasa siempre `--provider` o `--local`.

### Configuración persistente

SnapContext guarda tus preferencias (proveedor favorito, modelo y claves API de
los proveedores que configures) en `~/.snapcontext/config.json`:

```json
{
  "provider": "groq",
  "model": "llama-3.3-70b-versatile",
  "prompt_caching": true,
  "api_keys": {
    "gemini": "AIza...",
    "groq": "gsk_..."
  }
}
```

- **Primera vez** (sin configuración): pregunta si quieres elegir proveedor con el
  menú interactivo y ofrece guardarlo como predeterminado.
- **Siguientes veces**: usa directamente el proveedor guardado, sin menú.
- **`--provider ...` (y opcional `--model ...`)**: usa ese proveedor y sobrescribe
  la preferencia guardada.
- **`--no-persist`**: ignora la configuración guardada y fuerza el menú interactivo
  (no guarda nada).
- También respeta la variable de entorno `SNAPCONTEXT_PROVIDER` si no hay
  configuración guardada.

**Cómo editarlo:** abre `~/.snapcontext/config.json` con cualquier editor, cambia
`provider` y `model`, y guarda. La próxima ejecución lo lee (la carpeta se crea
automáticamente con `--init` o al elegir "guardar").

### Asistente de configuración inicial (`--init`)

Para no configurar claves y proveedor a mano, existe un asistente guiado:

```bash
snapcontext --init
```

Flujo del asistente:

1. Pide la **clave de API de Gemini** (campo oculto).
2. Pregunta si quieres configurar otros proveedores (Groq, DeepSeek) y guarda
   sus claves.
3. Te deja elegir el **proveedor y modelo favorito** con el menú interactivo.
4. Guarda todo en `~/.snapcontext/config.json`.
5. (Opcional) Prueba la conexión con la API del proveedor elegido.

Si ya existe una configuración, pregunta si quieres **sobrescribirla**.
Si `questionary` no está instalada, se muestra el aviso: `pip install
questionary` (o `pip install snapcontext[interactive]`).

`--init` es independiente: no necesita consulta, ni escaneo, ni Aider; solo
configura y sale (el resto de flags sigue funcionando igual).

### Auto-detección de modelos de Ollama

Si eliges **Ollama** (en el menú o como preferencia guardada) sin pasar
`--model`, SnapContext ejecuta `ollama list` en segundo plano, listo los modelos
locales y te deja elegir con las flechas:

```text
🤖 Selecciona el modelo de Ollama:
  ❯ llama3.2
    qwen2.5
```

- Los modelos se parsean de la primera columna de la salida de `ollama list`.
- Si `ollama` no está instalado o no hay modelos, se avisa y se ofrece volver al
  menú de proveedores o usar **Gemini** por defecto.
- Si pasas `--provider ollama --model <nombre>` por CLI, se omite la detección
  y se usa directamente ese modelo.

### Opciones principales

| Opción | Por defecto | Descripción |
|---|---|---|
| `consulta` | *(opcional con `--init`)* | La tarea a resolver, entre comillas |
| `--init` | off | Asistente de configuración inicial (claves + proveedor/modelo) |
| `--directorio` | `.` | Repositorio (detecta la raíz Git automáticamente) |
| `--carpetas` | `lib supabase` | Carpetas a escanear |
| `--max-archivos` | `3` | Archivos que recibe Aider |
| `--candidatos` | `80` | Candidatos que se envían al proveedor de IA |
| `--provider` | *(sin por defecto)* | Proveedor que selecciona archivos (`gemini`, `ollama`, `deepseek`, `groq`). Si no se indica (ni `--local`) se usa el guardado en `~/.snapcontext/config.json` o un menú interactivo |
| `--no-persist` | off | Ignora la configuración guardada y fuerza el menú interactivo |
| `--model` (alias `--modelo`) | según proveedor | Modelo del proveedor (o `SNAPCONTEXT_MODELO`) |
| `--local` | off | Selección sin IA (modo offline / pruebas). También desactiva la validación de carpeta |
| `--iniciar-proyecto` (alias `--no-validar`) | off | Omite la validación de carpeta: permite trabajar en carpetas vacías al empezar un proyecto desde cero |
| `--vista-previa` | off | Mostrar la selección y salir |
| `--experto` (alias `--expert`) | off | Revisar/añadir/eliminar archivos antes de Aider |
| `--aider-opciones` | `""` | Flags extra para Aider |
| `--test-loop` | off | Bucle agéntico Aider → pruebas → reparar |
| `--server-loop` | off | Bucle agéntico con `flutter run`, modo automático (reintenta y pregunta s/n) |
| `--manual-loop` | off | Bucle agéntico con `flutter run`, modo manual (usuario decide cada paso) |
| `--max-intentos` | `3` | Intentos máximos de `--server-loop` |
| `--dispositivo` | `web-server` | Plataforma/dispositivo de `flutter run` |
| `--url-defecto` | `http://localhost:5000` | URL para abrir el navegador si Flutter no reporta una |
| `--comando-test` | *auto* | Comando del bucle de pruebas (se detecta automáticamente según el lenguaje; ej. `pytest`, `go test ./...`, `flutter test`) |
| `--max-iteraciones` | `3` | Iteraciones máximas del bucle |
| `--validar` | on | Valida la sintaxis del código antes de guardar en el editor propio |
| `--no-validar-sintaxis` | off | Desactiva la validación de sintaxis del editor propio |
| `--max-intentos-validacion` | `3` | Intentos de validación de sintaxis antes de cancelar la edición |
| `--asesor` (`--sugerir`) | off | Asesor proactivo: analiza y sugiere mejoras sin modificar código |
| `--asesor-auto` | off | Aplica automáticamente las mejoras seguras del asesor |
| `--asesor-umbral` | `20` | Líneas máximas por función para el asesor |
| `--api` (`--api-server`) | off | Arranca la API REST pública (v3.6.0) en el puerto 8001 |
| `--api-puerto` | `8001` | Puerto de la API |
| `--api-host` | `127.0.0.1` | Host de escucha de la API |
| `--api-token` | — | API key de los endpoints `/api/v1/*`; por defecto usa/genera la de config.json |
| `--api-generate-key` | off | Genera y guarda una API key segura sin arrancar el servidor |

> **Nota:** el flag negativo de validación de sintaxis se llama `--no-validar-sintaxis`
> porque `--no-validar` ya existe como alias de `--iniciar-proyecto`.

---

## Cómo funciona por dentro

```
 consulta ──▶ [1] Escaneo local        ◀── git ls-files / os.walk
                     │
                     ▼
    candidatos ordenados (heurística: ruta + contenido)
                     │
                     ▼
      [2] Proveedor IA (JSON) ─▶ 3 archivos más relevantes
      (Gemini | Ollama | DeepSeek | Groq)
                     │
                     ▼
 [3] Aider --yes --file A --file B --message "consulta"
                     │
                     ▼
 [4] (opcional) verificación: flutter test / flutter run ─ si falla, Aider arregla
                     │                                     │
                     └── pasa ─────────────── fin (aprobado)
```

**El escaneo** usa `git ls-files -c -o --exclude-standard` (respeta `.gitignore`
e incluye archivos nuevos), con caída automática a recorrer el árbol si no hay
Git. Luego puntúa cada archivo: coincidencias en la ruta (un nombre de archivo
como `pago_page.dart` pesa más que el directorio `pagos/`) y coincidencias en
las primeras líneas del contenido (con tildes normalizadas: `botón` → `boton`).

**El proveedor de IA** recibe la lista de candidatos y responde en JSON (con
validación y fallback si el modelo no devuelve rutas válidas). Gemini usa
`google.generativeai`; DeepSeek, Groq y Ollama usan la librería `openai` (sus
APIs son compatibles con la de OpenAI). El modo `--local` usa solo la
heurística local, sin llamar a ningún proveedor.

---

## Bucle agéntico (`--test-loop`)

El modo `--test-loop` implementa el paso 4 de la arquitectura:
`Aider → flutter test → si falla, Aider recibe el error real y lo arregla`,
hasta un máximo de `--max-iteraciones`.

```bash
snapcontext "arreglar el flujo de pago" --test-loop --max-iteraciones 5
```

Este es el punto natural para extender SnapContext: por ejemplo, añadir
`flutter analyze`, linters o más herramientas dentro de
`ejecutar_bucle_test()`.

---

## 🛡️ Sandboxing inteligente

Desde **v5.4.0**, SnapContext ya no necesita que actives `--sandbox` para
protegerte: analiza cada comando y activa el contenedor Docker **solo cuando
es necesario**.

### Cómo funciona

1. Antes de ejecutar cualquier comando (planificador, ReAct `ejecutar_comando`,
   bucle de pruebas, plugins, MCP), SnapContext lo evalúa con
   `sandbox_utils.es_comando_peligroso()` (regex compiladas, muy rápido).
2. Si el comando es peligroso → se ejecuta dentro del sandbox Docker
   automáticamente: `🔒 Comando potencialmente peligroso detectado.
   Ejecutando en sandbox Docker.`
3. Si es peligroso pero Docker no está disponible → se advierte y, en modo
   interactivo, se pregunta si continuar; en `--auto` se **aborta** el comando.
4. Los comandos seguros se ejecutan directamente, sin fricción.

### Patrones detectados (extensibles en `sandbox_utils.py`)

- Borrado masivo: `rm -rf /`, `rm -rf ~`, `rm -rf .`, `--no-preserve-root`.
- Discos: `dd if=/of=`, `mkfs*`, `fdisk`, `wipefs`, `parted`.
- Descarga y ejecución: `curl ... | sh`, `wget ... | bash`, `... | sudo bash`.
- Permisos: `chmod 777 /`, `chmod -R 777`, `chown -R`.
- Fork bomb `:(){ :|:& };:`, `sudo` + comando destructivo.
- Escritura en dispositivos de bloque (`> /dev/sda`; `> /dev/null` es inocuo).
- Terminación de procesos: `kill -9`, `pkill`.

### Control por el usuario

| Mecanismo | Efecto |
|---|---|
| `--sandbox` | Fuerza el contenedor para **todos** los comandos (como en v4.3.0). |
| `--no-sandbox` | Desactiva todo el sandbox, incluso ante comandos peligrosos (prioridad máxima). |
| `SNAPCONTEXT_SANDBOX=1` | Sandbox siempre activo (equivale a `--sandbox`, sin error si falta Docker). |
| `SNAPCONTEXT_SANDBOX=0` | Equivale a `--no-sandbox`. |

Prioridad: `--no-sandbox` / `SNAPCONTEXT_SANDBOX=0` > `--sandbox` >
`SNAPCONTEXT_SANDBOX=1` > detección automática.

```bash
# Modo por defecto: solo los comandos peligrosos van al contenedor
snapcontext "limpiar el proyecto"

# Opt-out total (scripts de CI, confianza total)
snapcontext "tarea" --no-sandbox

# Siempre sandbox
SNAPCONTEXT_SANDBOX=1 snapcontext "tarea"
```

## 🧪 Detección automática de pruebas

Desde **v5.3.0**, SnapContext detecta automáticamente cómo correr las pruebas
de tu proyecto: escanea el directorio raíz, identifica el lenguaje/framework y
usa el comando de test adecuado **sin que tengas que escribir nada**.

Esto hace que tanto el **agente ReAct** (herramienta `ejecutar_pruebas`) como
el planificador (**`--test-loop`**) ejecuten los tests por sí solos.

### Lenguajes / frameworks soportados

| Archivo(s) en la raíz                | Lenguaje detectado      | Comando de test             |
|--------------------------------------|-------------------------|-----------------------------|
| `go.mod`                             | Go                      | `go test ./...`             |
| `Cargo.toml` / `Cargo.lock`          | Rust                    | `cargo test`                |
| `pom.xml`                            | Java (Maven)            | `mvn test`                  |
| `build.gradle`                       | Java (Gradle)           | `gradle test`               |
| `requirements.txt`                   | Python (pytest)         | `pytest`                    |
| `pyproject.toml` (con `pytest`)      | Python (pytest)         | `pytest`                    |
| `pyproject.toml` (sin pytest) / `setup.py` | Python (unittest) | `python -m unittest discover` |
| `package.json`                       | Node (npm)              | `npm test`                  |
| `yarn.lock` (con `package.json`)     | Node (yarn)             | `yarn test`                 |
| `pubspec.yaml`                       | Flutter                 | `flutter test`              |
| `*.csproj` (en la raíz)              | .NET                    | `dotnet test`               |
| `Gemfile`                            | Ruby                    | `bundle exec rspec`         |
| `mix.exs`                            | Elixir                  | `mix test`                  |

### Cómo funciona

1. `snapcontext.py` (vía `detector_tests.py`, sin dependencias externas) mira
   los archivos clave **solo en la raíz** (rápido y ligero).
2. Si el usuario pasa `--comando-test "..."`, **siempre se usa ese** (con
   compatibilidad hacia atrás); el detector solo actúa cuando no se da un
   comando explícito.
3. Si no se detecta nada, se usa `flutter test` como último recurso (el
   comportamiento histórico). En el agente ReAct, si nada funciona se devuelve
   un error claro pidiendo que especifiques el comando manualmente.

### Uso

```bash
# Antes: había que saber y escribir el comando.
snapcontext "hacer pasar los tests" --test-loop --comando-test "pytest"

# Ahora: se detecta solo según el lenguaje del proyecto.
snapcontext "hacer pasar los tests" --test-loop
```

### Extender el detector

Para añadir un lenguaje nuevo, edita `detector_tests.py`:

- Añade una entrada en `_LENGUAJES` con su `comando` y `estructura`.
- Registra su archivo identificador en `_DETECCION_POR_ARCHIVO` (o en
  `_REGLAS_CONTENIDO` si depende del contenido).

---

## Modo experto (`--experto` / `--expert`)

Con `--experto`, antes de ejecutar Aider SnapContext **te deja revisar y
editar la lista de archivos** que la IA seleccionó:

```bash
snapcontext "revisar el flujo de pago" --experto
```

1. Tras la selección pregunta: `¿Quieres revisar los archivos seleccionados?
   (s/n)`.
   - `n` → ejecuta Aider directamente (comportamiento normal).
   - `s` → abre el menú experto.
2. En el menú se muestran los archivos **numerados** y las opciones:

   ```
   ── Modo experto ─────────────────────────
     [1] lib/pago/pago_page.dart
     [2] lib/models/pedido.dart
     [3] supabase/functions/procesar-pago-mp/index.ts
   Opciones: [a]gregar   [e]liminar   [l]impiar   [c]ontinuar
   ```

   | Opción | Qué hace |
   |---|---|
   | `a` | Pide una ruta y la añade (se valida que exista y esté dentro del repo) |
   | `e` | Elimina por índice (fuera de rango se rechaza) |
   | `l` | Vacía la lista (con confirmación) |
   | `c` | Usa la lista final y ejecuta Aider |

3. Con `c` (continuar) se muestran los archivos finales, se los pasa a Aider
   y se ejecuta cualquiera de los bucles elegidos (`--test-loop`,
   `--server-loop`, etc.)

---

## Bucle agéntico con servidor (`--server-loop` / `--manual-loop`)

En vez de solo probar, SnapContext **lanza la app** con `flutter run` en
segundo plano, detecta que el servidor arrancó (buscando `Running on`,
`Synced`, `served at`, etc., o la URL real) y deja verificar la app.

**Modo automático (`--server-loop`):**

```bash
snapcontext "arreglar la pantalla de pago" --server-loop
snapcontext "..." --server-loop --max-intentos 5      # reintentos (por defecto 3)
snapcontext "..." --server-loop --dispositivo chrome  # abrir Chrome
```

1. Aider edita los archivos.
2. Se lanza `flutter run` (por defecto `-d web-server --web-port 5000`).
3. Si el servidor **arranca** → pregunta `¿Quieres probar la app
   manualmente? (s/n)`; con `s` abre el navegador con la URL real y espera a
   que pulses Enter. Termina con éxito.
4. Si el servidor **falla** → captura el error, se lo pasa a Aider
   (`Arregla este error: ...`) y reintenta, hasta `--max-intentos`.
5. Si se agotan los intentos → pregunta `¿Quieres cambiar a modo manual?
   (s/n)`; con `s` pasa a `--manual-loop`, con `n` termina con error.

**Modo manual (`--manual-loop`):**

```bash
snapcontext "revisar el flujo de login" --manual-loop
```

Tras cada intento (arranque o fallo) pregunta siempre
`¿La app funciona correctamente? (s/n)`. Si respondes `n`, te pide que
describas el error y esa descripción se pasa a Aider
(`Arregla este error: <tu texto>`), repitiendo el ciclo.

> El servidor se cierra solo al finalizar (o con Ctrl+C), sin dejar procesos
> huérfanos. Configura tu proyecto para que `flutter run` use web si pruebas la
> interfaz en el navegador.

---

## 🧠 Extensión para IntelliJ IDEA / PyCharm (v1.7.0)

SnapContext también vive dentro del ecosistema **JetBrains** (IntelliJ IDEA,
PyCharm, WebStorm…) con una extensión en Kotlin ubicada en `jetbrains/`.

### Funcionalidades

| Acción | Dónde | Qué hace |
|---|---|---|
| Ejecutar consulta… (`Alt+Shift+S`) | Tools → SnapContext | Pipeline completo de SnapContext sobre el proyecto abierto |
| Planificar tarea… | Tools → SnapContext | Lanza `--plan --auto` con la consulta |
| Corregir con bucle de pruebas… | Tools → SnapContext | Lanza la consulta con `--test-loop` |
| Abrir interfaz web | Tools → SnapContext | Arranca `--web` y abre `http://localhost:8000` al estar listo |
| Añadir al contexto | Menú contextual del explorador | Marca archivos prioritarios (equivalente a `/add`) |
| Limpiar archivos de contexto | Tools → SnapContext | Vacía el contexto marcado |

La salida se muestra en tiempo real en la herramienta inferior **«SnapContext»**
con barra de progreso cancelable.

### Instalación (desde código fuente)

Requisitos: **JDK 17+** y conexión a Internet (Gradle descarga el SDK de
IntelliJ Community la primera vez).

```bash
cd jetbrains
./gradlew buildPlugin        # genera build/distributions/snapcontext-jetbrains-1.7.0.zip
./gradlew runIde             # o prueba el plugin en un IDE sandbox
```

Para instalar el `.zip`: **Settings → Plugins → ⚙ → Install Plugin from Disk…**

### Configuración

**Settings → Tools → SnapContext**:

- **Comando**: cómo invocar SnapContext (`python -m snapcontext` por defecto;
  usa `snapcontext` si tienes el ejecutable/instalador .exe).
- **Proveedor**: equivalente a `--provider` (vacío = el guardado en config).
- **Confirmar**: desactívalo para pasar `--no-confirmar`.
- **Clave API**: opcional; se exporta como `GEMINI_API_KEY` (y equivalentes).

> Nota: el plugin llama a la CLI de SnapContext con `ProcessBuilder`; no
> requiere Python embebido ni dependencias adicionales.

---

## Interfaz Web (`--web`)

SnapContext incluye una interfaz web ligera sobre la **arquitectura de agentes**
para seguir en tiempo real lo que hacen el orquestador y los agentes (escaneo,
selección, Aider, pruebas, cierre) sin mirar la terminal.

**Requisito:** instala las dependencias opcionales:

```bash
pip install snapcontext[web]
#   o, en desarrollo: pip install -e '.[web]'
```

**Arranque** (bloquea hasta `Ctrl+C`):

```bash
snapcontext --web                      # http://localhost:8000
snapcontext --web --web-puerto 8123    # puerto personalizado
```

Después abre `http://localhost:8000` en el navegador. La página ofrece:

- **Campo consulta** + botón **Ejecutar** (también con `Enter`).
- **Logs en tiempo real**: panel que muestra cada evento `log` que emite el
  orquestador (info/aviso/error) vía WebSocket.
- **Resultados**: archivos seleccionados, avance de Aider y cierre de la tarea
  (éxito/error).
- Opciones rápidas: **Selección local (sin IA)**, **Vista previa**, **Bucle de
  pruebas** y campos opcionales de directorio y `--max-archivos`.

**Eventos que emite el orquestador** (visible en la web):

| Tipo | Significado |
|---|---|
| `log` | Línea de log del pipeline (info/aviso/error). |
| `selección` | Archivos elegidos por el agente de contexto. |
| `aider` | Inicio/fin de la ejecución de Aider (AgenteEditor). |
| `test` | Iteración de prueba del AgenteTester (`aider`/`prueba`, ok/falló). |
| `final` | Cierre de la ejecución con código de éxito/error. |

En la consola, si no hay dependencias web instaladas, `snapcontext --web`
muestra el mensaje: *"La interfaz web necesita dependencias opcionales…"* y sale
sin romper el resto de la CLI.

---

## 🎨 Interfaz web avanzada (v1.6.0)

La web añade un **editor de código**, un **grafo de dependencias** y un **panel
de acciones rápidas** para competir con interfaces como las de Claude Code. La
UI ahora se organiza en dos columnas: **editor + dependencias** (izquierda) y
**progreso + resultados** (derecha).

### 📝 Editor de código (Monaco)

- **Monaco Editor** (el mismo editor que VS Code) se carga desde CDN
  (`cdnjs`) y muestra el contenido de los archivos seleccionados con
  **resaltado de sintaxis** para Python, JavaScript, TypeScript, Dart, Go,
  Rust, Java, C/C++, C#, Swift, etc.
- **Edición en vivo** con guardado manual (botón *Guardar*) que escribe el
  archivo en el proyecto vía WebSocket.
- **Fallback limpio**: si Monaco no carga (sin red o CSP), se usa un
  `<textarea>` funcional, así la web sigue operando.
- **Abrir el archivo**:
  - Haz clic en cualquier archivo del panel *Resultados* → se abre en el editor.
  - Los nodos del grafo de dependencias también abren el archivo al hacer clic.
  - En `--chat` el comando `/edit <archivo>` sigue abriendo el editor externo;
    ahora además se puede abrir cada archivo directamente desde la web.

### 🔗 Visualización de dependencias

- Panel con pestaña **Dependencias** que dibuja un **grafo interactivo**
  (force-directed con **d3.js**) de los archivos del proyecto.
- Las aristas provienen de los **imports** reales del código (Python `import`,
  JS/TS `import`/`require`, Dart `import`, Go `import`, Rust `use`, …),
  resueltos contra los archivos del repositorio.
- **Interactivo (ampliado en v1.6.0)**: **zoom y pan** con la rueda
  (`d3.zoom`, 0.2–4×), arrastre de nodos, **clic para abrir** el archivo en el
  editor y **doble clic para resaltar rutas**: las conexiones directas del
  nodo se pintan en verde y el resto se atenúa (doble clic en el fondo limpia).
- **Filtros (v1.6.0)**: por **lenguaje** (select poblado con los lenguajes
  presentes en el proyecto) y por **nivel de dependencia** respecto al archivo
  abierto — «todos», «1 (directos)», «≤2», «≤3» (BFS).
- Tooltips por nodo con ruta completa, lenguaje y nivel.
- Si d3 no carga, se muestra una lista de `<origen → destino>`.

### ⚡ Panel de acciones rápidas

| Botón | Acción |
|---|---|
| `⚙ Fix` | Ejecuta el alias `fix` (bucle de pruebas) sobre la consulta. |
| `🔍 Review` | Ejecuta el alias `review` (vista previa + revisión experta). |
| `🧭 Plan` | Abre el planificador (`--plan`) con la consulta actual. |
| `▶ Run` | Ejecuta un comando personalizado (modal). Atajo: `Ctrl+R`. |
| `🧪 Tests` (v1.6.0) | Abre el modal de comandos pre-rellenado (`flutter test`) para lanzar la suite. |
| `🧠 Search` | Búsqueda semántica por embeddings (si el extra `embeddings` está instalado). |
| `🔎 Explorar` | Busca la consulta en el código (`rg`/`grep`/`findstr`). |

Todos los botones tienen **tooltip descriptivo** con lo que hacen.

### ✨ UX (v1.6.0)

- **Guardar con confirmación**: `💾 Guardar` o `Ctrl+S` pide confirmación antes
  de sobrescribir el archivo en disco.
- **Notificaciones toast** (info/ok/aviso/error) para selecciones, guardados,
  errores de conexión y acciones.
- **Historial de sesión**: las últimas tareas ejecutadas; clic para reutilizar
  la consulta en el campo principal.
- **Resultados mejorados**: ruta completa visible + botón «Abrir» por archivo.
- **Atajos de teclado**: `Ctrl+Enter` ejecutar · `Ctrl+S` guardar ·
  `Ctrl+D` pestaña Dependencias · `Ctrl+R`/`Ctrl+O` modal de comandos ·
  `Esc` cerrar modales.

### 🔌 Comunicación con el orquestador (WebSockets)

El servidor (`web/app.py`) amplía el protocolo del endpoint `/ws`:

- `archivo_seleccionado` → contenido + lenguaje para el editor.
- `archivo_guardado` → confirmación de escritura en disco.
- `dependencias_actualizadas` → nodos + enlaces del grafo.
- `semanticos` / `exploracion` → resultados de búsqueda.
- `accion_ejecutada` → cierre de cada acción rápida.
- `tarea` (o el `consulta` clásico) → pipeline del orquestador como siempre.

La CLI y la extensión VS Code **no se ven afectadas**; la webview de VS Code
sigue siendo una copia de `web/static/index.html` (`vscode/webview/`), y si el
entorno de la webview bloquea los CDN se usa el respaldo sin romper nada.

---

## 🧠 Búsqueda semántica con embeddings (v1.1.0)

SnapContext puede indexar tu proyecto con **embeddings semánticos locales**
usando `sentence-transformers` + `torch` (modelo `all-MiniLM-L6-v2`). Esto
permite buscar archivos por su significado —no solo por palabras clave—
mejorando drásticamente la selección de contexto y acercándose a la calidad
de Claude Code.

### Instalación

```bash
pip install "snapcontext[embeddings]"   # incluye sentence-transformers + torch
```

> **Opcional**: sin este extra, SnapContext funciona exactamente como v1.0.0
> (heurística + selección con IA). La búsqueda semántica se activa de forma
> automática solo cuando la librería está disponible.

### Cómo funciona

1. **Indexación**: al primer uso (o cuando cambia el proyecto), SnapContext
   escanea recursivamente los archivos de código (`.py`, `.js`, `.ts`,
   `.dart`, `.go`, `.rs`, …), respetando `.gitignore`, divide cada archivo en
   fragmentos de ~512 tokens y genera embeddings para cada uno.
2. **Caché persistente**: el índice se guarda en `~/.snapcontext/index/`.
   SnapContext almacena un **hash del proyecto** y, si detecta cambios,
   **reindexa automáticamente** (con aviso). Los archivos sin cambios reutilizan
   sus embeddings cacheados —solo se recomputan los fragmentos nuevos o
   modificados.
3. **Búsqueda**: en cada ejecución, SnapContext genera un embedding de la
   consulta, calcula la **similitud de coseno** con todos los fragmentos y
   prioriza los archivos más relevantes.

### Uso en el pipeline

Durante `snapcontext "<consulta>"` (o `--plan`, `--chat`, etc.), si está el
extra de embeddings y hay más candidatos que `max_archivos`, SnapContext:

1. Carga (o indexa) el proyecto.
2. Ejecuta `_seleccionar_archivos_con_embeddings()` para obtener los archivos
   más similares a la consulta (umbral configurable, por defecto 0.6).
3. **Reordena** los candidatos locales poniendo primero los archivos priorizados
   por embeddings, de modo que el proveedor de IA refine sobre un subconjunto
   de alta calidad.

Esto reduce drásticamente el número de llamadas al proveedor y mejora la
precisión de la selección final.

### Búsqueda desde el chat (`--chat`)

En modo chat interactivo, el comando `/search <consulta>` ejecuta una búsqueda
semántica y muestra los archivos más relevantes con su puntuación:

```
snapcontext --chat
/search botón de pago no funciona
📄 lib/pagos/pago_service.dart  (similitud: 0.89)
📄 lib/ui/pago_form.dart        (similitud: 0.76)
📄 test/pago_test.dart          (similitud: 0.65)
```

### Uso directo desde Python

```python
import snapcontext as sc

if sc._embeddings_disponibles():
    resultados = sc._buscar_semanticamente("gestión de usuarios", directorio=".")
    for r in resultados:
        print(r["archivo"], r["similitud"])

    archivos = sc._seleccionar_archivos_con_embeddings(
        "gestión de usuarios", directorio=".", max_archivos=3, umbral=0.6
    )
    print(archivos)
```

---

## Solución de problemas

| Problema | Solución |
|---|---|
| `No se encontró la librería 'google.generativeai'` | `pip install google-generativeai` |
| `No se encontró la librería 'openai'` | `pip install openai` |
| `No se encontró la variable de entorno GEMINI_API_KEY` | Configurar la clave del proveedor (ver Instalación) |
| Falta la clave de DeepSeek / Groq | Configurar `DEEPSEEK_API_KEY` / `GROQ_API_KEY` |
| `Error al llamar a Ollama` | Arrancar el servidor (`ollama serve`) y descargar el modelo (`ollama pull llama3.2`) |
| `No se encontró 'flutter'` | Instalar Flutter o revisar el PATH (bucle de servidor) |
| `--server-loop` no arranca la app | Ajustar `--dispositivo` (web-server / chrome / edge) y `--url-defecto` |
| `No se encontró el comando 'aider'` | `pip install aider-chat` |
| `No se encontraron archivos` | Revisar las carpetas escaneadas con `--carpetas` |
| Quieres probar sin gastar cuota API | `--local --vista-previa` |
| Errores de red / cuota en Gemini | Reintenta; el mensaje incluye el detalle |

---

## Extenderlo

El código está pensado como un **único script claro y comentado**. Puntos de
extensión fáciles:

- `escanear_repositorio()` → cambiar la heurística de relevancia local.
- `construir_prompt_seleccion()` → mejorar las instrucciones a Gemini.
- `ejecutar_bucle_test()` → añadir más herramientas al bucle agéntico.
- Constantes de configuración → ajustar el comportamiento por defecto.

---

## Variables de entorno

Ejemplo de configuración en **Linux/macOS** (`bash`/`zsh`):

```bash
export GEMINI_API_KEY="tu_clave"
export DEEPSEEK_API_KEY="tu_clave"
export GROQ_API_KEY="tu_clave"
export OLLAMA_URL="http://localhost:11434"
```

Ejemplo de configuración en **Windows** (PowerShell):

```powershell
$env:GEMINI_API_KEY = "tu_clave"
$env:DEEPSEEK_API_KEY = "tu_clave"
$env:GROQ_API_KEY = "tu_clave"
$env:OLLAMA_URL = "http://localhost:11434"
```

| Variable | Uso |
|---|---|
| `GEMINI_API_KEY` | Clave de Google AI Studio (proveedor `gemini`) |
| `DEEPSEEK_API_KEY` | Clave de la API de DeepSeek |
| `GROQ_API_KEY` | Clave de la API de Groq |
| `OLLAMA_URL` | URL del servidor Ollama (por defecto `http://localhost:11434`) |
| `OLLAMA_API_KEY` | Opcional, si tu servidor Ollama exige clave |
| `SNAPCONTEXT_PROVIDER` | Proveedor por defecto (opcional) |
| `SNAPCONTEXT_MODELO` | Modelo global por defecto (opcional) |
| `NO_COLOR` / `FORCE_COLOR` | Control de colores en la terminal |
| `AIDER_*` | Configuración heredada por Aider (KEY, model, etc.) |

---

## Compatibilidad y Permisos (Linux / macOS / Windows)

- **Windows**: Al instalar con `pip install snapcontext` o usando el one-liner (`install.ps1`), el instalador configura automáticamente el PATH del usuario permanentemente. Si instalaste manualmente sin usar el one-liner, ejecuta `snapcontext --setup-path` para añadir la carpeta al PATH.
- **Permisos de ejecución**: En Linux/macOS, al instalar con `pip install -e .` o `pip install snapcontext`, pip registra el ejecutable en el `PATH` del usuario de forma automática sin requerir permisos especiales. Si ejecutas `snapcontext.py` directamente como script en Unix, puedes asignarle permisos de ejecución con `chmod +x snapcontext.py`.
- **Servidor y Navegador**: En Linux y macOS, si `webbrowser.open()` no responde en entornos sin interfaz gráfica o con configuraciones personalizadas, SnapContext usa de forma automática los comandos nativos `xdg-open` (Linux) u `open` (macOS) como respaldo sin usar `shell=True`.
- **Manejo de Señales**: En Linux/macOS y Windows, la interrupción por teclado (`Ctrl+C` / `SIGINT`) o la señal de terminación (`SIGTERM` en Unix) capturan el evento, cierran limpiamente cualquier subproceso en segundo plano (como `flutter run`) y salen de forma ordenada con código `0`.

---

## Publicación en PyPI

SnapContext está preparado para publicarse en PyPI (el nombre `snapcontext`
está disponible; alternativas: `snapcontext-cli`, `snapcontext-tool`). Antes
de publicar, edita en `pyproject.toml` el campo `authors` (y `[project.urls]`)
con tus datos reales.

Sigue la guía completa en **[PUBLISHING.md](PUBLISHING.md)** (build + twine):

```bash
pip install build twine
python -m build
python -m twine check dist/*
python -m twine upload dist/*
pip install snapcontext   # verificar en un entorno limpio
```


---

## 🔗 Grafo de conocimiento (Graph RAG)

Desde **v5.5.0**, SnapContext puede combinar el análisis **AST** del proyecto con la **búsqueda semántica** (embeddings) para dar al agente una comprensión arquitectónica del código: no solo *qué archivos hablan del tema*, sino *también con quién se relacionan*.

### Cómo funciona

1. `graph_rag.py` construye un grafo con AST: **nodos** = archivos, funciones y clases; **aristas** = imports, llamadas a funciones y herencia.
2. El grafo se persiste en `~/.snapcontext/graph_cache.pkl` y solo se reconstruye cuando cambia algún `.py` (fingerprint por mtime + tamaño).
3. Cuando la búsqueda semántica devuelve archivos relevantes, el grafo añade hasta 3 archivos **relacionados** (quienes los importan y de quiénes dependen) al contexto del agente o del planificador.

### Activación

```bash
# Puntual
snapcontext "arreglar el checkout" --graph-rag

# Siempre activo
export SNAPCONTEXT_GRAPH_RAG=1
```

Solo Python en esta versión (tree-sitter para otros lenguajes llega en v5.6.0). El flag es completamente opcional: sin él, SnapContext se comporta igual que en 5.4.0.
---

## 🌐 Editor multi-lenguaje (Tree-sitter)

Desde **v5.6.0**, el editor propio (transaccional, fuzzy matching, análisis de
impacto y contexto selectivo) funciona también en proyectos **no Python**
gracias a [tree-sitter](https://tree-sitter.github.io/), un parser incremental
multi-lenguaje.

### Lenguajes soportados

| Extensión | Lenguaje |
|---|---|
| `.py` | Python (usa `ast` de la stdlib, como siempre) |
| `.js` / `.jsx` | JavaScript |
| `.ts` / `.tsx` | TypeScript |
| `.go` | Go |
| `.rs` | Rust |
| `.java` | Java |
| `.kt` | Kotlin |
| `.rb` | Ruby |
| `.php` | PHP |
| `.c` / `.h` | C |
| `.cpp` / `.hpp` / `.cc` | C++ |
| `.cs` | C# |
| `.swift` | Swift |
| `.dart` | Dart |

### Cómo funciona

1. **Detección** del lenguaje por extensión (`parser_universal.detectar_lenguaje_por_extension`).
2. **Parseo** con tree-sitter (`parsear_archivo`) para obtener el AST.
3. **Extracción** de funciones/clases/métodos (`extraer_nodos`) para el
   análisis de impacto y el contexto selectivo.
4. **Parche seguro** por bytes exactos del nodo (`aplicar_parche_arbol`).

La cadena de estrategias se mantiene intacta: **AST → Parche → Sobrescritura**.
Si tree-sitter no está instalado, el archivo está mal formado o el lenguaje no
está soportado, el editor cae elegantemente a las estrategias siguientes
(igual que en 5.5.0). Python **no** cambia: sigue usando el `ast` nativo.

### Instalación

```bash
pip install "snapcontext[editor_multilenguaje]"
# o directamente:
pip install tree-sitter tree-sitter-languages tree-sitter-language-pack
```


## 🙌 Agradecimientos

- **Aider** por su excelente motor de edición de código.
- **Google Gemini** por su generoso plan gratuito.
- **Ollama**, **DeepSeek** y **Groq** por sus modelos open-source.
- La comunidad open-source por las herramientas que hacen posible este proyecto.

## 🧠 Mostrar razonamiento del modelo

A partir de la versión 6.2.0, SnapContext puede mostrar el **razonamiento
interno (chain-of-thought)** del modelo antes de cada acción o respuesta, en
todos los modos principales (chat, planificador, editor propio y ReAct):

```bash
# Flag CLI
snapcontext "añade tests para el parser" --mostrar-razonamiento

# Variable de entorno (equivalente)
export SNAPCONTEXT_MOSTRAR_RAZONAMIENTO=1
```

Cómo funciona:

1. Si la respuesta del proveedor incluye un campo de razonamiento
   (`reasoning`, `thinking`, `chain_of_thought`, `reasoning_content`,
   `thoughts`) o bloques `<think>…</think>` (DeepSeek-R1 y otros modelos
   locales), se muestra en un panel antes del resultado.
2. Si el modelo no lo proporciona, se activa el **modo de dos pasos**: una
   llamada extra pide el razonamiento paso a paso y después se ejecuta la
   acción normal (con una advertencia, porque duplica las llamadas).
3. Los bloques `<think>…</think>` se eliminan del texto útil, de forma que no
   rompen el parseo de JSON del planificador ni los parches del editor.

El modo está **desactivado por defecto**: sin el flag ni la variable de
entorno, el flujo es idéntico al anterior y no añade ninguna llamada extra.

## 🌐 Omnicanalidad avanzada (v6.8.0)

SnapContext 6.8.0 introduce integración nativa con **GitHub Webhooks**, una **cola de tareas asíncronas persistente** (SQLite) y **notificaciones push** integradas con Telegram y Discord.

### 🐙 Integración con GitHub (`github_gateway.py`)
- **Validación de firmas HMAC SHA-256**: Verifica la autenticidad de los webhooks de GitHub en `POST /webhook/github` con tiempo constante.
- **Parseo y automatización**:
  - `pull_request` (opened, synchronize): Encola automáticamente una tarea `pr_review`.
  - `issues` (opened): Encola la planificación o resolución de incidencias (`plan`).
  - `push`: Ejecuta suites de pruebas automatizadas en segundo plano (`tests`).
- **Integración API REST de GitHub**: Permite consultar diffs (`obtener_pr_diff`) y publicar comentarios (`comentar_pr`) directamente en PRs e Issues.

#### Configuración de GitHub
```bash
# Configuración inicial de credenciales
snapcontext github setup --token ghp_xxxx --secret mi_secreto_webhook --webhook-url https://mi-dominio.com

# Registro del webhook en un repositorio de GitHub
snapcontext github webhook-registrar --repo owner/proyecto
```

### ⚡ Cola de Tareas Asíncronas y Worker (`task_queue.py`)
- Las tareas pesadas ya no bloquean la respuesta del webhook ni la CLI.
- Se persisten en la tabla SQLite `tareas` con estados `pendiente`, `ejecutando`, `completada`, `fallida`, `cancelada`.
- El daemon (`snapcontext --daemon`) o el worker en segundo plano consumen tareas y envían notificaciones push al finalizar.

### 💬 Nuevos Comandos en Telegram y Discord
- `/pr <numero>`: Encola la revisión asíncrona de un Pull Request.
- `/tests [rama]`: Encola la ejecución de pruebas en segundo plano.
- `/status`: Consulta el estado actual de las tareas recientes.
- `/cancel <id>`: Cancela una tarea en cola pendiente.

```
Usuario: /pr 42
SnapContext: 🔔 Tarea encolada (ID: 15) - Revisando PR #42 en segundo plano.
... (al finalizar)
SnapContext: ✅ Tarea 15 (pr_review) completada: Revisión de PR #42 completada con éxito.
```

## 🌐 Expansión de MCP (v6.7.0)

A partir de la versión 6.7.0, SnapContext expande su ecosistema MCP nativo con herramientas de **solo lectura** para interactuar con bases de datos relacionales e inspeccionar APIs externas.

### 🐘 Bases de Datos (`mcp_tools_db.py`)
Permite conectar a bases de datos y ejecutar consultas de lectura seguras integradas con el agente ReAct y el planificador:
- **`db_connect(url, driver=None)`**: Conexión perezosa a SQLite (nativo en Python), PostgreSQL (`psycopg2`) o MySQL (`pymysql`).
- **`db_query(consulta, auto=False)`**: Ejecuta consultas de **solo lectura** (`SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`, `PRAGMA`).
  - Bloquea estrictamente sentencias de modificación (`INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, etc.).
  - En modo interactivo solicita confirmación al usuario: `¿Ejecutar consulta: <consulta>? (s/n)`.
  - En modo `--auto` ejecuta sin preguntar (manteniendo la validación estricta de solo lectura).
- **`db_schema()`**: Obtiene la lista completa de tablas, columnas, tipos de datos y primary keys.
- **`db_disconnect()`**: Cierra la conexión activa y limpia el contexto de sesión.

#### Flags CLI para Bases de Datos
- `--db-url <url>`: URL de conexión (ej: `sqlite:///datos.db`, `postgresql://user:pass@localhost:5432/mi_db`, `mysql://user:pass@localhost:3306/mi_db`).
- `--db-driver <driver>`: Forzado opcional del driver (`sqlite`, `postgresql`, `mysql`).

```bash
# Conectar a SQLite y consultar esquemas / datos con ReAct
snapcontext "Analiza el esquema de la base de datos y dime cuántos usuarios hay" --db-url sqlite:///app.db

# Conectar a PostgreSQL
snapcontext "Verifica los pedidos pendientes" --db-url postgresql://postgres:secret@localhost:5432/ecommerce
```

### 🌐 APIs Externas (`mcp_tools_api.py`)
Permite inspeccionar endpoints REST y analizar respuestas:
- **`api_request(url, metodo="GET", headers={}, body="", timeout=15)`**: Realiza peticiones HTTP (`GET`, `POST`, `HEAD`, `OPTIONS`, etc.) mediante `httpx`.
  - Parsea respuestas JSON automáticamente.
  - Oculta cabeceras sensibles en las observaciones (`Authorization`, `Cookie`, `X-Api-Key`, etc.).
  - Aplica límite de seguridad al tamaño de cuerpo (`MAX_CUERPO = 200,000`).
- **`api_inspect(url, timeout=15)`**: Análisis rápido de salud y rendimiento (código HTTP, tiempo de respuesta en ms, tamaño en bytes, content-type y headers del servidor).

```bash
# Inspeccionar un endpoint y razonar sobre el resultado
snapcontext "Inspecciona https://api.github.com y dime qué headers de rate-limit devuelve" --react
```

## 🧠 Skills dinámicos (v6.6.0)

SnapContext ya no guarda solo pasos fijos: a partir de v6.6.0 el Curador
Proactivo extrae **reglas abstractas de arquitectura/estilo** de cada plan
exitoso y las reutiliza en futuras tareas.

### Cómo funciona
1. Tras un plan **totalmente exitoso** (de forma asíncrona, sin bloquear), se
   pide al LLM que resuma el plan como una regla: `patron`, `accion`,
   `archivos_afectados` y `dependencias`. Si el LLM falla, hay una heurística
   de respaldo (archivos más editados, confianza 0.6).
2. La regla se guarda en la tabla `reglas` de la base de datos SQLite. Si ya
   existía una regla similar (similitud ≥ 0.85), se **refuerza**: +0.05 de
   confianza y +1 uso, sin duplicar.
3. Las reglas con confianza > 0.8 se **inyectan automáticamente** en
   `CLAUDE.md`/`SNAPCONTEXT.md`, en la sección `## Reglas aprendidas`
   (idempotente: nunca se duplican líneas).
4. Antes de generar un plan, el planificador busca reglas cuyo patrón coincida
   con la tarea (similitud ≥ 0.45) y **enriquece el prompt** con las de mayor
   confianza, bajo el encabezado `REGLAS APRENDIDAS`.

### Flags
- `--skills-dinamicos` (activado por defecto): extracción y aplicación de
  reglas abstractas.
- `--sin-skills-dinamicos`: desactiva todo el sistema de reglas.
- `--inyectar-reglas`: fuerza la inyección de todas las reglas aprendidas en
  `CLAUDE.md` y termina (útil tras actualizar desde versiones anteriores).

Mensajes que verás:
```
🧠 Extrayendo regla abstracta del plan exitoso...
📝 Nueva regla aprendida: añadir endpoint de pagos (confianza: 1.0)
📄 Regla inyectada en CLAUDE.md
```

Seguridad: las reglas se sanitizan antes de inyectarse (sin bloques de código
` ``` ` ni caracteres de control, longitud máxima por campo), y nunca se
ejecutan: solo enriquecen prompts y documentación.

## 🌐 UI Web interactiva (v6.5.0)

Con `--web --web-interactive`, la interfaz web se convierte en un **centro de
control para agentes autónomos** (además de la web clásica, que sigue intacta):

```bash
snapcontext --web --web-interactive
# ℹ Interfaz web en http://localhost:8000  (Ctrl+C para salir)...
# ℹ 🌐 Interfaz web interactiva: http://localhost:8000/interactive
```

Abre `http://localhost:8000/interactive` en tu navegador y verás:

- **Timeline de ReAct**: cada ciclo Pensamiento → Acción → Observación llega en
  tiempo real por WebSocket (`/ws/interactive`) como una tarjeta coloreada
  (🟡 pensamiento, 🔵 acción, 🟢 observación, 🔴 error), con marca de tiempo
  relativa ("hace Xs") y detalles expandibles.
- **Diff viewer interactivo**: si el editor propio no puede aplicar un parche,
  se abre un modal con Monaco Editor en modo diff (original ⇆ propuesto).
  Puedes *Aceptar todo*, *Rechazar todo* o editar y *Aceptar líneas
  seleccionadas*; el resultado se envía de vuelta por WebSocket y se aplica de
  forma transaccional (solo tras tu confirmación explícita).
- **Panel de control**: estado del agente, contador de pasos, tiempo total y
  logs estructurados con niveles (info/warning/error).

Notas:
- El modo es **opt-in**: sin `--web-interactive`, `--web` funciona exactamente
  como antes.
- El hub (`web/interactive.py`) es no bloqueante: los eventos se descartan si
  la cola está llena, nunca ralentizan al agente.
- Los contenidos se validan y recortan (máx. 400k caracteres) antes de
  transportarse; el contenido aceptado en el diff viewer solo se escribe en el
  archivo objetivo.

## 🧠 Modo inteligente (v6.23.0)

A partir de v6.23.0 **ya no necesitas flags**: escribe tu tarea y SnapContext
decide solo qué hacer.

```bash
snapcontext "arregla el botón de pago"
```

Qué hace el modo inteligente:

1. **Detecta la complejidad** de la consulta y elige el modo de operación
   (`chat`, `plan`, `react`, `react_paralelo`) automáticamente.
2. **Detecta el tipo de tarea**: palabras como "arreglar", "corregir",
   "refactorizar" activan el planificador; "analizar", "revisar", "leer"
   activan ReAct con herramientas de lectura.
3. **Usa `--local` automáticamente** si no hay API key configurada.
4. **Muestra el razonamiento** del agente (`💭 ...`) por defecto.
5. **Reduce confirmaciones**: pregunta una sola vez al principio de la tarea
   (`📋 Plan: voy a 1) leer... 2) corregir... 3) probar. ¿Continuar? (s/n)`)
   en lugar de en cada paso.
6. **Es 100% opcional**: los flags explícitos (`--plan`, `--react`, `--auto`,
   `--paralelo`, etc.) tienen prioridad máxima y el comportamiento es
   idéntico al de siempre. Los comandos peligrosos (`rm -rf`, ...) siguen
   pidiendo confirmación explícita.

Si prefieres el comportamiento clásico (flags obligatorios, sin magia):

```bash
# PowerShell / Bash
$env:SNAPCONTEXT_MODO_DEFAULT = "manual"    # export SNAPCONTEXT_MODO_DEFAULT=manual
```

## 🧠 Memoria a largo plazo (v6.26.0)

A partir de v6.26.0 SnapContext **recuerda decisiones pasadas** entre sesiones
usando una base de datos SQLite local (`~/.snapcontext/memoria.db`).

```bash
snapcontext "arreglar el login"
# 🧠 Memoria a largo plazo activada (3 decisiones guardadas).
# 📖 Decisiones previas similares:
#   1) [exito] arreglar login: usar JWT para autenticacion...
#   2) [exito] optimizar consultas: agregar indices...
```

### Cómo funciona

1. **Al finalizar una tarea**, la decision se guarda automaticamente
   (tarea, archivos modificados, razonamiento, resultado).
2. **Al iniciar una nueva tarea**, se consulta el historial y se muestran
   decisiones previas similares.
3. **El historial se integra con GraphRAG** para enriquecer el contexto.

### Configuración

| Flag | Efecto |
|---|---|
| `--memoria` | Activa la memoria (por defecto). |
| `--no-memoria` | Desactiva la memoria. |
| `--memoria-limite N` | Maximo de decisiones a guardar (def: 100). |
| `--memoria-ver` | Muestra las ultimas decisiones y sale. |

### Privacidad

- Los datos se guardan **localmente** en `~/.snapcontext/memoria.db`.
- No se envia nada a servidores externos.
- Puedes limpiar el historial con `--memoria-limite 0` o eliminando el archivo.

## 🧪 QA Tester adversarial (v6.25.0)

A partir de v6.25.0 SnapContext incluye un **QA Tester adversarial** que revisa
automáticamente el código generado por el Programador, buscando errores de
seguridad, rendimiento, lógica y estilo.

```bash
snapcontext "implementa login con JWT" --multi-agent
# 🧪 QA Tester: revisando auth.py...
# ❌ QA Tester: 2 hallazgo(s) en auth.py.
# 🧪 QA Tester: problemas encontrados. Realimentando al Programador...
# 🧪 QA Tester: auth.py aprobado.
```

### Cómo funciona

1. **El Programador genera código** usando el editor propio de SnapContext.
2. **El QA Tester revisa** el código con un prompt adversarial (busca SQL
   injection, XSS, path traversal, fugas de memoria, off-by-one, etc.).
3. **Si encuentra problemas**, el Supervisor realimenta al Programador con
   las sugerencias y repite el ciclo.
4. **El código se aprueba** cuando el QA Tester no halla problemas o se
   alcanza el número máximo de iteraciones.

### Configuración

| Flag | Efecto |
|---|---|
| `--qa-tester` | Activa el QA Tester (por defecto). |
| `--no-qa-tester` | Desactiva el QA Tester. |
| `--qa-iteraciones N` | Máximo de iteraciones Programador ↔ QA Tester (def: 2). |
| `--qa-severidad {baja,media,alta}` | Exigencia del revisor (def: media). |

### Qué revisa

- **Seguridad**: inyección SQL, XSS, path traversal, command injection,
  secretos hardcodeados.
- **Rendimiento**: complejidad algorítmica, fugas de memoria, consultas N+1.
- **Lógica**: off-by-one, manejo de nulos, condiciones de carrera.
- **Estilo**: código duplicado, nombres poco descriptivos.

## 🧠 Autonomía Multi-Modelo (v6.24.0)

A partir de v6.24.0 SnapContext **elige el mejor modelo para cada tarea**,
optimizando costes (modelos locales/económicos para tareas simples) y calidad
(modelos premium para tareas críticas).

```bash
snapcontext "arregla el botón de pago"
# 🧠 Modelo enrutado: edicion_critica → gemini/gemini-2.5-pro
```

### Cómo funciona

1. **Clasifica la tarea** por complejidad/tipo usando heurísticas rápidas
   (sin llamadas a la IA):
   - `indexacion` — generar/actualizar índices, embeddings.
   - `busqueda_semantica` — búsqueda semántica / selección de archivos.
   - `planificacion_simple` — descomponer tareas en pasos.
   - `edicion_critica` — editar archivos (cambios en el código).
   - `razonamiento_complejo` — análisis largo, arquitectura, debugging difícil.
   - `chat_general` — conversación / consultas genéricas.
2. **Selecciona el modelo** más adecuado según tu configuración en `config.json`
   (sección `model_routing`).
3. **Muestra el enrutamiento** al inicio de cada tarea.

### Configuración

Edita `config.json` en la raíz del proyecto:

```json
{
  "model_routing": {
    "indexacion": {"provider": "ollama", "model": "qwen3.5:9b"},
    "busqueda_semantica": {"provider": "ollama", "model": "qwen3.5:9b"},
    "planificacion_simple": {"provider": "deepseek", "model": "deepseek-v3"},
    "edicion_critica": {"provider": "gemini", "model": "gemini-2.5-pro"},
    "razonamiento_complejo": {"provider": "anthropic", "model": "claude-3.7-sonnet"},
    "chat_general": {"provider": "ollama", "model": "qwen3.5:9b"}
  }
}
```

Si no configuras nada, SnapContext usa el modelo por defecto (comportamiento
idéntico al anterior — compatibilidad total).

### Flags

| Flag | Efecto |
|---|---|
| `--model-routing` | Activa el enrutamiento (por defecto). |
| `--no-model-routing` | Desactiva el enrutamiento, usa el modelo único. |
| `--model <nombre>` | Prioridad absoluta, ignora el enrutamiento. |
| `--provider <proveedor>` | Prioridad absoluta, ignora el enrutamiento. |

### Extensión

Para añadir una nueva categoría:

1. Agrega la constante en `model_router.py` (`CATEGORIAS`).
2. Añade las palabras clave en las tuplas `_KW_*`.
3. Agrega la entrada en `config.json` (`model_routing.<categoria>`).

## Licencia
## Licencia

MIT. Open-source y libre de usarlo, estudiarlo y mejorarlo.
