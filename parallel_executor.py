#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ejecutor genérico de tareas en paralelo (v6.20.0).

Sobre v6.13.0 (que ya dio `--paralelo` al planificador y
`ejecutar_sub_agentes_paralelo` a los sub-agentes), este módulo aporta un
**ejecutor genérico reutilizable** con:

- Grafo de dependencias: una tarea no arranca hasta que todas sus
  dependencias terminaron bien; si una dependencia falla, la tarea se marca
  ``omitida`` (idempotencia: los dependientes NUNCA se ejecutan).
- Límite de concurrencia (``max_workers``) con ``ThreadPoolExecutor``.
- Resultados en el MISMO orden que las tareas de entrada.
- Mensajes de usuario: ``🚀 Ejecutando N tareas en paralelo...``,
  ``✅ Tarea {nombre} completada`` y ``❌ Tarea {nombre} falló``.
- Nunca lanza: los fallos por tarea se capturan y se reportan en el
  resultado (``ok=False``), sin abortar al resto.

Uso::

    ex = ParallelExecutor(max_workers=4)
    resultados = ex.ejecutar_paralelo([
        {"nombre": "scout", "funcion": leer_docs, "dependencias": []},
        {"nombre": "reviewer", "funcion": revisar, "dependencias": ["scout"]},
    ])

Sin `--paralelo` (o con `--paralelo 1`) el resto del programa sigue su flujo
secuencial habitual: este ejecutor es totalmente opcional.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

import ui

__all__ = ["ParallelExecutor", "resolver_workers", "_ejecutar_tarea"]


def resolver_workers(paralelo: int) -> int:
    """Convierte el valor de ``--paralelo`` en nº de workers.

    - ``0`` → nº de núcleos de CPU (mínimo 2, para que valga la pena).
    - ``<0`` o inválido → 1 (secuencial, compatibilidad).
    - ``1`` → 1 (secuencial; el llamador decide si ni siquiera usa el pool).
    """
    try:
        n = int(paralelo)
    except (TypeError, ValueError):
        return 1
    if n == 0:
        return max(2, os.cpu_count() or 2)
    return max(1, n)


def _ejecutar_tarea(tarea: dict) -> dict:
    """Wrapper: ejecuta ``tarea["funcion"]`` y captura cualquier excepción.

    Devuelve un dict con ``nombre``, ``ok``, ``resultado``/``error`` y el
    tiempo empleado. Nunca lanza.
    """
    import time
    nombre = str(tarea.get("nombre") or "tarea")
    inicio = time.monotonic()
    try:
        funcion: Callable = tarea["funcion"]
        resultado = funcion(*tarea.get("args", ()), **tarea.get("kwargs", {}))
        return {"nombre": nombre, "ok": True, "resultado": resultado,
                "tiempo": time.monotonic() - inicio}
    except Exception as exc:                             # noqa: BLE001
        return {"nombre": nombre, "ok": False, "resultado": None,
                "error": str(exc), "tiempo": time.monotonic() - inicio}


