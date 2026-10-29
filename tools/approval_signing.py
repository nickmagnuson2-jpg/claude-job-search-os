#!/usr/bin/env python3
"""
approval_signing.py — an approval is a signature made with Nick's Touch ID key.

Before this, `inbox_decision_log.py approve --approver nick` wrote an approval row that any
caller could write, Claude included, so "approved by Nick" was a string, not a fact (S16,
output/codify-ui-102726/02-subtraction.md; round-2 Codex F3: a TTY check authenticates
nobody). Nick approved the design 2026-10-27: "yes re use the touch id key".

Enforcement tier: a check a gate reads. The signature is made with `ssh-keygen -Y sign`
through the Secretive agent, whose key lives in the Secure Enclave and asks for Touch ID on
every use (the same key that signs every push). `inbox_drain_apply.py` verifies each
approval with `ssh-keygen -Y verify` against ALLOWED_SIGNERS and ignores any approval
without a valid signature.

What it does NOT stop: a process that can write ALLOWED_SIGNERS can add a key of its own
and sign with it. The file lives outside the repo, in ~/.ssh, and Nick writes it once (the
`signer-line` command prints the line). A guard on writes to that file is the next tier.

Setup (Nick, once):
  PYTHONIOENCODING=utf-8 python3 tools/approval_signing.py signer-line >> ~/.ssh/allowed_signers
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

NAMESPACE = "inbox-drain-approval"
# Calendar writes from a session need a fresh signature in their own namespace
# (tools/check_calendar_mcp.py). Nick, 2026-10-28: "I want every write to need Touch ID."
# A separate namespace means a signature made for one purpose can never be replayed as the
# other. allowed_signers lists both on Nick's line (signer_line below).
CALENDAR_NAMESPACE = "calendar-write-approval"
NAMESPACES = (NAMESPACE, CALENDAR_NAMESPACE)
PRINCIPAL = "nick"
ALLOWED_SIGNERS = Path.home() / ".ssh" / "allowed_signers"
AGENT_SOCKET = (Path.home() / "Library/Containers/com.maxgoedjen.Secretive.SecretAgent"
                / "Data/socket.ssh")
# The fields a signature covers. Changing any of them after signing breaks the signature.
# block_commands maps each signed block to the exact command it will run ("" for none), so
# a block re-decided after signing with a new command is not covered (788.F2; guard edit
# approved by Nick 2026-10-28). No signed approval existed when it was added.
SIGNED_FIELDS = ("rule_id", "block_ids", "block_commands", "approved_at", "approved_by",
                 "note")


def payload(row: dict) -> bytes:
    """The exact bytes signed and verified for one approval row."""
    return json.dumps({k: row.get(k) for k in SIGNED_FIELDS}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def principal_key(allowed_signers: Path, principal: str = PRINCIPAL) -> str | None:
    """The public key listed for `principal`, as `<type> <base64>`."""
    if not allowed_signers.is_file():
        return None
    for line in allowed_signers.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if line.lstrip().startswith("#") or not parts or principal not in parts[0].split(","):
            continue
        # `principals [options] keytype base64 [comment]`: the key type is the anchor,
        # since both the options and the trailing comment are optional.
        for i, p in enumerate(parts[1:-1], start=1):
            if p.startswith(("ssh-", "ecdsa-", "sk-")):
                return f"{p} {parts[i + 1]}"
    return None


def sign(data: bytes, allowed_signers: Path = ALLOWED_SIGNERS,
         agent_socket: Path = AGENT_SOCKET, key_file: Path | None = None,
         namespace: str = NAMESPACE, timeout: float = 120) -> str:
    """Sign through the agent with the principal's key; Touch ID prompts here.

    key_file is for tests only (a throwaway private key); production signs through the
    agent with the PUBLIC key named in allowed_signers, so no private key is ever on disk.
    """
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ)
        if key_file is None:
            key = principal_key(allowed_signers)
            if key is None:
                raise RuntimeError(f"no key for principal '{PRINCIPAL}' in {allowed_signers}; "
                                   "see the setup line in tools/approval_signing.py")
            key_file = Path(d) / "signer.pub"
            key_file.write_text(key + "\n", encoding="utf-8")
            env["SSH_AUTH_SOCK"] = str(agent_socket)
        msg = Path(d) / "approval"
        msg.write_bytes(data)
        r = subprocess.run(["ssh-keygen", "-Y", "sign", "-n", namespace, "-f", str(key_file),
                            str(msg)], capture_output=True, text=True, env=env, timeout=timeout)
        sig = Path(str(msg) + ".sig")
        if r.returncode != 0 or not sig.is_file():
            raise RuntimeError(f"signing failed (Touch ID declined?): {r.stderr.strip()[:300]}")
        return sig.read_text(encoding="utf-8")


def verify(data: bytes, signature: str | None, allowed_signers: Path = ALLOWED_SIGNERS,
           namespace: str = NAMESPACE) -> bool:
    """True only for a valid signature by PRINCIPAL, in `namespace`, over exactly `data`."""
    if not signature or not allowed_signers.is_file():
        return False
    with tempfile.TemporaryDirectory() as d:
        sig = Path(d) / "approval.sig"
        sig.write_text(signature, encoding="utf-8")
        r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", str(allowed_signers),
                            "-I", PRINCIPAL, "-n", namespace, "-s", str(sig)],
                           input=data, capture_output=True, timeout=30)
        return r.returncode == 0


def signer_line(agent_socket: Path = AGENT_SOCKET) -> str:
    """`nick <key>` for the agent's key. Listing keys needs no Touch ID."""
    r = subprocess.run(["ssh-add", "-L"], capture_output=True, text=True,
                       env={**os.environ, "SSH_AUTH_SOCK": str(agent_socket)}, timeout=30)
    keys = [l for l in r.stdout.splitlines() if l.strip()]
    if r.returncode != 0 or len(keys) != 1:
        raise RuntimeError("the agent must hold exactly one key (see `ssh-add -L`) "
                           + r.stderr.strip()[:200])
    kind, b64 = keys[0].split()[:2]
    return f'{PRINCIPAL} namespaces="{",".join(NAMESPACES)}" {kind} {b64}'


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["signer-line"]:
        print(__doc__.strip().splitlines()[0])
        print("usage: approval_signing.py signer-line")
        return 2
    print(signer_line())
    return 0


if __name__ == "__main__":
    sys.exit(main())
