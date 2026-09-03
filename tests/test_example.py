from fastapi.testclient import TestClient

from examples.basic import app


def test_basic_example_exposes_three_tools_and_its_catalog_page() -> None:
    client = TestClient(app)

    page = client.get("/")
    assert page.status_code == 200
    assert "Book catalog" in page.text
    assert 'import { registerWebMCP } from "/_webmcp/runtime.js"' in page.text

    manifest = client.get("/_webmcp/manifest.json").json()
    assert {tool["name"] for tool in manifest["tools"]} == {
        "list_books",
        "create_book",
        "get_book",
    }
