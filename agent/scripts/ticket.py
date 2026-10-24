#!/usr/bin/env python3
"""Сообщить разработчику, что чего-то не хватает или что-то сломалось.

    ticket.py "<что нужно или что сломалось, ожидание, что вышло на деле>"

Тикет ложится в state/tickets.jsonl и печатается в консоль раннера. Цикл поддержки:
сбой → тикет → воспроизведение → исправление → повторная проверка.
"""
import sys
from _common import STATE, append_jsonl, now


def main():
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print('не записано: использование ticket.py "<текст>"')
        sys.exit(1)
    append_jsonl(STATE / "tickets.jsonl", {"at": now().isoformat(timespec="seconds"),
                                           "text": sys.argv[1].strip(), "status": "open"})
    print("тикет записан, разработчик увидит его в журнале")


if __name__ == "__main__":
    main()
