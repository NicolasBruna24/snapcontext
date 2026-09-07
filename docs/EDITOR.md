# Editor de SnapContext

Documentación del motor de edición de código de SnapContext (v6.35.0).

## Arquitectura

El editor de SnapContext soporta múltiples estrategias para modificar código,
ordenadas de más a menos fiable:

1. **Parche unificado (diff)** - Estrategia principal usando `git apply` o `patch`
2. **Reemplazo estructurado** (Fase 14) - Fallback cuando el parche falla
3. **Editor AST** - Modificación sintáctica para Python (via `tree_sitter`)
4. **Aider** - Fallback final usando Aider en el directorio

## Reemplazo Estructurado (v6.35.0)

### Función principal

```python
aplicar_reemplazo_estructurado(
    archivo: str,           # Ruta al archivo (relativa a directorio)
    bloque_original: str,   # Contenido a buscar
    bloque_nuevo: str,      # Contenido de reemplazo
    directorio: str = ".",  # Directorio base del proyecto
) -> str                   # Retorna el contenido modificado
```

### Estrategia de búsqueda

La función intenta encontrar el bloque original en 3 fases:

| Fase | Método | Cuándo se usa |
|------|--------|---------------|
| 1 | Búsqueda exacta | El bloque aparece textualmente en el archivo |
| 2 | Búsqueda difusa | Normalizando espacios en blanco y comentarios |
| 3 | Fuzzy (difflib) | Cuando la similitud supera el umbral (0.85) |

### Seguridad (M1 - Path Traversal)

La función incluye protección contra path traversal:
- Resuelve la ruta absoluta del archivo
- Verifica que esté dentro del directorio del proyecto
- Lanza `ValueError` si se intenta acceder fuera del proyecto

### Ejemplo de uso

```python
import snapcontext as sc

# Reemplazo simple
resultado = sc.aplicar_reemplazo_estructurado(
    archivo="src/main.py",
    bloque_original="def foo():\n    return 1",
    bloque_nuevo="def foo():\n    return 100",
    directorio="/path/al/proyecto",
)

# Escribir resultado
Path("src/main.py").write_text(resultado)
```

## Limitaciones

### Reemplazo estructurado
- **No maneja archivos binarios**: Solo texto UTF-8
- **Rendimiento**: Archivos > 10,000 líneas pueden ser lentos en búsqueda fuzzy
- **Precisión**: El modo fuzzy puede encontrar falsos positivos en archivos con
  mucho código repetitivo (ej. muchos imports idénticos)
- **Sin contexto semántico**: No entiende la estructura del código, solo busca
  texto

### Parche unificado
- Requiere `git` o `patch` instalados
- Puede fallar con conflictos de merge
- No maneja bien cambios en archivos con encoding no-UTF8

### Editor AST
- Solo para Python (via tree_sitter)
- Puede romper formato original (espaciado, comentarios)
- No disponible si tree_sitter no está instalado

## Mejores prácticas

1. **Usar el parche unificado siempre que sea posible** - Es más preciso y
   maneja conflictos mejor
2. **Usar reemplazo estructurado como fallback** - Cuando `git apply` falla,
   intentar reemplazo estructurado antes de recurrir a Aider
3. **Verificar resultado después de editar** - Especialmente para Python,
   usar `ast.parse()` para verificar validez sintáctica
4. **Hacer backup antes de editar archivos grandes** - El sistema de backups
   de SnapContext guarda copias antes de cada modificación

## Tests

Los tests del editor están en:
- `tests/test_editor_reemplazo.py` - Tests unitarios del reemplazo estructurado
- `tests/test_editor_estres.py` - Tests de estrés con archivos grandes

Ejecutar:
```bash
pytest tests/test_editor_reemplazo.py tests/test_editor_estres.py -v
```

