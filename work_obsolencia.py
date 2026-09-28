#!/usr/bin/env python3
"""B15-L — Obsolescencia explícita de la última verificación persistida.

Compone las tres piezas ya ratificadas, **sin mover ninguna de ellas**:

```text
Git adapter    → CurrentGitState   (work_verdict.leer_estado_git)
pure evaluator → ValidityResult    (work_verdict.evaluar_validez_verdict)
obsolescence op → state.md         (work_context.registrar_veredicto_obsoleto)
```

La obsolescencia significa, y solo significa, que el **contenido efectivo** del
árbol dejó de coincidir con el `tree_sha` que produjo la verificación:

```text
tree_sha_actual != tree_sha_del_verdict
```

`commit` y `working_tree_clean` son metadata y no participan: por eso un revert
exacto, o un commit nuevo con el mismo contenido, dejan de ser obsolescentes.

La operación es **explícita**: no hay daemon, ni polling, ni watcher, ni
detección implícita al leer un `WorkState`. Tampoco ejecuta ninguna verificación:
la pregunta es «¿el resultado histórico sigue describiendo el árbol actual?», no
«¿debemos volver a verificar?».
"""

from __future__ import annotations

from dataclasses import dataclass

from work_context import (
    leer_estado,
    leer_ultima_verificacion,
    leer_veredictos_obsoletos,
    registrar_veredicto_obsoleto,
)
from work_verdict import (
    OBSOLETE,
    CurrentGitState,
    ValidityResult,
    evaluar_validez_verdict,
    leer_estado_git,
)

__all__ = [
    "ES_OBSOLETO",
    "ES_SIN_VERIFICACION",
    "ES_VIGENTE",
    "ES_YA_REGISTRADO",
    "DeteccionObsolescencia",
    "detectar_obsolescencia",
]

#: La última verificación persistida sigue describiendo el árbol actual.
ES_VIGENTE = "vigente"
#: Dejó de describirlo y se registró la entrada en el historial.
ES_OBSOLETO = "obsoleto"
#: Ya había una entrada en el historial para ese mismo veredicto: no se duplica.
ES_YA_REGISTRADO = "ya_registrado"
#: No hay verificación persistida que invalidar (trabajo antiguo, o veredicto
#: de texto libre). No es un error: no hay nada que hacer.
ES_SIN_VERIFICACION = "sin_verificacion"


@dataclass(frozen=True)
class DeteccionObsolescencia:
    """Resultado de una detección explícita.

    Conserva las dos dimensiones, igual que B15-J y B15-K: ``estado`` describe
    la validez actual, y ``ya_registrado`` dice si la transición ya constaba.
    """

    estado: str
    validez: ValidityResult | None
    verificado_tree_sha: str | None
    tree_actual: str | None
    entradas_historicas: int
    ya_registrado: bool = False

    @property
    def hay_obsolescencia(self) -> bool:
        """¿Se detectó obsolescencia en esta operación?"""
        return self.estado == ES_OBSOLETO

    @property
    def se_registro(self) -> bool:
        """¿Se añadió una entrada nueva al historial en esta operación?"""
        return self.estado == ES_OBSOLETO and not self.ya_registrado



def _reconstruir_verdict(verificacion):
    """Reconstruye un `Verdict` **solo** con lo que el documento conserva.

    No inventa campos: `resultado`, `comando`, `scope` y el anchor
    (`tree_sha`, `commit`, `working_tree_clean`) salen de la línea
    `[verificacion:v1]`, y `instante` si se persistió. Es la verificación que
    ocurrió, no una nueva.
    """
    from work_verdict import GitAnchor, Verdict

    anchor = GitAnchor(
        tree_sha=verificacion.tree_sha,
        commit=verificacion.commit or "desconocido",
        working_tree_clean=bool(verificacion.working_tree_clean),
    )
    return Verdict(
        resultado=verificacion.resultado,
        comando=verificacion.comando or "(comando no registrado)",
        scope=verificacion.scope or "no definido",
        git_anchor=anchor,
        instante=verificacion.instante,
    )


