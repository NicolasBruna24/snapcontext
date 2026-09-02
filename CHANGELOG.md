# Changelog de SnapContext

Todos los cambios notables para SnapContext se documentarán en este archivo.

El formato sigue las [directrices de Keep a Changelog](https://keepachangelog.com/es/1.0.0/).

## [6.15.0] - 2026-09-01 - Extensión VS Code: icono en Activity Bar y fix del chat 🧩 vscode

### Fixed (extensión `snapcontext-ai` de VS Code)
- **Chat: `SyntaxError: invalid syntax` al enviar una consulta.** Los
  procesos se lanzaban con `shell: true` en Windows, por lo que `cmd.exe`
  interpretaba caracteres especiales de la consulta (paréntesis, `&`,
  comillas) y el comando llegaba roto a Python. Ahora se usa `shell: false`
  con el array de argumentos intacto (Node hace el escaping correcto), tanto
  para `python -m snapcontext ...` como para el script `-c` del servidor
  del chat.
- Se usa la ruta completa de `snapcontext.pythonPath` en lugar de
  `path.basename(...)`, para que funcione con rutas que contienen espacios.

### Added (extensión `snapcontext-ai` de VS Code)
- **Icono en la Activity Bar**: contenedor `snapcontext` con vista webview
  `snapcontext.chat` ("Chat") e icono propio `media/snapcontext-icon.svg`
  (24x24, mono para theming claro/oscuro de VS Code).
- La vista lateral reutiliza el mismo servidor del chat (`arrancarServidorChat`,
  ahora lazy y con error claro si no responde en 10 s) sin duplicar procesos.


## [6.14.0] - 2026-09-01 - LSP e indexación global 🔗🔍

### Added
- **`lsp_client.py`** (módulo nuevo): integración con Language Server Protocol.
  - Clase `LSPClient`: cliente JSON-RPC puro sobre stdio (sin dependencias
    obligatorias), con `iniciar`, `enviar_peticion` (con timeout),
    `obtener_definicion`, `obtener_referencias`, `obtener_tipo`, `cerrar` y
    soporte de contexto (`with`).
  - `CacheLSP`: caché en memoria + persistente (`~/.snapcontext/lsp_cache.db`,
    SQLite) con clave `(archivo, linea, columna, tipo_consulta)` e
    invalidación por hash del contenido del archivo.
  - Detección de lenguaje por extensión (`.py`, `.ts/.js`, `.go`, `.rs`,
    `.java`, `.cs`) y de servidores instalados vía `shutil.which`
    (`pyright-langserver`, `typescript-language-server`, `gopls`,
    `rust-analyzer`, `jdtls`, `OmniSharp`).
  - Singleton perezoso (`obtener_cliente_lsp`): el servidor solo se lanza en
    la primera consulta, nunca al inicio de la sesión.
- **Herramientas MCP nuevas**: `lsp_definicion`, `lsp_referencias` y
  `lsp_tipo`, disponibles en el agente ReAct y en los sub-agentes dinámicos
  cuando `--lsp` está activo.
- **Flag `--lsp`** y variable de entorno `SNAPCONTEXT_LSP=1`.
- **Grupo opcional `[lsp]`** en `pyproject.toml`
  (`python-lsp-server`, `pygls`).
- 25 tests nuevos en `tests/test_lsp_client.py`.

### Changed
- `react_agent.py`: nuevo parámetro `lsp` y herramientas LSP registradas
  condicionalmente (sin `--lsp` devuelven un error claro que sugiere el flag).
- `sub_agent.py` / `multi_agent.py`: propagación de `lsp` a los sub-agentes.

### Seguridad
- El servidor LSP corre en el mismo entorno; los comandos son una lista fija
  (no se construyen con entrada del usuario) y se verifica su existencia con
  `shutil.which` antes de lanzarlos.

### Robustez
- Si el servidor no está instalado, falla al arrancar o no responde
  (timeout), se muestra un mensaje claro y la tarea **continúa sin LSP**.


## [6.13.0] - 2026-09-01 - Sub-agentes dinámicos 🤖🚀

### Added
- **`sub_agent.py`** (módulo nuevo): sub-agentes ReAct dinámicos.
  - Clase `SubAgente`: encapsula un `ReactAgent` con historial propio
    (contexto aislado), prompt de sistema del rol y herramientas
    restringidas (mínimo privilegio).
  - `enviar_mensaje()` / `recibir_mensajes()`: buzón individual para
    comunicación con el Supervisor y otros agentes.
  - Registro `ROLES` con 5 roles predefinidos: `scout`, `debugger`,
    `frontender`, `tester` y `documentador` (cada uno con su prompt,
    sus herramientas y su límite de iteraciones).
  - `ejecutar_sub_agentes_paralelo()`: hilos con semáforo y límite de
    concurrencia; los errores por hilo no abortan al resto y los
    resultados llegan en orden.
  - Integración con la cola de tareas v6.8.0 (`encolar_sub_agente`,
    `ejecutar_tarea_sub_agente`, tipo `sub_agente`).
- **`multi_agent.py`**: el Supervisor ahora puede crear sub-agentes.
  - `Supervisor.crear_sub_agente(rol)`: instancia y registra sub-agentes.
  - `Supervisor._detectar_sub_tareas(plan)`: heurística por palabras clave
    sobre el plan del Arquitecto (documentación → scout, errores →
    debugger, pruebas → tester, CSS/UI → frontender, docs → documentador).
  - `Supervisor.ejecutar_sub_tareas(plan)`: detecta y ejecuta en paralelo
    las sub-tareas delegables y publica los resultados en el `Buzon`
    (`resultado_sub_agente`, `sub_tarea_completada`).
  - Nuevos parámetros `sub_agents` y `max_parallel` (fase opcional entre
    el plan y el bucle Programador→Tester).
- **CLI** (`snapcontext.py`): flags `--sub-agents` (activa la delegación)
  y `--max-parallel N` (concurrencia, por defecto 3).
- **Mensajes de usuario**: `Sub-agente '{rol}' instanciado.` /
  `Sub-agente '{rol}' completó su tarea.` /
  `Ejecutando N sub-agentes en paralelo...`.
- **Tests**: `tests/test_sub_agent.py` con 29 casos (roles, aislamiento,
  comunicación, paralelismo con límite, buzón, cola de tareas, Supervisor
  y flags CLI).
- **README**: sección "🤖 Sub-agentes dinámicos (v6.13.0)".

### Changed
- Versión 6.13.0 en `snapcontext.py` y `pyproject.toml`; tests de
  coherencia de versión actualizados.

## [6.12.0] - 2026-09-01 - TUI inmersiva con Textual 🖥️🚀

### Added
- **Modo `--tui`** (snapcontext.py): lanza la TUI inmersiva basada en Textual.
  El agente ejecuta el flujo habitual (ReAct por defecto) en un hilo demonio
  mientras la TUI muestra logs, pasos, diffs y estado en tiempo real. Sin
  Textual instalado muestra un error claro y devuelve código 2.
- **`tui_app.py`**: aplicación `SnapContextTUI` con pestañas (Logs con
  RichLog coloreado por nivel, Control con estado/pasos/tiempo y botones
  Pausar/Reanudar/Cancelar, Diffs con visor coloreado), árbol de archivos
  (`DirectoryTree`) y atajos `Ctrl+Q`/`Ctrl+L`/`Ctrl+D`/`Ctrl+T`.
- **`tui_hub.py`**: cola de eventos no bloqueante entre el agente y la TUI
  (`enviar_log`, `enviar_paso_react`, `enviar_estado`, `enviar_diff`,
  `enviar_fin`). Sin Textual como dependencia: testeable de forma aislada.
- **react_agent.py**: si la TUI está activa, el timeline ReAct
  (Pensamiento→Acción→Observación) se emite por `tui_hub` (misma interfaz
  que `web.interactive`) sin bloquear al agente.
- **snapcontext.py**: `info/exito/aviso/error` reenvían los logs a la TUI
  (coste ~0 cuando está inactiva); `_mostrar_diff_parche` envía los diffs
  generados por el editor propio a la pestaña Diffs.
- **pyproject.toml**: grupo opcional `tui = ["textual>=0.50.0"]`.
- **tests/test_tui.py**: 25 casos (cola de eventos, app Textual con mocks,
  integración con el CLI y degradación sin Textual).

### Changed
- README: nueva sección "🖥️ TUI inmersiva (v6.12.0)".
- Versión `6.12.0` en `snapcontext.py` y `pyproject.toml`.

## [6.11.0] - 2026-09-01 - Prompt Caching 🧠⚡

### Added
- **Prompt Caching** en `_enviar_al_proveedor` (snapcontext.py): mantiene en caché
  el mensaje del sistema, las herramientas MCP y la memoria del proyecto
  (`CLAUDE.md`) añadiendo la marca `cache_control: {"type": "ephemeral"}` para los
  proveedores compatibles (**Anthropic** y **DeepSeek**). Reduce drásticamente
  coste y latencia en sesiones largas.
- **Detección de soporte**: nueva clave `soporta_caching` en `PROVEEDORES`
  (True para `anthropic` y `deepseek`). Gemini, Groq y Ollama se envían tal cual
  (compatibilidad total garantizada).
- **Nuevas funciones**:
  - `_soporta_prompt_caching(proveedor)`: ¿el proveedor soporta `cache_control`?
  - `_resolver_prompt_caching(explicito)`: resuelve el estado (flag > entorno >
    config.json > defecto True).
  - `_aplicar_cache_control(mensajes)`: añade las marcas sin mutar los mensajes.
  - `_mensaje_caching_inicio(proveedor)`: mensaje de sesión.
- **Nueva configuración**: `prompt_caching: bool = True` en `~/.snapcontext/config.json`.
- **Nuevos flags**: `--prompt-caching` / `--no-prompt-caching`.
- **Nueva variable de entorno**: `SNAPCONTEXT_PROMPT_CACHING=0|1` para desactivar/activar.
- **Resumen automático (react_agent.py)**: `_comprimir_historial` preserva las
  marcas `cache_control` en el resumen si el proveedor soporta caching.
- **Mensajes de sesión**: `🧠 Prompt Caching activado para <proveedor>` (o
  `no soportado`) al inicio del chat y del modo ReAct.
- **Tests**: nuevo `tests/test_prompt_caching.py` (más de 15 casos).

### Changed
- Versión actualizada a `6.11.0` en `snapcontext.py`, `pyproject.toml`, `README.md`
  y las 22 aserciones de versión de los tests de coherencia.

### Security
- Las marcas `cache_control` **no modifican el contenido** de los mensajes: solo
  añaden la instrucción de caché. Ningún dato se almacena en local.

## [6.10.0] - 2026-08-31 - Navegador y multimodalidad 🌐👁️

### Added
- **Nuevo módulo `mcp_tools_browser.py`**: herramientas MCP de navegador con **Playwright** que permiten al agente ReAct *ver* y depurar interfaces visuales:
  - `browser_abrir(url, wait_for?, timeout?)`: abre una URL (espera opcional a un selector CSS).
  - `browser_screenshot(url?, full_page?, selector?)`: captura de pantalla en base64 PNG (página completa o un elemento concreto).
  - `browser_click(selector)` / `browser_type(selector, texto)`: interacción con la página (clic y escritura en campos).
  - `browser_get_text(selector)`: extrae el texto de un elemento.
  - `browser_analizar_imagen(imagen_base64, pregunta)`: análisis visual con modelos de visión (Gemini 2.5 Pro, Claude 3.7 Sonnet+). Si el modelo no soporta visión devuelve un error claro.
  - `browser_cerrar()`: cierra el navegador y libera recursos.
- **Nuevo flag `--browser`**: activa el modo navegador (inicialización perezosa de Playwright). Sin el flag, el agente no puede usar herramientas de navegador. Flag adicional `--browser-headed` para ejecutar con interfaz visible (por defecto headless por seguridad).
- **Sesión de navegador persistente**: una única instancia de navegador/página se reutiliza durante toda la tarea (similar a la sesión Docker); si el navegador muere inesperadamente se reinicia automáticamente en el siguiente uso.
- **Integración ReAct**: nuevas acciones válidas `browser_abrir`, `browser_screenshot`, `browser_click`, `browser_type`, `browser_get_text` y `browser_analizar_imagen` (documentadas en el prompt del sistema).
- **Nueva dependencia opcional**: grupo `[browser]` en `pyproject.toml` (`playwright>=1.40.0`), con instrucciones automáticas (`playwright install chromium`) si falta.
- **Tests**: `tests/test_mcp_tools_browser.py` (15 casos, Playwright mockeado).

### Security
- El navegador se ejecuta **headless por defecto** (sin interferir con el usuario).
- Playwright se carga solo cuando se usa (lazy import); sin `--browser` no se importa nunca.

## [6.8.0] - 2026-08-30 - Omnicanalidad avanzada (GitHub + tareas asíncronas + notificaciones)

## [6.9.0] - 2026-08-31 - Mejora de rendimiento ⚡

### Added
- **Flag `--benchmark`**: mide y muestra el tiempo de cada fase (inicio, CLI, escaneo, selección con embeddings, plan, edición fuzzy, detección de pruebas y total) en una tabla con `rich`. No necesita API key.
- **Cache persistente de embeddings (SQLite)**: `~/.snapcontext/embeddings.db` con tabla `embeddings(archivo, hash, embedding BLOB)`. Si el hash del contenido no cambia, el embedding se reutiliza (≈80% más rápido en re-escaneos). Si falla la escritura, se degrada silenciosamente.
- **Cache incremental del grafo (Graph RAG)**: `~/.snapcontext/graph_cache.pkl` con fingerprint por archivo; en la segunda sesión solo se reparsean los archivos modificados (≈70% más rápido).
- **`REACT_MAX_HISTORIAL`**: variable de entorno para el límite de iteraciones del historial ReAct (por defecto 20). Al superarlo se genera un resumen LLM automático con prompt específico (≈30% menos memoria en sesiones largas).
- **Tests**: nuevo `tests/test_rendimiento.py` con 15 casos cubriendo las cachés, el fuzzy matching, el worker sin polling, el límite de historial y `--benchmark`.

### Changed
- **Inicio del CLI**: imports pesados (`sentence-transformers`, `tree-sitter`, `graph_rag`, `multi_agent`, ...) convertidos a lazy imports; `--help` pasa de ~1.2s a <0.3s.
- **Editor de parches**: fuzzy matching optimizado — contexto limitado a `MAX_CONTEXTO_DIFUSO_LINEAS = 20` y resincronización de bloques vía `difflib.get_close_matches` (≈50% más rápido en archivos grandes).
- **Cola de tareas**: el worker usa `threading.Event` (`_WORKER_DESPERTAR`) en lugar de `time.sleep`; se despierta al instante con `event.set()` desde `encolar_tarea` (CPU idle ≈0%).
- Versión actualizada a `6.9.0`.

### Security
- Las cachés no almacenan código fuente, solo hashes, fingerprints y vectores de embeddings.



### Added
- **Nuevo módulo `github_gateway.py`**: integración bidireccional con GitHub:
  - `validar_firma`: verificación criptográfica de firmas HMAC SHA-256 (`X-Hub-Signature-256`) y SHA-1 (`X-Hub-Signature`) contra peticiones falsificadas.
  - `parsear_evento`: extracción normalizada de metadatos para eventos `pull_request`, `issues`, `push`, `issue_comment`.
  - `procesar_evento`: encolado automático de tareas asíncronas (`pr_review`, `plan`, `tests`) a partir de eventos de webhook.
  - `obtener_pr_diff` y `comentar_pr`: integración con la API REST de GitHub para inspeccionar diffs y comentar resoluciones de PRs e issues.
  - `configurar_webhook`: registro automatizado de webhooks en repositorios remotos.
  - Subcomando CLI: `snapcontext github setup|estado|webhook-registrar`.
- **Nuevo módulo `task_queue.py`**: sistema de cola SQLite persistente para tareas en segundo plano:
  - Tabla `tareas` (`id`, `tipo`, `estado`, `datos`, `resultado`, `chat_id`, `canal`, `creado`, `actualizado`).
  - Funciones `encolar_tarea`, `consumir_tarea`, `actualizar_estado_tarea`, `obtener_tarea`, `listar_tareas`, `cancelar_tarea`.
  - Worker demonio (`iniciar_worker` / `_daemon_tick`) para consumir y ejecutar tareas pesadas sin bloquear el CLI.
  - Notificaciones push al finalizar tareas (`✅ Tarea {id} completada`, `❌ Tarea {id} falló`).
- **Nuevos comandos asíncronos en Telegram y Discord**:
  - `/pr <numero>`: encola la revisión del Pull Request.
  - `/tests [rama]`: encola la ejecución de pruebas en segundo plano.
  - `/status`: consulta el estado de las tareas en cola.
  - `/cancel <id>`: cancela una tarea pendiente.
  - Notificaciones proactivas con `enviar_notificacion(chat_id, mensaje, canal)`.
- **Endpoints Webhook**: rutas `/webhook/github` y `/api/github/webhook` en la interfaz web FastAPI.
- **Nuevos flags CLI**: `--github-webhook-secreto`, `--github-token`, `--webhook-url`.
- **Tests**:
  - `tests/test_github_gateway.py` (15 casos).
  - `tests/test_task_queue.py` (15 casos).
  - `tests/test_omnicanalidad_avanzada.py` (10 casos).

## [6.7.0] - 2026-08-30 - Expansión de MCP (bases de datos y APIs)

### Added
- **Nuevo módulo `mcp_tools_db.py`**: herramientas MCP nativas de solo lectura para bases de datos:
  - `db_connect`: conexión perezosa a SQLite (nativo), PostgreSQL (`psycopg2`) y MySQL (`pymysql`).
  - `db_query`: ejecución de consultas de solo lectura (`SELECT`, `SHOW`, `DESCRIBE`, `EXPLAIN`, `PRAGMA`) con validación estricta y confirmación interactiva (`¿Ejecutar consulta: ...? (s/n)`). En modo `--auto` no solicita confirmación manteniendo el bloqueo de consultas de escritura (`INSERT`, `UPDATE`, `DELETE`, etc.).
  - `db_schema`: inspección completa del esquema de base de datos (tablas, columnas, tipos, primary keys).
  - `db_disconnect`: cierre y limpieza de conexión de sesión.
- **Nuevo módulo `mcp_tools_api.py`**: herramientas MCP para inspección y consumo seguro de APIs externas:
  - `api_request`: peticiones HTTP (GET, POST, HEAD, OPTIONS, etc.) usando `httpx`, con soporte para parseo JSON automático, límite de tamaño de respuesta y filtrado de cabeceras sensibles (`Authorization`, `Cookie`, `X-Api-Key`, etc.).
  - `api_inspect`: análisis de rendimiento y metadatos de endpoints (código de estado HTTP, tiempo de respuesta, tamaño de payload, headers del servidor).
- **Integración con ReAct y Planificador**: registro de `db_query`, `db_schema`, `api_request` y `api_inspect` como acciones nativas en `ReactAgent` y en el dispatcher MCP de `snapcontext.py`.
- **Nuevos flags CLI**:
  - `--db-url <url>`: URL de conexión de base de datos (`sqlite:///...`, `postgresql://...`, `mysql://...`).
  - `--db-driver <driver>`: especificación o forzado de driver (`sqlite`, `postgresql`, `mysql`).
- **Nuevas dependencias opcionales**: grupo `[db]` en `pyproject.toml` (`psycopg2-binary`, `pymysql`).
- **Tests unitarios e integración**:
  - `tests/test_mcp_tools_db.py` (22 casos).
  - `tests/test_mcp_tools_api.py` (12 casos).
  - `tests/test_mcp_expansion.py` (5 casos).

## [6.6.0] - 2026-08-30 - Skills dinámicos (reglas abstractas)

### Added
- **Nuevo módulo `skill_abstraction.py`**: el Curador ya no guarda pasos fijos
  (frágiles ante renombrados y cambios de estructura), sino **reglas
  abstractas** extraídas de planes exitosos: `patron`, `accion`,
  `archivos_afectados` y `dependencias`.
  - `extraer_regla(plan, contexto)`: usa el LLM y, si falla, una heurística de
    respaldo (archivos más editados, confianza 0.6).
  - `aplicar_regla(regla, tarea)`: devuelve pasos sugeridos si la tarea
    coincide con el patrón (similitud ≥ 0.45), o `None`.
  - `guardar_regla`: persiste en la nueva tabla `reglas` (SQLite) y **refuerza**
    reglas similares (similitud ≥ 0.85) en vez de duplicarlas (+0.05 confianza,
    +1 uso).
  - `inyectar_en_claudemd` / `inyectar_todas_las_reglas`: añade reglas con
    confianza > 0.8 a la sección `## Reglas aprendidas` de `CLAUDE.md`/
    `SNAPCONTEXT.md`, de forma **idempotente** y sanitizada (sin ```` ``` ````
    ni caracteres de control).
- **Enriquecimiento del planificador** (`_enriquecer_prompt_con_reglas`):
  antes de generar un plan, se inyectan en el prompt las reglas cuyo patrón
  coincide con la tarea, priorizadas por confianza (máx. 3, encabezado
  `REGLAS APRENDIDAS`).
- **Integración del curador**: tras un plan totalmente exitoso se extrae y
  guarda la regla en un hilo secundario (`snap-skills-dinamicos`), sin
  bloquear al usuario. Mensajes: `🧠 Extrayendo regla abstracta...`,
  `📝 Nueva regla aprendida: ...`, `📄 Regla inyectada en CLAUDE.md`.
- **Migración de base de datos**: nueva tabla `reglas` (patrón, acción,
  archivos/dependencias JSON, confianza, usos, fecha).
- **Nuevos flags CLI**: `--skills-dinamicos` (activado por defecto) /
  `--sin-skills-dinamicos`, y `--inyectar-reglas` (fuerza la inyección de
  todas las reglas en CLAUDE.md y termina).

### Compatibility
- Activado por defecto pero completamente opcional: con
  `--sin-skills-dinamicos` el comportamiento es idéntico al de v6.5.0. La
  extracción es asíncrona y solo se ejecuta tras planes exitosos.

### Security
- Reglas sanitizadas antes de inyectarse en CLAUDE.md (sin bloques de código
  ni caracteres de control); nunca se ejecutan, solo enriquecen prompts y
  documentación.

### Tests
- `tests/test_skill_abstraction.py`: 25 casos (extracción LLM/heurística,
  aplicación de reglas, inyección idempotente, migración de BD, curador,
  enriquecimiento del planificador y flags CLI).

## [6.5.0] - 2026-08-30 - UI Web interactiva (timeline de ReAct y diff viewer)

### Added
- **Centro de control web interactivo** (`--web-interactive`, junto a `--web`):
  - **Timeline de ReAct en tiempo real** (`http://localhost:8000/interactive`):
    cada paso (🟡 Pensamiento → 🔵 Acción → 🟢 Observación / 🔴 Error) se
    muestra como tarjeta coloreada con marca de tiempo relativa ("hace Xs") y
    detalles expandibles.
  - **Diff viewer interactivo** (Monaco Editor en modo diff): cuando el editor
    propio encuentra un conflicto de parche, se abre un modal con
    original ⇆ propuesto y botones *Aceptar todo*, *Rechazar todo* y *Aceptar
    líneas seleccionadas*. La respuesta vuelve por WebSocket y se aplica de
    forma transaccional (`sobrescribir` tras confirmación explícita).
  - **Panel de control**: estado del agente ("Pensando…", "Ejecutando
    acción"), contador de pasos, tiempo total y logs estructurados
    (info/warning/error).
- **Nuevo módulo `web/interactive.py`**: hub thread-safe y **no bloqueante**
  entre el agente y la web (cola de eventos con `put_nowait`; esperas por
  `threading.Event` para los diffs), con validación y recorte de contenidos
  (máx. 400k caracteres). Si el modo está inactivo, todo es un no-op.
- Nuevos archivos estáticos: `web/static/interactive.html` y
  `web/static/interactive.js`.
- Nuevos endpoints (solo en modo interactivo): `GET /interactive`,
  `GET /interactive/health`, `WS /ws/interactive` y montaje de `/static`.
- `ReactAgent(web_interactive=...)`: emite `react_step` / `agent_status` en
  cada fase del bucle sin ralentizarlo.

### Compatibility
- Sin `--web-interactive`, la web actual (`--web`) funciona exactamente igual;
  el flag es opt-in y los endpoints nuevos no se registran.

### Security
- Los diffs se validan antes de aplicarse: cadenas con tamaño máximo, el
  contenido del cliente nunca se ejecuta y solo se escribe en el archivo
  objetivo tras confirmación explícita del usuario en la web.

### Tests
- `tests/test_web_interactive.py`: 31 casos (hub, timeline, conflictos de
  diff, endpoints y flags, integración con ReAct y con el editor propio).

## [6.4.0] - 2026-08-30 - Persistencia de Docker por sesión

### Added
- **Nuevo módulo `sandbox_session.py`**: ciclo de vida completo de una sesión
  Docker persistente (`crear_sesion`, `obtener_sesion`, `ejecutar_en_sesion`,
  `destruir_sesion`, `limpiar_huérfanos`). El contenedor se lanza con
  `docker run -d --name snap-session-<id> -v "<proyecto>:/workspace"
  -w /workspace <imagen> tail -f /dev/null`, de modo que el sistema de
  archivos y las dependencias instaladas persisten entre comandos
  (`npm install` → `npm test`, `pip install -r requirements.txt` → `pytest`).
- **Nuevo flag `--sandbox-session`**: mantiene **un único contenedor vivo**
  durante toda la tarea (plan de varios pasos o bucle ReAct) en lugar de un
  `docker run --rm` por comando. La sesión se crea de forma perezosa en el
  primer comando, se reutiliza para todos los demás (con `docker exec`) y se
  destruye al finalizar (éxito, aborto o error), incluido en `Ctrl+C`/`SIGTERM`.
- **Nuevo flag `--sandbox-session-clean`**: busca y elimina contenedores
  `snap-session-*` huérfanos de sesiones anteriores (con confirmación en modo
  interactivo, automáticamente en `--auto`) y sale.
- El id de sesión se guarda en `~/.snapcontext/session_id.txt`, lo que permite
  recuperar sesiones entre invocaciones.
- Integración en `_ejecutar_comando`, el planificador (`_ejecutar_plan`,
  `_ejecutar_paso_plan`) y el agente ReAct (`ReactAgent(sesion_docker=...)`).
- Manejadores de señales actualizados: la sesión Docker se destruye también al
  interrumpir con `Ctrl+C`, evitando contenedores huérfanos.

### Compatibility
- Sin `--sandbox-session`, el comportamiento es idéntico al de la versión
  anterior (`docker run --rm` por comando). El contenedor de sesión monta
  **solo** el directorio del proyecto en `/workspace`, igual que el sandbox.

### Tests
- `tests/test_sandbox_session.py`: 26 casos (creación, reutilización,
  destrucción, huérfanos, integración en plan/ReAct, compatibilidad, Ctrl+C).


## [6.3.0] - 2026-08-30 - Mejora del editor de parches (fuzzy matching y resolución de conflictos)

### Added
- **Emparejamiento difuso por etapas** en `_aplicar_hunks_incremental()`: la
  búsqueda exacta (con `strip()`) ya no es el único método. Ahora, cuando un
  hunk no encaja en la posición declarada, se prueban, en orden y solo cuando
  la anterior falla:
  1. Coincidencia exacta cerca de la posición declarada.
  2. Coincidencia por **variantes** (espacios colapsados y sin comentario final
     `#`/`//`), tolerando reindentaciones y comentarios añadidos/eliminados.
  3. Búsqueda **difusa real** con `difflib.SequenceMatcher` (ratio medio de las
     líneas de contexto ≥ `UMBRAL_DIFUSO_HUNKS` = 0.85, y ≥
     `UMBRAL_DIFUSO_LINEA` = 0.90 por línea), tolerando variables renombradas.
  4. **Resincronización a nivel de bloque**: si nada encaja, se busca en TODO el
     archivo la ventana más parecida al bloque original del hunk
     (≥ `UMBRAL_DIFUSO_BLOQUE` = 0.80) y se reemplaza conservando las líneas de
     contexto locales del usuario.
- Umbrales configurados como constantes de módulo: `UMBRAL_DIFUSO_HUNKS` (0.85),
  `UMBRAL_DIFUSO_LINEA` (0.90) y `UMBRAL_DIFUSO_BLOQUE` (0.80).
- Nuevo flag `--mostrar-diff`: muestra el diff propuesto (coloreado con
  `rich.syntax.Syntax`, lenguaje `diff`) ANTES de aplicar un parche y pregunta si
  aplicarlo (`[a]`), cancelarlo (`[c]`) o editarlo manualmente (`[e]`). En modo
  `--auto` muestra el diff sin bloquear. Sin el flag, el parche se aplica sin
  preguntar (comportamiento histórico).
- Nuevos helpers en `snapcontext.py`:`_variantes_linea()`,
  `_lineas_equivalentes()`, `_quitar_comentario()`, `_ratio_bloque()`,
  `_contar_cambios_parche()` y `_mostrar_diff_parche()`; y en `ui.py`
  `mostrar_diff()` y `preguntar_interactivo()`.
- Mensajes de error más claros cuando el parche no puede aplicarse: se muestra el
  proceso seguido, el umbral usado y una sugerencia accionable (`--editor aider`
  o edición manual). Con `--mostrar-diff` se visualiza el diff antes de fallar.
- Tests `tests/test_editor_parches.py` (17 casos): fuzzy matching con espacios
  cambiados, comentarios añadidos, variables renombradas, hunk de añadido puro,
  desplazamiento acumulativo, resincronización de bloques, fallo controlado,
  umbrales expuestos, `--mostrar-diff` (aplicar/cancelar/editar/fallo) y modo sin
  preguntar.

### Changed
- `pyproject.toml` y `VERSION` → `6.3.0`.
- `agentes.py` y `orquestador.py`: el flag `mostrar_diff` se propaga desde la CLI
  a `ejecutar()` → estrategia de parche → `_aplicar_con_conflicto()`.
- Rendimiento: `difflib.SequenceMatcher` solo se usa cuando el hunk falla en las
  etapas exactas/variantes (el caso normal no paga el coste).

## [6.2.0] - 2026-08-29 - 🧠 Mostrar razonamiento del modelo (chain-of-thought)

### Added
- Flag `--mostrar-razonamiento` y variable de entorno
  `SNAPCONTEXT_MOSTRAR_RAZONAMIENTO=1` (valores `1`/`true`/`yes`) para mostrar
  el razonamiento del modelo antes de cada acción o respuesta.
- `ui.mostrar_razonamiento()`: panel con el razonamiento (borde verde, texto
  gris, truncado a 500 caracteres con aviso `[ver más]`).
- `_extraer_razonamiento()` en `snapcontext.py`: detecta los campos
  `reasoning`, `thinking`, `chain_of_thought`, `reasoning_content`,
  `thoughts` (top-level o anidados tipo OpenAI/Ollama) y bloques
  `<think>…</think>` en texto plano.
- Integración en chat (`--chat`), planificador (`_generar_plan` y paso
  `consultar`), editor propio (AST/parche/sobrescribir) y ReAct (razonamiento
  completo por turno en lugar del pensamiento resumido).
- Modo de dos pasos: si el modelo no devuelve razonamiento explícito, se pide
  primero el razonamiento paso a paso y luego se ejecuta la acción (con
  advertencia de lentitud; solo con el flag activo).
- Los bloques `<think>…</think>` se eliminan siempre del texto útil antes de
  parsear JSON o parches (evita fallos con DeepSeek-R1 y similares).

### Changed
- `pyproject.toml` y `VERSION` → `6.2.0`.

## [6.1.0] - 2026-08-29 - 🧠 Manejo de contexto inteligente y fallback a Aider

### Added
- **Nuevo módulo `context_utils.py`**: manejo de contexto inteligente para no
  desbordar la ventana de tokens del modelo (crítico en modelos locales como
  `deepseek-r1:14b`, con 4096 tokens).
  - `estimar_tokens(texto, modelo)`: usa `tiktoken` si está disponible; si no,
    la regla práctica 1 token ≈ 4 caracteres.
  - `estimar_tokens_de_archivo(ruta)`: estimación leyendo el archivo de disco.
  - `extraer_bloques_relevantes(contenido, lenguaje, objetivo)`: extrae
    funciones/clases con tree-sitter (vía `parser_universal`) o con regex
    básico como respaldo; devuelve `(resumen_ast, bloques)` y coloca primero
    el bloque `objetivo` si se indica.
  - `seleccionar_contexto(contenido, lenguaje, objetivo, max_tokens=3000)`:
    si el archivo cabe en el presupuesto se devuelve completo; si no, se
    compone resumen AST + bloque objetivo + bloques relevantes hasta llenar
    `max_tokens`.
  - `objetivo_en_mensaje(...)`: detecta la función/clase mencionada en la
    tarea para priorizar el bloque correcto.
  - `es_error_contexto(exc)`: detección unificada de errores de límite de
    contexto (Anthropic `exceed_context_size_error`, OpenAI/Ollama/DeepSeek
    `context length`, Gemini `context window`, …).
- **Editor propio con contexto selectivo (`agentes.py`)**: `_aplicar_modo_parche`
  y `_aplicar_modo_sobrescribir` estiman los tokens del archivo antes de llamar
  al proveedor; si supera `--max-context-tokens` envían solo el bloque
  relevante + resumen (el parche devuelto se reinserta en el archivo original).
  Si el proveedor falla por límite de contexto se **reintenta con el archivo
  completo** (el modelo real puede tener más contexto del estimado) y, si
  vuelve a fallar, se **pasa a la siguiente estrategia**
  (AST → Parche → Sobrescritura).
- **Modo AST (`snapcontext.py`)**: `_editor_ast` acepta `max_context_tokens`,
  reduce el contenido enviado con `seleccionar_contexto` y descarta la
  operación `completo` cuando el contexto está truncado (las operaciones
  `renombrar`/`insertar_import` se aplican sobre el archivo completo, nunca
  sobre el fragmento).
- **Fallback a Aider**: con `--editor-fallback`, si el editor propio no puede
  editar un archivo se invoca Aider automáticamente (si está instalado) para
  ese archivo, o se muestra una sugerencia clara al usuario.
- **Planificador (`snapcontext.py`)**: el contenido de `CLAUDE.md` incluido en
  `_generar_plan` se limita por tokens con `seleccionar_contexto` (antes:
  recorte bruto a 3000 caracteres); `_ejecutar_paso_plan` propaga
  `--max-context-tokens` y `--editor-fallback` al editor propio, igual que el
  orquestador en el flujo clásico.

### CLI
- `--max-context-tokens N` (3000): límite máximo de tokens a enviar al modelo
  en una sola petición.
- `--editor-fallback`: cuando el editor propio falla por contexto, invoca
  Aider automáticamente (si está instalado) para ese archivo.

### 🧪 Tests
- `tests/test_context_utils.py` (24 tests): estimación de tokens (con y sin
  `tiktoken`), extracción de bloques (tree-sitter, `ast` y fallback regex),
  selección de contexto (archivo pequeño devuelve todo, grande devuelve
  fragmento dentro del presupuesto, objetivo priorizado), `objetivo_en_mensaje`,
  constantes compartidas entre módulos, flags CLI, `_editor_ast` con
  `max_context_tokens` e integración del editor propio con proveedor mockeado
  (contexto selectivo y reintento tras error de contexto).

### 🔧 Versión
- `VERSION` en `snapcontext.py` y `pyproject.toml` → `6.1.0`. Se actualizaron
  las 21 aserciones de versión de los tests de coherencia (20 ficheros).
- `context_utils` añadido a `py-modules` (`pyproject.toml`) y a `MANIFEST.in`.

## [6.0.0] - 2026-08-28 - 🤖 Multi-agentes en paralelo

### Added
- **Nuevo módulo `multi_agent.py`**: sistema multi-agente con roles
  especializados, activado con `--multi-agent` o `SNAPCONTEXT_MULTI_AGENT=1`.
  - `Supervisor` (`Supervisor`): coordina el flujo, descompone la tarea y
    combina los resultados de los agentes con bucle de realimentación.
  - `Arquitecto` (`Arquitecto`): usa el LLM para generar un plan en JSON
    (objetivo, módulos, archivos a tocar y pasos). Si el LLM no devuelve JSON
    válido, degrada a un plan mínimo con la tarea (nunca rompe el flujo).
  - `Programador` (`Programador`): implementa el código con el editor propio
    (cadena AST → Parche → Sobrescritura) siguiendo el plan del Arquitecto.
  - `Tester` (`Tester`): ejecuta las pruebas con la detección automática de
    v5.3.0 (`detector_tests`) y devuelve éxito/fallo + salida.
  - `Buzon` (`Buzon`): buzón de mensajes thread-safe para la comunicación
    entre agentes (`publicar`/`recibir`/`vaciar`/`historial`).
- **Pipeline con realimentación**: si el Tester detecta fallos, el Supervisor
  realimenta el error al Programador (hasta `max_reintentos`, por defecto 3).
  Si no hay pruebas detectadas en el proyecto, se da por completado.
- **Integración CLI** (`snapcontext.py`): flag `--multi-agent`, variable
  `SNAPCONTEXT_MULTI_AGENT=1` y función `_ejecutar_multi_agent` enrutada desde
  `_ejecutar_modo_tarea`. El modo es opcional: `--plan`, ReAct y el flujo
  clásico siguen intactos. En modo interactivo el Supervisor pide confirmación
  del plan; con `--auto` la omite.
- **UX**: mensajes por agente (`🧠 Arquitecto`, `💻 Programador`,
  `🧪 Tester`, `🤖 Supervisor`).
- **Verificación temprana de directorio de proyecto** (`snapcontext.py`):
  extraída a `_advertencia_directorio_proyecto(args)`; en `--auto` ahora
  continúa sin preguntar (muestra el aviso y sigue).

### 🧪 Tests
- `tests/test_multi_agent.py` (33 tests): activación por flag/env, Buzon,
  Arquitecto, Programador, Tester, Supervisor (flujo completo, realimentación,
  sin pruebas, abortos, `--auto`, cancelación interactiva), integración CLI y
  enrutamiento, versionado/packaging.
- `tests/test_proyecto_verificacion.py` (22 tests): detección de raíz de
  proyecto y advertencia temprana (continuar/demo/salir, `--auto`,
  `--no-validar-proyecto`).
- Suite completa: `python -m unittest discover tests` → 855 OK.

### 🔧 Versión
- `VERSION` en `snapcontext.py` y `pyproject.toml` → `6.0.0`. Se actualizaron
  las 19 aserciones de versión de los tests de coherencia.
- `multi_agent` añadido a `py-modules` (`pyproject.toml`) y a `MANIFEST.in`.

## [5.6.0] - 2026-08-27 - 🌐 Editor multi-lenguaje (Tree-sitter)

### Added
- **Nuevo módulo `parser_universal.py`**: editor AST multi-lenguaje basado en
  tree-sitter (language pack), con fallback elegante cuando la librería no
  está instalada o el archivo es inválido.
  - `detectar_lenguaje_por_extension(archivo)`: `.py`, `.js`, `.ts`/`.tsx`,
    `.jsx`, `.go`, `.rs`, `.java`, `.kt`, `.rb`, `.php`, `.c`/`.h`,
    `.cpp`/`.hpp`, `.cs`, `.swift`, `.dart`, etc.
  - `parsear_archivo(contenido, lenguaje)`: AST tree-sitter o `None`.
  - `extraer_nodos(archivo, tipo_nodo)`: funciones, clases, métodos e imports
    (nombres de gramática comunes: `function_definition`,
    `function_declaration`, `class_definition`, `class_declaration`, …).
  - `aplicar_parche_arbol(contenido, nodo_viejo, nodo_nuevo)`: reemplazo
    seguro por bytes exactos del nodo.
- **Editor propio multi-lenguaje** (`agentes.py`, `snapcontext.py`):
  - `_resumen_ast` y `_extraer_bloques_ast` aceptan ahora archivos no-Python
    vía `parser_universal` (Python sigue usando `ast` de la stdlib).
  - `_extraer_contexto_selectivo` y `_puede_ast` soportan todos los
    lenguajes del pack; sin tree-sitter caen a parche/sobrescritura como
    siempre.
  - Se mantiene la cadena de estrategias AST → Parche → Sobrescritura y la
    transaccionalidad, fuzzy matching y análisis de impacto.
- **Dependencias** (`pyproject.toml`, extras opcionales): `tree-sitter`,
  `tree-sitter-languages` y `tree-sitter-language-pack`.

## [5.5.0] - 2026-08-27 - 🔗 Graph RAG (AST + embeddings)

### Added
- **Nuevo módulo `graph_rag.py`** (Grafo de Conocimiento): combina AST y la
  búsqueda semántica para que el agente entienda la arquitectura del proyecto.
  - `_extraer_nodos_y_aristas(directorio)`: nodos = archivos/funciones/clases;
    aristas = imports, llamadas y herencia (resolución de módulos dotted,
    relativos y paquetes).
  - `construir_grafo(directorio, forzar=False)`: cache en
    `~/.snapcontext/graph_cache.pkl` con fingerprint (mtime_ns + tamaño de cada
    `.py`); solo se reconstruye cuando cambia el código. Cache corrupto o
    ilegible → reconstrucción silenciosa.
  - `expandir_contexto(archivos, grafo, max_adicionales=3)`: añade dependencias
    entrantes (quienes importan) y salientes (lo que importa), priorizadas por
    número de conexiones. Nunca lanza excepciones.
- **Flag `--graph-rag`** y variable de entorno `SNAPCONTEXT_GRAPH_RAG=1`
  (prioridad: flag > entorno). Completamente opcional: sin él, comportamiento
  idéntico a 5.4.0.
- **Integración ReAct** (`react_agent.py`): nuevo parámetro `graph_rag=False`
  en `ReactAgent.__init__`; las herramientas `buscar_codigo` y `leer_archivo`
  expanden el contexto con archivos relacionados del grafo.
- **Integración planificador** (`orquestador.py`): con `--graph-rag`, los
  candidatos del pre-filtro semántico se amplían con el grafo antes de la
  selección del LLM (best-effort: nunca rompe el pipeline clásico).
- Mensaje informativo: `🔗 Grafo de conocimiento: expandiendo contexto con
  {n} archivo(s) relacionado(s).`
- Solo Python en esta versión (tree-sitter para otros lenguajes previsto en
  v5.6.0).

### Tests
- 23 tests nuevos en `tests/test_graph_rag.py`: construcción del grafo
  (imports/llamadas/herencia, sintaxis inválida, directorios ignorados),
  expansión de contexto, cache (hit, invalidación, corrupto, `forzar`), flag
  CLI/env y integración con ReAct y el planificador (mockeando).

## [5.4.0] - 2026-08-27 - 🛡️ Sandboxing inteligente

### Added
- **Detección de comandos peligrosos** (`sandbox_utils.py`): regex compiladas
  para `rm -rf /`, `dd`/`mkfs`/`fdisk`, `curl|wget ... | sh/bash`,
  `chmod 777`/`chown -R`, fork bomb, `sudo` + destructivo, escritura en
  dispositivos de bloque (`> /dev/sda`) y `kill -9`/`pkill`. Registro
  extensible (`_PATRONES_PELIGROSOS`) y sin dependencias externas.
- **Sandboxing automático**: los comandos peligrosos se ejecutan dentro del
  contenedor Docker sin intervención del usuario
  (`🔒 Comando potencialmente peligroso detectado...`); los seguros corren
  directamente (cero fricción).
- **Nuevo flag `--no-sandbox`**: desactiva todo el sandbox (prioridad máxima,
  incluso ante `--sandbox` y comandos peligrosos).
- **Variable de entorno `SNAPCONTEXT_SANDBOX`**: `1` fuerza el sandbox
  siempre; `0` lo desactiva por completo.
- Integración en `_ejecutar_comando` (planificador, ReAct, plugins, MCP),
  `_bucle_test` y procesos en segundo plano.
- Si el comando es peligroso y Docker no está disponible: pregunta al usuario
  en modo interactivo y **aborta** en `--auto`.
- Nuevos tests en `tests/test_sandbox_inteligente.py` (26 casos).

### Compatibilidad
- `--sandbox` explícito sigue funcionando exactamente igual (todo al
  contenedor).
- Sin flags, el comportamiento por defecto solo cambia para comandos
  peligrosos; los demás se ejecutan como siempre.
- Versión elevada a `5.4.0` (los 18 tests de coherencia de versión
  actualizados).

## [5.3.0] - 2026-08-27 - 🧪 Detección automática de pruebas

### Added
- Detección automática de pruebas para Go, Rust, Java (Maven/Gradle),
  Python (pytest/unittest), Node (npm/yarn), Flutter, .NET, Ruby y Elixir.
- Nuevo módulo `detector_tests.py` (sin dependencias externas): escanea la
  raíz del proyecto, detecta el lenguaje/framework (`detectar_lenguaje`) y
  devuelve el comando de test exacto (`detectar_comando_test`), la estructura
  de tests (`detectar_estructura_tests`) y un dict completo
  (`detectar_automaticamente`). Extensible: basta añadir una entrada al
  registro `_LENGUAJES` y su archivo identificador.
- Integración con el agente ReAct (`ejecutar_pruebas`): el comando se resuelve
  por prioridad (argumento explícito → `SNAPCONTEXT_COMANDO_TEST` → detección
  automática → pytest de archivo → por defecto). Si nada funciona, devuelve un
  error claro pidiendo que se especifique el comando manualmente.
- Integración con `--test-loop`: si el usuario no pasa `--comando-test`, se
  detecta el lenguaje del proyecto automáticamente. El comando explícito
  siempre tiene prioridad (compatibilidad hacia atrás).
- `--comando-test` ahora es opcional (antes por defecto `"flutter test"`): se
  detecta automáticamente y se mantiene `flutter test` como último recurso.



### 🐛 Fixed
- Corregido el empaquetado: `ui.py`, `react_agent.py` y `curador_proactivo.py`
  ahora están incluidos en `py-modules` de `[tool.setuptools]` (`pyproject.toml`)
  y en `MANIFEST.in`, por lo que viajan en el `.whl`/`.tar.gz`.
- Antes esto provocaba `ModuleNotFoundError: No module named 'ui'` al instalar
  SnapContext desde PyPI.
- Versión elevada a `5.2.1` (build local verificado: `ui.py` aparece en el `.whl`).

## [5.2.0] - 2026-08-26 - 🔄 ReAct es el modo por defecto

### ⚙️ ReAct como comportamiento por defecto (FEATURES)
- **ReAct (razonamiento dinámico) es ahora el modo por defecto** para cualquier
  consulta con `--plan`. El flujo `snapcontext "tarea"` instancia
  `ReactAgent` (`react_agent.py`) en lugar del planificador estático.
- El flag `--react` se mantiene por compatibilidad aunque sea **redundante**;
  `--react-max-iter N` sigue afinando el tope de iteraciones.
- El planificador estático pasa a ser **modo legacy** (`--plan`), preservado
  100 % para scripts/integraciones existentes (Telegram `/plan`, Discord `/plan`,
  extensiones VS Code/JetBrains, web, etc.). `--plan` activa el planificador
  clásico sin cambios en su comportamiento.
- `--auto` funciona idénticamente en ambos modos (sin preguntas interactivas).

### 🧪 Tests
- `tests/test_react_510.py` verifica que **sin flags** se ejecuta ReAct
  (`_ejecutar_modo_tarea` → `_ejecutar_react`); y que `--plan` sigue mapeando
  al planificador estático. Los 18 tests de coherencia de versión se ajusturaron
  a `5.2.0`.

## [5.1.0] - 2026-08-26 - 🧠 Motor ReAct (razonamiento dinámico)
## [5.1.0] - 2026-08-26 - 🧠 Motor ReAct (razonamiento dinámico)

### 🧠 Nuevo módulo `react_agent.py` (FEATURES)
- **Bucle ReAct** (Reasoning + Acting): el agente **piensa → actúa → observa**
  y decide el siguiente paso según el resultado real de la acción anterior,
  a diferencia del planificador `--plan`, cuya lista de pasos es estática.
- Clase `ReactAgent` con: `historial` de mensajes, tope anti-bucles
  (`max_iteraciones`, 15 por defecto), catálogo de herramientas, directorio de
  trabajo y modo `--auto`.
- Formato de salida del LLM en **JSON estricto** (`pensamiento`, `accion`,
  `argumentos`) con reintentos correctivos (hasta 3) si el JSON es inválido.
- Acción `finalizar` para cerrar el bucle con un resumen.

### 🔧 Herramientas disponibles (ACTIONS)
- `editar_archivo(ruta, contenido)` — usa el editor propio (v4.6/4.7),
  con copia de seguridad, y devuelve el diff aplicado.
- `ejecutar_pruebas(archivo=None)` — ejecuta el comando de pruebas configurado.
- `buscar_codigo(patron)` — búsqueda de código (o semántica si está activa).
- `ejecutar_comando(comando)` — shell; **respeta el sandbox Docker de v4.3.0**.
- `leer_archivo(ruta)` — lectura truncada (8 KB), con ruta validada.

### ⏳ Gestión de contexto (INTEGRATION)
- Resumen automático: cuando el historial supera el umbral de tokens (estimado,
  ~4 chars/token; configurable con `REACT_UMBRAL_RESUMEN_TOKENS`), se pide al
  LLM que lo comprima a ≤500 palabras y el bucle continúa con el resumen.

### 🖥️ Integración CLI (v5.1.0)
- Nuevo flag `--react` (+ `--react-max-iter N`). Sin `--plan`; compatible:
  sin el flag, el flujo actual no cambia en absoluto.
- En modo interactivo muestra el pensamiento y pregunta continuar/abortar/
  saltar con `ui.preguntar_interactivo`; con `--auto` ejecuta sin preguntar.

### 🧪 Tests
- Nuevos tests en `tests/test_react_510.py` (15 casos): bucle básico, límite de
  iteraciones, `finalizar`, JSON inválido (reintento correctivo), acciones
  desconocidas, modo interactivo (pregunta llamada), cada herramienta, resumen
  de contexto y presencia del flag en el parser.



## [5.0.0] - 2026-08-26 - 🤖 Curador Proactivo (motor autónomo estilo Hermes)

### 🤖 Nuevo módulo `curador_proactivo.py` (FEATURES)
- **Motor de refactorización autónoma de skills**: evalúa la calidad de cada
  skill (tasa de fallos, tokens promedio, tiempo), identifica candidatos
  (fallos > 20 % o tokens > `UMBRAL_TOKENS`, con ≥ 3 usos), pide al LLM un
  prompt más eficiente/clara/robusto, lo prueba en el **sandbox** de v4.3.0 y,
  solo si pasa las pruebas Y reduce tokens, guarda la nueva versión.
- **Nunca toca el código del usuario**: únicamente los prompts almacenados de
  los skills. La versión anterior queda archivada en la tabla
  `historial_skills` (trazabilidad completa) y se incrementa `version`.
- Si el candidato falla validación, sandbox o no mejora métricas: **no se
  guarda nada** y el motivo queda registrado en `historial_skills`.

### 🗄️ Migración de base de datos (INTEGRATION)
- `_db_migrar_curador()` añade idempotentemente a `skills` las columnas:
  `exitos`, `tokens_promedio`, `tiempo_promedio_ms`, `ultimo_uso`, `version`
  y `activo`; crea la tabla `historial_skills`
  (`skill_id, version, prompt, motivo, fecha`).
- **Registro de métricas en cada ejecución**: `_skill_registrar_exito()/_fallo()`
  actualizan exitos/fallos, medias ponderadas de tokens/tiempo y `ultimo_uso`
  (integrado en el pipeline de skills sin alterar la lógica del editor).

### ⏰ Daemon en segundo plano (FEATURES)
- Hilo demonio (`daemon_proactivo`) que ejecuta una pasada completa cada
  `CURADOR_INTERVALO_HORAS` horas (defecto 6; configurable por entorno).
  Espera fraccionada de 30 s, espera ANTES de la primera pasada (arranque
  instantáneo), respeta el interruptor persistente del curador y nunca muere.
- Arranca automáticamente en `main()` (sin bloquear el CLI); se desactiva con
  `CURADOR_DAEMON=0` y se omite bajo test runners.

### 🖥️ Comandos CLI (v5.0.0)
- `snapcontext curador estado` → estadísticas (usos, fallos, tokens,
  candidatos, mejoras totales, peor skill).
- `snapcontext curador ejecutar` → pasada manual del motor.
- `snapcontext curador desactivar` / `activar` → interruptor persistente.

### 🔔 Notificaciones (opcional)
- Al mejorar un skill envía *"🔧 Skill 'x' mejorado (vN). Tokens reducidos un
  X%."* por Telegram (`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`) o Discord
  (`DISCORD_WEBHOOK_URL`). Best-effort: sin credenciales es silencioso.

### 🧪 Tests
- Nuevos tests en `tests/test_curador_500.py` (≥ 10 casos): migración idempotente,
  registro de métricas, evaluación de candidatos, refactorización exitosa/fallida
  (LLM error, prompt inválido, sandbox falla, sin mejora de tokens), guardado con
  historial, notificaciones, comandos CLI y daemon.



## [4.8.1] - 2026-08-26 - 🔧 URL del repositorio configurable

### 🔧 Mejora (IMP)
- La URL del repositorio que muestra el banner ahora es **configurable** mediante
  la variable de entorno `SNAPCONTEXT_REPO`. Esto permite que los forks y los
  usuarios personalicen la URL sin modificar el código fuente.
- Si la variable **no está definida**, se usa el repositorio oficial por defecto
  (`https://github.com/NicolasBruna24/snapcontext`), así que el banner se ve
  exactamente igual que antes (compatibilidad hacia atrás).
- Implementado en `ui.py` (`ui.REPO_URL`, usado por `mostrar_banner()` tanto en
  modo Rich como en el fallback plano sin Rich).

### 🧪 Tests
- Nuevos tests en `tests/test_ui_480.py`: el banner muestra `REPO_URL`,
  el fallback sin Rich también lo usa, y `SNAPCONTEXT_REPO` sobreescribe el
  valor por defecto (con `importlib.reload` y `mock.patch.dict`).


## [4.8.0] - 2026-08-25 - 🎨 CLI profesional con Rich

### 🖥️ Nueva capa de presentación `ui.py` (FEATURES)
- Nuevo módulo **`ui.py`** que centraliza toda la presentación de terminal
  sobre [Rich](https://github.com/Textualize/rich) (nueva dependencia
  `rich>=13.0.0`): banner, progreso, tablas, diffs y prompts.
- **`mostrar_banner()`**: logotipo ASCII + versión + tabla de comandos
  principales (reemplaza los `print(_LOGO)` planos en `--version` y en el
  arranque sin argumentos).
- **`mostrar_progreso()`**: barra de progreso con ETA para bucles largos;
  integrada en el escaneo del repositorio (`escanear_repositorio`).
- **`mostrar_tabla_impacto()`**: tabla 📁 Archivo Afectado · 🔗 Dependencia ·
  📊 Acción Sugerida para el análisis de impacto (v4.7.0), filas críticas en
  amarillo; integrada en `AgenteEditorPropio`.
- **`preguntar_interactivo()`**: reemplaza el `input()` de continuar/abortar/
  añadir usando `rich.prompt.Prompt`. **Contrato intacto**: devuelve la tecla
  `'c'`, `'a'` o `'s'`; fallback a `input()` plano sin Rich.
- **`mostrar_diff()`** (Syntax lenguaje `diff`: verde +/rojo −) y
  **`mostrar_error()`** (panel rojo). Ambos visibles también en modo auto.

### 🔇 Modo no interactivo centralizado
- `ui.configurar_auto(True/False)`: sincronizado con `--auto` en `main()`.
  Con auto: sin barras de progreso, preguntas devuelven `'c'` por defecto
  sin preguntar; errores y diffs se muestran siempre.
- Degradación elegante: sin `rich` instalado todo sigue funcionando con
  `print()` plano (nunca rompe el CLI).

### 🧪 Tests
- Nuevo `tests/test_ui_480.py`: verifica llamadas a `Console.print`,
  silencio de progreso en auto, `'c'` por defecto en auto, menú de impacto y
  fallback plano sin Rich.
- `rich>=13.0.0` declarado en `pyproject.toml` (`dependencies`) y en
  `requirements.txt`.
- Tests de coherencia de versión (18 archivos) actualizados de `4.7.0` a
  `4.8.0` para reflejar el bump de versión. Suite completa: 628 tests OK.



## [4.7.0] - 2026-08-25 - 🧠 Editor inteligente: impacto + contexto selectivo

### 🔗 Análisis de Impacto por Dependencias (FEATURES)
- Nuevo paso de **Análisis de Impacto Previo** en `AgenteEditorPropio.ejecutar()`:
  antes de editar, usa el grafo de dependencias (`_grafo_dependencias`) para
  detectar qué archivos importan de los que se van a modificar.
- Si hay dependientes, muestra `⚠️ Atención: El cambio en 'X' afecta a los
  siguientes archivos: [...]` y pregunta: **continuar / abortar / añadir los
  dependientes a la edición** (para actualizarlos también y no romper nada).
- Con **`--auto`** no pregunta: solo registra la advertencia y continúa.

### 📦 Contexto inteligente para archivos grandes (MEJORAS)
- Nueva constante **`MAX_CONTEXT_LINES = 600`**: los archivos con más líneas ya
  NO se inyectan completos en el prompt del proveedor.
- Nueva `_extraer_contexto_selectivo()`: reutiliza `_resumen_ast_python` para
  el resumen de alto nivel (imports/clases/funciones) e incluye únicamente los
  bloques relevantes (función/clase mencionada en la tarea; si no hay coincidencia,
  los primeros bloques por proximidad) con ±5 líneas de contexto, más una
  `[RESTRICCIÓN]` que obliga al modelo a generar el diff solo del bloque.
- Modo `sobrescribir` sobre archivos grandes: el modelo devuelve el bloque entre
  marcadores `<<<ANTES>>>/<<<DESPUES>>>/<<<FIN>>>` y se reinserta en el archivo
  original con la nueva `_splicear_bloque()` (alineación difusa, ratio ≥ 0.80).
- Nuevo helper centralizado `_construir_prompt_edicion()` (agentes.py) usado por
  los modos parche y sobrescribir; sin cambios en firmas públicas.

### 🧪 Tests
- Nuevo `tests/test_editor_470.py`: advertencia de dependientes (`main.py` →
  `utils.py`), menú interactivo (añadir/abortar), modo auto sin `input()`,
  contexto selectivo en archivo de 1200 líneas (contiene resumen AST, no el
  archivo completo), splicing de bloque y flujo completo de sobrescritura
  truncada.



## [4.6.0] - 2026-08-25 - 🛡️ Editor transaccional, seguro y tolerante

### 🔒 Editor propio transaccional (FEATURES)
- **Rollback multiarchivo automático**: `AgenteEditorPropio.ejecutar()` toma un
  snapshot de todos los archivos antes de editar; si cualquier archivo falla o
  se lanza una excepción, se restauran TODOS los archivos a su estado original
  (nuevo método `_rollback`). Ya no quedan ediciones parciales a medias.
- **Backup obligatorio**: si no se puede crear la copia de seguridad en
  `~/.snapcontext/backups/`, la edición se ABORTA (antes solo avisaba y
  escribía igualmente). Nunca se escribe sin backup.
- **Aplicación incremental todo-o-nada**: `_aplicar_hunks_incremental()` ya no
  escribe archivos con hunks parcialmente aplicados; si algún hunk no puede
  aplicarse, la operación se aborta dejando el archivo intacto.

### 🧩 Fuzzy matching en parches (MEJORAS)
- La resolución incremental de conflictos ahora usa `difflib.SequenceMatcher`
  con umbral global 0.85 (y 0.90 por línea): tolera comentarios añadidos,
  cambios menores de espacios y pequeñas variaciones del código circundante.
  Antes solo había igualdad exacta tras `strip()`, y un comentario nuevo rompía
  el hunk.

### 🧪 Tests
- Nuevo `tests/test_editor_460.py`: rollback multiarchivo, fuzzy matching con
  comentario intercalado y aborto cuando el directorio de backups no permite
  escritura.




## [4.5.0] - 2026-08-25 - 🎮 Gateway de Omnicanalidad: Discord

### 🎮 Nuevo gateway de Discord (FEATURES)
- Nuevo módulo **`discord_gateway.py`**: recibe Slash Commands de Discord
  (interacciones), los procesa con el motor interno de SnapContext y responde
  en el mismo canal. Sin `discord.py`: solo `httpx` + `cryptography`.
- **Configuración** (prioridad: variable de entorno > `~/.snapcontext/config.json`
  clave `"discord"`): `DISCORD_PUBLIC_KEY`, `DISCORD_APPLICATION_ID`,
  `DISCORD_BOT_TOKEN` y `DISCORD_WEBHOOK_URL` (opcional).
- **Verificación de firma** (`verify_signature`): Ed25519 con headers
  `X-Signature-Ed25519`/`X-Signature-Timestamp`; firma inválida o mal formada
  lanza `ValueError` (protección contra peticiones falsificadas).
- **Envío robusto** (`send_discord_message`): follow-up vía
  `/webhooks/{app_id}/{interaction_token}` cuando hay token de interacción, o
  webhook estándar del canal; contenido > 2000 caracteres se envía como
  archivo `.txt`; errores de red tolerados.
- **Handler** (`handle_discord_interaction`): PING de Discord → `{"type": 1}`;
  comandos → respuesta diferida `{"type": 5}` y ejecución del agente con
  `asyncio.create_task` (límite de respuesta <3 s). Comandos: `/start`,
  `/help`, `/snap`, `/fix` (→ `--test-loop`) y `/plan` (→ `--plan`).

### 🔧 CLI, API e integración
- Nuevo subcomando **`snapcontext discord setup --public-key <KEY> --app-id
  <ID> --token <BOT_TOKEN> [--webhook-url <URL>]`** (persiste credenciales y
  muestra instrucciones paso a paso para configurar la INTERACTIONS ENDPOINT
  URL en el portal de Discord Developers), más `snapcontext discord estado`
  y ayuda detallada con `snapcontext discord help`.
- Nuevo endpoint **`POST /webhook/discord`** en `web/app.py`: verifica la
  firma (401 si es inválida), devuelve `503` sin `DISCORD_PUBLIC_KEY`, y
  responde el PONG/diferido inmediato mientras el pipeline corre en segundo plano.

### 🧪 Tests y documentación
- Nuevo `tests/test_discord_450.py` (17 tests): configuración, verificación
  de firma Ed25519 (vector RFC 8032, inválida/malformada/sin clave), handler
  (PONG, diferido, bienvenida, mapeo de comandos), envío (webhook, follow-up,
  archivo por longitud, error de red) y endpoint webhook (503/401/PING firmado).
- Suite completa: 594 pruebas pasando. README con sección "🎮 Gateway de
  Omnicanalidad: Discord". Versión actualizada a `4.5.0` (metadatos y badges).

## [4.4.0] - 2026-08-25 - 📱 Gateway de Omnicanalidad: Telegram

### 📱 Nuevo gateway de Telegram (FEATURES)
- Nuevo módulo **`telegram_gateway.py`**: recibe mensajes de Telegram, los
  procesa con el motor interno de SnapContext y responde al mismo chat.
- **Configuración** (prioridad: variable de entorno > `~/.snapcontext/config.json`
  clave `"telegram"`): `TELEGRAM_BOT_TOKEN` y `TELEGRAM_WEBHOOK_URL`.
- **Envío robusto** (`send_telegram_message`): `httpx.AsyncClient`; mensajes
  > 4096 caracteres se envían como documento `.txt`; errores de red tolerados.
- **Handler** (`handle_telegram_update`): `/start` con bienvenida; comandos
  `/fix` y `/plan` mapeados a `--test-loop`/`--plan`; el pipeline corre con
  `asyncio.create_task` para no bloquear al webhook (timeout de Telegram ~30 s).
- **Wrapper asíncrono** `run_agent_async(query)`: usa la API interna del núcleo
  (`flujo_principal` / `_ejecutar_planificador`) en un executor, capturando su
  salida — no lanza el CLI por subprocess.
- **Endpoint webhook**: `POST /webhook/telegram` en `web/app.py`. Devuelve
  `200 OK` inmediato; `503 Service Unavailable` sin `TELEGRAM_BOT_TOKEN`.

### 🔧 CLI e integración
- Nuevo subcomando **`snapcontext telegram setup --token <TOKEN> --webhook-url <URL>`**
  (persiste en config.json y registra el webhook con `setWebhook`), más
  `telegram estado` y `telegram webhook-registrar`.
- Nueva dependencia base `httpx>=0.27.0` en `pyproject.toml`.

### 🧪 Tests y documentación
- Nuevo `tests/test_telegram_440.py` (16 tests): resolución de configuración,
  mapeo de comandos, envío (ok/largo/error/sin token), handler (`/start`,
  updates inválidos, tarea en segundo plano) y endpoint webhook (200/503).
- Suite completa: 577 pruebas pasando. README con sección "📱 Gateway de
  Omnicanalidad: Telegram".

## [4.3.0] - 2026-08-25 - 🐳 Sandboxing con Docker

### 🐳 Sandbox opcional (FEATURES)
- Nuevo flag **`--sandbox`** (booleano, por defecto `False`): todos los comandos
  que ejecuta SnapContext (bucle de pruebas, MCP `execute_command`, pasos
  `"ejecutar"` del planificador, plugins y herramientas de usuario) se lanzan
  dentro de un contenedor Docker aislado.
- **Detección de Docker**: `_docker_disponible()` verifica `docker --version`
  (binario en el PATH) y `docker info` (daemon en ejecución). Nunca lanza
  excepciones.
- **Personalización**:
  - `--sandbox-imagen <imagen>`: imagen Docker personalizada.
  - `SNAPCONTEXT_SANDBOX_IMAGE`: imagen por defecto vía variable de entorno
    (prioridad: flag > entorno > `python:3.11-slim`).
  - `--sandbox-comando <cmd>`: comando de preparación antes del principal
    (ej.: `apt update && apt install -y make`), anteponiéndolo con `&&`.
- **Montaje**: el directorio del proyecto se monta en `/workspace`
  (`-v "<proyecto>:/workspace" -w /workspace`) con `--rm` (contenedor efímero).
- **Variables de entorno**: las claves del host (`*_API_KEY`, `OLLAMA_URL`,
  `SNAPCONTEXT_SANDBOX_IMAGE`) se pasan al contenedor automáticamente (`-e`).

### 🔧 Integración (INTEGRATION)
- `_ejecutar_comando()` y `_lanzar_proceso_fondo()` envuelven el comando con
  `_envolver_sandbox()` cuando el sandbox está activo; log:
  `ℹ [sandbox] Ejecutando en contenedor: ...`.
- **Bucle de pruebas** (`--test-loop` + `--sandbox`): las pruebas corren dentro
  del contenedor vía `_ejecutar_pruebas_argv()`; se omite la comprobación del
  binario en el PATH del host; los resultados se procesan igual que siempre.
- **MCP**: `execute_command` (foreground y background) usa el sandbox; las
  herramientas de solo lectura (`grep`, `read_file`, `list_files`, `ast`,
  `git_status`, `git_diff`, …) siguen en el host para mayor velocidad
  (context manager interno `_sandbox_pausado`).
- **Planificador**: con `--plan --sandbox`, los pasos `ejecutar` y `mcp` con
  comandos corren en el contenedor; `editar` y `consultar` no cambian.

### 🛡️ Manejo de errores (BUGFIXES)
- Con `--sandbox` explícito y Docker no disponible → error claro
  (`RuntimeError` con instrucciones) y código de salida 1.
- Sin Docker pero con activación no estricta → aviso y continuación sin sandbox.
- Los fallos de comandos dentro del contenedor muestran salida y código como
  siempre.

### ✅ Compatibilidad
- Sin `--sandbox`, SnapContext se comporta exactamente igual que en 4.2.0;
  CLI, web, extensiones, API y resto de funcionalidades sin cambios.

### 📝 Documentación y Tests (DOCUMENTATION)
- README: nueva sección "🐳 Sandboxing con Docker (v4.3.0)" con uso, tabla
  qué-corre-dónde y manejo de errores.
- Nuevo `tests/test_sandbox_430.py` (20 tests): detección de Docker simulada,
  activación estricta/no estricta, resolución de imagen (flag/env),
  envoltura `docker run`, ejecución en sandbox (`_ejecutar_comando`,
  `execute_command`, paso `ejecutar` del plan, bucle de pruebas,
  `AgenteTester`) y herramientas de solo lectura fuera del sandbox.
- Suite completa: 561 pruebas pasando.

## [4.2.0] - 2026-08-25 - Asesor mejorado: seguridad y rendimiento

### 🛡️ Análisis de seguridad (🔒)
- Nuevas detecciones heurísticas (sin dependencias externas tipo bandit):
  - **Inyección SQL**: consultas construidas por concatenación/format/f-string.
  - **Command injection**: `os.system(...)` y `subprocess` con `shell=True`.
  - **Path traversal**: rutas con `../` construidas dinámicamente.
  - **Hardcoded secrets**: API keys, passwords, tokens embebidos en el código.
  - **eval/exec** inseguros.
  - **XSS**: `innerHTML =` y `dangerouslySetInnerHTML`.
- Cada hallazgo incluye descripción, archivo:línea, solución sugerida y
  prioridad (alta/media), mostrado con el tag 🔒 Vulnerabilidad.

### ⚡ Análisis de rendimiento
- Bucles anidados O(n²) · `range(len(...))` · concatenación de cadenas con
  `+=` en bucles · consultas N+1 del ORM (`.objects.get(` en bucle) · lectura
  completa de archivos grandes en memoria. Tag ⚡ Rendimiento.

### 🚀 Integración
- Nuevo flag **`--asesor-profundo`**: ejecuta el análisis completo (heurísticas
  básicas + seguridad + rendimiento). Sin él, `--asesor` se comporta como en
  v3.5.0.
- **Planificador**: nuevas acciones de paso `"seguridad"` y `"rendimiento"`
  (en `--auto` solo informan, sin interacción).
- **Chat**: nuevos comandos `/seguridad` y `/rendimiento`.
- **AgenteAsesor**: nuevos métodos `analizar_seguridad()` y
  `analizar_rendimiento()`.

## [4.1.0] - 2026-08-25 - Editor propio por defecto

### ✏️ Editor propio, modo por defecto (antes Aider)
- `--editor` ahora vale **`propio`** por defecto; `--editor aider` sigue
  disponible y documentado para quien prefiera Aider (no se elimina nada).
- **Transparencia**: antes de cada intento se informa la estrategia en uso
  (`ℹ Editor propio: usando estrategia AST...`).

### 🩹 Heurísticas de auto-reparación
- Cadena de estrategias del editor propio: **AST → Parche → Sobrescritura**
  (la lógica vive en `AgenteEditorPropio.ejecutar()`, no en el orquestador).
- Si todo falla, error claro con archivo, estrategias intentadas, motivo y
  sugerencia (`--editor aider`), más registro local opcional del fallo en
  `~/.snapcontext/logs/editor_fallos.log`.

### ⚡ Prompts optimizados para modelos locales
- **Ollama detectado automáticamente** → prompts concisos y directos en las
  tres estrategias (AST, parche y sobrescritura).
- Nuevo flag **`--modelo-ligero`** para forzarlos con cualquier proveedor.

### 🔀 Resolución interactiva de conflictos de parche
- Cuando un parche no se aplica limpiamente se muestra el menú:
  `[a]plicar de todas formas · [v]er diff · [r]eintentar con IA · [c]ancelar`.
- En modo `--auto` no se pregunta: se pasa automáticamente a la siguiente
  estrategia. Funciona igual desde la CLI que desde `--chat`.

### Compatibilidad
- `--editor aider` no cambia de comportamiento; CLI, web, extensiones y API
  pública siguen funcionando igual.

## [4.0.0] - 2026-08-25 - Ecosistema de plugins

### 🧩 Ecosistema de plugins
- **Estructura**: `~/.snapcontext/plugins/<nombre>/` con un `plugin.json`:
  `nombre`, `version`, `autor`, `descripcion`, `permisos`
  (`archivos|red|red_escrita|ejecucion|entorno`), `habilitado` y
  `herramientas` (lista con `nombre`, `descripcion`, `script` Python o
  `comando` shell, `requiere_permiso` y `parametros`).
- **Registro automático**: al iniciar, SnapContext escanea el directorio y las
  herramientas habilitadas se integran en el sistema MCP (ejecutadas por
  subproceso; reciben argumentos JSON por **stdin** y responden JSON por
  stdout). Sin plugins instalados, SnapContext funciona exactamente igual.
- **CLI** (`snapcontext plugin <accion>`):
  | Acción | Descripción |
  |---|---|
  | `list` | Lista plugins instalados y sus herramientas. |
  | `install <nombre\|usuario/repo\|url\|ruta>` | Instala desde carpeta local o GitHub (ZIP codeload). |
  | `remove <nombre>` | Desinstala (con confirmación). |
  | `create [nombre]` | Genera la estructura básica (manifest + script ejemplo). |
  | `update <nombre>` | Reinstala desde el origen registrado. |
  | `enable` / `disable <nombre>` | Habilita/deshabilita individualmente. |
- **Seguridad**: confirmación explícita antes de instalar fuentes externas,
  mostrando los permisos declarados; los permisos viajan con cada herramienta.
  (Sandbox opcional con Docker: previsto para una versión futura.)
- **Chat**: `/plugin` lista los plugins; `/plugin <plugin>.<herramienta>
  '{"arg": ...}'` ejecuta una herramienta directamente.
- **Web**: acciones `plugins` (listar), `plugin_install` y `plugin_remove`;
  emiten el evento `{"tipo": "plugins", "plugins": [...]}` para el panel.
- **Repositorio de comunidad**: `plugin install <nombre>` resuelve contra
  https://github.com/NicolasBruna24/snapcontext-plugins (ver README para
  publicar plugins propios).

## [3.6.0] - 2026-08-25 - API pública

### 🔌 API pública
- **Servidor REST dedicado** con `snapcontext --api` (alias `--api-server`),
  sobre FastAPI/uvicorn reutilizando la infraestructura de `web/app.py`.
  - Flags: `--api-puerto` (8001 por defecto, la web sigue en 8000),
    `--api-host` (127.0.0.1) y `--api-token`.
  - Documentación OpenAPI automática en `/docs` (Swagger) y `/redoc`.
- **Endpoints** (`/api/v1/...`):
  | Endpoint | Método | Descripción |
  |---|---|---|
  | `/health` | GET | Estado del servidor (público). |
  | `/query` | POST | Ejecuta una consulta → `202 Accepted` + `task_id`. |
  | `/plan` | POST | Ejecuta un plan → `202 Accepted` + `task_id`. |
  | `/chat` | POST | Mensaje al proveedor de IA (síncrono, con historial). |
  | `/skills` | GET | Lista las skills aprendidas (`?archivados=true`). |
  | `/daemon` | POST | Gestiona el daemon: `estado` / `iniciar` / `detener`. |
  | `/tasks/{task_id}` | GET | Estado de una tarea asíncrona. |
- **Autenticación por API key**: header `X-API-Key` (o query param `api_key`)
  obligatorio en todos los endpoints salvo `/health`, `/docs` y `/redoc`.
  - `snapcontext --api-generate-key`: genera una clave segura (32 bytes,
    url-safe), la guarda en `~/.snapcontext/config.json` (`"api_key"`) y la
    muestra. Al arrancar sin clave configurada se genera automáticamente.
- **Ejecución asíncrona**: `query` y `plan` devuelven `202` con `task_id`
  (`threading` en segundo plano); el estado se consulta en
  `/tasks/{task_id}` (`pendiente → ejecutando → completada/fallida/error`).
- La API es opcional: si falta FastAPI se sugiere
  `pip install snapcontext[web]`. CLI, web y extensiones no cambian.

## [3.5.0] - 2026-08-25 - Asesor de código proactivo

### 🧠 Asesor de código proactivo
- **Análisis estático automático** del proyecto sin pedirlo explícitamente:
  - Funciones/métodos demasiado largos (> 20 líneas, umbral configurable).
  - Clases con demasiadas responsabilidades (> 10 métodos).
  - Nombres de variables/funciones poco descriptivos (heurística AST con
    diccionario de nombres sugeridos).
  - Patrones obsoletos: `except:` desnudo (prioridad alta), `== None`,
    `.has_key()` — disponibles para todos los lenguajes.
  - Código duplicado entre archivos (ventanas deslizantes normalizadas).
- **Lenguajes**: Python vía `ast`; JS/TS, Dart, Go, Rust y Java mediante
  heurísticas regex. Sin validador disponible el análisis se omite sin error.
- **Sugerencias estructuradas**: descripción, archivo:línea, solución propuesta
  y prioridad (alta/media/baja), mostradas en la CLI con colores.
- **Flags**:
  - `--asesor` (alias `--sugerir`): analiza y muestra; nunca modifica código.
  - `--asesor-auto`: aplica automáticamente las mejoras seguras (renombrar
    símbolos); cada cambio se valida con `_validar_sintaxis` antes de
    guardarse y se descarta si rompe la sintaxis.
  - `--asesor-umbral N`: sensibilidad del detector de funciones largas.
- **Configuración persistente**: umbrales en `~/.snapcontext/config.json`
  bajo la clave `"asesor"` (p. ej. `{"asesor": {"funcion_larga": 30}}`).
- **Integración con el planificador**: nueva acción de paso `{"accion":
  "asesor"}`; cada sugerencia se presenta al usuario para aceptarla o
  rechazarla individualmente (en `--auto` solo se informan).
- **Chat**: nuevo comando `/asesor` (alias `/sugerir`) en `snapcontext --chat`.
- **Web**: nueva acción `asesor` que emite el evento `{"tipo": "asesor",
  "sugerencias": [...]}` para el panel de resultados de la interfaz.
- **Nuevo agente**: `AgenteAsesor` en `agentes.py`, integrado en el
  Orquestador (`orch.agente_asesor`).

## [3.4.0] - 2026-08-25 - Validación de sintaxis del editor propio

### Validación de sintaxis antes de guardar
- Nueva función `_validar_sintaxis(archivo, contenido, directorio)` que escribe
  el contenido en un archivo temporal y ejecuta el validador del lenguaje
  detectado por `_lenguaje_archivo`:
  Python (`python -m py_compile`), JavaScript/TypeScript (`node --check`),
  Dart (`dart analyze` → `dart format`), Go (`go build -n` → `gofmt -e`),
  Rust (`rustc --parse-only`), Java (`javac -Xlint:none`) y C/C++ (`gcc` /
  `clang -fsyntax-only`). Si no hay validador o comando disponible, se omite
  la validación (aviso en logs de depuración).
- `_validar_sintaxis` captura `stdout`/`stderr` y maneja `subprocess` y timeouts,
  devolviendo `(exito: bool, mensaje_error: str)`. Nunca toca el archivo original.
- **Integración en `AgenteEditorPropio.ejecutar()`**: tras generar el nuevo
  contenido, antes de `_editor_sobrescribir()` / `_aplicar_parche_con_resolucion()`.
  Si la validación falla, se envía el error al proveedor para que corrija, hasta
  `MAX_INTENTOS_VALIDACION` (por defecto 3) o hasta que pase. Si se agotan los
  intentos se cancela la edición (nada se guarda).
- El modo parche valida el contenido resultante (aplicando el diff en memoria
  con `_aplicar_parche_preview`) antes de aplicar el parche real.

### Flags
- `--validar` (por defecto activado) y `--no-validar-sintaxis` para desactivar la
  validación (comportamiento previo). *Nota:* no se usa `--no-validar` porque ese
  alias ya está reservado por `--iniciar-proyecto`.
- `--max-intentos-validacion N` para ajustar los reintentos.
- Documentado en `--help` y en las categorías de ayuda.

### Logs
- `ℹ Validando sintaxis de archivo.py...`, `✔ Sintaxis válida.`,
  `✖ Error de sintaxis: ... Reintentando (1/3)...` y
  `✖ No se pudo validar tras 3 intentos. Edición cancelada.`

### Tests
- Nueva suite `tests/test_validacion_sintaxis_340.py` (15 tests): `_validar_sintaxis`
  con mocks de subprocess para Python/JS/Dart/Java (incluido fallback de Dart,
  timeout y comando no disponible), flags de CLI, flujo de reintentos en
  `_aplicar_modo_sobrescribir` (fallo→éxito y agotamiento de intentos) y
  `_aplicar_parche_preview`.

## [3.3.0] - 2026-08-24 - Editor propio refinado: AST multi-lenguaje, conflictos y skills

### AST y parches
- Detección de lenguaje refinada: mapa de extensiones ampliado (mts/cts/cjs,
  cxx/hh/hxx, zsh, scss, scala, lua, sql, elixir, zig, haskell, r, vue,
  svelte...) y nueva heurística por contenido (`_detectar_lenguaje_contenido`,
  `_lenguaje_archivo`) para scripts sin extensión y proyectos mixtos.
- `_resumen_ast` ahora detecta Python también por contenido (shebang/`def`).

### Manejo de conflictos en parches
- Nueva validación previa (`_validar_parche_previo`): comprueba que el
  archivo coincide con el contenido usado para generar el parche antes de
  aplicarlo (evita corrupción por cambios concurrentes).
- Nueva resolución automática (`_aplicar_hunks_incremental`): si `git apply`
  y `patch` fallan, aplica el parche línea a línea en Python puro con
  búsqueda por desfase y contexto difuso; los hunks irresolubles se omiten
  con aviso (aplicación parcial informada).
- `_aplicar_parche_con_resolucion` orquesta validación → git apply → patch →
  resolución incremental; ya no cae directamente a sobrescritura.

### Prompt de edición enriquecido
- El prompt del editor propio incluye ahora lenguaje detectado, tamaño del
  archivo, resumen del AST con posiciones y reglas explícitas (conservar
  estilo, cambios mínimos, anclarse a símbolos del AST).

### Integración con skills (aprendizaje)
- Clasificación de patrones de edición (`_editor_clasificar_tarea`):
  renombrar, añadir_import, refactorizar_clase, añadir_funcion,
  corregir_error o general.
- Tras una edición exitosa se guarda/refuerza un skill `editor-<patrón>`
  (`_skill_editor_guardar`) con la estrategia que funcionó.
- En tareas nuevas, si existe un skill confiable (>=0.6), su estrategia se
  prioriza automáticamente en la cadena de edición
  (`_skill_editor_estrategia`) acelerando ediciones repetidas sin IA.

### Compatibilidad
- El editor propio sigue siendo opcional (`--editor propio`) y la cadena de
  modos `auto` mantiene la heurística AST → parche → sobrescritura.

### Tests
- Nueva suite `tests/test_editor_330.py` (31 tests): clasificación de tareas,
  detección de lenguaje, extracción de ruta del parche, validación previa,
  parseo/aplicación incremental de hunks (incluido desfase), flujo de
  resolución, skills del editor, integración en `ejecutar()` y prompts.

## [3.2.0] - 2026-08-24 - Extensión VS Code en TypeScript

### Migración JavaScript → TypeScript
- `vscode/extension.js` → `vscode/src/extension.ts` con tipos completos
  (`vscode.ExtensionContext`, `vscode.Disposable`, `vscode.OutputChannel`,
  `ChildProcessWithoutNullStreams`, …) y modo `strict` activado.
- Nueva configuración `vscode/tsconfig.json` (ES2020, commonjs, strict,
  sourceMap, declaration, salida en `out/`, raíz `src/`).
- Misma funcionalidad: abrirChat, ejecutarConsulta, planificar,
  configurarApiKey, anadirAlContexto y limpiarSeleccion; el webview sigue
  sirviendo el servidor web FastAPI en http://localhost:<puerto dinámico>.

### Empaquetado
- `package.json`: `"main": "./out/extension.js"` y scripts `compile`
  (`tsc -p ./`), `watch`, `package` y `vscode:prepublish` que compila.
- Nuevas devDependencies: `typescript`, `@types/vscode`, `@types/node`.
- `scripts/empaquetar.ps1` / `.sh`: ejecutan `npm install` +
  `npm run compile` antes de `vsce package` y verifican que exista
  `out/extension.js`.

### Tests
- Nueva suite `tests/test_vscode_ts_320.py`: opciones obligatorias del
  tsconfig, entry point, scripts, devDependencies, registro de comandos,
  webview y compilación real (`tsc --noEmit`) cuando hay compilador.
- `tests/test_vscode_016.py` actualizado a la nueva estructura.

## [3.1.1] - 2026-08-24 - Primer uso plug and play

### Ayuda al ejecutar sin argumentos
- `snapcontext` sin consulta ni flags muestra ahora una ayuda resumida y
  amigable (comandos comunes + ejemplos listos para copiar) con código 0,
  en lugar del error de argparse por falta de consulta.

### Bienvenida automática en el primer uso
- Nuevo estado ligero `~/.snapcontext/estado.json` con la clave
  `primer_uso` (`_cargar_estado()`, `_guardar_estado()`,
  `_primer_uso_pendiente()`).
- La primera vez que se ejecuta cualquier comando, se muestra el tutorial
  interactivo (`--bienvenida`) y se guarda `primer_uso = False`.
- `--bienvenida` explícito también actualiza el estado.
- Solo se lanza en terminales interactivos (nunca en tests, CI o scripts)
  para no bloquear flujos automatizados.

### Tests
- Nueva suite `tests/test_bienvenida_311.py` (10 tests): ayuda sin
  argumentos, helpers de estado, bienvenida automática (primera vez,
  segunda vez y entrada no interactiva) y flag explícito.

## [3.1.0] - 2026-08-24 - Instalación y onboarding sin fricción

### Modo offline por defecto (Ollama)
- Sin ninguna API key (GEMINI/ANTHROPIC/DEEPSEEK/GROQ/OPENAI), SnapContext
  usa Ollama automaticamente eligiendo el modelo mas ligero disponible
  (`llama3.2:1b`, `llama3.2`, `phi3`, `gemma2:2b`, `qwen2.5:0.5b`).
- Si no hay ni API key ni Ollama, mensaje claro con instrucciones
  (https://ollama.com o `snapcontext --init`).
- Nuevas funciones: `hay_api_key_configurada()`, `_estado_ollama()`,
  `_elegir_modelo_ligero()` y `_proveedor_offline()`.

### Diagnostico (--diagnostico)
- Revision completa de la instalacion con resumen en colores: Python,
  instalacion del paquete, dependencias opcionales, PATH, proveedor de IA
  y memoria SQLite (PRAGMA quick_check + conteo de skills).
- Cada problema muestra su solucion sugerida. Codigos: 0 OK, 2 avisos,
  1 errores.

### Reparacion (--reparar)
- Limpia entornos uv corruptos (carpetas vacias en ~/.snapcontext),
  reinstala con pip, recrea la base SQLite corrupta (con respaldo
  `.db.corrupto`) y ajusta el PATH en Windows via --setup-path.

### Onboarding mejorado
- Nuevo `--bienvenida`: tutorial interactivo de primeros pasos.
- `--init` ampliado: configurar Ollama (ofrece abrir ollama.com), crear un
  proyecto de prueba y lanzar el tutorial interactivo.

### Instalador Windows (NSIS)
- Deteccion de Python en .onInit (guia al usuario hacia python.org).
- Seccion opcional de instalacion/actualizacion con pip + --setup-path.

### Tests y docs
- Nueva suite `tests/test_diagnostico_310.py` (19 tests) para los nuevos
  comandos y el modo offline; versiones actualizadas en tests existentes.
- README e install.ps1/install.sh documentan el modo offline.

## [3.0.0] - 2026-08-24 - Aprendizaje autonomo: memoria SQLite, skills, curador y daemon

### Memoria persistente avanzada (SQLite)
- Nueva base `~/.snapcontext/memoria.db` (stdlib `sqlite3`: cero dependencias
  nuevas) con tablas `skills`, `historial_aprendizaje`, `contexto_kv` y `cola`.
- API de bajo nivel: `_db_init()`, `_db_query()`, `_db_insert()` y
  `_db_ejecutar()` (RLock reentrante, WAL, filas como dict).

### Skills (procedimientos reutilizables)
- Al completar una tarea con exito se genera un skill con los pasos clave
  (descripcion IA opcional; fallback local sin red).
- `_skill_buscar()` encuentra el skill mas similar (embeddings o
  Jaccard/contencion) y lo reutiliza en tareas repetidas.
- Refuerzo/castigo: los exitos suben la confiabilidad (+0.15); 3 usos sin
  fallos marcan el skill como confiable (1.0); los fallos lo bajan (-0.25)
  y con baja confiabilidad queda marcado para revision.

### Curador autonomo
- `_curador_ejecutar()` archiva skills sin uso > 30 dias, fusiona similares
  (sim >= 0.90, conserva el mas usado) y notifica skills en revision por la
  CLI. Manual (`--curador`) o programado via daemon.

### Daemon (--daemon)
- Bucle en segundo plano (`_daemon_tick` / `_daemon_bucle`) que corre el
  curador segun `--daemon-intervalo` (168 h = semanal) y procesa la cola de
  skills pendientes (`_cola_encolar()`), descartando los archivados.

### Integracion
- Gancho de aprendizaje al final del planificador (`_aprender_de_tarea`) y
  del orquestador; desactivable con `--sin-aprendizaje`. Nunca rompe el
  pipeline (errores de memoria solo avisan).
- Nuevo `AgenteAprendizaje` en `agentes.py`; integrado en el `Orquestador`.

### Tests
- Nuevo `tests/test_memoria_300.py` (27 tests): capa DB, skills, busqueda,
  aprendizaje, curador, daemon, agente de aprendizaje y flags CLI.
- Suite completa: 350 pruebas pasando.
---
## [2.4.0] - 2026-08-24 - Instaladores robustos: limpieza de entornos corruptos y PATH automático
### 🔧 Mejoras en los instaladores
- **Detección robusta de Python**: ahora los scripts `install.ps1` y `install.sh` buscan Python en el PATH, con el lanzador `py`, y en rutas típicas de instalación. Si no lo encuentran, dan instrucciones claras para instalarlo.
- **Limpieza automática de entornos corruptos de `uv`**: detectan si la carpeta de `uv` está vacía o sin los archivos necesarios y la eliminan automáticamente antes de instalar, evitando el error `Invalid environment ... directory is empty`.
- **Gestión de conflictos**: si ya hay una instalación de SnapContext con `uv`, el instalador pregunta si se desea eliminar.
- **Fallback a `pip`**: si la instalación con `uv` falla, el instalador intenta automáticamente con `pip`.
- **Configuración automática del PATH**: en Windows, añade `%USERPROFILE%\.local\bin` al PATH del usuario sin duplicados; en Linux/macOS, añade `~/.local/bin` al perfil del shell.

---
## [2.3.0] - 2026-08-24 - Integracion MCP-Planificador + dependencias dinamicas

### Nuevas funcionalidades

#### Paso mcp en el planificador
- Nuevo tipo de paso `"accion": "mcp"`: ejecuta cualquier herramienta MCP
  del registro (campos `herramienta` y `args`) y guarda el resultado en el
  contexto del plan.
- El resultado se expone como `{{resultado}}` y, si el paso define
  `variable`, tambien bajo ese nombre; los pasos siguientes sustituyen
  marcadores `{{...}}` en `descripcion`, `comando`, `archivos`,
  `contenido` y `args`.
- `_generar_plan()` / prompt actualizado para que el proveedor pueda
  proponer pasos `mcp` con dependencias, condiciones y variables.

#### Condiciones dinamicas
- `_evaluar_condicion()` acepta comparaciones `==` / `!=` sobre:
  - Resultados de pasos previos: `pasos[0].resultado == 'ok'`.
  - Variables del contexto: `resultados.mi_variable != ''`.
- Nueva funcion de condicion `variable_existe('nombre')`.
- Las formas clasicas (`archivo_existe`, `archivo_contiene`,
  `comando_exito`) siguen funcionando igual.

#### execute_command mas flexible (MCP)
- `background=True` devuelve un `pid` consultable con
  `execute_command_status`.
- `capture_output=True` (por defecto) decide entre capturar
  `stdout`/`stderr` o mostrar la salida en tiempo real.
- En el planificador, el resultado queda en el contexto dinamico para
  pasos posteriores.

#### Paralelismo con dependencias dinamicas
- Con `--paralelo N`, un paso cuya condicion referencia una variable que
  otro paso pendiente puede producir se bloquea hasta que este
  disponible (sin deadlocks: si nada es lanzable, se marca saltado).

### Tests
- Nuevo `tests/test_plan_mcp_230.py` (21 tests): contexto dinamico,
  pasos MCP, condiciones dinamicas, marcadores `{{...}}`,
  `execute_command` background/capture y planificador paralelo con
  dependencias dinamicas.
- Suite completa: 323 pruebas pasando.

---

## [2.2.0] - 2026-08-23 · 🌳 Editor Propio Fase 3: Edición basada en AST

### ✨ Nuevas funcionalidades

#### 🎯 Refactorizaciones Estructurales con Árbol Sintáctico (AST)
- **Nuevo modo `--modo-edicion ast`** (además de `sobrescribir`, `parche` y `auto`):
  - `ast`: edita comprendiendo la estructura sintáctica del archivo y cae a
    sobrescritura si no se puede aplicar.
- **Editor con AST (`_editor_ast`)**:
  - Lee el contenido, genera el AST (Python con `ast`; otros lenguajes con
    `tree-sitter` si está instalado) y lo pasa al proveedor junto con la tarea.
  - El proveedor devuelve un *parche AST* (lista de operaciones de modificación
    del árbol) o el código completo resultante.
  - Aplica los cambios y convierte de vuelta a código fuente (con backup).
- **Operaciones de refactorización**:
  - Renombrar símbolos (variables, funciones, clases, parámetros) en todo el
    archivo con `_renombrar_identificador` (por token, sin tocar cadenas).
  - Inserción de imports con `_insertar_import`.
  - Reemplazo completo vía `{"tipo": "completo", "codigo": "..."}` (extraer
    función, mover código, añadir funciones, etc.).
- **Soporte multi-lenguaje**: `_resumen_ast` usa `tree-sitter` (si está
  instalado) para archivos no-Python y hace fallback a `ast`/parche/sobrescritura.

#### 🧩 Heurística en modo `auto`
- En modo `auto`, el agente elige estrategia según el lenguaje y la tarea:
  - Tarea estructural (renombrar/refactorizar/extraer/mover/insertar función)
    en archivo con AST → `ast` → `parche` → `sobrescribir`.
  - Restaurantos → `parche` → `sobrescribir`.

#### 🤖 Compilación
- Nueva clase `AgenteEditorAST` en `agentes.py` e integrada en el `Orquestador`.
- `AgenteEditorPropio` ahora soporta el modo `ast` con fallback.
- Flag `--modo-edicion` ampliado a `{sobrescribir,parche,auto,ast}`.

### 🧪 Tests
- Nuevo `tests/test_editor_ast_220.py` (16 tests): resumen AST, renombrado,
  interpretación de operaciones, `_editor_ast` (renombrar/completo/fallbacks),
  clase `AgenteEditorAST`, cadenas de estrategias, heurística `auto` y flags.
- Suite completa pasando con 302+ pruebas unitarias.

---
## [2.1.0] - 2026-08-23 · 🧩 Editor Propio Fase 2: Diffs y Parches Unificados

### ✨ Nuevas funcionalidades

#### 🎯 Parches Unificados y Ediciones Precisas
- **Generación y aplicación de diffs unificados**:
  - Función `_generar_parche(original, nuevo, ruta_archivo)` con `difflib.unified_diff()`.
  - Función `_aplicar_parche(parche, directorio)` que intenta aplicar con `git apply --whitespace=nowarn` y fallback a `patch -p1`.
- **Nuevo flag `--modo-edicion {sobrescribir,parche,auto}`** (por defecto `auto`):
  - `auto`: intenta primero aplicar parche unificado; si falla o no es diff, hace fallback a sobrescritura.
  - `parche`: aplica exclusivamente parches unificados.
  - `sobrescribir`: sobrescritura completa directa de archivos (Fase 1).
- **Mejoras en `AgenteEditorPropio` y `Orquestador`**:
  - Prompts refinados para pedir diffs unificados al proveedor de IA con encabezados estándar (`--- a/` y `+++ b/`).
  - Reducción del ruido en los commits y modificaciones más limpias y quirúrgicas.

### 🧪 Tests
- Nuevo `tests/test_diff_editor_210.py` (9 tests): generación de diffs unificados, aplicación con mocks de `git apply` / `patch`, ejecución de `AgenteEditorPropio` en modo parche y fallback a sobrescritura, y validación de flags.
- Toda la suite del proyecto actualizada y pasando con 280+ pruebas unitarias.

---

## [2.0.0] - 2026-08-23 · 🚀 Versión Estable & Editor Integrado Propio

### ✨ Nuevas funcionalidades

#### 🛠️ Editor Propio (Fase 1 — Sobrescritura de Archivos)
- **Nuevo flag `--editor {aider,propio}`**:
  - `aider` (por defecto): mantiene el flujo original con Aider sin romper nada.
  - `propio`: usa el editor integrado de SnapContext para escribir/sobrescribir código directamente sin requerir herramientas externas.
- **Sobrescritura segura con copias de seguridad automáticas**:
  - Función `_editor_sobrescribir()` con validación de rutas y protección estricta contra path traversal (`..` / `/`).
  - Copias de seguridad automáticas guardadas en `~/.snapcontext/backups/<timestamp>_<nombre_archivo>`.
  - Nueva clase `AgenteEditorPropio` integrada en la arquitectura de agentes (`agentes.py`) y en el `Orquestador` (`orquestador.py`).
- **Integración con Planificador y Chat**:
  - En `--plan` con `--editor propio`, los pasos de edición generan el código con el proveedor de IA configurado y lo aplican directamente.

#### ⚡ Optimización de Rendimiento
- **Indexación paralela con `ThreadPoolExecutor`**:
  - `_indexar_proyecto()` ahora lee y hashea archivos concurrentemente acelerando proyectos medianos y grandes.
  - Exclusión eficiente de carpetas pesadas (`node_modules`, `.git`, `build`, etc.).

### 🔧 Compatibilidad y Estabilidad
- Todas las funcionalidades existentes (Planificador, Chat REPL, Servidor Web FastAPI, Extensiones VS Code y JetBrains, MCP, Memoria CLAUDE.md) mantienen compatibilidad total.
- Bump de versión a `2.0.0` en `snapcontext.py`, `pyproject.toml`, `web/app.py`, `vscode/package.json` y `jetbrains/build.gradle.kts`.
- Clasificador de paquete actualizado a `Production/Stable`.

### 🧪 Tests
- Nuevo `tests/test_editor_propio_200.py` (9 tests): creación de archivos nuevos, backups automáticos en sobrescrituras, protección contra rutas fuera de repo, flags CLI y clase `AgenteEditorPropio`.
- Suite completa pasando con 270+ pruebas unitarias.

---

## [1.7.0] - 2026-08-23 · 🧠 Extensión para IntelliJ IDEA / PyCharm

### ✨ Nuevas funcionalidades
- **Nueva extensión JetBrains** (`jetbrains/`, Kotlin + Gradle) con la misma
  experiencia que la de VS Code:
  - **Tools → SnapContext → «Ejecutar consulta…»** (`Alt+Shift+S`): pipeline
    completo sobre el proyecto abierto.
  - **«Planificar tarea…»**: lanza el planificador `--plan --auto`.
  - **«Corregir con bucle de pruebas…»**: `--test-loop` (Aider → pruebas → reparar).
  - **«Abrir interfaz web»**: arranca `snapcontext --web` y abre
    `http://localhost:8000` en el navegador cuando Uvicorn está listo.
  - **Añadir al contexto**: menú contextual del explorador de proyectos para
    marcar archivos prioritarios (equivalente a `/add`; usa el mismo sufijo
    «Revisa especialmente estos archivos: …» que la extensión VS Code).
  - **Consola dedicada** en la herramienta inferior «SnapContext» con salida en
    tiempo real y barra de progreso cancelable.
- **Comunicación por CLI**: la extensión invoca `python -m snapcontext` (o el
  comando configurado) con `ProcessBuilder`, pasando `--directorio <proyecto>`,
  `--provider` y `--no-confirmar` según los ajustes, e inyecta la clave API
  opcional como variables de entorno. Sin dependencias nuevas en tiempo de
  ejecución del plugin.

### 🔧 Compatibilidad
- CLI, web, extensión VS Code e instalador .exe sin cambios funcionales.

### 🧪 Tests
- Nuevo `tests/test_jetbrains_170.py` (12 tests): estructura de ficheros,
  validez de `plugin.xml` (tool window, configurable, acciones), coherencia
  Gradle/Kotlin y paridad del mecanismo de contexto con VS Code.

---

## [1.6.0] - 2026-08-23 · 🎨 Interfaz web más completa e interactiva

### ✨ Nuevas funcionalidades

#### Editor Monaco
- **Guardar con confirmación**: al pulsar 💾 Guardar (o `Ctrl+S`) se pide
  confirmación antes de sobrescribir el archivo en disco.
- El resaltado multi-lenguaje ya existente (mapa `_comando_para_monaco`)
  se aplica también a los archivos abiertos desde el grafo y el historial.

#### Grafo de dependencias (D3.js)
- **Zoom y pan** con la rueda y arrastre del fondo (`d3.zoom`, escala 0.2–4×).
- **Resaltado de rutas**: doble clic en un nodo resalta sus conexiones
  directas (verde) y atenúa el resto; doble clic en el fondo limpia.
- **Filtros** por lenguaje (select poblado dinámicamente) y por nivel de
  dependencia (BFS desde el archivo abierto): «todos», «1 (directos)», «≤2», «≤3».
- Tooltips por nodo con ruta completa, lenguaje y nivel.

#### Panel de acciones rápidas
- Cada botón tiene **icono + descripción** (tooltip nativo).
- Nuevo botón **«🧪 Tests»**: abre el modal de comandos pre-rellenado
  (`flutter test`) para lanzar la suite del proyecto.

#### Feedback visual y UX
- **Notificaciones toast** (info/ok/aviso/error) para selecciones, guardados,
  errores de conexión y acciones.
- **Historial de sesión**: panel con las últimas tareas ejecutadas; clic para
  reutilizar la consulta.
- Panel de resultados con **ruta completa** + botón «Abrir» por archivo.
- **Atajos de teclado**: `Ctrl+Enter` ejecutar · `Ctrl+S` guardar ·
  `Ctrl+D` dependencias · `Ctrl+R`/`Ctrl+O` modal de comandos · `Esc` cerrar.

### 🔧 Compatibilidad
- Todo funciona con fallbacks: sin Monaco → `textarea`; sin d3 → lista de
  enlaces en texto; sin filtros aplicados → grafo completo.
- CLI sin cambios; extensión VS Code a v1.6.0 con su webview
  (`vscode/webview/index.html`) sincronizada con la nueva interfaz.

### 🧪 Tests
- Nuevo `tests/test_web_160.py` (12 tests): confirmación de guardado, toasts,
  historial, atajos, zoom, filtros y resaltado del grafo, botón Tests y campo
  `lenguaje` en los nodos del grafo (base del filtro).

---

## [1.5.0] - 2026-08-23 · 📦 Instalador .exe para Windows (sin Python)

### ✨ Nuevas funcionalidades
- **Ejecutable único `snapcontext.exe`** generado con **PyInstaller**
  (`snapcontext.spec`): incluye el código y las dependencias ligeras
  (`google-generativeai`, `openai`, `questionary`, `fastapi`, `uvicorn` y el
  estático de la interfaz web). El usuario final **no necesita Python**.
- **Instalador NSIS `SnapContext-Setup-<versión>.exe`** (`installer.nsi`):
  - Instala en `%LOCALAPPDATA%\Programs\SnapContext` (por usuario, sin admin).
  - Añade la carpeta al **PATH del usuario** automáticamente.
  - Accesos directos opcionales en **Menú Inicio** y **Escritorio**.
  - **Desinstalador** completo (PATH, accesos, registro; conserva
    `~\.snapcontext` con claves e historial).
- **Automatización**: nuevo script `scripts/empaquetar_exe.ps1`
  (y `empaquetar_exe.sh` para CI/Linux) que ejecuta PyInstaller + NSIS de una
  sola vez y verifica que el exe responde a `--version`.
- **Modo full opcional**: `-Full` (o `SNAPCONTEXT_EXE_FULL=1`) incluye los
  extras pesados (`sentence-transformers`, `tree-sitter`); por defecto se
  excluyen y SnapContext usa sus fallbacks (`ast`, aviso en búsqueda
  semántica), manteniendo el instalador ligero.

### 🔧 Compatibilidad
- `web/app.py` resuelve su estático vía `sys._MEIPASS` cuando corre como exe
  y vía `__file__` en desarrollo: `--web` funciona igual en ambos modos.
- CLI, chat, planificador, MCP y extensión VS Code sin cambios.

### 🧪 Tests
- Nuevo `tests/test_exe_150.py`: coherencia de artefactos (spec/NSIS/scripts)
  y resolución del estático web en modo desarrollo/frozen.

---

## [1.4.0] - 2026-08-23 · 🤖 MCP avanzado + planificador autónomo

### ✨ Nuevas funcionalidades

#### Herramientas MCP más potentes
- **`ast_avanzado`**: análisis sintáctico multi-lenguaje con **tree-sitter**
  (funciones, clases, imports y llamadas en Python, JS/TS, Dart, Go, Rust,
  Java, C/C++, Ruby, PHP...). Import diferido; sin tree-sitter hace
  *fallback* al módulo `ast` de la stdlib (solo Python) y para otros
  lenguajes devuelve un error descriptivo sin romper al agente.
  Dependencia opcional: `pip install snapcontext[mcp_avanzado]`.
- **`semantic_search`**: la búsqueda semántica por embeddings (v1.1.0) queda
  integrada en el registro de herramientas MCP, de modo que el agente puede
  usarla automáticamente como contexto. Falla elegantemente si no está el
  extra `embeddings`.
- **ripgrep (`rg`) preferente** en `grep`: ya se detectaba con
  `shutil.which('rg')`; ahora se documenta como alternativa ultrarrápida que
  respeta `.gitignore`, con fallback a `grep`/`findstr`.

#### Planificador autónomo
- **Dependencias entre pasos**: cada paso admite `"dependencias": [índices]`.
  Un paso solo se ejecuta si todas sus dependencias tuvieron éxito; si alguna
  falló o se saltó, el paso queda marcado como `saltado`.
- **Ejecución condicional**: campo `"condicion"` con las funciones
  `archivo_existe('ruta')`, `archivo_contiene('ruta', 'texto')` y
  `comando_exito('comando')`. Si la condición es falsa, el paso se salta.
- **Paralelismo básico**: nuevo flag `--paralelo N` (por defecto 1). En modo
  `--plan --auto`, lanza hasta N pasos independientes simultáneos con logs
  identificados `[paso N]`; los pasos dependientes esperan a sus rondas.

#### Chat (`--chat`)
- **`/grafo`**: grafo de dependencias del proyecto en formato texto ASCII
  (reutiliza `_grafo_dependencias` de v1.2.0).
- **`/dependencias <archivo>`**: imports directos y dependencias inversas
  («importado por») de un archivo.
- **`/buscar <consulta>`**: alias de `/search` (búsqueda semántica).

### 🔧 Compatibilidad
- Si ripgrep o tree-sitter no están instalados, las herramientas avisan y
  continúan sin errores (fallback a grep/findstr y ast respectivamente).
- CLI, chat, web, extensión VS Code y el resto de funcionalidades no cambian.

### 🧪 Tests
- Nuevo `tests/test_mcp_140.py` (24 tests): condiciones del planificador,
  normalización de dependencias, nuevas herramientas MCP, planificador
  paralelo con dependencias, flag `--paralelo` y comandos del chat.

---

## [1.3.0] - 2026-08-23 · 🚀 Proyectos desde cero + instaladores mejorados

### ✨ Nuevas funcionalidades
- **Nuevo flag `--iniciar-proyecto`** (alias `--no-validar`): desactiva por
  completo la validación de carpeta de proyecto, para empezar un proyecto
  desde cero en una carpeta vacía. Muestra el aviso
  `⚠️ Modo iniciar-proyecto: se omite la validación de carpeta...`.
- **Validación de proyecto más permisiva (`_es_proyecto_valido()`)**: ahora
  también valida si hay carpetas típicas (`lib/`, `src/`, `supabase/`,
  `app/`, `packages/`, `backend/`) **vacías**, archivos de código en la raíz
  (`.py`, `.dart`, `.js`, `.ts`, `.go`, `.rs`, `.java`, ...) **vacíos**, o
  archivos de configuración típicos (`pubspec.yaml`, `package.json`,
  `requirements.txt`, `go.mod`, `Cargo.toml`, `setup.py`, `pyproject.toml`)
  **vacíos**.
- **`--local` y `--directorio` explícito ya no bloquean**: si no se detecta
  estructura de proyecto, solo se muestra un aviso y se continúa (antes se
  abortaba).

### 🔧 Correcciones y mejoras
- Mensaje de error de validación más útil: sugiere `--iniciar-proyecto`,
  `--local` o `--directorio <ruta>`.
- `--help` documenta el nuevo flag y aclara que `--local` desactiva la
  validación.
- **Instalador Windows (`install.ps1`)**: detecta instalaciones previas vía
  `uv tool list` / `%USERPROFILE%\.local\bin\snapcontext.exe` y las elimina
  (evita que `snapcontext --version` muestre una versión antigua de uv);
  persiste `~\.local\bin` en el PATH del usuario.
- **Instalador Linux/macOS (`install.sh`)**: misma limpieza de instalaciones
  uv previas y añade `~/.local/bin` al perfil del shell (`.bashrc`/`.zshrc`)
  si falta.
- **Extensión VS Code v1.3.0**: `activationEvents` explícitos por comando
  (corrige problemas de activación) y títulos de comandos sin duplicar el
  prefijo en la paleta.

### 📝 Documentación
- Nueva sección en el README: **«🚀 Empezar un proyecto desde cero»** con
  ejemplos de `--iniciar-proyecto` y `--local`.

---

## [1.2.0] - 2026-08-22 · 🌐 Interfaz web avanzada (editor Monaco + dependencias)

### ✨ Nuevas funcionalidades (FEATURES)

#### Editor web (Monaco)
- **Monaco Editor** (el mismo de VS Code) integrado en `web/static/index.html`
  desde CDN, con resaltado de sintaxis para Python, JS/TS, Dart, Go, Rust,
  Java, C/C++, C#, Swift, etc.
- **Abrir archivo en el editor**: clic en un archivo del panel de resultados
  y clic en un nodo del grafo de dependencias → nuevo evento
  `archivo_seleccionado` con contenido + lenguaje.
- **Edición en vivo** con guardado manual (`guardar_archivo`) que escribe en
  disco; **fallback a `<textarea>`** si Monaco no carga.
- Nuevas utilidades en `snapcontext`: `_comando_para_monaco()` (mapa
  extensión → lenguaje) y el protocolo de lectura/escritura en `web/app.py`.

#### Visualización de dependencias
- Panel con pestaña **Dependencias** que dibuja un **grafo interactivo**
  (force-directed con **d3.js**): zoom, arrastre y clic para abrir archivo.
- `_extraer_dependencias()` extrae los imports por lenguaje (Python `import`,
  JS/TS `import`/`require`, Dart, Go, Rust, Java/Kotlin).
- `_grafo_dependencias()` construye nodos + enlaces resolviendo los imports
  contra los archivos del repositorio (`_resolver_dependencia`).
- `_buscar_en_codigo()` centraliza la exploración por `rg`/`grep`/`findstr`.

#### Panel de acciones rápidas
- Botones que lanzan acciones sin escribir comandos: **Fix**, **Review**,
  **Plan**, **Run** (comando personalizado), **Search** (embeddings) y
  **Explorar** (búsqueda de código). Emiten `accion_ejecutada`.

### 🔌 Comunicación con el orquestador (INTEGRATION)
- `web/app.py` amplía el protocolo WebSocket con `leer_archivo`,
  `guardar_archivo`, `dependencias`, `semantica`, `explorar` y `accion`,
  respondiendo `archivo_seleccionado`, `archivo_guardado`,
  `dependencias_actualizadas`, `semanticos`, `exploracion` y `accion_ejecutada`.
- Compatibilidad total con el protocolo `consulta`/`tarea` preexistente.
- La CLI y la extensión VS Code no se ven afectadas; `vscode/webview/` sigue
  siendo copia de `web/static/index.html`.

### 🧪 Tests
- Nuevo `tests/test_web_120.py` (mapa de lenguajes, extracción de imports,
  grafo de dependencias, búsqueda en código, versionado).
- Versionado actualizado a `1.2.0` en todos los tests.

### 📝 Documentación (DOCUMENTATION)
- README: badge 1.2.0 y nueva sección "🌐 Interfaz web avanzada".

---

### ✨ Nuevas funcionalidades (FEATURES)

#### Sistema de embeddings locales
- **Indexación semántica**: `pip install "snapcontext[embeddings]"` activa un
  indexador local basado en `sentence-transformers` (modelo `all-MiniLM-L6-v2`)
  que genera embeddings para fragmentos de ~512 tokens de cada archivo de
  código (`.py`, `.js`, `.ts`, `.dart`, `.go`, `.rs`, …).
- **Búsqueda semántica**: nueva función `_buscar_semanticamente()` que genera un
  embedding de la consulta y calcula la similitud de coseno con todos los
  fragmentos indexados, devolviendo los archivos más relevantes.
- **Selección con embeddings**: `_seleccionar_archivos_con_embeddings()`
  filtra por un umbral de similitud (0.6 por defecto) y completa con resultados
  locales si no alcanza `max_archivos`.
- **Comando `/search`** en `--chat`: ejecuta búsqueda semántica interactiva y
  muestra los archivos con su puntuación de similitud.
- **Caché persistente con invalidación**: el índice se guarda en
  `~/.snapcontext/index/` con un **hash del proyecto**; si detecta cambios,
  reindexa automáticamente (con aviso). Los archivos sin cambios reutilizan sus
  embeddings cacheados.

### 🔧 Integración en el pipeline (INTEGRATION)
- En `_planificar()` (orquestador), antes de llamar al proveedor de IA:
  carga (o indexa) el índice, ejecuta `_seleccionar_archivos_con_embeddings()`
  y reordena los candidatos locales para priorizar los archivos más similares a
  la consulta, reduciendo la carga sobre el proveedor y mejorando la precisión.
- Sin el extra de embeddings, SnapContext se comporta idénticamente a v1.0.0.

### 📝 Documentación (DOCUMENTATION)
- README: nueva sección "🧠 Búsqueda semántica con embeddings" con instalación,
  funcionamiento, uso en el pipeline, comando `/search` y API directa desde
  Python.
- README: badge de versión actualizado a 1.1.0.
- README: `pip install "snapcontext[embeddings]"` añadido a la sección de
  instalación.

### 🔴 Correcciones (BUGFIXES)
- Eliminado código inalcanzable y corrupto (restos de `_patrones_gitignore`) en
  `_guardar_indice()` que hacía referencia a la variable no definida
  `patrones`.

---

## [1.0.0] - 2026-08-22 · 🎉 Primera versión estable

SnapContext 1.0.0 consolida todas las funcionalidades desarrolladas desde la
0.4.0 en un lanzamiento estable y documentado.

### Resumen de características

- **Selección inteligente de contexto**: auto-detección del tipo de proyecto
  (Flutter, Node, Python, Go, Rust…), escaneo heurístico + selección con IA
  (Gemini, Claude/Anthropic, Ollama local, DeepSeek, Groq).
- **Tareas con Aider**: edición asistida con bucle de pruebas (`--test-loop`),
  bucles con servidor (`--server-loop`/`--manual-loop`) y revisión experta.
- **Alias**: `fix`, `review`, `server`, `interactive`.
- **Chat interactivo** (`--chat`): REPL con herramientas MCP (`/tool`,
  `/tools`), alias del pipeline, historial de sesión y `/save`.
- **MCP**: herramientas `grep`, `read_file`, `list_files`, `ast`, `git_status`,
  `git_diff`, `execute_command` + herramientas de usuario en
  `~/.snapcontext/mcp_tools.json`.
- **Planificador** (`--plan`): descomposición en pasos JSON
  (editar/ejecutar/consultar) con menú continuar/reintentar/saltar.
- **Modo autónomo** (`--auto`): ejecución sin confirmaciones paso a paso con
  reintentos automáticos (3) respetando permisos guardados.
- **Permisos**: `--confirmar/--no-confirmar` con preferencias persistentes
  (`permisos.json`, opciones s/n/t/a).
- **Memoria de proyecto**: `CLAUDE.md`/`SNAPCONTEXT.md` cargado automáticamente;
  generación con `--init-claude` (IA + fallback offline) y actualización
  propuesta tras tareas exitosas.
- **Historial persistente**: `historial.json` con `--historial` /
  `--historial-limpiar`.
- **Interfaces**: CLI, web en tiempo real (`--web`), extensión VS Code
  (`vscode/`, canal "SnapContext Output", webview del chat).
- **Calidad**: 163+ tests unitarios/de integración, demo sin dependencias
  (`--demo`), empaquetado PyPI y `.vsix`.

### Notas de migración

Ninguna: 1.0.0 es retrocompatible con las configuraciones y archivos de
versiones 0.x (config.json, permisos.json, historial.json, mcp_tools.json).

---

## [0.17.0] - 2026-08-22

### 🤖 Modo autónomo (`--auto`)

- Nuevo flag `--auto` para el planificador: ejecuta los pasos del plan sin
  confirmación inicial ni menú paso a paso.
- Los pasos fallidos se **reintentan automáticamente hasta 3 veces** antes de
  continuar con el siguiente; el resumen final muestra el número de intentos.
- Sigue respetando las preferencias de `permisos.json`: un tipo marcado como
  "nunca" se deniega automáticamente (nueva función `_permiso_recordado()`).
  Con `--no-confirmar` no añade diferencia adicional (todo ya está permitido).

---

## [0.16.0] - 2026-08-22

### 🧩 Extensión para VS Code

- Nuevo directorio `vscode/` con la extensión nativa: `package.json`
  (comandos, menús y configuración), `extension.js`, webview (copia de la
  interfaz web + `servidor_webview.py`) y scripts de empaquetado.
- Comandos: *Abrir chat* (webview con la interfaz web), *Ejecutar consulta*,
  *Planificar* (`--plan --no-confirmar`) y *Configurar API key*.
- Canal de salida dedicado **"SnapContext Output"** con los logs del
  orquestador; el workspace abierto se usa como directorio del proyecto.
- Integración visual con el editor: clic derecho en el explorador → "Añadir
  al contexto"; los archivos marcados se adjuntan a consultas y planes.

---



## [0.15.0] - 2026-08-22

### 📄 Memoria de proyecto (CLAUDE.md)

- Al iniciar cualquier modo (tarea, chat, plan, demo) SnapContext busca y
  carga `CLAUDE.md` (o `SNAPCONTEXT.md`) en la raíz del proyecto y lo usa como
  contexto persistente del agente.
- Nuevo comando **`snapcontext --init-claude`**: escanea el proyecto
  (estructura vía MCP, tipo detectado, estado git) y genera la memoria con el
  proveedor de IA; sin conexión cae a una plantilla básica offline.
- Chat: `/claude` muestra la memoria; `/context` muestra memoria + archivos en
  contexto; cada mensaje al proveedor incluye la memoria como contexto.
- Planificador: la memoria se incluye en la planificación para respetar las
  convenciones del proyecto. Tras una tarea/plan exitoso se propone (con
  confirmación) actualizar la memoria con lo aprendido.

---



## [0.14.0] - 2026-08-22

### 🛠 MCP (Model Context Protocol): herramientas para el agente

- Sistema de herramientas con resultados estructurados: registro predefinido
  (`grep`, `read_file`, `list_files`, `ast`, `git_status`, `git_diff`,
  `execute_command`) más herramientas de usuario definidas en
  `~/.snapcontext/mcp_tools.json` (comandos shell).
- Dispatcher `_ejecutar_herramienta_mcp()` con confirmación integrada
  (reutiliza `_confirmar_accion`; `execute_command` y herramientas de usuario
  requieren permiso; las de lectura no).
- Chat: `/tools` lista las disponibles, `/tool <nombre> [args|JSON]` las
  ejecuta mostrando el resultado coloreado y lo añade al contexto de la
  conversación. Mensajes de exploración ("busca…", "estado de git"…) activan
  automáticamente herramientas de solo lectura como contexto del proveedor.
- Planificador: `_generar_plan()` explora el proyecto (git_status + list_files)
  antes de pedir el plan para generar pasos más precisos.

---

## [0.13.0] - 2026-08-22

### 🔒 Permisos y confirmaciones (`--confirmar` / `--no-confirmar`)

- Nueva función `_confirmar_accion(descripcion, tipo, detalles)`: muestra un
  resumen de la acción y pregunta `¿Permitir esta acción? (s/n/t/a)`:
  - `s` permitir una vez · `n` saltar · `t` permitir todas las del tipo ·
    `a` no permitir ninguna del tipo.
- Las respuestas `t`/`a` se guardan en `~/.snapcontext/permisos.json`
  (`{"<tipo>": "siempre"|"nunca"}`) y se respetan en sesiones futuras.
- Integrado antes de cada paso del planificador (`--plan`, los tres tipos de
  acción), en `/run` y `/edit` del chat. `/explore` sigue sin pedir permiso
  (solo lectura). Con `--no-confirmar` se omiten todas las preguntas
  (modo automático).

---

## [0.12.0] - 2026-08-22

### ✨ Planificador de tareas (`--plan`)

- Nuevo modo agéntico: `snapcontext --plan "tarea"` pide al proveedor de IA un
  plan en JSON (`descripcion`, `accion`: editar/ejecutar/consultar, `archivos`,
  `comando`), muestra los pasos numerados y pide confirmación antes de actuar.
- Ejecución secuencial: `editar` reutiliza el orquestador (`_planificar` +
  `_bucle_test`), `ejecutar` usa `_ejecutar_comando`, `consultar` pregunta al
  proveedor. Menú por paso (continuar/reintentar/saltar/abortar) y resumen
  final registrado en `~/.snapcontext/historial.json`.
- Integración git explícita: `--branch <nombre>` crea la rama antes de empezar
  y `--git-commit/--no-git-commit` controla commits automáticos
  (`paso: <descripción>`) tras cada paso exitoso (por defecto: activado).

---

## [0.10.0] - 2026-08-22

### ✨ Soporte para Claude (Anthropic) como proveedor de IA

- Nueva entrada `"anthropic"` en `PROVEEDORES` (tipo `anthropic`, clave
  `ANTHROPIC_API_KEY`, modelo por defecto `claude-3-5-sonnet-20241022`).
- Nueva función `seleccionar_archivos_con_anthropic()` que usa el SDK oficial
  (`client.messages.create()`); import diferido con aviso claro si falta la
  librería: `pip install snapcontext[anthropic]`.
- `seleccionar_archivos()` redirige al nuevo tipo `"anthropic"`; también
  soportado en `--init` (prueba de conexión) y en el menú interactivo.
- Dependencia opcional `anthropic = ["anthropic>=0.30.0"]` en `pyproject.toml`.

### ✨ Modo chat interactivo (`--chat`)

- `snapcontext --chat` abre un REPL (`💬 SnapContext Chat`) con comandos:
  `/salir`, `/archivos`, `/limpiar`, `/seleccion <consulta>`,
  `/provider <proveedor>`, `/historial` y `/ayuda`. Cualquier otro texto se
  envía al proveedor actual manteniendo el historial de conversación de la
  sesión (últimos 20 turnos por petición).

### ✨ Memoria persistente (`historial.json`)

- Nuevos flags `--historial` (muestra las últimas 20 tareas) y
  `--historial-limpiar` (borra el archivo).
- `_cargar_historial()` / `_guardar_historial()` persisten en
  `~/.snapcontext/historial.json`; cada tarea ejecutada se registra
  automáticamente (fecha, consulta, archivos, resultado y duración) en un
  hilo secundario para no bloquear la salida.

### ✨ Lectura de archivos y ejecución de comandos genéricos

- `_leer_archivo(ruta)`: lee rutas relativas/absolutas (devuelve `None` con
  aviso si falla).
- `_ejecutar_comando(comando, directorio)`: ejecuta comandos de shell con
  timeout y devuelve `(codigo_retorno, stdout, stderr)`. Base para el
  planificador autónomo y disponible desde el chat.

---

## [0.9.0] - 2026-08-21

### ✨ Modo demo (`--demo`)

- Nueva función `_ejecutar_demo()` que muestra el valor de SnapContext en ~1
  minuto, **sin necesidad de API key ni Aider**:
  - Crea un proyecto Python de ejemplo en una carpeta temporal
    (`tempfile.mkdtemp()`), con un `saludar(nombre)` con bug y un test que
    falla.
  - Fase 1: ejecuta la selección de archivos con `--vista-previa --local` y la
    muestra en tiempo real.
  - Fase 2: ejecuta el bucle de pruebas completo (Editor → Tester → error
    realimentado → corrección → éxito) con un editor de demostración offline.
  - Resumen final con el tiempo total, los archivos seleccionados y el resultado
    de las pruebas.
- Integrado en `main()`: `snapcontext --demo`. No rompe ningún flag existente.

---

## [0.8.0] - 2026-08-21

### ✨ Mejoras de usabilidad

- **Auto-detección del tipo de proyecto** (`pubspec.yaml`, `package.json`,
  `requirements.txt`/`pyproject.toml`, `go.mod`, `Cargo.toml`, etc.) que ajusta
  automáticamente las carpetas y extensiones por defecto. Es transparente para
  el usuario (solo se informa con `--depurar`). Si no se detecta ningún tipo,
  se mantiene el comportamiento actual (`lib/`, `supabase/`, …).
- **Extensiones:** el escaneo ahora puede filtrarse por extensión según el tipo
  de proyecto detectado (`listar_archivos_candidatos`, `escanear_repositorio`).
- **Alias / atajos de comandos**:
  - `snapcontext fix "…"` → `--test-loop`
  - `snapcontext review "…"` → `--vista-previa --experto`
  - `snapcontext server "…"` → `--server-loop`
  - `snapcontext interactive` → `--web`
  - Si el primer argumento no coincide con ningún alias, se trata como consulta
    (comportamiento actual).
- **Interfaz web en tiempo real:** indicador de estado (spinner/barra de
  progreso), cronómetro con el tiempo transcurrido, contador de archivos
  escaneados y seleccionados, y renderizado con colores de los nuevos eventos
  (`inicio`, `escaneo_inicio/fin`, `seleccion_inicio/fin`, `test_inicio/fin`).

- Reparada una corrupción del working tree que dejaba funciones de detección
  dentro del literal `_LOGO` y un `crear_parser` duplicado.

---

## [0.6.0] - 2026-08-20

### 🔴 Correcciones críticas (CRITICAL FIXES)

#### `--init` con proveedores Groq y DeepSeek
- **Antes**: Usaba incorrectamente `questionary.text(password=True)` que no funciona.
- **Después**: Ahora usa correctamente `questionary.password()` para ingresar claves API de forma segura.
- **Impacto**: Los usuarios podrán configurar Groq y DeepSeek sin errores durante la instalación inicial.

#### Manejo de errores de configuración
- **Antes**: Si `~/.snapcontext/config.json` estaba corrupto o no existía, el programa fallaba silenciosamente.
- **Después**: Ahora envuelve la lectura del archivo con `try/except` explícito para `FileNotFoundError` y `json.JSONDecodeError`.
- **Impacto**: Los usuarios reciben avisos claros en lugar de errores inesperados o estados inconsistentes.

### 🟡 Mejoras (IMPROVEMENTS)

#### Actualización de versión
- Todos los metadatos ahora reflejan la versión 0.6.0:
  - `pyproject.toml`: `version = "0.6.0"`
  - `snapcontext.py`: `VERSION = "0.6.0"`
  - `README.md` y `index.html`: badges actualizados

#### One-liner de instalación con fallbacks
- Se añadieron URLs alternativas a `raw.githubusercontent.com` en README.md e index.html.
- Si GitHub Pages falla, los usuarios pueden instalar directamente desde el código raw del repositorio.

#### Scripts de instalación mejorados
- **install.sh**: Mensajes claros sobre detección de sistema, verificación de Python 3.9+, y fallbacks a `pip`.
- **install.ps1**: Funcional en Windows con soporte para PowerShell, mensajes coloreados y sugerencias para `$PROFILE` si el PATH no se actualiza automáticamente.

### 📝 Documentación (DOCUMENTATION)

#### CHANGELOG.md nuevo
- Historial de versiones desde la 0.6.0.
- Seguirá documentando cambios futuros en futuras versiones.

#### Actualizaciones en README.md
- Sección "Novedades" ahora corresponde a v0.6.0.
- One-liner de instalación con comentarios y opciones alternativas (raw GitHub).
- Badges de PyPI y versión actualizados.

#### index.html
- Versión actualizada a 0.6.0 en los badges de releases.

### ⚠️ Notas para usuarios
- Si tenías una configuración guardada en `~/.snapcontext/config.json` con datos corruptos, se mostrará un aviso al iniciar SnapContext. Esto es normal; el programa continuará funcionando con un nuevo perfil vacío si lo deseas.

---

## [0.5.0] - 2026-xx-xx (primera versión pública)

### Características principales
- **Validación de carpeta de proyecto**: comprueba al iniciar que existe una carpeta típica (`lib/`, `src/`, `supabase/`, `app/`, `packages/`, `backend/`) y avisa si no (sale con código 1).
- **Menú interactivo de proveedor** (`questionary` con flechas y Enter): elige Gemini, Ollama, DeepSeek o Groq sin escribir `--provider`.
- **Configuración persistente**: guarda tu proveedor/modelo favorito en `~/.snapcontext/config.json` y lo reutiliza en las siguientes ejecuciones.
- **Auto-detección de modelos de Ollama**: con `ollama list`, muestra los modelos locales disponibles para elegir.
- **Asistente de configuración inicial (`--init`)**: guía el alta de claves API, proveedor y modelo favorito, y una prueba opcional de conexión.

---

## [0.4.x] - Beta (desarrollo)

### 0.4.0+ (versiones beta no publicadas)
- Desplegó la funcionalidad base con validación de repositorios y selección de archivos asistida por IA.
- Implementó soporte inicial para Gemini como proveedor principal.
- Iteraciones tempranas en el asistente interactivo antes del lanzamiento oficial v0.5.0.

---

## Historial de versiones

| Versión | Fecha          | Estado   |
|---------|----------------|----------|
| 0.6.0   | 2026-08-20     | 🟢 Lista para publicar |
| 0.5.0   | TBA            | 🔵 Publicado (primera versión) |
| 0.4.x   | Beta           | ⚪ No publicado |

---

*Generado automáticamente por SnapContext. Cada cambio se documentará aquí antes del siguiente release.*