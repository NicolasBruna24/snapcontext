"""Tests de estado.py: subprocesos activos y procesos en segundo plano.

Todo el I/O real está mockeado (Popen, sandbox, lecturas de pipes).
"""

from __future__ import annotations

import io
import types
import unittest
from unittest import mock

import estado as estado_mod
import snapcontext as sc
from estado import _apagar_subprocesos, _estado_proceso_fondo, _lanzar_proceso_fondo


def _proceso(poll=None, stdout=None, stderr=None, returncode=0):
    """Crea un doble de subprocess.Popen."""
    proc = mock.Mock()
    proc.poll = mock.Mock(return_value=poll)
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    proc.pid = 4242
    return proc


class TestApagarSubprocesos(unittest.TestCase):
    def test_termina_los_activos(self):
        proc = _proceso(poll=None)
        with mock.patch.object(estado_mod, "_PROCESOS_ACTIVOS", {proc}):
            _apagar_subprocesos()
        proc.terminate.assert_called_once()

    def test_no_termina_los_finalizados(self):
        proc = _proceso(poll=0)
        with mock.patch.object(estado_mod, "_PROCESOS_ACTIVOS", {proc}):
            _apagar_subprocesos()
        proc.terminate.assert_not_called()

    def test_errores_al_terminar_no_propagan(self):
        proc = _proceso(poll=None)
        proc.terminate.side_effect = OSError("boom")
        proc2 = mock.Mock()  # .poll lanza AttributeError al no estar configurado
        proc2.poll = mock.Mock(side_effect=AttributeError("sin poll"))
        with mock.patch.object(estado_mod, "_PROCESOS_ACTIVOS", {proc, proc2}):
            _apagar_subprocesos()  # no debe lanzar


class TestLanzarProcesoFondo(unittest.TestCase):
    def test_directorio_inexistente(self):
        resultado = _lanzar_proceso_fondo("ls", "/no/existe/xyz")
        self.assertFalse(resultado["ok"])
        self.assertIn("no existe", resultado["error"])

    def test_comando_peligroso_sin_sandbox(self):
        with (
            mock.patch.object(sc, "_SANDBOX_ACTIVO", False),
            mock.patch.object(sc, "_es_comando_peligroso", return_value=True),
        ):
            resultado = _lanzar_proceso_fondo("rm -rf /", ".")
        self.assertFalse(resultado["ok"])
        self.assertIn("peligroso", resultado["error"])

    def test_lanzamiento_normal(self):
        registro = {}
        proc = _proceso(poll=None)
        with (
            mock.patch.object(sc, "_SANDBOX_ACTIVO", False),
            mock.patch.object(sc, "_es_comando_peligroso", return_value=False),
            mock.patch.object(estado_mod, "_PROCESOS_FONDO", registro),
            mock.patch(
                "sandbox_utils.lanzar_proceso_fondo_seguro", return_value=proc
            ) as lanzar,
        ):
            resultado = _lanzar_proceso_fondo("flutter run", ".", capture_output=False)
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["pid"], 4242)
        lanzar.assert_called_once()
        self.assertIn(4242, registro)
        self.assertEqual(registro[4242]["estado"], "ejecutando")

    def test_os_error_se_maneja(self):
        with (
            mock.patch.object(sc, "_SANDBOX_ACTIVO", False),
            mock.patch.object(sc, "_es_comando_peligroso", return_value=False),
            mock.patch.object(estado_mod, "_PROCESOS_FONDO", {}),
            mock.patch(
                "sandbox_utils.lanzar_proceso_fondo_seguro",
                side_effect=OSError("sin memoria"),
            ),
        ):
            resultado = _lanzar_proceso_fondo("ls", ".")
        self.assertFalse(resultado["ok"])

    def test_sandbox_envuelve_el_comando(self):
        proc = _proceso(poll=None)
        fake_su = types.SimpleNamespace(lanzar_proceso_fondo_seguro=mock.Mock(return_value=proc))
        with (
            mock.patch.object(sc, "_SANDBOX_ACTIVO", True),
            mock.patch.object(sc, "_SESION_DOCKER_SOLICITADA", False),
            mock.patch.object(sc, "_envolver_sandbox", return_value="docker-run cmd"),
            mock.patch.object(estado_mod, "_PROCESOS_FONDO", {}),
            mock.patch.dict("sys.modules", {"sandbox_utils": fake_su}),
        ):
            resultado = _lanzar_proceso_fondo("cmd", ".")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["comando"], "docker-run cmd")


