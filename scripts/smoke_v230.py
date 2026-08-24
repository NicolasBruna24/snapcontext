import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import snapcontext as sc

sc._contexto_plan_reiniciar()

# 1) normalización de pasos mcp
pasos = sc._normalizar_pasos({"pasos": [
    {"descripcion": "buscar", "accion": "mcp",
     "herramienta": "grep", "args": {"patron": "def "},
     "variable": "coincidencias"},
    {"descripcion": "sin herramienta", "accion": "mcp"},
]})
assert pasos[0]["herramienta"] == "grep"
assert pasos[0]["args"] == {"patron": "def "}
assert pasos[0]["variable"] == "coincidencias"
print("normalizar mcp OK:", len(pasos))

# 2) condiciones dinámicas
sc._registrar_resultado_plan(1, True, "ok")
ctx = sc._CONTEXTO_PLAN
sc._contexto_plan_variable("mi_variable", "listo")
assert sc._evaluar_condicion("pasos[1].resultado == 'ok'")
assert not sc._evaluar_condicion("pasos[1].resultado == 'fallo'")
assert sc._evaluar_condicion("resultados.mi_variable == 'listo'")
assert sc._evaluar_condicion("mi_variable != ''")
assert sc._evaluar_condicion("variable_existe('mi_variable')")
assert not sc._evaluar_condicion("variable_existe('otra')")
# formas clásicas siguen funcionando
import tempfile, os
tmp = tempfile.mkdtemp()
open(os.path.join(tmp, "x.txt"), "w").write("hola")
assert sc._evaluar_condicion("archivo_existe('x.txt')", tmp)
assert sc._evaluar_condicion("archivo_contiene('x.txt', 'hola')", tmp)
print("condiciones OK")

# 3) marcadores
sc._contexto_plan_variable("resultado", {"stdout": "42"})
assert sc._resolver_marcadores("valor: {{resultado}}") == 'valor: {"stdout": "42"}'
assert sc._resolver_marcadores("{{desconocida}}") == "{{desconocida}}"
print("marcadores OK")

# 4) refs de condición
idx, nombres = sc._refs_de_condicion("pasos[2].resultado == 'ok' && x != ''")
assert idx == {1} and nombres == {"x"}, (idx, nombres)
print("refs OK")

# 5) rama mcp del paso del plan (con mock del dispatcher)
import argparse, unittest.mock as mock
args = argparse.Namespace(auto=True, confirmar=False)
paso = {"descripcion": "listar", "accion": "mcp", "herramienta": "list_files",
        "args": {}, "variable": ""}
with mock.patch.object(sc, "_ejecutar_herramienta_mcp",
                       return_value={"ok": True, "resultado": {"archivos": ["a"]}}):
    ok, detalle = sc._ejecutar_paso_plan(paso, args, ".")
assert ok, detalle
assert ctx["variables"]["list_files"] == {"archivos": ["a"]}
print("paso mcp OK:", detalle)

# 6) paralelo: paso bloqueado por variable pendiente
pasos_par = [
    {"descripcion": "prod", "accion": "mcp", "herramienta": "git_status",
     "args": {}, "dependencias": [], "condicion": ""},
    {"descripcion": "consumidor", "accion": "ejecutar", "comando": "echo hi",
     "dependencias": [], "condicion": "resultados.git_status != ''"},
]
llamadas = []
def falso_paso(paso, args, raiz):
    llamadas.append(paso["descripcion"])
    return (True, "ok")
with mock.patch.object(sc, "_ejecutar_paso_plan", side_effect=falso_paso), \
     mock.patch.object(sc, "_ejecutar_herramienta_mcp",
                       return_value={"ok": True, "resultado": {"rama": "main"}}):
    res = sc._ejecutar_plan_en_paralelo(pasos_par, args, ".", max_hilos=2)
estados = {r["paso"]: r["resultado"] for r in res}
assert estados == {1: "éxito", 2: "éxito"}, estados
assert llamadas[0] == "prod", llamadas   # el productor va primero
print("paralelo dinámico OK")

print("SMOKE TEST COMPLETO")
