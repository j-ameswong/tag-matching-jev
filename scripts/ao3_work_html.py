#!/usr/bin/env python3
"""Extract work metadata from saved AO3 HTML; never execute or fetch page content."""

import argparse
from html.parser import HTMLParser
from pathlib import Path
import re

from ao3_benchmark import sha256, write_json


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def has(self, *classes):
        return set(classes) <= set(self.attrs.get("class", "").split())

    def text(self):
        if self.tag in ("script", "style"):
            return ""
        return " ".join(child.text() if isinstance(child, Node) else child for child in self.children)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()


class Parser(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def clean(node):
    return " ".join(node.text().split())


def parse_work(html):
    parser = Parser()
    parser.feed(html)
    nodes = list(parser.root.walk())
    meta = next((n for n in nodes if n.tag == "dl" and n.has("work", "meta")), None)
    if meta is None:
        raise ValueError("No AO3 work metadata; possibly a login or error page")
    stats = next((n for n in meta.walk() if n.tag == "dl" and n.has("stats")), None)
    raw = {} if stats is None else {n.attrs.get("class"): clean(n) for n in stats.walk() if n.tag == "dd"}
    types = {"rating": "Rating", "warning": "ArchiveWarning", "category": "Category", "fandom": "Fandom",
             "character": "Character", "relationship": "Relationship", "freeform": "Freeform"}
    tags = {types[n.attrs["class"].split()[0]]: [{"name": clean(a), "href": a.attrs.get("href")}
            for a in n.walk() if a.tag == "a" and a.has("tag")]
            for n in meta.walk() if n.tag == "dd" and n.has("tags") and n.attrs["class"].split()[0] in types}
    title = next((clean(n) for n in nodes if n.tag == "h2" and n.has("title", "heading")), None)
    summary_nodes = [n for n in nodes if n.tag == "div" and n.has("summary", "module")]
    summary = next((clean(b) for n in summary_nodes for b in n.walk() if b.tag == "blockquote"), None)
    chapter_nodes = [n for n in nodes if n.tag == "div" and n.has("chapter") and n.attrs.get("id", "").startswith("chapter-")]
    chapter_links = [a.attrs["href"] for n in chapter_nodes for a in n.walk()
                     if a.tag == "a" and re.fullmatch(r"/works/\d+/chapters/\d+", a.attrs.get("href", ""))]
    work_ids = {int(re.search(r"/works/(\d+)", a.attrs.get("href", "")).group(1)) for a in meta.walk()
                if a.tag == "a" and re.search(r"/works/(\d+)", a.attrs.get("href", ""))}
    work_ids.update(int(link.split("/")[2]) for link in chapter_links)
    if len(work_ids) > 1:
        raise ValueError("Ambiguous work identity")
    numeric = {key: int(raw[key].replace(",", "")) if key in raw else None
               for key in ("words", "comments", "kudos", "bookmarks", "hits")}
    return {"work_id": next(iter(work_ids), None), "title": title, "summary": summary,
            "language": next((clean(n) for n in meta.walk() if n.tag == "dd" and n.has("language")), None),
            "tags": tags, "stats": {**raw, **numeric}, "saved_chapter_containers": [n.attrs["id"] for n in chapter_nodes],
            "chapter_urls": chapter_links, "word_count_scope": "whole work; body may contain only selected chapters",
            "canonical_or_merger_labels_supplied": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=Path("tests/fixtures/ao3_work.html"))
    args = parser.parse_args()
    result = parse_work(args.path.read_text())
    # Metadata provenance is reviewable; prose stays with the supplied fixture.
    summary = result.pop("summary")
    result.update({"source": str(args.path), "source_sha256": sha256(args.path), "capture_date": None,
                   "summary_present": bool(summary), "summary_characters": len(summary or ""),
                   "benchmark_use": "extraction validation only; one work cannot estimate summary/engagement effectiveness"})
    write_json(Path("results/ao3/stage2/html-fixture.json"), result)
    print(f"Extracted work {result['work_id']}; summary present={bool(summary)}; {len(result['saved_chapter_containers'])} saved chapter(s)")


if __name__ == "__main__":
    main()
