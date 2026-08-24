# SnapContext v2.0.0 — Notas de Lanzamiento 🚀

¡Llegó la versión **2.0.0** de SnapContext! Esta versión marca un hito importante: **SnapContext alcanza el estado de producción/estable**, integrando todas las capacidades desarrolladas (planificación autónoma, memoria persistente, MCP, extensiones para VS Code y JetBrains, interfaz web interactiva) y presentando la primera fase de nuestro **Editor Integrado Propio**.

---

## Novedades Principales

### 1. 🛠️ Editor Integrado Propio (Fase 1 — Sobrescritura de Archivos)
SnapContext ahora incluye un editor nativo para aplicar modificaciones en tu repositorio:
- **Flag `--editor propio`**: Permite trabajar directamente sin requerir que Aider esté instalado en el entorno.
- **Backups Automáticos**: Antes de modificar cualquier archivo existente, SnapContext guarda una copia de seguridad en `~/.snapcontext/backups/`.
- **Compatibilidad Garantizada**: `--editor aider` sigue siendo la opción predeterminada.

```bash
# Ejecutar tarea con el editor propio integrado
snapcontext "crear función de autenticación" --editor propio

# Planificador en modo autónomo con editor propio
snapcontext plan "implementar endpoint de pagos" --auto --editor propio
```

### 2. ⚡ Rendimiento Optimizado (Indexación Paralela)
- **ThreadPoolExecutor**: La lectura y el cálculo de hashes durante la indexación semántica del proyecto ahora se ejecutan en paralelo con múltiples hilos.
- **Escaneo Inteligente**: Exclusión refinada de directorios pesados (`node_modules`, `.git`, `.dart_tool`, `build`, `dist`, `target`).

### 3. 🌐 Arquitectura Unificada y Multi-Entorno
- **CLI & REPL**: `snapcontext --chat`, `snapcontext --plan`, `snapcontext fix`, `snapcontext review`.
- **Extensiones Oficiales**:
  - VS Code (`vscode/`) — Versión 2.0.0
  - JetBrains IntelliJ IDEA / PyCharm (`jetbrains/`) — Versión 2.0.0
- **Interfaz Web (FastAPI + WebSockets)**: `snapcontext --web` con editor Monaco y visualización de dependencias en tiempo real.

---

## 🧪 Pruebas y Calidad
- Más de **270 pruebas unitarias** pasando exitosamente.
- Validación de rutas seguras y blindaje contra *path traversal*.
- Clasificación en PyPI: **Production/Stable**.

---

## 📦 Instalación y Actualización

```bash
pip install -U snapcontext
```
