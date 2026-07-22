"""掃描 ai/zhu-rules/ 底下所有規則檔案，產生機器可讀的規則清單 ai/zhu-rules/_manifest.json。

用途：
- 追蹤每條規則的 Rule ID、分類、可程式化程度、信心等級
- 解析規則內文中的 [[規則名稱]] 交叉引用，建立相依關係圖
- 抓出斷掉的連結（引用了不存在的規則名稱）
- 之後 scripts/check_rule_coverage.py 會拿這份清單去比對程式碼裡實際登記了哪些規則
"""

from __future__ import annotations

import json
import re
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent / "ai" / "zhu-rules"
MANIFEST_PATH = RULES_DIR / "_manifest.json"

FIELD_PATTERN = re.compile(r"^-\s*\*\*(.+?)\*\*:\s*(.*)$", re.MULTILINE)
LINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")

FIELD_KEY_MAP = {
    "Rule ID": "rule_id",
    "名稱": "name",
    "分類": "category",
    "原文與頁碼": "source",
    "解讀": "interpretation",
    "可程式化": "programmable",
    "所需資料": "required_data",
    "計算公式": "formula",
    "參數": "parameters",
    "可回測": "backtestable",
    "信心": "confidence",
    "底層規則依賴": "declared_dependencies",
}


def parse_rule_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fields: dict[str, str] = {}
    for raw_key, value in FIELD_PATTERN.findall(text):
        key = FIELD_KEY_MAP.get(raw_key.strip())
        if key:
            fields[key] = value.strip()

    links = sorted(set(LINK_PATTERN.findall(text)))

    # 「信心」「可程式化」欄位有些規則寫了很長的括號說明（例如「中高（xxx理由）」），
    # 只取開頭的等級詞（到全形/半形括號、頓號、換行為止），完整說明另外存成 note 欄位。
    def split_level_and_note(raw: str) -> tuple[str, str]:
        if not raw:
            return "", ""
        m = re.match(r"^([^（(\n]+)", raw)
        level = m.group(1).strip() if m else raw.strip()
        note = raw[len(level):].strip() if len(raw) > len(level) else ""
        return level, note

    confidence_level, confidence_note = split_level_and_note(fields.get("confidence", ""))
    programmable_level, programmable_note = split_level_and_note(fields.get("programmable", ""))

    return {
        "rule_id": fields.get("rule_id", ""),
        "name": fields.get("name") or path.stem,
        "category": fields.get("category") or path.parent.name,
        "file": str(path.relative_to(RULES_DIR)).replace("\\", "/"),
        "programmable": programmable_level,
        "programmable_note": programmable_note,
        "backtestable": fields.get("backtestable", ""),
        "confidence": confidence_level,
        "confidence_note": confidence_note,
        "referenced_names": links,
        "implementation_status": "not_started",
    }


def build_manifest() -> dict:
    rule_files = sorted(RULES_DIR.glob("*/*.md"))
    entries = [parse_rule_file(p) for p in rule_files]

    # 規則的「名稱」欄位常帶括號補充說明（例如「轉折波定義與取點演算法（5日/10日/20日短中長線）」），
    # 但引用時多半用檔名等級的精簡版本，所以查找鍵同時收錄「名稱全文」與「檔名（不含.md）」。
    name_to_id: dict[str, str] = {}
    for e in entries:
        name_to_id[e["name"]] = e["rule_id"]
        stem = Path(e["file"]).stem
        name_to_id.setdefault(stem, e["rule_id"])

    category_names = {e["category"] for e in entries}
    all_lookup_keys = sorted(name_to_id.keys(), key=len, reverse=True)

    def resolve(link_name: str) -> tuple[str | None, str]:
        """回傳 (rule_id 或 None, 解析方式)。"""
        if link_name in name_to_id:
            return name_to_id[link_name], "exact"
        # 前綴比對：連結文字是某條規則「名稱」或「檔名」的前綴（處理省略括號補充說明的情況）
        for key in all_lookup_keys:
            if key.startswith(link_name) or link_name.startswith(key):
                return name_to_id[key], "prefix"
        if link_name in category_names:
            return None, "category_reference"
        return None, "unresolved"

    broken_links: list[dict] = []
    duplicate_rule_ids: list[str] = []
    seen_ids: set[str] = set()

    for e in entries:
        resolved = []
        for link_name in e["referenced_names"]:
            target_id, how = resolve(link_name)
            if target_id:
                resolved.append(target_id)
            elif how != "category_reference":
                # 真正找不到對應規則的連結（category_reference，例如 [[支撐壓力]] 引用整個分類，不算斷link）
                broken_links.append({"from_rule": e["rule_id"], "from_file": e["file"], "broken_link": link_name})
        e["depends_on"] = sorted(set(resolved))
        del e["referenced_names"]

        if not e["rule_id"]:
            continue
        if e["rule_id"] in seen_ids:
            duplicate_rule_ids.append(e["rule_id"])
        seen_ids.add(e["rule_id"])

    missing_rule_id = [e["file"] for e in entries if not e["rule_id"]]

    by_category: dict[str, int] = {}
    by_confidence: dict[str, int] = {}
    by_programmable: dict[str, int] = {}
    for e in entries:
        by_category[e["category"]] = by_category.get(e["category"], 0) + 1
        by_confidence[e["confidence"]] = by_confidence.get(e["confidence"], 0) + 1
        by_programmable[e["programmable"]] = by_programmable.get(e["programmable"], 0) + 1

    manifest = {
        "total_rules": len(entries),
        "by_category": dict(sorted(by_category.items())),
        "by_confidence": dict(sorted(by_confidence.items())),
        "by_programmable": dict(sorted(by_programmable.items())),
        "quality_issues": {
            "missing_rule_id": missing_rule_id,
            "duplicate_rule_ids": sorted(set(duplicate_rule_ids)),
            "broken_links": broken_links,
        },
        "rules": entries,
    }
    return manifest


def main() -> None:
    manifest = build_manifest()
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"總規則數：{manifest['total_rules']}")
    print(f"寫入：{MANIFEST_PATH}")
    print("\n依分類：")
    for cat, count in manifest["by_category"].items():
        print(f"  {cat}: {count}")
    print("\n依信心等級：")
    for conf, count in manifest["by_confidence"].items():
        print(f"  {conf or '(空白)'}: {count}")
    print("\n依可程式化程度：")
    for prog, count in manifest["by_programmable"].items():
        print(f"  {prog or '(空白)'}: {count}")

    issues = manifest["quality_issues"]
    if issues["missing_rule_id"]:
        print(f"\n⚠️ 缺少 Rule ID 的檔案（{len(issues['missing_rule_id'])}）：")
        for f in issues["missing_rule_id"]:
            print(f"  {f}")
    if issues["duplicate_rule_ids"]:
        print(f"\n⚠️ 重複的 Rule ID（{len(issues['duplicate_rule_ids'])}）：")
        for rid in issues["duplicate_rule_ids"]:
            print(f"  {rid}")
    if issues["broken_links"]:
        print(f"\n⚠️ 無法解析的 [[連結]]（{len(issues['broken_links'])}，可能是分類名稱或真的斷link）：")
        for b in issues["broken_links"]:
            print(f"  {b['from_rule']} ({b['from_file']}) -> [[{b['broken_link']}]]")


if __name__ == "__main__":
    main()
