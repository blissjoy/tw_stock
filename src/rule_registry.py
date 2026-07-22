"""規則實作登記機制。

每個實作規則庫（ai/zhu-rules/）裡某條規則的函式，用 @implements_rule("R-XX-01") 標記。
`scripts/check_rule_coverage.py` 會匯入所有程式碼模組讓裝飾器執行、把登記表跟
ai/zhu-rules/_manifest.json 比對，算出 246 條規則裡有幾條已經被實作，哪些還沒有。

一個函式可以同時實作多條規則（例如一個「均線多頭排列」函式同時滿足
R-MA-08 這條規則），也允許多個函式共同實作同一條規則（例如一條規則拆成
「計算」+「判斷」兩個函式）。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, TypeVar

F = TypeVar("F", bound=Callable)

_REGISTRY: dict[str, list[str]] = defaultdict(list)


def implements_rule(*rule_ids: str) -> Callable[[F], F]:
    """裝飾器：標記某函式實作了哪一條（或哪幾條）規則庫的 Rule ID。"""

    def decorator(func: F) -> F:
        qualname = f"{func.__module__}.{func.__qualname__}"
        for rule_id in rule_ids:
            if qualname not in _REGISTRY[rule_id]:
                _REGISTRY[rule_id].append(qualname)
        return func

    return decorator


def get_registry() -> dict[str, list[str]]:
    """回傳目前登記表的複本：{rule_id: [實作函式的完整路徑, ...]}。"""
    return {rule_id: list(impls) for rule_id, impls in _REGISTRY.items()}


def reset_registry() -> None:
    """測試用：清空登記表。"""
    _REGISTRY.clear()
