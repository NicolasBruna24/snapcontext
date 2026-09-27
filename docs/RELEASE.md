# Guía de publicación (Fase 19)

Proceso completo para publicar SnapContext en los tres canales oficiales.

## 1. Preparación (cada release)

> **Procedimiento vigente desde B9.63.** Antes de B9.63 la versión se subía a mano
> en varios sitios; esa descripción ya no aplica.

1. **Edita `VERSION` en la raíz.** Es la **única fuente de verdad**: el resto de
   superficies se deriva de ella.
2. Sincroniza las superficies derivadas:
   ```bash
   python3 scripts/version_sync.py sync
   python3 scripts/version_sync.py check    # debe salir con código 0
   ```
   `sync` reescribe `vscode/package.json`, `vscode/package-lock.json`,
   `jetbrains/gradle.properties`, `jetbrains/build.gradle.kts` y
   `jetbrains/src/main/resources/META-INF/plugin.xml`. `check` es de solo lectura
   y es el gate que ejecuta CI.
   - `pyproject.toml` **no** lleva versión literal: usa
     `dynamic = ["version"]` con `version = {file = ["VERSION"]}`.
   - `snapcontext.VERSION` y la versión de FastAPI se derivan de `VERSION`.
3. Añade la entrada correspondiente en `CHANGELOG.md`.
4. Verifica el artefacto:
   ```bash
   python3 -m build
   python3 scripts/verify_release.py --tag vX.Y.Z
   python3 -m pytest -q
   ```
5. Commit de release, y **solo después** el tag (ver §2).

## 2. Publicar (automático con el tag)

```bash
git add -A && git commit -m "chore: bump version to X.Y.Z"
git push origin main
git tag vX.Y.Z
git push origin vX.Y.Z
```

Con el tag, GitHub Actions ejecuta tres workflows:

| Workflow | Canal | Requisito previo |
|---|---|---|
| `python-package.yml` | **PyPI** (trusted publishing, OIDC) | tener el "publisher" `NicolasBruna24/snapcontext` + environment `pypi` dado de alta en pypi.org (solo la primera vez) |
| `vsce-publish.yml` | **VS Code Marketplace** | secreto `VSCE_PAT` (PAT con scope Marketplace:Manage) |
| `pages-deploy.yml` | **Landing (GitHub Pages)** | Pages habilitado en Settings → Pages → Source: GitHub Actions |

El workflow de PyPI siempre publica. El de VS Code empaqueta el `.vsix` y lo
sube como artifact siempre; solo publica al Marketplace si `VSCE_PAT` existe.

## 3. Publicación manual de la extensión VS Code (sin secreto)

```bash
cd vscode
npm ci && npm run compile
npx @vscode/vsce package -o snapcontext-ai.vsix
npx @vscode/vsce publish -p TU_VSCE_PAT   # o sube el .vsix desde la web
```

## 4. Publicación manual del plugin JetBrains

1. Genera un token gratuito en <https://plugins.jetbrains.com/docs/marketplace/api-requests.html>
   (Marketplace → Profile → My Tokens).
2. Proporciona el token. El plugin Gradle de IntelliJ (1.x) lo lee de:
   - **`jetbrains/gradle.properties`**: `publishToken=...` y `publishChannel=default`
   - **variables de entorno**: `PUBLISH_TOKEN` y `PUBLISH_CHANNEL`

   > No existe una variable `JETBRAINS_TOKEN`. La documentación anterior de esta
   > guía la mencionaba por error; el plugin no la lee.

   La primera publicación de un plugin en JetBrains Marketplace debe hacerse
   **manualmente** desde la web; Gradle solo publica versiones posteriores.
3. Compila y publica:

```bash
cd jetbrains
./gradlew buildPlugin     # → build/distributions/*.zip
./gradlew publishPlugin   # sube al canal indicado (por defecto "default")
```

Requiere JDK 17+ (`kotlin { jvmToolchain(17) }`). La primera ejecución descarga
el SDK de IntelliJ. **No hay pipeline de CI para este canal**: la publicación es
siempre manual.

> El canal JetBrains **no está automatizado**. Su versión sí está sincronizada con
> `VERSION` (`build.gradle.kts`, `gradle.properties:pluginVersion`, `plugin.xml`),
> y `verify_release.py` lo comprueba (I6), pero la subida al Marketplace es un
> paso manual que hay que ejecutar explícitamente.

## 5. Verificación post-release

- PyPI: `pip install snapcontext==X.Y.Z && snapcontext --version`
- VS Code: <https://marketplace.visualstudio.com/items?itemName= NicolasBrunaFuentealbaIsaias.snapcontext-ai>
- Landing: <https://nicolasbruna24.github.io/snapcontext/>

## 6. Notas

- **No crear tags hasta confirmar los requisitos del canal** (p. ej. sin
  `VSCE_PAT` el job empaqueta pero no publica).
- Si `snapcontext` estuviera ocupado en PyPI, el fallback documentado es
  `snapcontext-cli` (ver `PUBLISHING.md`).