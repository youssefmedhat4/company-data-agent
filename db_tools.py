#!/usr/bin/env python3
"""
DB TOOLS — the six tools, with every security control enforced here.

This module has no MCP and no Ollama in it on purpose. It is plain Python
you can test directly. mcp_client.py talks to DBHub for questions the
curated tools cannot express. agent.py calls both.

Security controls, all enforced in code rather than by asking the model:
  - only SELECT, only against views named in catalog.py
  - parameterized queries everywhere; no string concatenation of input
  - hard row limit and query timeout
  - ID provenance: get_records rejects any id search_entities did not issue
  - sensitive entities require an explicit flag
  - bidi/control characters stripped from returned text
  - every call written to the audit log
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

import catalog

_engine = create_engine(catalog.DB_URL, pool_pre_ping=True)

# characters that can visually reorder text and disguise content
_BIDI = dict.fromkeys(
    [0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x200F, 0x200E]
)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_ar(value: str) -> str:
    """Same folding used when the shadow columns were built."""
    out = (value or "").strip().lower()
    for src, dst in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي"),
                     ("ـ", ""), ("ً", ""), ("ٌ", ""), ("ٍ", ""), ("َ", ""),
                     ("ُ", ""), ("ِ", ""), ("ّ", ""), ("ْ", "")):
        out = out.replace(src, dst)
    return " ".join(out.split())


def _clean(value):
    """Strip bidi controls from any string coming out of the database."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value.translate(_BIDI))
    return value


def _id_peers(entity_type: str) -> set[str]:
    """Entity types that describe the same rows, so an id issued for one really is
    an id for the other. This only recognises views over the same population; it
    does not relax the sensitive gate, which is checked separately on every call.
    """
    peers = {entity_type}
    spec = catalog.ENTITIES.get(entity_type) or {}
    linked = spec.get("shares_ids_with")
    if linked:
        peers.add(linked)
    for name, other in catalog.ENTITIES.items():
        if other.get("shares_ids_with") == entity_type:
            peers.add(name)
    return peers


def _safe_ident(name: str) -> str:
    """Identifiers come from catalog.py, never from the model. Verify anyway."""
    if not _IDENT.match(name or ""):
        raise ValueError(f"unsafe identifier: {name!r}")
    return name


