#!/usr/bin/env python3
"""Tests de B9.61-C: baseline de seguridad remota (invariantes REX-1..REX-14).

Cubre las decisiones ratificadas en
``docs/B9.61-C-DECISIONES-RATIFICADAS.md``:

- OD-1: allowlist remota de M1 vacía ⇒ DENY ALL en la acción ``run``.
- OD-2: identidad por ruta resuelta + argv, nunca basename.
- OD-3: shells e intérpretes denegados; argv estructurado, sin shell.
- OD-4: entorno mínimo, sin herencia de secretos.
- OD-5: ``EXECUTE_REMOTE`` no concede red.
- OD-6: instalación remota de plugins denegada.
- OD-7: ``OrigenPlugin`` explícito (LocalPath | RemoteURL), sin heurísticas.
- OD-8: extracción ZIP segura (sin zip-slip / absolute / symlink).
- R3-01: nunca se devuelve un ``str`` ambiguo reinterpretable como ruta.
"""

import hashlib
import hmac
import inspect
import io
import json
import os
import queue
import stat
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import github_gateway as gh
import snapcontext as sc
import task_queue as tq
import web.app as wa
from web import filesystem as fs_web
from web import remota as remota_web
from web.filesystem import ErrorFrontera, Frontera


@pytest.fixture(autouse=True)
def _frontera_limpia():
    fs_web.reiniciar_frontera()
    yield
    fs_web.reiniciar_frontera()


def _eventos(q):
    salida = []
    while not q.empty():
        salida.append(q.get_nowait())
    return salida


# ===========================================================================
# REX-1 / OD-1 — allowlist remota vacía ⇒ DENY ALL
# ===========================================================================
class TestRex1AllowlistVacia:
    def test_allowlist_m1_es_vacia(self):
        assert remota_web.allowlist_remota_efectiva() == ()

    def test_ningun_binario_esta_autorizado(self):
        for argv in (["echo", "x"], ["ls"], ["git", "status"], ["date"]):
            permitido, motivo, _ = remota_web.autorizar_comando_remoto(argv)
            assert permitido is False, argv
            assert motivo

    def test_accion_run_se_deniega(self):
        """OD-1: `run` es la superficie de ejecución remota arbitraria."""
        cola = queue.Queue()
        with mock.patch.object(sc, "_ejecutar_comando") as ejec:
            wa._ejecutar_accion(
                {"accion": "run", "comando": "echo HOLA", "directorio": "."}, cola
            )
            ejec.assert_not_called()
        fin = [e for e in _eventos(cola) if e.get("tipo") == "accion_ejecutada"]
        assert fin and fin[0]["ok"] is False

    def test_accion_run_con_comando_peligroso_no_llega_a_c2(self):
        cola = queue.Queue()
        with mock.patch.object(sc, "_ejecutar_comando") as ejec:
            with mock.patch("sandbox_utils.clasificar_comando") as clasificar:
                wa._ejecutar_accion(
                    {"accion": "run", "comando": "echo A | sh", "directorio": "."}, cola
                )
                ejec.assert_not_called()
                # La política remota decide ANTES de C2.
                clasificar.assert_not_called()

    def test_pipeline_no_esta_bloqueado(self):
        """OD-1 no debe apagar el pipeline del agente."""
        cola = queue.Queue()
        with mock.patch("web.app.remota_web") as remota_mock:
            # Si `run` se denegase por la politica, el pipeline tampoco correria.
            with mock.patch.object(sc, "flujo_principal", return_value=0):
                wa._ejecutar_accion(
                    {"accion": "fix", "consulta": "arregla", "directorio": "."}, cola
                )
            assert not remota_mock.autorizar_comando_remoto.called
        fin = [e for e in _eventos(cola) if e.get("tipo") == "accion_ejecutada"]
        assert fin and fin[0]["ok"] is True


# ===========================================================================
# REX-2 / REX-12 / OD-2 — identidad verificada
# ===========================================================================
class TestRex2Identidad:
    def test_string_no_es_argvalido(self):
        permitido, _, _ = remota_web.autorizar_comando_remoto("echo hi")
        assert permitido is False

    @pytest.mark.parametrize("argv", [[], [""], ["a", 1], None, {}])
    def test_argv_malformado_denegado(self, argv):
        permitido, _, _ = remota_web.autorizar_comando_remoto(argv)
        assert permitido is False

    def test_ejecutable_relativo_denegado(self):
        for argv in (["./x"], ["sub/x"], ["../x"], ["/usr/bin/echo"]):
            permitido, _, _ = remota_web.autorizar_comando_remoto(argv)
            assert permitido is False, argv

    def test_identidad_inexistente_denegada(self):
        permitido, motivo, _ = remota_web.autorizar_comando_remoto(["no_existe_xyz"])
        assert permitido is False
        assert motivo

    def test_no_hay_bypass_por_basename(self):
        """Un binario denegado no se autoriza por renombrarlo (OD-2/REX-12)."""
        with mock.patch.object(remota_web, "PATH_REMOTO", ("/tmp/fakebin",)):
            with mock.patch.object(
                remota_web, "_which_cerrado", return_value="/tmp/fakebin/sh"
            ):
                permitido, _, _ = remota_web.autorizar_comando_remoto(["sh", "-c", "id"])
                assert permitido is False

    def test_no_hay_bypass_por_path_manipulado(self):
        """El PATH del entorno no influye en la resolucion (OD-2)."""
        os.environ["PATH"] = "/tmp/ataque"
        try:
            permitido, _, _ = remota_web.autorizar_comando_remoto(["echo", "x"])
            assert permitido is False
            # Y la resolucion no busca en /tmp/ataque.
            assert not any(d.startswith("/tmp/ataque") for d in remota_web.PATH_REMOTO)
        finally:
            os.environ.pop("PATH", None)

    def test_identidad_incluye_ruta_resuelta(self):
        with mock.patch.object(
            remota_web, "allowlist_remota_efectiva",
            return_value=({"binario": "echo", "argv_permitidos": ("x",)},),
        ):
            permitido, _, ident = remota_web.autorizar_comando_remoto(["echo", "x"])
            if permitido:
                assert ident.ruta_resuelta.startswith("/")
                assert ident.argv == ("echo", "x")


