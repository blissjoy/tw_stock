from src.rule_registry import get_registry, implements_rule, reset_registry


def test_implements_rule_registers_function_under_given_ids():
    reset_registry()

    @implements_rule("R-TEST-01", "R-TEST-02")
    def dummy_func():
        return 42

    registry = get_registry()
    assert "R-TEST-01" in registry
    assert "R-TEST-02" in registry
    assert registry["R-TEST-01"][0].endswith("dummy_func")
    assert dummy_func() == 42  # 裝飾器不應該改變函式原本的行為

    reset_registry()


def test_multiple_functions_can_implement_the_same_rule():
    reset_registry()

    @implements_rule("R-TEST-SHARED")
    def func_a():
        pass

    @implements_rule("R-TEST-SHARED")
    def func_b():
        pass

    registry = get_registry()
    assert len(registry["R-TEST-SHARED"]) == 2

    reset_registry()
