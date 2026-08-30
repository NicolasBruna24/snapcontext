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

# v6.1.0 — Manejo de contexto inteligente: por encima de este umbral (tokens)
# se envía contexto selectivo (resumen AST + bloques) en lugar del archivo
# completo. Configurable desde la CLI con --max-context-tokens.
MAX_CONTEXT_TOKENS = 3000


def _es_error_contexto(exc: Exception) -> bool:
    """¿El error del proveedor parece de límite de contexto? (v6.1.0)

    Delega en :func:`context_utils.es_error_contexto` (implementación única,
    compartida con el planificador y otros módulos).
    """
    import context_utils
    return context_utils.es_error_contexto(exc)


def _tarea_estructura(tarea: str) -> bool:
    """Heurística simple para `auto`: ¿la tarea parece una refactorización estructural?

    Si es así, merece la pena intentar primero la edición basada en AST.
    Se normalizan acentos para tolerar variaciones ("renombra"/"renombrar", etc.).
    """
    if not tarea:
        return False
    import unicodedata as _u

    t = _u.normalize("NFD", tarea.lower())
    t = "".join(c for c in t if not _u.combining(c))
    claves = ("refactor", "renombra", "renombrar", "extra", "extraer",
              "mueve", "mover", "inserta", "insertar", "nueva funcion",
              "crear funcion", "funcion", "extraccion",
              # v5.6.0: símbolos estructurales multi-lenguaje (structs de Go/
              # Rust, clases Java/TS, métodos, campos…).
              "struct", "clase", "class", "metodo", "interface", "campo",
              "interfaz", "enum")
    return any(k in t for k in claves)


def _construir_prompt_edicion(modo: str, mensaje: str, archivo: str,
                              contenido_actual: str, lenguaje: str,
                              conciso: bool, error_msj: str = "",
                              truncado: Optional[bool] = None,
                              contexto: Optional[str] = None) -> tuple:
    """Construye el prompt de edición para el proveedor (v4.7.0 / v6.1.0).

    Devuelve ``(prompt, truncado)``:

    - Si el archivo tiene ≤ ``sc.MAX_CONTEXT_LINES`` líneas, se inyecta
      completo (comportamiento clásico de v4.x) y ``truncado=False``.
    - Si lo supera, se envía contexto selectivo (resumen AST + bloques
      relevantes vía ``sc._extraer_contexto_selectivo`` o ``contexto`` si el
      llamador ya seleccionó contexto por tokens en v6.1.0), ``truncado=True``
      y el formato de respuesta cambia:
        * modo "parche": diff unificado solo del bloque mostrado.
        * modo "sobrescribir": bloque entre marcadores ``<<<ANTES>>>`` /
          ``<<<DESPUES>>>`` / ``<<<FIN>>>`` para reinsertarlo en el original.

    Con ``truncado``/``contexto`` explícitos el llamador (v6.1.0) fuerza el
    modo reducido basado en tokens (``--max-context-tokens``) sin depender
    solo del número de líneas.
    """
    import snapcontext as sc

    num_lineas = (contenido_actual.count("\n") + 1) if contenido_actual else 0
    if truncado is None:
        max_lineas = int(getattr(sc, "MAX_CONTEXT_LINES", 600))
        truncado = num_lineas > max_lineas
    reintento = ""
    if error_msj:
        reintento = (
            f"\nTu intento anterior produjo un error de sintaxis que debes "
            f"corregir:\n{error_msj}\n\n"
            f"Mantén el objetivo de la tarea pero arregla ese error.\n")

    if modo == "parche":
        if truncado:
            if contexto is None:
                contexto = sc._extraer_contexto_selectivo(contenido_actual,
                                                          mensaje, archivo)
            prompt = (
                f"Genera un parche unificado (unified diff) que modifique SOLO "
                f"el bloque mostrado para cumplir con la tarea.\n"
                f"El parche debe ser aplicable con 'patch -p1' o 'git apply' "
                f"(encabezados --- a/{archivo} y +++ b/{archivo}).\n\n"
                f"Tarea: {mensaje}\n"
                f"Archivo: {archivo}  (lenguaje: {lenguaje}, "
                f"{num_lineas} líneas)\n\n"
                f"{contexto}\n\n"
                f"Devuelve ÚNICAMENTE el diff unificado, sin explicaciones."
            )
            return prompt + reintento, True
        if conciso:
            # v4.1.0: variante reducida para modelos ligeros (Ollama).
            prompt = (
                f"Tarea: {mensaje}\n"
                f"Archivo: {archivo} ({lenguaje}, {num_lineas} líneas)\n\n"
                f"```\n{contenido_actual}\n```\n\n"
                f"Responde SOLO el diff unificado (--- a/… +++ b/… con @@)."
            )
            return prompt + reintento, False
        prompt = (
            f"Genera un parche unificado (unified diff) que modifique el archivo "
            f"para cumplir con la tarea con precisión máxima.\n"
            f"El parche debe ser aplicable con 'patch -p1' o 'git apply' "
            f"(encabezados --- a/{archivo} y +++ b/{archivo}).\n\n"
            f"Tarea: {mensaje}\n"
            f"Archivo: {archivo}  (lenguaje: {lenguaje}, {num_lineas} líneas)\n\n"
            f"Instrucciones:\n"
            f"- Incluye suficiente contexto (3 líneas) para anclar cada hunk.\n"
            f"- Conserva el estilo existente (indentación, comillas).\n"
            f"- Modifica SOLO lo necesario; no reorganices el resto del archivo.\n"
            f"{reintento}"
            f"\nContenido actual completo:\n```{lenguaje}\n{contenido_actual}\n```\n\n"
            f"Devuelve ÚNICAMENTE el diff unificado, sin explicaciones ni rodeos."
        )
        return prompt, False

    # modo == "sobrescribir"
    if truncado:
        if contexto is None:
            contexto = sc._extraer_contexto_selectivo(contenido_actual,
                                                      mensaje, archivo)
        prompt = (
            f"Tarea: {mensaje}\n"
            f"El archivo {archivo} ({lenguaje}, {num_lineas} líneas) es "
            f"demasiado grande para mostrarlo entero.\n\n"
            f"{contexto}\n\n"
            f"Devuelve SOLO el bloque actualizado en este formato exacto:\n"
            f"<<<ANTES>>>\n(código ACTUAL del bloque a cambiar, copiado tal "
            f"cual del bloque mostrado)\n<<<DESPUES>>>\n(el mismo bloque ya "
            f"modificado para cumplir la tarea)\n<<<FIN>>>"
        )
        return prompt + reintento, True
    if conciso:
        # v4.1.0: variante reducida para modelos ligeros (Ollama).
        prompt = (
            f"Tarea: {mensaje}\n"
            f"Devuelve SOLO el código completo final de {archivo}:\n"
            f"```\n{contenido_actual}\n```"
        )
    else:
        prompt = (
            f"Modifica el siguiente archivo ({lenguaje}) para cumplir con la "
            f"tarea, conservando el estilo existente y cambiando solo lo necesario.\n\n"
            f"Tarea: {mensaje}\n"
            f"Archivo: {archivo}  (lenguaje: {lenguaje})\n"
            f"{reintento}"
            f"\nContenido actual completo:\n```{lenguaje}\n{contenido_actual}\n```\n\n"
            f"Devuelve ÚNICAMENTE el código completo resultante, sin explicaciones ni markdown."
        )
    return prompt, False

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


