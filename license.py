from __future__ import annotations

"""
Nyra License System
-------------------
Offline HMAC-SHA256 signed keys. No internet required on client.
Key format (base64url): <payload_b64>.<sig_b64>
Payload JSON: { "email": "...", "plan": "pro", "exp": 1234567890, "issued": ... }

Key generation (seller side only — run generate_key.py):
  python generate_key.py user@email.com 365
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import DATA_DIR

# ── Secret — change this before shipping, keep it PRIVATE ────────────────────
# This is embedded in the EXE. Use a long random string.
_SECRET = b"nyra-v2-license-secret-k8$Xp2@mQ9wL#nRzT5vY1jB6sD0hF4uC"

LICENSE_FILE = DATA_DIR / "license.key"
TRIAL_FILE   = DATA_DIR / "trial.json"
TRIAL_DAYS   = 14


@dataclass
class LicenseInfo:
    valid: bool
    trial: bool
    days_left: int          # -1 = unlimited (lifetime), 0 = expired
    email: str
    plan: str
    message: str


# ── Core verification ─────────────────────────────────────────────────────────

def _verify_key(key: str) -> Optional[dict]:
    """Return payload dict if key is valid, None otherwise."""
    try:
        parts = key.strip().split(".")
        if len(parts) != 2:
            return None
        payload_b64, sig_b64 = parts

        # Verify HMAC
        expected = hmac.new(_SECRET, payload_b64.encode(), hashlib.sha256).digest()
        given    = base64.urlsafe_b64decode(sig_b64 + "==")
        if not hmac.compare_digest(expected, given):
            return None

        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
        return payload
    except Exception:
        return None


def _trial_info() -> LicenseInfo:
    """Read or create trial record, return trial LicenseInfo."""
    if TRIAL_FILE.exists():
        try:
            data = json.loads(TRIAL_FILE.read_text())
            start = data.get("start", time.time())
        except Exception:
            start = time.time()
    else:
        start = time.time()
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TRIAL_FILE.write_text(json.dumps({"start": start}))

    elapsed_days = (time.time() - start) / 86400
    days_left    = max(0, int(TRIAL_DAYS - elapsed_days))

    if days_left > 0:
        return LicenseInfo(
            valid=True, trial=True, days_left=days_left,
            email="", plan="trial",
            message=f"Trial — {days_left} day{'s' if days_left != 1 else ''} remaining.",
        )
    return LicenseInfo(
        valid=False, trial=True, days_left=0,
        email="", plan="trial",
        message="Trial expired. Please enter a license key.",
    )


# ── Public API ────────────────────────────────────────────────────────────────

def check() -> LicenseInfo:
    """Check license status. Call at startup."""
    if LICENSE_FILE.exists():
        key = LICENSE_FILE.read_text().strip()
        payload = _verify_key(key)
        if payload:
            now = time.time()
            exp = payload.get("exp", 0)
            if exp == 0:  # lifetime
                days_left = -1
            else:
                days_left = max(0, int((exp - now) / 86400))

            if days_left != 0:
                return LicenseInfo(
                    valid=True, trial=False, days_left=days_left,
                    email=payload.get("email", ""),
                    plan=payload.get("plan", "pro"),
                    message="Licensed." if days_left < 0 else f"License valid — {days_left} days left.",
                )
            return LicenseInfo(
                valid=False, trial=False, days_left=0,
                email=payload.get("email", ""),
                plan="expired",
                message="License expired. Please renew at nyraai.com.",
            )
        # Key file exists but invalid
        return LicenseInfo(
            valid=False, trial=False, days_left=0,
            email="", plan="invalid",
            message="Invalid license key.",
        )

    return _trial_info()


def activate(key: str) -> LicenseInfo:
    """Save and verify a license key. Returns updated LicenseInfo."""
    payload = _verify_key(key)
    if not payload:
        return LicenseInfo(
            valid=False, trial=False, days_left=0,
            email="", plan="invalid",
            message="Invalid license key. Please check and try again.",
        )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LICENSE_FILE.write_text(key.strip())
    return check()


def deactivate() -> None:
    """Remove license key (for support/testing)."""
    if LICENSE_FILE.exists():
        LICENSE_FILE.unlink()
