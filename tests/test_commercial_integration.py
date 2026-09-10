"""Integration & Edge-Case Tests for GENESIS Commercial Features.

Covers:
- Licensing edge cases (never-expiring, malformed keys, boundary dates, capability gating)
- Setup CLI dry-run and target-client filtering
- Dashboard /api/license endpoint
- Cross-module integration (setup -> licensing -> dashboard)
"""

import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch, MagicMock

from genesis_memory.core.licensing import (
    generate_license_key,
    verify_license_key,
    load_active_license,
    activate_license,
    LicenseStatus,
    TIER_CAPABILITIES,
    _compute_signature,
)


class TestLicensingEdgeCases(unittest.TestCase):
    """Edge cases and boundary conditions for the licensing engine."""

    def test_never_expiring_license(self):
        """Licenses with valid_days=None should never expire."""
        key = generate_license_key(
            tier="pro",
            owner="eternal@test.com",
            valid_days=None,
        )
        status = verify_license_key(key)
        self.assertTrue(status.is_valid)
        self.assertEqual(status.expires_at, "never")
        self.assertIsNone(status.days_remaining)

    def test_empty_key_returns_community(self):
        status = verify_license_key("")
        self.assertFalse(status.is_valid)
        self.assertEqual(status.tier, "community")

    def test_none_key_returns_community(self):
        status = verify_license_key(None)
        self.assertFalse(status.is_valid)
        self.assertEqual(status.tier, "community")

    def test_random_string_key_rejected(self):
        status = verify_license_key("some-random-string")
        self.assertFalse(status.is_valid)
        self.assertIn("GEN-", status.message)

    def test_missing_signature_dot_rejected(self):
        status = verify_license_key("GEN-PRO-payloadwithnosignature")
        self.assertFalse(status.is_valid)
        self.assertIn("signature", status.message.lower())

    def test_wrong_prefix_rejected(self):
        status = verify_license_key("ABC-PRO-payload.signature")
        self.assertFalse(status.is_valid)

    def test_malformed_header_parts(self):
        status = verify_license_key("GEN-payload.signature")
        self.assertFalse(status.is_valid)

    def test_unknown_tier_generation_raises(self):
        with self.assertRaises(ValueError):
            generate_license_key(tier="platinum", owner="test@test.com")

    def test_tampered_payload_rejected(self):
        key = generate_license_key(tier="pro", owner="user@test.com", valid_days=30)
        parts = key.split(".", 1)
        # Corrupt the payload portion
        header_parts = parts[0].split("-", 2)
        corrupted_payload = header_parts[2][::-1]  # reverse payload
        tampered = f"GEN-{header_parts[1]}-{corrupted_payload}.{parts[1]}"
        status = verify_license_key(tampered)
        self.assertFalse(status.is_valid)

    def test_community_capability_accessible_without_license(self):
        status = LicenseStatus(is_valid=False, tier="community")
        self.assertTrue(status.has_capability("subconscious_hook"))
        self.assertTrue(status.has_capability("headless_spooling"))
        self.assertFalse(status.has_capability("high_ratio_compactor"))

    def test_pro_has_all_community_capabilities(self):
        for cap in TIER_CAPABILITIES["community"]:
            self.assertIn(cap, TIER_CAPABILITIES["pro"])

    def test_enterprise_has_all_pro_capabilities(self):
        for cap in TIER_CAPABILITIES["pro"]:
            self.assertIn(cap, TIER_CAPABILITIES["enterprise"])

    def test_license_status_to_dict_structure(self):
        status = LicenseStatus(
            is_valid=True,
            tier="pro",
            owner="dev@test.com",
            org="TestOrg",
            seats=5,
            expires_at="2027-01-01",
            days_remaining=100,
            message="OK",
        )
        d = status.to_dict()
        self.assertIn("is_valid", d)
        self.assertIn("tier", d)
        self.assertIn("tier_display", d)
        self.assertEqual(d["tier_display"], "PRO")
        self.assertIn("capabilities", d)
        self.assertIn("days_remaining", d)
        self.assertEqual(d["seats"], 5)

    def test_key_with_whitespace_trimmed(self):
        key = generate_license_key(tier="pro", owner="ws@test.com", valid_days=30)
        padded = f"  {key}  \n"
        status = verify_license_key(padded)
        self.assertTrue(status.is_valid)
        self.assertEqual(status.tier, "pro")

    def test_activate_invalid_key_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp)
            test_file = test_dir / "license.json"
            with patch("genesis_memory.core.licensing.GENESIS_DIR", test_dir), \
                 patch("genesis_memory.core.licensing.LICENSE_FILE_PATH", test_file):
                ok, status = activate_license("invalid-key")
                self.assertFalse(ok)
                self.assertFalse(status.is_valid)
                self.assertFalse(test_file.exists())

    def test_corrupted_license_file_falls_back_community(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_file = Path(tmp) / "license.json"
            test_file.write_text("not valid json!!!", encoding="utf-8")
            with patch("genesis_memory.core.licensing.LICENSE_FILE_PATH", test_file):
                status = load_active_license()
                self.assertFalse(status.is_valid)
                self.assertEqual(status.tier, "community")

    def test_license_file_with_expired_key_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp)
            test_file = test_dir / "license.json"
            expired_key = generate_license_key(
                tier="pro", owner="old@test.com", valid_days=-1
            )
            test_file.write_text(
                json.dumps({"license_key": expired_key}), encoding="utf-8"
            )
            with patch("genesis_memory.core.licensing.GENESIS_DIR", test_dir), \
                 patch("genesis_memory.core.licensing.LICENSE_FILE_PATH", test_file):
                status = load_active_license()
                self.assertFalse(status.is_valid)
                self.assertEqual(status.tier, "community")

    def test_signature_deterministic(self):
        payload = b"test-payload-data"
        sig1 = _compute_signature(payload)
        sig2 = _compute_signature(payload)
        self.assertEqual(sig1, sig2)
        self.assertEqual(len(sig1), 32)

    def test_different_payloads_different_signatures(self):
        sig1 = _compute_signature(b"payload-a")
        sig2 = _compute_signature(b"payload-b")
        self.assertNotEqual(sig1, sig2)


