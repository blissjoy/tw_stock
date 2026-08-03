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

# 「原文與頁碼」欄位文字裡引用的書籍筆記檔案存放位置——2026-08-04新增，供resolve_
# reference_files()把「原文與頁碼」裡提到的檔名還原成真正的檔案路徑。zhu-rules只會
# 引用朱家泓自己那本書的筆記(ai/ebook-summary/)，但檔名慣例(P##-C#-標題.md)跟陳家豐
# 那本書的筆記(ai/ebook-summary-chen/)剛好一樣，這裡兩個資料夾都找，找不到才視為
# 引用有誤(理論上不該發生)，不是只信任zhu-rules一定只指向ebook-summary/。
EBOOK_SUMMARY_DIRS = [
    Path(__file__).resolve().parent.parent / "ai" / "ebook-summary",
    Path(__file__).resolve().parent.parent / "ai" / "ebook-summary-chen",
]

_RULE_ID_LINE = re.compile(r"^- \*\*Rule ID\*\*: (R-[A-Z0-9-]+)\s*$", re.MULTILINE)
_FIELD_LINE = re.compile(r"^- \*\*(.+?)\*\*: (.*)$")
# 書籍筆記檔名慣例：P##-C#[a-z]?-任意標題文字.md(見ai/ebook-summary/的既有檔案)，標題
# 部分可能混雜中英文/數字，用\w(含底線)+中文字區間+連字號涵蓋，不含括號/頓號/全形標點
# 這類「原文與頁碼」欄位裡用來分隔說明文字的符號，讓比對能在檔名結束處自然停下來。
MD_FILENAME_PATTERN = re.compile(r"[\w一-鿿-]+\.md")


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


def find_ebook_summary_file(filename: str) -> Path | None:
    """依檔名(不含路徑，例如"P03-C5-不同K線組合的意義.md")在EBOOK_SUMMARY_DIRS依序
    尋找實際存在的檔案，都找不到回傳None。resolve_reference_files()跟desktop/
    main_window.py的連結點擊處理共用同一份查找邏輯，不要各自維護一份。
    """
    for base_dir in EBOOK_SUMMARY_DIRS:
        candidate = base_dir / filename
        if candidate.exists():
            return candidate
    return None


def resolve_reference_files(reference: str) -> list[tuple[str, Path]]:
    """從「原文與頁碼」欄位文字裡找出所有引用的書籍筆記檔名，回傳[(檔名, 實際完整
    路徑), ...]——2026-08-04新增，供UI把「原文與頁碼」裡的檔名變成可點擊連結、開
    新視窗閱讀該份筆記(見desktop/main_window.py的_open_rule_reference_window())。

    純函式：只負責「文字裡找出檔名、還原成真正存在的路徑」這個中性判斷，不管UI要
    怎麼呈現(連結樣式、視窗長相)——跟rule_docs.py其餘函式一樣不依賴任何UI框架。

    同一個檔名在文字裡出現多次只回傳一次(依第一次出現的順序)；找不到對應實體檔案
    的檔名(理論上不該發生，通常代表規則庫文字本身筆誤)不會出現在回傳結果裡，呼叫端
    没找到就不用把那段文字做成連結，比連到不存在的檔案安全。
    """
    seen: set[str] = set()
    result: list[tuple[str, Path]] = []
    for filename in MD_FILENAME_PATTERN.findall(reference):
        if filename in seen:
            continue
        seen.add(filename)
        path = find_ebook_summary_file(filename)
        if path is not None:
            result.append((filename, path))
    return result
