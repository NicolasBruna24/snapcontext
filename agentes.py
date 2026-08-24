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
        extensiones: Optional[List[str]] = None,
        max_candidatos: int = 80,
    ) -> List[str]:
        """Escanea ``directorio`` y devuelve los ``max_candidatos`` archivos más
        relevantes para ``consulta`` usando heurística local (sin llamar a la IA).

        ``extensiones`` (opcional) restringe el escaneo a determinadas
        extensiones (p. ej. ``[\".dart\"]``).

        Este es el sub-paso 1 del pipeline: reduce el repositorio a candidatos.
        """
        import snapcontext as sc

        sc.depurar(
            f"[AgenteContexto] Escaneando '{directorio}' "
            f"(carpetas={carpetas}, extensiones={extensiones}, "
            f"max_candidatos={max_candidatos})..."
        )
        resultado = sc.escanear_repositorio(
            consulta,
            directorio=directorio,
            carpetas=carpetas,
            extensiones=extensiones,
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
        extensiones: Optional[List[str]] = None,
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
            extensiones=extensiones,
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


class AgenteEditorPropio:
    """Agente Editor Propio: editor integrado de SnapContext.

    Aplica cambios directamente sobre el sistema de archivos sin depender de
    herramientas externas obligatorias.
    - Fase 1: Sobrescritura segura con copia de seguridad previa en ``~/.snapcontext/backups/``.
    - Fase 2: Generación y aplicación de parches unificados (unified diffs) con ``git apply`` / ``patch``
      y fallback automático a sobrescritura.
    """

    def sobrescribir(
        self,
        archivo: str,
        contenido: str,
        directorio: str = ".",
    ) -> bool:
        """Sobrescribe ``archivo`` con ``contenido`` en ``directorio``.

        Devuelve ``True`` si la escritura fue exitosa, ``False`` si falló.
        """
        import snapcontext as sc

        sc.depurar(
            f"[AgenteEditorPropio] Sobrescribiendo '{archivo}' en '{directorio}'"
        )
        return sc._editor_sobrescribir(archivo, contenido, directorio=directorio)

    def aplicar_parche(
        self,
        parche: str,
        directorio: str = ".",
    ) -> bool:
        """Aplica un parche unificado en ``directorio``.

        Devuelve ``True`` si se aplicó con éxito, ``False`` si falló.
        """
        import snapcontext as sc

        sc.depurar(f"[AgenteEditorPropio] Aplicando parche en '{directorio}'")
        return sc._aplicar_parche(parche, directorio=directorio)

    def ejecutar(
        self,
        archivos: List[str],
        mensaje: str,
        directorio: str = ".",
        modo_edicion: str = "auto",
        modelo: Optional[str] = None,
    ) -> bool:
        """Aplica las modificaciones para `archivos` con el `mensaje` especificado.

        - modo_edicion == 'parche': intenta aplicar parche unificado (falla si no puede).
        - modo_edicion == 'sobrescribir': aplica directamente sobrescritura de archivo.
        - modo_edicion == 'auto': intenta primero parche unificado; si falla o no es diff,
          hace fallback a sobrescritura.
        """
        import snapcontext as sc
        from pathlib import Path

        pref = sc.cargar_configuracion()
        proveedor = pref.get("provider") or sc.PROVEEDOR_DEFECTO
        raiz = Path(directorio).resolve()
        todo_ok = True

        for arch in archivos:
            camino = (raiz / arch).resolve()
            contenido_actual = ""
            if camino.is_file():
                try:
                    contenido_actual = camino.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

            if modo_edicion in ("parche", "auto"):
                prompt = (
                    f"Genera un parche unificado (unified diff) que modifique el archivo para cumplir con la tarea. "
                    f"El parche debe ser aplicable con 'patch -p1' o 'git apply' (encabezados --- a/{arch} y +++ b/{arch}).\n\n"
                    f"Tarea: {mensaje}\n"
                    f"Archivo: {arch}\n\n"
                    f"Contenido actual:\n```\n{contenido_actual}\n```\n\n"
                    f"Devuelve ÚNICAMENTE el diff unificado, sin explicaciones ni rodeos."
                )
                try:
                    respuesta = sc._enviar_al_proveedor(
                        proveedor, modelo,
                        [{"role": "user", "content": prompt}]
                    )
                    # Limpiar delimitadores markdown si vinieron
                    diff_limpio = respuesta
                    if "--- " in diff_limpio and "+++ " in diff_limpio:
                        # Extraer solo desde ---
                        idx_inicio = diff_limpio.find("--- ")
                        diff_limpio = diff_limpio[idx_inicio:]
                        if "```" in diff_limpio:
                            diff_limpio = diff_limpio[:diff_limpio.find("```")]

                    # Si parece un diff unificado válido, intentar aplicarlo
                    if ("--- " in diff_limpio and "+++ " in diff_limpio and "@@" in diff_limpio):
                        if self.aplicar_parche(diff_limpio, str(raiz)):
                            continue
                        sc.aviso(f"[AgenteEditorPropio] Parche para '{arch}' falló al aplicarse.")
                        if modo_edicion == "parche":
                            todo_ok = False
                            continue
                except Exception as exc:
                    sc.depurar(f"[AgenteEditorPropio] Error pidiendo parche para '{arch}': {exc}")
                    if modo_edicion == "parche":
                        sc.error(f"Error generando parche para {arch}: {exc}")
                        todo_ok = False
                        continue

            # Fallback o modo sobrescribir
            sc.depurar(f"[AgenteEditorPropio] Aplicando edición por sobrescritura para '{arch}'...")
            prompt_sobrescribir = (
                f"Modifica el siguiente archivo para cumplir con la tarea.\n\n"
                f"Tarea: {mensaje}\n"
                f"Archivo: {arch}\n\n"
                f"Contenido actual:\n```\n{contenido_actual}\n```\n\n"
                f"Devuelve ÚNICAMENTE el código completo resultante, sin explicaciones ni markdown."
            )
            try:
                nuevo_contenido = sc._enviar_al_proveedor(
                    proveedor, modelo,
                    [{"role": "user", "content": prompt_sobrescribir}]
                )
                if nuevo_contenido.startswith("```"):
                    lineas = nuevo_contenido.splitlines()
                    if len(lineas) >= 2 and lineas[-1].startswith("```"):
                        nuevo_contenido = "\n".join(lineas[1:-1])
                if not self.sobrescribir(arch, nuevo_contenido, str(raiz)):
                    todo_ok = False
            except Exception as exc:
                sc.error(f"Error generando cambios por sobrescritura para {arch}: {exc}")
                todo_ok = False

        return todo_ok




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