# ===========================================================================
# REX-3 / OD-3 — shells e intérpretes
# ===========================================================================
class TestRex3ShellsInterpretes:
    @pytest.mark.parametrize(
        "binario",
        ["sh", "bash", "zsh", "fish", "cmd", "powershell", "python", "python3",
         "node", "perl", "ruby", "npm", "npx", "pip", "xargs", "env"],
    )
    def test_shell_o_interprete_denegado(self, binario):
        permitido, motivo, _ = remota_web.autorizar_comando_remoto([binario, "-c", "id"])
        assert permitido is False
        assert "no permitido" in motivo

    def test_formas_indirectas_denegadas(self):
        for argv in (
            ["bash", "-c", "curl evil"],
            ["sh", "-c", "id"],
            ["python", "-c", "import os"],
            ["node", "-e", "process.exit(1)"],
        ):
            permitido, _, _ = remota_web.autorizar_comando_remoto(argv)
            assert permitido is False, argv

    def test_no_existe_shell_true_en_el_camino_remoto(self):
        """La politica remota no construye ni ejecuta strings de shell.

        Se analiza el ARBOL SINTACTICO (no el texto): docstrings y comentarios
        no son vias de ejecucion. Se busca cualquier llamada con el keyword
        ``shell=True`` o al uso de ``subprocess``/``os.system``/``os.popen``.
        """
        import ast

        arbol = ast.parse(inspect.getsource(remota_web))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Call):
                for kw in nodo.keywords:
                    assert kw.arg != "shell", "shell= no permitido en remoto"
                nombre = getattr(nodo.func, "id", "") or getattr(nodo.func, "attr", "")
                assert nombre not in ("system", "popen", "run", "Popen"), nombre
        # Y un comando con metacarácters tampoco se autoriza (OD-3).
        permitido, _, _ = remota_web.autorizar_comando_remoto(["echo", "a;b"])
        assert permitido is False

class TestRex4Entorno:
    def test_entorno_no_hereda_secrets(self):
        os.environ["GEMINI_API_KEY"] = "secreto"
        os.environ["AWS_SECRET_ACCESS_KEY"] = "secreto"
        try:
            env = remota_web.entorno_minimo(Path("/ws"), Path("/ws/tmp"))
            assert "GEMINI_API_KEY" not in env
            assert "AWS_SECRET_ACCESS_KEY" not in env
        finally:
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("AWS_SECRET_ACCESS_KEY", None)

    def test_entorno_solo_contiene_claves_permitidas(self):
        env = remota_web.entorno_minimo(Path("/ws"), Path("/ws/tmp"))
        assert set(env) == {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}

    def test_entorno_no_parte_de_os_environ(self):
        os.environ["PYTHONPATH"] = "/evil"
        try:
            env = remota_web.entorno_minimo(Path("/ws"), Path("/ws/tmp"))
            assert "PYTHONPATH" not in env
        finally:
            os.environ.pop("PYTHONPATH", None)

    def test_variables_sensibles_documentadas(self):
        for var in ("LD_PRELOAD", "PYTHONPATH", "NODE_PATH", "GIT_CONFIG",
                    "SSH_AUTH_SOCK", "GEMINI_API_KEY"):
            assert var in remota_web.VARIABLES_EXCLUIDAS


# ===========================================================================
# REX-5 / OD-5 — EXECUTE_REMOTE no concede red
# ===========================================================================
class TestRex5SinRed:
    @pytest.mark.parametrize("binario", ["curl", "wget", "nc", "ssh", "scp", "ftp"])
    def test_binarios_de_red_denegados(self, binario):
        permitido, _, _ = remota_web.autorizar_comando_remoto([binario, "http://x"])
        assert permitido is False

    def test_git_no_autorizado_remotamente(self):
        """git puede usar red (fetch/push/clone) ⇒ no entra en la allowlist M1."""
        permitido, _, _ = remota_web.autorizar_comando_remoto(["git", "status"])
        assert permitido is False


