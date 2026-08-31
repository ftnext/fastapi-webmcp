from typing import Annotated

from fastapi import FastAPI, Path, Query
from pydantic import BaseModel, Field

from fastapi_webmcp import FastAPIWebMCP, webmcp_tool

app = FastAPI(title="FastAPI WebMCP example")


class BookCreate(BaseModel):
    title: str = Field(min_length=1, description="Book title")
    author: str = Field(min_length=1, description="Author name")


BOOKS: list[dict[str, object]] = [
    {"id": 1, "title": "The Left Hand of Darkness", "author": "Ursula K. Le Guin"},
]


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
    return next(book for book in BOOKS if book["id"] == book_id)


FastAPIWebMCP(app).mount(page="/agent")
