# Changelog de SnapContext

Todos los cambios notables para SnapContext se documentarán en este archivo.

El formato sigue las [directrices de Keep a Changelog](https://keepachangelog.com/es/1.0.0/).

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