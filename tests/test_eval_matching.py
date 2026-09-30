from evals.run_evals import results_match

GOLD = [["Dubai", 100.0], ["Riyadh", 80.5]]


def test_same_result_matches():
    assert results_match(GOLD, ["city", "revenue"], [["Dubai", 100.0], ["Riyadh", 80.5]])


def test_order_names_and_extra_columns_ignored():
    assert results_match(GOLD, ["x", "rev", "city"], [[2, 80.5, "riyadh"], [1, 100.0, "DUBAI"]])


def test_rounding_tolerance():
    assert results_match(GOLD, ["c", "r"], [["Dubai", 100.004], ["Riyadh", 80.5]])


def test_wrong_value_fails():
    assert not results_match(GOLD, ["c", "r"], [["Dubai", 120.0], ["Riyadh", 80.5]])


def test_wrong_row_count_fails():
    assert not results_match(GOLD, ["c", "r"], [["Dubai", 100.0]])


def test_month_label_format_ignored():
    gold = [["2025-01-01", 10.0], ["2025-02-01", 20.0]]
    assert results_match(gold, ["month", "r"], [["Jan 2025", 10.0], ["Feb 2025", 20.0]])


def test_pivoted_answer_accepted():
    gold = [["In-Store", 5282993.7], ["Online", 4874766.58]]
    assert results_match(gold, ["online_revenue", "store_revenue"], [[4874766.58, 5282993.7]])


def test_pivoted_answer_with_wrong_numbers_fails():
    gold = [["In-Store", 5282993.7], ["Online", 4874766.58]]
    assert not results_match(gold, ["a", "b"], [[1.0, 5282993.7]])


def test_single_value_not_treated_as_pivot():
    assert not results_match([[10.0]], ["x"], [[11.0]])