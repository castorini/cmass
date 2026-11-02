"""The leaderboard page must show exactly what the published results say."""
import unittest

import prepare_inputs  # noqa: F401  (puts the repository root on sys.path)
from scripts.build_leaderboard import PAGE, rows


class LeaderboardTests(unittest.TestCase):
    def test_page_rows_match_results(self):
        self.assertIn("\n".join(rows()), PAGE.read_text())


if __name__ == "__main__":
    unittest.main()
