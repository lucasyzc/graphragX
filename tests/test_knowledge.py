import time


def _wait_knowledge_job_done(client, headers, timeout_sec: float = 10.0):
    started = time.time()
    while time.time() - started < timeout_sec:
        resp = client.get("/knowledge/jobs?limit=20", headers=headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        if items and items[0]["status"] in {"done", "failed"}:
            return items[0]
        time.sleep(0.1)
    raise AssertionError("knowledge job did not finish in time")


def test_knowledge_source_sync_and_query(client, tmp_path):
    headers = {"X-User": "alice", "X-Role": "admin"}
    docs_dir = tmp_path / "kb"
    docs_dir.mkdir()
    (docs_dir / "runbook.md").write_text(
        "# Cache Runbook\n\n"
        "Step 1: restart cache worker.\n"
        "Step 2: clear redis stale keys.\n"
        "Step 3: verify health endpoint.\n",
        encoding="utf-8",
    )

    project_resp = client.post(
        "/projects",
        headers=headers,
        json={
            "name": "kb-demo",
            "scm_provider": "local",
            "repo_url": str(tmp_path),
            "default_branch": "",
        },
    )
    assert project_resp.status_code == 200
    project = project_resp.json()

    source_resp = client.post(
        "/knowledge/sources",
        headers=headers,
        json={
            "project_id": project["id"],
            "name": "runbook-source",
            "source_type": "local_dir",
            "source_uri": str(docs_dir),
            "tags": ["ops", "runbook"],
            "enabled": True,
        },
    )
    assert source_resp.status_code == 200
    source = source_resp.json()
    assert source["project_id"] == project["id"]
    assert source["tags"] == ["ops", "runbook"]

    sync_resp = client.post(
        f"/knowledge/sources/{source['id']}/sync",
        headers=headers,
        json={"mode": "incremental"},
    )
    assert sync_resp.status_code == 200
    job = _wait_knowledge_job_done(client=client, headers=headers)
    assert job["status"] == "done"
    assert job["indexed_count"] >= 1

    list_source_resp = client.get(f"/knowledge/sources?project_id={project['id']}", headers=headers)
    assert list_source_resp.status_code == 200
    assert any(item["id"] == source["id"] for item in list_source_resp.json())

    query_resp = client.post(
        "/query",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={
            "project_id": project["id"],
            "question": "cache runbook step restart worker",
            "knowledge_scope": "knowledge",
            "source_types": ["doc"],
            "need_citations": True,
            "filters": {"tags": ["ops"]},
        },
    )
    assert query_resp.status_code == 200
    payload = query_resp.json()
    assert payload["contexts"]
    assert payload["citations"]
    assert payload["retrieval_meta"]["keyword_hits"] >= 1


def test_knowledge_sync_parses_generic_json_and_jsonl_records(client, tmp_path):
    headers = {"X-User": "alice", "X-Role": "admin"}
    docs_dir = tmp_path / "kb_json"
    docs_dir.mkdir()
    (docs_dir / "articles.jsonl").write_text(
        '{"uuid":"a-1","headline":"Event details and RSVP","payload":{"text":"Guests can RSVP on registration form page"}}\n'
        '{"record_id":"b-2","name":"Menu alignment request","details":{"note":"not possible to change alignment right to left"}}\n',
        encoding="utf-8",
    )
    (docs_dir / "help.json").write_text(
        '[{"topic":"Billing history","body":"You can view invoice from billing history","url":"https://example.com/invoice"}]',
        encoding="utf-8",
    )

    project_resp = client.post(
        "/projects",
        headers=headers,
        json={
            "name": "kb-json-demo",
            "scm_provider": "local",
            "repo_url": str(tmp_path),
            "default_branch": "",
        },
    )
    assert project_resp.status_code == 200
    project = project_resp.json()

    source_resp = client.post(
        "/knowledge/sources",
        headers=headers,
        json={
            "project_id": project["id"],
            "name": "json-source",
            "source_type": "local_dir",
            "source_uri": str(docs_dir),
            "tags": ["support"],
            "enabled": True,
        },
    )
    assert source_resp.status_code == 200
    source = source_resp.json()

    sync_resp = client.post(
        f"/knowledge/sources/{source['id']}/sync",
        headers=headers,
        json={"mode": "incremental"},
    )
    assert sync_resp.status_code == 200
    job = _wait_knowledge_job_done(client=client, headers=headers)
    assert job["status"] == "done"
    assert job["indexed_count"] >= 2

    align_query_resp = client.post(
        "/query",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={
            "project_id": project["id"],
            "question": "is it possible to change alignment right to left",
            "knowledge_scope": "knowledge",
            "source_types": ["doc"],
            "need_citations": True,
        },
    )
    assert align_query_resp.status_code == 200
    align_payload = align_query_resp.json()
    assert align_payload["retrieval_meta"]["keyword_hits"] >= 1
    assert any("#id=b-2" in (item.get("source_uri") or "") for item in align_payload["citations"])

    invoice_query_resp = client.post(
        "/query",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={
            "project_id": project["id"],
            "question": "where can I view invoice billing history",
            "knowledge_scope": "knowledge",
            "source_types": ["doc"],
            "need_citations": True,
        },
    )
    assert invoice_query_resp.status_code == 200
    invoice_payload = invoice_query_resp.json()
    assert any("example.com/invoice" in (item.get("source_uri") or "") for item in invoice_payload["citations"])
