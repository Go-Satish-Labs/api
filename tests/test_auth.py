def test_register_creates_user_and_workspace(client):
    resp = client.post("/auth/register", json={"email": "a@example.com", "password": "password123"})
    assert resp.status_code == 201
    assert "access_token" in resp.json()


def test_register_duplicate_email_rejected(client):
    client.post("/auth/register", json={"email": "dup@example.com", "password": "password123"})
    resp = client.post("/auth/register", json={"email": "dup@example.com", "password": "password123"})
    assert resp.status_code == 409


def test_login_success(client):
    client.post("/auth/register", json={"email": "b@example.com", "password": "password123"})
    resp = client.post("/auth/login", data={"username": "b@example.com", "password": "password123"})
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"


def test_login_wrong_password_rejected(client):
    client.post("/auth/register", json={"email": "c@example.com", "password": "password123"})
    resp = client.post("/auth/login", data={"username": "c@example.com", "password": "wrongpass"})
    assert resp.status_code == 401


def test_me_requires_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code == 401


def test_me_returns_free_plan_by_default(client, auth_headers):
    resp = client.get("/auth/me", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["plan"] == "free"
