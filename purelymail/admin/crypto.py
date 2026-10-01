"""Authenticated encryption using only the Python standard library.

No compiled packages are needed, so the app runs on any Python 3.9+ that macOS
has (including the Command Line Tools' Python 3.9 built against LibreSSL).

Construction (encrypt-then-MAC, both keys 256-bit, derived from one key):
  enc_key = HMAC-SHA256(key, "enc"),  mac_key = HMAC-SHA256(key, "mac")
  keystream block i = HMAC-SHA256(enc_key, nonce || i)      (HMAC as a PRF in counter mode)
  ciphertext = plaintext XOR keystream
  tag = HMAC-SHA256(mac_key, len(aad) || aad || nonce || ciphertext)
  blob = VERSION || nonce(16) || ciphertext || tag(32)
The tag is checked in constant time before anything is decrypted.

Password-based keys use PBKDF2-HMAC-SHA256 (hashlib.pbkdf2_hmac).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct

VERSION = b"\x01"
NONCE_LEN = 16
TAG_LEN = 32
KEY_LEN = 32
PBKDF2_ITERATIONS = 600_000


class DecryptionError(Exception):
    """Wrong key, or the data was modified."""


def new_key() -> bytes:
    return os.urandom(KEY_LEN)


def derive_key(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, KEY_LEN)


def _subkeys(key: bytes) -> tuple[bytes, bytes]:
    if len(key) != KEY_LEN:
        raise ValueError("key must be 32 bytes")
    return (hmac.new(key, b"enc", hashlib.sha256).digest(),
            hmac.new(key, b"mac", hashlib.sha256).digest())


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hmac.new(enc_key, nonce + struct.pack(">Q", counter), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def _tag(mac_key: bytes, aad: bytes, nonce: bytes, ciphertext: bytes) -> bytes:
    msg = struct.pack(">Q", len(aad)) + aad + nonce + ciphertext
    return hmac.new(mac_key, VERSION + msg, hashlib.sha256).digest()


def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    enc_key, mac_key = _subkeys(key)
    nonce = os.urandom(NONCE_LEN)
    ks = _keystream(enc_key, nonce, len(plaintext))
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, ks))
    return VERSION + nonce + ciphertext + _tag(mac_key, aad, nonce, ciphertext)


def decrypt(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if len(blob) < 1 + NONCE_LEN + TAG_LEN or blob[:1] != VERSION:
        raise DecryptionError("unrecognised encrypted data")
    enc_key, mac_key = _subkeys(key)
    nonce = blob[1:1 + NONCE_LEN]
    ciphertext = blob[1 + NONCE_LEN:-TAG_LEN]
    tag = blob[-TAG_LEN:]
    if not hmac.compare_digest(tag, _tag(mac_key, aad, nonce, ciphertext)):
        raise DecryptionError("wrong key or modified data")
    ks = _keystream(enc_key, nonce, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, ks))
