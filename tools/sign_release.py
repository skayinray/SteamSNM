"""Sign release assets and verify them with the embedded public key.

Usage:
    python tools/sign_release.py sign FILE [FILE ...]
    python tools/sign_release.py verify FILE FILE.sig

`sign` reads a base64-encoded PKCS8 PEM Ed25519 private key from the
RELEASE_SIGNING_KEY environment variable. For each file it writes FILE.sig
(base64 Ed25519 signature over the raw SHA-256 digest of the file) and a
SHA256SUMS file in the current directory.

`verify` uses only the public key below and exits non-zero if the signature or
the file contents do not match.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ENV_KEY = "RELEASE_SIGNING_KEY"
PUBLIC_KEY_B64 = "L8sewAYfEw4vpC64CR0CPOBQ4YcLgSx3t/LkwL4l7I0="


def _decode_b64(text: str, label: str) -> bytes:
    try:
        return base64.b64decode("".join(text.split()), validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError(f"{label} is not valid base64: {exc}") from exc


def _sha256_digest(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.digest()


def _private_key() -> Ed25519PrivateKey:
    encoded = os.environ.get(ENV_KEY, "").strip()
    if not encoded:
        raise ValueError(f"set the {ENV_KEY} environment variable to the base64 private key")
    key = serialization.load_pem_private_key(_decode_b64(encoded, ENV_KEY), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{ENV_KEY} must contain an Ed25519 private key")
    return key


def _public_key() -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(_decode_b64(PUBLIC_KEY_B64, "public key"))


def sign(paths: list[str]) -> int:
    key = _private_key()
    checksums: dict[str, str] = {}
    for name in paths:
        path = Path(name)
        if not path.is_file():
            raise ValueError(f"file not found: {path}")
        digest = _sha256_digest(path)
        signature = key.sign(digest)
        sig_path = path.with_name(path.name + ".sig")
        sig_path.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")
        if path.name in checksums:
            raise ValueError(f"duplicate file name in SHA256SUMS: {path.name}")
        checksums[path.name] = digest.hex()
        print(f"signed {path} -> {sig_path}")

    lines = [f"{checksums[name]}  {name}\n" for name in sorted(checksums)]
    Path("SHA256SUMS").write_text("".join(lines), encoding="ascii")
    print(f"wrote SHA256SUMS ({len(lines)} entries)")
    return 0


def sign_only(name: str) -> int:
    """Sign one existing file without rewriting SHA256SUMS."""
    key = _private_key()
    path = Path(name)
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    signature = key.sign(_sha256_digest(path))
    sig_path = path.with_name(path.name + ".sig")
    sig_path.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")
    print(f"signed {path} -> {sig_path}")
    return 0


def verify(path_name: str, sig_name: str) -> int:
    path = Path(path_name)
    sig_path = Path(sig_name)
    if not path.is_file():
        raise ValueError(f"file not found: {path}")
    if not sig_path.is_file():
        raise ValueError(f"signature not found: {sig_path}")
    signature = _decode_b64(sig_path.read_text(encoding="ascii"), "signature")
    _public_key().verify(signature, _sha256_digest(path))
    print(f"OK: {path} matches the embedded Ed25519 public key")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sign_parser = commands.add_parser("sign", help="sign files and write SHA256SUMS")
    sign_parser.add_argument("files", nargs="+", help="files to sign")
    only_parser = commands.add_parser("sign-only", help="sign one file without rewriting SHA256SUMS")
    only_parser.add_argument("file", help="file to sign")
    verify_parser = commands.add_parser("verify", help="verify one file against its signature")
    verify_parser.add_argument("file", help="file to verify")
    verify_parser.add_argument("signature", help="base64 .sig file")
    args = parser.parse_args(argv)

    try:
        if args.command == "sign":
            return sign(args.files)
        if args.command == "sign-only":
            return sign_only(args.file)
        return verify(args.file, args.signature)
    except (ValueError, OSError, InvalidSignature) as exc:
        detail = "signature does not match the file or public key" if isinstance(exc, InvalidSignature) else str(exc)
        print(f"error: {detail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
