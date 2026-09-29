# Publicación en PyPI — notas históricas

> **Autoridad actual:** el procedimiento vigente de release/publicación está
> documentado en **[`docs/RELEASE.md`](docs/RELEASE.md)**. Ese es el documento
> que hay que seguir.

Este archivo ya **no** describe el flujo operativo. Sus instrucciones
originales corresponden a la primera publicación manual del proyecto
(18–19/08/2026), anterior al flujo automatizado actual.

## Flujo vigente (resumen — detalle en `docs/RELEASE.md`)

- Fuente única de versión: el fichero `VERSION` de la raíz.
- Sincronización: `scripts/version_sync.py`.
- Validación de release: `scripts/verify_release.py` (gates I1–I12).
- Publicación: Git tag `vX.Y.Z` → GitHub Actions → PyPI
  **Trusted Publishing (OIDC)**, sin token manual.

## Disponibilidad del nombre y fallback (histórico)

Antes de la primera publicación, `snapcontext` constaba como disponible
(18/08/2026) y el fallback documentado era **`snapcontext-cli`**
(instalable como `pip install snapcontext-cli`); también se barajó
`snapcontext-tool`. Hoy el proyecto ya publica en PyPI como `snapcontext`,
así que **no** hay que renombrar nada.

Comprobación de disponibilidad de un nombre en PyPI
(404 = libre, 200 = ocupado), solo con valor histórico/de consulta:

```bash
curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/snapcontext/json
```

## Seguridad de tokens

**Nunca pegues un token o secreto directamente en texto plano dentro del
repositorio.** El flujo actual de PyPI usa Trusted Publishing/OIDC y
no requiere ningún `PYPI_TOKEN`. (Otros canales, p. ej. VS Code, usan el
secreto `VSCE_PAT` descrito en `docs/RELEASE.md`.)

## Test PyPI (histórico)

La primera publicación contempló subir primero a Test PyPI con
`python -m twine upload --repository testpypi dist/*`. Es **histórico**:
no lo usa ningún workflow actual y **no** forma parte del procedimiento
vigente documentado en `docs/RELEASE.md`.