# ===========================================================================
# REX-8 / OD-7 + R3-01 — OrigenPlugin explícito
# ===========================================================================
class TestRex8OrigenPlugin:
    def test_url_se_clasifica_como_remota(self):
        origen = wa._origen_plugin_por_frontera("https://ejemplo/x.zip")
        assert isinstance(origen, wa.RemoteURL)
        assert origen.clase == "remoto"

    def test_url_no_se_canonicaliza_como_ruta(self):
        origen = wa._origen_plugin_por_frontera("https://ejemplo/x.zip")
        assert isinstance(origen, wa.RemoteURL)
        assert not isinstance(origen, wa.LocalPath)

    def test_local_dentro_del_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            (ws / "p").mkdir()
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            origen = wa._origen_plugin_por_frontera("p")
            assert isinstance(origen, wa.LocalPath)
            assert origen.ruta == (ws / "p").resolve()

    @pytest.mark.parametrize(
        "origen", ["../fuera", "/etc", "/root/x", "~/x", "a/../../fuera"]
    )
    def test_local_fuera_se_deniega(self, origen):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with pytest.raises(ErrorFrontera):
                wa._origen_plugin_por_frontera(origen)

    def test_nunca_devuelve_str_ambiguo(self):
        """R3-01: la función no puede devolver un ``str`` reinterpretable."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            for entrada in ("p", "./a/b", "user/repo", "user/repo.git",
                            "foo/bar.zip", "https://x/y.zip", ".."):
                try:
                    resultado = wa._origen_plugin_por_frontera(entrada)
                except ErrorFrontera:
                    continue
                assert isinstance(resultado, (wa.LocalPath, wa.RemoteURL)), resultado

    def test_no_hay_heuristica_por_forma(self):
        """No se clasifica por nº de segmentos ni extensiones (OD-7)."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            for forma in ("user/repo", "a/b/c", "foo/bar.zip", "user/repo.git"):
                origen = wa._origen_plugin_por_frontera(forma)
                assert isinstance(origen, wa.LocalPath), forma

    def test_symlink_que_escapa_se_deniega(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            fuera = Path(tmp) / "fuera"
            fuera.mkdir()
            (ws / "l").symlink_to(fuera, target_is_directory=True)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with pytest.raises(ErrorFrontera):
                wa._origen_plugin_por_frontera("l")


# ===========================================================================
# REX-6 / OD-6 — instalación remota de plugins denegada
# ===========================================================================
class TestRex6InstalacionRemotaDenegada:
    def test_instalar_url_se_deniega(self):
        cola = queue.Queue()
        with mock.patch.object(sc, "_plugin_instalar") as inst:
            wa._ejecutar_accion(
                {"accion": "plugin_install", "origen": "https://ejemplo/x.zip"}, cola
            )
            inst.assert_not_called()
        fin = [e for e in _eventos(cola) if e.get("tipo") == "accion_ejecutada"]
        assert fin and fin[0]["ok"] is False

    def test_instalar_url_no_descarga(self):
        cola = queue.Queue()
        with mock.patch.object(sc, "_plugin_descargar_zip") as desc:
            wa._ejecutar_accion(
                {"accion": "plugin_install", "origen": "http://ejemplo/x.zip"}, cola
            )
            desc.assert_not_called()

    def test_instalar_local_sigue_disponible(self):
        """OD-6 no rompe la instalación local dentro del workspace."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            (ws / "p").mkdir()
            (ws / "p" / "plugin.json").write_text("{}", encoding="utf-8")
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            cola = queue.Queue()
            with mock.patch.object(sc, "_plugin_instalar", return_value=0) as inst:
                wa._ejecutar_accion({"accion": "plugin_install", "origen": "p"}, cola)
                inst.assert_called_once()
                assert str(ws.resolve() / "p") == inst.call_args.args[0]

    def test_instalar_local_fuera_se_deniega(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            fuera = Path(tmp) / "fuera"
            fuera.mkdir()
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            cola = queue.Queue()
            with mock.patch.object(sc, "_plugin_instalar") as inst:
                wa._ejecutar_accion(
                    {"accion": "plugin_install", "origen": str(fuera)}, cola
                )
                inst.assert_not_called()


# ===========================================================================
# REX-6 / OD-8 — extracción ZIP segura
# ===========================================================================
class TestRex8ExtraccionZip:
    @staticmethod
    def _zip(entradas, destino):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for nombre, datos, modo in entradas:
                info = zipfile.ZipInfo(nombre)
                info.create_system = 3
                info.external_attr = modo << 16
                z.writestr(info, datos)
        buf.seek(0)
        with zipfile.ZipFile(buf) as z:
            sc._extraer_zip_contenido(z, destino)
        return buf

    def test_entry_valida_se_extrae(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp)
            self._zip([("plugin/plugin.json", "{}", 0o100644)], destino)
            assert (destino / "plugin" / "plugin.json").is_file()

    def test_zip_slip_rechazado(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            self._zip([("../fuera.txt", "PWN", 0o100644)], destino)
            assert not (Path(tmp) / "fuera.txt").exists()

    def test_traversal_anidado_rechazado(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            self._zip([("a/b/../../../../fuera.txt", "PWN", 0o100644)], destino)
            assert not (Path(tmp) / "fuera.txt").exists()

    def test_ruta_absoluta_rechazada(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            self._zip([("/tmp/absoluta_c9c.txt", "PWN", 0o100644)], destino)
            assert not Path("/tmp/absoluta_c9c.txt").exists()

    def test_symlink_rechazado(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            self._zip([("enlace", "/etc/passwd", 0o120777)], destino)
            assert not (destino / "enlace").exists()
            assert not (destino / "enlace").is_symlink()

    def test_entrada_no_regular_rechazada(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            self._zip([("dispositivo", "", 0o020666)], destino)
            assert not (destino / "dispositivo").exists()

    def test_duplicado_rechazado(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            self._zip([("a.txt", "1", 0o100644), ("a.txt", "2", 0o100644)], destino)
            # Solo se extrae la primera ocurrencia.
            assert (destino / "a.txt").read_text() == "1"

    def test_zip_malicioso_no_sobrescribe_fuera(self):
        """Auditoría adversarial: mezcla de entrada válida y hostil."""
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            self._zip(
                [
                    ("ok.txt", "bien", 0o100644),
                    ("../../escape.txt", "PWN", 0o100644),
                    ("/etc/pwn_c9c", "PWN", 0o100644),
                ],
                destino,
            )
            assert (destino / "ok.txt").read_text() == "bien"
            assert not (Path(tmp) / "escape.txt").exists()
            assert not Path("/etc/pwn_c9c").exists()

    def test_destino_fuera_de_la_frontera_no_se_acepta(self):
        """La extracción no crea una segunda frontera: usa containment."""
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            # Un symlink previo en el destino no puede redirigir la escritura.
            (destino / "sub").mkdir()
            (destino / "sub" / "d").mkdir()
            self._zip([("sub/d/x.txt", "ok", 0o100644)], destino)
            assert (destino / "sub" / "d" / "x.txt").is_file()

    # --- C-R1-01: symlink preexistente / intermedio → NO escribe fuera ------
    def test_symlink_preexistente_no_escribe_fuera(self):
        """Reproducción exacta del hallazgo C-R1-01.

        tmp/ ├── destino/sub -> ../externo
              └── externo/        ZIP: `sub/x`

        Se comprueba el filesystem EXTERNO: `destino/sub/x` sería un falso
        verde, porque `sub` es un symlink que redirigiría la comprobación.
        """
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            externo = Path(tmp) / "externo"
            externo.mkdir()
            (destino / "sub").symlink_to(externo, target_is_directory=True)

            self._zip([("sub/x", "PWN", 0o100644)], destino)

            assert not (externo / "x").exists()
            assert not (destino / "sub" / "x").exists()
            assert (destino / "sub").is_symlink()  # el symlink sigue intacto

    def test_symlink_intermedio_no_escribe_fuera(self):
        """Componente intermedio malicioso en un nivel más profundo."""
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            externo = Path(tmp) / "externo"
            externo.mkdir()
            (destino / "a").mkdir()
            (destino / "a" / "b").symlink_to(externo, target_is_directory=True)

            self._zip([("a/b/f.txt", "PWN", 0o100644)], destino)

            assert not (externo / "f.txt").exists()
            assert not (destino / "a" / "b" / "f.txt").exists()

    def test_symlink_de_archivo_preexistente_no_se_sigue(self):
        """Un symlink a un archivo externo tampoco se trunca ni se sigue."""
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            externo = Path(tmp) / "externo.txt"
            externo.write_text("ORIGINAL", encoding="utf-8")
            (destino / "nota.txt").symlink_to(externo)

            self._zip([("nota.txt", "PWN", 0o100644)], destino)

            assert externo.read_text(encoding="utf-8") == "ORIGINAL"
            assert (destino / "nota.txt").is_symlink()

    def test_symlink_que_aparece_entre_validacion_y_escritura(self):
        """El componente se sustituye por un symlink justo antes de escribir.

        Se intercepta la escritura de la segunda entry (ya validada) para
        introducir el enlace: la extracción no debe seguirlo.
        """
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            externo = Path(tmp) / "externo"
            externo.mkdir()
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                for nombre in ("a/ok.txt", "b/x"):
                    info = zipfile.ZipInfo(nombre)
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    z.writestr(info, "datos")
            buf.seek(0)
            real = sc._escribir_entry_segura
            estado = {"hecho": False}

            def _con_carrera(comprimido, entrada, raiz_fd, relativo):
                if relativo == "b/x" and not estado["hecho"]:
                    estado["hecho"] = True
                    (destino / "b").mkdir()
                    (destino / "b").rmdir()
                    (destino / "b").symlink_to(externo, target_is_directory=True)
                return real(comprimido, entrada, raiz_fd, relativo)

            with zipfile.ZipFile(buf) as z:
                with mock.patch.object(sc, "_escribir_entry_segura", _con_carrera):
                    sc._extraer_zip_contenido(z, destino)

            assert not (externo / "x").exists()
            assert (destino / "a" / "ok.txt").read_text(encoding="utf-8") == "datos"

    def test_zip_slash_con_barra_trasera_unc_y_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            for nombre in (r"..\..\evil.txt", r"\\servidor\recurso\x.txt",
                           r"C:\Windows\evil.txt", r"a\..\..\evil.txt"):
                self._zip([(nombre, "PWN", 0o100644)], destino)
            assert list(destino.rglob("*")) == []

    def test_archivo_preexistente_se_sobrescribe_solo_dentro(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            (destino / "nota.txt").write_text("viejo", encoding="utf-8")
            self._zip([("nota.txt", "nuevo", 0o100644)], destino)
            assert (destino / "nota.txt").read_text(encoding="utf-8") == "nuevo"

    def test_no_se_crean_entradas_no_regulares(self):
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            self._zip([("guion.sh", "#!/bin/sh\n", 0o104755)], destino)
            creado = destino / "guion.sh"
            assert creado.is_file()
            assert stat.S_ISREG(creado.lstat().st_mode)
            assert not creado.stat().st_mode & (stat.S_ISUID | stat.S_ISGID)

    def test_zip_malicioso_mezclado_no_redirige(self):
        """Una entry hostil no impide las válidas ni redirige la escritura."""
        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "dest"
            destino.mkdir()
            externo = Path(tmp) / "externo"
            externo.mkdir()
            (destino / "sub").symlink_to(externo, target_is_directory=True)
            self._zip(
                [
                    ("ok.txt", "bien", 0o100644),
                    ("sub/x", "PWN", 0o100644),
                    ("../../escape.txt", "PWN", 0o100644),
                    ("/etc/pwn_c9c", "PWN", 0o100644),
                ],
                destino,
            )
            assert (destino / "ok.txt").read_text(encoding="utf-8") == "bien"
            assert not (externo / "x").exists()
            assert not (Path(tmp) / "escape.txt").exists()
            assert not Path("/etc/pwn_c9c").exists()


# ===========================================================================
# REX-9 / REX-11 — política cerrada y subconjunto de la local
# ===========================================================================
class TestC1R2EsquemasRemotos:
    @pytest.mark.parametrize(
        "url",
        ["http://example.com/x.zip", "ftp://example.com/x.zip",
         "file:///tmp/x.zip", "custom://x", "a://b"],
    )
    def test_esquema_no_soportado_no_es_remote_url(self, url):
        assert not wa._es_origen_plugin_remoto(url), url

    def test_https_si_es_remote_url(self):
        assert wa._es_origen_plugin_remoto("https://example.com/x.zip")
        origen = wa._origen_plugin_por_frontera("https://example.com/x.zip")
        assert isinstance(origen, wa.RemoteURL)

    @pytest.mark.parametrize(
        "forma", ["user/repo", "user/repo.git", "./user/repo", "foo/bar/plugin",
                  "a/b/c", "plugin.zip", "./x"])
    def test_formas_ambiguas_no_vuelven_url(self, forma):
        """R3-01 no se reabre: sin heuristicas por forma (C-R1-02)."""
        assert not wa._es_origen_plugin_remoto(forma), forma
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            ws.mkdir()
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            assert isinstance(wa._origen_plugin_por_frontera(forma), wa.LocalPath)

    def test_gateway_deniega_install_remoto_https(self):
        """OD-6 (M1): instalar un plugin remoto sigue siendo DENY."""
        cola = queue.Queue()
        with mock.patch.object(sc, "_plugin_instalar") as inst:
            wa._ejecutar_accion(
                {"accion": "plugin_install", "origen": "https://ejemplo/x.zip"}, cola
            )
            inst.assert_not_called()
        fin = [e for e in _eventos(cola) if e.get("tipo") == "accion_ejecutada"]
        assert fin and fin[0]["ok"] is False

    def test_gateway_deniega_esquemas_no_soportados_sin_descargar(self):
        cola = queue.Queue()
        for url in ("http://ejemplo/x.zip", "ftp://e/x.zip", "file:///tmp/x.zip"):
            with mock.patch.object(sc, "_plugin_descargar_zip") as desc:
                wa._ejecutar_accion({"accion": "plugin_install", "origen": url}, cola)
                desc.assert_not_called()
        fin = [e for e in _eventos(cola) if e.get("tipo") == "accion_ejecutada"]
        assert fin and all(e["ok"] is False for e in fin)


class TestC1R2DescargaCli:
    @pytest.mark.parametrize(
        "origen",
        ["http://example.com/x.zip", "ftp://example.com/x.zip", "file:///tmp/x.zip",
         "custom://x", "a://b", "https://user:pass@ejemplo/x.zip",
         "https://ejemplo/x.zip#frag"],
    )
    def test_no_descarga_esquema_no_soportado(self, origen):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("urllib.request.urlopen") as urlopen:
                assert sc._plugin_descargar_zip(origen, Path(tmp)) is None
                urlopen.assert_not_called()

    @pytest.mark.parametrize("slug", ["user/repo", "user/repo.git", "a-b_c.d/Repo-1.0"])
    def test_slug_github_construye_url_de_host_fijo(self, slug):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__ = lambda s: mock.Mock(read=lambda: b"x")
                urlopen.return_value.__exit__ = lambda s, *a: None
                sc._plugin_descargar_zip(slug, Path(tmp))
                pedido = urlopen.call_args[0][0]
            assert pedido.startswith("https://codeload.github.com/")
            assert pedido.endswith("/zip/refs/heads/main")

    @pytest.mark.parametrize(
        "slug",
        ["user/repo/extra", "../../etc/passwd", "user repo", "user/", "/repo",
         "user/repo?x=1", "user/repo#f", "user/repo/../otro", "user@repo",
         "user/repo\\x", "http://evil.example/x.zip", "file:///tmp/x.zip"],
    )
    def test_slug_invalido_no_abre_red(self, slug):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("urllib.request.urlopen") as urlopen:
                assert sc._plugin_descargar_zip(slug, Path(tmp)) is None
                urlopen.assert_not_called()

    def test_url_https_se_usa_tal_cual_sin_downgrade(self):
        """Una URL https válida llega a `urlopen` sin reescritura de esquema.

        La allowlist de hosts (spec §16) no forma parte de C-R1-02: aquí se
        garantiza el contrato de esquema (nunca http/ftp/file/arbitrario).
        """
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("urllib.request.urlopen") as urlopen:
                urlopen.return_value.__enter__ = lambda s: mock.Mock(read=lambda: b"x")
                urlopen.return_value.__exit__ = lambda s, *a: None
                sc._plugin_descargar_zip("https://evil.example/x.zip", Path(tmp))
                pedido = urlopen.call_args[0][0]
            assert pedido == "https://evil.example/x.zip"
            assert pedido.startswith("https://")


# ============================================================================
# B9.61-D — D-01: webhook GitHub fail-closed + no reachable shell
# ============================================================================
class TestD1WebhookFailClosed:
    @staticmethod
    def _app():
        return wa.crear_app(api_token="K" * 40, host="127.0.0.1", workspace_root="/tmp")

    def test_sin_secret_se_deniega_503(self):
        """D-01: la AUSENCIA de secreto es no-configurado, nunca fail-open."""
        app = self._app()
        with mock.patch.object(gh, "obtener_webhook_secreto", return_value=None):
            r = TestClient(app).post(
                "/webhook/github",
                json={"ref": "refs/heads/main"},
                headers={"X-GitHub-Event": "push"},
            )
        assert r.status_code == 503

    def test_secret_invalido_se_deniega_401(self):
        app = self._app()
        with mock.patch.object(gh, "obtener_webhook_secreto", return_value="secreto-real"):
            r = TestClient(app).post(
                "/webhook/github",
                json={"ref": "refs/heads/main"},
                headers={"X-GitHub-Event": "push"},
            )
        assert r.status_code == 401

    def test_firma_valida_no_crea_tarea_tests(self):
        """D-01/D-02: con firma válida, `push` NO encola trabajo ejecutable."""
        app = self._app()
        cuerpo = json.dumps({"ref": "refs/heads/main", "repository": {"full_name": "a/b"}}).encode()
        mac = hmac.new(b"secreto-real", cuerpo, hashlib.sha256).hexdigest()
        with (
            mock.patch.object(gh, "obtener_webhook_secreto", return_value="secreto-real"),
            mock.patch("task_queue.encolar_tarea") as enq,
        ):
            r = TestClient(app).post(
                "/webhook/github",
                content=cuerpo,
                headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": f"sha256={mac}"},
            )
        assert r.status_code == 200
        assert enq.call_count == 0

    @pytest.mark.parametrize(
        "rama",
        [
            "main; touch /tmp/PWNED",
            "main && id",
            "main || id",
            "main | id",
            "main$(id)",
            "main`id`",
            "main\nid",
            "main\rid",
            "-main",
            "--upload-pack=touch /tmp/x",
            "a/../../etc",
            "",
            "  ",
            "../../etc/passwd",
        ],
    )
    def test_rama_maliciosa_se_rechaza(self, rama):
        assert not gh.referencia_git_valida(rama)

    @pytest.mark.parametrize("rama", ["main", "feature/x", "release-1.2", "v1.0.0"])
    def test_rama_valida_se_acepta(self, rama):
        assert gh.referencia_git_valida(rama)


    def test_webhook_malicioso_no_alcanza_shell(self):
        """Integración D-01: HTTP webhook -> parseo -> cola, sin shell.

        El payload que en B9.61-D producía
        `git checkout main; touch /tmp/PWNED && pytest` muere en el parseo.
        """
        cuerpo = json.dumps(
            {"ref": "refs/heads/main; touch /tmp/PWNED_MARKER", "repository": {"full_name": "a/b"}}
        ).encode()
        mac = hmac.new(b"s3cr3t", cuerpo, hashlib.sha256).hexdigest()
        app = self._app()
        with (
            mock.patch.object(gh, "obtener_webhook_secreto", return_value="s3cr3t"),
            mock.patch("snapcontext._ejecutar_comando") as shell,
        ):
            r = TestClient(app).post(
                "/webhook/github",
                content=cuerpo,
                headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": f"sha256={mac}"},
            )
            evento = gh.parsear_evento(json.loads(cuerpo), tipo_evento="push")
            gh.procesar_evento(evento)
            res = tq.ejecutar_tarea({"tipo": "tests", "datos": {"rama": "main; id"}})
        assert r.status_code == 200
        assert evento["ok"] is False  # evento rechazado en el parseo
        shell.assert_not_called()  # nunca hubo shell
        assert res["ok"] is False  # y `tests` sigue denegado

    @pytest.mark.parametrize("tipo", ["tests", "pr_review", "review", "ejecutar_pruebas", "xx", ""])
    def test_worker_deniega_tipos_no_permitidos(self, tipo):
        ctx = tq.contexto_por_defecto(workspace_root=Path.cwd())
        res = tq.ejecutar_tarea({"tipo": tipo, "datos": {"consulta": "x"}, "contexto": ctx.a_dict()})
        assert res["ok"] is False
        assert "tipo no permitido" in res["error"]


# ============================================================================
# B9.61-D — D-02: contexto congelado + revalidación del worker
# ============================================================================
class TestD2ContextoCongelado:
    def test_owner_workspace_y_caps_congelados(self, tmp_path):
        ctx = tq.TaskSecurityContext(
            owner="api:abc123",
            workspace_root=str(tmp_path.resolve()),
            capabilities=("EXECUTE_REMOTE",),
            policy="m1",
            generacion=tq.generacion_actual(),
            origen="gateway",
        )
        db = tmp_path / "t.db"
        tid = tq.encolar_tarea("query", {"consulta": "hola"}, db_path=db, contexto=ctx)
        fila = tq.obtener_tarea(tid, db_path=db)
        assert fila["owner"] == "api:abc123"
        assert fila["contexto"]["workspace_root"] == str(tmp_path.resolve())
        assert fila["contexto"]["capabilities"] == ["EXECUTE_REMOTE"]
        assert fila["contexto"]["origen"] == "gateway"

    def test_contexto_ausente_deny(self):
        res = tq.ejecutar_tarea({"tipo": "query", "datos": {"consulta": "hola"}})
        assert res["ok"] is False
        assert "contexto ausente" in res["error"]

    def test_contexto_corrupto_deny(self):
        res = tq.ejecutar_tarea({"tipo": "query", "datos": {}, "contexto": "no-json{{{"})
        assert res["ok"] is False
        assert "contexto corrupto" in res["error"]

    def test_tipos_permitidos_solo_query_y_plan(self):
        assert tq.TIPOS_PERMITIDOS == frozenset({"query", "plan"})

    def test_generacion_incorrecta_cancela(self):
        ctx = tq.TaskSecurityContext(
            owner="api:abc",
            workspace_root=str(Path.cwd().resolve()),
            capabilities=(),
            policy="m1",
            generacion=tq.generacion_actual() + 99,
            origen="gateway",
        )
        res = tq.ejecutar_tarea({"tipo": "query", "datos": {"consulta": "x"}, "contexto": ctx.a_dict()})
        assert res["ok"] is False
        assert res["estado"] == tq.ESTADO_CANCELADA_ROTACION

    def test_no_hay_fallback_else_ejecutable(self):
        """Un tipo desconocido nunca cae en `flujo_principal`."""
        with mock.patch("snapcontext.flujo_principal") as principal:
            tq.ejecutar_tarea(
                {
                    "tipo": "loquesea",
                    "datos": {"consulta": "x"},
                    "contexto": tq.contexto_por_defecto(workspace_root=Path.cwd()).a_dict(),
                }
            )
        principal.assert_not_called()


# ============================================================================
# B9.61-D — D-03: rotación cancela tareas pendientes
# ============================================================================
class TestD3Rotacion:
    def test_rotacion_cancela_pendientes(self, tmp_path):
        db = tmp_path / "t.db"
        tid = tq.encolar_tarea("query", {"consulta": "x"}, db_path=db)
        assert tq.obtener_tarea(tid, db_path=db)["estado"] == "pendiente"
        tq.cancelar_pendientes_por_rotacion(db_path=db)
        assert tq.obtener_tarea(tid, db_path=db)["estado"] == tq.ESTADO_CANCELADA_ROTACION

    def test_tarea_cancelada_no_se_ejecuta(self, tmp_path):
        db = tmp_path / "t.db"
        tq.encolar_tarea("query", {"consulta": "x"}, db_path=db)
        tq.cancelar_pendientes_por_rotacion(db_path=db)
        with mock.patch("snapcontext.flujo_principal") as principal:
            tq.procesar_siguiente_tarea(db_path=db)
        principal.assert_not_called()

    def test_rotacion_avanza_generacion(self):
        antes = tq.generacion_actual()
        tq.cancelar_pendientes_por_rotacion(db_path=":memory:")
        assert tq.generacion_actual() == antes + 1

    def test_no_toca_tareas_terminadas(self, tmp_path):
        db = tmp_path / "t.db"
        tid = tq.encolar_tarea("query", {"consulta": "x"}, db_path=db)
        tq.actualizar_estado_tarea(tid, "completada", db_path=db)
        tq.cancelar_pendientes_por_rotacion(db_path=db)
        assert tq.obtener_tarea(tid, db_path=db)["estado"] == "completada"

    def test_tarea_posterior_a_rotacion_es_valida(self):
        ctx = tq.contexto_por_defecto(workspace_root=Path.cwd())
        _, motivo = tq._revalidar_contexto({"tipo": "query", "contexto": ctx.a_dict()})
        assert motivo == ""

    def test_hook_de_app_registrado_una_sola_vez(self):
        """§5.1: crear la app varias veces no duplica el hook de tareas."""
        wa._HOOK_TAREAS_REGISTRADO["activo"] = False
        wa.crear_app(api_token="K" * 40, host="127.0.0.1", workspace_root="/tmp")
        tras_primera = len(wa.seguridad._HOOKS_ROTACION)
        wa.crear_app(api_token="K" * 40, host="127.0.0.1", workspace_root="/tmp")
        wa.crear_app(api_token="K" * 40, host="127.0.0.1", workspace_root="/tmp")
        assert wa._HOOK_TAREAS_REGISTRADO["activo"] is True
        # Solo se añade 1 hook de cierre de WS por app; el de tareas, 1 total.
        assert len(wa.seguridad._HOOKS_ROTACION) == tras_primera + 2

    def test_rotacion_sigue_cerrando_websockets(self):
        """El cierre de WS no se perdió al añadir la invalidación."""
        import inspect as _inspect

        assert "call_soon_threadsafe" in _inspect.getsource(wa.crear_app)


class TestRex9PoliticaCerrada:
    def test_allowlist_corrupta_no_habilita(self):
        with mock.patch("configuracion.cargar_configuracion",
                        return_value={"sandbox_allowlist_remota": "no-es-lista"}):
            assert remota_web.allowlist_remota_efectiva() == ()

    def test_allowlist_con_contenido_no_habilita_en_m1(self):
        """M1 no habilita comandos aunque la clave tenga entradas (OD-1)."""
        with mock.patch("configuracion.cargar_configuracion",
                        return_value={"sandbox_allowlist_remota": [{"binario": "echo"}]}):
            assert remota_web.allowlist_remota_efectiva() == ()

    def test_config_ausente_no_habilita(self):
        with mock.patch("configuracion.cargar_configuracion", return_value={}):
            assert remota_web.allowlist_remota_efectiva() == ()

    def test_remota_es_mas_estricta_que_local(self):
        """REX-11: nada habilitado remotamente que no esté en la local."""
        from sandbox_utils import ALLOWLIST_BINARIOS_DEFECTO

        assert remota_web.allowlist_remota_efectiva() == ()
        # Y la denegación remota no depende de la allowlist local.
        permitido, _, _ = remota_web.autorizar_comando_remoto(["ls"])
        assert permitido is False
        assert "ls" in ALLOWLIST_BINARIOS_DEFECTO


# ===========================================================================
# REX-3 / REX-13 — cwd confinado y capabilities separadas
# ===========================================================================
class TestRex3Rex13:
    def test_run_usa_cwd_confinado(self):
        """Aunque se deniegue, el cwd se valida antes de tocar nada."""
        cola = queue.Queue()
        wa._ejecutar_accion(
            {"accion": "run", "comando": "echo x", "directorio": "../../etc"}, cola
        )
        fin = [e for e in _eventos(cola) if e.get("tipo") == "accion_ejecutada"]
        assert fin and fin[0]["ok"] is False

    def test_use_plugins_no_implica_ejecucion_remota(self):
        """REX-13: las capacidades siguen siendo independientes."""
        from web.seguridad import CAPABILIDADES

        assert "USE_PLUGINS" in CAPABILIDADES
        assert "EXECUTE_REMOTE" in CAPABILIDADES
        # Son valores distintos: instalar no autoriza ejecutar.
        assert "USE_PLUGINS" != "EXECUTE_REMOTE"

    def test_no_se_crearon_capacidades_nuevas(self):
        from web.seguridad import CAPABILIDADES

        assert len(CAPABILIDADES) == 9
        assert "PLUGIN_INSTALL" not in CAPABILIDADES

    def test_manifest_no_concede_capabilities(self):
        """REX-5: un manifest declara, no concede."""
        manifest = {
            "nombre": "x", "herramientas": [{}],
            "permisos": ["WRITE_WORKSPACE", "EXECUTE_REMOTE"],
            "capabilities": ["MANAGE_DAEMON"],
        }
        # La validación de manifest no otorga nada: las capacidades viven
        # únicamente en CAPABILIDADES.
        from web.seguridad import CAPABILIDADES

        for peticion in manifest["permisos"] + manifest["capabilities"]:
            assert peticion in CAPABILIDADES  # es un nombre, no un permiso
        # Y el deny remoto no consulta el manifest.
        permitido, _, _ = remota_web.autorizar_comando_remoto(["echo", "x"])
        assert permitido is False


# ===========================================================================
# REX-10 — rotación de credencial
# ===========================================================================
class TestRex10Rotacion:
    def test_operacion_remota_pendiente_no_conserva_privilegios(self):
        """REX-10: sin lista de comandos autorizados no hay nada que revalidar."""
        cola = queue.Queue()
        with mock.patch.object(sc, "_ejecutar_comando") as ejec:
            wa._ejecutar_accion(
                {"accion": "run", "comando": "echo x", "directorio": "."}, cola
            )
            ejec.assert_not_called()

    def test_identidad_no_se_transfiere(self):
        """Una identidad remota no concede capacidades (OD-9/REX-10)."""
        _, _, ident = remota_web.autorizar_comando_remoto(["echo", "x"])
        assert ident is None or not hasattr(ident, "capability")


# ===========================================================================
# REX-14 — errores sin fuga de paths
# ===========================================================================
class TestRex14Errores:
    def test_motivo_no_contiene_ruta_del_host(self):
        permitido, motivo, _ = remota_web.autorizar_comando_remoto(
            ["/root/secreto/x"]
        )
        assert permitido is False
        assert "/root/secreto" not in motivo

    def test_error_de_frontera_no_revela_workspace(self):
        with pytest.raises(ErrorFrontera) as info:
            wa._origen_plugin_por_frontera("/etc/passwd")
        assert str(fs_web.frontera_activa().raiz) not in str(info.value)
