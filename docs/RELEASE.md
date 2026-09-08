# Guía de publicación (Fase 19)

Proceso completo para publicar SnapContext en los tres canales oficiales.

## 1. Preparación (cada release)

1. Sube la versión en **los cuatro sitios** (deben coincidir):
   - `version` en `pyproject.toml`
   - `VERSION` en `snapcontext.py`
   - `version` en `vscode/package.json`
   - `version` en `jetbrains/build.gradle.kts` (+ `pluginVersion` en `gradle.properties`)
2. Añade la entrada correspondiente en `CHANGELOG.md`.
3. Verifica: `python -c "import snapcontext; print(snapcontext.VERSION)"`.

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

1. Genera un token gratuito en <https://plugins.jetbrains.com/docs/marketplace/api-requests.html>.
2. Colócalo en `jetbrains/gradle.properties` (`publishToken=...`) o exporta
   `JETBRAINS_TOKEN`.
3. Compila y publica:

```bash
cd jetbrains
./gradlew buildPlugin     # → build/distributions/*.zip
./gradlew publishPlugin   # sube al canal "default"
```

Requiere JDK 17+. La primera ejecución descarga el SDK de IntelliJ.

## 5. Verificación post-release

- PyPI: `pip install snapcontext==X.Y.Z && snapcontext --version`
- VS Code: <https://marketplace.visualstudio.com/items?itemName= NicolasBrunaFuentealbaIsaias.snapcontext-ai>
- Landing: <https://nicolasbruna24.github.io/snapcontext/>

## 6. Notas

- **No crear tags hasta confirmar los requisitos del canal** (p. ej. sin
  `VSCE_PAT` el job empaqueta pero no publica).
- Si `snapcontext` estuviera ocupado en PyPI, el fallback documentado es
  `snapcontext-cli` (ver `PUBLISHING.md`).