class ParallelExecutor:
    """Ejecuta tareas con dependencias en paralelo limitado por workers."""

    def __init__(self, max_workers: int = 4):
        self.max_workers = resolver_workers(max_workers)

    # -- API principal ------------------------------------------------------

    def ejecutar_paralelo(self, tareas: List[dict]) -> List[dict]:
        """Ejecuta las tareas respetando dependencias y límite de workers.

        Cada tarea: ``{"nombre", "funcion", "args", "kwargs", "dependencias"}``
        (``dependencias`` = lista de nombres de tareas que deben terminar
        bien antes). Devuelve los resultados EN EL MISMO ORDEN que las
        tareas de entrada; las no ejecutables van con ``ok=False`` y
        ``estado="omitida"``.
        """
        if not tareas:
            return []
        if self.max_workers <= 1 or len(tareas) == 1:
            # Camino secuencial: mismo contrato, cero overhead de hilos.
            return self._ejecutar_secuencial(tareas)
        ui.mostrar_estado(
            f"🚀 Ejecutando {len(tareas)} tareas en paralelo "
            f"(máx. {self.max_workers})...", emoji="")
        return self._ejecutar_con_pool(tareas)

    # -- Internos -------------------------------------------------------------

    def _ejecutar_secuencial(self, tareas: List[dict]) -> List[dict]:
        resultados: List[Optional[dict]] = [None] * len(tareas)
        terminadas: Dict[str, bool] = {}
        ui.mostrar_estado(
            f"🚀 Ejecutando {len(tareas)} tareas en paralelo (secuencial)...",
            emoji="")
        for indice, tarea in enumerate(tareas):
            res = self._ejecutar_si_puede(tarea, terminadas, anunciar=True)
            resultados[indice] = res
            if not res["ok"] and res.get("estado") != "omitida":
                # Un fallo bloquea a sus dependientes (idempotencia v6.20.0).
                self._marcar_huerfanos(tarea, tareas, resultados)
        return [r for r in resultados if r is not None]      # type: ignore

    def _ejecutar_con_pool(self, tareas: List[dict]) -> List[dict]:
        resultados: List[Optional[dict]] = [None] * len(tareas)
        terminadas: Dict[str, bool] = {}
        pendientes = list(range(len(tareas)))
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futuros = {}
            while pendientes:
                lanzadas = []
                for indice in pendientes:
                    tarea = tareas[indice]
                    if self._dependencias_ok(tarea, terminadas):
                        futuros[pool.submit(
                            _ejecutar_tarea, tarea)] = indice
                        lanzadas.append(indice)
                for indice in lanzadas:
                    pendientes.remove(indice)
                if not futuros:
                    # Solo quedan tareas bloqueadas (ciclo o dependencia
                    # fallida): se marcan como omitidas y se sale.
                    break
                terminados = [f for f in futuros if f.done()]
                if not terminados:                          # pragma: no cover
                    import time
                    time.sleep(0.01)
                    continue
                for futuro in terminados:
                    indice = futuros.pop(futuro)
                    res = futuro.result()
                    resultados[indice] = res
                    nombre = res["nombre"]
                    if res["ok"]:
                        terminadas[nombre] = True
                        ui.mostrar_estado(
                            f"✅ Tarea {nombre} completada", emoji="")
                    else:
                        terminadas[nombre] = False
                        ui.mostrar_error(
                            f"❌ Tarea {nombre} falló: "
                            f"{res.get('error', 'desconocido')}")
                        self._marcar_huerfanos(
                            tareas[indice], tareas, resultados)
            # Recoge futuros lanzados que siguieron vivos al salir del while
            # (p. ej. dependientes desbloqueados en la última ronda).
            for futuro, indice in list(futuros.items()):
                res = futuro.result()
                resultados[indice] = res
                nombre = res["nombre"]
                if res["ok"]:
                    terminadas[nombre] = True
                    ui.mostrar_estado(
                        f"✅ Tarea {nombre} completada", emoji="")
                else:
                    ui.mostrar_error(
                        f"❌ Tarea {nombre} falló: "
                        f"{res.get('error', 'desconocido')}")
                futuros.pop(futuro)
            # Si nuevas tareas se desbloquearon, sigue el bucle.
        # Cierra tareas que nunca pudieron lanzarse (ciclo de dependencias).
        for indice, tarea in enumerate(tareas):
            if resultados[indice] is None:
                resultados[indice] = {
                    "nombre": str(tarea.get("nombre") or "tarea"),
                    "ok": False, "resultado": None, "error": None,
                    "estado": "omitida"}
        return [r for r in resultados if r is not None]      # type: ignore

    # -- Dependencias ----------------------------------------------------------

    @staticmethod
    def _dependencias_ok(tarea: dict, terminadas: Dict[str, bool]) -> bool:
        for dep in tarea.get("dependencias") or ():
            if dep not in terminadas:
                return False
            if not terminadas[dep]:
                return False
        return True

    def _ejecutar_si_puede(self, tarea: dict, terminadas: Dict[str, bool],
                           anunciar: bool = True) -> dict:
        nombre = str(tarea.get("nombre") or "tarea")
        faltantes = [d for d in (tarea.get("dependencias") or ())
                     if d not in terminadas or not terminadas[d]]
        if faltantes:
            return {"nombre": nombre, "ok": False, "resultado": None,
                    "error": f"dependencia no resuelta: {faltantes}",
                    "estado": "omitida"}
        res = _ejecutar_tarea(tarea)
        terminadas[nombre] = res["ok"]
        if anunciar:
            if res["ok"]:
                ui.mostrar_estado(
                    f"✅ Tarea {nombre} completada", emoji="")
            else:
                ui.mostrar_error(
                    f"❌ Tarea {nombre} falló: {res.get('error', '')}")
        return res

    @staticmethod
    def _marcar_huerfanos(tarea: dict, tareas: List[dict],
                          resultados: List[Optional[dict]]) -> None:
        """Marca como omitidas las tareas que dependen (directa o
        transitivamente) de una tarea fallida — idempotencia v6.20.0."""
        huerfanos = {str(tarea.get("nombre") or "tarea")}
        changed = True
        while changed:
            changed = False
            for indice, otra in enumerate(tareas):
                if resultados[indice] is not None:
                    continue
                for dep in otra.get("dependencias") or ():
                    if dep in huerfanos:
                        resultados[indice] = {
                            "nombre": str(otra.get("nombre") or "tarea"),
                            "ok": False, "resultado": None,
                            "error": f"dependencia fallida: {dep}",
                            "estado": "omitida"}
                        huerfanos.add(str(otra.get("nombre") or "tarea"))
                        changed = True
                        break
