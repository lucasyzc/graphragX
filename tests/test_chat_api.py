def _create_project(client, user: str, name: str) -> dict:
    resp = client.post(
        "/projects",
        headers={"X-User": user, "X-Role": "admin"},
        json={
            "name": name,
            "scm_provider": "github",
            "repo_url": f"https://github.com/acme/{name}",
            "default_branch": "main",
        },
    )
    assert resp.status_code == 200
    return resp.json()


def test_chat_session_crud(client):
    project = _create_project(client, user="alice", name="chat-session-demo")

    create_resp = client.post(
        "/chat/sessions",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"title": "我的会话", "default_project_id": project["id"]},
    )
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["title"] == "我的会话"
    assert created["default_project_id"] == project["id"]
    assert created["project_id"] == project["id"]
    assert created["owner_user_id"] == "alice"

    list_resp = client.get(
        f"/chat/sessions?limit=20&offset=0&project_id={project['id']}",
        headers={"X-User": "alice", "X-Role": "viewer"},
    )
    assert list_resp.status_code == 200
    list_payload = list_resp.json()
    assert list_payload["total"] == 1
    assert list_payload["items"][0]["id"] == created["id"]

    patch_resp = client.patch(
        f"/chat/sessions/{created['id']}",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"title": "重命名会话", "archived": True},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["title"] == "重命名会话"
    assert patched["archived"] is True

    bob_list_resp = client.get("/chat/sessions?limit=20&offset=0", headers={"X-User": "bob", "X-Role": "viewer"})
    assert bob_list_resp.status_code == 200
    assert bob_list_resp.json()["total"] == 0


def test_chat_turn_persists_messages_and_keeps_default_project(client):
    project_a = _create_project(client, user="alice", name="chat-project-a")
    project_b = _create_project(client, user="alice", name="chat-project-b")

    session_resp = client.post(
        "/chat/sessions",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"title": "project-demo", "default_project_id": project_a["id"]},
    )
    assert session_resp.status_code == 200
    session = session_resp.json()

    turn_resp = client.post(
        f"/chat/sessions/{session['id']}/messages",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={
            "content": "where is retry logic?",
            "project_id_override": project_b["id"],
            "top_k": 5,
            "knowledge_scope": "auto",
            "need_citations": True,
        },
    )
    assert turn_resp.status_code == 200
    turn_payload = turn_resp.json()

    assert turn_payload["user_message"]["role"] == "user"
    assert turn_payload["assistant_message"]["role"] == "assistant"
    assert turn_payload["user_message"]["effective_project_id"] == project_a["id"]
    assert turn_payload["assistant_message"]["effective_project_id"] == project_a["id"]
    assert "retrieval_meta" in turn_payload
    assert isinstance(turn_payload["sources"], list)
    assert isinstance(turn_payload["citations"], list)
    assert isinstance(turn_payload.get("deprecation_warnings"), list)
    assert any("project_id_override" in item for item in turn_payload["deprecation_warnings"])

    messages_resp = client.get(
        f"/chat/sessions/{session['id']}/messages?limit=20",
        headers={"X-User": "alice", "X-Role": "viewer"},
    )
    assert messages_resp.status_code == 200
    messages_payload = messages_resp.json()
    assert messages_payload["total"] == 2
    assert [item["role"] for item in messages_payload["items"]] == ["user", "assistant"]

    sessions_resp = client.get("/chat/sessions?limit=20&offset=0", headers={"X-User": "alice", "X-Role": "viewer"})
    assert sessions_resp.status_code == 200
    refreshed_session = sessions_resp.json()["items"][0]
    assert refreshed_session["default_project_id"] == project_a["id"]


def test_create_chat_session_requires_project(client):
    session_resp = client.post(
        "/chat/sessions",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"title": "no-project"},
    )
    assert session_resp.status_code == 422


def test_chat_turn_returns_403_when_override_project_has_no_access(client):
    alice_project = _create_project(client, user="alice", name="alice-owned-project")
    bob_project = _create_project(client, user="bob", name="bob-owned-project")

    session_resp = client.post(
        "/chat/sessions",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"default_project_id": alice_project["id"]},
    )
    assert session_resp.status_code == 200
    session = session_resp.json()

    forbidden_resp = client.post(
        f"/chat/sessions/{session['id']}/messages",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={
            "content": "where is payment runbook?",
            "project_id_override": bob_project["id"],
        },
    )
    assert forbidden_resp.status_code == 200
    payload = forbidden_resp.json()
    assert payload["user_message"]["effective_project_id"] == alice_project["id"]
    assert any("ignored" in item for item in payload.get("deprecation_warnings", []))


def test_chat_session_can_move_to_another_project(client):
    project_a = _create_project(client, user="alice", name="chat-move-project-a")
    project_b = _create_project(client, user="alice", name="chat-move-project-b")

    session_resp = client.post(
        "/chat/sessions",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"default_project_id": project_a["id"]},
    )
    assert session_resp.status_code == 200
    session = session_resp.json()

    move_resp = client.patch(
        f"/chat/sessions/{session['id']}",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"default_project_id": project_b["id"]},
    )
    assert move_resp.status_code == 200
    assert move_resp.json()["default_project_id"] == project_b["id"]

    turn_resp = client.post(
        f"/chat/sessions/{session['id']}/messages",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"content": "after move"},
    )
    assert turn_resp.status_code == 200
    assert turn_resp.json()["assistant_message"]["effective_project_id"] == project_b["id"]


def test_list_chat_sessions_filters_by_project(client):
    project_a = _create_project(client, user="alice", name="chat-filter-project-a")
    project_b = _create_project(client, user="alice", name="chat-filter-project-b")

    session_a = client.post(
        "/chat/sessions",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"title": "a", "default_project_id": project_a["id"]},
    )
    assert session_a.status_code == 200
    session_b = client.post(
        "/chat/sessions",
        headers={"X-User": "alice", "X-Role": "viewer"},
        json={"title": "b", "default_project_id": project_b["id"]},
    )
    assert session_b.status_code == 200

    filtered = client.get(
        f"/chat/sessions?limit=20&offset=0&project_id={project_a['id']}",
        headers={"X-User": "alice", "X-Role": "viewer"},
    )
    assert filtered.status_code == 200
    payload = filtered.json()
    assert payload["total"] == 1
    assert payload["items"][0]["default_project_id"] == project_a["id"]
