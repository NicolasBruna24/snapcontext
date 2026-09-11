# Model Context Protocol (MCP) — Cliente estándar

SnapContext implementa un **cliente MCP estándar** que le permite conectarse a
servidores MCP externos (como los que usan Claude Code, Cline o OpenCode) y
exponer sus herramientas al agente ReAct como si fueran nativas.

## ¿Qué es MCP?

El [Model Context Protocol](https://modelcontextprotocol.io/) es un estándar
abierto que define cómo un cliente (IDE, agente, CLI) se comunica con
servidores de herramientas mediante JSON-RPC 2.0 sobre stdin/stdout.

SnapContext actúa como **cliente MCP**: lanza los servidores configurados,
negocia el protocolo y traduce las llamadas del agente en peticiones
`tools/list` y `tools/call`.

## Versión del protocolo

Se soporta la versión **`2024-11-05`** del protocolo MCP. El código está
preparado para actualizarse cuando el estándar evolucione (la versión se
define en la constante `PROTOCOLO_MCP` de `mcp_client.py`).

## Configuración

Los servidores MCP se configuran en un fichero `mcp_servers.json`:

- **Raíz del proyecto**: `./mcp_servers.json` (tiene precedencia).
- **Global**: `~/.snapcontext/mcp_servers.json`.

### Formato

```json
{
  "servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/ruta/al/proyecto"],
      "env": {},
      "enabled": true
    },
    "git": {
      "command": "uvx",
      "args": ["mcp-server-git", "--repository", "/ruta/al/proyecto"],
      "env": {},
      "enabled": true
    }
  }
}
```

Cada servidor requiere:

| Campo       | Tipo   | Descripción                                          |
|-------------|--------|------------------------------------------------------|
| `command`   | string | Comando ejecutable (en PATH o ruta absoluta).        |
| `args`      | list   | Argumentos que se pasan al comando.                  |
| `env`       | dict   | Variables de entorno adicionales (opcional).         |
| `enabled`   | bool   | `false` desactiva el servidor sin borrarlo.          |

> **Seguridad**: los comandos se ejecutan con `subprocess.Popen` usando lista
> de argumentos (`shell=False`). Nunca se interpola en un string de shell.

## Servidores populares

Algunos servidores MCP conocidos que puedes configurar:

| Servidor    | Comando                                                |
|-------------|--------------------------------------------------------|
| Filesystem  | `npx -y @modelcontextprotocol/server-filesystem /ruta` |
| Git         | `uvx mcp-server-git --repository /ruta`                |
| GitHub      | `npx -y @modelcontextprotocol/server-github`           |
| Postgres    | `npx -y @modelcontextprotocol/server-postgres URL`     |
| SQLite      | `uvx mcp-server-sqlite --db-path /ruta/db.sqlite`      |
| Puppeteer   | `npx -y @modelcontextprotocol/server-puppeteer`        |

## Comandos CLI

```
snapcontext mcp list              # Lista servidores y herramientas
snapcontext mcp add <nombre> <comando> [args...]
                                  # Añade un servidor a la configuración
snapcontext mcp remove <nombre>   # Elimina un servidor
snapcontext mcp help              # Muestra ayuda
```

### Ejemplo: añadir el servidor de filesystem

```bash
snapcontext mcp add filesystem \
  npx -y @modelcontextprotocol/server-filesystem /home/user/proyecto
```

Tras añadirlo, `snapcontext mcp list` muestra las herramientas expuestas
(`read_file`, `write_file`, `list_directory`, ...) y el agente ReAct puede
usarlas automáticamente con el prefijo `mcp_filesystem_`.

## Integración con el agente

Las herramientas de servidores MCP externos se registran en el catálogo de
herramientas con el prefijo **`mcp_<servidor>_<herramienta>`** para evitar
colisiones con las nativas (`grep`, `read_file`, `git_status`, ...).

El dispatcher `_ejecutar_herramienta_mcp` reconoce automáticamente las
herramientas externas y las enrutan al cliente MCP correspondiente.

### Degradación elegante

Si un servidor MCP no arranca (comando inexistente, handshake fallido,
timeout), SnapContext muestra un aviso y continúa: el servidor queda marcado
como caído para no reintentarlo en la misma sesión, y el resto del sistema
sigue funcionando sin interrupción.

## Arquitectura

```
┌──────────────────────────────────────────────────────────┐
│  Agente ReAct                                            │
│  "lee el archivo src/main.py"                            │
└──────────────┬───────────────────────────────────────────┘
               │ _ejecutar_herramienta_mcp("mcp_fs_read_file", ...)
               ▼
┌──────────────────────────────────────────────────────────┐
│  mcp_tools.py — dispatcher                              │
│  detecta prefijo mcp_ → enruta al cliente                │
└──────────────┬───────────────────────────────────────────┘
               │ cliente.call_tool("fs", "read_file", {...})
               ▼
┌──────────────────────────────────────────────────────────┐
│  mcp_client.py — MCPClient                              │
│  JSON-RPC 2.0 sobre stdin/stdout                        │
│  initialize → tools/list → tools/call                   │
└──────────────┬───────────────────────────────────────────┘
               │ subprocess.Popen (shell=False)
               ▼
┌──────────────────────────────────────────────────────────┐
│  Servidor MCP externo (filesystem, git, github, ...)     │
└──────────────────────────────────────────────────────────┘
```

## Tests

Los tests de `tests/test_mcp_client.py` verifican el handshake JSON-RPC,
`list_tools`, `call_tool` y la degradación elegante usando mocks de
`subprocess.Popen` (no requieren servidores externos instalados).

Para ejecutar solo los tests de MCP:

```bash
pytest tests/test_mcp_client.py -v
```

Incluye un test de integración opcional (`test_integracion_server_filesystem_real`)
que se marca `skip` por defecto porque requiere `npx` y descargar el paquete
`@modelcontextprotocol/server-filesystem`. Desmárcalo para probar con un
servidor real.
