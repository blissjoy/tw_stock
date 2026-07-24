"""從 ai/zhu-rules/ 規則庫的md檔案，依Rule ID查出該條規則的完整說明(名稱/解讀/信心/原文與
頁碼等)，供UI的「個股分析」面板顯示規則說明用。

規則庫本身沒有「Rule ID -> 檔案路徑」的索引（`_manifest.json`只有分類/信心/可程式化的
統計數字，見`scripts/check_rule_coverage.py`），這裡直接掃過整個目錄比對每個檔案開頭的
`- **Rule ID**: ...`這一行來建索引——規則庫只有246個檔案，掃描一次的成本可忽略，不需要
另外維護一份索引檔案跟原始.md檔案內容保持同步的負擔。
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

ZHU_RULES_DIR = Path(__file__).resolve().parent.parent / "ai" / "zhu-rules"

_RULE_ID_LINE = re.compile(r"^- \*\*Rule ID\*\*: (R-[A-Z0-9-]+)\s*$", re.MULTILINE)
_FIELD_LINE = re.compile(r"^- \*\*(.+?)\*\*: (.*)$")


@lru_cache(maxsize=1)
def _build_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for md_path in ZHU_RULES_DIR.rglob("*.md"):
        text = md_path.read_text(encoding="utf-8")
        match = _RULE_ID_LINE.search(text)
        if match is None:
            continue
        fields: dict[str, str] = {}
        for line in text.splitlines():
            field_match = _FIELD_LINE.match(line)
            if field_match:
                fields[field_match.group(1)] = field_match.group(2)
        index[match.group(1)] = fields
    return index


def load_rule_doc(rule_id: str) -> dict[str, str] | None:
    """回傳該Rule ID的欄位字典(名稱/分類/解讀/信心/原文與頁碼...)，查無此規則回傳None。"""
    return _build_index().get(rule_id)
