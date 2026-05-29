# tests/test_auth.py
"""Tests for authentication and authorization."""

import unittest
from unittest.mock import patch, MagicMock

from app.auth.security import hash_password, verify_password, create_access_token, decode_access_token


class TestPasswordHashing(unittest.TestCase):
    """Test password hashing and verification."""

    def test_hash_password_returns_valid_format(self):
        password = "TestPassword123!"
        result = hash_password(password)
        self.assertTrue(result.startswith("pbkdf2_sha256$"))
        parts = result.split("$")
        self.assertEqual(len(parts), 4)
        self.assertEqual(parts[0], "pbkdf2_sha256")
        self.assertGreater(int(parts[1]), 0)  # iterations
        self.assertGreater(len(parts[2]), 0)  # salt
        self.assertGreater(len(parts[3]), 0)  # hash

    def test_verify_password_correct(self):
        password = "TestPassword123!"
        password_hash = hash_password(password)
        self.assertTrue(verify_password(password, password_hash))

    def test_verify_password_incorrect(self):
        password = "TestPassword123!"
        password_hash = hash_password(password)
        self.assertFalse(verify_password("WrongPassword", password_hash))

    def test_verify_password_invalid_format(self):
        self.assertFalse(verify_password("test", "invalid_hash"))
        self.assertFalse(verify_password("test", ""))

    def test_different_passwords_different_hashes(self):
        hash1 = hash_password("password1")
        hash2 = hash_password("password2")
        self.assertNotEqual(hash1, hash2)

    def test_same_password_different_salts(self):
        hash1 = hash_password("same_password")
        hash2 = hash_password("same_password")
        self.assertNotEqual(hash1, hash2)  # Different salts


class TestJWTToken(unittest.TestCase):
    """Test JWT token creation and decoding."""

    def test_create_and_decode_token(self):
        user_id = "usr_test123"
        role = "user"
        token = create_access_token(user_id, role)
        payload = decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["user_id"], user_id)
        self.assertEqual(payload["sub"], user_id)
        self.assertEqual(payload["role"], role)
        self.assertIn("jti", payload)
        self.assertIn("iat", payload)
        self.assertIn("exp", payload)

    def test_decode_invalid_token(self):
        payload = decode_access_token("invalid.token.here")
        self.assertIsNone(payload)

    def test_decode_expired_token(self):
        import time
        user_id = "usr_test"
        token = create_access_token(user_id, "user", expires_delta=-1)
        payload = decode_access_token(token)
        self.assertIsNone(payload)

    def test_token_contains_required_claims(self):
        token = create_access_token("usr_123", "admin")
        payload = decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertIn("sub", payload)
        self.assertIn("user_id", payload)
        self.assertIn("role", payload)
        self.assertIn("jti", payload)
        self.assertIn("iat", payload)
        self.assertIn("exp", payload)

    def test_admin_token(self):
        token = create_access_token("usr_admin", "admin")
        payload = decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["role"], "admin")

    def test_user_token(self):
        token = create_access_token("usr_user", "user")
        payload = decode_access_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["role"], "user")


if __name__ == "__main__":
    unittest.main()
