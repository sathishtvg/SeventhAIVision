async def test_version_endpoint(app_client):
    resp = await app_client.get("/api/v1/system/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"]
    assert "git_sha" in body
    assert "build_date" in body
