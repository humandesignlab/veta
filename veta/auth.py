"""ComprasMX request signing (reverse-engineered from the SPA).

The public ComprasMX endpoints reject unsigned requests with 401. The Angular
SPA signs every API call with three headers, generated per request:

  grc  = base64(RSA_PKCS1v1_5(publicKey, base64(payload)))
  igrc = client ip (defaults to "127.0.0.1")
  xgrc = a random 40-character nonce, also embedded inside the payload

where payload is a comma-joined list:

  [siteKey, ip, dateTime, xgrc, origin, pathname, action]

  siteKey  the reCAPTCHA site key embedded in the app config
  ip       same value as the igrc header
  dateTime server clock formatted yyyyMMddHHmmss at America/Mexico_City
  xgrc     the same nonce sent in the xgrc header
  origin   https://comprasmx.buengobierno.gob.mx
  pathname /sitiopublico/
  action   an action name, e.g. "GET_PROCEDIMIENTOS"

The token is short-lived and single-use (a captured token cannot be replayed),
so a fresh one is generated for each request. This module has no third party
crypto dependency: RSA PKCS1 v1.5 encryption is done with stdlib big integers.

This mirrors the live SPA as of 2026-07-18. If ComprasMX rotates the public key
or changes the payload layout, update PUBLIC_KEY_PEM and build_payload here.
"""

from __future__ import annotations

import base64
import datetime
import os
import secrets

# Values lifted from the SPA config (qr_recaptcha) and window.location.
SITE_KEY = "6Lfc8UAkAAAAAGT6lZSUNvcZDMd-lmpyj9URluBp"
ORIGIN = "https://comprasmx.buengobierno.gob.mx"
PATHNAME = "/sitiopublico/"
DEFAULT_IP = "127.0.0.1"

# Action names from the SPA enum (kn). Only the ones Veta uses are listed.
ACTION_GET_PROCEDIMIENTOS = "GET_PROCEDIMIENTOS"
ACTION_GET_DETALLE = "GET_DETALLE_PROCEDIMIENTO"
ACTION_GET_REQECONOMICOS = "GET_REQECONOMICOS"
ACTION_DOWNLOAD_FILE = "DOWNLOAD_FILE"

# The xgrc nonce alphabet, exactly as built by the SPA getXGRC function. The
# pipe characters are part of the set (71 characters, indexed 0..70).
XGRC_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ|abcdefghijklmnopqrstuvwxyz|0123456789|-*/_.|"
)

PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEA7MsacN4dweh+KjrU6TWE
3NsV53I+bNFKeGAsWxwOCJ6KIQ5eiwBJlrpIHJcQgXLxw6JmzSj+OfJg4b6pbg2r
XcOniTzkvcUdschy5XArUTTa5+gUICrjHub+dZp2M8sH4XoUOdPcbomxaY7JzTrC
dAGq9NkyVTxRquO/62xetiIVM4X4yb5JQubB7W+kwN++R6EhRWNgBeHau9mcmjIH
IawburlDaKw74YwhpQc/pQRO1M5wm1fbb3awwNn/E747HiNUbxUv+qz9TWRIpzAn
D/hIY7yn/lq13eFtED+ySz3m94SVyjZYCSz+ci/IB3PzisyjOTTZT9z8xLzVLBkl
8g2+i/siSprpx06g06n/s+qVGuhi5m1H1nl6RdVSFZOwfYHQfgomh8tsRylptHz0
5RtUAuM6luuO8LgpagrQQzGGZXHYPnW8aUExEs6x37TntMIAkbb9sE7YlOH8334G
QtlBi2e7gKJhvZcjr/QN/GmB65rpFRUsSSPnhCXW0J1gyJO398lptcJMdyZzdx/R
uYuek5ME0hg2EJ6/brNhV4whcYSo1RM0Lwr5787v0lOGH9URhTvCtTsoQNfLhXJX
pyxwiNUsxv43JMvBCk7ppPduyx0H3N/XWdpFa0y+60SdfccNJLfYTjGFihwIYK67
LMaGjK/5DReRcDKhqdjmsc8CAwEAAQ==
-----END PUBLIC KEY-----"""

# America/Mexico_City is UTC-6 (no DST since 2023).
CDMX_UTC_OFFSET = datetime.timedelta(hours=-6)


def _read_der_length(der: bytes, pos: int) -> tuple[int, int]:
    length = der[pos]
    pos += 1
    if length & 0x80:
        num_bytes = length & 0x7F
        length = int.from_bytes(der[pos:pos + num_bytes], "big")
        pos += num_bytes
    return length, pos


def parse_public_key(pem: str = PUBLIC_KEY_PEM) -> tuple[int, int]:
    """Parse an RSA public key PEM into (modulus, exponent).

    Minimal DER walk of SubjectPublicKeyInfo -> BIT STRING -> RSAPublicKey.
    """
    body = "".join(line for line in pem.splitlines() if "-----" not in line)
    der = base64.b64decode(body)
    pos = 0

    def expect(tag: int) -> int:
        nonlocal pos
        if der[pos] != tag:
            raise ValueError(f"unexpected DER tag {der[pos]:#x}, wanted {tag:#x}")
        pos += 1
        length, new_pos = _read_der_length(der, pos)
        pos = new_pos
        return length

    expect(0x30)                 # SubjectPublicKeyInfo SEQUENCE
    alg_len = expect(0x30)       # AlgorithmIdentifier SEQUENCE
    pos += alg_len               # skip algorithm identifier
    expect(0x03)                 # BIT STRING
    if der[pos] != 0x00:
        raise ValueError("unexpected BIT STRING padding")
    pos += 1                     # unused-bits byte
    expect(0x30)                 # RSAPublicKey SEQUENCE
    n_len = expect(0x02)         # INTEGER modulus
    modulus = int.from_bytes(der[pos:pos + n_len], "big")
    pos += n_len
    e_len = expect(0x02)         # INTEGER exponent
    exponent = int.from_bytes(der[pos:pos + e_len], "big")
    return modulus, exponent


def rsa_pkcs1v15_encrypt(message: bytes, modulus: int, exponent: int) -> bytes:
    """Encrypt with RSAES-PKCS1-v1_5 (the scheme node-forge uses by default)."""
    k = (modulus.bit_length() + 7) // 8
    ps_len = k - len(message) - 3
    if ps_len < 8:
        raise ValueError("message too long for RSA modulus")
    padding = bytearray()
    while len(padding) < ps_len:
        byte = os.urandom(1)[0]
        if byte != 0:
            padding.append(byte)
    encoded = b"\x00\x02" + bytes(padding) + b"\x00" + message
    c = pow(int.from_bytes(encoded, "big"), exponent, modulus)
    return c.to_bytes(k, "big")


def random_xgrc(length: int = 40) -> str:
    """Random nonce over the SPA alphabet (secrets-backed)."""
    return "".join(secrets.choice(XGRC_ALPHABET) for _ in range(length))


def format_datetime(dt_cdmx: datetime.datetime) -> str:
    """Format a Mexico City datetime as yyyyMMddHHmmss.

    The SPA formats with the 12-hour token 'hh', but the server also accepts
    24-hour, so 24-hour is used here for clarity. Both validate.
    """
    return dt_cdmx.strftime("%Y%m%d%H%M%S")


def build_headers(
    server_time_cdmx: datetime.datetime,
    action: str = ACTION_GET_PROCEDIMIENTOS,
    ip: str = DEFAULT_IP,
) -> dict[str, str]:
    """Build the grc/igrc/xgrc headers for one signed request."""
    modulus, exponent = parse_public_key()
    xgrc = random_xgrc()
    date_time = format_datetime(server_time_cdmx)
    payload = ",".join(
        [SITE_KEY, ip, date_time, xgrc, ORIGIN, PATHNAME, action]
    )
    inner = base64.b64encode(payload.encode("utf-8"))
    grc = base64.b64encode(rsa_pkcs1v15_encrypt(inner, modulus, exponent))
    return {"grc": grc.decode("ascii"), "igrc": ip, "xgrc": xgrc}
