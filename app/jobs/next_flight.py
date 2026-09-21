"""Next.js App Router flight extractor — the local tier for client-rendered
Next pages. RSC pages embed their content as JSON rows in inline
``<script>self.__next_f.push([1,"..."])</script>`` chunks; body-scraping
extractors find an empty body on those, so this decodes the payloads and
walks the serialized element tree to Markdown before the page is handed to
remote fallbacks (jina, markdown.new, ...).

Deep module: the entire flight protocol (chunk collection, ref resolution,
element-tree walking) hides behind one function.

Interface: extract_next_flight_content(html) -> (title, content) | None.
None means "not a flight page / nothing usable" — the caller keeps its
fallback chain. Pure function; tests cross this same seam.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable

MIN_CONTENT_LENGTH = 100
MIN_FALLBACK_CHUNK_LENGTH = 50

ChunkParser = Callable[[str], Any]

_SCRIPT_RE = re.compile(
    r'<script[^>]*>\s*self\.__next_f\.push\(\[1,"([\s\S]*?)"\]\)\s*</script>'
)
_TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>")
_REF_RE = re.compile(r"^\$L([0-9a-f]+)$", re.IGNORECASE)
_PROTO_RE = re.compile(r"^\$[A-Z]")
_NOT_FOUND_RE = re.compile(r"page was not found|404")


def extract_next_flight_content(html: str) -> tuple[str, str] | None:
    """Entry point. Returns None fast for pages without flight data."""
    if "self.__next_f.push" not in html:
        return None

    chunk_map = collect_flight_chunks(html)
    if not chunk_map:
        return None

    title_match = _TITLE_RE.search(html)
    title = title_match.group(1).split("|")[0].strip() if title_match else ""

    parsed_cache: dict[str, Any | None] = {}
    visited_refs: set[str] = set()

    def get_parsed_chunk(chunk_id: str) -> Any | None:
        if chunk_id in parsed_cache:
            return parsed_cache[chunk_id]
        chunk = chunk_map.get(chunk_id)
        parsed: Any | None = None
        if chunk is not None and chunk.startswith("["):
            try:
                parsed = json.loads(chunk)
            except (ValueError, TypeError):
                parsed = None
        parsed_cache[chunk_id] = parsed
        return parsed

    # Main content chunk first (the conventional root-layout id), then a sweep
    # over every other chunk in payload order, deduped by leading text.
    main_chunk = get_parsed_chunk("23")
    if main_chunk is not None:
        content = flight_node_to_markdown(
            main_chunk, get_parsed_chunk, visited_refs
        ).strip()
        if len(content) > MIN_CONTENT_LENGTH:
            return title, collapse_blank_lines(content)

    parts: list[tuple[int, str]] = []
    for chunk_id in chunk_map:
        if chunk_id == "23":
            continue
        parsed = get_parsed_chunk(chunk_id)
        if parsed is None:
            continue
        visited_refs.clear()
        text = flight_node_to_markdown(parsed, get_parsed_chunk, visited_refs).strip()
        if len(text) > MIN_FALLBACK_CHUNK_LENGTH and not _NOT_FOUND_RE.search(text):
            parts.append((int(chunk_id, 16), text))
    parts.sort(key=lambda part: part[0])

    seen: set[str] = set()
    deduped: list[str] = []
    for _order, text in parts:
        key = text[:150]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    content = re.sub(r"\n{3,}", "\n\n", "\n\n".join(deduped)).strip()

    return (title, content) if len(content) > MIN_CONTENT_LENGTH else None


# ── Chunk collection ─────────────────────────────────────────────────────────
# Each inline script pushes a JSON-string-escaped fragment; fragments decode
# to lines of "hexId:payload". Duplicate ids keep the longest payload (later
# stream updates win).

def collect_flight_chunks(html: str) -> dict[str, str]:
    chunk_map: dict[str, str] = {}
    for match in _SCRIPT_RE.finditer(html):
        try:
            decoded = json.loads(f'"{match.group(1)}"')
        except (ValueError, TypeError):
            continue
        if not isinstance(decoded, str):
            continue
        for line in decoded.split("\n"):
            if not line.strip():
                continue
            colon_idx = line.find(":")
            if colon_idx <= 0 or colon_idx > 4:
                continue
            chunk_id = line[:colon_idx]
            if not re.fullmatch(r"[0-9a-f]+", chunk_id, re.IGNORECASE):
                continue
            payload = line[colon_idx + 1:]
            if not payload:
                continue
            existing = chunk_map.get(chunk_id)
            if existing is None or len(payload) > len(existing):
                chunk_map[chunk_id] = payload
    return chunk_map


def collapse_blank_lines(content: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", content).strip()


# ── Flight tree → Markdown ───────────────────────────────────────────────────
# A parsed chunk is either an element ["$", tag, key, props] or an array of
# nodes. Strings like "$L<id>" reference other chunks and resolve recursively
# with cycle protection; "$<Something>" protocol strings are dropped.

SKIP_TAGS = {
    "script", "style", "svg", "path", "circle", "link", "meta", "template",
    "button", "input", "nav", "footer", "aside",
}


def flight_node_to_markdown(
    node: Any,
    get_parsed_chunk: ChunkParser,
    visited_refs: set[str],
    ctx: dict[str, bool] | None = None,
) -> str:
    if node is None:
        return ""
    if ctx is None:
        ctx = {"inTable": False, "inCode": False}

    if isinstance(node, str):
        ref_match = _REF_RE.match(node)
        if ref_match:
            ref_id = ref_match.group(1)
            if ref_id in visited_refs:
                return ""
            visited_refs.add(ref_id)
            ref_node = get_parsed_chunk(ref_id)
            result = (
                flight_node_to_markdown(ref_node, get_parsed_chunk, visited_refs, ctx)
                if ref_node is not None
                else ""
            )
            visited_refs.discard(ref_id)
            return result
        if (
            not ctx["inCode"]
            and (node == "$undefined" or node == "$" or _PROTO_RE.match(node))
        ):
            return ""
        return node if node.strip() else ""

    if isinstance(node, bool):
        return ""
    if isinstance(node, (int, float)):
        return str(node)
    if not isinstance(node, list):
        return ""

    # Element: ["$", tag, key, props]
    if len(node) >= 2 and node[0] == "$" and isinstance(node[1], str):
        tag = node[1]
        props = node[3] if len(node) > 3 and isinstance(node[3], dict) else {}
        if tag in SKIP_TAGS:
            return ""

        if tag.startswith("$L"):
            ref_id = tag[2:]
            if ref_id in visited_refs:
                return ""
            if props.get("baseId") and props.get("children") is not None:
                return f"## {props['children']}\n\n"
            visited_refs.add(ref_id)
            ref_node = get_parsed_chunk(ref_id)
            if ref_node is not None:
                result = flight_node_to_markdown(
                    ref_node, get_parsed_chunk, visited_refs, ctx
                )
            elif props.get("children") is not None:
                result = flight_node_to_markdown(
                    props["children"], get_parsed_chunk, visited_refs, ctx
                )
            else:
                result = ""
            visited_refs.discard(ref_id)
            return result

        content = (
            flight_node_to_markdown(
                props["children"], get_parsed_chunk, visited_refs, ctx
            )
            if props.get("children") is not None
            else ""
        )

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            return f"{'#' * level} {content.strip()}\n\n"
        if tag == "p":
            return content if ctx["inTable"] else f"{content.strip()}\n\n"
        if tag == "code":
            code = (
                flight_node_to_markdown(
                    props["children"],
                    get_parsed_chunk,
                    visited_refs,
                    {"inTable": False, "inCode": True},
                )
                if props.get("children") is not None
                else ""
            )
            return code if ctx["inCode"] else f"`{code}`"
        if tag == "pre":
            pre = (
                flight_node_to_markdown(
                    props["children"],
                    get_parsed_chunk,
                    visited_refs,
                    {"inTable": False, "inCode": True},
                )
                if props.get("children") is not None
                else ""
            )
            return f"```\n{pre}\n```\n\n"
        if tag in ("strong", "b"):
            return f"**{content}**"
        if tag in ("em", "i"):
            return f"*{content}*"
        if tag == "li":
            return f"- {content.strip()}\n"
        if tag in ("ul", "ol"):
            return content
        if tag == "blockquote":
            return f"> {content.strip()}\n\n"
        if tag == "table":
            return f"{flight_table_to_markdown(node, get_parsed_chunk, visited_refs)}\n"
        if tag in ("thead", "tbody", "tr", "th", "td"):
            return content
        if tag == "div":
            if props.get("role") == "alert" or props.get("data-slot") == "alert":
                return f"> {content.strip()}\n\n"
            return content
        if tag == "a":
            href = props.get("href")
            if isinstance(href, str) and not href.startswith("#"):
                return f"[{content}]({href})"
            return content
        return content

    # Array of child nodes
    return "".join(
        flight_node_to_markdown(child, get_parsed_chunk, visited_refs, ctx)
        for child in node
    )


def flight_table_to_markdown(
    table_node: list[Any],
    get_parsed_chunk: ChunkParser,
    visited_refs: set[str],
) -> str:
    props = table_node[3] if len(table_node) > 3 and isinstance(table_node[3], dict) else {}
    rows: list[list[str]] = []
    header_row_count = 0

    def walk(node: Any, is_header: bool) -> None:
        nonlocal header_row_count
        if node is None:
            return
        if isinstance(node, str):
            ref_match = _REF_RE.match(node)
            if ref_match and ref_match.group(1) not in visited_refs:
                visited_refs.add(ref_match.group(1))
                walk(get_parsed_chunk(ref_match.group(1)), is_header)
                visited_refs.discard(ref_match.group(1))
            return
        if not isinstance(node, list):
            return
        if len(node) >= 1 and node[0] == "$":
            tag = node[1] if len(node) > 1 else None
            if not isinstance(tag, str):
                return
            node_props = (
                node[3] if len(node) > 3 and isinstance(node[3], dict) else {}
            )
            if tag == "thead":
                walk(node_props.get("children"), True)
            elif tag == "tbody":
                walk(node_props.get("children"), False)
            elif tag == "tr":
                cells: list[str] = []
                walk_cells(node_props.get("children"), cells)
                if cells:
                    rows.append(cells)
                    if is_header:
                        header_row_count += 1
            elif tag.startswith("$L"):
                ref_id = tag[2:]
                if ref_id not in visited_refs:
                    visited_refs.add(ref_id)
                    walk(get_parsed_chunk(ref_id), is_header)
                    visited_refs.discard(ref_id)
            else:
                walk(node_props.get("children"), is_header)
        else:
            for child in node:
                walk(child, is_header)

    def walk_cells(node: Any, cells: list[str]) -> None:
        if node is None:
            return
        if isinstance(node, str):
            ref_match = _REF_RE.match(node)
            if ref_match and ref_match.group(1) not in visited_refs:
                visited_refs.add(ref_match.group(1))
                walk_cells(get_parsed_chunk(ref_match.group(1)), cells)
                visited_refs.discard(ref_match.group(1))
            return
        if not isinstance(node, list):
            return
        if len(node) >= 1 and node[0] == "$":
            tag = node[1] if len(node) > 1 else None
            node_props = (
                node[3] if len(node) > 3 and isinstance(node[3], dict) else {}
            )
            if tag in ("td", "th"):
                cell_props = (
                    node[3] if len(node) > 3 and isinstance(node[3], dict) else {}
                )
                text = (
                    flight_node_to_markdown(
                        cell_props.get("children"),
                        get_parsed_chunk,
                        visited_refs,
                        {"inTable": True, "inCode": False},
                    )
                    .strip()
                    .replace("\n", " ")
                    .replace("\\", "\\\\")
                    .replace("|", "\\|")
                )
                cells.append(text)
            elif isinstance(tag, str) and tag.startswith("$L"):
                ref_id = tag[2:]
                if ref_id not in visited_refs:
                    visited_refs.add(ref_id)
                    ref_node = get_parsed_chunk(ref_id)
                    walk_cells(ref_node if ref_node is not None else node_props.get("children"), cells)
                    visited_refs.discard(ref_id)
            else:
                walk_cells(node_props.get("children"), cells)
        else:
            for child in node:
                walk_cells(child, cells)

    walk(props.get("children"), False)
    if not rows:
        return ""

    col_count = max(len(row) for row in rows)
    md = ""
    for i, row in enumerate(rows):
        padded = row + [""] * (col_count - len(row))
        md += f"| {' | '.join(padded)} |\n"
        if i == header_row_count - 1 or (header_row_count == 0 and i == 0):
            md += f"| {' | '.join(['---'] * col_count)} |\n"
    return md
