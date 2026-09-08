# Robustez del Graph RAG y LSP (Fase 16 — degradación elegante)

SnapContext es **tolerante a fallos**: si el servidor LSP falla o aún no está
terminado el índice del Graph RAG, degrada automáticamente a una búsqueda más
ligera sin interrumpir el flujo ni mostrar un traceback.

## LSP: degradación elegante

Si el servidor LSP no está presente, no responde o lanza una excepción,
SnapContext:

1. Espera como mucho **2 segundos** (timeout global `TIMEOUT_DEGRADACION` /
   `TIMEOUT_LSP_DEGRADACION`) por la respuesta.
2. Si el LSP falla o agota el tiempo, cae automáticamente a:
   - **Búsqueda por expresiones regulares (regex)** sobre los archivos del
     proyecto (rápida, sin dependencias).
   - **Embeddings sintácticos ligeros** (AST de Python, y `tree-sitter` si está
     instalado) para extraer funciones/clases.
3. Muestra **una sola vez** por archivo el aviso:
   `⚠ LSP no disponible, usando búsqueda por regex.`

Nunca se propaga una excepción al usuario: los fallbacks devuelven el mismo
formato de resultados (lista de coincidencias con ruta y línea).

## Graph RAG: indexación en segundo plano

Para que el CLI arranque **instantáneamente** (incluso con la primera consulta),
la indexación del grafo no bloquea:

- `cargar_grafo(directorio)` devuelve al instante:
  1. el grafo ya indexado (en curso o terminado), o
  2. el cache válido (carga rápida y síncrona), o
  3. un grafo **parcial/vacío** mientras un **hilo demonio** construye el grafo.
- Mientras no termina, `grafo["indexado"] == False` y el sistema usa el modo
  degradado (regex), mostrando el aviso:
  `ℹ Indexando grafo en segundo plano (puedes seguir usando SnapContext).`
- El grafo se lee siempre bajo candado y se sustituye por referencia completa
  al terminar, sin condiciones de carrera.
- El arranque del CLI lanza la indexación en background únicamente cuando el
  Graph RAG está activo (`--graph-rag` o `SNAPCONTEXT_GRAPH_RAG=1`) y fuera de
  un test runner. Se puede desactivar con `SNAPCONTEXT_INDEX_BG=0`.

### API

| Función | Descripción |
|---------|-------------|
| `cargar_grafo(directorio)` | Devuelve el mejor grafo disponible sin bloquear. |
| `indexar_en_background(directorio)` | Lanza un hilo demonio que popula el grafo. |
| `indexacion_en_curso(directorio)` | `True` si aún se está indexando. |
| `grafo_indexable(directorio)` | El gestor (`GrafoIndexable`) del proyecto o `None`. |

## Garantías

- **No bloqueo**: el CLI arranca y responde consultas sin esperar al LSP ni al
  índice del grafo.
- **Avisos, no tracebacks**: cualquier fallo interno se convierte en un mensaje
  informativo y el flujo continúa.
- **Fallbacks rápidos**: la búsqueda por regex está acotada en profundidad y en
  número de archivos (máx. 1500), y no cruza límites de proyecto/home.