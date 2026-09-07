"""Planificador: gestión de contexto y visualización de planes (Fase 5)."""

from threading import Lock

# --- Contexto dinámico del plan (v6.30.0) ---------------------------------
_CONTEXTO_PLAN = {"variables": {}, "pasos": {}}
_CANDADO_CONTEXTO_PLAN = Lock()


def _contexto_plan_reiniciar() -> None:
    """Limpia el contexto dinámico al empezar cada ejecución del plan."""
    with _CANDADO_CONTEXTO_PLAN:
        _CONTEXTO_PLAN["variables"].clear()
        _CONTEXTO_PLAN["pasos"].clear()


def _contexto_plan_variable(nombre: str, valor) -> None:
    """Guarda ``valor`` bajo ``nombre`` (y como último ``resultado``)."""
    if not nombre:
        return
    with _CANDADO_CONTEXTO_PLAN:
        _CONTEXTO_PLAN["variables"][nombre] = valor
        _CONTEXTO_PLAN["variables"]["resultado"] = valor


def _registrar_resultado_plan(numero: int, ok: bool, detalle: str, estado: str = "") -> None:
    """Registra el resultado de un paso (base 1) para condiciones dinámicas."""
    with _CANDADO_CONTEXTO_PLAN:
        _CONTEXTO_PLAN["pasos"][str(numero)] = {
            "resultado": estado or ("ok" if ok else "fallo"),
            "ok": ok,
            "detalle": detalle,
        }


# --- Visualización resumida del plan (v6.23.0) ----------------------------
def _mostrar_plan_resumido(plan: list | None) -> str:
    """Devuelve un resumen legible del plan en 3-5 líneas (v6.23.0).

    En lugar de listar el plan completo, genera una frase compacta
    ``"Voy a: 1) leer el login, 2) corregir el error, ..."``; si hay más de 5
    pasos añade ``"y N más"``. Devuelve ``""`` si el plan está vacío.
    """
    if not plan:
        return ""
    pasos = list(plan)[:5]
    trozos: list[str] = []
    for i, paso in enumerate(pasos, start=1):
        if isinstance(paso, dict):
            desc = paso.get("descripcion") or paso.get("comando") or ""
            desc = str(desc).strip()
        else:
            desc = str(paso).strip()
        trozos.append(f"{i}) {desc}".strip())
    resumen = ", ".join(t for t in trozos if t)
    resto = len(list(plan)) - len(pasos)
    if resto > 0:
        resumen += f" y {resto} más"
    return f"Voy a: {resumen}"


# ===========================================================================
# Fase 9: núcleo del planificador (generación y ejecución de planes).
# Movido verbatim desde snapcontext.py. Los nombres que aún viven en
# snapcontext (proveedores, comandos, editor, hooks, permisos, mcp, ...) se
# resuelven de forma perezosa vía ``import snapcontext as _sc`` dentro de
# cada función, preservando el comportamiento y el monkey-patching.
# ===========================================================================
import argparse
import concurrent.futures
import json
import os
import re
import shlex
import sys
import threading
from pathlib import Path

PROMPT_PLAN = (
    "Eres un planificador de tareas de desarrollo. Descompón la siguiente "
    "tarea en pasos CONCRETOS y ATÓMICOS (máximo 8).\n\n"
    "TAREA: {consulta}\n\n"
    "Devuelve SOLO un objeto JSON con esta forma exacta (sin explicaciones):\n"
    '{{"pasos": [{{\n'
    '  "descripcion": "qué hace este paso",\n'
    '  "accion": "editar" | "ejecutar" | "consultar" | "mcp" | "asesor",\n'
    '  "archivos": ["ruta/relativa.py"],   // solo para accion "editar"\n'
    '  "comando": "comando shell",         // solo para accion "ejecutar"\n'
    '  "herramienta": "grep|read_file|list_files|ast|git_status|git_diff|\n'
    '                 "execute_command|...",   // solo para accion "mcp"\n'
    '  "args": {{"patron": "..."}},        // solo para accion "mcp"\n'
    '  "variable": "mi_resultado",        // opcional (mcp): nombre del resultado\n'
    "}}]}}\n\n"
    "Significado de las acciones:\n"
    ' - "editar": modificar código (Aider). Indica los archivos implicados.\n'
    ' - "ejecutar": lanzar un comando (tests, build, migraciones...).\n'
    ' - "consultar": aclarar una duda sobre el proyecto sin cambiar nada.\n'
    ' - "mcp": ejecutar una herramienta MCP (campos "herramienta" y "args") y\n'
    "   usar su resultado en pasos posteriores con {{{{resultado}}}} o {{{{mi_variable}}}}.\n"
    "Condiciones admitidas (el paso se salta si son falsas):\n"
    " - archivo_existe(ruta), archivo_contiene(ruta, texto), comando_exito(cmd),\n"
    "   variable_existe(nombre) o comparaciones como\n"
    "   \"pasos[0].resultado == 'ok'\"  ·  \"resultados.mi_variable != ''\".\n"
)

