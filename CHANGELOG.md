# Changelog de SnapContext

Todos los cambios notables para SnapContext se documentarán en este archivo.

El formato sigue las [directrices de Keep a Changelog](https://keepachangelog.com/es/1.0.0/).



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