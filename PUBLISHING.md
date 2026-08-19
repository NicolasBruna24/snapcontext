# Publicación en PyPI

Guía paso a paso para subir **SnapContext** a [PyPI](https://pypi.org).

## 0. Disponibilidad del nombre

| Nombre | Estado (18/08/2026) |
|---|---|
| `snapcontext` | **DISPONIBLE** ✅ |
| `snapcontext-cli` | DISPONIBLE (alternativa) |
| `snapcontext-tool` | DISPONIBLE (alternativa) |

> Antes de publicar conviene comprobarlo de nuevo:
> `curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/snapcontext/json`
> (404 = libre, 200 = ocupado). Si `snapcontext` estuviera ocupado, cambia
> `name` en `pyproject.toml` por `snapcontext-cli` (y así lo instalarías con
> `pip install snapcontext-cli`).

## 1. Requisitos previos
1. **Cuenta en PyPI**: créala en <https://pypi.org/account/register/>.
   (Opcional, recomendado) cuenta en **Test PyPI**: <https://test.pypi.org/>.
2. **Edita `pyproject.toml`** con tus datos reales (es obligatorio antes de
   publicar una versión pública):
   - `authors`: tu nombre y correo.
   - `[project.urls]`: apunta a tu repositorio real.
3. Copia este proyecto a un repositorio Git (recomendado para el `Homepage`).

## 2. Instalar las herramientas de construcción
```bash
# Con el entorno virtual activado:
pip install build twine
# (también quedan como extra de desarrollo: pip install -e ".[dev]")
```

## 3. Comprobar que el módulo se importa y la versión es correcta
```bash
python -c "import snapcontext; print(snapcontext.VERSION)"   # → 0.4.0
python -m snapcontext --version                                # → snapcontext 0.4.0
```
> El número de versión está en dos sitios que deben coincidir:
> `version` en `pyproject.toml` y `VERSION` en `snapcontext.py`.

## 4. Construir el paquete
```bash
python -m build
```
Genera `dist/snapcontext-0.4.0.tar.gz` (sdist) y
`dist/snapcontext-0.4.0-py3-none-any.whl` (wheel).

`dist/`, `build/` y `*.egg-info/` ya están en `.gitignore`.

## 5. Validar los metadatos
```bash
python -m twine check dist/*
```
Debe mostrar `PASSED` para cada artefacto (comprueba README, licencia,
descripción y campos obligatorios).

## 6. Subir a PyPI
1. Crea una **API token** en <https://pypi.org/manage/account/token/> con
   ámbito "Entire account" (o de proyecto) y copia `pypi-XXXX...`.
2. Sube:
```bash
python -m twine upload dist/*
```
   Te pedirá el usuario (`__token__`) y la contraseña (el token `pypi-...`).
   **Nunca pegues el token en texto plano en un repositorio público.**
3. Si usas **Test PyPI** primero:
```bash
python -m twine upload --repository testpypi dist/*
```

## 7. Verificar la instalación desde PyPI
En un entorno limpio (o una venv nueva):
```bash
pip install snapcontext
snapcontext --version
python -m snapcontext "revisar login" --local --vista-previa
```

## 8. Publicar versiones nuevas (cada mejora)
1. Sube la versión en `pyproject.toml` y en `snapcontext.py` (p. ej. `0.5.0`).
2. Repite los pasos 4 → 6 (reconstruye; borra `dist/` si hubiera versiones viejas).

## Notas
- El `entry point` (`[project.scripts] snapcontext = "snapcontext:main"`) ya
  está definido: al instalar, crea el comando `snapcontext` (y `main()` ya
  devuelve un código de salida, así que el wrapper funciona bien).
- Dependencias mínimas: `google-generativeai>=0.8.3` y `openai>=1.30.0`
  (Aider se instala aparte, como extra `dev`, porque arrastra más paquetes).
