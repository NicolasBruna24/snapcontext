#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gateway de omnicanalidad: Telegram para SnapContext (v4.4.0).

Recibe mensajes de Telegram (vía webhook HTTP servido por ``web/app.py``),
los procesa con el motor de SnapContext y responde al mismo chat.

Configuración (prioridad: variable de entorno > ``~/.snapcontext/config.json``
clave ``"telegram"``):

    TELEGRAM_BOT_TOKEN    token del bot (de @BotFather).
    TELEGRAM_WEBHOOK_URL  URL pública del webhook (ngrok/dominio), opcional.

Uso típico:

    snapcontext telegram setup --token 123:ABC --webhook-url https://xxx.ngrok.io
    snapcontext --api            # o --web; webhook en POST /webhook/telegram

El pipeline es bloqueante, así que se ejecuta en segundo plano
(``asyncio.create_task`` + executor) para responder ``200 OK`` a Telegram
dentro de su ventana (~30 s) y enviar la respuesta cuando esté lista.
"""

import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
from typing import Optional

import httpx

API_TELEGRAM = "https://api.telegram.org"
# Límite duro de la API de Telegram por mensaje.
TELEGRAM_MAX_MENSAJE = 4096


# ---------------------------------------------------------------------------
# Configuración (token + webhook URL)
# ---------------------------------------------------------------------------
def _ruta_config() -> Path:
    return Path.home() / ".snapcontext" / "config.json"


def obtener_token() -> Optional[str]:
    """Token del bot: ``TELEGRAM_BOT_TOKEN`` > config.json > None."""
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if token:
        return token
    try:
        cfg = json.loads(_ruta_config().read_text(encoding="utf-8"))
        token = ((cfg.get("telegram") or {}).get("bot_token") or "").strip()
        return token or None
    except Exception:                      # noqa: BLE001 — config ausente/corrupta
        return None


def obtener_webhook_url() -> Optional[str]:
    """URL pública del webhook: ``TELEGRAM_WEBHOOK_URL`` > config.json > None."""
    url = (os.environ.get("TELEGRAM_WEBHOOK_URL") or "").strip()
    if url:
        return url
    try:
        cfg = json.loads(_ruta_config().read_text(encoding="utf-8"))
        url = ((cfg.get("telegram") or {}).get("webhook_url") or "").strip()
        return url or None
    except Exception:                      # noqa: BLE001
        return None


def guardar_configuracion_telegram(token: Optional[str],
                                   webhook_url: Optional[str]) -> dict:
    """Persiste token/webhook en ``~/.snapcontext/config.json`` → ``"telegram"``."""
    ruta = _ruta_config()
    try:
        cfg = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:                      # noqa: BLE001 — no existe o corrupto
        cfg = {}
    seccion = cfg.setdefault("telegram", {})
    if token is not None:
        seccion["bot_token"] = token.strip()
    if webhook_url is not None:
        seccion["webhook_url"] = webhook_url.strip()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(cfg, indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return dict(seccion)


def registrar_webhook(url: Optional[str] = None) -> tuple:
    """Llama a ``setWebhook`` en la API de Telegram. Devuelve (ok, detalle)."""
    token = obtener_token()
    if not token:
        return (False, "No hay TELEGRAM_BOT_TOKEN configurado.")
    destino = (url or obtener_webhook_url() or "").rstrip("/")
    if not destino:
        return (False, "Falta la URL del webhook (--webhook-url o "
                       "TELEGRAM_WEBHOOK_URL).")
    try:
        resp = httpx.post(
            f"{API_TELEGRAM}/bot{token}/setWebhook",
            json={"url": f"{destino}/webhook/telegram"},
            timeout=30,
        )
        datos = resp.json()
        return (bool(datos.get("ok")), datos.get("description")
                or datos.get("result") or str(datos))
    except Exception as exc:               # noqa: BLE001 — red/timeout
        return (False, f"Error llamando a setWebhook: {exc}")


# ---------------------------------------------------------------------------
# Envío de mensajes
# ---------------------------------------------------------------------------
async def send_telegram_message(chat_id, text: str,
                                parse_mode: str = "Markdown") -> bool:
    """Envía ``text`` al chat ``chat_id`` con ``httpx.AsyncClient``.

    - Mensajes > 4096 caracteres: se envía un avance truncado y la salida
      completa como documento ``.txt`` (``sendDocument``).
    - Devuelve ``True`` si Telegram aceptó el mensaje; nunca lanza.
    """
    token = obtener_token()
    if not token:
        print("⚠ [telegram] Sin TELEGRAM_BOT_TOKEN: no se puede responder.")
        return False
    base = f"{API_TELEGRAM}/bot{token}"
    try:
        async with httpx.AsyncClient(timeout=60) as cliente:
            if len(text) <= TELEGRAM_MAX_MENSAJE:
                resp = await cliente.post(f"{base}/sendMessage", json={
                    "chat_id": chat_id, "text": text,
                    "parse_mode": parse_mode})
                return bool(resp.json().get("ok"))
            cabeza = text[:TELEGRAM_MAX_MENSAJE]
            await cliente.post(f"{base}/sendMessage", json={
                "chat_id": chat_id,
                "text": cabeza + "\n\n… (salida completa adjunta)",
                "parse_mode": parse_mode})
            archivos = {"document": ("snapcontext_salida.txt",
                                     text.encode("utf-8"), "text/plain")}
            resp = await cliente.post(
                f"{base}/sendDocument",
                data={"chat_id": str(chat_id)}, files=archivos)
            return bool(resp.json().get("ok"))
    except Exception as exc:               # noqa: BLE001 — red/timeout/API
        print(f"✖ [telegram] Error enviando mensaje: {exc}")
        return False


# ---------------------------------------------------------------------------
# Motor: ejecuta el pipeline de SnapContext y captura la respuesta
# ---------------------------------------------------------------------------
_MENSAJE_BIENVENIDA = (
    "👋 Hola, soy *SnapContext*, tu asistente de desarrollo con contexto "
    "automático.\n\n"
    "Envíame una tarea en lenguaje natural (p. ej. «arregla el login») y:\n"
    "1. Detecto el tipo de proyecto y selecciono los archivos relevantes.\n"
    "2. Ejecuto el motor de IA sobre ellos.\n"
    "3. Te devuelvo el resultado aquí mismo.\n\n"
    "Comandos: /fix <tarea> (bucle de pruebas) · /plan <tarea> "
    "(planificador) · /help"
)


def _limpiar_consulta(texto: str) -> tuple:
    """Separa comando y consulta. Devuelve ``(consulta_final, argv_extra)``.

    - ``/fix arreglar login`` → consulta «arreglar login» con bucle de pruebas.
    - ``/plan migrar a pytest`` → consulta con el planificador.
    - Sin comando → la consulta tal cual.
    """
    texto = (texto or "").strip()
    argv_extra: list = []
    if texto.startswith("/"):
        partes = texto.split(None, 1)
        comando = partes[0].lower().lstrip("/")
        resto = partes[1].strip() if len(partes) > 1 else ""
        if comando == "fix":
            argv_extra = ["--test-loop"]
        elif comando == "plan":
            argv_extra = ["--plan"]
        elif comando in ("help", "start"):
            return ("", [])
        return (resto, argv_extra)
    return (texto, argv_extra)


def _ejecutar_pipeline(consulta: str, argv_extra: list) -> str:
    """Ejecuta el pipeline interno (bloqueante) capturando su salida.

    Usa la API interna del núcleo (``flujo_principal``/``_ejecutar_planificador``
    según los flags), NO el CLI por subprocess.
    """
    import snapcontext as sc

    buffer = io.StringIO()
    argv = ([consulta] + list(argv_extra)) if consulta else []
    try:
        args = sc.crear_parser().parse_args(sc._preparar_argv_aliases(argv))
        args.auto = True                      # Telegram nunca es interactivo
        args.no_confirmar = True
        args.depurar = False
        if hasattr(args, "confirmar"):
            args.confirmar = False
        with contextlib.redirect_stdout(buffer):
            if getattr(args, "plan", False):
                codigo = sc._ejecutar_planificador(args)
            else:
                codigo = sc.flujo_principal(args)
    except SystemExit:
        codigo = 0                            # argparse cortó el flujo
    except Exception as exc:                  # noqa: BLE001 — reportar al chat
        return f"✖ Error ejecutando la tarea: {exc}"
    salida = buffer.getvalue().strip()
    estado = "✅" if codigo == 0 else "⚠️"
    resumen = (salida[-(TELEGRAM_MAX_MENSAJE - 200):] if salida
               else "(sin salida capturada)")
    return f"{estado} Tarea terminada (código {codigo}).\n\n{resumen}"


async def run_agent_async(query: str, chat_id=None) -> str:
    """Ejecuta el agente sin bloquear el event loop y devuelve la respuesta.

    Wrapper asíncrono sobre :func:`_ejecutar_pipeline` (executor de hilos).
    """
    consulta, argv_extra = _limpiar_consulta(query)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, _ejecutar_pipeline, consulta or query, argv_extra)


# ---------------------------------------------------------------------------
# Handler del webhook
# ---------------------------------------------------------------------------
_TAREAS_ACTIVAS: set = set()


async def handle_telegram_update(update_data: dict) -> None:
    """Procesa un ``Update`` de Telegram y responde al chat.

    El trabajo pesado se lanza con ``asyncio.create_task`` para no bloquear
    al llamador (el webhook debe responder 200 en <30 s).
    """
    mensaje = update_data.get("message") or {}
    chat = mensaje.get("chat") or {}
    chat_id = chat.get("chat_id") or chat.get("id")
    texto = (mensaje.get("text") or "").strip()
    if not chat_id or not texto:
        return                                  # update no soportado

    if texto.lower().startswith("/start"):
        await send_telegram_message(chat_id, _MENSAJE_BIENVENIDA)
        return

    tarea = asyncio.create_task(_procesar_y_responder(chat_id, texto))
    # Referencia viva para que la tarea no sea recolectada prematuramente.
    _TAREAS_ACTIVAS.add(tarea)
    tarea.add_done_callback(_TAREAS_ACTIVAS.discard)


async def _procesar_y_responder(chat_id, texto: str) -> None:
    """Ejecuta el agente y envía el resultado al chat."""
    await send_telegram_message(
        chat_id, "⏳ Procesando tu solicitud con SnapContext…")
    try:
        respuesta = await run_agent_async(texto, chat_id=chat_id)
    except Exception as exc:                    # noqa: BLE001 — siempre responder
        respuesta = f"✖ Error inesperado: {exc}"
    await send_telegram_message(chat_id, respuesta)