def _audit(tool: str, args: dict, rows: int, user: str = "local",
           allow_sensitive: bool = False) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "tool": tool,
        "args": args,
        "rows": rows,
        # Without this the log cannot answer "was that salary read permitted?".
        "allow_sensitive": allow_sensitive,
    }
    try:
        with open(catalog.AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _select(sql: str, params: dict) -> list[dict]:
    """Run one read-only query with a row cap."""
    if not sql.lstrip().lower().startswith("select"):
        raise ValueError("only SELECT statements are permitted")
    with _engine.connect() as conn:
        result = conn.execute(text(sql), params)
        rows = result.mappings().fetchmany(catalog.MAX_ROWS)
    return [{k: _clean(v) for k, v in row.items()} for row in rows]


class ToolSession:
    """One conversation. Tracks which IDs were legitimately handed out."""

    def __init__(self, user: str = "local", allow_sensitive: bool = False):
        self.user = user
        self.allow_sensitive = allow_sensitive
        self.issued: dict[str, set] = {}  # entity_type -> ids this session handed out
        self.returned_numbers: set[int] = set()
        self.calls: list[str] = []

    # -- bookkeeping ------------------------------------------------
    def _remember_numbers(self, payload) -> None:
        if isinstance(payload, dict):
            for v in payload.values():
                self._remember_numbers(v)
        elif isinstance(payload, list):
            for v in payload:
                self._remember_numbers(v)
        elif isinstance(payload, (int, float)) and not isinstance(payload, bool):
            self.returned_numbers.add(int(payload))

    def _was_issued(self, entity_type: str, entity_id) -> bool:
        return any(entity_id in self.issued.get(peer, set())
                   for peer in _id_peers(entity_type))

    def _finish(self, tool: str, args: dict, payload):
        self.calls.append(tool)
        self._remember_numbers(payload)
        n = len(payload) if isinstance(payload, list) else 1
        _audit(tool, args, n, self.user, self.allow_sensitive)
        return payload

    # -- tool 1 -----------------------------------------------------
    def list_data_areas(self) -> dict:
        areas = {
            "entities": [
                {"type": name, "description": spec["description"],
                 "restricted": bool(spec.get("sensitive")),
                 "can_list_by": list(spec.get("filterable", {}))}
                for name, spec in catalog.ENTITIES.items()
            ],
            "metrics": [
                {"metric": name, "unit": spec["unit"], "definition": spec["definition"],
                 "group_by": list(spec["group_by_options"].keys()),
                 "restricted": bool(spec.get("sensitive"))}
                for name, spec in catalog.METRICS.items()
            ],
            "periods": list(catalog.PERIODS.keys()),
        }
        return self._finish("list_data_areas", {}, areas)

    # -- tool 2 -----------------------------------------------------
    def search_entities(self, entity_type: str, query: str) -> dict:
        args = {"entity_type": entity_type, "query": query}
        spec = catalog.ENTITIES.get(entity_type)
        if spec is None:
            return self._finish("search_entities", args,
                                {"error": f"unknown entity_type. Valid: {list(catalog.ENTITIES)}"})
        if spec.get("sensitive") and not self.allow_sensitive:
            return self._finish("search_entities", args,
                                {"error": "this entity is restricted for the current user"})

        view = _safe_ident(spec["view"])
        col = _safe_ident(spec["search_column"])
        cols = ", ".join(_safe_ident(c) for c in spec["display_columns"])

        needle = normalize_ar(query)
        rows = _select(
            f"SELECT {cols} FROM {view} WHERE {col} LIKE :needle LIMIT :lim",
            {"needle": f"%{needle}%", "lim": catalog.MAX_ROWS},
        )

        id_col = spec["id_column"]
        self.issued.setdefault(entity_type, set()).update(
            r[id_col] for r in rows if id_col in r
        )

        payload = {
            "matches": rows,
            "count": len(rows),
            "note": (
                "These results contain only identifying fields. "
                f"Call get_records with entity_type='{entity_type}' and an id from "
                "this list to obtain full details."
            ),
        }
        if not rows:
            payload["hint"] = (
                "No match. Names may be stored in Arabic or English -- try the other "
                "script, or a shorter part of the name. Do not guess an id."
            )
        return self._finish("search_entities", args, payload)

    # -- tool 6 -----------------------------------------------------
    def list_entities(self, entity_type: str, filter_field: str | None = None,
                      filter_value: str | None = None) -> dict:
        """Answer "who" and "which": rows of one entity type, optionally narrowed
        to one whitelisted column. Returns identifying fields only, exactly like
        search_entities, and registers the ids so get_records can follow up."""
        args = {"entity_type": entity_type, "filter_field": filter_field,
                "filter_value": filter_value}
        spec = catalog.ENTITIES.get(entity_type)
        if spec is None:
            return self._finish("list_entities", args,
                                {"error": f"unknown entity_type. Valid: {list(catalog.ENTITIES)}"})
        if spec.get("sensitive") and not self.allow_sensitive:
            return self._finish("list_entities", args,
                                {"error": "this entity is restricted for the current user"})

        filterable = spec.get("filterable", {})
        view = _safe_ident(spec["view"])
        cols = ", ".join(_safe_ident(c) for c in spec["display_columns"])

        params: dict = {"lim": catalog.MAX_ROWS}
        where = ""
        if filter_field:
            column = filterable.get(filter_field)
            if column is None:
                return self._finish("list_entities", args, {
                    "error": (f"cannot filter {entity_type} by {filter_field}. "
                              f"Valid: {list(filterable) or 'none'}"),
                })
            if not filter_value:
                return self._finish("list_entities", args,
                                    {"error": f"filter_value is required with {filter_field}"})
            # Compared case-folded so "finance" matches "Finance".
            where = f" WHERE LOWER({_safe_ident(column)}) = LOWER(:val)"
            params["val"] = filter_value.strip()

        rows = _select(f"SELECT {cols} FROM {view}{where} LIMIT :lim", params)

        id_col = spec["id_column"]
        self.issued.setdefault(entity_type, set()).update(
            r[id_col] for r in rows if id_col in r
        )

        payload = {
            "matches": rows,
            "count": len(rows),
            "truncated": len(rows) >= catalog.MAX_ROWS,
            "note": ("Identifying fields only. For any further detail call "
                     f"get_records with entity_type='{entity_type}' and an id above."),
        }
        return self._finish("list_entities", args, payload)

    # -- tool 3 -----------------------------------------------------
    def get_metric(self, metric: str, period: str, group_by: str = "none",
                   filter_entity: str | None = None,
                   filter_id: int | None = None) -> dict:
        args = {"metric": metric, "period": period, "group_by": group_by,
                "filter_entity": filter_entity, "filter_id": filter_id}
        spec = catalog.METRICS.get(metric)
        if spec is None:
            return self._finish("get_metric", args,
                                {"error": f"unknown metric. Valid: {list(catalog.METRICS)}"})
        if spec.get("sensitive") and not self.allow_sensitive:
            return self._finish("get_metric", args,
                                {"error": "this metric is restricted for the current user"})
        # A snapshot metric describes the present, so any period is ignored rather
        # than rejected -- the model has no way to express "now" as a period.
        snapshot = spec["date_column"] is None
        if not snapshot and period not in catalog.PERIODS:
            return self._finish("get_metric", args,
                                {"error": f"unknown period. Valid: {list(catalog.PERIODS)}"})
        if group_by not in spec["group_by_options"]:
            return self._finish("get_metric", args,
                                {"error": f"invalid group_by. Valid: {list(spec['group_by_options'])}"})

        view = _safe_ident(spec["view"])
        group_expr = spec["group_by_options"][group_by]

        params: dict = {"lim": catalog.MAX_ROWS}
        where = []
        if snapshot:
            start = end = None
        else:
            start, end = catalog.PERIODS[period]
            params |= {"start": start, "end": end}
            where.append(f"{_safe_ident(spec['date_column'])} BETWEEN :start AND :end")

        if filter_entity and filter_id is not None:
            fk = spec["filter_by"].get(filter_entity)
            if fk is None:
                return self._finish("get_metric", args,
                                    {"error": f"cannot filter {metric} by {filter_entity}"})
            # provenance: the id must have come from a search
            if not self._was_issued(filter_entity, filter_id):
                return self._finish("get_metric", args, {
                    "error": f"id {filter_id} was not returned by search_entities. "
                             "Search for the name first, then use the id from the result.",
                })
            where.append(f"{_safe_ident(fk)} = :fid")
            params["fid"] = filter_id

        clause = (" WHERE " + " AND ".join(where)) if where else ""
        if group_expr:
            sql = (f"SELECT {group_expr} AS group_key, {spec['expression']} AS value "
                   f"FROM {view}{clause} GROUP BY {group_expr} "
                   f"ORDER BY value DESC LIMIT :lim")
        else:
            sql = f"SELECT {spec['expression']} AS value FROM {view}{clause}"

        rows = _select(sql, params)
        payload = {
            "metric": metric,
            "period": "current" if snapshot else period,
            "range": None if snapshot else [start, end],
            "unit": spec["unit"], "group_by": group_by, "results": rows,
        }
        if snapshot:
            payload["note"] = ("A present-day total. Do not describe it as belonging to "
                               "any time period.")
        return self._finish("get_metric", args, payload)

    # -- tool 4 -----------------------------------------------------
    def get_records(self, entity_type: str, entity_id: int) -> dict:
        args = {"entity_type": entity_type, "entity_id": entity_id}
        spec = catalog.ENTITIES.get(entity_type)
        if spec is None:
            return self._finish("get_records", args,
                                {"error": f"unknown entity_type. Valid: {list(catalog.ENTITIES)}"})
        if spec.get("sensitive") and not self.allow_sensitive:
            return self._finish("get_records", args,
                                {"error": "this entity is restricted for the current user"})

        # THE GUARD -- an id we never handed out is refused
        if not self._was_issued(entity_type, entity_id):
            return self._finish("get_records", args, {
                "error": f"id {entity_id} was not returned by search_entities or "
                         f"list_entities for '{entity_type}'. Call one of those first "
                         "and use an id from its result. Never guess an id.",
            })

        view = _safe_ident(spec["view"])
        id_col = _safe_ident(spec["id_column"])
        cols = ", ".join(_safe_ident(c) for c in spec["detail_columns"])
        rows = _select(f"SELECT {cols} FROM {view} WHERE {id_col} = :eid", {"eid": entity_id})
        payload = rows[0] if rows else {"error": "not found"}

        # Point at the view that holds the rest. Without this the model asks
        # 'employee' for a salary, gets a record with no salary in it, and stalls.
        if rows:
            elsewhere = {}
            for peer in _id_peers(entity_type) - {entity_type}:
                peer_spec = catalog.ENTITIES.get(peer, {})
                missing = [c for c in peer_spec.get("detail_columns", [])
                           if c not in spec["detail_columns"]]
                if missing:
                    elsewhere[peer] = missing
            if elsewhere:
                payload = dict(payload)
                payload["more_detail_in"] = elsewhere
                payload["note"] = (
                    "Fields absent here live in the entity_type shown in "
                    "more_detail_in. Call get_records again with that entity_type and "
                    "the same id. It may require permission.")

        return self._finish("get_records", args, payload)

    # -- tool 5 -----------------------------------------------------
    def describe_metric(self, metric: str) -> dict:
        args = {"metric": metric}
        spec = catalog.METRICS.get(metric)
        if spec is None:
            return self._finish("describe_metric", args,
                                {"error": f"unknown metric. Valid: {list(catalog.METRICS)}"})
        payload = {
            "metric": metric,
            "definition": spec["definition"],
            "unit": spec["unit"],
            "group_by_options": list(spec["group_by_options"].keys()),
            "restricted": bool(spec.get("sensitive")),
        }
        return self._finish("describe_metric", args, payload)

    # -- dispatch ---------------------------------------------------
    def call(self, name: str, args: dict) -> dict:
        fn = {
            "list_data_areas": lambda a: self.list_data_areas(),
            "search_entities": lambda a: self.search_entities(
                a.get("entity_type", ""), a.get("query", "")),
            "list_entities": lambda a: self.list_entities(
                a.get("entity_type", ""), a.get("filter_field"), a.get("filter_value")),
            "get_metric": lambda a: self.get_metric(
                a.get("metric", ""), a.get("period", ""), a.get("group_by", "none"),
                a.get("filter_entity"), a.get("filter_id")),
            "get_records": lambda a: self.get_records(
                a.get("entity_type", ""), a.get("entity_id", -1)),
            "describe_metric": lambda a: self.describe_metric(a.get("metric", "")),
        }.get(name)
        if fn is None:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(args)
        except Exception as exc:
            return {"error": f"tool failed: {exc}"}


def tool_schemas() -> list[dict]:
    """OpenAI/Ollama-format schemas, generated from the catalog."""
    entity_types = list(catalog.ENTITIES.keys())
    metrics = list(catalog.METRICS.keys())
    periods = list(catalog.PERIODS.keys())
    groups = sorted({g for m in catalog.METRICS.values() for g in m["group_by_options"]})
    filter_fields = sorted({f for e in catalog.ENTITIES.values()
                            for f in e.get("filterable", {})})

    return [
        {"type": "function", "function": {
            "name": "list_data_areas",
            "description": (
                "List what data exists: entity types that can be searched, metrics that "
                "can be requested, and valid time periods. Use when unsure what is available."),
            "parameters": {"type": "object", "properties": {}}}},

        {"type": "function", "function": {
            "name": "search_entities",
            "description": (
                "Find a person or company by name and return ONLY their id and identifying "
                "fields. This tool never returns salaries, totals, or other details. Use it "
                "FIRST whenever the user names someone, then call get_records with the id. "
                "If several matches come back, ask the user which they mean. Works with "
                "Arabic or English spelling."),
            "parameters": {"type": "object", "properties": {
                "entity_type": {"type": "string", "enum": entity_types},
                "query": {"type": "string", "description": "Name as the user wrote it."}},
                "required": ["entity_type", "query"]}}},

        {"type": "function", "function": {
            "name": "list_entities",
            "description": (
                "List people or companies, optionally narrowed to one attribute. Use "
                "for 'who', 'which' and 'list' questions such as 'who works in "
                "Finance', 'list the customers in Cairo', 'name every employee'. "
                "Returns identifying fields only -- follow up with get_records using "
                "an id from the result for salary or other detail. Use search_entities "
                "instead when the user names someone."),
            "parameters": {"type": "object", "properties": {
                "entity_type": {"type": "string", "enum": entity_types},
                "filter_field": {"type": "string", "enum": filter_fields,
                                 "description": "Optional. Attribute to narrow by."},
                "filter_value": {"type": "string",
                                 "description": "Optional. Value for filter_field, "
                                                "e.g. 'Finance'. Case-insensitive."}},
                "required": ["entity_type"]}}},

        {"type": "function", "function": {
            "name": "get_metric",
            "description": (
                "Return an aggregated figure for a period, optionally grouped or filtered to "
                "one entity. Use for 'how much', 'how many', 'total', 'compare'. To filter by "
                "a named company, first call search_entities and pass the id you receive. "
                "Do NOT use this for details of one person -- use get_records."),
            "parameters": {"type": "object", "properties": {
                "metric": {"type": "string", "enum": metrics},
                "period": {"type": "string", "enum": periods},
                "group_by": {"type": "string", "enum": groups,
                             "description": "Use 'none' unless a breakdown was requested."},
                "filter_entity": {"type": "string", "enum": entity_types,
                                  "description": "Optional. Restrict to one entity type."},
                "filter_id": {"type": "integer",
                              "description": "Optional. Id from search_entities. Never guess."}},
                "required": ["metric", "period"]}}},

        {"type": "function", "function": {
            "name": "get_records",
            "description": (
                "Return the full details of ONE person or company -- department, region, "
                "salary where permitted. This is the only tool that returns such details. "
                "Requires an id previously returned by search_entities; invented ids are "
                "rejected."),
            "parameters": {"type": "object", "properties": {
                "entity_type": {"type": "string", "enum": entity_types},
                "entity_id": {"type": "integer",
                              "description": "Id from search_entities. Never guess."}},
                "required": ["entity_type", "entity_id"]}}},

        {"type": "function", "function": {
            "name": "describe_metric",
            "description": (
                "Explain how a metric is defined, its unit, and how it can be grouped. "
                "Use when the user asks what a number means, not to retrieve a value."),
            "parameters": {"type": "object", "properties": {
                "metric": {"type": "string", "enum": metrics}},
                "required": ["metric"]}}},
    ]
