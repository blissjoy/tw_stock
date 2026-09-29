from src.presentation.industry_groups import INDUSTRY_GROUPS, group_industries


def test_industry_groups_have_no_duplicate_members_across_groups():
    """同一個細項產業別不該同時出現在兩個大分類底下，不然使用者勾兩個大分類時
    checked_items()會出現重複，或篩選邏輯產生混淆。"""
    seen: set[str] = set()
    for members in INDUSTRY_GROUPS.values():
        for m in members:
            assert m not in seen, f"{m} 同時出現在多個大分類"
            seen.add(m)


def test_group_industries_splits_known_and_unknown():
    all_industries = ["半導體業", "金融保險", "一個尚未分類的新產業"]
    grouped, ungrouped = group_industries(all_industries)
    assert grouped["電子類"] == ["半導體業"]
    assert grouped["金融類"] == ["金融保險"]
    assert ungrouped == ["一個尚未分類的新產業"]


def test_group_industries_omits_groups_with_no_matches():
    grouped, ungrouped = group_industries(["半導體業"])
    assert list(grouped.keys()) == ["電子類"]
    assert ungrouped == []


def test_group_industries_empty_input():
    grouped, ungrouped = group_industries([])
    assert grouped == {}
    assert ungrouped == []
