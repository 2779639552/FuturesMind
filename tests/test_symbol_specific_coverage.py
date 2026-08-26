"""`symbol_specific` must cover every variety in VARIETY_METADATA.

Regression test for worklog/2026-08-22-news-missing-3-varieties.md: when the
ZCE pool expanded 8→21 (2026-07-30), the news keyword map was not kept in sync,
so AP/CJ/PK had NO variety-specific keywords and their industry terms (冷库/
套袋/灰枣/油料米…) were silently dropped from news recall. This pins the
coverage so a future variety addition cannot silently degrade news again.
"""

import ast
from pathlib import Path

import pytest

from tradingagents.dataflows.commodity_futures import VARIETY_METADATA

_MODULE = Path(__file__).resolve().parent.parent / "tradingagents" / "dataflows" / "commodity_futures.py"


def _symbol_specific_keys() -> set[str]:
    """Extract the keys of the local `symbol_specific` dict via AST."""
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "symbol_specific":
                    return {k.value for k in node.value.keys}  # type: ignore[union-attr]
    raise AssertionError("symbol_specific dict not found in module source")


@pytest.mark.unit
def test_symbol_specific_covers_all_variety_metadata():
    covered = _symbol_specific_keys()
    expected = set(VARIETY_METADATA)
    missing = expected - covered
    assert not missing, f"varieties missing variety-specific news keywords: {sorted(missing)}"


@pytest.mark.unit
def test_symbol_specific_has_no_orphan_keys():
    covered = _symbol_specific_keys()
    known = set(VARIETY_METADATA)
    orphans = covered - known
    assert not orphans, f"symbol_specific has keys outside VARIETY_METADATA: {sorted(orphans)}"


@pytest.mark.unit
def test_each_symbol_specific_keyword_list_is_nonempty():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "symbol_specific":
                    for key, value in zip(node.value.keys, node.value.values):
                        assert value.elts, f"symbol_specific[{key.value}] is empty"
                    return