ACCIONES_VALIDAS = {"editar", "ejecutar", "consultar", "mcp", "asesor", "seguridad", "rendimiento"}


def _normalizar_pasos(datos) -> list[dict]:
    """Normaliza la respuesta del proveedor a una lista de pasos válidos.

    Acepta ``{"pasos": [...]}``, una lista directa o un único paso suelto.
    Descarta pasos mal formados (sin descripción o con acción desconocida).
    """

    import snapcontext as _sc  # perezoso: evita import circular

    if isinstance(datos, dict):
        datos = datos.get("pasos", [])
    if isinstance(datos, dict):
        datos = [datos]
    if not isinstance(datos, list):
        return []
    pasos: list[dict] = []
    for crudo in datos:
        if not isinstance(crudo, dict):
            continue
        descripcion = str(crudo.get("descripcion") or "").strip()
        accion = str(crudo.get("accion") or "").strip().lower()
        if not descripcion or accion not in ACCIONES_VALIDAS:
            continue
        archivos = crudo.get("archivos") or []
        if not isinstance(archivos, list):
            archivos = []
        paso = {
            "descripcion": descripcion,
            "accion": accion,
            "archivos": [str(a) for a in archivos if str(a).strip()],
            "comando": str(crudo.get("comando") or "").strip(),
            # v1.3.0: dependencias entre pasos y ejecución condicional.
            "dependencias": _sc._normalizar_dependencias(crudo.get("dependencias")),
            "condicion": str(crudo.get("condicion") or "").strip(),
            # v2.3.0: pasos de tipo "mcp".
            "herramienta": str(crudo.get("herramienta") or "").strip(),
            "args": crudo.get("args") if isinstance(crudo.get("args"), dict) else {},
            "variable": str(crudo.get("variable") or "").strip(),
        }
        pasos.append(paso)
    return pasos


def _normalizar_dependencias(valor) -> list[int]:
    """Convierte el campo ``dependencias`` de un paso en una lista de índices.

    Acepta lista de enteros/strings numéricos o un único valor. Se descartan
    los índices no válidos (negativos o fuera de rango se validan en la
    ejecución, aquí solo se normaliza el tipo).
    """

    if valor is None or valor == "":
        return []
    if not isinstance(valor, list):
        valor = [valor]
    indices: list[int] = []
    for item in valor:
        try:
            indice = int(item)
        except (TypeError, ValueError):
            continue
        if indice < 0:
            continue
        indices.append(indice)
    return sorted(set(indices))