class TestEstadoProcesoFondo(unittest.TestCase):
    def test_pid_desconocido(self):
        with mock.patch.object(estado_mod, "_PROCESOS_FONDO", {}):
            resultado = _estado_proceso_fondo(999)
        self.assertFalse(resultado["ok"])
        self.assertEqual(resultado["estado"], "desconocido")

    def test_registro_sin_proceso(self):
        registro = {5: {"ok": True, "estado": "ejecutando", "pid": 5, "proceso": None}}
        with mock.patch.object(estado_mod, "_PROCESOS_FONDO", registro):
            resultado = _estado_proceso_fondo(5)
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["estado"], "ejecutando")

    def test_proceso_en_marcha(self):
        registro = {6: {"estado": "ejecutando", "proceso": _proceso(poll=None)}}
        with mock.patch.object(estado_mod, "_PROCESOS_FONDO", registro):
            resultado = _estado_proceso_fondo(6)
        self.assertEqual(resultado["estado"], "ejecutando")

    def test_proceso_finalizado_captura_salida(self):
        proc = _proceso(
            poll=0,
            stdout=io.StringIO("salida"),
            stderr=io.StringIO("error"),
            returncode=3,
        )
        registro = {7: {"estado": "ejecutando", "stdout": "", "stderr": "", "proceso": proc}}
        with mock.patch.object(estado_mod, "_PROCESOS_FONDO", registro):
            resultado = _estado_proceso_fondo(7)
        self.assertEqual(resultado["estado"], "finalizado")
        self.assertEqual(resultado["codigo_retorno"], 3)
        self.assertEqual(resultado["stdout"], "salida")
        self.assertEqual(resultado["stderr"], "error")

    def test_proceso_finalizado_sin_pipes(self):
        proc = _proceso(poll=0, stdout=None, stderr=None, returncode=0)
        registro = {8: {"estado": "ejecutando", "stdout": "", "stderr": "", "proceso": proc}}
        with mock.patch.object(estado_mod, "_PROCESOS_FONDO", registro):
            resultado = _estado_proceso_fondo(8)
        self.assertEqual(resultado["estado"], "finalizado")
        self.assertEqual(resultado["stdout"], "")


class TestSanityReexport(unittest.TestCase):
    def test_sc_comparte_los_mismos_registros(self):
        self.assertIs(sc._PROCESOS_FONDO, estado_mod._PROCESOS_FONDO)
        self.assertIs(sc._PROCESOS_ACTIVOS, estado_mod._PROCESOS_ACTIVOS)

    def test_sandbox_sesion_docker(self):
        proc = _proceso(poll=None)
        fake_ss = types.SimpleNamespace(comando_en_sesion=mock.Mock(return_value="sesion cmd"))
        fake_su = types.SimpleNamespace(lanzar_proceso_fondo_seguro=mock.Mock(return_value=proc))
        with (
            mock.patch.object(sc, "_SANDBOX_ACTIVO", True),
            mock.patch.object(sc, "_SESION_DOCKER_SOLICITADA", True),
            mock.patch.object(sc, "_asegurar_sesion_docker", return_value=True),
            mock.patch.dict(
                "sys.modules", {"sandbox_session": fake_ss, "sandbox_utils": fake_su}
            ),
            mock.patch.object(estado_mod, "_PROCESOS_FONDO", {}),
        ):
            resultado = _lanzar_proceso_fondo("cmd", ".")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["comando"], "sesion cmd")

    def test_sandbox_sesion_sin_docker_cae_en_contenedor(self):
        proc = _proceso(poll=None)
        fake_su = types.SimpleNamespace(lanzar_proceso_fondo_seguro=mock.Mock(return_value=proc))
        with (
            mock.patch.object(sc, "_SANDBOX_ACTIVO", True),
            mock.patch.object(sc, "_SESION_DOCKER_SOLICITADA", True),
            mock.patch.object(sc, "_asegurar_sesion_docker", return_value=False),
            mock.patch.object(sc, "_envolver_sandbox", return_value="envuelto"),
            mock.patch.dict("sys.modules", {"sandbox_utils": fake_su}),
            mock.patch.object(estado_mod, "_PROCESOS_FONDO", {}),
        ):
            resultado = _lanzar_proceso_fondo("cmd", ".")
        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["comando"], "envuelto")


if __name__ == "__main__":
    unittest.main()

