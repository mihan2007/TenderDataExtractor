import subprocess
import sys

import check_prices as cp
from make_sample_xlsx import build


def test_queries_parsed():
    assert cp.parse_queries('"A" | "B" | “C”') == {"a", "b", "c"}


def test_sample_flags_incomplete_markets(tmp_path):
    path = build(str(tmp_path / "s.xlsx"))
    (total, checked, problems), recheck = cp.check(path, 5)
    assert total == 2 and checked == 2
    by_no = {str(n): probs for n, _, probs in problems}
    assert "1" not in by_no                       # domestic complete, international marker has 6 queries
    assert any("Domestic: found 0 of 3" in p for p in by_no["2"])
    assert {r["No."] for r in recheck} == {"1", "2"}


def test_cli_exit_code(tmp_path):
    path = build(str(tmp_path / "s.xlsx"))
    res = subprocess.run([sys.executable, "tools/check_prices.py", "--xlsx", path], capture_output=True, text=True)
    assert res.returncode == 1 and "PROBLEMS" in res.stdout