def _prompts_concisos(proveedor: Optional[str],
                      modelo_ligero: bool = False) -> bool:
    """v4.1.0: ¿usar prompts concisos? Con Ollama o ``--modelo-ligero``."""
    if modelo_ligero:
        return True
    return str(proveedor or "").strip().lower() == "ollama"


def _proveedor_efectivo(proveedor: Optional[str]) -> str:
    """Resuelve el proveedor: explícito → config.json → defecto."""
    import snapcontext as sc

    if proveedor:
        return proveedor
    try:
        return sc.cargar_configuracion().get("provider") or \
            sc.PROVEEDOR_DEFECTO
    except Exception:
        return sc.PROVEEDOR_DEFECTO


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
        contenido_esperado: Optional[str] = None,
    ) -> bool:
        """Aplica un parche unificado en ``directorio`` (v3.3.0).

        Con ``contenido_esperado`` se valida antes que el archivo coincida
        con el usado para generar el parche y, si hay conflicto, se intenta
        la resolución incremental línea a línea.

        Devuelve ``True`` si se aplicó con éxito, ``False`` si falló.
        """
        import snapcontext as sc

        sc.depurar(f"[AgenteEditorPropio] Aplicando parche en '{directorio}'")
        return sc._aplicar_parche_con_resolucion(
            parche, directorio=directorio,
            contenido_esperado=contenido_esperado)

    def _cadena_modos(self, archivo: str, mensaje: str,
                      modo_edicion: str) -> List[str]:
        """Devuelve la cadena de estrategias de edición a intentar para un archivo.

        - 'sobrescribir' → solo sobrescritura.
        - 'parche'       → solo parche unificado (sin fallback).
        - 'ast'          → AST y, si falla, sobrescritura.
        - 'auto'         → AST (si el lenguaje lo permite y la tarea es estructural),
                           luego parche y, por último, sobrescritura.
        """
        import snapcontext as sc

        if modo_edicion == "sobrescribir":
            return ["sobrescribir"]
        if modo_edicion == "parche":
            return ["parche"]
        if modo_edicion == "ast":
            return ["ast", "sobrescribir"]
        # auto: heurística simple según lenguaje y tarea
        if sc._ast_disponible(archivo) and _tarea_estructura(mensaje):
            return ["ast", "parche", "sobrescribir"]
        return ["parche", "sobrescribir"]

    def editar_ast(self, archivo: str, tarea: str,
                   directorio: str = ".", modelo: Optional[str] = None,
                   conciso: bool = False,
                   max_context_tokens: Optional[int] = None) -> bool:
        """Edita ``archivo`` con base en su AST usando el proveedor de IA.

        Desde v6.1.0 ``max_context_tokens`` permite recortar el contenido
        enviado al proveedor mediante :func:`context_utils.seleccionar_contexto`.
        """
        import snapcontext as sc

        pref = sc.cargar_configuracion()
        proveedor = pref.get("provider") or sc.PROVEEDOR_DEFECTO
        sc.depurar(f"[AgenteEditorPropio] Editando por AST '{archivo}' en '{directorio}'")
        return sc._editor_ast(archivo, tarea, directorio=directorio,
                              proveedor=proveedor, modelo=modelo,
                              conciso=conciso,
                              max_context_tokens=max_context_tokens)

    def _preparar_contenido_envio(self, archivo: str, mensaje: str,
                                  contenido_actual: str,
                                  max_context_tokens: Optional[int]):
        """Decide el contenido que se enviará al proveedor (v6.1.0).

        Basándose en el tamaño en tokens (``context_utils.estimar_tokens``),
        devuelve ``(contenido_a_enviar, truncado, objetivo, n_tokens)``:

        - si el archivo cabe en ``max_context_tokens`` → se envía completo;
        - si lo supera → se selecciona contexto selectivo (resumen AST +
          bloque objetivo + bloques relevantes) y se notifica al usuario.
        """
        import context_utils as ctx
        import snapcontext as sc

        limite = max_context_tokens if max_context_tokens is not None \
            else MAX_CONTEXT_TOKENS
        n = ctx.estimar_tokens(contenido_actual or "")
        if n <= int(limite or 0) or n == 0:
            return contenido_actual, False, None, n
        lenguaje = sc._lenguaje_archivo(archivo, contenido_actual) or "?"
        objetivo = ctx.objetivo_en_mensaje(contenido_actual, lenguaje, mensaje)
        reducido = ctx.seleccionar_contexto(
            contenido_actual, lenguaje, objetivo=objetivo,
            max_tokens=int(limite))
        if objetivo:
            sc.info(f"ℹ Archivo grande ({n} tokens). Usando contexto selectivo "
                    f"(bloque: '{objetivo}')...")
        else:
            sc.info(f"ℹ Archivo grande ({n} tokens). Usando contexto "
                    "selectivo...")
        return reducido, True, objetivo, n

    def _ejecutar_con_aider(self, archivos: List[str], mensaje: str,
                            directorio: str) -> bool:
        """Fallback automático a Aider cuando el editor propio falla (v6.1.0)."""
        import snapcontext as sc

        if not archivos:
            return True
        if shutil.which("aider") is None:
            sc.aviso("Aider no está instalado para el fallback. "
                     "Instálalo con:  pip install aider-chat")
            return False
        sc.aviso("⚠ El editor propio no pudo editar este archivo. "
                 "Usando Aider como fallback...")
        try:
            return sc.ejecutar_aider(archivos, mensaje, directorio)
        except Exception as exc:
            sc.error(f"Aider falló: {exc}")
            return False

    @staticmethod
    def _aplicar_parche_preview(parche: str, contenido_actual: str):
        """Aplica ``parche`` a ``contenido_actual`` **en memoria** para poder
        validar la sintaxis del resultado sin tocar el archivo real.

        Reutiliza la segmentación de hunks de ``_parsear_hunks`` y aplica cada
        hunk con tolerancia a pequeños desfases. Devuelve ``(resultado, n)``
        donde ``n`` es el número de hunks aplicados (0 ⇒ no se pudo reproducir).
        """
        import snapcontext as sc

        resultado = (contenido_actual or "").splitlines()
        desplazamiento = 0
        aplicados = 0
        for inicio_orig, cambios in sc._parsear_hunks(parche):
            base = max(inicio_orig - 1 + desplazamiento, 0)
            n_borrados = sum(1 for marca, _ in cambios if marca != "+")
            nuevos = [texto for marca, texto in cambios if marca != "-"]

            posicion = -1
            for delta in (0, 1, -1, 2, -2, 3, -3, 5, -5, 10, -10):
                candidato = base + delta
                if candidato < 0 or candidato > len(resultado):
                    continue
                idx = candidato
                encaja = True
                for marca, texto in cambios:
                    if marca == "+":
                        continue
                    if idx >= len(resultado) or resultado[idx] != texto:
                        encaja = False
                        break
                    idx += 1
                if encaja:
                    posicion = candidato
                    break
            if posicion < 0:
                continue

            resultado[posicion:posicion + n_borrados] = nuevos
            desplazamiento += len(nuevos) - n_borrados
            aplicados += 1

        return "\n".join(resultado) + "\n", aplicados

    def _aplicar_modo_parche(self, archivo: str, mensaje: str,
                             contenido_actual: str, modelo: Optional[str],
                             directorio: str, validar: bool = True,
                             max_intentos_validacion: int = 3,
                             conciso: bool = False, auto: bool = False,
                             max_context_tokens: Optional[int] = None) -> bool:
        """Intenta editar `archivo` pidiendo un parche unificado al proveedor.

        v6.1.0: si el archivo supera ``max_context_tokens`` se envía contexto
        selectivo; si el proveedor falla por límite de contexto, se reintenta
        con el archivo completo antes de declarar el fallo de esta estrategia.
        """
        import snapcontext as sc

        pref = sc.cargar_configuracion()
        proveedor = pref.get("provider") or sc.PROVEEDOR_DEFECTO
        lenguaje = sc._lenguaje_archivo(archivo, contenido_actual) or "?"
        num_lineas = (contenido_actual.count("\n") + 1
                      if contenido_actual else 0)
        max_val = max(1, int(max_intentos_validacion))

        # v6.1.0: contenido a enviar (completo o contexto selectivo por tokens).
        contenido_envio, truncado, _objetivo, _n_tokens = \
            self._preparar_contenido_envio(archivo, mensaje, contenido_actual,
                                           max_context_tokens)

        error_msj = ""
        for intento in range(1, max_val + 1):
            prompt, _truncado = _construir_prompt_edicion(
                "parche", mensaje, archivo, contenido_envio, lenguaje,
                conciso, error_msj=error_msj, truncado=truncado)
            try:
                respuesta = sc._enviar_al_proveedor(
                    proveedor, modelo, [{"role": "user", "content": prompt}])
            except RuntimeError as exc:
                if truncado and _es_error_contexto(exc):
                    # El modelo real puede tener más contexto que la estimación.
                    sc.info("⚠ El proveedor falló por límite de contexto. "
                            "Reintentando con el archivo completo...")
                    contenido_envio = contenido_actual
                    truncado = False
                    prompt_completo, _c = _construir_prompt_edicion(
                        "parche", mensaje, archivo, contenido_actual, lenguaje,
                        conciso, error_msj=error_msj, truncado=False)
                    respuesta = sc._enviar_al_proveedor(
                        proveedor, modelo,
                        [{"role": "user", "content": prompt_completo}])
                else:
                    sc.error(f"[EditorPropio] El proveedor falló en modo "
                             f"parche: {exc}")
                    return False
            diff_limpio = respuesta
            if "--- " in diff_limpio and "+++ " in diff_limpio:
                idx_inicio = diff_limpio.find("--- ")
                diff_limpio = diff_limpio[idx_inicio:]
                if "```" in diff_limpio:
                    diff_limpio = diff_limpio[:diff_limpio.find("```")]
            if not ("--- " in diff_limpio and "+++ " in diff_limpio
                    and "@@" in diff_limpio):
                return False

            if not validar:
                # v3.3.0: validación previa + resolución de conflictos.
                return self._aplicar_con_conflicto(
                    archivo, diff_limpio, directorio, contenido_actual,
                    preview=None, auto=auto) == "ok"

            # v3.4.0: validar la sintaxis del contenido resultante.
            preview, aplicados = self._aplicar_parche_preview(
                diff_limpio, contenido_actual)
            if aplicados == 0:
                # No se pudo reproducir en memoria → se omite la validación.
                return self._aplicar_con_conflicto(
                    archivo, diff_limpio, directorio, contenido_actual,
                    preview=None, auto=auto) == "ok"

            sc.info(f"Validando sintaxis de {archivo}...")
            exito, err = sc._validar_sintaxis(archivo, preview, directorio)
            if not exito:
                error_msj = err
                if intento < max_val:
                    sc.error(
                        f"Error de sintaxis: {err}. "
                        f"Reintentando ({intento}/{max_val})...")
                    continue
                sc.error(
                    f"No se pudo validar tras {max_val} intentos. "
                    f"Edición cancelada.")
                return False
            sc.exito("Sintaxis válida.")
            resultado = self._aplicar_con_conflicto(
                archivo, diff_limpio, directorio, contenido_actual,
                preview=preview, auto=auto)
            if resultado == "ok":
                return True
            if resultado == "cancelar":
                return False
            # "reintentar": el usuario pidió reintentar con el proveedor.
            error_msj = ("el parche no se aplicó limpiamente; "
                         "genera un diff corregido y completo")
            if intento >= max_val:
                return False
            sc.info(f"Reintentando parche ({intento}/{max_val})...")
        return False

    def _aplicar_con_conflicto(self, archivo: str, diff: str,
                               directorio: str, contenido_actual: str,
                               preview: Optional[str] = None,
                               auto: bool = False) -> str:
        """Aplica el parche resolviendo conflictos de forma interactiva.

        Devuelve ``"ok"``, ``"reintentar"`` o ``"cancelar"``.
        En modo ``--auto`` no pregunta: devuelve el resultado directo para que
        ``ejecutar()`` pase a la siguiente estrategia.
        """
        import snapcontext as sc

        ok = self.aplicar_parche(diff, directorio,
                                 contenido_esperado=contenido_actual)
        if ok or auto:
            return "ok" if ok else "reintentar"
        while True:
            opcion = sc._menu_conflicto_parche()
            if opcion == "a":
                # Aplicar de todas formas: usa el preview ya calculado si lo
                # hay; si no, reintenta el parche sin comprobación previa.
                if preview is not None:
                    return ("ok" if self.sobrescribir(archivo, preview,
                                                      directorio)
                            else "cancelar")
                ok2 = self.aplicar_parche(diff, directorio)
                if ok2:
                    return "ok"
                sc.error("El parche sigue sin aplicarse incluso sin "
                         "comprobación previa.")
                continue
            if opcion == "v":
                print("--- Diff propuesto ---")
                print(diff)
                print("----------------------")
                continue
            if opcion == "r":
                return "reintentar"
            sc.info(f"Se conserva la versión original de '{archivo}'.")
            return "cancelar"

    def _aplicar_modo_sobrescribir(self, archivo: str, mensaje: str,
                                   contenido_actual: str, modelo: Optional[str],
                                   directorio: str, validar: bool = True,
                                   max_intentos_validacion: int = 3,
                                   conciso: bool = False,
                                   max_context_tokens: Optional[int] = None) -> bool:
        """Sobrescribe `archivo` con el código completo que devuelve el proveedor.

        v6.1.0: los archivos grandes usan contexto selectivo (bloque objetivo)
        y, si el proveedor falla por límite de contexto, se reintenta con el
        archivo completo antes de fallar esta estrategia.
        """
        import snapcontext as sc

        pref = sc.cargar_configuracion()
        proveedor = pref.get("provider") or sc.PROVEEDOR_DEFECTO
        lenguaje = sc._lenguaje_archivo(archivo, contenido_actual) or "?"
        max_val = max(1, int(max_intentos_validacion))

        # v6.1.0: contenido a enviar (completo o contexto selectivo por tokens).
        contenido_envio, truncado, _objetivo, _n_tokens = \
            self._preparar_contenido_envio(archivo, mensaje, contenido_actual,
                                           max_context_tokens)

        error_msj = ""
        nuevo_contenido = ""
        for intento in range(1, max_val + 1):
            prompt, _t = _construir_prompt_edicion(
                "sobrescribir", mensaje, archivo, contenido_envio, lenguaje,
                conciso, error_msj=error_msj, truncado=truncado)
            try:
                respuesta = sc._enviar_al_proveedor(
                    proveedor, modelo, [{"role": "user", "content": prompt}])
            except RuntimeError as exc:
                if truncado and _es_error_contexto(exc):
                    # El modelo real puede tener más contexto que la estimación.
                    sc.info("⚠ El proveedor falló por límite de contexto. "
                            "Reintentando con el archivo completo...")
                    contenido_envio = contenido_actual
                    truncado = False
                    prompt_completo, _c = _construir_prompt_edicion(
                        "sobrescribir", mensaje, archivo, contenido_actual,
                        lenguaje, conciso, error_msj=error_msj, truncado=False)
                    respuesta = sc._enviar_al_proveedor(
                        proveedor, modelo,
                        [{"role": "user", "content": prompt_completo}])
                else:
                    sc.error(f"[EditorPropio] El proveedor falló en modo "
                             f"sobrescribir: {exc}")
                    return False
            nuevo_contenido = respuesta
            if nuevo_contenido.startswith("```"):
                lineas = nuevo_contenido.splitlines()
                if len(lineas) >= 2 and lineas[-1].startswith("```"):
                    nuevo_contenido = "\n".join(lineas[1:-1])
            if truncado:
                # v4.7.0: el modelo devuelve solo el bloque modificado entre
                # marcadores; se reinserta en el archivo original.
                import re as _re
                m = _re.search(
                    r"<<<ANTES>>>\s*\n(.*?)\n?\s*<<<DESPUES>>>\s*\n(.*?)\n?\s*<<<FIN>>>",
                    nuevo_contenido, _re.S)
                if m:
                    empotrado = sc._splicear_bloque(
                        contenido_actual, m.group(1), m.group(2))
                    if empotrado is not None:
                        nuevo_contenido = empotrado
                        sc.info("[EditorPropio] Bloque reinsertado en el "
                                "archivo original (contexto selectivo).")
                    else:
                        sc.error("[EditorPropio] No se pudo ubicar el bloque "
                                 "devuelto dentro del archivo original.")
                        nuevo_contenido = ""   # fuerza reintento/fallo limpio
                else:
                    sc.error("[EditorPropio] La respuesta no contenía los "
                             "marcadores <<<ANTES>>>/<<<DESPUES>>>/<<<FIN>>>.")
                    nuevo_contenido = ""
            # v4.7.0: NUNCA escribir una respuesta vacía (borraría el archivo).
            if not nuevo_contenido.strip():
                sc.error("[EditorPropio] Contenido resultante vacío; no se "
                         "escribe el archivo.")
                if intento < max_val:
                    error_msj = "la respuesta estaba vacía o no se pudo ubicar"
                    continue
                return False

            if not validar:
                break

            sc.info(f"Validando sintaxis de {archivo}...")
            exito, err = sc._validar_sintaxis(archivo, nuevo_contenido, directorio)
            if exito:
                sc.exito("Sintaxis válida.")
                break
            error_msj = err
            if intento < max_val:
                sc.error(
                    f"Error de sintaxis: {err}. "
                    f"Reintentando ({intento}/{max_val})...")
            else:
                sc.error(
                    f"No se pudo validar tras {max_val} intentos. "
                    f"Edición cancelada.")
                return False

        return self.sobrescribir(archivo, nuevo_contenido, directorio)

    def _analizar_impacto_previo(self, archivos, directorio, auto):
        """Análisis de Impacto Previo (v4.7.0).

        Usa ``sc._grafo_dependencias`` para detectar qué otros archivos del
        proyecto importan de los que se van a editar (enlaces con
        ``destino == archivo``). Devuelve la lista definitiva de archivos a
        editar —posiblemente ampliada con los dependientes que el usuario
        decida añadir— o ``None`` si el usuario aborta.

        Con ``auto=True`` (modo no interactivo) solo muestra la advertencia y
        continúa; nunca llama a ``input()``.
        """
        import snapcontext as sc

        try:
            grafo = sc._grafo_dependencias(str(directorio))
        except Exception as exc:
            sc.depurar(f"[impacto] Grafo de dependencias falló: {exc}")
            return list(archivos)
        enlaces = grafo.get("enlaces", []) or []
        objetivos = list(archivos)
        anadidos = []
        for arch in archivos:
            arch_norm = str(arch).replace("\\", "/").lstrip("./")
            if not arch_norm:
                continue
            dependientes = sorted({
                e.get("origen") for e in enlaces
                if str(e.get("destino") or "").replace("\\", "/").lstrip("./")
                == arch_norm
                and str(e.get("origen") or "").replace("\\", "/").lstrip("./")
                not in [str(a).replace("\\", "/").lstrip("./")
                        for a in archivos]})
            if not dependientes:
                continue
            lista = ", ".join(dependientes)
            sc.aviso(f"⚠️ Atención: El cambio en '{arch}' afecta a los "
                     f"siguientes archivos: [{lista}].")
            try:
                # v4.8.0: presentación Rich centralizada en ui.py. La lógica y
                # el contrato ('c'/'a'/'s') no cambian; solo la presentación.
                from ui import mostrar_tabla_impacto, preguntar_interactivo
                mostrar_tabla_impacto({arch: list(dependientes)})
            except Exception as exc:      # sin ui/rich → solo aviso plano
                sc.depurar(f"[impacto] UI Rich no disponible: {exc}")
            if auto:
                continue          # --auto: solo advierte y continúa.
            try:
                from ui import preguntar_interactivo
                respuesta = preguntar_interactivo(
                    None,
                    f"Cambio en '{arch}' con impacto cruzado en "
                    f"{len(dependientes)} archivo(s). ¿Qué quieres hacer?",
                    defecto="c")
            except Exception as exc:
                sc.depurar(f"[impacto] UI Rich no disponible: {exc}")
                respuesta = input(
                    "¿Continuar (c), abortar (a) o añadir los archivos "
                    "dependientes a esta edición (s)? [c/a/s] ").strip().lower()
            if respuesta.startswith("a"):
                return None
            if respuesta.startswith("s"):
                for dep in dependientes:
                    if dep not in objetivos:
                        objetivos.append(dep)
                        anadidos.append(dep)
        if anadidos:
            sc.info(f"[impacto] Archivos añadidos a la edición por impacto: "
                    f"{', '.join(anadidos)}")
        return objetivos

    def _editar_archivo_en_cadena(self, arch: str, mensaje: str,
                                  modo_edicion: str, estrategia_aprendida,
                                  raiz, modelo: Optional[str],
                                  conciso: bool, validar: bool,
                                  max_intentos_validacion: int,
                                  auto: bool,
                                  max_context_tokens: Optional[int] = None) -> bool:
        """Edita un único archivo recorriendo su cadena de estrategias.

        Lógica extraída de ``ejecutar()`` en v4.6.0 para soportar el rollback
        transaccional multiarchivo sin duplicar código.
        """
        import snapcontext as sc
        from pathlib import Path

        camino = (Path(raiz) / arch).resolve()
        contenido_actual = ""
        if camino.is_file():
            try:
                contenido_actual = camino.read_text(encoding="utf-8",
                                                    errors="replace")
            except Exception:
                pass

        cadena = self._cadena_modos(arch, mensaje, modo_edicion)
        # El skill aprendido se coloca el primero en la cadena.
        if estrategia_aprendida and estrategia_aprendida in cadena:
            cadena.remove(estrategia_aprendida)
            cadena.insert(0, estrategia_aprendida)
        conseguido = False
        for estrategia in cadena:
            sc.info(f"Editor propio: usando estrategia "
                    f"{estrategia.upper()} para '{arch}'...")
            try:
                if estrategia == "ast":
                    conseguido = self.editar_ast(arch, mensaje,
                                                 str(raiz), modelo,
                                                 conciso=conciso,
                                                 max_context_tokens=max_context_tokens)
                elif estrategia == "parche":
                    conseguido = self._aplicar_modo_parche(
                        arch, mensaje, contenido_actual, modelo, str(raiz),
                        validar=validar,
                        max_intentos_validacion=max_intentos_validacion,
                        conciso=conciso, auto=auto,
                        max_context_tokens=max_context_tokens)
                elif estrategia == "sobrescribir":
                    conseguido = self._aplicar_modo_sobrescribir(
                        arch, mensaje, contenido_actual, modelo, str(raiz),
                        validar=validar,
                        max_intentos_validacion=max_intentos_validacion,
                        conciso=conciso,
                        max_context_tokens=max_context_tokens)
                else:
                    conseguido = False
            except Exception as exc:
                sc.depurar(
                    f"[AgenteEditorPropio] {estrategia} falló para '{arch}': {exc}")
                conseguido = False
            if conseguido:
                break

        if conseguido:
            # v3.3.0 — Aprendizaje: guardar/reforzar el patrón de edición.
            patron = sc._editor_clasificar_tarea(mensaje)
            sid = sc._skill_editor_guardar(
                mensaje, arch, patron, estrategia=estrategia)
            if sid is not None:
                try:
                    sc._skill_registrar_exito(sid)
                except Exception as exc:
                    sc.depurar(f"[skills] Refuerzo falló: {exc}")
            return True

        # v4.1.0: mensaje claro con estrategias intentadas, motivo y
        # sugerencia; además se registra en ~/.snapcontext/logs/.
        estrategias_txt = " → ".join(cadena)
        sc.error(
            f"✖ El editor propio no pudo completar la edición.\n"
            f"  Archivo: {arch}\n"
            f"  Estrategias intentadas: {estrategias_txt}\n"
            f"  Motivo: ninguna estrategia produjo un cambio válido "
            f"(revisa la salida anterior).\n"
            f"  Sugerencia: prueba con '--editor aider' o revisa la "
            f"tarea manualmente.")
        try:
            sc._registrar_fallo_editor(arch, mensaje, cadena,
                                       "sin cambio válido")
        except Exception:
            pass
        return False

    def ejecutar(
        self,
        archivos: List[str],
        mensaje: str,
        directorio: str = ".",
        modo_edicion: str = "auto",
        modelo: Optional[str] = None,
        validar: bool = True,
        max_intentos_validacion: int = 3,
        proveedor: Optional[str] = None,
        modelo_ligero: bool = False,
        auto: bool = False,
        max_context_tokens: Optional[int] = None,
        editor_fallback: bool = False,
    ) -> bool:
        """Aplica las modificaciones para `archivos` con el `mensaje` especificado.

        - modo_edicion == 'parche': intenta aplicar parche unificado (falla si no puede).
        - modo_edicion == 'sobrescribir': aplica directamente sobrescritura de archivo.
        - modo_edicion == 'ast': edita con base en el AST y cae a sobrescritura si falla.
        - modo_edicion == 'auto': decide por heurística (AST para refactorizaciones,
          parche, y fallback a sobrescritura).

        v4.1.0:
        - Transparencia: informa la estrategia en uso antes de cada intento.
        - Prompts concisos automáticos con Ollama o ``modelo_ligero=True``.
        - Resolución interactiva de conflictos de parche (omitida con ``auto``).
        - Al fallar todo, error claro con estrategias intentadas + sugerencia
          ``--editor aider`` y registro del fallo en ~/.snapcontext/logs/.

        v6.1.0:
        - ``max_context_tokens``: si un archivo supera este umbral se usa
          contexto selectivo (resumen AST + bloque objetivo) en el prompt.
        - ``editor_fallback``: si el editor propio falla en todos los archivos,
          intenta automáticamente Aider (si está instalado) como respaldo.
        """
        import snapcontext as sc
        from pathlib import Path

        raiz = Path(directorio).resolve()
        todo_ok = True
        conciso = _prompts_concisos(
            _proveedor_efectivo(proveedor), modelo_ligero)
        if conciso:
            sc.info("Editor propio: usando prompts concisos (modelo ligero).")

        # v3.3.0 — Integración con skills: si ya existe un patrón de edición
        # aprendido para esta tarea, se prioriza su estrategia (sin pasar por
        # el proveedor de IA cuando el skill lo permita).
        estrategia_aprendida = None
        try:
            estrategia_aprendida = sc._skill_editor_estrategia(mensaje)
            if estrategia_aprendida:
                sc.info("[skills] Reutilizando patrón de edición aprendido: "
                        f"'{estrategia_aprendida}'.")
        except Exception as exc:
            sc.depurar(f"[skills] Búsqueda de skill falló: {exc}")

        # v4.7.0 — Análisis de Impacto Previo: advierte de archivos que
        # dependen de los que se van a editar y permite abortar o ampliar la
        # edición a esos dependientes (con --auto solo advierte).
        objetivos_impacto = self._analizar_impacto_previo(
            archivos, directorio, auto)
        if objetivos_impacto is None:
            sc.info("Edición cancelada por el usuario tras el análisis "
                    "de impacto.")
            return False
        archivos = objetivos_impacto

        # v4.6.0 — Snapshots para rollback transaccional: antes de tocar
        # nada se guarda el estado de cada archivo (contenido y existencia).
        # Si CUALQUIER archivo falla o se lanza una excepción, se restauran
        # TODOS los archivos a su estado original (atomicidad).
        snapshots = []                          # [(ruta_str, bytes, existia)]
        for arch in archivos:
            camino = (raiz / arch).resolve()
            existia = camino.is_file()
            contenido_bytes = b""
            if existia:
                try:
                    contenido_bytes = camino.read_bytes()
                except Exception as exc:
                    sc.depurar(f"[AgenteEditorPropio] No se pudo leer "
                               f"'{arch}' para el snapshot: {exc}")
            snapshots.append((str(camino), contenido_bytes, existia))

        fallo_excepcion = None
        fallidos: List[str] = []
        for arch in archivos:
            try:
                conseguido = self._editar_archivo_en_cadena(
                    arch, mensaje, modo_edicion, estrategia_aprendida,
                    raiz, modelo, conciso, validar,
                    max_intentos_validacion, auto,
                    max_context_tokens=max_context_tokens)
            except Exception as exc:
                sc.depurar(f"[AgenteEditorPropio] Excepción editando "
                           f"'{arch}': {exc}")
                conseguido = False
                fallo_excepcion = exc
            if not conseguido:
                todo_ok = False
                fallidos.append(arch)

        # v6.1.0 — Fallback automático a Aider cuando el editor propio falla.
        if fallidos and editor_fallback:
            if self._ejecutar_con_aider(fallidos, mensaje, str(raiz)):
                todo_ok = True        # Aider cubrió todos los fallidos.
                fallo_excepcion = None

        # v4.6.0 — Rollback transaccional: si algo falló, se restaura TODO.
        if not todo_ok or fallo_excepcion is not None:
            if fallo_excepcion is not None:
                sc.error(f"✖ Excepción durante la edición múltiple: "
                         f"{fallo_excepcion}")
            self._rollback(snapshots)
            return False

        return todo_ok

    @staticmethod
    def _rollback(snapshots) -> None:
        """Restaura todos los archivos a su estado previo a la edición (v4.6.0).

        ``snapshots`` es la lista construida en :meth:`ejecutar` con tuplas
        ``(ruta_str, contenido_bytes, existia)``. Los archivos que no existían
        se eliminan; los que sí, se reescriben con su contenido original.
        Nunca lanza excepciones (los errores se reportan sin abortar).
        """
        import snapcontext as sc
        from pathlib import Path

        sc.error("[EditorPropio] Edición incompleta; revirtiendo TODOS los "
                 "archivos al estado original (rollback v4.6.0)...")
        restaurados = 0
        for ruta_str, contenido_bytes, existia in snapshots:
            camino = Path(ruta_str)
            try:
                if not existia:
                    if camino.exists():
                        camino.unlink()
                else:
                    camino.write_bytes(contenido_bytes)
                restaurados += 1
            except Exception as exc:
                sc.error(f"[EditorPropio] No se pudo revertir '{ruta_str}': "
                         f"{exc}. Copia manual disponible en "
                         "~/.snapcontext/backups/.")
        if restaurados == len(snapshots):
            sc.info("[EditorPropio] Rollback completado: todos los archivos "
                    "fueron restaurados.")


