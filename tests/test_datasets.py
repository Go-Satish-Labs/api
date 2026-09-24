import io

SAMPLE_CSV = b"""date,region,sales,units
2024-01-01,North,1000,10
2024-01-02,South,1500,15
2024-01-03,North,1200,12
2024-01-04,East,900,9
2024-01-05,West,1700,17
2024-01-06,North,1100,11
2024-01-07,South,1600,16
"""


def _upload(client, headers, filename="sales.csv", content=SAMPLE_CSV):
    files = {"file": (filename, io.BytesIO(content), "text/csv")}
    return client.post("/datasets", headers=headers, files=files)


def test_upload_valid_csv_is_profiled_and_analyzed(client, auth_headers):
    resp = _upload(client, auth_headers)
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "analyzed"
    assert body["row_count"] == 7
    assert body["column_count"] == 4


def test_upload_rejects_bad_extension(client, auth_headers):
    files = {"file": ("data.txt", io.BytesIO(b"hello"), "text/plain")}
    resp = client.post("/datasets", headers=auth_headers, files=files)
    assert resp.status_code == 400


def test_upload_rejects_empty_file(client, auth_headers):
    files = {"file": ("empty.csv", io.BytesIO(b""), "text/csv")}
    resp = client.post("/datasets", headers=auth_headers, files=files)
    assert resp.status_code == 400


def test_free_plan_dataset_quota_enforced(client, auth_headers):
    for _ in range(3):  # FREE_MAX_DATASETS default = 3
        resp = _upload(client, auth_headers)
        assert resp.status_code == 201
    resp = _upload(client, auth_headers)
    assert resp.status_code == 403
    assert "limit" in resp.json()["detail"].lower()


def test_dashboard_is_generated_alongside_dataset(client, auth_headers):
    upload_resp = _upload(client, auth_headers)
    dataset_id = upload_resp.json()["id"]
    resp = client.get(f"/dashboards/by-dataset/{dataset_id}", headers=auth_headers)
    assert resp.status_code == 200
    config = resp.json()["config"]
    assert "kpi_cards" in config
    assert "charts" in config


def test_delete_dataset_removes_it(client, auth_headers):
    upload_resp = _upload(client, auth_headers)
    dataset_id = upload_resp.json()["id"]
    del_resp = client.delete(f"/datasets/{dataset_id}", headers=auth_headers)
    assert del_resp.status_code == 204
    get_resp = client.get(f"/datasets/{dataset_id}", headers=auth_headers)
    assert get_resp.status_code == 404


def test_dataset_isolated_per_workspace(client):
    client.post("/auth/register", json={"email": "u1@example.com", "password": "password123"})
    t1 = client.post("/auth/login", data={"username": "u1@example.com", "password": "password123"}).json()["access_token"]
    client.post("/auth/register", json={"email": "u2@example.com", "password": "password123"})
    t2 = client.post("/auth/login", data={"username": "u2@example.com", "password": "password123"}).json()["access_token"]

    h1 = {"Authorization": f"Bearer {t1}"}
    h2 = {"Authorization": f"Bearer {t2}"}

    dataset_id = _upload(client, h1).json()["id"]
    # user 2 must not be able to see user 1's dataset
    resp = client.get(f"/datasets/{dataset_id}", headers=h2)
    assert resp.status_code == 404
