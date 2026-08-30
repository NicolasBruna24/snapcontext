import sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import context_utils as c
import agentes as ag
import inspect
for n in ("_metadatos_regex",):
    print("=" * 30, n)
    print(inspect.getsource(getattr(c, n)))
for n in ("_preparar_contenido_envio", "_ejecutar_con_aider"):
    print("=" * 30, n)
    print(inspect.getsource(getattr(ag.AgenteEditorPropio, n)))
import snapcontext as sc
print("=" * 30, "_splicear_bloque")
print(inspect.getsource(sc._splicear_bloque))
