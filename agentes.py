#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Módulo de Agentes de SnapContext.

Arquitectura de agentes (cada agente se centra en una responsabilidad del
pipeline y se coordina desde el Orquestador):

  - ``AgenteContexto``: analiza el repositorio y selecciona los archivos
    relevantes para la tarea. Es el paso "Contexto" (escaneo + selección IA).
  - ``AgenteEditor``  : recibe órdenes EXACTAS de qué cambiar y ejecuta Aider
    sobre los archivos seleccionados. No decide qué cambiar; solo aplica órdenes.
  - ``AgenteTester``  : ejecuta las pruebas y analiza el error devuelto para
    que el Editor corrija justo lo que falló.

Cada método importa de forma diferida las funciones ya probadas de
``snapcontext``, para conservar exactamente el comportamiento actual, y añade
logs de depuración visibles con ``--depurar`` (marcas ``[Agente…]``).
"""

import subprocess
from typing import List, Optional, Union


class AgenteContexto:
    """Agente de Contexto: selecciona los archivos relevantes para la consulta.

    No modifica código: solo elige qué archivos merece la pena abrir/editar.
    Compone el escaneo local (heurística) y la selección final con el proveedor
    de IA configurado.
    """

    def escanear_candidatos(
        self,
        consulta: str,
        directorio: str = ".",
        carpetas: Optional[List[str]] = None,
        max_candidatos: int = 80,
    ) -> List[str]:
        """Escanea ``directorio`` y devuelve los ``max_candidatos`` archivos más
        relevantes para ``consulta`` usando heurística local (sin llamar a la IA).

        Este es el sub-paso 1 del pipeline: reduce el repositorio a candidatos.
        """
        import snapcontext as sc

        sc.depurar(
            f"[AgenteContexto] Escaneando '{directorio}' "
            f"(carpetas={carpetas}, max_candidatos={max_candidatos})..."
        )
        resultado = sc.escanear_repositorio(
            consulta,
            directorio=directorio,
            carpetas=carpetas,
            max_candidatos=max_candidatos,
        )
        sc.depurar(
            f"[AgenteContexto] {len(resultado)} candidato(s) relevante(s) localmente."
        )
        return resultado

    def seleccionar_archivos(
        self,
        consulta: str,
        directorio: str,
        carpetas: Optional[List[str]],
        max_archivos: int,
        provider: str,
        modelo: str,
    ) -> List[str]:
        """Pipeline completo del agente de contexto: escanea el repositorio y
        pide al proveedor IA ``provider`` que se quede con las ``max_archivos``
        rutas más relevantes para ``consulta``.

        Devuelve la lista final (vacía si no hay candidatos).
        """
        import snapcontext as sc

        candidatos = self.escanear_candidatos(
            consulta,
            directorio=directorio,
            carpetas=carpetas,
            max_candidatos=max(max_archivos * 3, 1),
        )
        if not candidatos:
            sc.depurar("[AgenteContexto] Sin candidatos; no hay qué seleccionar.")
            return []

        sc.depurar(
            f"[AgenteContexto] Seleccionando {max_archivos} rutas "
            f"(proveedor='{provider}', modelo='{modelo}')..."
        )
        return sc.seleccionar_archivos(
            consulta,
            candidatos,
            proveedor=provider,
            modelo=modelo,
            max_archivos=max_archivos,
        )

# __PARTE2__
class AgenteEditor:
    """Agente Editor: recibe órdenes exactas y ejecuta Aider.

    No decide de forma autónoma qué cambiar: aplica el ``mensaje`` (normalmente
    la tarea original y, si viene del ``AgenteTester``, el error exacto) sobre
    los ``archivos`` indicados por el orquestador.
    """

    def ejecutar_aider(
        self,
        archivos: List[str],
        mensaje: str,
        directorio: str,
        opciones: str = "",
    ) -> bool:
        """Ejecuta Aider en ``directorio`` sobre ``archivos`` con ``mensaje``.

        Devuelve ``True`` si Aider terminó con código 0; ``False`` en otro caso.
        """
        import snapcontext as sc

        sc.depurar(
            f"[AgenteEditor] Aider sobre {len(archivos)} archivo(s) en '{directorio}'"
        )
        return sc.ejecutar_aider(archivos, mensaje, directorio, opciones)


class AgenteTester:
    """Agente Tester: ejecuta las pruebas y analiza los errores devueltos.

    Su salida alimenta al ``AgenteEditor`` para que corrija justo lo que falló,
    sin que Aider pierda de vista la tarea original.
    """

    def ejecutar_pruebas(
        self, comando: List[str], directorio: str
    ) -> "subprocess.CompletedProcess":
        """Ejecuta ``comando`` (lista de argv) en ``directorio`` capturando la salida.

        Devuelve el ``subprocess.CompletedProcess`` para poder analizarlo después.
        """
        import snapcontext as sc

        sc.depurar(f"[AgenteTester] Ejecutando pruebas: {' '.join(comando)}")
        return subprocess.run(
            comando, cwd=directorio, capture_output=True, text=True
        )

    def analizar_error(
        self, salida: Union["subprocess.CompletedProcess", str]
    ) -> str:
        """Normaliza la salida de unas pruebas que fallaron para entregársela a Aider.

        Acepta el ``CompletedProcess`` devuelto por :meth:`ejecutar_pruebas` o un
        ``str`` con la salida cruda. Limpia códigos ANSI y recorta demasiado
        texto para no llenar el contexto de Aider.
        """
        import snapcontext as sc

        if hasattr(salida, "stdout"):
            # Objeto CompletedProcess → reutilizamos el normalizador de snapcontext.
            return sc._extraer_error(salida)

        limpieza: str = salida or ""
        import re as _re

        limpieza = _re.sub(r"\x1b\[[0-9;]*m", "", limpieza)
        limpieza = limpieza.strip() or "(el comando de prueba no devolvió salida)"
        limite = getattr(sc, "MAX_ERROR_SALIDA", 4000)
        if len(limpieza) > limite:
            limpieza = "\n--- (salida recortada) ---\n" + limpieza[-limite:]
        return limpieza