#!/usr/bin/env python3
"""Назначить себе следующее пробуждение.

    sleep.py <минуты>

Решает персонаж, а не расписание: есть дело — скоро, тихо — позже.
"""
import sys
from _common import STATE

MIN = 1


def main():
    try:
        minutes = int(float(sys.argv[1]))
    except (IndexError, ValueError):
        print("не назначено: использование sleep.py <минуты>")
        sys.exit(1)
    clamped = max(MIN, minutes)
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "sleep.txt").write_text(f"{clamped}\n", encoding="utf-8")
    note = "" if clamped == minutes else f" (просил {minutes}, меньше минуты не бывает)"
    print(f"следующее пробуждение через {clamped} мин{note}")


if __name__ == "__main__":
    main()
