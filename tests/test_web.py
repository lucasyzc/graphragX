def test_chat_home_page_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Smart Chat" in resp.text


def test_search_center_page_renders(client):
    resp = client.get("/search")
    assert resp.status_code == 200
    assert "检索中心" in resp.text


def test_impact_page_renders(client):
    resp = client.get("/scenarios/impact")
    assert resp.status_code == 200
    assert "变更影响分析" in resp.text


def test_manage_pages_render(client):
    projects_resp = client.get("/manage/projects")
    sync_resp = client.get("/manage/sync")
    knowledge_resp = client.get("/manage/knowledge")

    assert projects_resp.status_code == 200
    assert sync_resp.status_code == 200
    assert knowledge_resp.status_code == 200
    assert "项目管理" in projects_resp.text
    assert "同步管理" in sync_resp.text
    assert "知识库管理" in knowledge_resp.text


def test_favicon_request_does_not_404(client):
    resp = client.get("/favicon.ico")
    assert resp.status_code == 204
