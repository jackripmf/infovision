import csv
import tempfile
import unittest
from datetime import date
from pathlib import Path

import stock


HEADER = ["item_id", "location_id", "trans_date", "qty", "cost_amount"]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target, delimiter=";", lineterminator="\n")
        writer.writerow(HEADER)
        writer.writerows(rows)


class StockCalculationTests(unittest.TestCase):
    def test_aggregates_duplicates_carries_days_and_adds_new_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initial = root / "stock" / "stock_2025_04_30.csv"
            movements = root / "invent_trans" / "invent_trans_2025_05.csv"
            output = root / "result"
            write_csv(initial, [["A", "L1", "2025-04-30", "10", "100.25"]])
            write_csv(movements, [
                ["A", "L1", "2025-05-01", "-2", "-20.10"],
                ["A", "L1", "2025-05-01", "0.5", "5.05"],
                ["B", "L2", "2025-05-03", "3.25", "12.30"],
            ])
            written = stock.calculate(initial, [movements], output)
            self.assertEqual([p.name for p in written], [
                "stock_2025_05_01.csv", "stock_2025_05_02.csv", "stock_2025_05_03.csv"
            ])

    def test_target_date_writes_only_requested_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initial = root / "stock_2025_04_30.csv"
            movements = root / "invent_trans_2025_05.csv"
            output = root / "result"
            write_csv(initial, [["A", "L", "2025-04-30", "1", "2"]])
            write_csv(movements, [["A", "L", "2025-05-03", "2", "4"]])
            written = stock.calculate(initial, [movements], output, target_date=date(2025, 5, 2))
            self.assertEqual([p.name for p in written], ["stock_2025_05_02.csv"])

    def test_rejects_duplicate_initial_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stock_2025_04_30.csv"
            write_csv(path, [
                ["A", "L", "2025-04-30", "1", "2"],
                ["A", "L", "2025-04-30", "3", "4"],
            ])
            with self.assertRaisesRegex(ValueError, "повтор ключа"):
                stock.load_initial_stock(path)


if __name__ == "__main__":
    unittest.main()
