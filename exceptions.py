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
