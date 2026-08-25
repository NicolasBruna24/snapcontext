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
              "crear funcion", "funcion", "extraccion")
    return any(k in t for k in claves)

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
                   directorio: str = ".", modelo: Optional[str] = None) -> bool:
        """Edita ``archivo`` con base en su AST usando el proveedor de IA."""
        import snapcontext as sc

        pref = sc.cargar_configuracion()
        proveedor = pref.get("provider") or sc.PROVEEDOR_DEFECTO
        sc.depurar(f"[AgenteEditorPropio] Editando por AST '{archivo}' en '{directorio}'")
        return sc._editor_ast(archivo, tarea, directorio=directorio,
                              proveedor=proveedor, modelo=modelo)

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
                             max_intentos_validacion: int = 3) -> bool:
        """Intenta editar `archivo` pidiendo un parche unificado al proveedor."""
        import snapcontext as sc

        pref = sc.cargar_configuracion()
        proveedor = pref.get("provider") or sc.PROVEEDOR_DEFECTO
        lenguaje = sc._lenguaje_archivo(archivo, contenido_actual) or "?"
        num_lineas = (contenido_actual.count("\n") + 1
                      if contenido_actual else 0)
        max_val = max(1, int(max_intentos_validacion))

        error_msj = ""
        for intento in range(1, max_val + 1):
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
            )
            if error_msj:
                prompt += (
                    f"\nTu intento anterior produjo un error de sintaxis que debes "
                    f"corregir:\n{error_msj}\n\n"
                    f"Mantén el objetivo de la tarea pero arregla ese error.\n"
                )
            prompt += (
                f"\nContenido actual completo:\n```{lenguaje}\n{contenido_actual}\n```\n\n"
                f"Devuelve ÚNICAMENTE el diff unificado, sin explicaciones ni rodeos."
            )
            respuesta = sc._enviar_al_proveedor(
                proveedor, modelo, [{"role": "user", "content": prompt}])
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
                return self.aplicar_parche(diff_limpio, directorio,
                                           contenido_esperado=contenido_actual)

            # v3.4.0: validar la sintaxis del contenido resultante.
            preview, aplicados = self._aplicar_parche_preview(
                diff_limpio, contenido_actual)
            if aplicados == 0:
                # No se pudo reproducir en memoria → se omite la validación.
                return self.aplicar_parche(diff_limpio, directorio,
                                           contenido_esperado=contenido_actual)

            sc.info(f"Validando sintaxis de {archivo}...")
            exito, err = sc._validar_sintaxis(archivo, preview, directorio)
            if exito:
                sc.exito("Sintaxis válida.")
                return self.aplicar_parche(diff_limpio, directorio,
                                           contenido_esperado=contenido_actual)

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
        return False

    def _aplicar_modo_sobrescribir(self, archivo: str, mensaje: str,
                                   contenido_actual: str, modelo: Optional[str],
                                   directorio: str, validar: bool = True,
                                   max_intentos_validacion: int = 3) -> bool:
        """Sobrescribe `archivo` con el código completo que devuelve el proveedor."""
        import snapcontext as sc

        pref = sc.cargar_configuracion()
        proveedor = pref.get("provider") or sc.PROVEEDOR_DEFECTO
        lenguaje = sc._lenguaje_archivo(archivo, contenido_actual) or "?"
        max_val = max(1, int(max_intentos_validacion))

        error_msj = ""
        nuevo_contenido = ""
        for intento in range(1, max_val + 1):
            prompt = (
                f"Modifica el siguiente archivo ({lenguaje}) para cumplir con la "
                f"tarea, conservando el estilo existente y cambiando solo lo necesario.\n\n"
                f"Tarea: {mensaje}\n"
                f"Archivo: {archivo}  (lenguaje: {lenguaje})\n"
            )
            if error_msj:
                prompt += (
                    f"\nTu intento anterior produjo un error de sintaxis que debes "
                    f"corregir:\n{error_msj}\n\n"
                    f"Mantén el objetivo de la tarea pero arregla ese error.\n"
                )
            prompt += (
                f"\nContenido actual completo:\n```{lenguaje}\n{contenido_actual}\n```\n\n"
                f"Devuelve ÚNICAMENTE el código completo resultante, sin explicaciones ni markdown."
            )
            respuesta = sc._enviar_al_proveedor(
                proveedor, modelo, [{"role": "user", "content": prompt}])
            nuevo_contenido = respuesta
            if nuevo_contenido.startswith("```"):
                lineas = nuevo_contenido.splitlines()
                if len(lineas) >= 2 and lineas[-1].startswith("```"):
                    nuevo_contenido = "\n".join(lineas[1:-1])

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

    def ejecutar(
        self,
        archivos: List[str],
        mensaje: str,
        directorio: str = ".",
        modo_edicion: str = "auto",
        modelo: Optional[str] = None,
        validar: bool = True,
        max_intentos_validacion: int = 3,
    ) -> bool:
        """Aplica las modificaciones para `archivos` con el `mensaje` especificado.

        - modo_edicion == 'parche': intenta aplicar parche unificado (falla si no puede).
        - modo_edicion == 'sobrescribir': aplica directamente sobrescritura de archivo.
        - modo_edicion == 'ast': edita con base en el AST y cae a sobrescritura si falla.
        - modo_edicion == 'auto': decide por heurística (AST para refactorizaciones,
          parche, y fallback a sobrescritura).
        """
        import snapcontext as sc
        from pathlib import Path

        raiz = Path(directorio).resolve()
        todo_ok = True

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

        for arch in archivos:
            camino = (raiz / arch).resolve()
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
                try:
                    if estrategia == "ast":
                        conseguido = self.editar_ast(arch, mensaje,
                                                     str(raiz), modelo)
                    elif estrategia == "parche":
                        conseguido = self._aplicar_modo_parche(
                            arch, mensaje, contenido_actual, modelo, str(raiz),
                            validar=validar,
                            max_intentos_validacion=max_intentos_validacion)
                    elif estrategia == "sobrescribir":
                        conseguido = self._aplicar_modo_sobrescribir(
                            arch, mensaje, contenido_actual, modelo, str(raiz),
                            validar=validar,
                            max_intentos_validacion=max_intentos_validacion)
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
            else:
                sc.error(f"[AgenteEditorPropio] No se pudo editar '{arch}'.")
                todo_ok = False

        return todo_ok


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
                 umbral_funcion: Optional[int] = None) -> List[dict]:
        """Ejecuta el análisis estático y devuelve la lista de sugerencias."""
        import snapcontext as sc

        return sc._asesor_analizar(directorio, umbral_funcion=umbral_funcion)

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