#!/usr/bin/env python3
"""Gateway de integración con GitHub para SnapContext (v6.8.0).

Permite conectar SnapContext con repositorios de GitHub mediante webhooks:
- Validación de firmas HMAC SHA-256 (seguridad contra peticiones falsificadas).
- Parseo de eventos: `pull_request`, `issues`, `push`, `issue_comment`.
- Procesamiento y encolado automático de tareas asíncronas en `task_queue.py`.
- Integración opcional con la API REST de GitHub (obtener diff de PRs, comentar en PRs y registrar webhooks).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

API_GITHUB = "https://api.github.com"
CONFIG_DIR = Path.home() / ".snapcontext"
CONFIG_PATH = CONFIG_DIR / "config.json"


# ---------------------------------------------------------------------------
# Configuración y Credenciales
# ---------------------------------------------------------------------------
def _leer_seccion_github() -> dict[str, Any]:
    try:
        if CONFIG_PATH.is_file():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return cfg.get("github") or {}
    except Exception:
        pass
    return {}


def obtener_webhook_secreto() -> str | None:
    """Secreto del webhook: GITHUB_WEBHOOK_SECRET > config.json > None."""
    secreto = (os.environ.get("GITHUB_WEBHOOK_SECRET") or "").strip()
    if secreto:
        return secreto
    return (_leer_seccion_github().get("webhook_secret") or "").strip() or None


def obtener_github_token() -> str | None:
    """Token personal/App de GitHub: GITHUB_TOKEN > config.json > None."""
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        return token
    return (_leer_seccion_github().get("token") or "").strip() or None


def obtener_webhook_url() -> str | None:
    """URL pública del webhook: GITHUB_WEBHOOK_URL / SNAPCONTEXT_WEBHOOK_URL > config.json."""
    url = (
        os.environ.get("GITHUB_WEBHOOK_URL") or os.environ.get("SNAPCONTEXT_WEBHOOK_URL") or ""
    ).strip()
    if url:
        return url
    return (_leer_seccion_github().get("webhook_url") or "").strip() or None


def guardar_configuracion_github(
    webhook_secret: str | None = None,
    token: str | None = None,
    webhook_url: str | None = None,
) -> dict[str, Any]:
    """Guarda la configuración de GitHub en ~/.snapcontext/config.json."""
    try:
        if CONFIG_PATH.is_file():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        else:
            cfg = {}
    except Exception:
        cfg = {}
    seccion = cfg.setdefault("github", {})
    if webhook_secret is not None:
        seccion["webhook_secret"] = webhook_secret.strip()
    if token is not None:
        seccion["token"] = token.strip()
    if webhook_url is not None:
        seccion["webhook_url"] = webhook_url.strip()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return dict(seccion)


# ---------------------------------------------------------------------------
# Validación de Firma HMAC
# ---------------------------------------------------------------------------
def validar_firma(
    payload: bytes | str, firma_cabecera: str | None, secreto: str | None = None
) -> bool:
    """Verifica que la firma HMAC enviada por GitHub coincida con el payload.

    GitHub envía firmas en la cabecera `X-Hub-Signature-256` (formato `sha256=HEX`)
    o `X-Hub-Signature` (formato `sha1=HEX`).
    """
    if secreto is None:
        secreto = obtener_webhook_secreto()
    if not secreto:
        # Si no hay secreto configurado, se rechaza la verificación por seguridad.
        return False
    if not firma_cabecera or not isinstance(firma_cabecera, str):
        return False

    cuerpo = payload.encode("utf-8") if isinstance(payload, str) else payload

    partes = firma_cabecera.split("=", 1)
    if len(partes) != 2:
        return False
    algoritmo, firma_hex = partes[0].lower(), partes[1].strip()

    if algoritmo == "sha256":
        hash_fn = hashlib.sha256
    elif algoritmo == "sha1":
        hash_fn = hashlib.sha1
    else:
        return False

    mac = hmac.new(secreto.encode("utf-8"), msg=cuerpo, digestmod=hash_fn)
    firma_calculada = mac.hexdigest()
    return hmac.compare_digest(firma_calculada, firma_hex)


# ---------------------------------------------------------------------------
# B9.61-D (D-01) — Validación estricta de referencias Git
#
# `rama` llega de un webhook (input NO confiable). Antes se interpolaba en
# `f"git checkout {rama} && pytest"` (shell=True). Ahora se valida contra la
# gramática de `git check-ref-format` y, si no es válida, el evento se rechaza
# (`ok: False`). No se "sanea": un valor inválido es un DENY, no un silent fix.
# ---------------------------------------------------------------------------
_RE_REF_SIMPLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")

#: Caracteres con significado para un shell o para la ExpansionParameter de git.
#: Se rechazan por la vía del ref, nunca "limpiados" (deny, no silent fix).
_REF_CARACTERES_PROHIBIDOS = set(";&|$`\n\r\t\\'\"()<>*?[]{}!#%^~ ")


def referencia_git_valida(ref: object) -> bool:
    """``True`` solo si ``ref`` es una referencia Git válida y utilizable como
    argumento estructurado (nunca como fragmento de shell)."""
    if not isinstance(ref, str):
        return False
    valor = ref.strip()
    if not valor or len(valor) > 200:
        return False
    if valor.startswith("-") or valor in (".", "..", "@", "/", "//"):
        return False
    if valor.endswith(("/", ".lock", ".", "/.")):
        return False
    if ".." in valor or "@{" in valor:
        return False
    if any(c in _REF_CARACTERES_PROHIBIDOS for c in valor):
        return False
    if "//" in valor:
        return False
    return bool(_RE_REF_SIMPLE.match(valor))


def _tipo_tarea_webhook_permitido(tipo: str) -> bool:
    """B9.61-D (D-01/D-02): el webhook solo puede encolar tipos ejecutables.

    El contrato ratificado (B9.59 §5, B9.60 §10) limita la ejecución a
    `query` y `plan`. `tests` y `pr_review` **no se encolan**: en M1 su
    ejecución está DENY y mantenerlos encolados solo alimentaría una cola
    distribuida que no puede ejecutarse. No se inventa una excepción al
    contrato para conservar el comportamiento histórico.
    """
    return tipo in ("query", "plan")


# ---------------------------------------------------------------------------
# Parseo de Eventos de GitHub
# ---------------------------------------------------------------------------
def parsear_evento(
    payload: dict[str, Any] | str, tipo_evento: str = "pull_request"
) -> dict[str, Any]:
    """Extrae los datos clave de un payload de webhook de GitHub.

    Soporta eventos: `pull_request`, `issues`, `push`, `issue_comment`.
    """
    if isinstance(payload, str):
        try:
            datos = json.loads(payload)
        except Exception as exc:
            return {"ok": False, "error": f"JSON inválido: {exc}"}
    else:
        datos = dict(payload or {})

    evento = str(tipo_evento or "pull_request").lower()
    resultado: dict[str, Any] = {
        "ok": True,
        "tipo_evento": evento,
        "accion": datos.get("action", ""),
        "repositorio": (datos.get("repository") or {}).get("full_name", ""),
        "emisor": (datos.get("sender") or {}).get("login", ""),
    }

    if evento == "pull_request":
        pr = datos.get("pull_request") or {}
        resultado.update(
            {
                "numero": datos.get("number") or pr.get("number"),
                "titulo": pr.get("title", ""),
                "cuerpo": pr.get("body", ""),
                "estado": pr.get("state", ""),
                "rama_origen": (pr.get("head") or {}).get("ref", ""),
                "rama_destino": (pr.get("base") or {}).get("ref", ""),
                "head_sha": (pr.get("head") or {}).get("sha", ""),
                "diff_url": pr.get("diff_url", ""),
                "creador": (pr.get("user") or {}).get("login", ""),
                "mergeable": pr.get("mergeable"),
            }
        )
    elif evento == "issues":
        issue = datos.get("issue") or {}
        resultado.update(
            {
                "numero": issue.get("number"),
                "titulo": issue.get("title", ""),
                "cuerpo": issue.get("body", ""),
                "estado": issue.get("state", ""),
                "creador": (issue.get("user") or {}).get("login", ""),
                "etiquetas": [
                    t.get("name") for t in (issue.get("labels") or []) if isinstance(t, dict)
                ],
            }
        )
    elif evento == "push":
        # B9.61-D (D-01): `ref` es input no confiable. Se valida como referencia
        # Git; si es inválido el evento se rechaza entero (no se sanea).
        ref = str(datos.get("ref") or "")
        rama = ref.replace("refs/heads/", "", 1) if ref.startswith("refs/heads/") else ""
        if not referencia_git_valida(rama):
            return {
                "ok": False,
                "error": "referencia de rama invalida",
                "tipo_evento": evento,
            }
        head_commit = datos.get("head_commit") or {}
        resultado.update(
            {
                "ref": ref,
                "rama": rama,
                "head_sha": datos.get("after", ""),
                "mensaje_commit": head_commit.get("message", ""),
                "autor_commit": (head_commit.get("author") or {}).get("name", ""),
                "total_commits": len(datos.get("commits") or []),
                "modificados": head_commit.get("modified") or [],
                "agregados": head_commit.get("added") or [],
                "eliminados": head_commit.get("removed") or [],
            }
        )
    elif evento == "issue_comment":
        comentario = datos.get("comment") or {}
        issue = datos.get("issue") or {}
        resultado.update(
            {
                "numero": issue.get("number"),
                "es_pr": "pull_request" in issue,
                "cuerpo_comentario": comentario.get("body", ""),
                "autor_comentario": (comentario.get("user") or {}).get("login", ""),
            }
        )
    else:
        resultado["datos_crudos"] = datos

    return resultado


# ---------------------------------------------------------------------------
# Procesamiento de Eventos y Encolado de Tareas
# ---------------------------------------------------------------------------
def procesar_evento(
    evento_parseado: dict[str, Any],
    chat_id: str | None = None,
    canal: str | None = None,
    db_path: str | None = None,
) -> int | None:
    """Crea y encola una tarea en `task_queue` según el evento de GitHub.

    B9.61-D (D-01/D-02) — solo se encolan tipos **ejecutables** en M1:

    - Issue (opened/reopened) → tarea ``plan``.
    - ``issue_comment`` ``/snap``/``/fix``/``/review`` en un issue → ``plan``.
    - Pull Request → **sin tarea**: la revisión exige red + pipeline y su
      ejecución está DENY en M1 (B9.59 §5). El evento se autentica y registra.
    - Push → **sin tarea**: exigiría ``git checkout {rama} && pytest`` con
      ``shell=True`` sobre un ``ref`` remoto. La rama se valida igualmente
      (:func:`referencia_git_valida`) al parsear el evento.

    Devuelve ``None`` cuando el evento no genera tarea ejecutable.
    """
    if not evento_parseado.get("ok"):
        return None

    try:
        import task_queue as tq
    except ImportError:
        return None

    tipo_evento = evento_parseado.get("tipo_evento")
    accion = evento_parseado.get("accion", "")

    tarea_tipo = None
    datos_tarea = dict(evento_parseado)

    if tipo_evento == "pull_request":
        # B9.61-D (D-01/D-02): la revisión de PR requiere `obtener_pr_diff`
        # (salida de red) y `flujo_principal`; su ejecución está DENY en M1
        # (B9.59 §5). El evento se autentica y se registra, pero NO genera
        # tarea ejecutable. Requirirlo en M1 exigiría una decisión nueva.
        if accion in ("opened", "synchronize", "reopened"):
            return None
    elif tipo_evento == "issues":
        if accion in ("opened", "reopened"):
            tarea_tipo = "plan"
            datos_tarea["consulta"] = (
                f"Resolver Issue #{evento_parseado.get('numero')}: {evento_parseado.get('titulo')}\n{evento_parseado.get('cuerpo')}"
            )
    elif tipo_evento == "push":
        # B9.61-D (D-01): `tests` exigiría `git checkout {rama} && pytest` con
        # `shell=True`. No se genera tarea: el push solo se registra. La
        # referencia ya fue validada en `parsear_evento`.
        return None
    elif tipo_evento == "issue_comment":
        cuerpo = (evento_parseado.get("cuerpo_comentario") or "").strip()
        if cuerpo.startswith("/snap") or cuerpo.startswith("/fix") or cuerpo.startswith("/review"):
            if evento_parseado.get("es_pr"):
                return None  # comentario en PR → pr_review DENY en M1
            tarea_tipo = "plan"
            datos_tarea["consulta"] = cuerpo

    if tarea_tipo:
        # B9.61-D (D-01/D-02): frontera de tipos. El webhook nunca encola
        # `tests` ni `pr_review` (su ejecución está DENY en M1) ni tipos
        # desconocidos. Sin esto, un evento remoto depositaba trabajo no
        # ejecutable que además reintroducía la ruta a `git checkout`/red.
        if not _tipo_tarea_webhook_permitido(tarea_tipo):
            return None
        try:
            import task_queue as tq
        except ImportError:
            return None
        task_id = tq.encolar_tarea(
            tipo=tarea_tipo,
            datos=datos_tarea,
            chat_id=chat_id,
            canal=canal,
            db_path=db_path,
        )
        return task_id

    return None


# ---------------------------------------------------------------------------
# Operaciones con la API REST de GitHub
# ---------------------------------------------------------------------------
def obtener_pr_diff(repo: str, numero: int | str, token: str | None = None) -> str | None:
    """Obtiene el diff unificado de un Pull Request desde la API de GitHub."""
    tok = token or obtener_github_token()
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "User-Agent": "SnapContext-Agent/6.9.0",
    }
    if tok:
        headers["Authorization"] = f"token {tok}"

    url = f"{API_GITHUB}/repos/{repo}/pulls/{numero}"
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as cliente:
            resp = cliente.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.text
            return None
    except Exception:
        return None


def comentar_pr(repo: str, numero: int | str, mensaje: str, token: str | None = None) -> bool:
    """Publica un comentario en un Pull Request o Issue de GitHub."""
    tok = token or obtener_github_token()
    if not tok:
        return False

    headers = {
        "Authorization": f"token {tok}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "SnapContext-Agent/6.9.0",
    }
    url = f"{API_GITHUB}/repos/{repo}/issues/{numero}/comments"
    try:
        with httpx.Client(timeout=30.0) as cliente:
            resp = cliente.post(url, headers=headers, json={"body": mensaje})
            return resp.status_code in (200, 201)
    except Exception:
        return False


def configurar_webhook(
    url: str,
    secreto: str,
    repo: str | None = None,
    token: str | None = None,
    eventos: list[str] | None = None,
) -> tuple[bool, str]:
    """Registra el webhook de SnapContext en el repositorio de GitHub."""
    tok = token or obtener_github_token()
    if not tok:
        return False, "Falta el token de GitHub (GITHUB_TOKEN)."
    if not repo:
        return False, "Falta especificar el repositorio (ej: 'owner/repo')."
    if not url:
        return False, "Falta la URL pública del webhook."

    headers = {
        "Authorization": f"token {tok}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "SnapContext-Agent/6.9.0",
    }
    destino = url.rstrip("/")
    if not destino.endswith("/webhook/github"):
        destino = f"{destino}/webhook/github"

    datos_hook = {
        "name": "web",
        "active": True,
        "events": eventos or ["pull_request", "issues", "push", "issue_comment"],
        "config": {
            "url": destino,
            "content_type": "json",
            "secret": secreto,
            "insecure_ssl": "0",
        },
    }

    endpoint = f"{API_GITHUB}/repos/{repo}/hooks"
    try:
        with httpx.Client(timeout=30.0) as cliente:
            resp = cliente.post(endpoint, headers=headers, json=datos_hook)
            if resp.status_code == 201:
                return True, "Webhook configurado exitosamente en GitHub."
            return False, f"Error de GitHub ({resp.status_code}): {resp.text}"
    except Exception as exc:
        return False, f"Error de conexión con GitHub: {exc}"