def detectar_obsolescencia(
    work_id: str,
    directorio: str = ".",
    *,
    instante_deteccion: str | None = None,
) -> DeteccionObsolescencia:
    """Detecta si la última verificación persistida sigue vigente.

    Secuencia explícita:

    1. leer `ultimo_veredicto` del documento;
    2. observar el árbol actual (:func:`leer_estado_git`);
    3. reconstruir el `Verdict` histórico y evaluarlo con el evaluador **puro**;
    4. si es `VALID` → no se toca nada;
    5. si es `OBSOLETE` → registrar **una** entrada en `veredictos_obsoletos`.

    No modifica `ultimo_veredicto` ni el mandato: conserva la última
    verificación tal y como se registró (su `resultado`, `tree_sha` y comando).
    La validez vigente es la que acaba de calcular el evaluador, y la transición
    queda como evento histórico. B15-L no borra ni reescribe la verificación.

    Es **idempotente**: repetirla con el mismo árbol no añade entradas, porque
    la identidad de una entrada es el `tree_sha` del veredicto obsoleto, no el
    instante de detección.

    Args:
        work_id: trabajo a inspeccionar.
        directorio: raíz del proyecto.
        instante_deteccion: marca de la **detección** (UTC ISO-8601). Es un
            hecho distinto del `instante` del Verdict (cuándo se verificó) y no
            lo sustituye. Si no se pasa, no se escribe ninguna marca: no se
            inventa un reloj.

    Returns:
        DeteccionObsolescencia con el estado y la evidencia mínima.

    Raises:
        ContratoEstadoInvalidoError: si la línea de verificación está corrupta
            (se declara `[verificacion:vN]` pero no se puede parsear). No se
            inventa un Verdict ni se registra historial.
        EstadoGitIndisponibleError: si Git no es observable. El documento no se
            modifica: no se puede afirmar nada.
    """
    estado_documento = leer_estado(work_id, directorio)

    # 1. Sin verificación persistida no hay nada que invalidar: un trabajo
    #    antiguo o un veredicto de texto libre es un no-op, no un error.
    verificacion = leer_ultima_verificacion(estado_documento)
    if verificacion is None:
        return DeteccionObsolescencia(
            estado=ES_SIN_VERIFICACION,
            validez=None,
            verificado_tree_sha=None,
            tree_actual=None,
            entradas_historicas=len(estado_documento.veredictos_obsoletos),
        )

    # 2. Estado Git actual. Falla cerrado: sin observación no hay afirmación.
    actual: CurrentGitState = leer_estado_git(str(directorio))

    # 3. La decisión es del evaluador puro, no de esta función.
    validez = evaluar_validez_verdict(_reconstruir_verdict(verificacion), actual)

    historiales = leer_veredictos_obsoletos(estado_documento)
    if validez.status != OBSOLETE:
        # 4. Sigue vigente: el historial no se toca. Un revert exacto o un
        #    commit con el mismo contenido caen aquí.
        return DeteccionObsolescencia(
            estado=ES_VIGENTE,
            validez=validez,
            verificado_tree_sha=verificacion.tree_sha,
            tree_actual=actual.tree_sha,
            entradas_historicas=len(historiales),
        )

    # 5. Obsoleto: una única entrada por veredicto.
    from work_context import formatear_veredicto_obsoleto

    entrada = formatear_veredicto_obsoleto(
        verificacion.resultado,
        verificacion.tree_sha,
        actual.tree_sha,
        validez.motivo,
        commit=verificacion.commit,
        working_tree_clean=verificacion.working_tree_clean,
        scope=verificacion.scope,
        comando=verificacion.comando,
        instante=verificacion.instante,
        detectado=instante_deteccion,
    )
    ya_registrado = any(h.clave == verificacion.tree_sha for h in historiales)
    if not ya_registrado:
        registrar_veredicto_obsoleto(work_id, entrada, directorio)

    return DeteccionObsolescencia(
        estado=ES_OBSOLETO,
        validez=validez,
        verificado_tree_sha=verificacion.tree_sha,
        tree_actual=actual.tree_sha,
        entradas_historicas=len(historiales) + (0 if ya_registrado else 1),
        ya_registrado=ya_registrado,
    )
