def test_query_endpoint_returns_response_shape(client):
    project_resp = client.post(
        "/projects",
        headers={"X-User": "alice", "X-Role": "admin"},
        json={
            "name": "demo-repo",
            "scm_provider": "github",
            "repo_url": "https://github.com/acme/demo-repo",
            "default_branch": "main",
        },
    )
    project = project_resp.json()

    query_resp = client.post(
        "/query",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"project_id": project["id"], "question": "where is retry logic?", "top_k": 5},
    )

    assert query_resp.status_code == 200
    payload = query_resp.json()
    assert "answer" in payload
    assert "sources" in payload
    assert "contexts" in payload
    assert "citations" in payload
    assert "retrieval_meta" in payload
    assert isinstance(payload["sources"], list)
    assert isinstance(payload["citations"], list)
    assert "keyword_hits" in payload["retrieval_meta"]
    assert "evidence_coverage" in payload["retrieval_meta"]