def _generar_plan(  # noqa: C901  (refactor de complejidad: Fase 10c)
    consulta: str, proveedor: str | None = None, modelo: str | None = None
) -> list[dict]:
    """Pide al proveedor de IA un plan en JSON para la ``consulta``.

    Devuelve la lista de pasos normalizada (vacía si el proveedor no devolvió
    nada utilizable). Lanza RuntimeError ante fallos de configuración/API.
    """

    import snapcontext as _sc  # perezoso: evita import circular

    preferencias = _sc.cargar_configuracion()
    proveedor = proveedor or preferencias.get("provider") or _sc.PROVEEDOR_DEFECTO
    cfg = _sc.PROVEEDORES[proveedor]
    modelo = modelo or cfg["modelo_default"]
    prompt = PROMPT_PLAN.format(consulta=consulta)
    _sc.info(f"Generando plan con {cfg['nombre']} ({modelo})...")

    # MCP (v0.14.0): explora el proyecto con herramientas de solo lectura para
    # generar pasos más precisos (best-effort: nunca rompe la planificación).
    try:
        contexto_proyecto: list[str] = []
        estado = _sc._ejecutar_herramienta_mcp("git_status", {}, confirmar=False)
        if estado.get("ok"):
            res = estado["resultado"]
            contexto_proyecto.append(
                f"Rama git: {res.get('rama')} · cambios sin commitear: {res.get('total_cambios')}"
            )
        listado = _sc._ejecutar_herramienta_mcp("list_files", {"max_archivos": 30}, confirmar=False)
        if listado.get("ok"):
            contexto_proyecto.append(
                "Archivos del proyecto (muestra): "
                + ", ".join(listado["resultado"]["archivos"][:30])
            )
        if contexto_proyecto:
            prompt += "\n\nCONTEXTO DEL PROYECTO (obtenido con herramientas MCP):\n" + "\n".join(
                contexto_proyecto
            )
            _sc.info("🗺 Contexto MCP del proyecto añadido al planificador.")
    except Exception as exc:
        _sc.depurar(f"[mcp] contexto de planificación falló: {exc}")

    # Memoria de proyecto (v0.15.0): CLAUDE.md como contexto persistente.
    if _sc.MEMORIA_PROYECTO:
        # v6.1.0: el contenido del archivo CLAUDE.md se limita por tokens con
        # contexto selectivo (antes: recorte bruto a 3000 caracteres).
        try:
            import context_utils as _ctxm

            memoria_ctx = _ctxm.seleccionar_contexto(
                _sc.MEMORIA_PROYECTO, "markdown", max_tokens=750
            )
        except Exception as _exc:
            _sc.depurar(f"[plan] contexto selectivo de CLAUDE.md falló: {_exc}")
            memoria_ctx = _sc.MEMORIA_PROYECTO[:3000]
        prompt += (
            "\n\nMEMORIA DEL PROYECTO (CLAUDE.md, respeta sus "
            "convenciones al proponer pasos):\n" + memoria_ctx
        )
        _sc.info("🗜 Memoria del proyecto (CLAUDE.md) incluida en la planificación.")

    # Skills dinámicos (v6.6.0): reglas abstractas aprendidas de planes
    # exitosos enriquecen el prompt (máx. 3, priorizadas por confianza).
    prompt = _sc._enriquecer_prompt_con_reglas(prompt, consulta)

    tipo = cfg["tipo"]
    if tipo == "gemini":
        if _sc._importar_genai() is None:
            raise RuntimeError(_sc.MENSAJE_GENAI_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if not api_key:
            raise RuntimeError(_sc.MENSAJE_API_KEY)
        _sc.genai.configure(api_key=api_key)
        generador = _sc.genai.GenerativeModel(model_name=modelo)
        config = _sc.genai.types.GenerationConfig(
            temperature=0.2, response_mime_type="application/json"
        )
        try:
            respuesta = generador.generate_content(prompt, generation_config=config)
            texto = respuesta.text or ""
        except Exception as exc:
            raise RuntimeError(f"Error al generar el plan con Gemini: {exc}") from exc

    elif tipo == "_sc.anthropic":
        if _sc._importar_anthropic() is None:
            raise RuntimeError(_sc.MENSAJE_ANTHROPIC_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if not api_key:
            raise RuntimeError(_sc._mensaje_clave_faltante(proveedor, cfg))
        cliente = _sc.anthropic.Anthropic(api_key=api_key)
        try:
            respuesta = cliente.messages.create(
                model=modelo,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            texto = "".join(
                bloque.text
                for bloque in respuesta.content
                if getattr(bloque, "type", None) == "text"
            )
        except Exception as exc:
            raise RuntimeError(f"Error al generar el plan con Claude: {exc}") from exc

    else:  # tipo "_sc.openai"
        if _sc._importar_openai() is None:
            raise RuntimeError(_sc.MENSAJE_OPENAI_FALTANTE)
        api_key = os.environ.get(cfg["clave_env"], "").strip()
        if cfg["requiere_clave"] and not api_key:
            raise RuntimeError(_sc._mensaje_clave_faltante(proveedor, cfg))
        cliente = _sc.openai.OpenAI(
            api_key=api_key or "ollama-local", base_url=_sc._resolver_url_openai(cfg), timeout=120
        )
        mensajes = [{"role": "user", "content": prompt}]
        try:
            try:
                respuesta = cliente.chat.completions.create(
                    model=modelo,
                    messages=mensajes,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                )
            except Exception:
                respuesta = cliente.chat.completions.create(
                    model=modelo, messages=mensajes, temperature=0.2
                )
            texto = respuesta.choices[0].message.content or ""
        except Exception as exc:
            raise RuntimeError(f"Error al generar el plan con {cfg['nombre']}: {exc}") from exc

    # v6.2.0: muestra el razonamiento (chain-of-thought) si está activado y
    # limpia los bloques <think> antes de parsear el JSON del plan.
    texto, _raz_plan = _sc._procesar_razonamiento(texto, activo=_sc._razonamiento_activo())
    _sc.depurar(f"Plan recibido ({len(texto)} caracteres): {texto[:200]}")
    return _sc._normalizar_pasos(_sc.parsear_json(texto))


def _ejecutar_paso_plan(paso: dict, args: argparse.Namespace, raiz: str) -> tuple:  # noqa: C901  (refactor de complejidad: Fase 10c)
    """Ejecuta un paso del plan. Devuelve (ok: bool, detalle: str).

    - "editar": usa el orquestador actual — ``_planificar`` para elegir los
      archivos y ``_bucle_test``/AgenteEditor para aplicar la descripción.
    - "ejecutar": lanza ``paso["comando"]`` con ``_sc._ejecutar_comando``.
    - "consultar": pregunta al proveedor y muestra su respuesta.

    v6.22.0: ejecuta los hooks ``before_plan_step`` (puede abortar el paso)
    y ``after_plan_step`` al finalizar.
    """

    import snapcontext as _sc  # perezoso: evita import circular
    import snapcontext as sc
    from orquestador import Orquestador

    # v6.22.0: hook before_plan_step — puede modificar el paso o abortarlo.
    _ctx_hook = {"paso": paso, "accion": paso.get("accion"), "descripcion": paso.get("descripcion")}
    _abortado, _ctx_hook = _sc._hooks.ejecutar_hook("before_plan_step", _ctx_hook)
    if _abortado:
        _sc.aviso(f"Paso abortado por hook: {paso.get('descripcion', '')!s:.60}")
        return (False, "abortado por hook before_plan_step")
    paso = _ctx_hook.get("paso") or paso

    accion = paso["accion"]
    descripcion = paso["descripcion"]
    # v2.3.0: sustitución de marcadores {{variable}} / {{resultado}} en los
    # campos del paso usando el contexto dinámico del plan.
    descripcion = _sc._resolver_marcadores(descripcion)
    for _clave in ("comando", "herramienta", "contenido"):
        _valor = paso.get(_clave)
        if isinstance(_valor, str) and "{{" in _valor:
            paso[_clave] = _sc._resolver_marcadores(_valor)
    if isinstance(paso.get("archivos"), list):
        paso["archivos"] = [_sc._resolver_marcadores(a) for a in paso["archivos"]]
    if isinstance(paso.get("args"), dict) and paso["args"]:
        paso["args"] = _sc._resolver_marcadores_args(paso["args"])

    # Confirmación de permisos (v0.13.0) antes de cualquier acción.
    # En modo autónomo (--auto, v0.17.0) no se pregunta: solo se respetan las
    # preferencias ya guardadas en permisos.json (nunca → denegado).
    if accion == "ejecutar":
        detalles_paso = paso.get("comando") or None
    elif accion == "editar":
        detalles_paso = "\n".join(paso.get("archivos", [])) or None
    elif accion == "mcp":
        detalles_paso = str(paso.get("herramienta") or "")
    else:
        detalles_paso = None
    if getattr(args, "auto", False):
        if _sc._permiso_recordado(accion) is False:
            _sc.aviso(f"[auto] Paso '{accion}' denegado por permisos guardados (permisos.json).")
            return (False, "denegado por permisos guardados")
    elif not _sc._confirmar_accion(
        descripcion, tipo=accion, detalles=detalles_paso, confirmar=getattr(args, "confirmar", True)
    ):
        return (False, "denegado por el usuario")

    if accion == "ejecutar":
        if not paso.get("comando"):
            return (False, 'el paso no indica "comando"')
        _sc.info(f"$ {paso['comando']}")
        codigo, stdout, stderr = _sc._ejecutar_comando(paso["comando"], raiz)
        if stdout.strip():
            _sc._emitir(sys.stdout, _sc._pintar(stdout.rstrip(), _sc._VERDE))
        if stderr.strip():
            _sc._emitir(sys.stdout, _sc._pintar(stderr.rstrip(), _sc._AMARILLO))
        return (codigo == 0, f"código {codigo}")

    # accion == "mcp" (v2.3.0): ejecuta una herramienta MCP y deja su
    # resultado en el contexto del plan para los pasos siguientes.
    if accion == "mcp":
        herramienta = paso.get("herramienta")
        if not herramienta:
            return (False, 'el paso no indica "herramienta"')
        argumentos = _sc._resolver_marcadores_args(paso.get("args") or {})
        _sc.info(("[mcp] " + herramienta + " " + str(argumentos)).rstrip())
        llamada = _sc._ejecutar_herramienta_mcp(herramienta, argumentos)
        res = llamada.get("resultado", {})
        try:
            muestra = json.dumps(res, ensure_ascii=False)
        except Exception:
            muestra = str(res)
        if len(muestra) > 400:
            muestra = muestra[:400] + "…"
        if llamada.get("ok"):
            _sc.exito("[mcp] resultado: " + muestra)
            _contexto_plan_variable(str(paso.get("variable") or herramienta), res)
            return (True, herramienta + ": ok")
        _sc.error("[mcp] falló: " + muestra)
        return (False, herramienta + ": " + str(res.get("_sc.error", "fallo")))

    if accion == "consultar":
        preferencias = _sc.cargar_configuracion()
        proveedor = preferencias.get("provider") or _sc.PROVEEDOR_DEFECTO
        try:
            respuesta = _sc._enviar_al_proveedor(
                proveedor,
                getattr(args, "modelo", None),
                [
                    {
                        "role": "user",
                        "content": f"Tarea general: {getattr(args, 'consulta', '')}\n"
                        f"Paso a aclarar: {descripcion}\n"
                        "Responde de forma breve y útil.",
                    }
                ],
            )
            respuesta, _raz = _sc._procesar_razonamiento(
                respuesta, activo=_sc._razonamiento_activo(args)
            )
            _sc._emitir(sys.stdout, _sc._pintar(respuesta, _sc._VERDE))
            return (True, "respuesta mostrada")
        except RuntimeError as exc:
            _sc.error(str(exc))
            return (False, str(exc))

    # accion == "seguridad" / "rendimiento" (v4.2.0): análisis enfocado;
    # en --auto se ejecutan solos y las sugerencias solo se muestran.
    if accion in ("seguridad", "rendimiento"):
        tipos = ("vulnerabilidad",) if accion == "seguridad" else ("rendimiento",)
        encontradas = _sc._asesor_analizar_por_tipo(raiz, tipos)
        if not encontradas:
            _sc.exito(f"[{accion}] Sin hallazgos: sin problemas detectados.")
            return (True, "sin hallazgos")
        for sugg in encontradas:
            texto = f"[{accion}] {sugg['descripcion']} ({sugg['archivo']}:{sugg['linea']})"
            if getattr(args, "auto", False):
                _sc.aviso(texto + f" → {sugg['solucion']}")
                continue
            if _sc._confirmar_accion(
                texto,
                tipo=accion,
                detalles=sugg.get("solucion"),
                confirmar=getattr(args, "confirmar", True),
            ):
                _sc.exito(f"Anotada: {sugg['solucion']}")
        return (True, f"{len(encontradas)} hallazgo(s) de {accion}")

    # accion == "asesor" (v3.5.0): análisis estático del proyecto; cada
    # sugerencia se presenta al usuario para aceptarla o rechazarla. En modo
    # --auto solo se informan (nunca se aplica código sin confirmación).
    if accion == "asesor":
        sugerencias_paso = _sc._asesor_analizar(raiz)
        if not sugerencias_paso:
            _sc.exito("[asesor] Sin sugerencias: el código está limpio.")
            return (True, "sin sugerencias")
        aceptadas = 0
        for sugg in sugerencias_paso:
            texto = f"[asesor] {sugg['descripcion']} ({sugg['archivo']}:{sugg['linea']})"
            if getattr(args, "auto", False):
                _sc.aviso(texto + f" → {sugg['solucion']}")
                continue
            if _sc._confirmar_accion(
                texto,
                tipo="asesor",
                detalles=sugg.get("solucion"),
                confirmar=getattr(args, "confirmar", True),
            ):
                aceptadas += 1
                _sc.exito(f"Sugerencia aceptada: {sugg['solucion']}")
            else:
                _sc.info("Sugerencia descartada.")
        return (True, f"{len(sugerencias_paso)} sugerencia(s), {aceptadas} aceptada(s)")

    # accion == "editar": reutiliza el pipeline existente o usa el editor propio
    editor_elegido = getattr(args, "editor", "aider") or "aider"
    if editor_elegido == "propio":
        archivos_paso = paso.get("archivos", [])
        contenido_paso = paso.get("contenido")
        if archivos_paso and contenido_paso is not None:
            # Si el paso trae archivo y contenido explícito
            todo_ok = True
            for arch in archivos_paso:
                if not _sc._editor_sobrescribir(arch, contenido_paso, raiz):
                    todo_ok = False
            return (todo_ok, f"EditorPropio sobre {len(archivos_paso)} archivo(s)")

    paso_args = argparse.Namespace(**vars(args))
    paso_args.consulta = descripcion
    orch = Orquestador()
    plan = orch._planificar(paso_args, sc)
    if plan is None:
        return (False, "no se pudo planificar la edición (sin candidatos)")
    _, ruta_raiz, _, seleccion = plan

    if editor_elegido == "propio":
        modo_ed = getattr(args, "modo_edicion", "auto") or "auto"
        todo_ok = orch.agente_editor_propio.ejecutar(
            seleccion,
            descripcion,
            directorio=str(ruta_raiz),
            modo_edicion=modo_ed,
            modelo=getattr(args, "modelo", None),
            validar=getattr(args, "validar", True),
            max_intentos_validacion=getattr(
                args, "max_intentos_validacion", _sc.MAX_INTENTOS_VALIDACION
            ),
            proveedor=getattr(args, "provider", None),
            modelo_ligero=getattr(args, "modelo_ligero", False),
            auto=getattr(args, "auto", False),
            max_context_tokens=getattr(args, "max_context_tokens", None),
            editor_fallback=getattr(args, "editor_fallback", False),
            mostrar_diff=getattr(args, "mostrar_diff", False),
        )
        return (todo_ok, f"EditorPropio sobre {len(seleccion)} archivo(s)")

    if getattr(args, "test_loop", False):
        _comando_test = None
        if getattr(args, "comando_test", None):
            _comando_test = shlex.split(args.comando_test)
        ok = orch._bucle_test(
            descripcion,
            seleccion,
            str(ruta_raiz),
            opciones_aider=getattr(args, "aider_opciones", ""),
            comando_test=_comando_test,
            max_iteraciones=max(getattr(args, "max_iteraciones", 1), 1),
        )
        return (ok, "bucle de pruebas")
    ok = orch.agente_editor.ejecutar_aider(
        seleccion,
        descripcion,
        str(ruta_raiz),
        opciones_aider=getattr(args, "aider_opciones", ""),
    )
    # v6.22.0: hook `after_plan_step` — observabilidad post-ejecución del paso.
    try:
        _sc._hooks.ejecutar_hook(
            "after_plan_step",
            {"paso": paso, "ok": ok, "detalle": f"Aider sobre {len(seleccion)} archivo(s)"},
        )
    except Exception:
        pass
    return (ok, f"Aider sobre {len(seleccion)} archivo(s)")


# --- Condiciones y paralelismo del planificador (v1.4.0) --------------------
def _evaluar_condicion(condicion: str, raiz: str = ".", contexto: dict | None = None) -> bool:  # noqa: C901  (refactor de complejidad: Fase 10c)
    """Evalúa la condición de un paso del plan. Devuelve True si se cumple.

    Formatos soportados:

      Funciones (v1.4.0):
        archivo_existe('src/main.py')
        archivo_contiene('src/main.py', 'def main')
        comando_exito('flutter test')
        variable_existe('mi_variable')            # v2.3.0

      Comparaciones dinámicas (v2.3.0), con resultados de pasos previos o
      variables dejadas en el contexto (p. ej. por pasos "mcp"):
        pasos[0].resultado == 'ok'
        pasos[2].resultado != 'fallo'
        resultados.mi_variable == 'listo'
        mi_variable != ''                         # forma abreviada

    Las cadenas pueden ir con comillas simples o dobles. Cualquier condición
    mal formada o desconocida devuelve False con un _sc.aviso (fallo elegante:
    el paso se salta, nunca se aborta el plan).
    """

    import snapcontext as _sc  # perezoso: evita import circular

    if contexto is None:
        contexto = _CONTEXTO_PLAN
    condicion = (condicion or "").strip()
    if not condicion:
        return True

    # 1) Comparaciones dinámicas (== / !=).
    comparacion = re.match(r"^(.+?)\s*(==|!=)\s*(.+)$", condicion, re.S)
    if comparacion and "(" not in condicion.split("==")[0].split("!=")[0]:
        izquierdo = _sc._resolver_operando_condicion(comparacion.group(1).strip(), contexto)
        derecho = _sc._resolver_operando_condicion(comparacion.group(3).strip(), contexto)
        if izquierdo is _DESCONOCIDO or derecho is _DESCONOCIDO:
            _sc.aviso(f"Condición con referencia desconocida: '{condicion}'.")
            return False
        iguales = _sc._normalizar_comparacion(izquierdo) == _sc._normalizar_comparacion(derecho)
        return iguales if comparacion.group(2) == "==" else not iguales

    # 2) Formas funcionales clásicas.
    coincidencia = re.match(r"^([a-zA-Z_]\w*)\s*\((.*)\)\s*$", condicion, re.S)
    if not coincidencia:
        _sc.aviso(f"Condición de paso mal formada: '{condicion}'. Se interpreta como no cumplida.")
        return False
    funcion, crudo_args = coincidencia.group(1), coincidencia.group(2)
    try:
        argumentos = [a.strip() for a in _sc._partir_argumentos(crudo_args)]
    except ValueError as exc:
        _sc.aviso(f"Condición inválida '{condicion}': {exc}")
        return False

    if funcion == "archivo_existe":
        return len(argumentos) == 1 and (Path(raiz) / argumentos[0]).exists()
    if funcion == "archivo_contiene":
        if len(argumentos) != 2:
            return False
        contenido = _sc._leer_archivo(Path(raiz) / argumentos[0])
        return contenido is not None and argumentos[1] in contenido
    if funcion == "comando_exito":
        if not argumentos or not argumentos[0]:
            return False
        codigo, _, _ = _sc._ejecutar_comando(argumentos[0], raiz, timeout=300)
        return codigo == 0
    if funcion == "variable_existe":
        with _CANDADO_CONTEXTO_PLAN:
            variables = dict(contexto.get("variables", {}))
        return bool(argumentos) and argumentos[0] in variables

    _sc.aviso(
        f"Función de condición desconocida: '{funcion}'. Soportadas: "
        f"archivo_existe, archivo_contiene, comando_exito, "
        f"variable_existe."
    )
    return False


# Resultados desconocidos para condiciones dinamicas.
_DESCONOCIDO = object()


def _resolver_operando_condicion(operando: str, contexto: dict):  # noqa: C901  (refactor de complejidad: Fase 10c)
    """Convierte un operando de condición en un valor Python concreto.

    Acepta literales ('texto', números, true/false/null) y referencias al
    contexto: pasos[N].campo, resultados.nombre o un identificador simple.
    Devuelve _DESCONOCIDO si no se puede resolver.
    """

    operando = operando.strip()
    if len(operando) >= 2 and operando[0] in "'\"" and operando[-1] == operando[0]:
        return operando[1:-1]
    if operando.lower() in ("true", "verdad"):
        return True
    if operando.lower() in ("false", "falso"):
        return False
    if operando.lower() in ("none", "null", "nulo"):
        return None
    try:
        return int(operando)
    except ValueError:
        pass
    try:
        return float(operando)
    except ValueError:
        pass

    m = re.match(r"^pasos\[(\d+)\]\.(\w+)$", operando)
    if m:
        numero, campo = int(m.group(1)), m.group(2)
        with _CANDADO_CONTEXTO_PLAN:
            paso_ctx = contexto.get("pasos", {}).get(str(numero))
        if not isinstance(paso_ctx, dict) or campo not in paso_ctx:
            return _DESCONOCIDO
        return paso_ctx[campo]

    m = re.match(r"^resultados?\.(\w+)$", operando)
    if m:
        with _CANDADO_CONTEXTO_PLAN:
            variables = contexto.get("variables", {})
        return variables.get(m.group(1), _DESCONOCIDO)

    if re.match(r"^[a-z_][\w]*$", operando):
        with _CANDADO_CONTEXTO_PLAN:
            variables = contexto.get("variables", {})
        return variables.get(operando, _DESCONOCIDO)

    return _DESCONOCIDO


def _normalizar_comparacion(valor):
    """Normaliza valores para poder compararlos entre sí."""

    if isinstance(valor, bool):
        return "ok" if valor else "fallo"
    if isinstance(valor, (int, float)):
        return str(valor)
    if isinstance(valor, (dict, list)):
        try:
            import json as _json

            return _json.dumps(valor, sort_keys=True, ensure_ascii=False)
        except Exception:
            return str(valor)
    return str(valor)


def _partir_argumentos(texto: str) -> list[str]:
    """Separa los argumentos de una condición respetando comillas."""

    partes, actual, comilla = [], "", None
    for caracter in texto:
        if comilla:
            if caracter == comilla:
                comilla = None
            else:
                actual += caracter
            continue
        if caracter in ("'", '"'):
            comilla = caracter
            continue
        if caracter == ",":
            partes.append(actual)
            actual = ""
            continue
        actual += caracter
    if comilla:
        raise ValueError("comillas sin cerrar")
    partes.append(actual)
    return [p for p in (p.strip() for p in partes)]


# --- Contexto dinámico del plan (v2.3.0) ------------------------------------
# Los pasos pueden dejar resultados (p. ej. herramientas MCP) en este contexto
# y los pasos posteriores los consumen con {{resultado}}, {{mi_variable}} o
# condiciones como "pasos[0].resultado == 'ok'" / "resultados.mi_var == 'x'".


def _resolver_marcadores(texto: str):
    """Sustituye la marca de doble llave {{clave}} por el valor que
    tenga esa clave en el contexto dinámico del plan. Si la clave
    no existe o el texto no es una cadena, se devuelve sin cambios.

    Si ``texto`` no es una cadena se devuelve tal cual. Las claves desconocidas
    se dejan sin sustituir (fallo elegante).
    """

    if not isinstance(texto, str) or "{{" not in texto:
        return texto
    import json as _json

    with _CANDADO_CONTEXTO_PLAN:
        variables = dict(_CONTEXTO_PLAN["variables"])

    def _sustituir(coincidencia):
        clave = coincidencia.group(1).strip()
        if clave not in variables:
            return coincidencia.group(0)
        valor = variables[clave]
        if isinstance(valor, str):
            return valor
        try:
            return _json.dumps(valor, ensure_ascii=False)
        except Exception:
            return str(valor)

    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", _sustituir, texto)


def _refs_de_condicion(condicion: str) -> tuple:
    """Extrae los índices de pasos y nombres de variables que usa una condición."""

    condicion = condicion or ""
    indices = set()
    for m in re.findall(r"pasos\[(\d+)\]", condicion):
        try:
            indices.add(int(m) - 1)
        except ValueError:
            continue
    nombres = set(re.findall(r"resultados?\.(\w+)", condicion))
    for m in re.findall(
        r"(?:^|\(|&&|\|)\s*([a-z_][\w]*)"
        r"\s*(?:==|!=)",
        condicion,
    ):
        nombre = m[1] if isinstance(m, tuple) else m
        if nombre not in ("true", "false", "none", "ok"):
            nombres.add(nombre)
    return indices, nombres


def _resolver_marcadores_args(argumentos: dict) -> dict:
    """Aplica la sustitución de marcadores a los valores string de un dict."""

    import snapcontext as _sc  # perezoso: evita import circular

    resuelto = {}
    for clave, valor in (argumentos or {}).items():
        if isinstance(valor, str):
            resuelto[clave] = _sc._resolver_marcadores(valor)
        elif isinstance(valor, list):
            resuelto[clave] = [_sc._resolver_marcadores(v) for v in valor]
        else:
            resuelto[clave] = valor
    return resuelto


_CANDADO_GIT_PLAN = threading.Lock()  # serializa commits en modo --paralelo


def _ejecutar_paso_paralelo(paso: dict, args: argparse.Namespace, raiz: str, numero: int) -> dict:
    """Ejecuta un paso en modo --paralelo (hilo secundario). Devuelve registro."""

    import snapcontext as _sc  # perezoso: evita import circular

    prefijo = f"[paso {numero}]"
    _sc.exito(f"{prefijo} [{paso['accion']}]: {paso['descripcion']}")

    condicion = paso.get("condicion")
    if condicion and not _sc._evaluar_condicion(condicion, raiz):
        _sc.aviso(f"{prefijo} condición no cumplida ({condicion}); se salta.")
        return {
            "paso": numero,
            "descripcion": paso["descripcion"],
            "accion": paso["accion"],
            "resultado": "saltado",
            "detalle": f"condición no cumplida: {condicion}",
            "intentos": 0,
        }
    try:
        ok, detalle = _sc._ejecutar_paso_plan(paso, args, raiz)
    except Exception as exc:  # blindaje del hilo
        ok, detalle = False, f"excepción: {exc}"
    _registrar_resultado_plan(numero, ok, detalle)
    marca = "✔" if ok else "✖"
    _sc._emitir(sys.stdout, f"  {marca} {prefijo} terminado ({detalle})")
    if ok and getattr(args, "git_commit", True):
        with _CANDADO_GIT_PLAN:
            _sc._commit_paso(paso, args, raiz)
    return {
        "paso": numero,
        "descripcion": paso["descripcion"],
        "accion": paso["accion"],
        "resultado": "éxito" if ok else "fallo",
        "detalle": detalle,
        "intentos": 1,
    }


def _ejecutar_plan_en_paralelo(  # noqa: C901  (refactor de complejidad: Fase 10c)
    pasos: list[dict], args: argparse.Namespace, raiz: str, max_hilos: int
) -> list[dict]:
    """Ejecuta el plan con ``--paralelo N`` (modo --auto).

    Rondas de ejecución: en cada ronda se lanzan todos los pasos cuyas
    dependencias ya tuvieron éxito (_sc.ThreadPoolExecutor limita la concurrencia
    a ``max_hilos``); los pasos con dependencias fallidas o saltadas se marcan
    como saltados. Los logs llevan el identificador ``[paso N]``.
    """

    import snapcontext as _sc  # perezoso: evita import circular

    estado: dict = {}  # índice → resultado terminal
    resultados: list[dict] = []
    pendientes = set(range(len(pasos)))
    MALOS_TERMINALES = ("fallo", "saltado")

    with _sc.ThreadPoolExecutor(max_workers=max(1, max_hilos)) as pool:
        while pendientes:
            # 'dependencias' guarda números de paso (base 1): convertimos.
            for i in sorted(pendientes):
                deps = [d - 1 for d in (pasos[i].get("dependencias") or [])]
                if any(estado.get(d) in MALOS_TERMINALES for d in deps):
                    numero = i + 1
                    _sc.aviso(
                        f"[paso {numero}] saltado: dependencia(s) sin éxito "
                        f"({[d + 1 for d in deps]})."
                    )
                    estado[i] = "saltado"
                    resultados.append(
                        {
                            "paso": numero,
                            "descripcion": pasos[i]["descripcion"],
                            "accion": pasos[i]["accion"],
                            "resultado": "saltado",
                            "detalle": "dependencia sin éxito",
                            "intentos": 0,
                        }
                    )
                    pendientes.discard(i)

            # v2.3.0: además de las dependencias explícitas, un paso queda
            # bloqueado mientras su condición referencie variables que algún
            # paso pendiente aún puede producir (p. ej. un paso "mcp").
            producibles = set()
            for j in pendientes:
                _pj = pasos[j]
                _ri, _rv = _sc._refs_de_condicion(_pj.get("condicion") or "")
                producibles |= _rv
                if _pj.get("accion") == "mcp":
                    producibles.add(str(_pj.get("variable") or _pj.get("herramienta") or ""))
                    producibles.add("resultado")

            def _listo(i):
                deps = [d - 1 for d in (pasos[i].get("dependencias") or [])]
                if any(estado.get(d) != "éxito" for d in deps):
                    return False
                ref_i, ref_v = _sc._refs_de_condicion(pasos[i].get("condicion") or "")
                if any(estado.get(d) != "éxito" for d in ref_i):
                    return False
                with _CANDADO_CONTEXTO_PLAN:
                    disponibles = set(_CONTEXTO_PLAN["variables"])
                    registrados = set(_CONTEXTO_PLAN["pasos"])
                for v in ref_v:
                    if v not in disponibles and v in producibles:
                        return False  # esperar a que se produzca
                for d in ref_i:
                    if str(d + 1) not in registrados:
                        return False
                return True

            lanzables = [i for i in sorted(pendientes) if _listo(i)]
            if not lanzables:
                if pendientes:  # nada ejecutable → evitar bloqueo
                    for i in sorted(pendientes):
                        estado[i] = "saltado"
                        resultados.append(
                            {
                                "paso": i + 1,
                                "descripcion": pasos[i]["descripcion"],
                                "accion": pasos[i]["accion"],
                                "resultado": "saltado",
                                "detalle": "dependencias insatisfechas",
                                "intentos": 0,
                            }
                        )
                    pendientes.clear()
                continue

            futuros = {
                pool.submit(_sc._ejecutar_paso_paralelo, pasos[i], args, raiz, i + 1): i
                for i in lanzables
            }
            for i in lanzables:
                pendientes.discard(i)
            for futuro in concurrent.futures.as_completed(futuros):
                i = futuros[futuro]
                registro = futuro.result()
                estado[i] = registro["resultado"]
                resultados.append(registro)

    resultados.sort(key=lambda r: r["paso"])
    return resultados
