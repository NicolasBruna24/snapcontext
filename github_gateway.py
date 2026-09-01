#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

API_GITHUB = "https://api.github.com"
CONFIG_DIR = Path.home() / ".snapcontext"
CONFIG_PATH = CONFIG_DIR / "config.json"


# ---------------------------------------------------------------------------
# Configuración y Credenciales
# ---------------------------------------------------------------------------
def _leer_seccion_github() -> Dict[str, Any]:
    try:
        if CONFIG_PATH.is_file():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return cfg.get("github") or {}
    except Exception:  # noqa: BLE001
        pass
    return {}


def obtener_webhook_secreto() -> Optional[str]:
    """Secreto del webhook: GITHUB_WEBHOOK_SECRET > config.json > None."""
    secreto = (os.environ.get("GITHUB_WEBHOOK_SECRET") or "").strip()
    if secreto:
        return secreto
    return (_leer_seccion_github().get("webhook_secret") or "").strip() or None


def obtener_github_token() -> Optional[str]:
    """Token personal/App de GitHub: GITHUB_TOKEN > config.json > None."""
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        return token
    return (_leer_seccion_github().get("token") or "").strip() or None


def obtener_webhook_url() -> Optional[str]:
    """URL pública del webhook: GITHUB_WEBHOOK_URL / SNAPCONTEXT_WEBHOOK_URL > config.json."""
    url = (os.environ.get("GITHUB_WEBHOOK_URL") or os.environ.get("SNAPCONTEXT_WEBHOOK_URL") or "").strip()
    if url:
        return url
    return (_leer_seccion_github().get("webhook_url") or "").strip() or None


def guardar_configuracion_github(
    webhook_secret: Optional[str] = None,
    token: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Guarda la configuración de GitHub en ~/.snapcontext/config.json."""
    try:
        if CONFIG_PATH.is_file():
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        else:
            cfg = {}
    except Exception:  # noqa: BLE001
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
def validar_firma(payload: bytes | str, firma_cabecera: Optional[str], secreto: Optional[str] = None) -> bool:
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
# Parseo de Eventos de GitHub
# ---------------------------------------------------------------------------
def parsear_evento(payload: Dict[str, Any] | str, tipo_evento: str = "pull_request") -> Dict[str, Any]:
    """Extrae los datos clave de un payload de webhook de GitHub.

    Soporta eventos: `pull_request`, `issues`, `push`, `issue_comment`.
    """
    if isinstance(payload, str):
        try:
            datos = json.loads(payload)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"JSON inválido: {exc}"}
    else:
        datos = dict(payload or {})

    evento = str(tipo_evento or "pull_request").lower()
    resultado: Dict[str, Any] = {
        "ok": True,
        "tipo_evento": evento,
        "accion": datos.get("action", ""),
        "repositorio": (datos.get("repository") or {}).get("full_name", ""),
        "emisor": (datos.get("sender") or {}).get("login", ""),
    }

    if evento == "pull_request":
        pr = datos.get("pull_request") or {}
        resultado.update({
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
        })
    elif evento == "issues":
        issue = datos.get("issue") or {}
        resultado.update({
            "numero": issue.get("number"),
            "titulo": issue.get("title", ""),
            "cuerpo": issue.get("body", ""),
            "estado": issue.get("state", ""),
            "creador": (issue.get("user") or {}).get("login", ""),
            "etiquetas": [t.get("name") for t in (issue.get("labels") or []) if isinstance(t, dict)],
        })
    elif evento == "push":
        head_commit = datos.get("head_commit") or {}
        resultado.update({
            "ref": datos.get("ref", ""),
            "rama": (datos.get("ref", "")).replace("refs/heads/", ""),
            "head_sha": datos.get("after", ""),
            "mensaje_commit": head_commit.get("message", ""),
            "autor_commit": (head_commit.get("author") or {}).get("name", ""),
            "total_commits": len(datos.get("commits") or []),
            "modificados": head_commit.get("modified") or [],
            "agregados": head_commit.get("added") or [],
            "eliminados": head_commit.get("removed") or [],
        })
    elif evento == "issue_comment":
        comentario = datos.get("comment") or {}
        issue = datos.get("issue") or {}
        resultado.update({
            "numero": issue.get("number"),
            "es_pr": "pull_request" in issue,
            "cuerpo_comentario": comentario.get("body", ""),
            "autor_comentario": (comentario.get("user") or {}).get("login", ""),
        })
    else:
        resultado["datos_crudos"] = datos

    return resultado


# ---------------------------------------------------------------------------
# Procesamiento de Eventos y Encolado de Tareas
# ---------------------------------------------------------------------------
def procesar_evento(
    evento_parseado: Dict[str, Any],
    chat_id: Optional[str] = None,
    canal: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Optional[int]:
    """Crea y encola una tarea en `task_queue` según el evento de GitHub.

    - Pull Request (opened, synchronize, reopened) → tarea `pr_review`.
    - Issue (opened) → tarea `issue_triage` / `plan`.
    - Push (rama principal) → tarea `tests`.
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
        if accion in ("opened", "synchronize", "reopened"):
            tarea_tipo = "pr_review"
            datos_tarea["instruccion"] = f"Revisar PR #{evento_parseado.get('numero')}: {evento_parseado.get('titulo')}"
    elif tipo_evento == "issues":
        if accion in ("opened", "reopened"):
            tarea_tipo = "plan"
            datos_tarea["consulta"] = f"Resolver Issue #{evento_parseado.get('numero')}: {evento_parseado.get('titulo')}\n{evento_parseado.get('cuerpo')}"
    elif tipo_evento == "push":
        tarea_tipo = "tests"
        datos_tarea["rama"] = evento_parseado.get("rama", "main")
        datos_tarea["instruccion"] = f"Ejecutar pruebas tras push en {datos_tarea['rama']}"
    elif tipo_evento == "issue_comment":
        cuerpo = (evento_parseado.get("cuerpo_comentario") or "").strip()
        if cuerpo.startswith("/snap") or cuerpo.startswith("/fix") or cuerpo.startswith("/review"):
            tarea_tipo = "pr_review" if evento_parseado.get("es_pr") else "plan"
            datos_tarea["consulta"] = cuerpo

    if tarea_tipo:
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
def obtener_pr_diff(repo: str, numero: int | str, token: Optional[str] = None) -> Optional[str]:
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
    except Exception:  # noqa: BLE001
        return None


def comentar_pr(repo: str, numero: int | str, mensaje: str, token: Optional[str] = None) -> bool:
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
    except Exception:  # noqa: BLE001
        return False


def configurar_webhook(
    url: str,
    secreto: str,
    repo: Optional[str] = None,
    token: Optional[str] = None,
    eventos: Optional[List[str]] = None,
) -> Tuple[bool, str]:
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
    except Exception as exc:  # noqa: BLE001
        return False, f"Error de conexión con GitHub: {exc}"
