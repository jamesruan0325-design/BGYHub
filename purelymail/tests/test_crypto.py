"""Tests for the standard-library encryption used for stored passwords."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from admin import crypto  # noqa: E402


class CryptoTests(unittest.TestCase):
    def test_roundtrip_and_randomised(self):
        key = crypto.new_key()
        for msg in (b"", b"x", "密码Pw!@#".encode(), b"a" * 1000):
            a, b = crypto.encrypt(key, msg, b"001@bgyhub.com"), crypto.encrypt(key, msg, b"001@bgyhub.com")
            self.assertNotEqual(a, b)  # random nonce
            self.assertEqual(crypto.decrypt(key, a, b"001@bgyhub.com"), msg)
            if len(msg) > 4:
                self.assertNotIn(msg, a)  # plaintext never appears in the output

    def test_wrong_key_aad_or_tampering_rejected(self):
        key = crypto.new_key()
        blob = crypto.encrypt(key, b"secret password", b"001@bgyhub.com")
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt(crypto.new_key(), blob, b"001@bgyhub.com")
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt(key, blob, b"002@bgyhub.com")  # bound to the mailbox
        for i in (0, 5, 20, len(blob) - 1):
            bad = bytearray(blob)
            bad[i] ^= 1
            with self.assertRaises(crypto.DecryptionError):
                crypto.decrypt(key, bytes(bad), b"001@bgyhub.com")
        with self.assertRaises(crypto.DecryptionError):
            crypto.decrypt(key, blob[:10])

    def test_password_derivation(self):
        salt = b"s" * 16
        self.assertEqual(crypto.derive_key("pw", salt, 1000), crypto.derive_key("pw", salt, 1000))
        self.assertNotEqual(crypto.derive_key("pw", salt, 1000), crypto.derive_key("pw2", salt, 1000))
        self.assertEqual(len(crypto.derive_key("pw", salt, 1000)), 32)


if __name__ == "__main__":
    unittest.main()
