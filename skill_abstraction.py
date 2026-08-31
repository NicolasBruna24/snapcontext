#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skills dinámicos: reglas abstractas aprendidas de planes exitosos (v6.6.0).

En lugar de guardar pasos fijos (frágiles ante renombrados y refactorizados),
este módulo extrae **reglas de estilo/arquitectura** de los planes que terminan
con éxito y las almacena en la tabla SQLite ``reglas``:

    reglas(id, patron, accion, archivos_afectados, dependencias,
           confianza, usos, creado)

Piezas principales:

- :func:`extraer_regla`: usa el LLM para abstraer una regla del plan; si
  falla, aplica una heurística local (archivos más editados).
- :func:`aplicar_regla`: si una tarea del usuario coincide con el patrón de
  la regla (similitud + palabras clave), devuelve pasos sugeridos.
- :func:`guardar_regla`: persiste la regla; si ya existía una similar,
  refuerza su ``confianza`` y ``usos`` en lugar de duplicarla.
- :func:`inyectar_en_claudemd`: añade la regla a la sección ``## Reglas
  aprendidas`` de ``CLAUDE.md``/``SNAPCONTEXT.md`` (idempotente).
- :func:`buscar_reglas`: reglas ordenadas por confianza para enriquecer el
  prompt del planificador.

