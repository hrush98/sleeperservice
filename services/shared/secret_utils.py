"""
Secret helpers for live trading.
"""

from __future__ import annotations

import subprocess
import os
from pathlib import Path


def decrypt_age_keyfile(
    keyfile_path: str,
    *,
    prompt: str = "Enter passphrase: ",
) -> str:
    """Decrypt an age-encrypted keyfile and return the plaintext string."""
    resolved = Path(os.path.expanduser(keyfile_path)).resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Keyfile not found: {resolved}")

    try:
        result = subprocess.run(
            ["age", "--decrypt", "-o", "-", str(resolved)],
            check=True,
            text=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        message = stderr or "age decryption failed"
        raise RuntimeError(message) from exc

    plaintext = result.stdout.strip()
    if not plaintext.startswith("0x"):
        raise ValueError("Decrypted key does not look like a hex private key.")
    return plaintext
