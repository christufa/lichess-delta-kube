from lichess_spark.raw import branch_name, evaluate


def good(rows=100):
    return {"rows": rows, "missing_site": 0, "bad_result": 0, "wrong_month": 0}


def test_all_pass():
    assert all(c.passed for c in evaluate("2013-01", good(), 100))


def test_count_mismatch_fails():
    checks = {c.name: c for c in evaluate("2013-01", good(99), 100)}
    assert not checks["row_count_matches_manifest"].passed
    assert checks["row_count_matches_manifest"].observed == 99


def test_missing_manifest_fails():
    assert not {c.name: c for c in evaluate("2013-01", good(), None)}["row_count_matches_manifest"].passed


def test_bad_values_fail():
    checks = {c.name: c.passed for c in evaluate("2013-01", {**good(), "bad_result": 3, "missing_site": 1}, 100)}
    assert not checks["result_valid"] and not checks["site_present"]


def test_empty_month_fails():
    assert not {c.name: c.passed for c in evaluate("2013-01", good(0), 0)}["not_empty"]


def test_branch_name():
    assert branch_name("2013-01") == "audit_2013_01"
