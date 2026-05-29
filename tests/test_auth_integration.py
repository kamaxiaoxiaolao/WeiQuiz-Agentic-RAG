# tests/test_auth_integration.py
"""Integration tests for auth system (requires running server)."""

import unittest
import requests
import json


BASE_URL = "http://localhost:8000"


class TestAuthIntegration(unittest.TestCase):
    """Integration tests for auth endpoints."""

    def setUp(self):
        self.base_url = BASE_URL
        self.admin_token = None
        self.user_token = None

    def test_01_health_check(self):
        """Test health endpoint is accessible."""
        resp = requests.get(f"{self.base_url}/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_02_register_user(self):
        """Test user registration."""
        resp = requests.post(f"{self.base_url}/auth/register", json={
            "username": "testuser",
            "password": "TestPassword123!",
            "display_name": "Test User",
            "email": "test@example.com"
        })
        self.assertIn(resp.status_code, [200, 409])  # 200 or 409 if exists

    def test_03_login_user(self):
        """Test user login."""
        resp = requests.post(f"{self.base_url}/auth/login", json={
            "username": "testuser",
            "password": "TestPassword123!"
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("access_token", data)
        self.assertEqual(data["token_type"], "bearer")
        self.user_token = data["access_token"]

    def test_04_get_current_user(self):
        """Test get current user with valid token."""
        if not self.user_token:
            self.skipTest("No user token available")

        resp = requests.get(
            f"{self.base_url}/auth/me",
            headers={"Authorization": f"Bearer {self.user_token}"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["username"], "testuser")
        self.assertEqual(data["role"], "user")

    def test_05_access_protected_without_token(self):
        """Test accessing protected endpoint without token."""
        resp = requests.get(f"{self.base_url}/auth/me")
        self.assertEqual(resp.status_code, 401)

    def test_06_access_protected_with_invalid_token(self):
        """Test accessing protected endpoint with invalid token."""
        resp = requests.get(
            f"{self.base_url}/auth/me",
            headers={"Authorization": "Bearer invalid.token.here"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_07_chat_stream_requires_auth(self):
        """Test chat stream requires authentication."""
        resp = requests.post(f"{self.base_url}/chat/stream", json={
            "session_id": "test_session",
            "message": "hello"
        })
        self.assertEqual(resp.status_code, 401)

    def test_08_documents_requires_admin(self):
        """Test documents endpoint requires admin role."""
        if not self.user_token:
            self.skipTest("No user token available")

        resp = requests.get(
            f"{self.base_url}/documents",
            headers={"Authorization": f"Bearer {self.user_token}"}
        )
        self.assertEqual(resp.status_code, 403)

    def test_09_sessions_requires_auth(self):
        """Test sessions endpoint requires authentication."""
        resp = requests.get(f"{self.base_url}/sessions")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
