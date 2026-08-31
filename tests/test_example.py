from fastapi.testclient import TestClient

from examples.basic import app


def test_basic_example_exposes_three_tools_and_its_plain_html_page() -> None:
    client = TestClient(app)

    page = client.get("/agent")
    assert page.status_code == 200
    assert "FastAPI WebMCP" in page.text

    manifest = client.get("/agent/manifest.json").json()
    assert {tool["name"] for tool in manifest["tools"]} == {
        "list_books",
        "create_book",
        "get_book",
    }
