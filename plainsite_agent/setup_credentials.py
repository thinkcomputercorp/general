"""One-time setup: store the PlainSite API key in the vault created by `ant apply`.

Run after `ant apply`, from this directory:

    PLAINSITE_KEY=... python setup_credentials.py

The key is sent once to Anthropic's vault, which is write-only, and is never
written to disk here. Sessions see a placeholder; the real key is substituted
only on requests to www.plainsite.org.
"""

import json
import os
import sys

import anthropic

LOCK_FILE = "claude-lock.json"
VAULT_FILE = "./vaults/plainsite.yaml"


def vault_id_from_lock() -> str:
    with open(LOCK_FILE) as f:
        lock = json.load(f)
    try:
        return lock["resources"][VAULT_FILE]["id"]
    except KeyError:
        sys.exit(f"No {VAULT_FILE} entry in {LOCK_FILE}. Run `ant apply` first.")


def main() -> None:
    key = os.environ.get("PLAINSITE_KEY")
    if not key:
        sys.exit("Set PLAINSITE_KEY to the PlainSite API key first.")

    client = anthropic.Anthropic()
    vault_id = vault_id_from_lock()
    credential = client.beta.vaults.credentials.create(
        vault_id=vault_id,
        display_name="PlainSite API key",
        auth={
            "type": "environment_variable",
            "secret_name": "PLAINSITE_KEY",
            "secret_value": key,
            # Substitute the key only in request headers, and only for PlainSite.
            "injection_location": {"header": True},
            "networking": {
                "type": "limited",
                "allowed_hosts": ["www.plainsite.org", "plainsite.org"],
            },
        },
    )
    print(f"Stored PLAINSITE_KEY in vault {vault_id} (credential {credential.id}).")


if __name__ == "__main__":
    main()
