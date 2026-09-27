#!/usr/bin/env python3
"""Suite de tests de seguridad para B9.61-A: Web Gateway Authentication + Capability Enforcement.

Cubre:
1. Credential lifecycle (first-run, 0600, loading, runtime fail-closed, timing attack, rotación).
2. HTTP capability enforcement (rutas protegidas, capabilities, públicas, LAN docs).
3. WebSocket enforcement (/ws pre-accept, _API_TOKEN eliminado, /ws/interactive, closed dispatch).
4. Origin & Host enforcement (valid, invalid, missing, forwarding, no cookie auth).
5. Invariants (closed-world capability map, unmapped route fail-closed).
"""

import json
import os
import secrets
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import configuracion
from web import app as web_app
from web import seguridad
from web.app import API_PREFIJO, CAPABILITY_MAP, crear_app

CLAVE_TEST = "clave-super-segura-de-prueba-32chars"

# ===========================================================================
# 1. Authentication & Credential Lifecycle Tests (Casos 1 - 11)
# ===========================================================================
class TestCredentialLifecycle:
    def test_01_first_run_credential_generation(self):
        """1. Generación de credencial en primera ejecución con secrets.token_urlsafe(32)."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            with mock.patch.object(configuracion, "CONFIG_PATH", cfg):
                assert not cfg.exists()
                clave = seguridad.generar_credencial()
                assert clave is not None
                assert len(clave) >= 32
                assert cfg.exists()

    def test_02_credential_persisted(self):
        """2. La credencial generada se persiste en la configuración."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            with mock.patch.object(configuracion, "CONFIG_PATH", cfg):
                clave = seguridad.generar_credencial()
                datos = json.loads(cfg.read_text(encoding="utf-8"))
                assert datos.get("api_key") == clave

    def test_03_permissions_0600(self):
        """3. La credencial almacenada debe tener permisos 0600 en Unix."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            with mock.patch.object(configuracion, "CONFIG_PATH", cfg):
                seguridad.generar_credencial()
                modo = cfg.stat().st_mode & 0o777
                assert modo == 0o600

    def test_04_valid_credential_accepted(self):
        """4. Credencial válida es aceptada."""
        valida = "B" * 32
        assert seguridad.verificar_credencial(valida, valida) is True

    def test_05_invalid_credential_denied(self):
        """5. Credencial inválida es denegada."""
        valida = "B" * 32
        invalida = "C" * 32
        assert seguridad.verificar_credencial(invalida, valida) is False
        assert seguridad.verificar_credencial("", valida) is False
        assert seguridad.verificar_credencial(None, valida) is False

    def test_06_missing_credential_runtime_behavior(self):
        """6. Ausencia de credencial en runtime produce None (fail-closed) y NUNCA regenera."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            with mock.patch.object(configuracion, "CONFIG_PATH", cfg):
                assert not cfg.exists()
                assert seguridad.cargar_credencial() is None
                assert not cfg.exists()

    def test_07_malformed_credential_denied(self):
        """7. Credencial malformada (<32 caracteres, corrupta, permisos inseguros) es rechazada."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            with mock.patch.object(configuracion, "CONFIG_PATH", cfg):
                cfg.write_text(json.dumps({"api_key": "demasiado-corta"}), encoding="utf-8")
                os.chmod(cfg, 0o600)
                assert seguridad.cargar_credencial() is None

                cfg.write_text("{corrupto", encoding="utf-8")
                assert seguridad.cargar_credencial() is None

                cfg.write_text(json.dumps({"api_key": "Z" * 32}), encoding="utf-8")
                os.chmod(cfg, 0o644)
                assert seguridad.cargar_credencial() is None

    def test_08_constant_time_comparison_path(self):
        """8. Comparación en tiempo constante usando secrets.compare_digest."""
        with mock.patch("secrets.compare_digest", wraps=secrets.compare_digest) as mock_cd:
            clave = "K" * 32
            res = seguridad.verificar_credencial(clave, clave)
            assert res is True
            mock_cd.assert_called_once_with(clave, clave)

    def test_09_rotation_invalidates_old_credential(self):
        """9. Rotación invalida la credencial anterior inmediatamente."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            with mock.patch.object(configuracion, "CONFIG_PATH", cfg):
                k1 = seguridad.generar_credencial()
                assert seguridad.cargar_credencial() == k1
                k2 = seguridad.rotar_credencial()
                assert k2 != k1
                assert seguridad.verificar_credencial(k1, seguridad.cargar_credencial()) is False

    def test_10_new_credential_accepted(self):
        """10. La nueva credencial rotada es aceptada."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            with mock.patch.object(configuracion, "CONFIG_PATH", cfg):
                seguridad.generar_credencial()
                k2 = seguridad.rotar_credencial()
                assert seguridad.verificar_credencial(k2, seguridad.cargar_credencial()) is True

    def test_11_credential_never_appears_in_error_response(self):
        """11. La credencial nunca aparece en respuestas de error HTTP."""
        client = TestClient(crear_app(api_token=CLAVE_TEST))
        r = client.get("/api/v1/skills", headers={"X-API-Key": "clave_incorrecta"})
        assert r.status_code == 401
        assert CLAVE_TEST not in r.text
        assert "clave_incorrecta" not in r.text

HEADERS_TEST = {"X-API-Key": CLAVE_TEST}

# ===========================================================================
# 2. HTTP Capability Enforcement Tests (Casos 12 - 19)
# ===========================================================================
class TestHTTPCapabilityEnforcement:
    def test_12_protected_endpoint_without_credential_denied(self):
        """12. Endpoint protegido sin credencial devuelve 401."""
        client = TestClient(crear_app(api_token=CLAVE_TEST))
        r = client.get("/api/v1/skills")
        assert r.status_code == 401

    def test_13_protected_endpoint_with_invalid_credential_denied(self):
        """13. Endpoint protegido con credencial incorrecta devuelve 401."""
        client = TestClient(crear_app(api_token=CLAVE_TEST))
        r = client.get("/api/v1/skills", headers={"X-API-Key": "clave-falsa"})
        assert r.status_code == 401

    def test_14_protected_endpoint_missing_capability_denied(self):
        """14. Credencial válida pero capability requerida denegada en contexto (LAN) -> 403."""
        client = TestClient(crear_app(api_token=CLAVE_TEST))
        r = client.post(
            "/api/v1/daemon",
            json={"accion": "iniciar"},
            headers={**HEADERS_TEST, "X-Forwarded-For": "192.168.1.100"},
        )
        assert r.status_code == 403

    def test_15_valid_credential_and_required_capability_allowed(self):
        """15. Credencial válida con capability permitida autoriza el endpoint."""
        client = TestClient(crear_app(api_token=CLAVE_TEST))
        with mock.patch("snapcontext._skill_listar", return_value=[]):
            r = client.get("/api/v1/skills", headers=HEADERS_TEST)
            assert r.status_code == 200

    def test_16_unknown_capability_denied(self):
        """16. Capability desconocida fuera del modelo de 9 -> fail-closed 403."""
        decl_invalida = seguridad.DeclaracionSuperficie(capacidad="UNKNOWN_CAP", requiere_auth=True)
        with pytest.raises(Exception) as exc:
            seguridad.exigir_politica(decl_invalida, "loopback")
        assert exc.value.status_code == 403

    def test_17_endpoint_cannot_execute_before_authorization(self):
        """17. La acción real del endpoint no debe ejecutarse antes de la autorización."""
        client = TestClient(crear_app(api_token=CLAVE_TEST))
        llamado = False

        def mock_flujo(*args, **kwargs):
            nonlocal llamado
            llamado = True
            return 0

        with mock.patch("snapcontext.flujo_principal", side_effect=mock_flujo):
            r = client.post("/api/v1/query", json={"consulta": "test"})
            assert r.status_code == 401
            assert llamado is False

    def test_18_public_endpoint_behavior_remains_as_specified(self):
        """18. Endpoints públicos (/health, /api/v1/health) responden 200 sin credenciales."""
        client = TestClient(crear_app(api_token=CLAVE_TEST))
        assert client.get("/health").status_code == 200
        assert client.get(f"{API_PREFIJO}/health").status_code == 200

    def test_19_lan_documentation_api_behavior_matches_b960(self):
        """19. Documentación y schema (/docs, /redoc) en LAN devuelven 403."""
        client = TestClient(crear_app(api_token=CLAVE_TEST))
        assert client.get("/docs").status_code == 200
        r_lan = client.get("/docs", headers={"X-Forwarded-For": "10.0.0.1"})
        assert r_lan.status_code == 403

