#!/usr/bin/env -S uv run --script

# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "jsonschema>=4.21",
#   "referencing>=0.35",
# ]
# ///

"""Validation harness for the JSON Schemas under ``schemas/``.

Runs five checks and emits a machine-readable JSON summary on stdout
(human-readable progress goes to stderr):

1. Draft-07 meta-validation of every ``schemas/*.schema.json``.
2. Fixture validation: for each schema, every document under
   ``fixtures/<name>/valid/`` must pass and every document under
   ``fixtures/<name>/invalid/`` must fail (TOML fixtures are converted
   via ``tomllib``; JSON fixtures via ``json``).
3. Real-corpus validation: the five hook examples under
   ``crates/codegen/xai-grok-hooks/examples/hooks/*.json`` against
   ``grok-hooks.schema.json`` and
   ``crates/codegen/xai-grok-models/default_models.json`` against
   ``default-models.schema.json``.
4. Citation drift check: every ``[src: <path>:<lines>]`` provenance
   suffix embedded in the schemas is verified against the working tree
   (path exists, largest cited line <= file line count). This check is
   purely about citations going stale after a "Synced from monorepo"
   commit; it never judges semantic strictness (the S1/S3 transport
   ``anyOf`` and the S12 resolver-side clamps are deliberate).
5. AC-5 gate: zero occurrences of ``"additionalProperties": false``
   across all schemas.

Exit status is 0 if and only if every check passes.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

SCHEMAS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCHEMAS_DIR.parent
FIXTURES_DIR = SCHEMAS_DIR / "fixtures"

REAL_CORPUS: dict[str, list[str]] = {
    "grok-hooks.schema.json": ["crates/codegen/xai-grok-hooks/examples/hooks/*.json"],
    "default-models.schema.json": [
        "crates/codegen/xai-grok-models/default_models.json"
    ],
}

CITATION_BLOCK_RE = re.compile(r"\[src: ([^\]]+)\]")
PART_WITH_LINES_RE = re.compile(
    r"^(?P<path>[A-Za-z0-9_.\-/]+):"
    r"(?P<lines>\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*)"
    r"(?:\s.*)?$"
)
PART_PATH_ONLY_RE = re.compile(r"^(?P<path>[A-Za-z0-9_.\-/]+)$")
WALK_SKIP_DIRS = frozenset({".git", "target", "node_modules", ".omc"})

logging.basicConfig(
    stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s"
)
logger = logging.getLogger("validate")


@dataclass
class Citation:
    """One parsed provenance citation part extracted from a schema."""

    schema: str
    raw: str
    path: str
    max_line: int | None


@dataclass
class Results:
    """Mutable accumulator for the run summary."""

    schemas_meta_valid: int = 0
    schemas_total: int = 0
    valid_pass: int = 0
    valid_fail: int = 0
    invalid_expected_fail: int = 0
    invalid_unexpected_pass: int = 0
    real_corpus: dict[str, dict[str, Any]] = field(default_factory=dict)
    stale_citations: list[dict[str, str]] = field(default_factory=list)
    citations_checked: int = 0
    additional_properties_false: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when every check passed."""
        return (
            self.schemas_meta_valid == self.schemas_total
            and self.schemas_total > 0
            and self.valid_fail == 0
            and self.invalid_unexpected_pass == 0
            and all(entry["ok"] for entry in self.real_corpus.values())
            and not self.stale_citations
            and self.additional_properties_false == 0
        )

    def summary(self) -> dict[str, Any]:
        """Assemble the machine-readable summary emitted on stdout."""
        return {
            "schemas_meta_valid": self.schemas_meta_valid,
            "schemas_total": self.schemas_total,
            "fixtures": {
                "valid_pass": self.valid_pass,
                "valid_fail": self.valid_fail,
                "invalid_expected_fail": self.invalid_expected_fail,
                "invalid_unexpected_pass": self.invalid_unexpected_pass,
            },
            "real_corpus": self.real_corpus,
            "stale_citations": self.stale_citations,
            "citations_checked": self.citations_checked,
            "additional_properties_false": self.additional_properties_false,
            "failures": self.failures,
            "ok": self.ok,
        }


def load_schemas() -> dict[str, dict[str, Any]]:
    """Load every ``*.schema.json`` in the schemas directory, keyed by filename."""
    schemas: dict[str, dict[str, Any]] = {}
    for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        with path.open("rb") as fh:
            schemas[path.name] = json.load(fh)
    return schemas


def build_registry(schemas: dict[str, dict[str, Any]]) -> Registry:
    """Register each schema under both its ``$id`` and its bare filename.

    ``requirements-config.schema.json`` carries a relative
    ``$ref: "./grok-config.schema.json"``; resolving it against the
    referrer's ``$id`` base URI lands on ``grok-config.schema.json``'s
    ``$id``, and the filename mapping covers resolvers that treat the
    schemas directory itself as the base.
    """
    pairs: list[tuple[str, Resource]] = []
    for name, contents in schemas.items():
        resource = Resource.from_contents(contents, default_specification=DRAFT7)
        schema_id = contents.get("$id")
        if isinstance(schema_id, str) and schema_id:
            pairs.append((schema_id, resource))
        pairs.append((name, resource))
        pairs.append((f"./{name}", resource))
    return Registry().with_resources(pairs)


