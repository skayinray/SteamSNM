# Security and release verification

SteamSNM is an independent client. It only reads public Steam Store and Steam Web API endpoints over HTTPS and does not use or scrape SteamDB. TLS verification stays enabled everywhere, including inside the AppImage (a CA bundle is shipped and used via `SSL_CERT_FILE`).

Release assets from this repository are signed with a project Ed25519 key. The raw public key, base64 encoded, is:

```
L8sewAYfEw4vpC64CR0CPOBQ4YcLgSx3t/LkwL4l7I0=
```

Each release includes `SHA256SUMS` and one `<asset>.sig` file per asset. A `.sig` file is base64 text containing an Ed25519 signature over the raw 32-byte SHA-256 digest of the asset. Verify both the signature and the checksum before running a download:

```bash
python3 -m pip install -r requirements-dev.txt
python3 tools/sign_release.py verify SteamSNM-x86_64.AppImage SteamSNM-x86_64.AppImage.sig
sha256sum -c SHA256SUMS
```

On Windows, use `certutil -hashfile SteamSNM.exe SHA256` or another SHA-256 tool and compare the result with `SHA256SUMS`. The Ed25519 verification command is the same, with `SteamSNM.exe` and `SteamSNM.exe.sig`.

The signing key is a GitHub repository secret (`RELEASE_SIGNING_KEY`) and is never included in the source tree. The verifier has the public key embedded in `tools/sign_release.py`; it does not need the secret.

To report a security issue, open a private report through GitHub's repository security advisory form rather than a public issue. Do not include credentials or private keys in reports.

## Platform scope

Linux AppImage and Windows portable EXE release assets are signed as described above. There is no signed or notarized macOS release, and no Authenticode-signed Windows installer: the Windows artifact is a portable EXE. Real code signing requires a certificate we do not have, so this project does not claim it. macOS builds are a documented manual path only.
