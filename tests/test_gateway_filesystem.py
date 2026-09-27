#!/usr/bin/env python3
"""Suite de tests de seguridad de filesystem para B9.61-B.

Organizada como una auditoría de bypass, no como cobertura positiva:

1. Workspace root (loopback vs LAN, raíz explícita, raíces prohibidas).
2. Contención semántica (incluye ``/workspace`` vs ``/workspace-other``).
3. Traversal (literal, anidado, codificado, ``~``).
4. Rutas absolutas.
5. Symlinks (archivo, directorio, anidado, padre, roto, permitidos dentro).
6. Lecturas seguras.
7. Escrituras seguras (C3 preservado, sin escape por symlink).
8. ``cwd`` de subprocesos.
9. Cobertura de los entry points del gateway.
10. Arranque fail-closed.
11. Invariante estructural de frontera única.
"""

import inspect
import json
import os
import queue
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import snapcontext as sc
import web.app as wa
from web import filesystem as fs_web
from web.filesystem import ErrorFrontera, Frontera, WorkspaceRootInvalido

CLAVE = "clave-b961b-de-prueba-32-chars"
#: Credencial en minúsculas + dígitos: cumple el perfil de B9.61-A sin ambigüedad.
CLAVE_VALIDA = "claveb961bdepruebaclavevalida123"


@pytest.fixture(autouse=True)
def _frontera_limpia():
    """Cada test parte de la frontera loopback por defecto (aislamiento)."""
    fs_web.reiniciar_frontera()
    yield
    fs_web.reiniciar_frontera()


def _ws_en(tmp):
    """Crea ``<tmp>/ws`` y devuelve ``(ws, tmp)`` para montar la frontera."""
    ws = Path(tmp) / "ws"
    ws.mkdir()
    return ws, Path(tmp)


def _eventos(cola):
    """Vacía una cola y devuelve la lista de eventos."""
    salida = []
    while not cola.empty():
        salida.append(cola.get_nowait())
    return salida


# ===========================================================================
# 1. Workspace root
# ===========================================================================
class TestWorkspaceRoot:
    def test_raiz_explicita_valida_en_lan(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = fs_web.configurar_frontera(exposicion="lan", workspace_root=str(ws))
            assert f.raiz == ws.resolve()

    def test_sin_raiz_en_lan_aborta(self):
        with pytest.raises(WorkspaceRootInvalido):
            fs_web.configurar_frontera(exposicion="lan", workspace_root=None)

    def test_sin_raiz_en_internet_aborta(self):
        with pytest.raises(WorkspaceRootInvalido):
            fs_web.configurar_frontera(exposicion="internet", workspace_root="")

    def test_loopback_sin_raiz_usa_raiz_del_servidor(self):
        """B9.61-B-CORRECTION F-01: la raiz la fija el servidor, no el request."""
        f = fs_web.configurar_frontera(exposicion="loopback", workspace_root=None)
        assert f.raiz == fs_web.raiz_servidor_por_defecto()

    def test_raiz_inexistente_aborta(self):
        with tempfile.TemporaryDirectory() as tmp:
            with pytest.raises(WorkspaceRootInvalido):
                fs_web.configurar_frontera(
                    exposicion="lan", workspace_root=str(Path(tmp) / "no-existe")
                )

    def test_raiz_que_no_es_directorio_aborta(self):
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "f.txt"
            archivo.write_text("x", encoding="utf-8")
            with pytest.raises(WorkspaceRootInvalido):
                fs_web.configurar_frontera(exposicion="lan", workspace_root=str(archivo))

    @pytest.mark.parametrize("prohibida", ["/", "/home", "/root", "/Users"])
    def test_raices_prohibidas_abortan(self, prohibida):
        if not Path(prohibida).exists():
            pytest.skip(f"{prohibida} no existe en esta plataforma")
        with pytest.raises(WorkspaceRootInvalido):
            fs_web.configurar_frontera(exposicion="lan", workspace_root=prohibida)

    def test_home_del_usuario_aborta(self):
        if not Path.home().is_dir():
            pytest.skip("home no disponible")
        with pytest.raises(WorkspaceRootInvalido):
            fs_web.configurar_frontera(exposicion="lan", workspace_root=str(Path.home()))

    def test_raiz_symlink_se_resuelve_a_realpath(self):
        """Raíz dada como symlink: la raíz efectiva es su realpath."""
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real"
            real.mkdir()
            link = Path(tmp) / "link"
            link.symlink_to(real, target_is_directory=True)
            f = fs_web.configurar_frontera(exposicion="lan", workspace_root=str(link))
            assert f.raiz == real.resolve()

    def test_raiz_symlink_roto_aborta(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "roto"
            link.symlink_to(Path(tmp) / "destino-inexistente")
            with pytest.raises(WorkspaceRootInvalido):
                fs_web.configurar_frontera(exposicion="lan", workspace_root=str(link))

    def test_raiz_symlink_que_apunta_a_prohibida_aborta(self):
        """El realpath de la raíz nunca puede caer en una raíz prohibida."""
        with tempfile.TemporaryDirectory() as tmp:
            if not Path("/home").exists():
                pytest.skip("/home no existe")
            link = Path(tmp) / "h"
            link.symlink_to("/home", target_is_directory=True)
            with pytest.raises(WorkspaceRootInvalido):
                fs_web.configurar_frontera(exposicion="lan", workspace_root=str(link))

    def test_raiz_invalida_no_degrada_a_cwd(self):
        """Fail-closed: una raíz inválida NO se convierte en otro directorio."""
        antes = fs_web.frontera_activa()
        with pytest.raises(WorkspaceRootInvalido):
            fs_web.configurar_frontera(exposicion="lan", workspace_root="/")
        assert fs_web.frontera_activa() is antes


# ===========================================================================
# 2. Contención semántica
# ===========================================================================
class TestContencionSemantica:
    def test_prefix_check_no_basta_workspace_other(self):
        """/workspace-other NO es hijo de /workspace (B9.61-B §4)."""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "workspace"
            otro = Path(tmp) / "workspace-other"
            ws.mkdir()
            otro.mkdir()
            (otro / "secreto.txt").write_text("S", encoding="utf-8")
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            # Un startswith ingenuo confundiría los dos prefijos...
            assert str(otro.resolve()).startswith(str(ws.resolve()) + "-")
            # ...pero la contención semántica no lo hace.
            with pytest.raises(ErrorFrontera) as info:
                f.leer("secreto.txt", str(otro))
            assert info.value.codigo == "outside_workspace"

    def _contener_no_usa_prefijos_de_texto(self, raiz, candidato):
        return fs_web._contener(Path(raiz), Path(candidato))

    def test_contener_no_usa_prefijos_de_texto(self):
        """/workspace-other no queda contenido en /workspace."""
        assert not self._contener_no_usa_prefijos_de_texto("/workspace", "/workspace-other")
        assert not self._contener_no_usa_prefijos_de_texto("/ws", "/ws2/x")
        assert self._contener_no_usa_prefijos_de_texto("/ws", "/ws/x")
        assert self._contener_no_usa_prefijos_de_texto("/ws", "/ws")


# ===========================================================================
# 3. Traversal
# ===========================================================================
class TestTraversal:
    @pytest.mark.parametrize(
        "ruta",
        [
            "../fuera.txt",
            "../../fuera.txt",
            "sub/../../fuera.txt",
            "a/b/../../../fuera.txt",
            "..",
            "./../fuera.txt",
        ],
    )
    def test_traversal_denegado(self, ruta):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (Path(tmp) / "fuera.txt").write_text("secreto", encoding="utf-8")
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera):
                f.resolver_ruta_en(ruta, str(ws))

    @pytest.mark.parametrize(
        "ruta",
        [
            "%2e%2e/fuera.txt",
            "..%2ffuera.txt",
            "%2E%2E%2Ffuera.txt",
            "%252e%252e%252ffuera.txt",
            "..%5c..%5cfuera.txt",
            "file%3a%2f%2f%2fetc%2fpasswd",
        ],
    )
    def test_traversal_codificado_denegado(self, ruta):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera):
                f.resolver_ruta_en(ruta, str(ws))

    def test_traversal_en_directorio_denegado(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera) as info:
                f.base_operacion(str(ws / ".." / "fuera"))
            assert info.value.codigo == "outside_workspace"

    def test_byte_nulo_denegado(self):
        with pytest.raises(ErrorFrontera):
            Frontera().resolver_ruta_en("a\x00.txt", ".")

    def test_no_string_denegado(self):
        f = Frontera()
        with pytest.raises(ErrorFrontera):
            f.resolver_ruta_en(123, ".")
        with pytest.raises(ErrorFrontera):
            f.resolver_ruta_en(None, ".")

    def test_otra_expresion_de_la_misma_ruta_se_normaliza(self):
        """``a/./b`` y ``a//b`` se normalizan; el escape sigue bloqueado."""
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (ws / "sub").mkdir()
            (ws / "sub" / "b.txt").write_text("ok", encoding="utf-8")
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            assert f.leer("./sub//b.txt", str(ws)) == "ok"
            with pytest.raises(ErrorFrontera):
                f.leer("sub/../../fuera.txt", str(ws))


