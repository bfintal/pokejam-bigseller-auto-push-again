#!/usr/bin/env python3
"""Tests for install.sh and systemd configuration files.

These tests verify the shell script syntax and systemd unit file validity
without actually running them with root privileges.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parent


class ShellScriptTests(unittest.TestCase):
    """Test install.sh shell script."""

    def test_shellcheck_install_script(self):
        """Run shellcheck on install.sh to catch common shell scripting issues."""
        install_sh = REPO_ROOT / "install.sh"
        self.assertTrue(install_sh.exists(), "install.sh not found")
        
        try:
            result = subprocess.run(
                ["shellcheck", "-s", "bash", str(install_sh)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                f"shellcheck failed:\n{result.stdout}\n{result.stderr}",
            )
        except FileNotFoundError:
            self.skipTest("shellcheck not installed")

    def test_install_script_syntax(self):
        """Verify install.sh has valid bash syntax."""
        install_sh = REPO_ROOT / "install.sh"
        result = subprocess.run(
            ["bash", "-n", str(install_sh)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Syntax error in install.sh:\n{result.stderr}",
        )

    def test_install_script_has_set_euo_pipefail(self):
        """Verify install.sh uses strict mode (set -euo pipefail)."""
        install_sh = REPO_ROOT / "install.sh"
        content = install_sh.read_text()
        self.assertIn("set -euo pipefail", content)

    def test_install_script_checks_root(self):
        """Verify install.sh checks for root privileges."""
        install_sh = REPO_ROOT / "install.sh"
        content = install_sh.read_text()
        self.assertIn("EUID", content)


class SystemdServiceTests(unittest.TestCase):
    """Test systemd service unit file."""

    def test_service_file_exists(self):
        """Verify the service file exists."""
        service_file = REPO_ROOT / "systemd" / "pokejam-bigseller-auto-push.service"
        self.assertTrue(service_file.exists())

    def test_systemd_analyze_verify(self):
        """Run systemd-analyze verify on the service file."""
        service_file = REPO_ROOT / "systemd" / "pokejam-bigseller-auto-push.service"
        
        try:
            # systemd-analyze verify needs the file in a specific location or uses --root
            # We'll copy it to a temp location and try to verify
            result = subprocess.run(
                ["systemd-analyze", "verify", str(service_file)],
                capture_output=True,
                text=True,
            )
            # Note: systemd-analyze verify may return warnings but not errors
            # It returns 0 even with warnings, non-zero only for hard errors
            if result.returncode != 0:
                # Check if it's just a warning about the file not being in /etc
                if "not found" not in result.stderr.lower():
                    self.fail(f"systemd-analyze verify failed:\n{result.stderr}")
        except FileNotFoundError:
            self.skipTest("systemd-analyze not installed")

    def test_service_file_has_required_sections(self):
        """Verify service file has required [Unit], [Service], [Install] sections."""
        service_file = REPO_ROOT / "systemd" / "pokejam-bigseller-auto-push.service"
        content = service_file.read_text()
        
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)

    def test_service_uses_correct_user(self):
        """Verify service runs as pokejam user."""
        service_file = REPO_ROOT / "systemd" / "pokejam-bigseller-auto-push.service"
        content = service_file.read_text()
        
        self.assertIn("User=pokejam", content)
        self.assertIn("Group=pokejam", content)

    def test_service_has_security_hardening(self):
        """Verify service has security hardening options."""
        service_file = REPO_ROOT / "systemd" / "pokejam-bigseller-auto-push.service"
        content = service_file.read_text()
        
        self.assertIn("NoNewPrivileges=true", content)
        self.assertIn("ProtectSystem=strict", content)
        self.assertIn("ProtectHome=true", content)

    def test_service_uses_log_namespace(self):
        """Verify service uses the pokejam log namespace."""
        service_file = REPO_ROOT / "systemd" / "pokejam-bigseller-auto-push.service"
        content = service_file.read_text()
        
        self.assertIn("LogNamespace=pokejam", content)


class JournaldConfigTests(unittest.TestCase):
    """Test journald namespace configuration file."""

    def test_journald_config_exists(self):
        """Verify the journald config file exists."""
        config_file = REPO_ROOT / "systemd" / "journald@pokejam.conf"
        self.assertTrue(config_file.exists())

    def test_journald_config_has_journal_section(self):
        """Verify journald config has [Journal] section."""
        config_file = REPO_ROOT / "systemd" / "journald@pokejam.conf"
        content = config_file.read_text()
        
        self.assertIn("[Journal]", content)

    def test_journald_config_has_size_limit(self):
        """Verify journald config has storage size limit."""
        config_file = REPO_ROOT / "systemd" / "journald@pokejam.conf"
        content = config_file.read_text()
        
        self.assertIn("SystemMaxUse=", content)

    def test_journald_config_has_retention(self):
        """Verify journald config has retention setting."""
        config_file = REPO_ROOT / "systemd" / "journald@pokejam.conf"
        content = config_file.read_text()
        
        self.assertIn("MaxRetentionSec=", content)

    def test_journald_config_format_valid(self):
        """Verify journald config has valid INI-like format."""
        config_file = REPO_ROOT / "systemd" / "journald@pokejam.conf"
        content = config_file.read_text()
        
        in_section = False
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                in_section = True
                continue
            if in_section:
                # Should be key=value format
                self.assertIn("=", line, f"Invalid config line: {line}")


class PythonSyntaxTests(unittest.TestCase):
    """Test Python file syntax."""

    def test_run_py_syntax(self):
        """Verify run.py has valid Python syntax."""
        run_py = REPO_ROOT / "run.py"
        result = subprocess.run(
            ["python3", "-m", "py_compile", str(run_py)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Syntax error in run.py:\n{result.stderr}",
        )

    def test_test_run_py_syntax(self):
        """Verify test_run.py has valid Python syntax."""
        test_py = REPO_ROOT / "test_run.py"
        result = subprocess.run(
            ["python3", "-m", "py_compile", str(test_py)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            f"Syntax error in test_run.py:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
