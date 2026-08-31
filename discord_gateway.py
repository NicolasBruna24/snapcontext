#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gateway de omnicanalidad: Discord para SnapContext (v4.5.0).

Recibe Slash Commands de Discord (interacciones vía webhook HTTP servido por
``web/app.py``), los procesa con el motor interno de SnapContext y responde
en el mismo canal.

Configuración (prioridad: variable de entorno > ``~/.snapcontext/config.json``
clave ``"discord"``):

    DISCORD_PUBLIC_KEY      clave pública Ed25519 de la aplicación.
    DISCORD_APPLICATION_ID  ID de la aplicación.
    DISCORD_BOT_TOKEN       token del bot (respuestas vía API).
    DISCORD_WEBHOOK_URL     webhook estándar del canal (opcional/alternativo).

Uso típico:

    snapcontext discord setup --public-key <KEY> --app-id <ID> --token <BOT_TOKEN>
    snapcontext --api            # o --web; webhook en POST /webhook/discord

100 % local/self-hosted: sin discord.py ni servidores centrales; solo
``httpx`` + ``cryptography`` (verificación de firma Ed25519 de Discord).
"""

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import httpx

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey)
    from cryptography.exceptions import InvalidSignature
except ImportError:                            # pragma: no cover — dependencia base
    Ed25519PublicKey = None
    InvalidSignature = Exception

API_DISCORD = "https://discord.com/api/v10"
# Límite duro de la API de Discord por mensaje.
DISCORD_MAX_MENSAJE = 2000

# Tipos de interacción de Discord.
INTERACTION_PING = 1
INTERACTION_APPLICATION_COMMAND = 2


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
def _ruta_config() -> Path:
    return Path.home() / ".snapcontext" / "config.json"


def _leer_seccion() -> dict:
    try:
        cfg = json.loads(_ruta_config().read_text(encoding="utf-8"))
        return cfg.get("discord") or {}
    except Exception:                          # noqa: BLE001 — ausente/corrupto
        return {}


def obtener_public_key() -> Optional[str]:
    """Clave pública de la app: ``DISCORD_PUBLIC_KEY`` > config.json > None."""
    clave = (os.environ.get("DISCORD_PUBLIC_KEY") or "").strip()
    if clave:
        return clave
    return (_leer_seccion().get("public_key") or "").strip() or None


def obtener_application_id() -> Optional[str]:
    """ID de la aplicación: ``DISCORD_APPLICATION_ID`` > config.json > None."""
    valor = (os.environ.get("DISCORD_APPLICATION_ID") or "").strip()
    if valor:
        return valor
    return (_leer_seccion().get("application_id") or "").strip() or None


def obtener_bot_token() -> Optional[str]:
    """Token del bot: ``DISCORD_BOT_TOKEN`` > config.json > None."""
    token = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    if token:
        return token
    return (_leer_seccion().get("bot_token") or "").strip() or None


def obtener_webhook_url() -> Optional[str]:
    """Webhook estándar del canal: ``DISCORD_WEBHOOK_URL`` > config.json."""
    url = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if url:
        return url
    return (_leer_seccion().get("webhook_url") or "").strip() or None


def guardar_configuracion_discord(public_key: Optional[str],
                                  application_id: Optional[str],
                                  bot_token: Optional[str],
                                  webhook_url: Optional[str]) -> dict:
    """Persiste las credenciales en ``~/.snapcontext/config.json`` → ``"discord"``."""
    ruta = _ruta_config()
    try:
        cfg = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:                          # noqa: BLE001
        cfg = {}
    seccion = cfg.setdefault("discord", {})
    if public_key is not None:
        seccion["public_key"] = public_key.strip()
    if application_id is not None:
        seccion["application_id"] = application_id.strip()
    if bot_token is not None:
        seccion["bot_token"] = bot_token.strip()
    if webhook_url is not None:
        seccion["webhook_url"] = webhook_url.strip()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return dict(seccion)


# ---------------------------------------------------------------------------
# Verificación de firma (Ed25519 de Discord)
# ---------------------------------------------------------------------------
def verify_signature(data: bytes, signature_hex: str,
                     timestamp: str) -> bool:
    """Verifica la firma Ed25519 de una interacción de Discord.

    Lanza ``ValueError`` si la firma es inválida o los parámetros están mal
    formados (protección contra peticiones falsificadas).
    """
    if Ed25519PublicKey is None:
        raise ValueError(
            "Falta 'cryptography': pip install cryptography")
    public_key_hex = obtener_public_key()
    if not public_key_hex:
        raise ValueError("DISCORD_PUBLIC_KEY no está configurada.")
    try:
        firma = bytes.fromhex(signature_hex)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Firma con formato inválido: {exc}") from exc
    if len(firma) != 64:
        raise ValueError("La firma debe tener exactamente 64 bytes.")
    mensaje = timestamp.encode("utf-8") + data
    try:
        Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(public_key_hex)).verify(firma, mensaje)
    except InvalidSignature as exc:
        raise ValueError("Firma inválida.") from exc
    return True


# ---------------------------------------------------------------------------
# Envío de mensajes
# ---------------------------------------------------------------------------
async def send_discord_message(webhook_url: Optional[str], content: str,
                               interaction_id: Optional[str] = None,
                               interaction_token: Optional[str] = None) -> bool:
    """Envía ``content`` a Discord (100 % async con ``httpx.AsyncClient``).

    - Con ``interaction_token``: follow-up vía ``/webhooks/{app_id}/{token}``
      (válido 15 min tras la interacción).
    - Sin token: POST al webhook estándar del canal.
    - Contenido > 2000 caracteres: resumen + salida completa como `.txt`.
    - Devuelve ``True`` si Discord aceptó el mensaje; nunca lanza.
    """
    try:
        async with httpx.AsyncClient(timeout=60) as cliente:
            if interaction_token:
                app_id = obtener_application_id() or "@me"
                base = f"{API_DISCORD}/webhooks/{app_id}/{interaction_token}"
            else:
                if not webhook_url:
                    print("⚠ [discord] Sin webhook ni interaction_token: "
                          "no se puede responder.")
                    return False
                base = webhook_url

            if len(content) <= DISCORD_MAX_MENSAJE:
                resp = await cliente.post(base, json={"content": content})
                return resp.status_code in (200, 204)

            # Demasiado largo: primer trozo + salida completa como .txt.
            cabeza = content[:DISCORD_MAX_MENSAJE]
            await cliente.post(base, json={
                "content": cabeza + "\n… (salida completa adjunta)"})
            archivos = {"files[0]": ("snapcontext_salida.txt",
                                     content.encode("utf-8"), "text/plain")}
            resp = await cliente.post(base, data={}, files=archivos)
            return resp.status_code in (200, 204)
    except Exception as exc:                   # noqa: BLE001 — red/timeout/API
        print(f"✖ [discord] Error enviando mensaje: {exc}")
        return False


# ---------------------------------------------------------------------------
# Envío de notificaciones push (v6.8.0)
# ---------------------------------------------------------------------------
def enviar_notificacion(canal_id_o_webhook: Optional[str], mensaje: str) -> bool:
    """Envío sincrónico/asincrónico de notificación push a Discord."""
    try:
        url = canal_id_o_webhook or obtener_webhook_url()
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(send_discord_message(url, mensaje))
            return True
        return loop.run_until_complete(send_discord_message(url, mensaje))
    except RuntimeError:
        return asyncio.run(send_discord_message(canal_id_o_webhook or obtener_webhook_url(), mensaje))
    except Exception as exc:
        print(f"✖ [discord] Error en enviar_notificacion: {exc}")
        return False


# ---------------------------------------------------------------------------
# Handler de interacciones
# ---------------------------------------------------------------------------
_MENSAJE_BIENVENIDA = (
    "👋 Hola, soy **SnapContext**, tu asistente de desarrollo con contexto "
    "automático.\n\nComandos síncronos:\n"
    "- `/snap <tarea>` — ejecuta el pipeline completo.\n"
    "- `/fix <tarea>` — bucle de pruebas (equivalente a `snapcontext fix`).\n"
    "- `/plan <tarea>` — planificador (`snapcontext --plan`).\n\n"
    "Comandos asíncronos (v6.8.0):\n"
    "- `/pr <numero>` — revisar Pull Request en segundo plano.\n"
    "- `/tests [rama]` — ejecutar pruebas en segundo plano.\n"
    "- `/status` — ver estado de tareas en cola.\n"
    "- `/cancel <id>` — cancelar tarea en cola.\n"
    "- `/help` — muestra esta ayuda."
)


def _extraer_consulta(interaction_data: dict) -> tuple:
    """Extrae ``(comando, consulta, argv_extra)`` de una interacción.

    - ``/fix arreglar login`` → ``("fix", "arreglar login", ["--test-loop"])``.
    - ``/plan migrar`` → ``("plan", "migrar", ["--plan"])``.
    - ``/start`` / ``/help`` → ``("", "", [])`` (respuesta directa).
    """
    datos = interaction_data.get("data") or {}
    comando = (datos.get("name") or "").lower().lstrip("/")
    opciones = datos.get("options") or []
    consulta = ""
    if opciones:
        consulta = str(opciones[0].get("value") or "").strip()
    argv_extra: list = []
    if comando == "fix":
        argv_extra = ["--test-loop"]
    elif comando == "plan":
        argv_extra = ["--plan"]
    elif comando in ("start", "help", "pr", "tests", "test", "status", "cancel"):
        # Se conserva el comando para que se procese adecuadamente
        return (comando, consulta, [])
    return (comando, consulta, argv_extra)


async def run_agent_async(query: str) -> str:
    """Ejecuta el motor interno sin bloquear el event loop.

    Reutiliza el pipeline capturador de ``telegram_gateway._ejecutar_pipeline``
    (misma lógica interna: ``flujo_principal``/``_ejecutar_planificador``).
    """
    from telegram_gateway import _ejecutar_pipeline
    loop = asyncio.get_running_loop()
    texto = query.strip()
    if texto.startswith("/"):                  # compat.: admite "/fix tarea"
        from telegram_gateway import _limpiar_consulta
        texto, argv_extra = _limpiar_consulta(texto)
        return await loop.run_in_executor(
            None, _ejecutar_pipeline, texto, argv_extra)
    return await loop.run_in_executor(
        None, _ejecutar_pipeline, texto, [])


_TAREAS_ACTIVAS: set = set()


async def handle_discord_interaction(interaction_data: dict) -> Optional[dict]:
    """Procesa una interacción de Discord y devuelve la respuesta inmediata.

    El trabajo pesado se lanza con ``asyncio.create_task`` para no bloquear
    al llamador (Discord exige respuesta en <3 s). El valor devuelto es el
    JSON que debe enviar el endpoint HTTP (o ``None`` si no hay respuesta).
    """
    tipo = int(interaction_data.get("type") or 0)
    if tipo == INTERACTION_PING:
        return {"type": 1}                     # PONG de verificación

    if tipo != INTERACTION_APPLICATION_COMMAND:
        return None                            # interacción no soportada

    interaction_id = str(interaction_data.get("id") or "")
    interaction_token = interaction_data.get("token") or ""
    canal = ((interaction_data.get("channel") or {}).get("id")
             or interaction_data.get("channel_id"))

    comando, consulta, argv_extra = _extraer_consulta(interaction_data)

    if comando in ("start", "help"):
        await send_discord_message(None, _MENSAJE_BIENVENIDA,
                                   interaction_id=interaction_id,
                                   interaction_token=interaction_token)
        return None

    tarea = asyncio.create_task(
        _procesar_y_responder(canal, comando, consulta,
                              argv_extra, interaction_id, interaction_token))
    # Referencia viva para que la tarea no sea recolectada prematuramente.
    _TAREAS_ACTIVAS.add(tarea)
    tarea.add_done_callback(_TAREAS_ACTIVAS.discard)
    # Respuesta diferida (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE): Discord
    # muestra "pensando…" mientras el agente trabaja en segundo plano.
    return {"type": 5}


async def _procesar_y_responder(canal, comando: str, consulta: str, argv_extra: list,
                                interaction_id: str,
                                interaction_token: str) -> None:
    """Ejecuta el agente o comandos en cola y envía el resultado al canal."""
    # Comandos asíncronos v6.8.0
    if comando == "pr":
        try:
            import task_queue as tq
            num = int(consulta) if consulta.isdigit() else 0
            tid = tq.encolar_tarea(
                tipo="pr_review",
                datos={"numero": num, "instruccion": f"Revisar PR #{num}"},
                chat_id=canal,
                canal="discord",
            )
            respuesta = f"🔔 **Tarea encolada** (ID: `{tid}`)\nRevisando PR #{num} en segundo plano."
        except Exception as exc:
            respuesta = f"❌ Error encolando PR: {exc}"
    elif comando in ("tests", "test"):
        try:
            import task_queue as tq
            tid = tq.encolar_tarea(
                tipo="tests",
                datos={"rama": consulta or "main"},
                chat_id=canal,
                canal="discord",
            )
            respuesta = f"🔔 **Tarea encolada** (ID: `{tid}`)\nEjecutando suite de pruebas (rama: `{consulta or 'actual'}`) en segundo plano."
        except Exception as exc:
            respuesta = f"❌ Error encolando pruebas: {exc}"
    elif comando == "status":
        try:
            import task_queue as tq
            tareas = tq.listar_tareas(limite=5)
            if not tareas:
                respuesta = "📋 No hay tareas registradas en la cola."
            else:
                lineas = ["📋 **Estado de Tareas Recientes:**"]
                for t in tareas:
                    simbolo = "⏳" if t["estado"] in ("pendiente", "ejecutando") else ("✅" if t["estado"] == "completada" else "❌")
                    lineas.append(f"{simbolo} `#{t['id']}` [{t['tipo']}] — **{t['estado']}**")
                respuesta = "\n".join(lineas)
        except Exception as exc:
            respuesta = f"❌ Error consultando estado: {exc}"
    elif comando == "cancel":
        try:
            import task_queue as tq
            tid = int(consulta) if consulta.isdigit() else 0
            ok = tq.cancelar_tarea(tid)
            if ok:
                respuesta = f"✅ Tarea `#{tid}` cancelada correctamente."
            else:
                respuesta = f"⚠️ No se pudo cancelar la tarea `#{tid}` (no existe o ya no está pendiente)."
        except Exception as exc:
            respuesta = f"❌ Error cancelando tarea: {exc}"
    else:
        try:
            from telegram_gateway import _ejecutar_pipeline
            loop = asyncio.get_running_loop()
            respuesta = await loop.run_in_executor(
                None, _ejecutar_pipeline, consulta or comando, argv_extra)
        except Exception as exc:                    # noqa: BLE001 — siempre responder
            respuesta = f"✖ Error inesperado: {exc}"

    await send_discord_message(None, respuesta,
                               interaction_id=interaction_id,
                               interaction_token=interaction_token)


