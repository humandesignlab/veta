"""Tests for the reverse-engineered request signing (veta.auth)."""

from __future__ import annotations

import base64
import datetime

import pytest

from veta import auth


def test_parse_public_key_shape():
    modulus, exponent = auth.parse_public_key()
    # The SPA ships a 4096-bit RSA key with the standard F4 exponent.
    assert modulus.bit_length() == 4096
    assert exponent == 65537


def test_random_xgrc_length_and_alphabet():
    xgrc = auth.random_xgrc()
    assert len(xgrc) == 40
    assert set(xgrc) <= set(auth.XGRC_ALPHABET)


def test_random_xgrc_custom_length():
    assert len(auth.random_xgrc(10)) == 10


def test_format_datetime():
    dt = datetime.datetime(2026, 7, 3, 15, 23, 31)
    assert auth.format_datetime(dt) == "20260703152331"


def test_rsa_output_length_matches_modulus():
    modulus, exponent = auth.parse_public_key()
    k = (modulus.bit_length() + 7) // 8
    ciphertext = auth.rsa_pkcs1v15_encrypt(b"hello", modulus, exponent)
    assert len(ciphertext) == k
    # Ciphertext must be a valid residue below the modulus.
    assert int.from_bytes(ciphertext, "big") < modulus


def test_rsa_rejects_oversized_message():
    modulus, exponent = auth.parse_public_key()
    k = (modulus.bit_length() + 7) // 8
    with pytest.raises(ValueError):
        auth.rsa_pkcs1v15_encrypt(b"x" * k, modulus, exponent)


def test_build_headers_structure():
    dt = datetime.datetime(2026, 7, 3, 15, 23, 31)
    headers = auth.build_headers(dt, action=auth.ACTION_GET_PROCEDIMIENTOS, ip="1.2.3.4")
    assert set(headers) == {"grc", "igrc", "xgrc"}
    assert headers["igrc"] == "1.2.3.4"
    assert len(headers["xgrc"]) == 40
    # grc is base64 and decodes to the RSA block size.
    raw = base64.b64decode(headers["grc"])
    modulus, _ = auth.parse_public_key()
    assert len(raw) == (modulus.bit_length() + 7) // 8


def test_build_headers_are_single_use():
    dt = datetime.datetime(2026, 7, 3, 15, 23, 31)
    first = auth.build_headers(dt)
    second = auth.build_headers(dt)
    # Fresh nonce and randomized padding mean tokens never repeat.
    assert first["xgrc"] != second["xgrc"]
    assert first["grc"] != second["grc"]
