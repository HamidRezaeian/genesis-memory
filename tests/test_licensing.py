"""Unit and Integration Tests for GENESIS Commercial Licensing Engine."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from genesis_memory.core.licensing import (
    generate_license_key,
    verify_license_key,
    load_active_license,
    activate_license,
    TIER_CAPABILITIES,
    LicenseStatus,
)
from genesis_memory.cli.run import main as cli_main


class TestLicensingCore(unittest.TestCase):

    def test_default_community_fallback(self):
        """When no license file exists, system defaults to Community tier."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_lic_file = Path(tmp_dir) / "license.json"
            with patch("genesis_memory.core.licensing.LICENSE_FILE_PATH", test_lic_file):
                status = load_active_license()
                self.assertTrue(status.is_valid)
                self.assertEqual(status.tier, "community")
                self.assertTrue(status.has_capability("subconscious_hook"))
                self.assertFalse(status.has_capability("high_ratio_compactor"))

    def test_pro_key_generation_and_verification(self):
        """Pro license keys verify successfully with valid signature and dates."""
        key = generate_license_key(
            tier="pro",
            owner="developer@test.com",
            org="Acme Labs",
            seats=1,
            valid_days=30,
        )
        self.assertTrue(key.startswith("GEN-PRO-"))

        status = verify_license_key(key)
        self.assertTrue(status.is_valid)
        self.assertEqual(status.tier, "pro")
        self.assertEqual(status.owner, "developer@test.com")
        self.assertEqual(status.org, "Acme Labs")
        self.assertEqual(status.seats, 1)
        self.assertGreaterEqual(status.days_remaining, 29)
        self.assertTrue(status.has_capability("high_ratio_compactor"))
        self.assertTrue(status.has_capability("hebbian_sleep_distillation"))

    def test_enterprise_key_generation_and_verification(self):
        """Enterprise keys verify and unlock team capabilities."""
        key = generate_license_key(
            tier="enterprise",
            owner="admin@enterprise.com",
            org="Big Corp Inc.",
            seats=25,
            valid_days=365,
        )
        self.assertTrue(key.startswith("GEN-ENTERPRISE-"))

        status = verify_license_key(key)
        self.assertTrue(status.is_valid)
        self.assertEqual(status.tier, "enterprise")
        self.assertEqual(status.seats, 25)
        self.assertTrue(status.has_capability("team_shared_memory_sync"))
        self.assertTrue(status.has_capability("onprem_docker_gateway"))

    def test_tampered_key_signature_mismatch(self):
        """Modifying payload or signature immediately fails cryptographic verification."""
        valid_key = generate_license_key(
            tier="pro",
            owner="hacker@test.com",
            valid_days=30,
        )
        # Tamper signature (change last character)
        tampered_sig_key = valid_key[:-1] + ("0" if valid_key[-1] != "0" else "1")
        status = verify_license_key(tampered_sig_key)
        self.assertFalse(status.is_valid)
        self.assertIn("signature mismatch", status.message.lower())

    def test_expired_key_rejection(self):
        """Expired licenses are flagged as invalid."""
        # Issue license that expired yesterday
        expired_key = generate_license_key(
            tier="pro",
            owner="olduser@test.com",
            valid_days=-1,
        )
        status = verify_license_key(expired_key)
        self.assertFalse(status.is_valid)
        self.assertIn("expired", status.message.lower())

    def test_activation_flow_and_persistence(self):
        """Activating a valid key writes to license.json and reloads correctly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_genesis_dir = Path(tmp_dir)
            test_lic_file = test_genesis_dir / "license.json"
            with patch("genesis_memory.core.licensing.GENESIS_DIR", test_genesis_dir), \
                 patch("genesis_memory.core.licensing.LICENSE_FILE_PATH", test_lic_file):

                pro_key = generate_license_key(
                    tier="pro",
                    owner="active@test.com",
                    valid_days=90,
                )
                ok, status = activate_license(pro_key)
                self.assertTrue(ok)
                self.assertTrue(test_lic_file.exists())

                # Load and verify from disk
                loaded = load_active_license()
                self.assertTrue(loaded.is_valid)
                self.assertEqual(loaded.tier, "pro")
                self.assertEqual(loaded.owner, "active@test.com")

    def test_cli_license_and_auth_dispatch(self):
        """CLI subcommands 'genesis license' and 'genesis auth' execute cleanly."""
        code_status = cli_main(["license"])
        self.assertEqual(code_status, 0)

        # Generate key via CLI
        code_keygen = cli_main(["license", "keygen", "--tier", "pro", "--owner", "cli@test.com"])
        self.assertEqual(code_keygen, 0)


if __name__ == "__main__":
    unittest.main()
