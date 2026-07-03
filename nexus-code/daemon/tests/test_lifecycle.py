import os
import json
import psutil
import socket
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

# We need to set the environment variable before importing auth or main
os.environ["NEXUS_DAEMON_SECRET"] = "test-secret-123"

from app.main import app
from app.middleware.auth import _get_shared_secret

client = TestClient(app)

def test_missing_secret_fails():
    response = client.get("/health")
    assert response.status_code == 401
    assert "Invalid or missing X-Nexus-Secret" in response.text

def test_wrong_secret_fails():
    response = client.get("/health", headers={"X-Nexus-Secret": "wrong-secret"})
    assert response.status_code == 401
    assert "Invalid or missing X-Nexus-Secret" in response.text

def test_correct_secret_succeeds():
    response = client.get("/health", headers={"X-Nexus-Secret": "test-secret-123"})
    # It might return 200, or 401 if it further requires Bearer auth,
    # but /health doesn't require Bearer auth (auth_header is None).
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_bearer_auth_passed_to_route():
    # If we pass correct secret but no Bearer token to a route that needs it (e.g., chat)
    response = client.post("/v1/chat", json={}, headers={"X-Nexus-Secret": "test-secret-123"})
    assert response.status_code == 401
    assert "Missing or invalid Authorization header" in response.text
