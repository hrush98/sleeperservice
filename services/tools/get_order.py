#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from shared.config import settings
from shared.secret_utils import decrypt_age_keyfile
from shared.clob_executor import ClobExecutor


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python services/tools/get_order.py <order_id>")
        return 1

    order_id = sys.argv[1].strip()
    if not order_id:
        print("Missing order_id.")
        return 1

    # Decrypt private key (age will prompt for passphrase)
    private_key = decrypt_age_keyfile(settings.polymarket_keyfile_path)

    executor = ClobExecutor(
        private_key=private_key,
        funder=settings.polymarket_funder_address,
        signature_type=settings.polymarket_signature_type,
        chain_id=settings.polymarket_chain_id,
    )

    result = executor.get_order(order_id)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())