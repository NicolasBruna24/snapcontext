#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de refactorización autónoma de skills (v5.0.0) — estilo Hermes.

Evalúa la calidad de cada skill (exitos/fallos/tokens/tiempo), identifica a
los candidatos a mejorar (fallos > 20 % o tokens por encima del umbral),
pide al LLM un prompt más eficiente, lo prueba en un sandbox y, si mejora las
métricas, guarda la nueva versión (dejando constancia de la anterior en la
tabla `historial_skills`).

Principios:
  - NUNCA toca el código del usuario: solo los prompts almacenados de los
    skills.
  - Usa el sandbox de v4.3.0 (`sc._envolver_sandbox`) para probar los nuevos
    prompts antes de persistirlos.
  - Respet ``--auto`` (aplica sin preguntar) e interactivo (pregunta antes).
  - Se ejecuta en segundo plano vía un daemon con intervalo configurable por
    la variable de entorno ``CURADOR_INTERVALO_HORAS`` (por defecto 6).
  - Notifica por Telegram/Discord si están configurados (mejoras).
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import threading
from typing import List, Optional

__all__ = [
    "UMBRAL_FALLOS",
    "UMBRAL_TOKENS",
    "MIN_USOS",
    "CLAVE_ACTIVO",
    "CLAVE_ULTIMA_PASADA",
    "activar_curador",
    "desactivar_curador",
    "esta_activo",
    "intervalo_horas",
    "evaluar_skills",
    "refactorizar_skill",
    "ejecutar_curador",
    "estado_curador",
    "notificar_mejora",
    "daemon_proactivo",
    "iniciar_daemon_fondo",
]

# Los skills malos (muchos fallos, tokens altos) se consideran candidatos.
UMBRAL_FALLOS = 0.20            # tasa de fallos > 20 %
UMBRAL_TOKENS = 1500            # tokens promedio > umbral
MIN_USOS = 3                    # un skill necesita usarse ≥ N veces

CLAVE_ACTIVO = "curador_proactivo_activo"            # "1"/"0" (contexto_kv)
CLAVE_ULTIMA_PASADA = "curador_proactivo_ultima_pasada"
CLAVE_INTERVALO = "curador_proactivo_intervalo_horas"


def _sc():
    """Importa (perezosamente) el módulo principal snapcontext."""
    import snapcontext as _sc
    return _sc


def _ahora() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def intervalo_horas() -> int:
    """Intervalo del daemon en horas (env `CURADOR_INTERVALO_HORAS`, def 6)."""
    import os
    try:
        return max(1, int(os.environ.get("CURADOR_INTERVALO_HORAS", "6")))
    except (TypeError, ValueError):
        return 6


def esta_activo() -> bool:
    """Estado persistente del motor (por defecto activo)."""
    try:
        sc = _sc()
        valor = sc._kv_obtener(CLAVE_ACTIVO, "1")  # noqa: SLF001
        return valor != "0"
    except Exception:               # pragma: no cover - blindaje
        return True


def activar_curador() -> None:
    """Reactiva el curador proactivo (persistente)."""
    sc = _sc()
    sc._kv_fijar(CLAVE_ACTIVO, "1")


def desactivar_curador() -> None:
    """Desactiva el curador proactivo (persistente)."""
    sc = _sc()
    sc._kv_fijar(CLAVE_ACTIVO, "0")


