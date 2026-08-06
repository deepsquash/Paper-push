"""SQLite 去重存储：记录已见过的 DOI，判断新论文。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable, Set


class Storage:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS seen (
                doi TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                push_count INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._conn.commit()

    def record(self, dois: Iterable[str], pushed: bool = False) -> Set[str]:
        """记录本次出现的 DOI；返回【第一次出现】的 DOI 集合。"""
        now = datetime.now().isoformat(timespec="seconds")
        new: Set[str] = set()
        for doi in dois:
            d = doi.lower()
            cur = self._conn.execute("SELECT push_count FROM seen WHERE doi = ?", (d,))
            row = cur.fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO seen (doi, first_seen, push_count) VALUES (?, ?, ?)",
                    (d, now, 1 if pushed else 0),
                )
                new.add(d)
            else:
                self._conn.execute(
                    "UPDATE seen SET push_count = push_count + ? WHERE doi = ?",
                    (1 if pushed else 0, d),
                )
        self._conn.commit()
        return new

    def close(self) -> None:
        self._conn.close()