class AgenteEditorAST:
    """Agente Editor AST (Fase 3): ediciones precisas basadas en árbol sintáctico.

    Trabaja directamente con el AST del archivo (Python con ``ast``; otros
    lenguajes con ``tree-sitter`` si está instalado) pidiendo al proveedor
    operaciones de modificación del árbol o el código completo resultante.
    """

    def editar(
        self,
        archivos: List[str],
        mensaje: str,
        directorio: str = ".",
        modelo: Optional[str] = None,
    ) -> bool:
        """Aplica a cada archivo una edición guiada por AST. Devuelve bool."""
        todo = True
        for archivo in archivos:
            if not self.editar_archivo(archivo, mensaje,
                                       directorio=directorio, modelo=modelo):
                todo = False
        return todo

    def editar_archivo(
        self,
        archivo: str,
        tarea: str,
        directorio: str = ".",
        modelo: Optional[str] = None,
    ) -> bool:
        """Edita un único archivo con AST. Devuelve ``True`` si tuvo éxito."""
        import snapcontext as sc

        pref = sc.cargar_configuracion()
        proveedor = pref.get("provider") or sc.PROVEEDOR_DEFECTO
        sc.depurar(f"[AgenteEditorAST] AST sobre '{archivo}' en '{directorio}'")
        return sc._editor_ast(archivo, tarea, directorio=directorio,
                              proveedor=proveedor, modelo=modelo)


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
        # v4.3.0: con --sandbox activo las pruebas corren en el contenedor.
        if sc.sandbox_activo():
            codigo, stdout, stderr = sc._ejecutar_pruebas_argv(
                comando, directorio)
            return subprocess.CompletedProcess(
                comando, codigo, stdout=stdout, stderr=stderr)
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


