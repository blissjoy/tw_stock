from src.data.trading_calendar import parse_holiday_csv


def _sample_csv() -> str:
    return (
        '"115年（西元2026年）辦公日曆表","",""\n'
        '"日期","說明","備註"\n'
        '"1月1日 (四)","中華民國開國紀念日",""\n'
        '"2月28日 (六)","和平紀念日","*"\n'
        '"6月19日 (五)","端午節",""\n'
    )


def test_parse_holiday_csv_extracts_month_day_ignoring_weekday_parens():
    holidays = parse_holiday_csv(_sample_csv(), 2026)
    assert holidays == ["2026-01-01", "2026-02-28", "2026-06-19"]


def test_parse_holiday_csv_returns_empty_for_header_only_content():
    content = '"title","",""\n"日期","說明","備註"\n'
    assert parse_holiday_csv(content, 2026) == []


def test_parse_holiday_csv_returns_empty_for_empty_content():
    assert parse_holiday_csv("", 2026) == []


def test_parse_holiday_csv_skips_rows_with_unparsable_date():
    content = (
        '"title","",""\n'
        '"日期","說明","備註"\n'
        '"格式異常的一列","",""\n'
        '"12月25日 (五)","行憲紀念日",""\n'
    )
    assert parse_holiday_csv(content, 2026) == ["2026-12-25"]
