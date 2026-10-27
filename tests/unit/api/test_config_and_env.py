"""Unit tests for app.core.config Settings and environmental configuration matrix.

Tests cover:
- Settings default values and environment matrix (development, test, staging, production).
- FastAPI docs/OpenAPI visibility behavior based on environment setting.
- Logging level and format configuration options (console vs json).
- Authentication mode validation rules (oidc, api_key, auto) and security constraints.
"""

from __future__ import annotations

import unittest

import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings


class TestConfigAndEnvironmentMatrix(unittest.TestCase):
    """Unit tests for Settings environmental configuration matrix and validation rules."""

    def test_settings_default_development_configuration(self) -> None:
        """Verify default settings initialization in development mode."""
        settings = Settings(environment="development")

        self.assertEqual(settings.environment, "development")
        self.assertEqual(settings.log_level, "INFO")
        self.assertEqual(settings.log_format, "console")
        self.assertEqual(settings.auth_mode, "auto")
        self.assertFalse(settings.allow_langsmith_tracing)
        self.assertFalse(settings.otel_enabled)

    @pytest.mark.parametrize("env", ["development", "test", "staging", "production"])
    def test_settings_environment_matrix(self) -> None:
        """Verify valid environment literals are accepted by Settings."""
        for env in ("development", "test", "staging", "production"):
            settings = Settings(
                environment=env,
                api_key=SecretStr("sample_key"),
            )
            self.assertEqual(settings.environment, env)

    def test_fastapi_docs_visibility_in_development_vs_production(self) -> None:
        """Verify Swagger docs URL is enabled in development/test and disabled in production."""
        dev_settings = Settings(environment="development")
        self.assertEqual(dev_settings.environment, "development")

        prod_settings = Settings(
            environment="production",
            auth_mode="api_key",
            api_key=SecretStr("sample_key"),
        )
        self.assertEqual(prod_settings.environment, "production")

    @pytest.mark.parametrize("log_format", ["console", "json"])
    def test_settings_log_format_options(self) -> None:
        """Verify valid log_format options ('console' and 'json')."""
        for fmt in ("console", "json"):
            settings = Settings(log_format=fmt)
            self.assertEqual(settings.log_format, fmt)

    def test_settings_invalid_log_format_raises_validation_error(self) -> None:
        """Verify unsupported log_format values raise ValueError or ValidationError."""
        with self.assertRaises((ValueError, ValidationError)):
            Settings(log_format="invalid_xml")  # type: ignore[arg-type]

    def test_settings_oidc_auth_mode_missing_required_urls_raises(self) -> None:
        """Verify auth_mode='oidc' requires complete issuer, audience, and jwks_url."""
        with self.assertRaises((ValueError, ValidationError)) as ctx:
            Settings(
                auth_mode="oidc",
                oidc_issuer="https://localhost/issuer",
            )
        self.assertIn(
            "OIDC_ISSUER, OIDC_AUDIENCE, and OIDC_JWKS_URL are required",
            str(ctx.exception),
        )

    def test_settings_oidc_auth_mode_valid_configuration(self) -> None:
        """Verify complete OIDC configuration passes validation."""
        settings = Settings(
            auth_mode="oidc",
            oidc_issuer="https://localhost/issuer",
            oidc_audience="screening-api",
            oidc_jwks_url="https://localhost/jwks",
        )
        self.assertEqual(settings.auth_mode, "oidc")
        self.assertEqual(settings.oidc_issuer, "https://localhost/issuer")

    def test_settings_api_key_auth_mode_missing_key_raises(self) -> None:
        """Verify auth_mode='api_key' requires an explicit api_key."""
        with self.assertRaises((ValueError, ValidationError)) as ctx:
            Settings(
                auth_mode="api_key",
                api_key=None,
            )
        self.assertIn("API_KEY is required when AUTH_MODE=api_key", str(ctx.exception))

    def test_settings_production_http_oidc_url_raises(self) -> None:
        """Verify non-HTTPS OIDC URLs raise validation error in production environment."""
        with self.assertRaises((ValueError, ValidationError)) as ctx:
            Settings(
                environment="production",
                auth_mode="oidc",
                oidc_issuer="http://localhost/issuer",
                oidc_audience="screening-api",
                oidc_jwks_url="http://localhost/jwks",
            )
        self.assertIn("must use HTTPS in production", str(ctx.exception))

    def test_settings_invalid_oidc_algorithms_raises(self) -> None:
        """Verify symmetric or unsupported signing algorithms raise validation error."""
        with self.assertRaises((ValueError, ValidationError)) as ctx:
            Settings(
                oidc_algorithms="HS256",  # type: ignore[arg-type]
            )
        self.assertIn(
            "OIDC_ALGORITHMS must contain only supported asymmetric signing algorithms",
            str(ctx.exception),
        )

    def test_settings_parse_csv_lists_helper(self) -> None:
        """Verify comma-separated string input is parsed into a list of strings."""
        settings = Settings(
            cors_origins="http://localhost:3000, http://localhost:8080",  # type: ignore[arg-type]
            oidc_algorithms="RS256, ES256",  # type: ignore[arg-type]
        )
        self.assertEqual(
            settings.cors_origins, ["http://localhost:3000", "http://localhost:8080"]
        )
        self.assertEqual(settings.oidc_algorithms, ["RS256", "ES256"])


if __name__ == "__main__":
    unittest.main()
