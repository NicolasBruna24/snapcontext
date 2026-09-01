# -*- coding: utf-8 -*-
"""Repara el mojibake (UTF-8 leído como cp1252) en archivos de tests."""
import pathlib
import re

FILES = [
    "test_auto_017", "test_diagnostico_310", "test_diff_editor_210",
    "test_editor_ast_220", "test_editor_propio_200", "test_exe_150",
    "test_jetbrains_170", "test_mcp_014", "test_mcp_140",
    "test_memoria_015", "test_memoria_300", "test_multi_agent",
    "test_omnicanalidad_avanzada", "test_parser_universal",
    "test_permisos_013", "test_plan_012", "test_plan_mcp_230",
    "test_skill_abstraction", "test_v010", "test_validacion_130",
    "test_vscode_016", "test_web_160",
]

PAT = re.compile(
    "[\u0080-\u00ff\u0152\u0153\u0178\u2018\u2019\u201c\u201d\u2013\u2014"
    "\u2020\u2026\u2039\u203a\u20ac\u2122]{2,}")


def _fix(m):
    try:
        return m.group(0).encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return m.group(0)


for name in FILES:
    p = pathlib.Path("tests") / (name + ".py")
    t = p.read_text(encoding="utf-8")
    if ("\u00f0" not in t and "\u00c3" not in t
            and "\u00e2\u20ac" not in t):
        continue
    fixed = PAT.sub(_fix, t)
    p.write_text(fixed, encoding="utf-8", newline="\n")
    print("reparado", name)
