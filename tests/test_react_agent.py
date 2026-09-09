"""Tests de helpers y herramientas del agente ReAct — Fase 1 de cobertura.

Cubre utilidades puras (estimación de tokens, extracción JSON, observación de
resultados, rutas seguras, resumen de historial) sin llamar a LLM ni a
herramientas reales. Para métodos que solo usan atributos, se instancia con
``object.__new__``.
"""

from __future__ import annotations

import os
from pathlib import Path

from react_agent import (
    ReactAgent,
    _max_historial,
    _ns_git,
    _umbral_resumen,
    estimar_tokens,
)


def _agente(directorio: str = ".") -> ReactAgent:
    ag = ReactAgent.__new__(ReactAgent)
    ag.directorio = str(Path(directorio).resolve())
    ag.historial = []
    ag.max_historial = 20
    return ag


class TestEstimarTokens:
    def test_texto_vacio_minimo(self):
        assert estimar_tokens("") == 1

    def test_estimacion_aprox(self):
        assert estimar_tokens("abcd") == 1
        assert estimar_tokens("a" * 8) == 2


class TestMaxHistorial:
    def test_por_defecto(self, monkeypatch):
        monkeypatch.delenv("REACT_MAX_HISTORIAL", raising=False)
        assert _max_historial() >= 1

    def test_valor_valido(self, monkeypatch):
        monkeypatch.setenv("REACT_MAX_HISTORIAL", "30")
        assert _max_historial() == 30

    def test_valor_invalido_usa_default(self, monkeypatch):
        monkeypatch.setenv("REACT_MAX_HISTORIAL", "abc")
        assert _max_historial() > 0


class TestUmbralResumen:
    def test_por_defecto(self, monkeypatch):
        monkeypatch.delenv("REACT_UMBRAL_RESUMEN", raising=False)
        assert _umbral_resumen() >= 1000

    def test_valor_valido(self, monkeypatch):
        monkeypatch.setenv("REACT_UMBRAL_RESUMEN", "5000")
        assert _umbral_resumen() == 5000

    def test_valor_invalido(self, monkeypatch):
        monkeypatch.setenv("REACT_UMBRAL_RESUMEN", "x")
        assert _umbral_resumen() >= 1000


class TestNsGit:
    def test_crea_namespace(self):
        ns = _ns_git("mensaje")
        assert ns.git_mensaje == "mensaje"

    def test_none(self):
        assert _ns_git(None).git_mensaje is None


class TestExtraerJson:
    def test_extrae_objeto_limitado(self):
        decision = ReactAgent._extraer_json('{"accion": "leer_archivo", "argumentos": {}}')
        assert decision["accion"] == "leer_archivo"

    def test_texto_con_fences(self):
        decision = ReactAgent._extraer_json('```json\n{"accion": "finalizar"}\n```')
        assert decision["accion"] == "finalizar"

    def test_texto_con_ruido(self):
        decision = ReactAgent._extraer_json('texto previo\n{"accion": "x"}\nsalida')
        assert decision is not None and decision["accion"] == "x"

    def test_json_invalido_devuelve_none(self):
        assert ReactAgent._extraer_json("no soy json") is None
        assert ReactAgent._extraer_json("") is None


class TestObservarResultado:
    def test_ok_con_campos(self):
        mensaje = ReactAgent._observar_resultado({"ok": True, "ruta": "a.py", "codigo": 0})
        assert mensaje.startswith("ok")
        assert "ruta=a.py" in mensaje

    def test_fallo_con_error_y_stderr(self):
        mensaje = ReactAgent._observar_resultado(
            {"ok": False, "error": "boom", "stderr": "detalle"}
        )
        assert mensaje.startswith("FALLO")
        assert "error: boom" in mensaje
        assert "detalle" in mensaje

    def test_lista_en_stdout(self):
        mensaje = ReactAgent._observar_resultado({"ok": True, "stdout": ["a", "b"]})
        assert "a\nb" in mensaje

    def test_truncamiento(self):
        mensaje = ReactAgent._observar_resultado({"ok": True, "contenido": "x" * 5000})
        assert "…(salida truncada)" in mensaje


class TestRutaSegura:
    def test_ruta_dentro(self, tmp_path: Path):
        ag = _agente(str(tmp_path))
        (tmp_path / "a.py").write_text("x")
        ruta = ag._ruta_segura("a.py")
        assert ruta is not None and ruta.name == "a.py"

    def test_ruta_fuera_devuelve_none(self, tmp_path: Path):
        ag = _agente(str(tmp_path))
        assert ag._ruta_segura("/etc/hostname") is None


class TestToolLeerArchivo:
    def test_sin_ruta_error(self, tmp_path: Path):
        ag = _agente(str(tmp_path))
        r = ag._tool_leer_archivo({})
        assert r["ok"] is False
        assert "ruta" in r["error"]

    def test_lee_archivo(self, tmp_path: Path):
        ag = _agente(str(tmp_path))
        (tmp_path / "a.py").write_text("print('hola')\n")
        r = ag._tool_leer_archivo({"ruta": "a.py"})
        assert r["ok"] is True
        assert "hola" in r["contenido"]

    def test_archivo_inexistente(self, tmp_path: Path):
        ag = _agente(str(tmp_path))
        r = ag._tool_leer_archivo({"ruta": "no_existe.py"})
        assert r["ok"] is False

    def test_archivo_grande_se_trunca(self, tmp_path: Path):
        ag = _agente(str(tmp_path))
        (tmp_path / "grande.txt").write_text("x" * 10000)
        r = ag._tool_leer_archivo({"ruta": "grande.txt"})
        assert r["ok"] is True
        assert "…(truncado)" in r["contenido"]


class TestHistorial:
    def test_tokens_historial(self):
        ag = _agente()
        ag.historial = [
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "mundo"},
        ]
        assert ag._tokens_historial() >= 1

    def test_resumir_por_longitud_falso(self):
        ag = _agente()
        ag.max_historial = 5
        ag.historial = [{"role": "user", "content": "x"}] * 10
        assert ag._resumir_por_longitud() is False

    def test_resumir_por_longitud_true(self):
        ag = _agente()
        ag.max_historial = 2
        ag.historial = [{"role": "user", "content": "x"}] * 30
        assert ag._resumir_por_longitud() is True