class AgenteAprendizaje:
    """Agente de Aprendizaje (v3.0.0): memoria persistente y skills.

    Encapsula la memoria SQLite de SnapContext (skills, historial de
    aprendizaje, contexto clave/valor) y el curador autónomo. Delega en las
    funciones del módulo :mod:`snapcontext` para mantener una única fuente
    de verdad; esta clase aporta la capa de alto nivel que consumen el
    orquestador y futuras integraciones (chat, web).
    """

    def inicializar(self) -> str:
        """Crea la base de datos de memoria si no existe. Devuelve su ruta."""
        import snapcontext as sc

        return sc._db_init()

    def buscar_skill(self, consulta: str,
                     umbral: float = 0.75) -> Optional[dict]:
        """Busca un skill similar a ``consulta`` (embeddings o fallback)."""
        import snapcontext as sc

        return sc._skill_buscar(consulta, umbral=umbral)

    def generar_skill(self, consulta: str, resultados: List[dict],
                      raiz: str = ".") -> Optional[int]:
        """Genera y guarda un skill a partir de una tarea exitosa."""
        import snapcontext as sc

        return sc._skill_generar(consulta, resultados, raiz=raiz)

    def registrar_exito(self, skill_id: int) -> float:
        """Refuerza un skill tras reutilizarlo con éxito."""
        import snapcontext as sc

        return sc._skill_registrar_exito(skill_id)

    def registrar_fallo(self, skill_id: int) -> float:
        """Penaliza un skill tras un fallo (queda marcado para revisión)."""
        import snapcontext as sc

        return sc._skill_registrar_fallo(skill_id)

    def aprender_de_tarea(self, consulta: str, todo_ok: bool,
                          resultados: List[dict], raiz: str = ".",
                          detalle: str = "") -> Optional[int]:
        """Gancho principal: registra la tarea y aprende de ella."""
        import snapcontext as sc

        return sc._aprender_de_tarea(consulta, todo_ok, resultados,
                                     raiz=raiz, detalle=detalle)

    def encolar_skill(self, skill_id: int) -> int:
        """Encola un skill para ejecución en segundo plano por el daemon."""
        import snapcontext as sc

        return sc._cola_encolar(skill_id)

    def curar(self, dias_sin_uso: int = 30,
              umbral_fusion: float = 0.90) -> dict:
        """Ejecuta el curador autónomo y devuelve el resumen de acciones."""
        import snapcontext as sc

        return sc._curador_ejecutar(dias_sin_uso=dias_sin_uso,
                                    umbral_fusion=umbral_fusion)

    def listar_skills(self, incluir_archivados: bool = False) -> List[dict]:
        """Lista los skills almacenados en la memoria persistente."""
        import snapcontext as sc

        return sc._skill_listar(incluir_archivados=incluir_archivados)


