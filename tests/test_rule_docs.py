from src import rule_docs
from src.rule_docs import load_rule_doc, parse_confidence, resolve_reference_files


def test_load_rule_doc_returns_fields_for_known_rule_id():
    doc = load_rule_doc("R-TREND-14")
    assert doc is not None
    assert "解讀" in doc
    assert doc["信心"].startswith("92")


def test_load_rule_doc_returns_none_for_unknown_rule_id():
    assert load_rule_doc("R-DOES-NOT-EXIST-99") is None


def test_load_rule_doc_finds_chen_rules_directory():
    """2026-08-04新增ai/chen-rules/(陳家豐書中規則，目前只有R-CHIP-01/02兩條)，
    索引器要能同時認得zhu-rules跟chen-rules兩個目錄，不是只看zhu-rules。"""
    doc = load_rule_doc("R-CHIP-01")
    assert doc is not None
    assert doc["名稱"] == "投信連續買超觀察"
    assert doc["信心"].startswith("80")


def test_parse_confidence_extracts_leading_number():
    assert parse_confidence("R-TREND-14") == 92
    assert parse_confidence("R-CHIP-02") == 85


def test_parse_confidence_returns_none_for_unknown_rule_id():
    assert parse_confidence("R-DOES-NOT-EXIST-99") is None


def test_resolve_reference_files_extracts_single_filename(monkeypatch, tmp_path):
    (tmp_path / "P03-C5-不同K線組合的意義.md").write_text("# 測試", encoding="utf-8")
    monkeypatch.setattr(rule_docs, "EBOOK_SUMMARY_DIRS", [tmp_path])

    result = resolve_reference_files(
        "P03-C5-不同K線組合的意義.md（上冊 p.229-231；原書3-5節「一、繼續看漲的K線組合」型態2）"
    )

    assert result == [("P03-C5-不同K線組合的意義.md", tmp_path / "P03-C5-不同K線組合的意義.md")]


def test_resolve_reference_files_extracts_multiple_filenames_from_prose(monkeypatch, tmp_path):
    """有些「原文與頁碼」欄位是一段文字裡引用了好幾份筆記檔案(用反引號跟中文連接詞
    分隔)，不是單純「檔名開頭+括號說明」這種簡單格式，要能全部抓出來，依出現順序。"""
    for name in ["P12-C4b-18種K線空轉多祕笈圖.md", "P12-C4a-15種K線多轉空祕笈圖.md", "P03-C5-不同K線組合的意義.md"]:
        (tmp_path / name).write_text("# 測試", encoding="utf-8")
    monkeypatch.setattr(rule_docs, "EBOOK_SUMMARY_DIRS", [tmp_path])
    reference = (
        "主要對應 `P12-C4b-18種K線空轉多祕笈圖.md` 第8種「紅黑紅上漲圖」與 "
        "`P12-C4a-15種K線多轉空祕笈圖.md` 第11種「黑紅黑續跌圖」。"
        "本次已重新查閱 `P03-C5-不同K線組合的意義.md`（上冊 p.226-241）確認。"
    )

    result = resolve_reference_files(reference)

    assert [name for name, _ in result] == [
        "P12-C4b-18種K線空轉多祕笈圖.md", "P12-C4a-15種K線多轉空祕笈圖.md", "P03-C5-不同K線組合的意義.md",
    ]


def test_resolve_reference_files_dedups_repeated_filename(monkeypatch, tmp_path):
    (tmp_path / "P03-C1-K線起源與基本概念.md").write_text("# 測試", encoding="utf-8")
    monkeypatch.setattr(rule_docs, "EBOOK_SUMMARY_DIRS", [tmp_path])
    reference = "P03-C1-K線起源與基本概念.md（p.125-128）；P03-C1-K線起源與基本概念.md（p.150-153）"

    result = resolve_reference_files(reference)

    assert len(result) == 1


def test_resolve_reference_files_skips_filenames_with_no_matching_file(monkeypatch, tmp_path):
    monkeypatch.setattr(rule_docs, "EBOOK_SUMMARY_DIRS", [tmp_path])

    result = resolve_reference_files("P99-C9-不存在的檔案.md（p.1）")

    assert result == []


def test_resolve_reference_files_falls_back_to_second_directory(monkeypatch, tmp_path):
    """檔案不在第一個資料夾時要繼續找第二個資料夾(對應真實情境：ai/ebook-summary/
    找不到才找ai/ebook-summary-chen/)，不是找到第一個資料夾沒有就直接放棄。"""
    dir1 = tmp_path / "dir1"
    dir2 = tmp_path / "dir2"
    dir1.mkdir()
    dir2.mkdir()
    (dir2 / "P01-C1-從籌碼面看出主力動態.md").write_text("# 測試", encoding="utf-8")
    monkeypatch.setattr(rule_docs, "EBOOK_SUMMARY_DIRS", [dir1, dir2])

    result = resolve_reference_files("P01-C1-從籌碼面看出主力動態.md（p.22-26）")

    assert result == [("P01-C1-從籌碼面看出主力動態.md", dir2 / "P01-C1-從籌碼面看出主力動態.md")]


def test_resolve_reference_files_works_against_real_ebook_summary_directory():
    """端到端驗證：真實的規則庫檔案跟真實的ai/ebook-summary/目錄要能對得上，不是
    只在測試用的假目錄裡驗證得過。"""
    doc = load_rule_doc("R-CANDLE-27")
    assert doc is not None

    result = resolve_reference_files(doc["原文與頁碼"])

    assert len(result) == 1
    filename, path = result[0]
    assert filename == "P03-C5-不同K線組合的意義.md"
    assert path.exists()
