#!/usr/bin/env python3
"""Minimal Notion REST helpers (stdlib only)."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

API = "https://api.notion.com/v1"
VERSION = "2022-06-28"


def token() -> str:
    t = os.environ.get("NOTION_TOKEN") or os.environ.get("NOTION_API_KEY")
    if not t:
        raise SystemExit(
            "Set NOTION_TOKEN (internal integration secret) in the environment."
        )
    return t


def request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token()}",
            "Notion-Version": VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Notion API {method} {path} failed ({e.code}): {err}") from e


def create_page(parent_database_id: str, properties: dict[str, Any]) -> dict[str, Any]:
    return request(
        "POST",
        "/pages",
        {"parent": {"database_id": parent_database_id}, "properties": properties},
    )


def update_page(page_id: str, properties: dict[str, Any]) -> dict[str, Any]:
    return request("PATCH", f"/pages/{page_id}", {"properties": properties})


def query_database(
    database_id: str, filter_obj: dict[str, Any] | None = None, page_size: int = 20
) -> list[dict[str, Any]]:
    body: dict[str, Any] = {"page_size": page_size}
    if filter_obj:
        body["filter"] = filter_obj
    result = request("POST", f"/databases/{database_id}/query", body)
    return list(result.get("results") or [])


def title(text: str) -> dict[str, Any]:
    return {"title": [{"text": {"content": text[:2000]}}]}


def rich(text: str) -> dict[str, Any]:
    return {"rich_text": [{"text": {"content": text[:2000]}}]}


def select(name: str) -> dict[str, Any]:
    return {"select": {"name": name}}


def url(value: str | None) -> dict[str, Any]:
    return {"url": value}


def date_start(iso_date: str) -> dict[str, Any]:
    return {"date": {"start": iso_date}}


def checkbox(value: bool) -> dict[str, Any]:
    return {"checkbox": value}


def number(value: float | int | None) -> dict[str, Any]:
    return {"number": value}
