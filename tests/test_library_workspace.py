"""Workspace interactions use isolated users and the test suite's temporary database."""
import uuid

import pytest

from app.auth import mint_user_token
from app.main import app
from tests.conftest import RawTestClient

BASE = "/api/arrangements/library"


@pytest.fixture
def workspace_client():
    owner = "workspace-" + uuid.uuid4().hex
    with RawTestClient(app, headers={"X-User-Token": mint_user_token(owner)}) as client:
        yield client


def data(response):
    assert response.status_code == 200, response.text
    return response.json()["data"]


def state(client):
    return data(client.get(BASE + "/workspace"))


def change(client, action, **values):
    return data(client.post(BASE + "/workspace", json={
        "version": state(client)["version"], "action": action, "confirmed": True, **values,
    }))


def source(client, title="自检文档"):
    return data(client.post("/api/arrangements/sources", json={
        "title": title, "content": "我负责整理资料。", "kind": "document_text",
    }))


def test_folders_stars_and_search_survive_reload(workspace_client):
    client = workspace_client
    document = source(client)
    root = change(client, "create_folder", name="研究")["folders"][0]
    change(client, "move_source", source_id=document["id"], folder_id=root["id"])
    change(client, "star_source", source_id=document["id"], starred=True)
    saved = state(client)
    assert saved["entries"][document["id"]] == {"folder_id": root["id"], "starred": True}
    assert data(client.get(BASE + "/search", params={"folder_id": root["id"], "starred": True}))["total"] == 1
    assert data(client.get(BASE + "/search", params={"folder_id": "unsorted"}))["total"] == 0
    change(client, "star_source", source_id=document["id"], starred=False)
    assert data(client.get(BASE + "/search", params={"starred": True}))["total"] == 0
    assert data(client.get(f"/api/arrangements/sources/{document['id']}"))["content"] == document["content"]


def test_cycles_and_stale_versions_are_rejected_atomically(workspace_client):
    client = workspace_client
    parent = change(client, "create_folder", name="父目录")["folders"][0]
    before = state(client)
    child = change(client, "create_folder", name="子目录", parent_id=parent["id"])["folders"][-1]
    stale = client.post(BASE + "/workspace", json={"action": "rename_folder", "folder_id": parent["id"],
        "name": "过期覆盖", "version": before["version"], "confirmed": True})
    assert stale.status_code == 409
    current = state(client)
    invalid = client.post(BASE + "/workspace", json={"action": "move_folder", "folder_id": parent["id"],
        "parent_id": child["id"], "version": current["version"], "confirmed": True})
    assert invalid.status_code == 422
    assert state(client) == current


def test_folder_removal_preserves_documents_and_children(workspace_client):
    client = workspace_client
    document = source(client)
    parent = change(client, "create_folder", name="父目录")["folders"][0]
    child = change(client, "create_folder", name="子目录", parent_id=parent["id"])["folders"][-1]
    change(client, "move_source", source_id=document["id"], folder_id=parent["id"])
    change(client, "star_source", source_id=document["id"], starred=True)
    result = change(client, "remove_folder", folder_id=parent["id"])
    assert result["folders"] == [{**child, "parent_id": ""}]
    assert result["entries"][document["id"]] == {"folder_id": "", "starred": True}
    assert data(client.get(BASE + "/search", params={"folder_id": "unsorted"}))["total"] == 1
    assert data(client.get(f"/api/arrangements/sources/{document['id']}"))["title"] == document["title"]


def test_workspace_identity_cannot_be_forged(workspace_client):
    client = workspace_client
    document = source(client)
    folder = change(client, "create_folder", name="私人目录")["folders"][0]
    before = state(client)
    with RawTestClient(app, headers={"X-User-Token": mint_user_token("other-" + uuid.uuid4().hex)}) as other:
        assert state(other) == {"version": 0, "folders": [], "entries": {}}
        for action in [
            {"action": "move_source", "source_id": document["id"], "folder_id": ""},
            {"action": "rename_folder", "folder_id": folder["id"], "name": "伪造修改"},
        ]:
            response = other.post(BASE + "/workspace", json={"version": 0, "confirmed": True, **action})
            assert response.status_code == 404
        assert other.get(BASE + "/search", params={"folder_id": folder["id"]}).status_code == 404
    assert state(client) == before


def test_same_name_folder_destinations_and_sorting(workspace_client):
    client = workspace_client
    for title in ["B 文档", "A 文档"]:
        source(client, title)
    result = data(client.get(BASE + "/search", params={"sort": "title"}))
    assert [item["title"] for item in result["items"]] == ["A 文档", "B 文档"]
    first = change(client, "create_folder", name="研究")["folders"][0]
    second = change(client, "create_folder", name="生活")["folders"][-1]
    change(client, "create_folder", name="笔记", parent_id=first["id"])
    change(client, "create_folder", name="笔记", parent_id=second["id"])
    assert len(state(client)["folders"]) == 4
