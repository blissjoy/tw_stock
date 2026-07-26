"""重新產生 ai/RULE_STATUS.md：246條規則庫的接入狀態總表。

用法：
    python scripts/build_rule_manifest.py    # 先確保 manifest 是最新的
    python scripts/build_rule_status.py      # 再跑這支重新產生RULE_STATUS.md

跟 scripts/check_rule_coverage.py 的差別：check_rule_coverage.py 只回答「這條規則有沒有
被某個函式標記@implements_rule」(目前246條全數是"有")；這支腳本進一步回答「那個函式有沒有
被使用者實際看得到的路徑呼叫」，區分「已接入」／「已實作但只是底層building block」／
「已實作但沒有被呼叫」三種狀態。

機制：
1. 匯入 src/ 底下所有模組，讓 @implements_rule 裝飾器登記完成(跟check_rule_coverage.py
   同一套機制)。
2. 對每條規則的登記函式，逐一檢查函式名稱是不是以「呼叫」語法(`funcname(`)出現在三個
   使用者真的看得到訊號的進入點檔案(rule_scan.py/daily_screener.py/latest_day_summary.py)
   裡，或是出現在兩個一定會被前三者呼叫的「組裝層」檔案(trend_state.py組裝R-TREND-01/
   03/04、chart_overlays.py組裝R-LINE-01~06/11/12)裡。
3. 從 ai/PLAN.md 用bullet(`- R-XX-YY(...)：...`)的格式擷取每條規則的排除/延後理由文字，
   供「已實作未接入」的規則附上說明。

⚠️ 機制的已知限制(所以下面留了兩份手動覆寫清單，需要人工維護)：
- 有些函式是「傳函式本身當參數」(higher-order function)被共用邏輯呼叫，原始碼裡看不到
  `funcname(`這種直接呼叫語法(例如R-MA-22~29的單/雙均線策略函式)。
- 有些規則是用不同名稱重新實作同一套邏輯，不是直接呼叫已標記的函式(例如R-CLASSIC-24)。
- PLAN.md裡少數bullet的Rule ID用括號各自分隔而非用/、共用一個標題(例如"R-SCREEN-17(...)/
  R-SCREEN-18(...)")，自動擷取抓不到後面那個ID，需要另外補在REASON_OVERRIDE。

每次新增這類「機制掃不到但其實已接入/已排除」的規則時，把rule_id加進下面對應的清單，
不要每次都重新人工核對一遍全部246條。
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MANIFEST_PATH = ROOT / "ai" / "zhu-rules" / "_manifest.json"
PLAN_PATH = ROOT / "ai" / "PLAN.md"
OUTPUT_PATH = ROOT / "ai" / "RULE_STATUS.md"

# 三個「使用者真的看得到訊號」的進入點檔案。
ENTRY_FILES = {
    "rule_scan.py": ROOT / "src/screener/rule_scan.py",
    "daily_screener.py": ROOT / "src/screener/daily_screener.py",
    "latest_day_summary.py": ROOT / "src/patterns/latest_day_summary.py",
}
# 一定會被上面三者呼叫、本身沒有掛@implements_rule但內部大量呼叫已標記函式的組裝層檔案。
COMPOSITION_FILES = {
    "trend_state.py(組裝層)": ROOT / "src/patterns/trend_state.py",
    "chart_overlays.py(組裝層)": ROOT / "src/patterns/chart_overlays.py",
}

# 函式本身是計算公式/取點演算法/狀態分類器/字串對照表，不是獨立的「今天有沒有觸發」訊號，
# 即使被大量呼叫也不算「已接入」——見ai/PLAN.md裡明確寫「建構演算法」/「純數值計算公式」/
# 「狀態非訊號」/「純字串對照表」/「間接接入」的段落。
BUILDING_BLOCK_OVERRIDE = {
    "R-MA-01", "R-MA-02", "R-MA-03", "R-MA-21",
    "R-INDICATOR-01", "R-INDICATOR-10", "R-INDICATOR-13", "R-INDICATOR-20",
    "R-TREND-01",
    "R-LINE-01", "R-LINE-02", "R-LINE-03", "R-LINE-04", "R-LINE-05", "R-LINE-06",
}

# 機制掃描判定"未接入"、但人工核對過確認其實已經接入的規則(見上方docstring的已知限制)。
MANUAL_WIRED_OVERRIDE = {
    "R-CLASSIC-24": "daily_screener.py",
    "R-MA-22": "daily_screener.py",
    "R-MA-23": "daily_screener.py",
    "R-MA-24": "daily_screener.py",
    "R-MA-25": "daily_screener.py",
    "R-MA-28": "daily_screener.py",
    "R-MA-29": "daily_screener.py",
}

# PLAN.md自動擷取抓不到的理由，手動補上(見上方docstring的已知限制第3點)。
REASON_OVERRIDE = {
    "R-SCREEN-18": (
        "R-SCREEN-17(做多環境四大前提規則)/R-SCREEN-18(做空環境四大前提規則，鏡射)："
        "需要大盤指數、各類股指數強度排名、個股在類股內的相對強度排名，是「由上而下」的"
        "市場環境濾網，跟這個專案目前逐檔股票分析的架構層級不同——需要先有大盤/類股指數"
        "資料與跨股票排名的基礎設施才能接，超出「幫單一股票加一條訊號」的範圍。"
    ),
}

STATUS_LABEL = {
    "wired": "✅ 已接入",
    "building_block": "🔧 建構元件/間接使用",
    "implemented_not_wired": "⏸️ 已實作未接入",
    "not_implemented": "❌ 未實作",
}


def import_all_src_modules() -> None:
    import src

    for module_info in pkgutil.walk_packages(src.__path__, prefix="src."):
        importlib.import_module(module_info.name)


def _strip_import_block(text: str) -> str:
    """去掉檔案開頭的import區塊，避免`from x import (funcname, ...)`裡的函式名稱
    被誤判成「呼叫」。"""
    body_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("from ") or stripped.startswith("import ") or (
            not body_lines and (stripped == "" or stripped.startswith(")") or stripped.startswith("#"))
        ):
            continue
        body_lines.append(line)
    return "\n".join(body_lines)


def build_wiring_index() -> tuple[dict[str, str], dict[str, str]]:
    entry_bodies = {label: _strip_import_block(path.read_text(encoding="utf-8")) for label, path in ENTRY_FILES.items()}
    comp_bodies = {label: _strip_import_block(path.read_text(encoding="utf-8")) for label, path in COMPOSITION_FILES.items()}
    return entry_bodies, comp_bodies


def find_wiring(qualnames: list[str], entry_bodies: dict[str, str], comp_bodies: dict[str, str]) -> tuple[list[str], list[str]]:
    entry_hits, comp_hits = [], []
    for qualname in qualnames:
        func_name = qualname.rsplit(".", 1)[-1]
        pattern = re.compile(r"\b" + re.escape(func_name) + r"\s*\(")
        for label, body in entry_bodies.items():
            if pattern.search(body):
                entry_hits.append(label)
        for label, body in comp_bodies.items():
            if pattern.search(body):
                comp_hits.append(label)
    return sorted(set(entry_hits)), sorted(set(comp_hits))


def expand_ids(heading: str) -> list[str]:
    """從bullet標題抓出所有Rule ID，支援"R-GAP-03/04"、"R-LINE-01/02/03"這種同分類簡寫。"""
    ids = []
    for m in re.finditer(r"R-([A-Z]+)-(\d+)((?:[/、]\d+)*)", heading):
        prefix, first_num, rest = m.group(1), m.group(2), m.group(3)
        ids.append(f"R-{prefix}-{first_num}")
        for extra in re.findall(r"\d+", rest):
            ids.append(f"R-{prefix}-{extra}")
    return ids


def build_reason_index() -> dict[str, str]:
    plan_text = PLAN_PATH.read_text(encoding="utf-8")
    bullet_pattern = re.compile(r"^- (.*?)(?=\n- |\n\n|\Z)", re.M | re.S)
    by_rule: dict[str, str] = {}
    for b in bullet_pattern.findall(plan_text):
        heading = b.split("(", 1)[0].split("（", 1)[0]
        ids_in_heading = expand_ids(heading)
        if not ids_in_heading:
            continue
        text = re.sub(r"\s+", "", "- " + b)
        if len(text) > 400:
            text = text[:400] + "…"
        for rid in ids_in_heading:
            by_rule[rid] = text  # 後面出現的覆蓋前面，取最新判斷
    by_rule.update(REASON_OVERRIDE)
    return by_rule


def main() -> None:
    if not MANIFEST_PATH.exists():
        print(f"找不到 {MANIFEST_PATH}，請先執行 scripts/build_rule_manifest.py")
        raise SystemExit(1)

    import_all_src_modules()
    from src.rule_registry import get_registry

    registry = get_registry()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entry_bodies, comp_bodies = build_wiring_index()
    reasons = build_reason_index()

    rows = []
    for r in manifest["rules"]:
        rule_id = r["rule_id"]
        qualnames = registry.get(rule_id, [])
        implemented = len(qualnames) > 0
        entry_hits, comp_hits = find_wiring(qualnames, entry_bodies, comp_bodies) if implemented else ([], [])
        conf_match = re.match(r"(\d+)", r.get("confidence", ""))
        rows.append({
            "rule_id": rule_id,
            "name": r.get("name", ""),
            "category": r.get("category", ""),
            "confidence_num": int(conf_match.group(1)) if conf_match else -1,
            "confidence_str": r.get("confidence", ""),
            "implemented": implemented,
            "entry_hits": entry_hits,
            "comp_hits": comp_hits,
            "impl_functions": [q.rsplit(".", 1)[-1] for q in qualnames],
        })
    rows.sort(key=lambda x: -x["confidence_num"])

    def status_of(r: dict) -> str:
        rid = r["rule_id"]
        if rid in BUILDING_BLOCK_OVERRIDE:
            return "building_block"
        if rid in MANUAL_WIRED_OVERRIDE or r["entry_hits"] or r["comp_hits"]:
            return "wired"
        if r["implemented"]:
            return "implemented_not_wired"
        return "not_implemented"

    counts: dict[str, int] = {}
    for r in rows:
        counts[status_of(r)] = counts.get(status_of(r), 0) + 1

    lines = []
    lines.append("# 246條規則接入狀態總覽")
    lines.append("")
    lines.append("> 這份文件是`ai/zhu-rules/`246條規則庫的接入狀態總表，跟`ai/PLAN.md`的差別是：")
    lines.append("> PLAN.md是「按時間順序記錄做了什麼決定、為什麼」的敘事型開發日誌，這份文件是")
    lines.append("> 「此時此刻246條規則各自的狀態」的**橫向總表**，方便快速查詢「這條規則到底有沒有")
    lines.append("> 接」而不用整份PLAN.md逐段搜尋。**這份文件是半自動產生(見`scripts/build_rule_")
    lines.append("> status.py`)+手動核對修正產生的，不是每次都重新生成，需要人工維護——見文件")
    lines.append("> 最後的維護方式**。")
    lines.append("")
    lines.append("## 狀態定義")
    lines.append("")
    lines.append("- **✅ 已接入**：函式被`src/screener/rule_scan.py`(個股分析面板「今天觸發的訊號」)、")
    lines.append("  `src/screener/daily_screener.py`(每日選股候選清單)或`src/patterns/latest_day_")
    lines.append("  summary.py`(最新交易日K棒/量價分析面板)三者之一實際呼叫，使用者看得到這條規則")
    lines.append("  產生的訊號文字。含三種情況：直接呼叫、透過`trend_state.py`/`chart_overlays.py`")
    lines.append("  這兩個一定會被前三者呼叫的組裝層間接呼叫(例如R-TREND-03/04)、或用不同名稱")
    lines.append("  重新實作同一套邏輯而非直接呼叫已標記函式(例如R-CLASSIC-24)。")
    lines.append("- **🔧 建構元件/間接使用**：函式本身是其他規則依賴的計算公式、取點演算法、狀態")
    lines.append("  分類器或字串對照表(例如均線計算、轉折點取點、切線畫法)，雖然被大量呼叫，但")
    lines.append("  不是「今天有沒有觸發」的獨立訊號，不會單獨產生一則使用者看得到的通知文字。")
    lines.append("- **⏸️ 已實作未接入**：Python函式已經寫好(`@implements_rule`已登記)，但沒有被")
    lines.append("  上述路徑呼叫，使用者目前看不到這條規則的訊號。「說明」欄若有內容，是從")
    lines.append("  `ai/PLAN.md`裡對應段落擷取的排除/延後理由(逐字擷取，可能是這條規則不只一次")
    lines.append("  被討論時最後一次寫下的判斷)；沒有內容代表這條規則信心<80分，不在2026-07-26")
    lines.append("  「114條都接上」批次的個別評估範圍內，還沒有人工核對過排除理由。")
    lines.append("- **❌ 未實作**：目前0條，246條規則全部至少有一個Python函式實作(見")
    lines.append("  `scripts/check_rule_coverage.py`)。保留這個狀態分類是為了未來如果規則庫擴充")
    lines.append("  (例如新增書籍章節)時這份文件的分類架構不用重新設計。")
    lines.append("")
    lines.append("## 統計")
    lines.append("")
    lines.append(f"- 規則總數：{len(rows)}")
    for key in ("wired", "building_block", "implemented_not_wired", "not_implemented"):
        lines.append(f"- {STATUS_LABEL[key]}：{counts.get(key, 0)}")
    lines.append("")
    lines.append("## 規則列表(依信心分數降冪排序)")
    lines.append("")
    lines.append("| Rule ID | 名稱 | 分類 | 信心 | 狀態 | 接入位置／說明 |")
    lines.append("|---|---|---|---|---|---|")

    for r in rows:
        rid = r["rule_id"]
        s = status_of(r)
        label = STATUS_LABEL[s]
        if s == "wired":
            if rid in MANUAL_WIRED_OVERRIDE:
                detail = MANUAL_WIRED_OVERRIDE[rid]
            elif r["entry_hits"]:
                detail = "、".join(r["entry_hits"])
            else:
                detail = "、".join(r["comp_hits"]) + "(間接觸發)"
        elif s == "building_block":
            detail = "、".join(r["impl_functions"][:3])
        elif s == "implemented_not_wired":
            reason = reasons.get(rid)
            if reason:
                reason = reason.lstrip("-")
                if len(reason) > 120:
                    reason = reason[:120] + "…"
                detail = reason
            else:
                detail = "(信心<80分，未個別評估)"
        else:
            detail = ""
        name = r["name"].replace("|", "\\|")
        detail = detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {rid} | {name} | {r['category']} | {r['confidence_str']} | {label} | {detail} |")

    lines.append("")
    lines.append("## 維護方式")
    lines.append("")
    lines.append("這份文件不是自動生成後就一勞永逸——**每次調整任何規則的接入狀態(新接上、改寫")
    lines.append("wiring、重新排除)，都要回來更新這份文件對應的那一列，跟`ai/PLAN.md`一起維護，")
    lines.append("不要只更新其中一份**。`ai/PLAN.md`負責記錄「為什麼」跟時間軸，這份文件負責當下")
    lines.append("的「是什麼」——兩份文件的角色不同，缺一不可。若批次調整較大，也可以重新執行")
    lines.append("`python scripts/build_rule_status.py`整份重新產生，但要先檢查`BUILDING_BLOCK_")
    lines.append("OVERRIDE`/`MANUAL_WIRED_OVERRIDE`/`REASON_OVERRIDE`這三份手動清單是否需要更新，")
    lines.append("否則機制的已知限制(見本檔案docstring)會讓重新產生的結果跟人工核對過的版本不一致。")

    OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    # 終端機摘要刻意不用emoji(Windows主控台預設cp950編碼會直接UnicodeEncodeError崩潰)，
    # emoji只留在寫入檔案的內容裡，檔案是用encoding="utf-8"明確寫入，不受主控台編碼影響。
    plain_label = {
        "wired": "已接入",
        "building_block": "建構元件/間接使用",
        "implemented_not_wired": "已實作未接入",
        "not_implemented": "未實作",
    }
    print(f"寫入 {OUTPUT_PATH}，共{len(rows)}條規則")
    for key in ("wired", "building_block", "implemented_not_wired", "not_implemented"):
        print(f"  {plain_label[key]}：{counts.get(key, 0)}")


if __name__ == "__main__":
    main()
