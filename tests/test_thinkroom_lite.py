from fastapi.testclient import TestClient

from examples.thinkroom_lite import app


def test_thinkroom_lite_serves_a_framework_free_editor() -> None:
    client = TestClient(app)
    page = client.get("/room?slug=demo&mode=edit")
    assert page.status_code == 200
    assert "Thinkroom Lite" in page.text
    assert '<script type="module" src="./app.js"></script>' in page.text


def test_thinkroom_lite_manifest_is_permission_scoped() -> None:
    client = TestClient(app)

    view = client.get("/_webmcp/manifest.json?slug=demo&mode=view").json()
    assert {tool["name"] for tool in view["tools"]} == {
        "room.guide",
        "room.read_document",
        "room.comment",
    }
    assert view["context"] == {"slug": "demo", "canWrite": False}

    edit = client.get("/_webmcp/manifest.json?slug=demo&mode=edit").json()
    assert {tool["name"] for tool in edit["tools"]} == {
        "room.guide",
        "room.read_document",
        "room.comment",
        "room.replace_content",
    }
    assert edit["context"] == {"slug": "demo", "canWrite": True}

    for tool in edit["tools"]:
        if tool["kind"] == "request":
            assert tool["request"]["boundPathParams"] == {"slug": "demo"}


def test_thinkroom_lite_header_mapping_reaches_fastapi() -> None:
    response = TestClient(app).post(
        "/api/documents/demo/comments",
        headers={"X-Agent-Name": "Scout"},
        json={"body": "A test comment"},
    )
    assert response.status_code == 201
    assert response.json() == {"agent_name": "Scout", "body": "A test comment"}
