# Copyright 2026 UniFi Insights contributors
"""Tests for ha-unifi-insights integration."""

from .fixtures.deprecations import install_deprecation_report_handler

# Before conftest imports the integration, so import-time reports are caught too.
install_deprecation_report_handler()
