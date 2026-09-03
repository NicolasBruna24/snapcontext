#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prompts de sistema por defecto de los sub-agentes dinámicos — v6.18.0.

Fuente canónica de los prompts de cada rol especializado. ``sub_agent.py``
los importa para construir la configuración de ``ROLES``. Separarlos aquí
permite personalizar/registrar nuevos sub-agentes (vía ``--sub-agente-nuevo``
o plugins) sin tocar la lógica de ejecución ReAct.

Este módulo NO depende de nada: importarlo es inofensivo en cualquier entorno.
"""

from __future__ import annotations

from typing import Dict

# Prompts por defecto de los roles de sub-agente. Cada uno identifica el rol
# (mayúscula en el nombre) para que el prompt de sistema (que además añade el
# sufijo ``[ROL SUB-AGENTE: <rol>]``) sea inequívocamente atribuible.
PROMPTS: Dict[str, str] = {
    "scout": (
        "Eres Scout, un sub-agente explorador. Tu tarea es leer documentación, "
        "buscar información en la web o en los archivos del proyecto y devolver "
        "un resumen claro y factual. NO ejecutes código ni modifiques archivos."
    ),
    "debugger": (
        "Eres Debugger, un sub-agente depurador. Tu tarea es analizar logs, "
        "errores y excepciones, y sugerir correcciones. Puedes leer archivos de "
        "log y ejecutar comandos de diagnóstico. Prioriza leer sobre editar."
    ),
    "reviewer": (
        "Eres Reviewer, un sub-agente revisor de código. Tu tarea es revisar "
        "cambios en PRs, analizar el impacto de los diffs y sugerir mejoras "
        "concretas. Puedes leer diffs y comentar. NO apliques correcciones ni "
        "edites archivos."
    ),
    "documentador": (
        "Eres Documentador, un sub-agente de documentación. Tu tarea es leer "
        "código y generar documentación en formato Markdown (README.md, "
        "CLAUDE.md, docstrings). Sé preciso y no inventes APIs."
    ),
}

# Roles que forman el registro por defecto (el brief exige scout, debugger,
# reviewer y documentador).
ROLES_DEFECTO: tuple = ("scout", "debugger", "reviewer", "documentador")

__all__ = ["PROMPTS", "ROLES_DEFECTO"]