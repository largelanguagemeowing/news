"""Tests for the Next.js RSC flight extractor.

The module is pure, so tests cross the same seam the caller does:
``extract_next_flight_content(html)`` with inline fixture pages. Fixtures
mirror what App Router pages actually embed: inline scripts pushing
JSON-string-escaped "hexId:payload" fragments.
"""

from __future__ import annotations

import json

from app.jobs.next_flight import extract_next_flight_content


def flight_page(chunks: list[str], title: str = "Docs | Example") -> str:
    """Build a fake App Router page from raw chunk strings ("23:[...]")."""
    scripts = "\n".join(
        f"<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>"
        for chunk in chunks
    )
    return (
        f"<!DOCTYPE html><html><head><title>{title}</title></head>"
        f"<body>{scripts}</body></html>"
    )


def chunk(cid: str, node) -> str:
    """Flight chunk: "hexId:" + JSON of the element tree."""
    return f"{cid}:{json.dumps(node)}"


def el(tag: str, props: dict) -> list:
    return ["$", tag, None, props]


def children(*nodes) -> list:
    return list(nodes)


LONG_TEXT = (
    "Run the installer and follow the prompts. The installer detects your "
    "platform automatically, downloads the matching binary, and verifies its "
    "checksum before placing it on your PATH."
)


def test_walks_main_chunk_tree_to_markdown() -> None:
    main = chunk(
        "23",
        el(
            "div",
            {
                "children": children(
                    el("h1", {"children": "Install Guide"}),
                    el("p", {"children": LONG_TEXT}),
                    el(
                        "ul",
                        {
                            "children": children(
                                el("li", {"children": "first step"}),
                                el("li", {"children": "second step"}),
                            )
                        },
                    ),
                )
            },
        ),
    )
    out = extract_next_flight_content(flight_page([main]))
    assert out is not None, "should extract content"
    title, content = out
    # Title splitting keeps the page name from "Page | Site" titles
    assert title == "Docs"
    assert content.startswith("# Install Guide")
    assert "Run the installer and follow the prompts." in content
    assert "- first step\n- second step" in content


def test_resolves_dollar_l_refs_across_chunks() -> None:
    chunks = [
        chunk("23", el("div", {"children": "$L2"})),
        chunk(
            "2",
            el(
                "article",
                {
                    "children": children(
                        el("h2", {"children": "Cross-chunk section"}),
                        el(
                            "p",
                            {
                                "children": (
                                    "Body text from the referenced chunk. This "
                                    "paragraph lives in a different payload row "
                                    "than the main layout chunk, which is exactly "
                                    "the shape streamed pages produce."
                                )
                            },
                        ),
                    )
                },
            ),
        ),
    ]
    out = extract_next_flight_content(flight_page(chunks))
    assert out is not None
    _title, content = out
    assert "## Cross-chunk section" in content
    assert "Body text from the referenced chunk." in content


def test_renders_table_with_header_separator() -> None:
    table = el(
        "table",
        {
            "children": children(
                el(
                    "thead",
                    {
                        "children": el(
                            "tr",
                            {
                                "children": children(
                                    el("th", {"children": "Name"}),
                                    el("th", {"children": "Size"}),
                                )
                            },
                        ),
                    },
                ),
                el(
                    "tbody",
                    {
                        "children": el(
                            "tr",
                            {
                                "children": children(
                                    el("td", {"children": "alpha"}),
                                    el(
                                        "td",
                                        {
                                            "children": (
                                                "3 MB, compressed to 1.1 MB on "
                                                "disk after the build step finishes"
                                            )
                                        },
                                    ),
                                )
                            },
                        ),
                    },
                ),
            )
        },
    )
    main = chunk("23", el("div", {"children": table}))
    out = extract_next_flight_content(flight_page([main]))
    assert out is not None
    _title, content = out
    assert "| Name | Size |" in content
    assert "| --- | --- |" in content
    assert "| alpha | 3 MB, compressed" in content


def test_skips_non_content_tags_and_renders_code_and_links() -> None:
    filler = (
        " today. The rest of this paragraph exists so the rendered markdown "
        "clears the extractor's minimum content length without changing what "
        "is being asserted."
    )
    main = chunk(
        "23",
        el(
            "div",
            {
                "children": children(
                    el("nav", {"children": "Home About Contact"}),
                    el(
                        "p",
                        {
                            "children": children(
                                "See ",
                                el(
                                    "a",
                                    {
                                        "href": "https://example.com/docs",
                                        "children": "the docs",
                                    },
                                ),
                                " and ",
                                el("code", {"children": "npm i x"}),
                                filler,
                            )
                        },
                    ),
                )
            },
        ),
    )
    out = extract_next_flight_content(flight_page([main]))
    assert out is not None
    _title, content = out
    assert "Home About Contact" not in content
    assert "[the docs](https://example.com/docs)" in content
    assert "`npm i x`" in content


def test_cycle_safe_on_self_referencing_chunks() -> None:
    self_ref = chunk("5", el("p", {"children": "$L5"}))
    main = chunk("23", el("div", {"children": "$L5"}))
    out = extract_next_flight_content(flight_page([self_ref, main]))
    # No hang; cycle renders empty → thin → None
    assert out is None


def test_falls_back_to_sweep_when_main_chunk_is_thin() -> None:
    thin_main = chunk("23", el("div", {"children": "tiny"}))
    body = chunk(
        "7",
        el(
            "section",
            {
                "children": children(
                    el("h1", {"children": "Full Content Lives Here"}),
                    el(
                        "p",
                        {
                            "children": (
                                "The page body was streamed into this later chunk "
                                "instead of the main one, which happens when a route "
                                "defers its shell and hydrates sections independently."
                            )
                        },
                    ),
                )
            },
        ),
    )
    out = extract_next_flight_content(flight_page([thin_main, body]))
    assert out is not None
    _title, content = out
    assert "# Full Content Lives Here" in content


def test_returns_none_for_non_flight_pages_and_unparseable_chunks() -> None:
    assert (
        extract_next_flight_content("<html><body><p>plain page</p></body></html>")
        is None
    )
    assert (
        extract_next_flight_content(
            "<html><body><script>self.__next_f.push([1,'not-json'])</script></body></html>"
        )
        is None
    )
    empty = flight_page([chunk("23", el("div", {"children": ""}))])
    assert extract_next_flight_content(empty) is None