class AgenteAsesor:
    """Asesor de código proactivo (v3.5.0).

    Analiza el proyecto sin que el usuario lo pida explícitamente y propone
    mejoras (refactorizaciones, deuda técnica, patrones obsoletos...). Por
    defecto solo informa; las sugerencias marcadas como seguras pueden
    aplicarse con ``--asesor-auto`` y siempre se validan antes de guardarse.
    """

    def analizar(self, directorio: str = ".",
                 umbral_funcion: Optional[int] = None,
                 profundo: bool = False) -> List[dict]:
        """Ejecuta el análisis estático y devuelve la lista de sugerencias.

        Con ``profundo=True`` añade seguridad 🔒 y rendimiento ⚡ (v4.2.0).
        """
        import snapcontext as sc

        return sc._asesor_analizar(directorio, umbral_funcion=umbral_funcion,
                                   profundo=profundo)

    def analizar_seguridad(self, directorio: str = ".") -> List[dict]:
        """Solo vulnerabilidades de seguridad (v4.2.0)."""
        import snapcontext as sc

        return sc._analizar_seguridad(directorio)

    def analizar_rendimiento(self, directorio: str = ".") -> List[dict]:
        """Solo sugerencias de rendimiento (v4.2.0)."""
        import snapcontext as sc

        return sc._analizar_rendimiento(directorio)

    def mostrar(self, sugerencias: List[dict]) -> None:
        """Muestra las sugerencias en la CLI con colores."""
        import snapcontext as sc

        sc._asesor_mostrar(sugerencias)

    def aplicar_automaticas(self, sugerencias: List[dict],
                            directorio: str = ".") -> int:
        """Aplica las mejoras seguras (auto=True); devuelve cuántas se aplicaron."""
        import snapcontext as sc

        return sc._asesor_aplicar_automaticas(sugerencias, directorio)

    def ejecutar(self, args) -> int:
        """Flujo completo del modo CLI (--asesor / --asesor-auto)."""
        import snapcontext as sc

        return sc._ejecutar_asesor(args)