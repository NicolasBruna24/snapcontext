#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orquestador de SnapContext.

Replica el flujo de ``flujo_principal`` (de ``snapcontext``) pero coordinando
los agentes del módulo :mod:`agentes`. El Orquestador planifica, decide el modo
de ejecución (Aider directo, pruebas o bucle con servidor) y delega el trabajo
en los agentes:

  - :class:`~agentes.AgenteContexto`  → escanea y selecciona archivos.
  - :class:`~agentes.AgenteEditor`    → ejecuta Aider con órdenes exactas.
  - :class:`~agentes.AgenteTester`    → ejecuta pruebas y analiza errores.

La entrada CLI (parsing de argumentos) sigue viviendo en ``snapcontext``; este
módulo solo aporta la capa de orquestación.
"""

import shlex
import sys
from typing import List, Optional, Tuple

from agentes import (AgenteContexto, AgenteEditor, AgenteEditorAST,
                     AgenteEditorPropio, AgenteAprendizaje, AgenteTester)

# Centinela diferenciado del None de "aborto": _planificar lo devuelve cuando
# el pipeline termina de forma exitosa y temprana (p. ej. --vista-previa).
VISTA_PREVIA = "vista_previa"


class Orquestador:
    """Coordina a los agentes y expone :meth:`ejecutar_flujo` (pipeline CLI)."""

    def __init__(self, evento_callback=None) -> None:
        """Crea el orquestador.

        ``evento_callback`` es un callable opcional ``fn(dict)`` que recibe los
        eventos de la ejecución (``log``, ``selección``, ``aider``, ``test``,
        ``final``). Lo usa la interfaz web para mostrar el avance en tiempo real.
        """
        self.agente_contexto = AgenteContexto()
        self.agente_editor = AgenteEditor()
        self.agente_editor_propio = AgenteEditorPropio()
        self.agente_editor_ast = AgenteEditorAST()
        self.agente_tester = AgenteTester()
        self.agente_aprendizaje = AgenteAprendizaje()
        self.evento_callback = evento_callback

    def _on_evento(self, evento: dict) -> None:
        """Reenvía un evento al callback registrado, ignorando errores del consumidor."""
        if self.evento_callback is None:
            return
        try:
            self.evento_callback(evento)
        except Exception:
            pass

    def _emitir_tipo(self, tipo: str, **datos) -> None:
        """Emite un evento tipado (selección/aider/test/final) hacia el callback."""
        self._on_evento({"tipo": tipo, **datos})

    # ------------------------------------------------------------------
    # Modo de pruebas: coreografía Editor → Tester → (si falla) → Editor
    # ------------------------------------------------------------------
    def _bucle_test(
        self,
        consulta: str,
        archivos: List[str],
        directorio: str,
        opciones_aider: str,
        comando_test: List[str],
        max_iteraciones: int,
    ) -> bool:
        """Bucle de pruebas con la arquitectura de agentes.

        En cada iteración, el ``AgenteEditor`` aplica la orden (primera vez la
        tarea; después la tarea + el error analizado por ``AgenteTester``) y el
        ``AgenteTester`` ejecuta el comando de prueba. Si superan, termina.
        """
        import snapcontext as sc

        if not comando_test:
            raise RuntimeError("El comando de pruebas está vacío (--comando-test).")

        ultimo_error = ""
        self._emitir_tipo("test_inicio", comando=" ".join(comando_test),
                          max_iteraciones=max_iteraciones)
        for iteracion in range(1, max_iteraciones + 1):
            sc.info(f"Iteración {iteracion} de {max_iteraciones} — Aider...")
            self._emitir_tipo("test", iteracion=iteracion, accion="aider",
                              comando=" ".join(comando_test))
            if iteracion == 1 or not ultimo_error:
                mensaje = consulta
            else:
                # Devolvemos el error real de la iteración anterior para que
                # Aider repare el código sin perder de vista la tarea original.
                mensaje = (
                    f"La tarea original era:\n{consulta}\n\n"
                    f"El comando de prueba falló en la iteración {iteracion - 1} con:\n"
                    f"```\n{ultimo_error}\n```\n"
                    "Corrige esos errores sin cambiar el alcance de la tarea original."
                )

            self.agente_editor.ejecutar_aider(
                archivos, mensaje, directorio, opciones_aider
            )

            sc.info(f"Ejecutando pruebas: {' '.join(comando_test)}")
            resultado = self.agente_tester.ejecutar_pruebas(comando_test, directorio)
            superado = resultado.returncode == 0
            self._emitir_tipo("test", iteracion=iteracion, accion="prueba",
                              ok=superado, comando=" ".join(comando_test))
            if superado:
                sc.exito(f"¡Pruebas superadas en la iteración {iteracion}!")
                self._emitir_tipo("test_fin", ok=True, iteracion=iteracion)
                return True

            ultimo_error = self.agente_tester.analizar_error(resultado)
            sc.aviso(
                f"Pruebas fallidas (código {resultado.returncode}). "
                "Se envía el error a Aider para que lo corrija..."
            )

        sc.error(
            f"No se consiguió que las pruebas pasaran tras {max_iteraciones} iteraciones."
        )
        self._emitir_tipo("test_fin", ok=False, iteracion=max_iteraciones)
        return False

# __M2__
    # ------------------------------------------------------------------
    # Pipeline principal
    # ------------------------------------------------------------------
    def ejecutar_flujo(self, args) -> int:
        """Replica ``flujo_principal`` coordinando a los agentes.

        Devuelve el código de salida (0 = éxito, 1 = error). Si se construyó con
        un ``evento_callback``, emite los eventos ``selección``/``aider``/
        ``test``/``final`` además de reenviar cada log del pipeline.
        """
        # Import diferido para evitar ciclos: snapcontext no estará importado aún
        # cuando se construye este orquestador desde ``flujo_principal``.
        import snapcontext as sc

        if self.evento_callback is not None:
            # Los logs del pipeline (info/aviso/error/…) se reenvían hacia la web.
            sc.fijar_evento_callback(self._on_evento)
        try:
            plan = self._planificar(args, sc)
            if plan == VISTA_PREVIA:
                self._emitir_tipo("final", ok=True, nota="vista previa")
                return 0
            if plan is None:
                self._emitir_tipo("final", ok=False, nota="flujo abortado")
                return 1

            consulta, raiz, carpeta, seleccion = plan
            sc.depurar(
                f"[Orquestador] Plan listo: {len(seleccion)} archivo(s) a usar."
            )

            # 3) Ejecución (Aider directo, pruebas o bucle con servidor Flutter)
            sc._emitir(sys.stdout, "")
            if args.server_loop or args.manual_loop:
                sc.depurar("[Orquestador] Modo bucle con servidor (flutter run).")
                ok = sc.ejecutar_bucle_agente(
                    consulta, seleccion,
                    modo="auto" if args.server_loop else "manual",
                    max_intentos=args.max_intentos,
                    directorio=str(raiz), opciones_aider=args.aider_opciones,
                    dispositivo=args.dispositivo, url_defecto=args.url_defecto,
                )
            elif args.test_loop:
                sc.depurar("[Orquestador] Modo bucle de pruebas (Editor + Tester).")
                ok = self._bucle_test(
                    consulta, seleccion, str(raiz),
                    opciones_aider=args.aider_opciones,
                    comando_test=shlex.split(args.comando_test),
                    max_iteraciones=max(args.max_iteraciones, 1),
                )
            else:
                editor_tipo = getattr(args, "editor", "aider") or "aider"
                if editor_tipo == "propio":
                    sc.depurar("[Orquestador] Modo edición directa (AgenteEditorPropio).")
                    self._emitir_tipo("editor", accion="iniciar", archivos=seleccion)
                    modo_ed = getattr(args, "modo_edicion", "auto") or "auto"
                    ok = self.agente_editor_propio.ejecutar(
                        seleccion,
                        consulta,
                        directorio=str(raiz),
                        modo_edicion=modo_ed,
                        modelo=getattr(args, "modelo", None),
                        validar=getattr(args, "validar", True),
                        max_intentos_validacion=getattr(
                            args, "max_intentos_validacion", 3),
                    )
                    self._emitir_tipo("editor", accion="fin", ok=ok)
                else:
                    sc.depurar("[Orquestador] Modo edición directa (AgenteEditor).")
                    self._emitir_tipo("aider", accion="iniciar", archivos=seleccion)
                    ok = self.agente_editor.ejecutar_aider(
                        seleccion, consulta, str(raiz),
                        opciones_aider=args.aider_opciones,
                    )
                    self._emitir_tipo("aider", accion="fin", ok=ok)
            self._emitir_tipo("final", ok=ok)
            # Aprendizaje continuo (v3.0.0): registrar el resultado de la
            # tarea en la memoria persistente y generar/reforzar skills.
            try:
                if not getattr(args, "sin_aprendizaje", False):
                    self.agente_aprendizaje.aprender_de_tarea(
                        consulta, bool(ok),
                        [{"descripcion": consulta, "accion": "editar",
                          "archivos": seleccion}],
                        raiz=str(raiz))
            except Exception as exc:
                sc.aviso(f"[aprendizaje] No se pudo registrar ({exc})")
            return 0 if ok else 1
        finally:
            # Si este orquestador fue quien registró el callback global, lo limpia.
            if self.evento_callback is not None:
                sc.fijar_evento_callback(None)

# __M3__
    # ------------------------------------------------------------------
    # Planificación: validación + escaneo/selección con AgenteContexto
    # ------------------------------------------------------------------
    def _planificar(self, args, sc) -> Optional[Tuple]:
        """Valida argumentos y ejecuta el escaneo/selección con agentes.

        Devuelve ``(consulta, raiz, carpetas, seleccion)`` o ``None`` si hay que
        abortar (proyecto inválido o sin candidatos).
        """
        consulta = getattr(args, "consulta", None)
        if not consulta:
            raise RuntimeError(
                "Falta la consulta (la tarea a resolver). Uso:\n"
                '  snapcontext "el botón de pago no funciona"\n'
                "  snapcontext --init   (para la configuracion inicial)"
            )
        if args.max_archivos < 1:
            raise RuntimeError("--max-archivos debe ser al menos 1.")

        raiz = sc.resolver_raiz(args.directorio)

        # v1.3.0: --iniciar-proyecto desactiva por completo la validación.
        if getattr(args, "iniciar_proyecto", False):
            sc.aviso(
                "Modo iniciar-proyecto: se omite la validación de carpeta. "
                "Asegúrate de estar en el directorio correcto."
            )
        else:
            directo_explicito = args.directorio not in (".", "")
            valido = sc._es_proyecto_valido(raiz)
            if not valido and (args.local or directo_explicito):
                # Con --local o --directorio explícito solo se avisa: el
                # usuario ya indicó claramente dónde quiere trabajar.
                sc.aviso(
                    "No se detectó una carpeta de proyecto típica "
                    "(lib/, src/, supabase/, etc.), pero se continúa por "
                    + ("usar --local." if args.local
                       else f"haber indicado --directorio ({raiz}).")
                )
            elif not valido:
                sc.error(
                    "⚠️ No se detectó una carpeta de proyecto típica "
                    "(lib/, src/, supabase/, etc.).\n"
                    "Si estás empezando un proyecto desde cero, usa "
                    "--iniciar-proyecto para saltar esta validación.\n"
                    "O usa --local para trabajar sin IA (también desactiva "
                    "la validación).\n"
                    "También puedes indicar la carpeta con --directorio <ruta>."
                )
                return None

        sc.info(f"Repositorio: {raiz}")

        # Auto-detección del tipo de proyecto (transparente; solo logs con --depurar).
        tipo = sc._detectar_tipo_proyecto(str(raiz))
        if tipo:
            sc.depurar(f"[Orquestador] Tipo de proyecto detectado: {tipo}")
            sc._ajustar_parametros_por_tipo(tipo, args)

        carpetas = args.carpetas or list(sc.CARPETAS_DEFECTO)
        extensiones = getattr(args, "extensiones", None)

        # 2) Escaneo del repositorio (Agente de Contexto)
        sc.info("Escaneando el repositorio para encontrar candidatos...")
        self._emitir_tipo("escaneo_inicio", directorio=str(raiz),
                          carpetas=carpetas, extensiones=extensiones)
        candidatos = self.agente_contexto.escanear_candidatos(
            consulta, str(raiz),
            carpetas=carpetas, extensiones=extensiones,
            max_candidatos=max(args.candidatos, 1),
        )
        self._emitir_tipo("escaneo_fin", total=len(candidatos))
        if not candidatos:
            sc.error(
                "No se encontraron archivos. ¿Hay código dentro de "
                f"{', '.join(carpetas)}? Revisa --carpetas."
            )
            return None
        sc.info(f"{len(candidatos)} candidato(s) relevante(s) localmente.")

        # Pre-filtro semántico (v1.1.0): si hay embeddings disponibles, se
        # reordenan los candidatos poniendo primero los archivos más similares
        # a la consulta. Si falla o no hay librería, se continúa como siempre.
        try:
            if (not args.local and sc._embeddings_disponibles()
                    and len(candidatos) > args.max_archivos):
                relevantes = sc._seleccionar_archivos_con_embeddings(
                    consulta, str(raiz),
                    max_archivos=max(len(candidatos), args.max_archivos))
                if relevantes:
                    conjunto = set(relevantes)
                    ordenados = ([c for c in candidatos if c in conjunto]
                                 + [c for c in candidatos if c not in conjunto])
                    sc.info(f"🧠 Pre-filtro semántico: {len(relevantes)} "
                            f"archivo(s) priorizado(s) por embeddings.")
                    sc.depurar(f"[embeddings] Orden semántico: {relevantes}")
                    candidatos = ordenados
        except Exception as exc:            # nunca romper el pipeline clásico
            sc.aviso(f"[embeddings] Pre-filtro omitido ({exc})")

        # 3) Selección final (Agente de Contexto)
        self._emitir_tipo("seleccion_inicio", max_archivos=args.max_archivos,
                          directorio=str(raiz), carpetas=carpetas)
        if args.local:
            sc.aviso("Modo --local: selección por heurística, sin proveedor de IA.")
            seleccion = candidatos[: args.max_archivos]
        elif len(candidatos) <= args.max_archivos:
            sc.aviso(
                "Hay pocos candidatos; se usan todos sin consultar al selector IA."
            )
            seleccion = candidatos
        else:
            pref = sc._determinar_proveedor(args)
            seleccion = self.agente_contexto.seleccionar_archivos(
                consulta, str(raiz), carpetas, args.max_archivos,
                provider=pref["provider"], modelo=pref["model"],
                extensiones=extensiones,
            )
            if not seleccion:
                sc.aviso(
                    "El proveedor no devolvió rutas válidas; se usan las mejor "
                    "puntuadas localmente."
                )
                seleccion = candidatos[: args.max_archivos]

        self._emitir_tipo("seleccion_fin", cantidad=len(seleccion),
                          archivos=seleccion, directorio=str(raiz))

        sc._emitir(sys.stdout, "")
        sc.exito(f"Archivos seleccionados ({len(seleccion)}):")
        for archivo in seleccion:
            sc._emitir(sys.stdout, "   " + sc._pintar("• " + archivo, sc._VERDE))
        # Evento para la UI web: se emite incluso en vista previa (que aquí sale).
        self._emitir_tipo("selección", archivos=seleccion, directorio=str(raiz))

        if args.vista_previa:
            sc.aviso("Modo --vista-previa: no se ejecuta Aider.")
            return VISTA_PREVIA

        # Modo experto: revisar/editar la selección antes de ejecutar Aider
        if args.experto and sc._preguntar_si(
            "¿Quieres revisar los archivos seleccionados? (s/n): "
        ):
            seleccion = sc.modo_experto(seleccion, raiz)
            sc._emitir(sys.stdout, "")
            sc.exito("Lista final para Aider:")
            for archivo in seleccion:
                sc._emitir(sys.stdout, "   " + sc._pintar("• " + archivo, sc._VERDE))
            sc._emitir(sys.stdout, "")

        return (consulta, raiz, carpetas, seleccion)