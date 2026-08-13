"""Расчёт ежедневных товарных остатков по начальным остаткам и движениям."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


PATH_SOURCE = Path(__file__).resolve().parent
PATH_TRANS = PATH_SOURCE / "invent_trans"
PATH_STOCK = PATH_SOURCE / "stock"

COLUMNS = ("item_id", "location_id", "trans_date", "qty", "cost_amount")
KEY_COLUMNS = ("item_id", "location_id")
STOCK_FILE_RE = re.compile(r"stock_(\d{4}_\d{2}_\d{2})\.csv$")

StockKey = tuple[str, str]
StockValue = list[Decimal]
State = dict[StockKey, StockValue]
DailyMovements = dict[date, dict[StockKey, StockValue]]


def parse_date(value: str, *, source: Path, row_number: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"{source}: строка {row_number}: некорректная дата {value!r}"
        ) from exc


def parse_decimal(value: str, *, field: str, source: Path, row_number: int) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(
            f"{source}: строка {row_number}: поле {field} не является числом: {value!r}"
        ) from exc
    if not result.is_finite():
        raise ValueError(
            f"{source}: строка {row_number}: поле {field} должно быть конечным числом"
        )
    return result


def read_rows(path: Path) -> Iterable[tuple[int, dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter=";")
        if reader.fieldnames != list(COLUMNS):
            raise ValueError(
                f"{path}: ожидаются столбцы {list(COLUMNS)}, получены {reader.fieldnames}"
            )
        for row_number, row in enumerate(reader, start=2):
            if any(row[column] is None or row[column] == "" for column in COLUMNS):
                raise ValueError(f"{path}: строка {row_number}: обнаружено пустое поле")
            yield row_number, row


def find_initial_stock(stock_dir: Path) -> Path:
    candidates: list[tuple[date, Path]] = []
    for path in stock_dir.glob("stock_*.csv"):
        match = STOCK_FILE_RE.fullmatch(path.name)
        if match:
            candidates.append((date.fromisoformat(match.group(1).replace("_", "-")), path))
    if not candidates:
        raise FileNotFoundError(f"В {stock_dir} не найден файл stock_YYYY_MM_DD.csv")
    return min(candidates)[1]


def load_initial_stock(path: Path) -> tuple[date, State]:
    state: State = {}
    stock_date: date | None = None
    for row_number, row in read_rows(path):
        row_date = parse_date(row["trans_date"], source=path, row_number=row_number)
        if stock_date is None:
            stock_date = row_date
        elif row_date != stock_date:
            raise ValueError(f"{path}: начальный остаток содержит несколько дат")

        key = (row["item_id"], row["location_id"])
        if key in state:
            raise ValueError(f"{path}: строка {row_number}: повтор ключа {key}")
        state[key] = [
            parse_decimal(row["qty"], field="qty", source=path, row_number=row_number),
            parse_decimal(
                row["cost_amount"], field="cost_amount", source=path, row_number=row_number
            ),
        ]

    if stock_date is None:
        raise ValueError(f"{path}: файл начальных остатков пуст")
    return stock_date, state


def load_movements(path: Path, *, after: date) -> DailyMovements:
    movements: DailyMovements = defaultdict(dict)
    for row_number, row in read_rows(path):
        trans_date = parse_date(row["trans_date"], source=path, row_number=row_number)
        if trans_date <= after:
            raise ValueError(
                f"{path}: строка {row_number}: движение {trans_date} не позже "
                f"начального остатка {after}"
            )
        key = (row["item_id"], row["location_id"])
        totals = movements[trans_date].setdefault(key, [Decimal(0), Decimal(0)])
        totals[0] += parse_decimal(
            row["qty"], field="qty", source=path, row_number=row_number
        )
        totals[1] += parse_decimal(
            row["cost_amount"], field="cost_amount", source=path, row_number=row_number
        )
    return dict(movements)


def decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def write_stock(path: Path, stock_date: date, state: State) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target, delimiter=";", lineterminator="\n")
        writer.writerow(COLUMNS)
        for (item_id, location_id), (qty, cost_amount) in sorted(state.items()):
            writer.writerow(
                (
                    item_id,
                    location_id,
                    stock_date.isoformat(),
                    decimal_text(qty),
                    decimal_text(cost_amount),
                )
            )
    temporary.replace(path)


def date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def calculate(
    initial_stock: Path,
    movement_files: list[Path],
    output_dir: Path,
    target_date: date | None = None,
) -> list[Path]:
    current_date, state = load_initial_stock(initial_stock)
    written: list[Path] = []

    for movement_file in movement_files:
        daily = load_movements(movement_file, after=current_date)
        if not daily:
            continue
        first_date, last_date = min(daily), max(daily)
        expected_start = current_date + timedelta(days=1)
        if first_date > expected_start:
            print(
                f"Предупреждение: в {movement_file.name} нет движений с {expected_start} "
                f"по {first_date - timedelta(days=1)}; остатки будут перенесены без изменений.",
                file=sys.stderr,
            )

        for day in date_range(expected_start, last_date):
            for key, (qty_delta, cost_delta) in daily.get(day, {}).items():
                balance = state.setdefault(key, [Decimal(0), Decimal(0)])
                balance[0] += qty_delta
                balance[1] += cost_delta

            if target_date is None or day == target_date:
                output = output_dir / f"stock_{day:%Y_%m_%d}.csv"
                write_stock(output, day, state)
                written.append(output)
        current_date = last_date

    if target_date is not None and target_date > current_date:
        raise ValueError(
            f"Целевая дата {target_date} выходит за диапазон движений до {current_date}"
        )
    if target_date is not None and not written:
        raise ValueError(
            f"Целевая дата {target_date} не позже начального остатка или отсутствует в диапазоне"
        )
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Рассчитать остатки на конец каждого дня по CSV-файлам движений."
    )
    parser.add_argument(
        "--initial-stock",
        type=Path,
        default=None,
        help="Файл начального остатка (по умолчанию самый ранний stock_*.csv).",
    )
    parser.add_argument(
        "--trans-dir",
        type=Path,
        default=PATH_TRANS,
        help="Каталог с invent_trans_YYYY_MM.csv.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PATH_STOCK,
        help="Каталог для рассчитанных stock_YYYY_MM_DD.csv.",
    )
    parser.add_argument(
        "--target-date",
        type=date.fromisoformat,
        default=None,
        help="Записать только одну дату YYYY-MM-DD; без параметра записываются все дни.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    initial_stock = args.initial_stock or find_initial_stock(args.output_dir)
    movement_files = sorted(args.trans_dir.glob("invent_trans_*.csv"))
    if not movement_files:
        raise FileNotFoundError(f"В {args.trans_dir} не найдены invent_trans_*.csv")

    written = calculate(initial_stock, movement_files, args.output_dir, args.target_date)
    print(f"Готово. Создано файлов: {len(written)}")
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        raise SystemExit(1)
