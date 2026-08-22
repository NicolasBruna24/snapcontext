# SnapContext
<p align="center">
  <pre>
┌──────────────────────────────────────────────────────────┐
  │                                                          │
  │                                                          │
  │    ███████╗███╗   ██╗ █████╗ ██████╗  ██████╗ ██████╗   │
  │    ██╔════╝████╗  ██║██╔══██╗██╔══██╗██╔════╝██╔════╝   │
  │    ███████╗██╔██╗ ██║███████║██████╔╝██║     ██║        │
  │    ╚════██║██║╚██╗██║██╔═══╝ ██║     ██║     ██║        │
  │    ███████║██║ ╚████║██║  ██║██║     ╚██████╗╚██████╗   │
  │    ╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝      ╚═════╝ ╚═════╝   │
  │                                                          │
  │    » Selección inteligente de archivos                  │
  │    » Soporte: Gemini · Ollama · DeepSeek · Groq        │
  │    » v0.8.0                                             │
  │                                                          │
  └──────────────────────────────────────────────────────────┘
  </pre>
</p>

[![PyPI version](https://badge.fury.io/py/snapcontext.svg)](https://pypi.org/project/snapcontext/) (disponible en desarrollo; versión pública: **v0.6.0**)

[![Release](https://img.shields.io/badge/release-v0.6.0-blue.svg)](https://github.com/NicolasBruna24/snapcontext/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

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
code --install-extension vscode/snapcontext-vscode-0.16.0.vsix
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
| `grep` | Buscar patrón en el código (rg/grep/findstr) | no |
| `read_file` | Leer archivo completo o rango de líneas | no |
| `list_files` | Listar archivos con filtro de extensión | no |
| `ast` | Extraer imports/clases/funciones de un `.py` | no |
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
   `{"descripcion": "...", "accion": "editar|ejecutar|consultar", "archivos": [...], "comando": "..."}`
2. Los pasos se muestran numerados y se pide confirmación.
3. Cada paso se ejecuta secuencialmente: **editar** usa el pipeline completo
   (`_planificar` + `_bucle_test`), **ejecutar** lanza comandos shell,
   **consultar** pregunta al proveedor.
4. Tras cada paso eliges: continuar / reintentar / saltar / abortar. Al final
   hay un resumen que se guarda en `historial.json`.

Opciones git:

```bash
snapcontext --plan "migrar a null-safety" --branch fix/null-safety   # rama nueva
snapcontext --plan "..." --no-git-commit                             # sin commits por paso
snapcontext --plan "..."                                             # commits 'paso: ...' activados
```

## ✨ Novedades v0.10.0

- **Claude (Anthropic) como proveedor de IA**: `snapcontext "..." --provider anthropic`
  (requiere `ANTHROPIC_API_KEY` y `pip install snapcontext[anthropic]`). Modelo por
  defecto: `claude-3-5-sonnet-20241022`.
- **Modo chat interactivo**: `snapcontext --chat` abre un REPL con los comandos
  `/salir`, `/archivos`, `/limpiar`, `/seleccion <consulta>`,
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
  `/save` (guarda la sesión en historial.json) además de los comandos previos.
  Los comandos largos (`/run`, `/explore`, `/fix`, `/review`, `/server`) se
  ejecutan en un hilo separado para no bloquear el chat.

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

### Validación de carpeta de proyecto

Al iniciar, SnapContext comprueba que el directorio contiene carpetas típicas de
proyecto (`lib/`, `src/`, `supabase/`, `app/`, `packages/`, `backend/`). Si no
encuentra ninguna, avisa y sale (código 1):

```
⚠️ No parece que estés en una carpeta de proyecto. SnapContext espera carpetas como
lib/, src/, supabase/, etc. Puedes especificar una carpeta con --directorio <ruta> o
navega a la raíz de tu proyecto y vuelve a intentarlo.
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
| `--local` | off | Selección sin IA (modo offline / pruebas) |
| `--vista-previa` | off | Mostrar la selección y salir |
| `--experto` (alias `--expert`) | off | Revisar/añadir/eliminar archivos antes de Aider |
| `--aider-opciones` | `""` | Flags extra para Aider |
| `--test-loop` | off | Bucle agéntico Aider → pruebas → reparar |
| `--server-loop` | off | Bucle agéntico con `flutter run`, modo automático (reintenta y pregunta s/n) |
| `--manual-loop` | off | Bucle agéntico con `flutter run`, modo manual (usuario decide cada paso) |
| `--max-intentos` | `3` | Intentos máximos de `--server-loop` |
| `--dispositivo` | `web-server` | Plataforma/dispositivo de `flutter run` |
| `--url-defecto` | `http://localhost:5000` | URL para abrir el navegador si Flutter no reporta una |
| `--comando-test` | `"flutter test"` | Comando del bucle de pruebas |
| `--max-iteraciones` | `3` | Iteraciones máximas del bucle |

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
## 🙌 Agradecimientos

- **Aider** por su excelente motor de edición de código.
- **Google Gemini** por su generoso plan gratuito.
- **Ollama**, **DeepSeek** y **Groq** por sus modelos open-source.
- La comunidad open-source por las herramientas que hacen posible este proyecto.

## Licencia

MIT. Open-source y libre de usarlo, estudiarlo y mejorarlo.