def meta_validate(schemas: dict[str, dict[str, Any]], results: Results) -> None:
    """Check every schema against the draft-07 meta-schema."""
    for name, contents in schemas.items():
        results.schemas_total += 1
        try:
            Draft7Validator.check_schema(contents)
        except SchemaError as exc:
            results.failures.append(
                {"kind": "meta", "schema": name, "detail": exc.message}
            )
            logger.error("meta-validation failed: %s: %s", name, exc.message)
        else:
            results.schemas_meta_valid += 1
    logger.info(
        "meta-validation: %d/%d schemas draft-07 valid",
        results.schemas_meta_valid,
        results.schemas_total,
    )


def toml_to_jsonable(value: Any) -> Any:
    """Recursively convert tomllib output into JSON-compatible values."""
    if isinstance(value, dict):
        return {key: toml_to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [toml_to_jsonable(item) for item in value]
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    return value


def load_document(path: Path) -> Any:
    """Parse a fixture or corpus document (JSON or TOML) into plain values."""
    if path.suffix == ".toml":
        with path.open("rb") as fh:
            return toml_to_jsonable(tomllib.load(fh))
    with path.open("rb") as fh:
        return json.load(fh)


def error_digest(validator: Draft7Validator, document: Any) -> list[str]:
    """Return up to three concise validation error messages for a document."""
    errors = sorted(validator.iter_errors(document), key=lambda err: err.json_path)
    return [f"{err.json_path}: {err.message}"[:300] for err in errors[:3]]


def validate_fixtures(
    schemas: dict[str, dict[str, Any]], registry: Registry, results: Results
) -> None:
    """Validate valid/ and invalid/ fixture documents for every schema."""
    for name, contents in schemas.items():
        fixture_dir = FIXTURES_DIR / name.removesuffix(".schema.json")
        if not fixture_dir.is_dir():
            results.failures.append(
                {
                    "kind": "fixture-dir-missing",
                    "schema": name,
                    "detail": str(fixture_dir),
                }
            )
            logger.error("fixture directory missing for %s: %s", name, fixture_dir)
            continue
        validator = Draft7Validator(contents, registry=registry)
        for fixture in sorted((fixture_dir / "valid").glob("*")):
            rel = fixture.relative_to(SCHEMAS_DIR)
            try:
                document = load_document(fixture)
            except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
                results.valid_fail += 1
                results.failures.append(
                    {
                        "kind": "valid-fixture-parse-error",
                        "schema": name,
                        "file": str(rel),
                        "detail": str(exc),
                    }
                )
                logger.error("valid fixture unparseable: %s: %s", rel, exc)
                continue
            digest = error_digest(validator, document)
            if digest:
                results.valid_fail += 1
                results.failures.append(
                    {
                        "kind": "valid-fixture-rejected",
                        "schema": name,
                        "file": str(rel),
                        "detail": "; ".join(digest),
                    }
                )
                logger.error("valid fixture rejected: %s: %s", rel, digest[0])
            else:
                results.valid_pass += 1
        for fixture in sorted((fixture_dir / "invalid").glob("*")):
            rel = fixture.relative_to(SCHEMAS_DIR)
            try:
                document = load_document(fixture)
            except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
                results.invalid_expected_fail += 1
                logger.warning(
                    "invalid fixture failed at parse (counted as expected"
                    " failure, but should be schema-invalid, not"
                    " syntax-invalid): %s: %s",
                    rel,
                    exc,
                )
                continue
            if error_digest(validator, document):
                results.invalid_expected_fail += 1
            else:
                results.invalid_unexpected_pass += 1
                results.failures.append(
                    {
                        "kind": "invalid-fixture-accepted",
                        "schema": name,
                        "file": str(rel),
                        "detail": "document validated but must fail",
                    }
                )
                logger.error("invalid fixture unexpectedly passed: %s", rel)
    logger.info(
        "fixtures: %d valid passed, %d valid failed; %d invalid rejected"
        " as expected, %d invalid unexpectedly passed",
        results.valid_pass,
        results.valid_fail,
        results.invalid_expected_fail,
        results.invalid_unexpected_pass,
    )


def validate_real_corpus(
    schemas: dict[str, dict[str, Any]], registry: Registry, results: Results
) -> None:
    """Validate the in-repo real documents against their schemas."""
    for schema_name, patterns in REAL_CORPUS.items():
        validator = Draft7Validator(schemas[schema_name], registry=registry)
        for pattern in patterns:
            matches = sorted(REPO_ROOT.glob(pattern))
            if not matches:
                results.real_corpus[pattern] = {
                    "schema": schema_name,
                    "ok": False,
                    "errors": ["no files matched pattern"],
                }
                logger.error("real corpus pattern matched nothing: %s", pattern)
                continue
            for path in matches:
                rel = str(path.relative_to(REPO_ROOT))
                digest = error_digest(validator, load_document(path))
                results.real_corpus[rel] = {
                    "schema": schema_name,
                    "ok": not digest,
                    "errors": digest,
                }
                if digest:
                    logger.error("real corpus failed: %s: %s", rel, digest[0])
                else:
                    logger.info("real corpus ok: %s", rel)


def collect_citations(schemas: dict[str, dict[str, Any]]) -> list[Citation]:
    """Extract every ``[src: ...]`` citation part from schema string values."""
    citations: list[Citation] = []

    def walk(node: object, schema_name: str) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value, schema_name)
        elif isinstance(node, list):
            for item in node:
                walk(item, schema_name)
        elif isinstance(node, str):
            for block in CITATION_BLOCK_RE.findall(node):
                for part in block.split(";"):
                    part = part.strip()
                    if not part:
                        continue
                    match = PART_WITH_LINES_RE.match(part)
                    if match:
                        max_line = max(
                            int(n) for n in re.findall(r"\d+", match.group("lines"))
                        )
                        citations.append(
                            Citation(schema_name, part, match.group("path"), max_line)
                        )
                        continue
                    match = PART_PATH_ONLY_RE.match(part)
                    if match:
                        citations.append(
                            Citation(schema_name, part, match.group("path"), None)
                        )
                        continue
                    citations.append(Citation(schema_name, part, "", None))

    for name, contents in schemas.items():
        walk(contents, name)
    return citations


