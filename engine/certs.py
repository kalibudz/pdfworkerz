"""SIG-06: a local self-signed X.509 certificate and private key generator.

Pure `cryptography` (no pyHanko, no network calls, no filesystem writes --
callers decide where, if anywhere, the result is persisted; see cli/main.py's
``cert-generate`` command for the one place in this codebase that writes it to
disk). The pair this produces is meant to be usable directly by a later
PAdES-signing feature (SIG-02): it is an ordinary RSA key plus a matching
self-signed end-entity certificate with a digital-signature key usage, the
same shape pyHanko's ``signers.SimpleSigner.load_pkcs12`` or
``load`` (cert + key, separately or as one PKCS#12 bundle) expects.

Binary output travels as base64 text (``pkcs12_base64``), matching this
codebase's convention for binary Op results (e.g. ``engine.images``'
``image_base64``); the PEM fields are themselves ASCII text, so they need no
such encoding.
"""

from __future__ import annotations

import base64
import datetime as _dt

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    NoEncryption,
    PrivateFormat,
    pkcs12,
)
from cryptography.x509.oid import NameOID
from pydantic import BaseModel, ConfigDict

from engine.errors import OpValidationError

MIN_KEY_SIZE = 2048
_PUBLIC_EXPONENT = 65537
_VALID_DAYS = 3650  # 10 years: a locally generated, self-signed cert has no CA revocation path anyway.
_CLOCK_SKEW = _dt.timedelta(days=1)


class CertResult(BaseModel):
    """A freshly generated self-signed certificate and its matching private key.

    Nothing here is written to disk by this module; the caller (CLI command,
    server route) decides where, if anywhere, to persist it, and with what
    file permissions."""

    model_config = ConfigDict(frozen=True)

    common_name: str
    key_size: int
    cert_pem: str
    """The certificate, PEM-encoded (ASCII text)."""
    key_pem: str
    """The private key, PEM-encoded (PKCS#8); encrypted with the given
    passphrase (``ENCRYPTED PRIVATE KEY``) when one was supplied, plain
    (``PRIVATE KEY``) otherwise."""
    key_encrypted: bool
    pkcs12_base64: str
    """A PKCS#12 bundle (cert + key together) as base64 -- the usual way to
    hand both to a signing tool (e.g. pyHanko's ``load_pkcs12``) in one file."""


def generate_self_signed_cert(
    common_name: str, *, key_size: int = MIN_KEY_SIZE, passphrase: bytes | None = None
) -> CertResult:
    """Generate an RSA key pair and a self-signed X.509 certificate for it,
    entirely locally (no network call, no CA, nothing written to disk).

    `key_size` must be 2048 or more (RSA keys weaker than that are refused,
    not silently accepted). `passphrase`, if given, encrypts the private key
    (both the standalone PEM and the PKCS#12 bundle); the caller is strongly
    encouraged to supply one -- see module docstring and SIG-06's acceptance
    criteria: on Windows, a POSIX file-mode bit is not a meaningful
    protection, so a required passphrase is the protection that actually
    travels with the key.
    """
    name = common_name.strip()
    if not name:
        raise OpValidationError("common_name must not be empty")
    if key_size < MIN_KEY_SIZE:
        raise OpValidationError(f"key_size must be at least {MIN_KEY_SIZE} bits (got {key_size})")
    if passphrase is not None and len(passphrase) == 0:
        raise OpValidationError("passphrase must not be empty; omit it entirely for no passphrase")

    key = rsa.generate_private_key(public_exponent=_PUBLIC_EXPONENT, key_size=key_size)  # nosec B505 -- size is checked above
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = _dt.datetime.now(_dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _CLOCK_SKEW)
        .not_valid_after(now + _dt.timedelta(days=_VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            # digital_signature + content_commitment (non-repudiation): the pair a
            # document-signing certificate needs; everything else this key is not for.
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_encryption = BestAvailableEncryption(passphrase) if passphrase else NoEncryption()
    key_pem = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, key_encryption).decode("ascii")
    cert_pem = cert.public_bytes(Encoding.PEM).decode("ascii")
    p12_bytes = pkcs12.serialize_key_and_certificates(
        name=name.encode("utf-8"), key=key, cert=cert, cas=None, encryption_algorithm=key_encryption
    )

    return CertResult(
        common_name=name,
        key_size=key_size,
        cert_pem=cert_pem,
        key_pem=key_pem,
        key_encrypted=passphrase is not None,
        pkcs12_base64=base64.b64encode(p12_bytes).decode("ascii"),
    )