# ===========================================================================
# 4. Rutas absolutas
# ===========================================================================
class TestRutasAbsolutas:
    @pytest.mark.parametrize(
        "ruta", ["/etc/passwd", "/home/usuario/.ssh/id_rsa", "/tmp/x", "/root/.bashrc"]
    )
    def test_absoluta_denegada(self, ruta):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera):
                f.resolver_ruta_en(ruta, str(ws))

    def test_absoluta_dentro_del_workspace_tambien_se_deniega(self):
        """La operación exige rutas relativas: absoluta nunca, ni dentro."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "a.txt").write_text("x", encoding="utf-8")
            with pytest.raises(ErrorFrontera) as info:
                Frontera().resolver_ruta_en(str(Path(tmp) / "a.txt"), tmp)
            assert info.value.codigo == "invalid_path"

    def test_tilde_denegada(self):
        with pytest.raises(ErrorFrontera):
            Frontera().resolver_ruta_en("~/.ssh/id_rsa", ".")


# ===========================================================================
# 5. Symlinks
# ===========================================================================
class TestSymlinks:
    def test_symlink_de_archivo_que_escapa(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            secreto = Path(tmp) / "secreto.txt"
            secreto.write_text("CLAVE", encoding="utf-8")
            (ws / "link.txt").symlink_to(secreto)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera) as info:
                f.leer("link.txt", str(ws))
            assert info.value.codigo == "outside_workspace"

    def test_symlink_de_directorio_que_escapa(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fuera = Path(tmp) / "fuera"
            fuera.mkdir()
            (fuera / "s.txt").write_text("CLAVE", encoding="utf-8")
            (ws / "link").symlink_to(fuera, target_is_directory=True)
            with pytest.raises(ErrorFrontera):
                Frontera(exposicion="lan", raiz=ws.resolve()).leer("link/s.txt", str(ws))

    def test_symlink_anidado_que_escapa(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (ws / "a" / "b").mkdir(parents=True)
            fuera = Path(tmp) / "fuera"
            fuera.mkdir()
            (fuera / "s.txt").write_text("CLAVE", encoding="utf-8")
            (ws / "a" / "b" / "link").symlink_to(fuera, target_is_directory=True)
            with pytest.raises(ErrorFrontera):
                Frontera(exposicion="lan", raiz=ws.resolve()).leer("a/b/link/s.txt", str(ws))

    def test_symlink_de_padre_que_escapa(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fuera = Path(tmp) / "fuera"
            fuera.mkdir()
            (fuera / "s.txt").write_text("CLAVE", encoding="utf-8")
            (ws / "link_padre").symlink_to(fuera, target_is_directory=True)
            with pytest.raises(ErrorFrontera):
                Frontera(exposicion="lan", raiz=ws.resolve()).leer("link_padre/s.txt", str(ws))

    def test_symlink_roto_que_apunta_fuera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (ws / "roto").symlink_to(Path(tmp) / "no-existe" / "x.txt")
            with pytest.raises(ErrorFrontera):
                Frontera(exposicion="lan", raiz=ws.resolve()).leer("roto", str(ws))

    def test_symlink_dentro_del_workspace_permitido(self):
        """B9.61-B §5: symlink interno permitido, escape denegado."""
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (ws / "real.txt").write_text("dato", encoding="utf-8")
            (ws / "alias.txt").symlink_to(ws / "real.txt")
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            assert f.leer("alias.txt", str(ws)) == "dato"

    def test_directorio_symlink_que_escapa(self):
        """El propio directorio puede ser un symlink que sale de la raíz."""
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fuera = Path(tmp) / "fuera"
            fuera.mkdir()
            (ws / "fuga").symlink_to(fuera, target_is_directory=True)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera) as info:
                f.base_operacion(str(ws / "fuga"))
            assert info.value.codigo == "outside_workspace"


# ===========================================================================
# 6. Lecturas seguras
# ===========================================================================
class TestSafeReads:
    def test_lectura_valida(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (ws / "a.txt").write_text("hola", encoding="utf-8")
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            assert f.leer("a.txt", str(ws)) == "hola"

    def test_lectura_fuera_denegada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (Path(tmp) / "secreto.txt").write_text("CLAVE", encoding="utf-8")
            with pytest.raises(ErrorFrontera):
                Frontera(exposicion="lan", raiz=ws.resolve()).leer("../secreto.txt", str(ws))

    def test_lectura_etc_passwd_via_entrypoint(self):
        r = wa._leer_archivo_web({"ruta": "/etc/passwd", "directorio": "."})
        assert r["contenido"] is None and "error" in r

    def test_lectura_home_ssh_via_entrypoint(self):
        r = wa._leer_archivo_web({"ruta": "~/.ssh/id_rsa", "directorio": "."})
        assert r["contenido"] is None and "error" in r

    def test_lectura_no_existente_codigo_not_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera) as info:
                f.leer("no-existe.txt", str(ws))
            assert info.value.codigo == "not_found"

    def test_lectura_de_directorio_denegada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (ws / "d").mkdir()
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera) as info:
                f.leer("d", str(ws))
            assert info.value.codigo == "invalid_path"

    def test_error_no_revela_ruta_real(self):
        """Los errores no filtran la ruta absoluta real ni el contenido."""
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            secreto = Path(tmp) / "secreto.txt"
            secreto.write_text("CLAVE-SECRETA", encoding="utf-8")
            (ws / "l").symlink_to(secreto)
            with pytest.raises(ErrorFrontera) as info:
                Frontera(exposicion="lan", raiz=ws.resolve()).leer("l", str(ws))
            texto = str(info.value)
            assert str(secreto) not in texto
            assert str(tmp) not in texto
            assert "CLAVE-SECRETA" not in texto

    def test_lectura_usa_o_nofollow(self):
        """La lectura se abre con O_NOFOLLOW (B9.58-R F9)."""
        import web.filesystem as mod

        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (ws / "a.txt").write_text("ok", encoding="utf-8")
            real_abrir = mod.os.open
            visto = {}

            def _spy(ruta, flags, *args, **kwargs):
                visto["flags"] = flags
                return real_abrir(ruta, flags, *args, **kwargs)

            with mock.patch.object(mod.os, "open", _spy):
                Frontera(exposicion="lan", raiz=ws.resolve()).leer("a.txt", str(ws))
            assert visto["flags"] & os.O_NOFOLLOW


# ===========================================================================
# 7. Escrituras seguras (C3 preservado)
# ===========================================================================
class TestSafeWrites:
    def test_escritura_valida(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            f.escribir("nuevo.txt", "contenido", str(ws))
            assert (ws / "nuevo.txt").read_text(encoding="utf-8") == "contenido"

    def test_escritura_fuera_denegada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera):
                f.escribir("../fuera.txt", "x", str(ws))
            assert not (Path(tmp) / "fuera.txt").exists()

    def test_escritura_por_symlink_que_escapa_denegada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            objetivo = Path(tmp) / "objetivo.txt"
            objetivo.write_text("original", encoding="utf-8")
            (ws / "l.txt").symlink_to(objetivo)
            with pytest.raises(ErrorFrontera):
                Frontera(exposicion="lan", raiz=ws.resolve()).escribir("l.txt", "PWN", str(ws))
            assert objetivo.read_text(encoding="utf-8") == "original"

    def test_escritura_por_symlink_de_directorio_denegada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fuera = Path(tmp) / "fuera"
            fuera.mkdir()
            (ws / "d").symlink_to(fuera, target_is_directory=True)
            with pytest.raises(ErrorFrontera):
                Frontera(exposicion="lan", raiz=ws.resolve()).escribir("d/x.txt", "PWN", str(ws))
            assert not (fuera / "x.txt").exists()

    def test_escritura_conserva_c3(self):
        """La frontera sigue delegando en utils.escribir_archivo_seguro (C3)."""
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with mock.patch("utils.escribir_archivo_seguro") as escritor:
                f.escribir("a.txt", "x", str(ws))
            escritor.assert_called_once()
            assert escritor.call_args.args[0] == "a.txt"
            assert escritor.call_args.args[2] == ws.resolve()

    def test_c3_rechaza_symlink_al_crear(self):
        """C3 (O_NOFOLLOW) sigue bloqueando la escritura sobre un symlink."""
        from exceptions import RutaInseguraError
        from utils import escribir_archivo_seguro

        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            objetivo = Path(tmp) / "objetivo.txt"
            objetivo.write_text("original", encoding="utf-8")
            (ws / "l.txt").symlink_to(objetivo)
            with pytest.raises(RutaInseguraError):
                escribir_archivo_seguro("l.txt", "PWN", ws)
            assert objetivo.read_text(encoding="utf-8") == "original"

    def test_guardar_entrypoint_fuera_denegado(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            res = wa._guardar_archivo_web(
                {"ruta": "../escape.txt", "directorio": str(ws), "contenido": "x"}
            )
            assert res["ok"] is False
            assert not (Path(tmp) / "escape.txt").exists()

    def test_guardar_entrypoint_absoluta_denegada(self):
        res = wa._guardar_archivo_web({"ruta": "/etc/passwd", "contenido": "x"})
        assert res["ok"] is False


# ===========================================================================
# 8. cwd de subprocesos
# ===========================================================================
class TestCwdProtegido:
    def test_cwd_valido(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            assert f.resolver_cwd(str(ws)) == ws.resolve()

    def test_cwd_fuera_denegado(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera) as info:
                f.resolver_cwd(str(fuera))
            assert info.value.codigo == "outside_workspace"

    def test_cwd_traversal_denegado(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            f = Frontera(exposicion="lan", raiz=ws.resolve())
            with pytest.raises(ErrorFrontera):
                f.resolver_cwd(str(ws / ".." / ".."))

    def test_run_no_ejecuta_con_cwd_fuera(self):
        """_ejecutar_accion deniega antes de llamar a _ejecutar_comando."""
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            cola = queue.Queue()
            with mock.patch.object(sc, "_ejecutar_comando") as ejec:
                wa._ejecutar_accion(
                    {"accion": "run", "comando": "echo HOLA", "directorio": str(fuera)}, cola
                )
                ejec.assert_not_called()
            fin = [e for e in _eventos(cola) if e.get("tipo") == "accion_ejecutada"]
            assert fin and fin[0]["ok"] is False

    def test_run_ya_no_ejecuta_por_politica_remota(self):
        """B9.61-C (OD-1): ``run`` es ejecución remota y M1 = DENY ALL.

        Antes (B9.61-B) este test comprobaba que ``run`` recibía el ``cwd``
        validado. Ahora la política remota deniega **antes** de ejecutar; el
        ``cwd`` sigue validándose por la frontera (test siguiente).
        """
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            cola = queue.Queue()
            with mock.patch.object(sc, "_ejecutar_comando") as ejec:
                wa._ejecutar_accion(
                    {"accion": "run", "comando": "echo HOLA", "directorio": str(ws)}, cola
                )
                ejec.assert_not_called()
            fin = [e for e in _eventos(cola) if e.get("tipo") == "accion_ejecutada"]
            assert fin and fin[0]["ok"] is False

    def test_run_con_cwd_fuera_se_deniega_por_frontera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            cola = queue.Queue()
            with mock.patch.object(sc, "_ejecutar_comando") as ejec:
                wa._ejecutar_accion(
                    {"accion": "run", "comando": "echo x", "directorio": str(fuera)}, cola
                )
                ejec.assert_not_called()


# ===========================================================================
# 9. Cobertura de entry points
# ===========================================================================
class TestEntryPoints:
    def test_construir_args_rechaza_directorio_fuera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with pytest.raises(ErrorFrontera):
                wa._construir_args({"consulta": "x", "directorio": str(fuera)})

    def test_construir_args_pasa_ruta_confinada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            args = wa._construir_args({"consulta": "x", "directorio": str(ws)})
            assert args.directorio == str(ws.resolve())

    def test_construir_args_accion_rechaza_directorio_fuera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with pytest.raises(ErrorFrontera):
                wa._construir_args_accion("fix", "x.py", str(fuera))

    def test_dependencias_rechaza_directorio_fuera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with mock.patch.object(sc, "_grafo_dependencias") as grafo:
                res = wa._dependencias_web({"directorio": str(fuera)})
                grafo.assert_not_called()
            assert res["nodos"] == [] and "error" in res

    def test_dependencias_recibe_ruta_confinada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with mock.patch.object(
                sc, "_grafo_dependencias", return_value={"nodos": [1], "enlaces": []}
            ) as grafo:
                res = wa._dependencias_web({"directorio": str(ws)})
            grafo.assert_called_once_with(str(ws.resolve()))
            assert res["nodos"] == [1]

    def test_semantica_rechaza_directorio_fuera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with mock.patch.object(sc, "_embeddings_disponibles", return_value=True):
                with mock.patch.object(sc, "_buscar_semanticamente") as buscar:
                    assert wa._semantica_web({"consulta": "q", "directorio": str(fuera)}) == []
                    buscar.assert_not_called()

    def test_semantica_recibe_ruta_confinada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with mock.patch.object(sc, "_embeddings_disponibles", return_value=True):
                with mock.patch.object(
                    sc, "_buscar_semanticamente", return_value=[{"a": 1}]
                ) as buscar:
                    res = wa._semantica_web({"consulta": "q", "directorio": str(ws)})
            assert res == [{"a": 1}]
            assert buscar.call_args.args[1] == str(ws.resolve())

    def test_explorar_rechaza_directorio_fuera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with mock.patch.object(sc, "_buscar_en_codigo") as buscar:
                assert wa._explorar_web({"tema": "t", "directorio": str(fuera)}) == []
                buscar.assert_not_called()

    def test_explorar_recibe_ruta_confinada(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with mock.patch.object(sc, "_buscar_en_codigo", return_value=["l"]) as buscar:
                assert wa._explorar_web({"tema": "t", "directorio": str(ws)}) == ["l"]
            assert buscar.call_args.args[1] == str(ws.resolve())

    def test_ejecutar_accion_deniega_todas_las_acciones_sin_tocar_fs(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            for accion in ("run", "search", "explorar", "fix", "review", "plan"):
                cola = queue.Queue()
                with mock.patch.object(sc, "flujo_principal") as flujo:
                    with mock.patch.object(sc, "_grafo_dependencias") as grafo:
                        wa._ejecutar_accion(
                            {"accion": accion, "consulta": "q", "directorio": str(fuera)}, cola
                        )
                    flujo.assert_not_called()
                    grafo.assert_not_called()
                fin = [e for e in _eventos(cola) if e.get("tipo") == "accion_ejecutada"]
                assert fin and fin[0]["ok"] is False, accion

    def test_lanzar_tarea_api_rechaza_directorio_fuera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, fuera = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            with mock.patch.object(wa.threading, "Thread") as hilo:
                with pytest.raises(ErrorFrontera):
                    wa._lanzar_tarea_api("query", {"consulta": "q", "directorio": str(fuera)})
                hilo.assert_not_called()

    def test_ejecutar_tarea_api_usa_base_congelado(self):
        """El hilo en segundo plano usa el base ya validado, no el crudo."""
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            with mock.patch.object(sc, "crear_parser") as parser:
                parser.return_value.parse_args.return_value = mock.Mock(
                    auto=False, no_confirmar=True, depurar=False
                )
                with mock.patch.object(sc, "flujo_principal", return_value=0):
                    wa._TAREAS_API["t1"] = {"estado": "pendiente"}
                    wa._ejecutar_tarea_api("t1", "query", {"consulta": "q"}, ws.resolve())
            argv = parser.return_value.parse_args.call_args.args[0]
            assert "--directorio" in argv
            assert str(ws.resolve()) in argv

    def test_leer_y_guardar_usan_la_frontera_activa(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            (ws / "a.txt").write_text("hola", encoding="utf-8")
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            leer = wa._leer_archivo_web({"ruta": "a.txt", "directorio": str(ws)})
            assert leer["contenido"] == "hola"
            guardar = wa._guardar_archivo_web(
                {"ruta": "b.txt", "directorio": str(ws), "contenido": "x"}
            )
            assert guardar["ok"] is True
            assert (ws / "b.txt").exists()


# ===========================================================================
# 10. Arranque fail-closed
# ===========================================================================
class TestArranqueFailClosed:
    def test_crear_app_no_loopback_sin_raiz_aborta(self):
        with (
            mock.patch("web.seguridad.preparar_credencial_inicio", return_value=CLAVE_VALIDA),
            mock.patch("web.seguridad.cargar_credencial", return_value=CLAVE_VALIDA),
        ):
            with pytest.raises(WorkspaceRootInvalido):
                wa.crear_app(api_token=CLAVE_VALIDA, host="0.0.0.0", workspace_root=None)

    def test_crear_app_no_loopback_con_raiz_invalida_aborta(self):
        with (
            mock.patch("web.seguridad.preparar_credencial_inicio", return_value=CLAVE_VALIDA),
            mock.patch("web.seguridad.cargar_credencial", return_value=CLAVE_VALIDA),
        ):
            with pytest.raises(WorkspaceRootInvalido):
                wa.crear_app(api_token=CLAVE_VALIDA, host="0.0.0.0", workspace_root="/")

    def test_crear_app_loopback_sin_raiz_arranca(self):
        with (
            mock.patch("web.seguridad.preparar_credencial_inicio", return_value=CLAVE_VALIDA),
            mock.patch("web.seguridad.cargar_credencial", return_value=CLAVE_VALIDA),
        ):
            app = wa.crear_app(api_token=CLAVE_VALIDA, host="127.0.0.1")
        assert app is not None
        # F-01: la raiz del servidor, congelada al arrancar.
        assert fs_web.frontera_activa().raiz == fs_web.raiz_servidor_por_defecto()

    def test_crear_app_con_raiz_congela_la_frontera(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            with (
                mock.patch("web.seguridad.preparar_credencial_inicio", return_value=CLAVE_VALIDA),
                mock.patch("web.seguridad.cargar_credencial", return_value=CLAVE_VALIDA),
            ):
                wa.crear_app(api_token=CLAVE_VALIDA, host="127.0.0.1", workspace_root=str(ws))
            assert fs_web.frontera_activa().raiz == ws.resolve()

    def test_raiz_congelada_no_se_re_resuelve(self):
        """B9.60 §9.6: si el path de la raíz cambia, la operación se deniega."""
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            otro = Path(tmp) / "otro"
            otro.mkdir()
            with (
                mock.patch("web.seguridad.preparar_credencial_inicio", return_value=CLAVE_VALIDA),
                mock.patch("web.seguridad.cargar_credencial", return_value=CLAVE_VALIDA),
            ):
                wa.crear_app(api_token=CLAVE_VALIDA, host="127.0.0.1", workspace_root=str(ws))
            os.rename(ws, otro)
            # La raíz sigue congelada en el path viejo: el directorio nuevo
            # queda fuera y la lectura se deniega (no se re-resuelve).
            assert fs_web.frontera_activa().raiz == ws.resolve()
            with pytest.raises(ErrorFrontera):
                fs_web.frontera_activa().base_operacion(str(otro))
            res = wa._leer_archivo_web({"ruta": "a.txt", "directorio": str(otro)})
            assert res["contenido"] is None and "error" in res


# ===========================================================================
# 11. Invariante estructural: una sola frontera
# ===========================================================================
class TestInvarianteFronteraUnica:
    def test_web_app_no_tiene_operaciones_de_fs_directas(self):
        """web/app.py no abre archivos ni recorre directorios por su cuenta.

        La frontera (y C3) son los únicos que tocan el filesystem; los handlers
        solo propagan el Path ya validado.
        """
        src = inspect.getsource(wa)
        for token in (
            "open(",
            "read_text(",
            "write_text(",
            "rglob(",
            "glob(",
            "iterdir(",
            "mkdir(",
            "unlink(",
            "shutil.",
        ):
            assert token not in src, f"web/app.py usa {token} directamente"

    def test_la_frontera_se_reutiliza_entre_todas_las_operaciones(self):
        """Reads, writes, directorios y cwd comparten la misma frontera."""
        metodos = {"leer", "escribir", "base_operacion", "resolver_ruta", "resolver_cwd"}
        assert metodos <= set(dir(Frontera))

    def test_no_hay_una_segunda_implementacion_de_contencion(self):
        """No se duplica la lógica de containment fuera de la frontera."""
        # Se ignoran docstrings/comentarios: lo que importa es el código real.
        codigo = inspect.getsource(wa)
        codigo = "\n".join(linea.split("#", 1)[0] for linea in codigo.splitlines())
        assert ".relative_to(" not in codigo
        assert "_validar_ruta_segura" not in codigo

    def test_toda_lectura_del_gateway_pasa_por_la_frontera(self):
        """_leer_archivo_web no llama a sc._leer_archivo con una ruta sin validar."""
        src = inspect.getsource(wa._leer_archivo_web)
        assert "frontera_activa().leer(" in src
        assert "_leer_archivo(" not in src


# ===========================================================================
# 12. Correcciones B9.61-B-R: F-01 (raiz del servidor), F-02, F-03
# ===========================================================================
class TestF01RaizDelServidor:
    """``directorio`` es input atacante y NUNCA define la raíz (B9.58 §7).

    En loopback sin ``--workspace-root`` la raíz la fija el SERVIDOR
    (cwd/repo), no el request. Estos tests fallaban con la implementación
    previa, que tomaba ``directorio`` como raíz efectiva.
    """

    def test_leer_etc_passwd_en_loopback_se_deniega(self):
        """P0 original: {"ruta":"passwd","directorio":"/etc"} leia /etc/passwd."""
        fs_web.reiniciar_frontera()  # loopback, raiz del servidor
        raiz = fs_web.frontera_activa().raiz_efectiva()
        assert raiz == fs_web.raiz_servidor_por_defecto()
        res = wa._leer_archivo_web({"ruta": "passwd", "directorio": "/etc"})
        assert res["contenido"] is None
        assert "error" in res

    def test_leer_etc_passwd_con_raiz_congelada_se_deniega(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
            res = wa._leer_archivo_web({"ruta": "passwd", "directorio": "/etc"})
            assert res["contenido"] is None and "error" in res

    def test_escribir_en_tmp_se_deniega(self):
        """P0 original: escribia /tmp/PWNED."""
        fs_web.reiniciar_frontera()
        objetivo = Path("/tmp") / "B961B_PWNED"
        res = wa._guardar_archivo_web(
            {"ruta": "B961B_PWNED", "directorio": "/tmp", "contenido": "x"}
        )
        assert res["ok"] is False
        assert not objetivo.exists()

    def test_dependencias_etc_se_deniega(self):
        fs_web.reiniciar_frontera()
        with mock.patch.object(sc, "_grafo_dependencias") as grafo:
            res = wa._dependencias_web({"directorio": "/etc"})
            grafo.assert_not_called()
        assert res["nodos"] == [] and "error" in res

    def test_explorar_etc_se_deniega(self):
        fs_web.reiniciar_frontera()
        with mock.patch.object(sc, "_buscar_en_codigo") as buscar:
            assert wa._explorar_web({"tema": "t", "directorio": "/etc"}) == []
            buscar.assert_not_called()

    def test_directorio_padre_se_deniega(self):
        fs_web.reiniciar_frontera()
        with pytest.raises(ErrorFrontera) as info:
            fs_web.frontera_activa().base_operacion("..")
        assert info.value.codigo == "outside_workspace"

    def test_subdirectorial_relativo_valido_se_permite(self):
        fs_web.reiniciar_frontera()
        raiz = fs_web.frontera_activa().raiz_efectiva()
        assert fs_web.frontera_activa().base_operacion("web") == raiz / "web"

    def test_directorio_igual_a_raiz_se_permite(self):
        """La ruta "." se resuelve a la raiz del servidor."""
        fs_web.reiniciar_frontera()
        raiz = fs_web.frontera_activa().raiz_efectiva()
        assert fs_web.frontera_activa().base_operacion(".") == raiz

    def test_directorio_no_cambia_la_raiz(self):
        """El request no puede mover la frontera."""
        fs_web.reiniciar_frontera()
        antes = fs_web.frontera_activa().raiz
        for d in ("/etc", "/tmp", "..", "/root", "/"):
            with pytest.raises(ErrorFrontera):
                fs_web.frontera_activa().base_operacion(d)
            assert fs_web.frontera_activa().raiz == antes
        assert antes == fs_web.raiz_servidor_por_defecto()

    def test_raiz_sin_configurar_deniega(self):
        """Fail-closed: sin raiz no hay operacion (nunca fallback a cwd)."""
        f = Frontera(exposicion="loopback", raiz=None)
        with pytest.raises(ErrorFrontera):
            f.base_operacion(".")

    def test_lectura_normal_del_repo_sigue_funcionando(self):
        """Backward compatibility: el uso local legitimo no se rompe."""
        fs_web.reiniciar_frontera()
        if not (fs_web.frontera_activa().raiz_efectiva() / "web" / "app.py").is_file():
            pytest.skip("no estamos dentro del repo")
        res = wa._leer_archivo_web({"ruta": "web/app.py", "directorio": "."})
        assert res.get("contenido")
        assert "error" not in res


class TestF04RaizRelativa:
    def test_raiz_relativa_en_lan_se_rechaza(self):
        with pytest.raises(WorkspaceRootInvalido):
            fs_web.resolver_raiz_workspace(".", "lan")

    def test_raiz_relativa_por_defecto_se_rechaza(self):
        with pytest.raises(WorkspaceRootInvalido):
            fs_web.resolver_raiz_workspace("ws/relativa", "lan")

    def test_raiz_absoluta_se_acepta(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = _ws_en(tmp)
            assert fs_web.resolver_raiz_workspace(str(ws), "lan") == ws.resolve()


class TestF02ExplorarSinShellInjection:
    """``tema`` es un ARGUMENTO, nunca codigo shell (B9.61-B-R F-02).

    Antes ``tema`` se interpolaba en un f-string con comillas dobles y se
    ejecutaba con ``shell=True``, permitiendo ``"; cat /tmp/X; echo "``.
    """

    @staticmethod
    def _ws_con_secreto(tmp):
        ws = Path(tmp) / "ws"
        ws.mkdir()
        (ws / "a.py").write_text("def pago():\n    return 1\n", encoding="utf-8")
        secreto = Path(tmp) / "SECRET"
        secreto.write_text("AUDIT_SECRET_VALUE", encoding="utf-8")
        fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
        return ws, secreto

    def test_tema_normal_sigue_funcionando(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _ = self._ws_con_secreto(tmp)
            lineas = wa._explorar_web({"tema": "pago", "directorio": str(ws)})
            assert any("pago" in l for l in lineas)

    @pytest.mark.parametrize(
        "plantilla",
        [
            '"; cat {s}; echo "',
            "pago; cat {s}",
            "pago && cat {s}",
            "pago | cat {s}",
            "$(cat {s})",
            "`cat {s}`",
            "'; touch {s}.pwn; echo '",
        ],
    )
    def test_tema_con_metacaracteres_no_ejecuta(self, plantilla):
        """Ningun metacarácter puede producir ejecucion secundaria."""
        with tempfile.TemporaryDirectory() as tmp:
            ws, secreto = self._ws_con_secreto(tmp)
            tema = plantilla.format(s=secreto)
            lineas = wa._explorar_web({"tema": tema, "directorio": str(ws)})
            # El secreto no aparece en la salida (no se ejecuto `cat`).
            assert not any("AUDIT_SECRET_VALUE" in l for l in lineas)
            # No se creo el fichero lateral de `touch` (no habia ejecucion).
            assert not Path(f"{secreto}.pwn").exists()

    def test_comando_construido_usa_argumento_literal(self):
        """``shlex.split`` debe devolver el tema como UN argumento."""
        import shlex

        with mock.patch.object(sc, "_herramienta_busqueda", return_value="rg"):
            with mock.patch.object(sc, "_ejecutar_comando", return_value=(0, "", "")) as ej:
                sc._buscar_en_codigo('"; touch /tmp/pwn; echo "', ".")
        comando = ej.call_args.args[0]
        argv = shlex.split(comando)
        assert argv[0] == "rg"
        assert argv[-1] == '"; touch /tmp/pwn; echo "'
        assert len(argv) == 6  # rg -n -i --max-count 5 <tema>

    def test_no_hay_interpolacion_directa_de_tema(self):
        """No se reintroduce el f-string con comillas dobles."""
        import inspect

        src = inspect.getsource(sc._buscar_en_codigo)
        assert '"{tema}"' not in src
        assert "shlex.quote" in src


class TestF03PluginPorFrontera:
    """``origen`` de plugin es input atacante: si es ruta local, pasa por la
    frontera ANTES de que ``_plugin_instalar`` toque el filesystem (F-03)."""

    @staticmethod
    def _ws(tmp):
        ws = Path(tmp) / "ws"
        ws.mkdir()
        dentro = ws / "plugin_ok"
        dentro.mkdir()
        (dentro / "plugin.json").write_text("{}", encoding="utf-8")
        fuera = Path(tmp) / "fuera"
        fuera.mkdir()
        (fuera / "plugin.json").write_text("{}", encoding="utf-8")
        fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=ws.resolve()))
        return ws, dentro, fuera

    def test_plugin_local_dentro_se_permite(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, dentro, _ = self._ws(tmp)
            origen = wa._origen_plugin_por_frontera(str(dentro))
            # B9.61-C (OD-7): devuelve una representación semántica explícita.
            assert isinstance(origen, wa.LocalPath)
            assert origen.ruta == dentro.resolve()

    def test_plugin_local_fuera_se_deniega(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, _, fuera = self._ws(tmp)
            with pytest.raises(ErrorFrontera) as info:
                wa._origen_plugin_por_frontera(str(fuera))
            assert info.value.codigo == "outside_workspace"

    def test_plugin_por_traversal_se_deniega(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ws(tmp)
            for origen in ("../fuera", "a/../../fuera", ".."):
                with pytest.raises(ErrorFrontera):
                    wa._origen_plugin_por_frontera(origen)

    def test_plugin_por_symlink_que_escapa_se_deniega(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws, _, fuera = self._ws(tmp)
            (ws / "link").symlink_to(fuera, target_is_directory=True)
            with pytest.raises(ErrorFrontera):
                wa._origen_plugin_por_frontera(str(ws / "link"))

    def test_plugin_etc_se_deniega(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._ws(tmp)
            with pytest.raises(ErrorFrontera):
                wa._origen_plugin_por_frontera("/etc")

    def test_origen_remoto_por_url_no_se_rota(self):
        """Solo `https://…` es un RemoteURL soportado (C-R1-02).

        R2-F01: `usuario/repo` NO es prueba de origen remoto (también es una
        ruta local válida), así que ya no se acepta sin validar. `http://` ya no
        se degrada a URL remota: se deniega explícitamente.
        """
        with tempfile.TemporaryDirectory() as tmp:
            self._ws(tmp)
            origen = wa._origen_plugin_por_frontera("https://ejemplo/x.zip")
            assert isinstance(origen, wa.RemoteURL)
            assert origen.como_texto() == "https://ejemplo/x.zip"
            with pytest.raises(ErrorFrontera) as info:
                wa._origen_plugin_por_frontera("http://a/b.zip")
            assert info.value.codigo == "unsupported_origin"

    def test_plugin_instalar_no_recibe_ruta_externa(self):
        """El denegado ocurre ANTES de llamar a ``_plugin_instalar``."""
        with tempfile.TemporaryDirectory() as tmp:
            _, _, fuera = self._ws(tmp)
            cola = queue.Queue()
            with mock.patch.object(sc, "_plugin_instalar") as inst:
                wa._ejecutar_accion({"accion": "plugin_install", "origen": str(fuera)}, cola)
                inst.assert_not_called()
            eventos = _eventos(cola)
            fin = [e for e in eventos if e.get("tipo") == "accion_ejecutada"]
            assert fin and fin[0]["ok"] is False


# ===========================================================================
# 13. Corrección R2-F01: clasificación de origen de plugin fail-closed
# ===========================================================================
class TestR2F01ClasificacionFailClosed:
    """``origen`` ambiguo ⇒ ruta local. Solo una URL con esquema es remota.

    R2-F01: la heurística anterior (``len(segmentos)``, extensiones, forma
    ``user/repo``/``./x``) clasificaba rutas locales como remotas, con lo que
    evitaban la frontera y ``_plugin_leer_manifest`` leía fuera del workspace.
    """

    @staticmethod
    def _montaje(tmp):
        """Raíz congelada A, cwd B, y un plugin malicioso SOLO en B."""
        import os

        raiz = Path(tmp) / "A"
        raiz.mkdir()
        cwd_b = Path(tmp) / "B"
        cwd_b.mkdir()
        (cwd_b / "PLUG_R2_E2E").mkdir()
        (cwd_b / "PLUG_R2_E2E" / "plugin.json").write_text('{"nombre":"PWN"}', encoding="utf-8")
        (cwd_b / "user").mkdir()
        (cwd_b / "user" / "repo").mkdir()
        (cwd_b / "user" / "repo" / "plugin.json").write_text("{}", encoding="utf-8")
        anterior = Path.cwd()
        os.chdir(cwd_b)
        fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=raiz.resolve()))
        return raiz, cwd_b, anterior

    # --- el exploit exacto de la auditoría ---------------------------------
    def test_exploit_r2_f01_ya_no_lee_fuera(self):
        """``./PLUG_R2_E2E`` con raiz A y cwd B: nunca debe leerse el manifest."""
        import os

        with tempfile.TemporaryDirectory() as tmp:
            raiz, cwd_b, anterior = self._montaje(tmp)
            try:
                cola = queue.Queue()
                with mock.patch.object(sc, "_plugin_leer_manifest") as man:
                    with mock.patch.object(sc, "_plugin_instalar") as inst:
                        wa._ejecutar_accion(
                            {"accion": "plugin_install", "origen": "./PLUG_R2_E2E"}, cola
                        )
                # El directorio malicioso está en B, fuera de la raíz A.
                assert (cwd_b / "PLUG_R2_E2E" / "plugin.json").exists()
                leidos = [str(c.args[0]) for c in man.call_args_list]
                assert not leidos, f"manifest leído fuera de la raíz: {leidos}"
                for c in inst.call_args_list:
                    assert str(c.args[0]).startswith(str(raiz))
            finally:
                os.chdir(anterior)

    def test_exploit_r2_f01_por_websocket_real(self):
        """Mismo exploit por el camino WebSocket real (USE_PLUGINS)."""
        import os

        from fastapi.testclient import TestClient

        clave = "claveb961bdepruebaclavevalida123"
        with tempfile.TemporaryDirectory() as tmp:
            raiz, cwd_b, anterior = self._montaje(tmp)
            try:
                app = wa.crear_app(api_token=clave, host="127.0.0.1", workspace_root=str(raiz))
                assert fs_web.frontera_activa().raiz == raiz.resolve()
                cli = TestClient(app)
                with mock.patch.object(sc, "_plugin_leer_manifest") as man:
                    with cli.websocket_connect(f"/ws?api_key={clave}") as w:
                        w.send_text(
                            json.dumps(
                                {
                                    "tipo": "accion",
                                    "accion": "plugin_install",
                                    "origen": "./PLUG_R2_E2E",
                                }
                            )
                        )
                        for _ in range(8):
                            if w.receive_json().get("tipo") == "accion_ejecutada":
                                break
                leidos = [str(c.args[0]) for c in man.call_args_list]
                assert not leidos, f"manifest leído por WS fuera de la raíz: {leidos}"
                cli.close()
            finally:
                os.chdir(anterior)

    # --- adversarial: ninguna forma ambigua puede ser REMOTO ---------------
    @pytest.mark.parametrize(
        "origen",
        [
            "./x",
            "./a/b",
            "a/b",
            "a/b/c",
            "a/b.zip",
            "user/repo",
            "user/repo.git",
            "../x",
            "../../x",
            "foo/../x",
            "./plugin",
            "./user/repo",
            "foo/bar",
            "foo/bar/plugin",
            "foo/bar.zip",
            "foo/../bar",
            "foo/bar/../../outside",
        ],
    )
    def test_ninguna_forma_ambigua_se_clasifica_como_remota(self, origen):
        with tempfile.TemporaryDirectory() as tmp:
            raiz, _cwd_b, anterior = self._montaje(tmp)
            try:
                import os

                os.chdir(anterior)
                assert not wa._es_origen_plugin_remoto(origen), origen
            finally:
                os.chdir(anterior)

    @pytest.mark.parametrize(
        "origen",
        [
            "./plugin",
            "./user/repo",
            "foo/bar",
            "foo/bar/plugin",
            "foo/bar.zip",
            "user/repo",
            "user/repo.git",
        ],
    )
    def test_locales_ambiguos_pasan_por_la_frontera(self, origen):
        """Se canonicalizan contra la raíz, nunca contra el cwd."""
        import os

        with tempfile.TemporaryDirectory() as tmp:
            raiz, _cwd_b, anterior = self._montaje(tmp)
            try:
                os.chdir(anterior)
                resultado = wa._origen_plugin_por_frontera(origen)
                # B9.61-C (OD-7): la asercion usa la ruta canonica explicita.
                assert isinstance(resultado, wa.LocalPath)
                assert str(resultado.ruta).startswith(str(raiz)), resultado
            finally:
                os.chdir(anterior)

    @pytest.mark.parametrize(
        "origen",
        ["../plugin", "../../plugin", "../../outside", "foo/../../outside", "a/b/../../../x"],
    )
    def test_traversal_que_escapa_se_deniega(self, origen):
        """Traversal que sale de la raíz: DENY."""
        import os

        with tempfile.TemporaryDirectory() as tmp:
            raiz, _cwd_b, anterior = self._montaje(tmp)
            try:
                os.chdir(anterior)
                with pytest.raises(ErrorFrontera) as info:
                    wa._origen_plugin_por_frontera(origen)
                assert info.value.codigo == "outside_workspace"
            finally:
                os.chdir(anterior)

    @pytest.mark.parametrize("origen", ["foo/../bar", "./a/../b", "foo/bar/../../outside"])
    def test_traversal_que_permanece_dentro_se_canonicaliza(self, origen):
        """Traversal que NO escapa se canonicaliza dentro de la raíz.

        Se rechaza como origen ambiguo (no pasa como remoto), pero se resuelve
        contra la raíz del servidor: nunca se lee fuera del workspace.
        """
        import os

        with tempfile.TemporaryDirectory() as tmp:
            raiz, _cwd_b, anterior = self._montaje(tmp)
            try:
                os.chdir(anterior)
                resultado = wa._origen_plugin_por_frontera(origen)
                # B9.61-C (OD-7): la asercion usa la ruta canonica explicita.
                assert isinstance(resultado, wa.LocalPath)
                assert str(resultado.ruta).startswith(str(raiz)), resultado
                assert ".." not in str(resultado)
            finally:
                os.chdir(anterior)

    @pytest.mark.parametrize("origen", ["/etc/passwd", "/tmp/plugin", "/root/plugin", "~/.ssh"])
    def test_absolutos_y_home_se_deniegan(self, origen):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            raiz, _cwd_b, anterior = self._montaje(tmp)
            try:
                os.chdir(anterior)
                with pytest.raises(ErrorFrontera) as info:
                    wa._origen_plugin_por_frontera(origen)
                assert info.value.codigo == "outside_workspace"
            finally:
                os.chdir(anterior)

    def test_symlink_interno_que_apunta_fuera_se_deniega(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "A"
            raiz.mkdir()
            fuera = Path(tmp) / "outside"
            fuera.mkdir()
            (fuera / "plugin.json").write_text("{}", encoding="utf-8")
            (raiz / "plugin").symlink_to(fuera, target_is_directory=True)
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=raiz.resolve()))
            with pytest.raises(ErrorFrontera):
                wa._origen_plugin_por_frontera("plugin")

    def test_plugin_dentro_del_workspace_se_permite(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "A"
            raiz.mkdir()
            (raiz / "plugin.json").write_text("{}", encoding="utf-8")
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=raiz.resolve()))
            origen = wa._origen_plugin_por_frontera("plugin")
            assert isinstance(origen, wa.LocalPath)
            assert origen.ruta == (raiz / "plugin").resolve()

    def test_url_remota_se_preserva(self):
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "A"
            raiz.mkdir()
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=raiz.resolve()))
            # C-R1-02: `https://` es el ÚNICO RemoteURL soportado.
            origen = wa._origen_plugin_por_frontera("https://ejemplo/x.zip")
            assert isinstance(origen, wa.RemoteURL)
            assert origen.como_texto() == "https://ejemplo/x.zip"

    @pytest.mark.parametrize(
        "url",
        [
            "http://a/b.zip",
            "ftp://ejemplo/x.zip",
            "file:///tmp/x.zip",
            "custom://x",
            "a://b",
            "HTTP://ejemplo/x.zip",
        ],
    )
    def test_esquema_remoto_no_soportado_se_deniega(self, url):
        """C-R1-02: solo `https` es RemoteURL; el resto es DENY explícito.

        Ningún esquema se degrada silenciosamente a `LocalPath` (en particular
        `file://`, que sería lectura arbitraria local).
        """
        with tempfile.TemporaryDirectory() as tmp:
            raiz = Path(tmp) / "A"
            raiz.mkdir()
            fs_web.fijar_frontera(Frontera(exposicion="lan", raiz=raiz.resolve()))
            with pytest.raises(ErrorFrontera) as info:
                wa._origen_plugin_por_frontera(url)
            assert info.value.codigo == "unsupported_origin"
            assert not wa._es_origen_plugin_remoto(url)

    def test_plugin_instalar_no_recibe_ruta_local_externa(self):
        """El sink nunca recibe una ruta local externa, ni por cwd."""
        import os

        with tempfile.TemporaryDirectory() as tmp:
            raiz, _cwd_b, anterior = self._montaje(tmp)
            try:
                cola = queue.Queue()
                with mock.patch.object(sc, "_plugin_instalar") as inst:
                    wa._ejecutar_accion({"accion": "plugin_install", "origen": "user/repo"}, cola)
                    inst.assert_called()
                    for c in inst.call_args_list:
                        assert str(c.args[0]).startswith(str(raiz))
            finally:
                os.chdir(anterior)
