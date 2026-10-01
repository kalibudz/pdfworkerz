"""SIG-06: local self-signed certificate generator (engine.certs)."""

from __future__ import annotations

import base64
import socket
import sys
import time

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_private_key, pkcs12

from engine.certs import MIN_KEY_SIZE, generate_self_signed_cert
from engine.errors import OpValidationError
from engine.ops.certs import GenerateCertificateOp


@pytest.mark.feature("SIG-06", criterion=1)
def test_generates_a_cert_and_key_locally_fast_and_offline() -> None:
    # No real network call is made: generation is pure local computation, so it's
    # fast (no timeout waiting on a socket) and "requests"/network libs are never
    # imported by engine.certs -- confirmed by checking sys.modules below rather
    # than needing a network mock (there's nothing to mock: no socket is ever opened).
    before = time.monotonic()
    result = generate_self_signed_cert("Jane Doe")
    elapsed = time.monotonic() - before
    assert elapsed < 5.0
    assert result.common_name == "Jane Doe"
    assert "engine.certs" in sys.modules
    assert not any(name in sys.modules for name in ("requests", "httpx", "urllib3"))  # not imported transitively


@pytest.mark.feature("SIG-06", criterion=1)
def test_empty_common_name_is_refused() -> None:
    with pytest.raises(OpValidationError):
        generate_self_signed_cert("   ")


@pytest.mark.feature("SIG-06", criterion=2)
def test_key_is_rsa_2048_or_stronger() -> None:
    result = generate_self_signed_cert("Jane Doe")
    key = load_pem_private_key(result.key_pem.encode("ascii"), password=None)
    assert key.key_size >= MIN_KEY_SIZE  # type: ignore[attr-defined]


@pytest.mark.feature("SIG-06", criterion=2)
def test_weaker_than_2048_bits_is_refused() -> None:
    with pytest.raises(OpValidationError):
        generate_self_signed_cert("Jane Doe", key_size=1024)


@pytest.mark.feature("SIG-06", criterion=3)
def test_cert_and_key_are_a_matching_pair_that_can_sign_arbitrary_bytes() -> None:
    """Proves usability for a later SIG-02 signer: the private key really
    matches the public key in the certificate, and can sign/verify."""
    result = generate_self_signed_cert("Jane Doe")
    key = load_pem_private_key(result.key_pem.encode("ascii"), password=None)
    cert = x509.load_pem_x509_certificate(result.cert_pem.encode("ascii"))
    assert cert.public_key().public_numbers() == key.public_key().public_numbers()  # type: ignore[union-attr]

    message = b"a document's bytes, or a hash of them"
    signature = key.sign(message, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr]
    cert.public_key().verify(signature, message, padding.PKCS1v15(), hashes.SHA256())  # type: ignore[union-attr]


@pytest.mark.feature("SIG-06", criterion=3)
def test_pkcs12_bundle_loads_back_as_a_matching_pair() -> None:
    result = generate_self_signed_cert("Jane Doe", passphrase=b"hunter2222")
    priv, cert, cas = pkcs12.load_key_and_certificates(base64.b64decode(result.pkcs12_base64), b"hunter2222")
    assert priv is not None
    assert cert is not None
    assert cas in (None, [])
    assert cert.public_key().public_numbers() == priv.public_key().public_numbers()  # type: ignore[union-attr]


@pytest.mark.feature("SIG-06", criterion=4)
def test_passphrase_protects_both_the_pem_key_and_the_bundle() -> None:
    result = generate_self_signed_cert("Jane Doe", passphrase=b"correct horse battery staple")
    assert result.key_encrypted is True
    assert b"ENCRYPTED" in result.key_pem.encode("ascii")

    # The plain PEM key can't be loaded without the passphrase...
    with pytest.raises(TypeError):
        load_pem_private_key(result.key_pem.encode("ascii"), password=None)
    # ...and the wrong passphrase is rejected, not silently accepted.
    with pytest.raises(ValueError):
        load_pem_private_key(result.key_pem.encode("ascii"), password=b"wrong password")
    # The right one works.
    load_pem_private_key(result.key_pem.encode("ascii"), password=b"correct horse battery staple")

    with pytest.raises(ValueError):
        pkcs12.load_key_and_certificates(base64.b64decode(result.pkcs12_base64), b"wrong password")


@pytest.mark.feature("SIG-06", criterion=4)
def test_no_passphrase_leaves_an_unencrypted_but_explicit_plain_key() -> None:
    result = generate_self_signed_cert("Jane Doe")
    assert result.key_encrypted is False
    assert b"ENCRYPTED" not in result.key_pem.encode("ascii")
    load_pem_private_key(result.key_pem.encode("ascii"), password=None)  # loads with no password needed


@pytest.mark.feature("SIG-06", criterion=4)
def test_empty_passphrase_is_refused_rather_than_silently_meaning_none() -> None:
    with pytest.raises(OpValidationError):
        generate_self_signed_cert("Jane Doe", passphrase=b"")


@pytest.mark.feature("SIG-06", criterion=1)
def test_generate_certificate_op_round_trips_through_parse_op() -> None:
    from engine.ops.base import parse_op

    op = parse_op({"op": "generate_certificate", "common_name": "Jane Doe", "passphrase": "s3cret!!"})
    assert isinstance(op, GenerateCertificateOp)
    result = op.apply(None)
    assert result.common_name == "Jane Doe"
    assert result.key_encrypted is True


def test_no_socket_is_opened_during_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt-and-braces: if a future change accidentally added a network call
    (e.g. an OCSP/CRL fetch or a timestamp authority call), opening a real
    socket here would fail the test immediately."""

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("engine.certs must never open a network socket")

    monkeypatch.setattr(socket, "socket", _refuse)
    generate_self_signed_cert("Jane Doe")
