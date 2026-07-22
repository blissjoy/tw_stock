"""比對 ai/zhu-rules/_manifest.json 與程式碼裡實際登記的規則，回報覆蓋率。

用法：
    python scripts/build_rule_manifest.py   # 先確保 manifest 是最新的
    python scripts/check_rule_coverage.py   # 再跑這支比對覆蓋率

這是 ai/IMPLEMENTATION-PLAN.md 第三節「怎麼確保246條規則都有被考量到」講的
機器檢查機制：不靠人腦記憶，程式碼裡每個函式用 @implements_rule("R-XX-01")
登記對應規則，這支腳本匯入 src/ 底下所有模組讓裝飾器跑過一次，再跟 manifest
交叉比對，找出「還沒做」的規則清單。
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MANIFEST_PATH = ROOT / "ai" / "zhu-rules" / "_manifest.json"


def import_all_src_modules() -> None:
    """遞迴匯入 src/ 底下所有模組，讓 @implements_rule 裝飾器全部執行過一次。"""
    import src

    for module_info in pkgutil.walk_packages(src.__path__, prefix="src."):
        importlib.import_module(module_info.name)


def main() -> None:
    if not MANIFEST_PATH.exists():
        print(f"找不到 {MANIFEST_PATH}，請先執行 scripts/build_rule_manifest.py")
        raise SystemExit(1)

    import_all_src_modules()
    from src.rule_registry import get_registry

    registry = get_registry()

    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)

    rules = manifest["rules"]
    total = len(rules)
    implemented = [r for r in rules if r["rule_id"] and r["rule_id"] in registry]
    not_implemented = [r for r in rules if not (r["rule_id"] and r["rule_id"] in registry)]

    print(f"總規則數：{total}")
    print(f"已登記實作：{len(implemented)} ({len(implemented) / total:.1%})")
    print(f"尚未實作：{len(not_implemented)}")

    cat_total: dict[str, int] = defaultdict(int)
    cat_done: dict[str, int] = defaultdict(int)
    for r in rules:
        cat_total[r["category"]] += 1
        if r["rule_id"] in registry:
            cat_done[r["category"]] += 1

    print("\n依分類覆蓋率：")
    for cat in sorted(cat_total):
        print(f"  {cat}: {cat_done[cat]}/{cat_total[cat]}")

    # 找出重複登記在多個 rule_id 下但函式數量異常多的情況（可能是複製貼上打錯 Rule ID）
    over_registered = {rid: impls for rid, impls in registry.items() if len(impls) > 3}
    if over_registered:
        print("\n⚠️ 同一個 Rule ID 被超過3個函式登記（請確認是否為打錯 Rule ID）：")
        for rid, impls in over_registered.items():
            print(f"  {rid}: {impls}")

    # 登記表裡出現、但 manifest 裡根本沒有這個 Rule ID 的情況（打錯字或規則已被刪除改名）
    manifest_ids = {r["rule_id"] for r in rules if r["rule_id"]}
    unknown_ids = sorted(set(registry) - manifest_ids)
    if unknown_ids:
        print(f"\n⚠️ 程式碼登記了 manifest 裡找不到的 Rule ID（{len(unknown_ids)}）：")
        for rid in unknown_ids:
            print(f"  {rid}: {registry[rid]}")

    if not_implemented:
        print(f"\n尚未實作清單（共{len(not_implemented)}條）：")
        for r in not_implemented:
            conf = r["confidence"] or "?"
            prog = r["programmable"] or "?"
            print(f"  {r['rule_id'] or '(無ID)':12s} {r['name']:40s} [{r['category']}] 信心={conf} 可程式化={prog}")


if __name__ == "__main__":
    main()