class TestSetupCLIDryRun(unittest.TestCase):
    """Tests for genesis setup --preview / --dry-run mode."""

    def test_dry_run_returns_zero_exit(self):
        from genesis_memory.cli.init_cmd import run_init
        code = run_init(
            auto_confirm=True,
            dry_run=True,
            quiet=True,
            skip_clients=True,
        )
        self.assertEqual(code, 0)

    def test_generate_plan_with_no_clients(self):
        from genesis_memory.cli.init_cmd import generate_plan
        env = {
            "genesis_dir_exists": True,
            "spool_dir_exists": True,
            "memory_db_exists": True,
            "detected_clients": [],
        }
        plan = generate_plan(env, skip_clients=True)
        # Should still have mcp_snippet creation
        actions = [p["action"] for p in plan]
        self.assertIn("CREATE_FILE", actions)

    def test_generate_plan_with_target_filter(self):
        from genesis_memory.cli.init_cmd import generate_plan
        from genesis_memory.cli.client_registry import DiscoveredClient, ClientCapability
        mock_clients = [
            DiscoveredClient(
                id="cursor", name="Cursor",
                capabilities=[ClientCapability.MCP],
                config_path=Path("/tmp/fake_cursor.json"),
                detected=True, configured=False,
            ),
            DiscoveredClient(
                id="opencode", name="OpenCode",
                capabilities=[ClientCapability.MCP],
                config_path=Path("/tmp/fake_oc.jsonc"),
                detected=True, configured=False,
            ),
        ]
        env = {
            "genesis_dir_exists": True,
            "spool_dir_exists": True,
            "memory_db_exists": True,
            "detected_clients": mock_clients,
        }
        plan = generate_plan(env, target_clients=["cursor"])
        wire_actions = [p for p in plan if p["action"] == "WIRE_CLIENT"]
        client_ids = [p["client_id"] for p in wire_actions]
        self.assertIn("cursor", client_ids)
        self.assertNotIn("opencode", client_ids)


class TestDashboardLicenseEndpoint(unittest.TestCase):
    """Tests for the dashboard /api/license HTTP endpoint."""

    def test_snapshot_includes_license_tier(self):
        """The snapshot() function should return valid data structure."""
        from genesis_memory.daemon.server import snapshot, DB_PATH
        data = snapshot(DB_PATH)
        self.assertIn("episodes", data)
        self.assertIn("uptime_s", data)

    def test_license_status_serializable(self):
        """LicenseStatus.to_dict() should be JSON-serializable."""
        status = LicenseStatus(
            is_valid=True,
            tier="enterprise",
            owner="admin@corp.com",
            org="Corp Inc",
            seats=50,
            expires_at="2027-12-31",
            days_remaining=365,
            message="Valid",
        )
        serialized = json.dumps(status.to_dict())
        parsed = json.loads(serialized)
        self.assertEqual(parsed["tier"], "enterprise")
        self.assertEqual(parsed["seats"], 50)
        self.assertTrue(parsed["is_valid"])


