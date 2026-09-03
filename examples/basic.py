# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "fastapi-webmcp>=0.3.0",
#   "uvicorn>=0.35.0",
# ]
# ///

from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from fastapi_webmcp import FastAPIWebMCP, webmcp_tool

app = FastAPI(title="FastAPI WebMCP example")


class BookCreate(BaseModel):
    title: str = Field(min_length=1, description="Book title")
    author: str = Field(min_length=1, description="Author name")


BOOKS: list[dict[str, object]] = [
    {"id": 1, "title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin"},
]


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index() -> str:
    return HTML


@app.get("/books", operation_id="list_books")
@webmcp_tool(read_only=True, description="List books in the catalog.")
async def list_books(
    query: Annotated[str | None, Query(description="Optional title search")] = None,
) -> list[dict[str, object]]:
    if query is None:
        return BOOKS
    needle = query.casefold()
    return [book for book in BOOKS if needle in str(book["title"]).casefold()]


@app.post("/books", operation_id="create_book", status_code=201)
@webmcp_tool(description="Add a book to the catalog.", untrusted_content=False)
async def create_book(book: BookCreate) -> dict[str, object]:
    created: dict[str, object] = {"id": len(BOOKS) + 1, **book.model_dump()}
    BOOKS.append(created)
    return created


@app.get("/books/{book_id}", operation_id="get_book")
@webmcp_tool(read_only=True, description="Get one book by id.")
async def get_book(
    book_id: Annotated[int, Path(ge=1, description="Book id")],
) -> dict[str, object]:
    book = next((book for book in BOOKS if book["id"] == book_id), None)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


# The HTML page imports the packaged runtime below. Its forms and the WebMCP tools
# deliberately call the same API routes.
FastAPIWebMCP(app).mount(page="/_webmcp")


HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WebMCP Book Catalog</title>
  <style>
    body {
      font: 16px/1.5 system-ui, sans-serif;
      max-width: 48rem;
      margin: 2rem auto;
      padding: 0 1rem;
    }
    form { display: flex; gap: .5rem; flex-wrap: wrap; margin: 1rem 0; }
    input, button { font: inherit; padding: .45rem .6rem; }
    li { margin: .35rem 0; }
    #status { padding: .65rem; background: #eee; }
  </style>
  <script type="module">
    import { registerWebMCP } from "/_webmcp/runtime.js"

    const books = document.querySelector("#books")
    const status = document.querySelector("#status")
    const webmcpStatus = document.querySelector("#webmcp-status")

    function showStatus(message, error = false) {
      status.textContent = message
      status.style.color = error ? "#a00" : "inherit"
    }

    function render(items) {
      books.replaceChildren(...items.map((book) => {
        const item = document.createElement("li")
        const button = document.createElement("button")
        button.type = "button"
        button.textContent = "View"
        button.addEventListener("click", async () => {
          try {
            const selected = await request(`/books/${book.id}`)
            showStatus(`#${selected.id}: ${selected.title} by ${selected.author}`)
          } catch (error) {
            showStatus(error.message, true)
          }
        })
        item.append(`#${book.id} — ${book.title} by ${book.author} `, button)
        return item
      }))
      if (items.length === 0) {
        const item = document.createElement("li")
        item.textContent = "No books found."
        books.append(item)
      }
    }

    async function request(url, options) {
      const response = await fetch(url, options)
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail ?? `HTTP ${response.status}`)
      return data
    }

    async function refresh(query = "") {
      const suffix = query ? `?query=${encodeURIComponent(query)}` : ""
      const items = await request(`/books${suffix}`)
      render(items)
      showStatus(`${items.length} book(s) shown.`)
    }

    document.querySelector("#search").addEventListener("submit", async (event) => {
      event.preventDefault()
      try {
        await refresh(new FormData(event.currentTarget).get("query").trim())
      } catch (error) {
        showStatus(error.message, true)
      }
    })

    document.querySelector("#create").addEventListener("submit", async (event) => {
      event.preventDefault()
      const form = event.currentTarget
      const values = new FormData(form)
      try {
        const created = await request("/books", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: values.get("title"), author: values.get("author") }),
        })
        form.reset()
        await refresh()
        showStatus(`Added “${created.title}”.`)
      } catch (error) {
        showStatus(error.message, true)
      }
    })

    const registration = registerWebMCP({ manifestUrl: "/_webmcp/manifest.json" })
    window.addEventListener("pagehide", () => registration.abort(), { once: true })
    registration.ready.then((result) => {
      webmcpStatus.textContent = result.supported
        ? `${result.tools.length} WebMCP tools registered.`
        : "WebMCP is unavailable in this browser; the human UI still works."
    }).catch((error) => {
      webmcpStatus.textContent = `WebMCP registration failed: ${error.message}`
    })

    refresh().catch((error) => showStatus(error.message, true))
  </script>
</head>
<body>
  <main>
    <h1>Book catalog</h1>
    <p>This UI and browser agents use the same FastAPI operations.</p>
    <p id="webmcp-status">Checking WebMCP support…</p>
    <p id="status" role="status" aria-live="polite">Loading books…</p>

    <form id="search">
      <label>Title <input name="query" placeholder="Search by title"></label>
      <button type="submit">Search</button>
      <button type="button" onclick="this.form.reset(); this.form.requestSubmit()">Show all</button>
    </form>

    <ul id="books"></ul>

    <h2>Add a book</h2>
    <form id="create">
      <label>Title <input name="title" required></label>
      <label>Author <input name="author" required></label>
      <button type="submit">Add</button>
    </form>
  </main>
</body>
</html>
"""


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
