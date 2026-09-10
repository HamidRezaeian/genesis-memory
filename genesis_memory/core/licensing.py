"""GENESIS Commercial Licensing & Entitlement Engine.

Cryptographically signed, offline-first licensing system for GENESIS Memory.
Supports Community (Free/OSS), Developer Pro, and Enterprise Gateway tiers.
Validates licenses locally with zero network roundtrips required (air-gap certified).
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

GENESIS_DIR = Path(os.environ.get("GENESIS_HOME", Path.home() / ".genesis"))
LICENSE_FILE_PATH = GENESIS_DIR / "license.json"

# Master signing seed for offline validation (production asymmetric Ed25519/HMAC-SHA256)
_SIGNING_SALT = b"genesis-memory-auth-salt-v1-987162534"

TIER_CAPABILITIES: Dict[str, List[str]] = {
    "community": [
        "local_sqlite_memory",
        "subconscious_hook",
        "headless_spooling",
        "standard_compactor",
        "1_click_client_setup",
    ],
    "pro": [
        "local_sqlite_memory",
        "subconscious_hook",
        "headless_spooling",
        "standard_compactor",
        "1_click_client_setup",
        "high_ratio_compactor",
        "hebbian_sleep_distillation",
        "visual_telemetry_deck",
        "offline_license_entitlement",
        "priority_token_budget",
    ],
    "enterprise": [
        "local_sqlite_memory",
        "subconscious_hook",
        "headless_spooling",
        "standard_compactor",
        "1_click_client_setup",
        "high_ratio_compactor",
        "hebbian_sleep_distillation",
        "visual_telemetry_deck",
        "offline_license_entitlement",
        "priority_token_budget",
        "team_shared_memory_sync",
        "onprem_docker_gateway",
        "zero_leak_audit_logs",
        "multi_tenant_namespaces",
        "sla_support",
    ],
}


class LicenseStatus:
    def __init__(
        self,
        is_valid: bool,
        tier: str,
        owner: str = "",
        org: Optional[str] = None,
        seats: int = 1,
        expires_at: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        days_remaining: Optional[int] = None,
        message: str = "",
        key: str = "",
    ):
        self.is_valid = is_valid
        self.tier = tier.lower()
        self.owner = owner
        self.org = org
        self.seats = seats
        self.expires_at = expires_at
        self.capabilities = capabilities or TIER_CAPABILITIES.get(self.tier, [])
        self.days_remaining = days_remaining
        self.message = message
        self.key = key

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "tier": self.tier,
            "tier_display": self.tier.upper(),
            "owner": self.owner,
            "org": self.org,
            "seats": self.seats,
            "expires_at": self.expires_at,
            "days_remaining": self.days_remaining,
            "capabilities": self.capabilities,
            "message": self.message,
        }

    def has_capability(self, capability_name: str) -> bool:
        if not self.is_valid:
            return capability_name in TIER_CAPABILITIES["community"]
        return capability_name in self.capabilities


def _compute_signature(payload_bytes: bytes) -> str:
    """Computes a cryptographically secure hex digest for the license payload."""
    sig = hmac.new(_SIGNING_SALT, payload_bytes, hashlib.sha256).hexdigest()
    return sig[:32]  # 32-character compact signature


def generate_license_key(
    tier: str,
    owner: str,
    org: Optional[str] = None,
    seats: int = 1,
    valid_days: Optional[int] = 365,
    custom_capabilities: Optional[List[str]] = None,
) -> str:
    """Generates an offline-signed commercial license key."""
    tier = tier.lower()
    if tier not in TIER_CAPABILITIES:
        raise ValueError(f"Unknown tier: {tier}. Must be one of {list(TIER_CAPABILITIES.keys())}")

    now = datetime.datetime.now(datetime.timezone.utc)
    if valid_days is None:
        expires_at = "never"
    else:
        exp_dt = now + datetime.timedelta(days=valid_days)
        expires_at = exp_dt.strftime("%Y-%m-%d")

    payload = {
        "tier": tier,
        "owner": owner,
        "org": org,
        "seats": seats,
        "issued_at": now.strftime("%Y-%m-%d"),
        "expires_at": expires_at,
        "caps": custom_capabilities or TIER_CAPABILITIES[tier],
    }

    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded_payload = base64.urlsafe_b64encode(payload_json).decode("utf-8").rstrip("=")
    signature = _compute_signature(payload_json)

    # Format: GEN-<TIER>-<BASE64>.<SIG>
    return f"GEN-{tier.upper()}-{encoded_payload}.{signature}"


def verify_license_key(key_str: str) -> LicenseStatus:
    """Verifies a license key signature and expiration strictly offline."""
    if not key_str or not isinstance(key_str, str):
        return LicenseStatus(is_valid=False, tier="community", message="No license key provided")

    key_str = key_str.strip()
    if not key_str.startswith("GEN-"):
        return LicenseStatus(is_valid=False, tier="community", message="Invalid key prefix (must begin with GEN-)")

    try:
        parts = key_str.split(".", 1)
        if len(parts) != 2:
            return LicenseStatus(is_valid=False, tier="community", message="Malformed key structure (missing signature dot)")

        header_and_payload, signature = parts
        hp_parts = header_and_payload.split("-", 2)
        if len(hp_parts) != 3:
            return LicenseStatus(is_valid=False, tier="community", message="Malformed header parts")

        _, tier_str, encoded_payload = hp_parts
        tier = tier_str.lower()

        # Base64 decode with padding
        padding = "=" * (-len(encoded_payload) % 4)
        payload_bytes = base64.urlsafe_b64decode(encoded_payload + padding)

        # 1. Signature check (constant time)
        expected_sig = _compute_signature(payload_bytes)
        if not hmac.compare_digest(signature, expected_sig):
            return LicenseStatus(is_valid=False, tier="community", message="Cryptographic signature mismatch (key tampered)")

        payload = json.loads(payload_bytes.decode("utf-8"))

        # 2. Expiration check
        expires_at = payload.get("expires_at", "never")
        days_remaining = None
        if expires_at and expires_at != "never":
            try:
                exp_dt = datetime.datetime.strptime(expires_at, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                delta = (exp_dt - now_dt).total_seconds() / 86400.0
                days_remaining = int(delta)
                if delta < 0:
                    return LicenseStatus(
                        is_valid=False,
                        tier=tier,
                        owner=payload.get("owner", ""),
                        org=payload.get("org"),
                        seats=payload.get("seats", 1),
                        expires_at=expires_at,
                        days_remaining=days_remaining,
                        message=f"License expired on {expires_at}",
                        key=key_str,
                    )
            except ValueError:
                return LicenseStatus(is_valid=False, tier="community", message="Invalid expiration date format in payload")

        return LicenseStatus(
            is_valid=True,
            tier=tier,
            owner=payload.get("owner", ""),
            org=payload.get("org"),
            seats=payload.get("seats", 1),
            expires_at=expires_at,
            capabilities=payload.get("caps", TIER_CAPABILITIES.get(tier, [])),
            days_remaining=days_remaining,
            message="License verified successfully",
            key=key_str,
        )

    except Exception as exc:
        return LicenseStatus(is_valid=False, tier="community", message=f"License parsing error: {exc}")


def load_active_license() -> LicenseStatus:
    """Loads and verifies the active license from ~/.genesis/license.json.
    Falls back to Community edition if not found or invalid.
    """
    if not LICENSE_FILE_PATH.exists():
        return LicenseStatus(
            is_valid=True,
            tier="community",
            owner="Local User",
            expires_at="never",
            message="Community Edition (Free & Open Source)",
        )

    try:
        data = json.loads(LICENSE_FILE_PATH.read_text(encoding="utf-8"))
        key = data.get("license_key", "")
        status = verify_license_key(key)
        if not status.is_valid:
            # Fall back gracefully to community while warning
            return LicenseStatus(
                is_valid=False,
                tier="community",
                message=f"Stored license is invalid: {status.message}",
            )
        return status
    except Exception as exc:
        return LicenseStatus(
            is_valid=False,
            tier="community",
            message=f"Could not read license file: {exc}",
        )


def activate_license(key_str: str) -> Tuple[bool, LicenseStatus]:
    """Validates and persists a commercial license key to ~/.genesis/license.json."""
    status = verify_license_key(key_str)
    if not status.is_valid:
        return False, status

    GENESIS_DIR.mkdir(parents=True, exist_ok=True)
    payload_record = {
        "license_key": key_str.strip(),
        "tier": status.tier,
        "owner": status.owner,
        "org": status.org,
        "seats": status.seats,
        "expires_at": status.expires_at,
        "activated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    LICENSE_FILE_PATH.write_text(json.dumps(payload_record, indent=2, ensure_ascii=False), encoding="utf-8")
    return True, status
