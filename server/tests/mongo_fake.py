"""A small in-memory stand-in for the Mongo aggregation the signals service uses.

It understands only the stages app/services/signals.py actually sends
($match, $project, $addFields, $sort, $limit, $group with $min/$max/$sum), and
it COUNTS the documents it hands back so a test can prove the endpoint no longer
materialises a whole collection in Python.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.services import processors


class FakeAggregateCursor:
    """Async cursor over a pre-computed pipeline result."""

    def __init__(self, rows: list[dict], collection: "FakeAggCollection"):
        self._rows = rows
        self._index = 0
        self.collection = collection

    def __aiter__(self):
        return self

    async def __anext__(self) -> dict:
        if self._index >= len(self._rows):
            raise StopAsyncIteration
        row = self._rows[self._index]
        self._index += 1
        self.collection.rows_returned += 1
        return row


class FakeAggCollection:
    def __init__(self, rows: Iterable[dict], name: str = ""):
        self.rows = [dict(row) for row in rows]
        self.name = name
        self.rows_returned = 0
        self.pipelines: list[list[dict]] = []
        # how many documents each pipeline actually handed back to Python
        self.results: list[tuple[list[dict], int]] = []

    def aggregate(self, pipeline: list[dict], batchSize: int | None = None) -> FakeAggregateCursor:
        rows = self._run(pipeline)
        self.pipelines.append(list(pipeline))
        self.results.append((list(pipeline), len(rows)))
        return FakeAggregateCursor(rows, self)

    async def count_documents(self, _query: dict) -> int:
        return len(self.rows)

    # ------------------------------------------------------------ the mini engine

    def _run(self, pipeline: list[dict]) -> list[dict]:
        rows = [dict(row) for row in self.rows]
        for stage in pipeline:
            if "$match" in stage:
                rows = [row for row in rows if _matches(row, stage["$match"])]
            elif "$project" in stage:
                rows = [_project(row, stage["$project"]) for row in rows]
            elif "$addFields" in stage:
                rows = [_add_fields(row, stage["$addFields"]) for row in rows]
            elif "$sort" in stage:
                for field, direction in reversed(list(stage["$sort"].items())):
                    rows.sort(
                        key=lambda row, f=field: _sort_key(row.get(f)),
                        reverse=int(direction) < 0,
                    )
            elif "$limit" in stage:
                rows = rows[: int(stage["$limit"])]
            elif "$group" in stage:
                rows = _group(rows, stage["$group"])
            else:  # pragma: no cover - a stage the service does not send
                raise AssertionError(f"unsupported stage in the fake: {stage}")
        return rows


def _sort_key(value: Any) -> tuple:
    """Mongo puts missing/null first, then numbers, then strings."""
    if value is None:
        return (0, 0.0)
    if isinstance(value, str):
        return (2, value)
    return (1, float(value))


def _matches(row: dict, condition: dict) -> bool:
    for field, rule in condition.items():
        value = row.get(field)
        if not isinstance(rule, dict):
            if value != rule:
                return False
            continue
        if "$ne" in rule and value == rule["$ne"]:
            return False
        if "$nin" in rule and value in rule["$nin"]:
            return False
        if "$in" in rule and value not in rule["$in"]:
            return False
        if "$gte" in rule and not (value is not None and str(value) >= str(rule["$gte"])):
            return False
        if "$lte" in rule and not (value is not None and str(value) <= str(rule["$lte"])):
            return False
        if "$gt" in rule and not (value is not None and float(value) > float(rule["$gt"])):
            return False
        if "$exists" in rule and (field in row) != bool(rule["$exists"]):
            return False
        if "$type" in rule and not _is_type(value, rule["$type"]):
            return False
    return True


def _is_type(value: Any, alias: str) -> bool:
    if alias == "string":
        return isinstance(value, str)
    if alias == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if alias == "null":
        return value is None
    raise AssertionError(f"unsupported $type alias in the fake: {alias}")


def _project(row: dict, spec: dict) -> dict:
    keep = {field for field, wanted in spec.items() if wanted and field != "_id"}
    if not keep:
        return dict(row)
    return {field: row[field] for field in keep if field in row}


def _add_fields(row: dict, spec: dict) -> dict:
    out = dict(row)
    for field, expression in spec.items():
        out[field] = _evaluate(row, expression)
    return out


def _evaluate(row: dict, expression: Any) -> Any:
    """Only the two operators app/services/signals.py actually sends."""
    if not isinstance(expression, dict):
        return expression
    if "$subtract" in expression:
        parts = [_evaluate(row, operand) for operand in expression["$subtract"]]
        return processors.to_number(parts[0]) - processors.to_number(parts[1])
    if "$ifNull" in expression:
        field, default = expression["$ifNull"]
        value = row.get(field[1:]) if isinstance(field, str) and field.startswith("$") else field
        return default if value is None else value
    return expression


def _group(rows: list[dict], spec: dict) -> list[dict]:
    """One output row per group key ('_id': None means one group overall)."""
    key_expression = spec.get("_id")
    if isinstance(key_expression, str) and key_expression.startswith("$"):
        field = key_expression[1:]
        groups: dict[Any, list[dict]] = {}
        for row in rows:
            groups.setdefault(row.get(field), []).append(row)
    else:
        groups = {None: rows}

    outputs: list[dict] = []
    for key, group_rows in groups.items():
        out: dict[str, Any] = {"_id": key}
        for name, accumulator in spec.items():
            if name == "_id":
                continue
            (operator, source), = accumulator.items()
            values = [row.get(source.lstrip("$")) for row in group_rows]
            values = [value for value in values if value is not None]
            if operator == "$min":
                out[name] = min(values) if values else None
            elif operator == "$max":
                out[name] = max(values) if values else None
            elif operator == "$sum":
                out[name] = sum(processors.to_number(value) for value in values)
            else:  # pragma: no cover
                raise AssertionError(f"unsupported accumulator {operator}")
        outputs.append(out)
    return outputs
