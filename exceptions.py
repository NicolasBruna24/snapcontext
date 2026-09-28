"""Excepciones compartidas de SnapContext (sin dependencias del monolito).

Aloja :class:`RutaInseguraError` aqui para que ``utils.py`` pueda
importarla sin crear un ciclo con ``snapcontext``.
"""


class RutaInseguraError(ValueError):
    """Validacion de ruta segura rechazo la operacion de escritura/lectura.

    El mensaje explica el motivo del rechazo para que el caller pueda
    informar al usuario o logear sin revelar paths sensibles.
    """

    pass


class ContratoEstadoInvalidoError(ValueError):
    """El documento `state.md` no cumple el contrato documental minimo.

    Solo cubre la incompatibilidad del documento (cabecera ausente,
    `Kind`/`Contract`/`Work` incorrectos, cabecera ambigua). La ausencia de
    un campo opcional del cuerpo **no** es un error: se representa como
    ausencia en la representacion devuelta.
    """

    pass


class AutoridadInsuficienteError(ValueError):
    """Operacion valida pero no autorizada por la politica de escritura (B14-H).

    Deliberadamente **distinta** de `ValueError` de "entrada invalida": el
    llamador debe poder diferenciar

    * "me equivoque al pedir esto" (nombre de campo, tipo, valor), de
    * "esto es valido pero no me dejan" (campo protegido, o operacion que
      requiere autorizacion explicita y no la trae).

    No se persiste nada cuando se lanza: el rechazo ocurre **antes** de
    escribir, por lo que nunca hay que revertir.
    """

    def __init__(self, mensaje: str, campos: tuple[str, ...] = ()):
        super().__init__(mensaje)
        #: Campos que motivaron el rechazo (información segura para el consumidor).
        self.campos = campos