def build_basename_index(names: set[str]) -> dict[str, list[Path]]:
    """Locate candidate files for bare-basename citations in one repo walk."""
    index: dict[str, list[Path]] = {name: [] for name in names}
    if not names:
        return index
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in WALK_SKIP_DIRS]
        for filename in filenames:
            if filename in index:
                index[filename].append(Path(dirpath) / filename)
    return index


def count_lines(path: Path, cache: dict[Path, int]) -> int:
    """Count lines in a file, memoized across citations."""
    if path not in cache:
        with path.open("rb") as fh:
            cache[path] = sum(1 for _ in fh)
    return cache[path]


def check_citations(citations: list[Citation], results: Results) -> None:
    """Verify each citation's path exists and its lines fit the file.

    Bare basenames (no ``/``) are resolved by searching the repo; a bare
    citation is stale only when no candidate file satisfies the bound.
    """
    line_cache: dict[Path, int] = {}
    bare_names = {c.path for c in citations if c.path and "/" not in c.path}
    basename_index = build_basename_index(bare_names)
    seen_stale: set[tuple[str, str]] = set()

    def add_stale(citation: Citation, reason: str) -> None:
        key = (citation.schema, citation.raw)
        if key not in seen_stale:
            seen_stale.add(key)
            results.stale_citations.append(
                {"schema": citation.schema, "citation": citation.raw, "reason": reason}
            )
            logger.error(
                "stale citation in %s: [src: %s] (%s)",
                citation.schema,
                citation.raw,
                reason,
            )

    for citation in citations:
        results.citations_checked += 1
        if not citation.path:
            add_stale(citation, "unparseable citation part")
            continue
        if "/" in citation.path:
            candidates = [REPO_ROOT / citation.path]
        else:
            candidates = basename_index.get(citation.path, [])
        existing = [p for p in candidates if p.is_file()]
        if not existing:
            add_stale(citation, "file not found in repo")
            continue
        if citation.max_line is not None and not any(
            citation.max_line <= count_lines(p, line_cache) for p in existing
        ):
            add_stale(
                citation,
                f"cited line {citation.max_line} exceeds file length"
                f" ({max(count_lines(p, line_cache) for p in existing)} lines)",
            )
    logger.info(
        "drift check: %d citation parts checked, %d stale",
        results.citations_checked,
        len(results.stale_citations),
    )


def count_additional_properties_false(node: object) -> int:
    """Count ``"additionalProperties": false`` occurrences (AC-5 gate)."""
    count = 0
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "additionalProperties" and value is False:
                count += 1
            count += count_additional_properties_false(value)
    elif isinstance(node, list):
        for item in node:
            count += count_additional_properties_false(item)
    return count


def main() -> int:
    """Run all checks and print the JSON summary to stdout."""
    schemas = load_schemas()
    registry = build_registry(schemas)
    results = Results()

    meta_validate(schemas, results)
    validate_fixtures(schemas, registry, results)
    validate_real_corpus(schemas, registry, results)
    check_citations(collect_citations(schemas), results)

    for name, contents in schemas.items():
        found = count_additional_properties_false(contents)
        if found:
            results.failures.append(
                {
                    "kind": "additional-properties-false",
                    "schema": name,
                    "detail": f"{found} occurrence(s)",
                }
            )
            logger.error("AC-5 violation: %s sets additionalProperties: false", name)
        results.additional_properties_false += found

    summary = results.summary()
    logger.info("overall: %s", "ok" if summary["ok"] else "FAILED")
    json.dump(summary, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
