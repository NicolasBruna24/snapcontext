#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests de la auto-detección de tipo de proyecto (snapcontext.py).

Cubre ``_detectar_tipo_proyecto``, ``_ajustar_parametros_por_tipo``, el filtro
por extensiones del escaneo y los alias de comandos (``_preparar_argv_aliases``).
Se ejecuta con:

    python -m pytest tests -v
    python -m unittest tests.test_deteccion -v
"""
import argparse
import os
import shutil
import tempfile
import unittest
from pathlib import Path

import snapcontext as sc


def _crear_directorio(archivos):
    """Crea un directorio temporal con los archivos/carpetas indicados.

    ``archivos`` es un dict {ruta_relativa: contenido|None}. Si el valor es
    ``None`` se crea una carpeta; si no, un archivo de texto.
    """
    tmp = tempfile.mkdtemp()
    for nombre, contenido in archivos.items():
        ruta = os.path.join(tmp, nombre)
        if contenido is None:
            os.makedirs(ruta, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(ruta) or tmp, exist_ok=True)
            with open(ruta, "w", encoding="utf-8") as fh:
                fh.write(contenido)
    return tmp


class TestDetectarTipoProyecto(unittest.TestCase):
    def tearDown(self):
        if hasattr(self, "_tmp") and self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)
        self._tmp = None

    def _detectar(self, archivos):
        self._tmp = _crear_directorio(archivos)
        return sc._detectar_tipo_proyecto(self._tmp)

    def test_flutter(self):
        self.assertEqual(self._detectar({"pubspec.yaml": "name: x"}), "flutter")

    def test_node(self):
        self.assertEqual(self._detectar({"package.json": "{}"}), "node")

    def test_python_requirements(self):
        self.assertEqual(self._detectar({"requirements.txt": ""}), "python")

    def test_python_pyproject(self):
        self.assertEqual(self._detectar({"pyproject.toml": ""}), "python")

    def test_go(self):
        self.assertEqual(self._detectar({"go.mod": ""}), "go")

    def test_rust(self):
        self.assertEqual(self._detectar({"Cargo.toml": ""}), "rust")

    def test_sin_tipo_devuelve_none(self):
        self.assertIsNone(self._detectar({"_vacío.txt": ""}))

    def test_por_carpeta_tipica(self):
        # Sin archivo identificador, la carpetilla lib/ sugiere flutter.
        self.assertEqual(self._detectar({"lib": None}), "flutter")

    def test_directorio_inexistente(self):
        self.assertIsNone(sc._detectar_tipo_proyecto(
            os.path.join(os.getcwd(), "_no_existe_xyz")))


class TestAjustarParametrosPorTipo(unittest.TestCase):
    def test_ajusta_carpetas_y_extensiones(self):
        args = argparse.Namespace(carpetas=None)
        out = sc._ajustar_parametros_por_tipo("python", args)
        self.assertEqual(out.carpetas, ["src", "app", "lib", "tests", "scripts"])
        self.assertEqual(out.extensiones, [".py", ".pyx", ".pxd", ".ipynb"])

    def test_respeta_carpetas_explicitas(self):
        args = argparse.Namespace(carpetas=["mis", "carpetas"])
        out = sc._ajustar_parametros_por_tipo("flutter", args)
        self.assertEqual(out.carpetas, ["mis", "carpetas"])
        self.assertFalse(hasattr(out, "extensiones"))

    def test_sin_tipo_usa_defecto(self):
        args = argparse.Namespace(carpetas=None)
        out = sc._ajustar_parametros_por_tipo(None, args)
        self.assertIsNone(out.carpetas)
        self.assertFalse(hasattr(out, "extensiones"))


class TestFiltroExtensionesEscaneo(unittest.TestCase):
    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_solo_extension_filtrada(self):
        self._tmp = _crear_directorio({"lib/a.dart": "void main(){}",
                                       "lib/b.js": "console.log(1)"})
        candidatos = sc.listar_archivos_candidatos(
            Path(self._tmp), ["lib"], extensiones=[".dart"]
        )
        self.assertEqual(candidatos, ["lib/a.dart"])


class TestAliases(unittest.TestCase):
    def test_fix(self):
        self.assertEqual(sc._preparar_argv_aliases(["fix", "hola"]),
                         ["--test-loop", "hola"])

    def test_review(self):
        self.assertEqual(sc._preparar_argv_aliases(["review", "hola"]),
                         ["--vista-previa", "--experto", "hola"])

    def test_server(self):
        self.assertEqual(sc._preparar_argv_aliases(["server", "hola"]),
                         ["--server-loop", "hola"])

    def test_interactive(self):
        self.assertEqual(sc._preparar_argv_aliases(["interactive"]), ["--web"])

    def test_no_alias_se_trata_como_consulta(self):
        argv = ["arreglar el login"]
        self.assertEqual(sc._preparar_argv_aliases(argv), argv)

    def test_vacio(self):
        self.assertEqual(sc._preparar_argv_aliases([]), [])
        self.assertEqual(sc._preparar_argv_aliases(None), [])


class TestModoDemo(unittest.TestCase):
    """El modo --demo debe ser autónomo, offline y terminar en éxito."""

    def test_parser_tiene_flag_demo(self):
        args = sc.crear_parser().parse_args(["--demo"])
        self.assertTrue(args.demo)

    def test_crear_demo_proyecto_genera_archivos(self):
        tmp = Path(tempfile.mkdtemp(prefix="sc-test-cdp-"))
        try:
            sc._crear_demo_proyecto(tmp)
            self.assertTrue((tmp / "requirements.txt").is_file())
            self.assertTrue((tmp / "src" / "main.py").is_file())
            self.assertTrue((tmp / "tests" / "test_main.py").is_file())
            # El archivo tiene el bug de la demo.
            self.assertIn("return f\"Hola, {name}\"",
                          (tmp / "src" / "main.py").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_ejecutar_demo_termina_en_exito(self):
        self.assertEqual(sc._ejecutar_demo(), 0)


if __name__ == "__main__":
    unittest.main()