def _estimar_tokens(texto: str) -> int:
    """Estimación ligera de tokens (≈ palabras / trozos de 4 caracteres)."""
    texto = texto or ""
    return max(1, len(str(texto).split()), len(str(texto)) // 4)


def _texto_prompt(skill: dict) -> str:
    """Serializa el 'prompt' de un skill: consulta + pasos de instrucción."""
    consulta = str(skill.get("consulta") or "")
    partes = [consulta]
    pasos = skill.get("pasos") or []
    if pasos:
        partes.append("Pasos:")
        for paso in pasos:
            partes.append(f"- {paso.get('descripcion') or paso.get('accion') or ''}")
    return "\n".join(p for p in partes if p).strip()


def evaluar_skills(umbral_fallos: float = UMBRAL_FALLOS,
                   umbral_tokens: int = UMBRAL_TOKENS,
                   min_usos: int = MIN_USOS) -> list:
    """Escanea la tabla `skills` y devuelve los candidatos a refactorizar.

    Candidatos = skills activos, no archivados, con >= ``min_usos`` usos y que
    superan el umbral de fallos o el de tokens promedio.
    """
    sc = _sc()
    sc._db_init()                                        # noqa: SLF001
    filas = sc._db_query(                                 # noqa: SLF001
        "SELECT * FROM skills WHERE activo = 1 AND archivado = 0 "
        "AND usos >= ? ORDER BY usos DESC", (min_usos,))
    candidatos = []
    for fila in filas:
        usos = int(fila["usos"] or 0)
        fallos = int(fila["fallos"] or 0)
        tokens = int(fila.get("tokens_promedio") or 0)
        tasa = fallos / usos if usos else 0.0
        if tasa > umbral_fallos or tokens > umbral_tokens:
            skill = dict(fila)
            skill["tasa_fallos"] = round(tasa, 3)
            candidatos.append(skill)
    return candidatos


def _proveedor_efectivo() -> str:
    sc = _sc()
    try:
        cfg = sc.cargar_configuracion()
        return str(cfg.get("provider") or sc.PROVEEDOR_DEFECTO)
    except Exception:                                       # pragma: no cover
        return sc.PROVEEDOR_DEFECTO


def _llm_reescribir(skill: dict, proveedor: Optional[str] = None,
                    modelo: Optional[str] = None) -> str:
    """Pide al LLM un prompt más eficiente/claro/robusto para el skill."""
    sc = _sc()
    proveedor = proveedor or _proveedor_efectivo()
    original = _texto_prompt(skill)
    sistema = (
        "Eres SnapContext, experto en refactorización de prompts de "
        "habilidades (skills). Escribe de nuevo el siguiente prompt para que sea "
        "más claro, conciso, eficiente (menos tokens) y robusto (sin ambigüedad). "
        "Conserva la intención y todos los pasos importantes. Devuelve SOLO el "
        "nuevo prompt en texto plano, sin preámbulos.")
    mensajes = [
        {"role": "user", "content": f"{sistema}\n\n---\n\n{original}"},
    ]
    return str(sc._enviar_al_proveedor(proveedor, modelo, mensajes)).strip()


# ---------------------------------------------------------------------------
# Validación y prueba (sandbox) de prompts candidatos
# ---------------------------------------------------------------------------

def _validar_prompt(nuevo: str, original: str) -> Optional[str]:
    """Validación estructural del prompt candidato.

    Devuelve ``None`` si es válido; en caso contrario, una cadena con el
    motivo del rechazo.
    """
    nuevo = (nuevo or "").strip()
    original = (original or "").strip()
    if not nuevo:
        return "prompt vacío"
    if len(nuevo) < 10:
        return "prompt demasiado corto (< 10 caracteres)"
    if nuevo == original:
        return "el LLM devolvió el mismo prompt sin cambios"
    if len(nuevo) > 20000:
        return "prompt excesivamente largo (> 20000 caracteres)"
    return None


def _probar_prompt(skill: dict, nuevo_prompt: str,
                   comando_prueba: Optional[str] = None,
                   timeout_seg: int = 600) -> bool:
    """Prueba el nuevo prompt en el sandbox de v4.3.0.

    Ejecuta ``comando_prueba`` (por defecto la suite de tests del proyecto)
    envuelto con ``sc._envolver_sandbox`` cuando el sandbox está activo.
    Devuelve ``True`` solo si el comando termina con código 0. Nunca toca
    archivos del usuario: la prueba corre sobre el workspace montado que
    aporta el sandbox.
    """
    comando = comando_prueba or os.environ.get(
        "CURADOR_COMANDO_PRUEBA",
        "python -m unittest discover -s tests -q")
    try:
        timeout_seg = int(os.environ.get("CURADOR_TIMEOUT_PRUEBA", timeout_seg))
    except (TypeError, ValueError):
        pass
    sc = _sc()
    raiz = os.getcwd()
    if getattr(sc, "_SANDBOX_ACTIVO", False):
        try:
            comando = sc._envolver_sandbox(comando, raiz)  # noqa: SLF001
        except Exception:                                   # pragma: no cover
            pass
    try:
        proc = subprocess.run(
            comando, shell=True, capture_output=True, text=True,
            timeout=timeout_seg)
        return proc.returncode == 0
    except Exception:                                       # noqa: BLE001
        return False


def refactorizar_skill(skill_id: int, auto: bool = True,
                       proveedor: Optional[str] = None,
                       modelo: Optional[str] = None) -> dict:
    """Refactoriza un skill de forma autónoma (flujo completo estilo Hermes).

    Pasos:
      1. Lee el prompt actual del skill.
      2. Pide al LLM una versión más eficiente/clara/robusta.
      3. Valida estructuralmente el candidato.
      4. Lo prueba en el sandbox (suite de tests).
      5. Si pasa las pruebas Y reduce tokens, persiste la nueva versión
         (la anterior queda registrada en `historial_skills`) y notifica.
      6. Si algo falla, NO guarda nada y registra el motivo.

    Devuelve un dict: {"ok", "mejorado", "skill", "version", "motivo",
    "ahorro_pct"}.
    """
    sc = _sc()
    sc._db_init()                                        # noqa: SLF001
    filas = sc._db_query(                                # noqa: SLF001
        "SELECT * FROM skills WHERE id = ?", (skill_id,))
    if not filas:
        return {"ok": False, "mejorado": False, "skill": f"#{skill_id}",
                "motivo": "skill no encontrado", "version": None,
                "ahorro_pct": 0.0}
    skill = dict(filas[0])
    nombre = str(skill.get("nombre") or f"#{skill_id}")
    version_actual = int(skill.get("version") or 1)
    original = _texto_prompt(skill)
    tokens_antes = _estimar_tokens(original)

    # 2) Reescritura por el LLM.
    try:
        nuevo = _llm_reescribir(skill, proveedor, modelo)
    except Exception as exc:                             # noqa: BLE001
        motivo = f"error del LLM: {exc}"
        _registrar_error(skill_id, version_actual, motivo)
        return {"ok": False, "mejorado": False, "skill": nombre,
                "motivo": motivo, "version": version_actual,
                "ahorro_pct": 0.0}

    # 3) Validación estructural.
    rechazo = _validar_prompt(nuevo, original)
    if rechazo:
        _registrar_error(skill_id, version_actual, rechazo)
        return {"ok": False, "mejorado": False, "skill": nombre,
                "motivo": rechazo, "version": version_actual,
                "ahorro_pct": 0.0}

    # 4) Prueba en sandbox.
    if not _probar_prompt(skill, nuevo):
        motivo = "el prompt candidato no pasó las pruebas del sandbox"
        _registrar_error(skill_id, version_actual, motivo)
        return {"ok": False, "mejorado": False, "skill": nombre,
                "motivo": motivo, "version": version_actual,
                "ahorro_pct": 0.0}

    # 5) ¿Mejora las métricas? (menos tokens que el original).
    tokens_nuevo = _estimar_tokens(nuevo)
    if tokens_nuevo >= tokens_antes:
        motivo = f"sin mejora: {tokens_nuevo} tokens (original {tokens_antes})"
        _registrar_error(skill_id, version_actual, motivo)
        return {"ok": True, "mejorado": False, "skill": nombre,
                "motivo": motivo, "version": version_actual,
                "ahorro_pct": 0.0}

    ahorro_pct = round((tokens_antes - tokens_nuevo) / tokens_antes * 100.0, 1)

    # 6) Persistir: archivar la versión previa y actualizar el skill.
    fecha = _ahora()
    sc._db_ejecutar(                                     # noqa: SLF001
        "INSERT INTO historial_skills (skill_id, version, prompt, motivo, "
        "fecha) VALUES (?, ?, ?, ?, ?)",
        (skill_id, version_actual, original, "refactorizado", fecha))
    nueva_version = version_actual + 1
    sc._db_ejecutar(                                     # noqa: SLF001
        "UPDATE skills SET consulta = ?, version = ? WHERE id = ?",
        (nuevo, nueva_version, skill_id))

    # Notificación best-effort (Telegram/Discord si están configurados).
    notificar_mejora(nombre, nueva_version, ahorro_pct)
    return {"ok": True, "mejorado": True, "skill": nombre,
            "motivo": "prompt refactorizado y validado",
            "version": nueva_version, "ahorro_pct": ahorro_pct}


def _registrar_error(skill_id: int, version: int, motivo: str) -> None:
    """Registra intentos fallidos de refactorización (trazabilidad)."""
    try:
        sc = _sc()
        sc._db_init()                                    # noqa: SLF001
        sc._db_ejecutar(                                 # noqa: SLF001
            "INSERT INTO historial_skills (skill_id, version, prompt, "
            "motivo, fecha) VALUES (?, ?, '', ?, ?)",
            (skill_id, version, f"error: {motivo}"[:400], _ahora()))
    except Exception:                                    # pragma: no cover
        pass


# ---------------------------------------------------------------------------
# Motor de alto nivel: ejecución, estado, notificaciones y daemon
# ---------------------------------------------------------------------------

def ejecutar_curador(auto: Optional[bool] = None,
                     proveedor: Optional[str] = None) -> Optional[list]:
    """Ejecuta una pasada completa del motor de refactorización.

    1. Comprueba que el curador esté activo (si no, devuelve ``None``).
    2. Evalúa los skills candidatos (`evaluar_skills`).
    3. Refactoriza cada candidato.
    4. Guarda la marca de tiempo de la última pasada.

    ``auto=None`` hereda el modo de ``snapcontext`` (``--auto``/interactivo).
    Devuelve la lista de resultados por skill (posiblemente vacía).
    """
    if not esta_activo():
        return None
    sc = _sc()
    if auto is None:
        auto = bool(getattr(sc, "_AUTO", False))
    candidatos = evaluar_skills()
    resultados = []
    for skill in candidatos:
        if not auto:
            # Modo interactivo: preguntar antes de aplicar (vía ui.py si existe).
            try:
                from ui import preguntar_interactivo            # noqa: E402
                opciones = ["[c] Continuar", "[a] Abortar", "[s] Saltar"]
                respuesta = preguntar_interactivo(
                    opciones, f"¿Refactorizar el skill '{skill['nombre']}'?")
                if respuesta == "a":
                    break
                if respuesta == "s":
                    continue
            except Exception:                            # noqa: BLE001
                pass                                     # sin ui: aplicar igual
        resultados.append(
            refactorizar_skill(int(skill["id"]), auto=auto,
                               proveedor=proveedor))
    sc._kv_fijar(CLAVE_ULTIMA_PASADA, _ahora())          # noqa: SLF001
    return resultados


def estado_curador() -> dict:
    """Resumen de estado del curador para `snapcontext curador estado`.

    Claves: activo, intervalo_horas, total_skills, activos, candidatos,
    ultima_pasada, reinado_lista (peor skill por fallos), mejoras totales.
    """
    sc = _sc()
    sc._db_init()                                        # noqa: SLF001
    total = sc._db_query("SELECT COUNT(*) AS n FROM skills")[0]["n"]  # noqa: SLF001
    activos = sc._db_query(                              # noqa: SLF001
        "SELECT COUNT(*) AS n FROM skills WHERE activo = 1 AND archivado = 0"
    )[0]["n"]
    candidatos = evaluar_skills()
    reinado = sc._db_query(                              # noqa: SLF001
        "SELECT id, nombre, usos, exitos, fallos, tokens_promedio FROM "
        "skills WHERE usos >= ? ORDER BY fallos DESC, tokens_promedio DESC "
        "LIMIT 20", (MIN_USOS,))
    mejoras = sc._db_query(                              # noqa: SLF001
        "SELECT COUNT(*) AS n FROM historial_skills "
        "WHERE motivo = 'refactorizado'")[0]["n"]
    return {
        "activo": esta_activo(),
        "intervalo_horas": intervalo_horas(),
        "total_skills": int(total),
        "activos": int(activos),
        "candidatos": len(candidatos),
        "ultima_pasada": sc._kv_obtener(CLAVE_ULTIMA_PASADA, ""),  # noqa: SLF001
        "mejoras_totales": int(mejoras),
        "reinado_lista": [dict(fila) for fila in reinado],
    }


def notificar_mejora(nombre: str, version: int, ahorro_pct: float) -> bool:
    """Notifica una mejora por Telegram/Discord (best-effort, nunca falla).

    Mensaje: *"🔧 Skill 'x' mejorado (v2). Tokens reducidos un 30 %."*
    Usa `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`, o `DISCORD_WEBHOOK_URL`.
    """
    mensaje = (f"🔧 Skill '{nombre}' mejorado (v{version}). "
               f"Tokens reducidos un {ahorro_pct:.0f}%.")
    token_tg = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_tg = os.environ.get("TELEGRAM_CHAT_ID", "")
    webhook_dc = os.environ.get("DISCORD_WEBHOOK_URL", "")
    try:
        import httpx                                     # noqa: F401,E402
        if token_tg and chat_tg:
            url = "https://api.telegram.org/bot" + token_tg + "/sendMessage"
            resp = httpx.post(url, json={
                "chat_id": chat_tg, "text": mensaje}, timeout=10)
            return resp.status_code == 200
        if webhook_dc:
            resp = httpx.post(webhook_dc, json={
                "content": mensaje}, timeout=10)
            return resp.status_code in (200, 204)
    except Exception:                                    # noqa: BLE001
        return False
    return False


# ---------------------------------------------------------------------------
# Daemon en segundo plano (no bloquea el CLI principal)
# ---------------------------------------------------------------------------

_DAEMON_HILO: Optional[threading.Thread] = None
_DAEMON_PARAR = threading.Event()


def daemon_proactivo(parar: Optional[threading.Event] = None) -> None:
    """Bucle del daemon: `evaluar` + `refactorizar` cada X horas.

    El intervalo se configura con ``CURADOR_INTERVALO_HORAS`` (def. 6).
    Duerme en incrementos de 30 s para poder detenerse con agilidad.
    """
    parar = parar or _DAEMON_PARAR
    while not parar.is_set():
        # Espera fraccionada ANTES de la primera pasada: así el arranque del
        # CLI es instantáneo y el coste del curador nunca paga el cold-start.
        segundos = intervalo_horas() * 3600
        transcurrido = 0
        while not parar.is_set() and transcurrido < segundos:
            parar.wait(min(30, segundos - transcurrido))
            transcurrido += 30
        if parar.is_set():
            break
        try:
            if esta_activo():
                ejecutar_curador(auto=True)
        except Exception:                                # noqa: BLE001
            pass                                         # el daemon nunca muere


def iniciar_daemon_fondo() -> Optional[threading.Thread]:
    """Arranca el daemon en un hilo demonio (si no estaba ya corriendo)."""
    global _DAEMON_HILO
    if not esta_activo():
        return None
    if _DAEMON_HILO is not None and _DAEMON_HILO.is_alive():
        return _DAEMON_HILO
    _DAEMON_PARAR.clear()
    _DAEMON_HILO = threading.Thread(
        target=daemon_proactivo, name="curador-proactivo", daemon=True)
    _DAEMON_HILO.start()
    return _DAEMON_HILO


def detener_daemon() -> None:
    """Señala la parada del daemon (útil en tests)."""
    _DAEMON_PARAR.set()