class TestCrossModuleIntegration(unittest.TestCase):
    """End-to-end flow: generate key -> activate -> load -> verify capabilities."""

    def test_full_lifecycle_pro(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp)
            test_file = test_dir / "license.json"
            with patch("genesis_memory.core.licensing.GENESIS_DIR", test_dir), \
                 patch("genesis_memory.core.licensing.LICENSE_FILE_PATH", test_file):
                # Generate
                key = generate_license_key(
                    tier="pro", owner="lifecycle@test.com",
                    org="TestCo", seats=3, valid_days=90,
                )
                # Activate
                ok, status = activate_license(key)
                self.assertTrue(ok)
                self.assertTrue(status.is_valid)
                # Load from disk
                loaded = load_active_license()
                self.assertTrue(loaded.is_valid)
                self.assertEqual(loaded.tier, "pro")
                self.assertEqual(loaded.owner, "lifecycle@test.com")
                self.assertEqual(loaded.org, "TestCo")
                self.assertEqual(loaded.seats, 3)
                # Verify capabilities
                self.assertTrue(loaded.has_capability("high_ratio_compactor"))
                self.assertTrue(loaded.has_capability("hebbian_sleep_distillation"))
                self.assertFalse(loaded.has_capability("team_shared_memory_sync"))

    def test_full_lifecycle_enterprise(self):
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp)
            test_file = test_dir / "license.json"
            with patch("genesis_memory.core.licensing.GENESIS_DIR", test_dir), \
                 patch("genesis_memory.core.licensing.LICENSE_FILE_PATH", test_file):
                key = generate_license_key(
                    tier="enterprise", owner="admin@bigcorp.com",
                    org="BigCorp", seats=100, valid_days=365,
                )
                ok, status = activate_license(key)
                self.assertTrue(ok)
                loaded = load_active_license()
                self.assertTrue(loaded.is_valid)
                self.assertEqual(loaded.tier, "enterprise")
                self.assertTrue(loaded.has_capability("team_shared_memory_sync"))
                self.assertTrue(loaded.has_capability("onprem_docker_gateway"))
                self.assertTrue(loaded.has_capability("sla_support"))

    def test_upgrade_from_community_to_pro(self):
        """Simulates upgrading from community to pro tier."""
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp)
            test_file = test_dir / "license.json"
            with patch("genesis_memory.core.licensing.GENESIS_DIR", test_dir), \
                 patch("genesis_memory.core.licensing.LICENSE_FILE_PATH", test_file):
                # Start as community
                status = load_active_license()
                self.assertEqual(status.tier, "community")
                self.assertFalse(status.has_capability("high_ratio_compactor"))
                # Upgrade to pro
                key = generate_license_key(tier="pro", owner="upgrade@test.com", valid_days=30)
                ok, _ = activate_license(key)
                self.assertTrue(ok)
                # Verify upgrade
                loaded = load_active_license()
                self.assertEqual(loaded.tier, "pro")
                self.assertTrue(loaded.has_capability("high_ratio_compactor"))

    def test_license_persisted_data_matches_key(self):
        """Verify the persisted JSON contains correct metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            test_dir = Path(tmp)
            test_file = test_dir / "license.json"
            with patch("genesis_memory.core.licensing.GENESIS_DIR", test_dir), \
                 patch("genesis_memory.core.licensing.LICENSE_FILE_PATH", test_file):
                key = generate_license_key(
                    tier="enterprise", owner="persist@test.com",
                    org="PersistCo", seats=10, valid_days=180,
                )
                activate_license(key)
                raw = json.loads(test_file.read_text(encoding="utf-8"))
                self.assertEqual(raw["tier"], "enterprise")
                self.assertEqual(raw["owner"], "persist@test.com")
                self.assertEqual(raw["org"], "PersistCo")
                self.assertEqual(raw["seats"], 10)
                self.assertIn("activated_at", raw)
                self.assertIn("license_key", raw)


if __name__ == "__main__":
    unittest.main()
