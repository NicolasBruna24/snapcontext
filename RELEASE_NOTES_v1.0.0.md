# SnapContext v1.0.0 — Notas de lanzamiento 🎉

**Fecha:** 2026-08-22 · **Tipo:** primera versión estable

SnapContext 1.0.0 marca la madurez del proyecto: un asistente de IA con
contexto automático que detecta tu stack, selecciona los archivos relevantes,
edita con Aider, planifica tareas complejas y aprende de tu proyecto.

## ✨ Lo más destacado

- **5 proveedores de IA**: Gemini, Claude (Anthropic), Ollama (local),
  DeepSeek y Groq.
- **Chat interactivo** (`--chat`) con herramientas MCP: `grep`, `read_file`,
  `list_files`, `ast`, `git_status`, `git_diff`, `execute_command` y soporte
  para herramientas propias (`~/.snapcontext/mcp_tools.json`).
- **Planificador** (`--plan`): divide tareas en pasos
  editar/ejecutar/consultar, con menú continuar/reintentar/saltar.
- **Modo autónomo** (`--auto`): ejecución sin supervisión con reintentos
  automáticos (hasta 3 por paso).
- **Permisos granulares** (`--confirmar/--no-confirmar`): opciones
  s/n/todos/nunca persistidas en `~/.snapcontext/permisos.json`.
- **Memoria de proyecto**: `CLAUDE.md` (o `SNAPCONTEXT.md`) cargada
  automáticamente; generación con `--init-claude`.
- **Historial persistente** de tareas (`--historial`).
- **Interfaces**: CLI con alias (`fix`, `review`, `server`, `interactive`),
  web en tiempo real (`--web`), demo sin dependencias (`--demo`) y **extensión
  para VS Code** (webview + canal de salida "SnapContext Output").

## 📦 Instalación

```bash
pip install snapcontext            # PyPI
# o desde el repo:
curl -fsSL https://raw.githubusercontent.com/NicolasBruna24/snapcontext/main/install.sh | sh
```

## 🔄 Migración

Retrocompatible con 0.x: `config.json`, `permisos.json`, `historial.json` y
`mcp_tools.json` se siguen usando sin cambios.

## 🙏 Gracias

Gracias a todos los que probaron las versiones 0.x y reportaron problemas.