Seguridad: los textos se sanea (control de longitud, se eliminan cierres de
bloques `` ``` `` para que una regla no pueda inyectar bloques de código).
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any, Dict, List, Optional

UMBRAL_CONFIANZA_INYECCION = 0.8   # reglas con confianza > 0.8 → CLAUDE.md
UMBRAL_COINCIDENCIA_TAREA = 0.45   # similitud mínima patrón ↔ tarea
MAX_TEXTO = 500                    # longitud máxima por campo de regla

SECCION_REGLAS = "## Reglas aprendidas"

__all__ = [
    "extraer_regla", "aplicar_regla", "guardar_regla", "buscar_reglas",
    "inyectar_en_claudemd", "inyectar_todas_las_reglas",
    "regla_a_linea", "sanitizar", "UMBRAL_CONFIANZA_INYECCION",
    "UMBRAL_COINCIDENCIA_TAREA", "SECCION_REGLAS",
]


# ---------------------------------------------------------------------------
# Saneamiento
# ---------------------------------------------------------------------------
def sanitizar(texto: Any, maximo: int = MAX_TEXTO) -> str:
    """Devuelve texto plano seguro: sin `` ``` `` (bloques de código) ni
    caracteres de control, recortado a ``maximo`` caracteres."""
    texto = str(texto or "")
    texto = texto.replace("```", "'")
    texto = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", texto)
    return texto.strip()[:maximo]


def _sc():
    import snapcontext as _sc            # importación perezosa
    return _sc


def _regla_vacia(tarea: str) -> Dict[str, Any]:
    return {"patron": sanitizar(tarea, 200), "accion": "",
            "archivos_afectados": [], "dependencias": [], "confianza": 1.0}


def _extraer_regla_heuristica(plan: dict, contexto: dict) -> Dict[str, Any]:
    """Fallback local: los archivos más editados definen la regla."""
    conteo: Dict[str, int] = {}
    for paso in (plan.get("pasos") or []):
        archivo = str(paso.get("archivo") or paso.get("ruta") or "").strip()
        if archivo:
            conteo[archivo] = conteo.get(archivo, 0) + 1
    archivos = [a for a, _ in
                sorted(conteo.items(), key=lambda kv: -kv[1])[:5]]
    tarea = plan.get("tarea") or contexto.get("tarea") or ""
    regla = _regla_vacia(tarea)
    regla["accion"] = ("editar " + ", ".join(archivos)) if archivos else ""
    regla["archivos_afectados"] = archivos
    regla["confianza"] = 0.6            # heurística ⇒ confianza menor
    return regla


def _parsear_regla_llm(texto: str, tarea: str) -> Optional[Dict[str, Any]]:
    """Convierte la respuesta del LLM (JSON o suelto) en regla validada."""
    if not texto:
        return None
    candidato = texto.strip()
    m = re.search(r"\{.*\}", candidato, re.DOTALL)
    if m:
        candidato = m.group(0)
    try:
        datos = json.loads(candidato)
    except (ValueError, TypeError):
        return None
    if not isinstance(datos, dict):
        return None
    patron = sanitizar(datos.get("patron") or tarea, 200)
    if not patron:
        return None
    archivos = datos.get("archivos_afectados") or []
    if not isinstance(archivos, list):
        archivos = []
    dependencias = datos.get("dependencias") or []
    if not isinstance(dependencias, list):
        dependencias = []
    try:
        confianza = float(datos.get("confianza", 1.0))
    except (TypeError, ValueError):
        confianza = 1.0
    return {"patron": patron,
            "accion": sanitizar(datos.get("accion") or "", MAX_TEXTO),
            "archivos_afectados": [sanitizar(a, 300) for a in archivos
                                   if str(a).strip()][:10],
            "dependencias": [sanitizar(d, 300) for d in dependencias
                             if str(d).strip()][:10],
            "confianza": min(max(confianza, 0.0), 1.0)}


def extraer_regla(plan: dict, contexto: Optional[dict] = None) -> Dict[str, Any]:
    """Extrae una regla abstracta de un plan exitoso.

    Intenta con el LLM (JSON estructurado) y, ante cualquier fallo, usa la
    heurística de archivos más editados. Nunca lanza.
    """
    contexto = contexto or {}
    tarea = str(plan.get("tarea") or contexto.get("tarea") or "").strip()
    try:
        sc = _sc()
        pasos_resumen = []
        for paso in (plan.get("pasos") or [])[:15]:
            pasos_resumen.append({
                "tipo": paso.get("tipo") or paso.get("accion"),
                "archivo": paso.get("archivo") or paso.get("ruta"),
                "descripcion": paso.get("descripcion")})
        peticion = (
            "Analiza este plan de tarea completada con éxito y extrae UNA "
            "regla abstracta reutilizable (patrón de tarea → acción "
            "arquitectónica). Responde SOLO con JSON:\n"
            '{"patron": "...", "accion": "...", "archivos_afectados": [...],'
            ' "dependencias": [...]}\n\n'
            f"Tarea: {tarea}\n"
            f"Pasos: {json.dumps(pasos_resumen, ensure_ascii=False)}")
        cfg = sc.cargar_configuracion()
        proveedor = (contexto.get("proveedor")
                     or cfg.get("provider") or sc.PROVEEDOR_DEFECTO)
        respuesta = sc._enviar_al_proveedor(proveedor, None,
                                            [{"role": "user",
                                              "content": peticion}])
        regla = _parsear_regla_llm(str(respuesta), tarea)
        if regla is not None:
            return regla
    except Exception:                    # noqa: BLE001 — fallback silencioso
        pass
    return _extraer_regla_heuristica(plan, contexto or {})


# ---------------------------------------------------------------------------
# Aplicación de reglas a tareas nuevas
# ---------------------------------------------------------------------------
def _similitud_tarea(patron: str, tarea: str) -> float:
    """Similitud entre patrón y tarea: SequenceMatcher + solape de palabras."""
    patron_n = re.sub(r"\W+", " ", (patron or "").lower()).strip()
    tarea_n = re.sub(r"\W+", " ", (tarea or "").lower()).strip()
    if not patron_n or not tarea_n:
        return 0.0
    base = difflib.SequenceMatcher(None, patron_n, tarea_n).ratio()
    palabras_p = set(patron_n.split())
    palabras_t = set(tarea_n.split())
    solape = (len(palabras_p & palabras_t) / len(palabras_p)
              if palabras_p else 0.0)
    return max(base, solape * 0.9)


def aplicar_regla(regla: dict, tarea: str) -> Optional[List[dict]]:
    """Devuelve pasos sugeridos si ``tarea`` coincide con el patrón, si no None."""
    if not regla or not tarea:
        return None
    if _similitud_tarea(str(regla.get("patron") or ""), tarea) \
            < UMBRAL_COINCIDENCIA_TAREA:
        return None
    pasos: List[dict] = []
    for archivo in (regla.get("archivos_afectados") or []):
        pasos.append({"tipo": "editar", "archivo": str(archivo),
                      "descripcion": str(regla.get("accion")
                                         or "aplicar regla aprendida")})
    if not pasos:
        pasos.append({"tipo": "plan", "descripcion": str(regla.get("accion")
                                                         or regla.get("patron"))})
    return pasos


def regla_a_linea(regla: dict) -> str:
    """Serializa una regla como línea de texto (para CLAUDE.md/prompt)."""
    archivos = ", ".join(str(a) for a in
                         (regla.get("archivos_afectados") or [])[:5])
    linea = f"- Cuando la tarea sea '{regla.get('patron')}': " \
            f"{regla.get('accion') or 'aplicar la acción aprendida'}."
    if archivos:
        linea += f" Archivos implicados: {archivos}."
    deps = ", ".join(str(d) for d in (regla.get("dependencias") or [])[:5])
    if deps:
        linea += f" Dependencias: {deps}."
    return sanitizar(linea, 800)


# ---------------------------------------------------------------------------
# Persistencia (tabla `reglas`)
# ---------------------------------------------------------------------------
def guardar_regla(regla: dict, directorio: str = ".") -> Dict[str, Any]:
    """Guarda la regla en la tabla ``reglas``.

    Si ya existe una regla con patrón muy similar (≥ 0.85), incrementa su
    ``confianza`` (hacia 1.0) y ``usos`` en vez de duplicarla. Devuelve
    ``{"id": int|None, "nueva": bool, "confianza": float, "inyectada": bool}``.
    """
    sc = _sc()
    sc._db_init()
    patron = sanitizar(regla.get("patron") or "", 200)
    if not patron:
        return {"id": None, "nueva": False, "confianza": 0.0,
                "inyectada": False}
    filas = sc._db_query("SELECT id, patron, confianza, usos FROM reglas")
    for fila in filas:
        if difflib.SequenceMatcher(None, patron.lower(),
                                   str(fila["patron"]).lower()).ratio() >= 0.85:
            nueva_conf = min(1.0, float(fila["confianza"] or 1.0) + 0.05)
            usos = int(fila["usos"] or 0) + 1
            sc._db_ejecutar(
                "UPDATE reglas SET confianza = ?, usos = ? WHERE id = ?",
                (nueva_conf, usos, int(fila["id"])))
            regla_reforzada = dict(regla)
            regla_reforzada.update({"patron": fila["patron"],
                                    "confianza": nueva_conf})
            inyectada = False
            if nueva_conf > UMBRAL_CONFIANZA_INYECCION:
                inyectada = inyectar_en_claudemd(regla_reforzada, directorio)
            return {"id": int(fila["id"]), "nueva": False,
                    "confianza": nueva_conf, "inyectada": inyectada}
    rid = sc._db_insert(
        "INSERT INTO reglas (patron, accion, archivos_afectados, "
        "dependencias, confianza, usos) VALUES (?, ?, ?, ?, ?, 1)",
        (patron, sanitizar(regla.get("accion") or ""),
         json.dumps(regla.get("archivos_afectados") or [],
                    ensure_ascii=False),
         json.dumps(regla.get("dependencias") or [], ensure_ascii=False),
         float(regla.get("confianza", 1.0))))
    inyectada = False
    if float(regla.get("confianza", 1.0)) > UMBRAL_CONFIANZA_INYECCION:
        inyectada = inyectar_en_claudemd(regla, directorio)
    return {"id": rid, "nueva": True,
            "confianza": float(regla.get("confianza", 1.0)),
            "inyectada": inyectada}


def buscar_reglas(tarea: str, umbral: float = UMBRAL_COINCIDENCIA_TAREA,
                  max_reglas: int = 3) -> List[dict]:
    """Reglas cuya coincidencia con ``tarea`` supere ``umbral``,
    priorizadas por confianza (para enriquecer el prompt del planificador)."""
    sc = _sc()
    sc._db_init()
    resultados: List[dict] = []
    try:
        filas = sc._db_query(
            "SELECT * FROM reglas ORDER BY confianza DESC, usos DESC")
    except Exception:                    # noqa: BLE001 — tabla aún sin crear
        return []
    for fila in filas:
        regla = dict(fila)
        try:
            regla["archivos_afectados"] = json.loads(
                fila.get("archivos_afectados") or "[]")
            regla["dependencias"] = json.loads(
                fila.get("dependencias") or "[]")
        except (ValueError, TypeError):
            regla["archivos_afectados"] = []
            regla["dependencias"] = []
        if _similitud_tarea(str(regla.get("patron") or ""), tarea) >= umbral:
            resultados.append(regla)
        if len(resultados) >= max_reglas:
            break
    return resultados


# ---------------------------------------------------------------------------
# Inyección en CLAUDE.md / SNAPCONTEXT.md (idempotente)
# ---------------------------------------------------------------------------
def _memoria_proyecto(directorio: str):
    sc = _sc()
    try:
        camino = sc._buscar_claude_md(directorio)
        if camino is not None:
            return camino
    except AttributeError:
        pass
    from pathlib import Path
    for nombre in ("CLAUDE.md", "SNAPCONTEXT.md"):
        camino = Path(directorio) / nombre
        if camino.exists():
            return camino
    return Path(directorio) / "CLAUDE.md"


def inyectar_en_claudemd(regla: dict, directorio: str = ".") -> bool:
    """Añade la regla a ``## Reglas aprendidas`` en CLAUDE.md/SNAPCONTEXT.md.

    Crea la sección si no existe; no duplica reglas ya presentes. Devuelve
    ``True`` si el archivo se actualizó.
    """
    try:
        from pathlib import Path
        camino = _memoria_proyecto(directorio)
        linea = regla_a_linea(regla)
        if not linea.strip("- ").strip():
            return False
        contenido = camino.read_text(encoding="utf-8") if camino.exists() \
            else ""
        if linea in contenido:
            return False                 # idempotente: ya inyectada
        if SECCION_REGLAS in contenido:
            partes = contenido.split(SECCION_REGLAS, 1)
            cabeza = partes[0] + SECCION_REGLAS + "\n"
            resto = partes[1]
            fin = len(resto)
            m = re.search(r"\n## ", resto)
            if m:
                fin = m.start()
            nuevo = cabeza + resto[:fin].rstrip("\n") + "\n" + linea + "\n" \
                + resto[fin:]
        else:
            nuevo = (contenido.rstrip("\n") + "\n\n" + SECCION_REGLAS
                     + "\n\n" + linea + "\n") if contenido.strip() \
                else (SECCION_REGLAS + "\n\n" + linea + "\n")
        camino.parent.mkdir(parents=True, exist_ok=True)
        camino.write_text(nuevo, encoding="utf-8")
        return True
    except Exception:                    # noqa: BLE001 — nunca romper
        return False


def inyectar_todas_las_reglas(directorio: str = ".") -> int:
    """Fuerza la inyección de todas las reglas en CLAUDE.md. Devuelve cuántas
    líneas se añadieron (0 si ya estaban todas)."""
    sc = _sc()
    sc._db_init()
    try:
        filas = sc._db_query(
            "SELECT * FROM reglas ORDER BY confianza DESC")
    except Exception:                    # noqa: BLE001
        return 0
    añadidas = 0
    for fila in filas:
        regla = dict(fila)
        try:
            regla["archivos_afectados"] = json.loads(
                fila.get("archivos_afectados") or "[]")
            regla["dependencias"] = json.loads(
                fila.get("dependencias") or "[]")
        except (ValueError, TypeError):
            regla["archivos_afectados"] = []
            regla["dependencias"] = []
        if inyectar_en_claudemd(regla, directorio):
            añadidas += 1
    return añadidas

