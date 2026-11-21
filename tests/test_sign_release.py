import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools import sign_release


def test_sign_and_verify_roundtrip(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    monkeypatch.setenv("RELEASE_SIGNING_KEY", base64.b64encode(pem).decode("ascii"))
    monkeypatch.setattr(
        sign_release, "PUBLIC_KEY_B64", base64.b64encode(public_raw).decode("ascii")
    )
    monkeypatch.chdir(tmp_path)

    asset = tmp_path / "asset.bin"
    asset.write_bytes(b"release payload")
    assert sign_release.sign([str(asset)]) == 0
    assert (tmp_path / "asset.bin.sig").is_file()
    assert "asset.bin" in (tmp_path / "SHA256SUMS").read_text(encoding="ascii")
    assert sign_release.verify(str(asset), str(tmp_path / "asset.bin.sig")) == 0


def test_verify_rejects_modified_file(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    monkeypatch.setenv("RELEASE_SIGNING_KEY", base64.b64encode(pem).decode("ascii"))
    monkeypatch.setattr(
        sign_release, "PUBLIC_KEY_B64", base64.b64encode(public_raw).decode("ascii")
    )
    monkeypatch.chdir(tmp_path)

    asset = tmp_path / "asset.bin"
    asset.write_bytes(b"original")
    sign_release.sign([str(asset)])
    asset.write_bytes(b"tampered")
    assert sign_release.main(["verify", str(asset), str(tmp_path / "asset.bin.sig")]) == 1
