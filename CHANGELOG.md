# Changelog de SnapContext

Todos los cambios notables para SnapContext se documentarán en este archivo.

El formato sigue las [directrices de Keep a Changelog](https://keepachangelog.com/es/1.0.0/).

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