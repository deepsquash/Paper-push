"""本地持久化文献库与用户交互（收藏、屏蔽）。"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

from .models import Author, Paper


class Library:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS papers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                paper_key TEXT NOT NULL UNIQUE,
                doi TEXT,
                title TEXT NOT NULL,
                journal TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'crossref',
                category TEXT DEFAULT '',
                issn TEXT DEFAULT '',
                authors_json TEXT NOT NULL DEFAULT '[]',
                abstract TEXT DEFAULT '',
                fulltext TEXT DEFAULT '',
                published_online TEXT,
                published_print TEXT,
                created TEXT,
                url TEXT DEFAULT '',
                is_early_access INTEGER NOT NULL DEFAULT 0,
                first_seen TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_papers_date ON papers(published_online DESC);
            CREATE INDEX IF NOT EXISTS idx_papers_journal ON papers(journal, published_online DESC);
            CREATE TABLE IF NOT EXISTS reactions (
                paper_key TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS source_sync (
                source_key TEXT PRIMARY KEY,
                synced_from TEXT,
                synced_at TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                message TEXT DEFAULT ''
            );
            """
        )
        # 旧库迁移：补 created 列
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(papers)").fetchall()}
        if "created" not in cols:
            self.conn.execute("ALTER TABLE papers ADD COLUMN created TEXT")
        self.conn.commit()

    @staticmethod
    def key_for(paper: Paper) -> str:
        if paper.doi:
            return paper.doi.casefold()
        return f"{paper.journal}|{paper.title}".casefold()

    def upsert(self, papers: Iterable[Paper], source: str = "crossref", category: str = "") -> int:
        now = datetime.now().isoformat(timespec="seconds")
        count = 0
        for paper in papers:
            key = self.key_for(paper)
            authors = [{"given": a.given, "family": a.family, "sequence": a.sequence} for a in paper.authors]
            self.conn.execute(
                """INSERT INTO papers
                (paper_key, doi, title, journal, source, category, issn, authors_json, abstract,
                 fulltext, published_online, published_print, created, url, is_early_access, first_seen, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(paper_key) DO UPDATE SET
                  title=excluded.title, journal=excluded.journal, source=excluded.source,
                  category=excluded.category, issn=excluded.issn, authors_json=excluded.authors_json,
                  abstract=CASE WHEN excluded.abstract != '' THEN excluded.abstract ELSE papers.abstract END,
                  fulltext=CASE WHEN excluded.fulltext != '' THEN excluded.fulltext ELSE papers.fulltext END,
                  published_online=excluded.published_online, published_print=excluded.published_print,
                  created=excluded.created,
                  url=excluded.url, is_early_access=excluded.is_early_access, updated_at=excluded.updated_at""",
                (key, paper.doi, paper.title, paper.journal, source, category, paper.issn,
                 json.dumps(authors, ensure_ascii=False), paper.abstract, paper.fulltext,
                 paper.published_online, paper.published_print, paper.created, paper.url,
                 int(paper.is_early_access), now, now),
            )
            count += 1
        self.conn.commit()
        return count

    @staticmethod
    def effective_date(online: str, printed: str, first_seen: str, created: str = "") -> str:
        """展示/排序用的“实际上线日期”。

        期刊常给未来的卷期日期（如 J Neurosci Methods 把 print 写成两个月后），
        这类未来日期不是真实上线时间。优先级：
          1) published-online（若不晚于今天）——真正的“提前在线”日期
          2) created（Crossref 记录创建日，最接近上线，且从不为未来）
          3) 其余不晚于今天的日期里取最近者
          4) 都为未来则退回 first_seen
        """
        today = date.today().isoformat()
        if online and online <= today:
            return online
        if created and created <= today:
            return created
        past = [d for d in (online, printed, created) if d and d <= today]
        if past:
            return max(past)
        seen = (first_seen or "")[:10]
        return seen or created or online or printed or ""

    def _row_dict(self, row: sqlite3.Row) -> dict:
        authors = json.loads(row["authors_json"] or "[]")
        created = row["created"] if "created" in row.keys() else ""
        eff = self.effective_date(row["published_online"] or "", row["published_print"] or "",
                                  row["first_seen"] or "", created or "")
        return {
            "key": row["paper_key"], "doi": row["doi"], "title": row["title"],
            "journal": row["journal"], "source": row["source"], "category": row["category"],
            "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in authors],
            "abstract": row["abstract"], "published_online": row["published_online"],
            "published_print": row["published_print"], "published_display": eff, "url": row["url"],
            "is_early_access": bool(row["is_early_access"]), "reaction": row["reaction"] or "",
        }

    def reaction_map(self, keys: Iterable[str]) -> dict[str, str]:
        keys = list(dict.fromkeys(keys))
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        rows = self.conn.execute(
            f"SELECT paper_key,state FROM reactions WHERE paper_key IN ({placeholders})", keys
        ).fetchall()
        return {row["paper_key"]: row["state"] for row in rows}

    def list_papers(self, *, journal: str = "", days: int = 180, limit: int = 100,
                    offset: int = 0, favorites: bool = False, include_hidden: bool = False,
                    source: str = "", exclude_source: str = "") -> list[dict]:
        today = date.today().isoformat()
        # SQL 中的“实际上线日期”：优先 online(≤今天)，其次 created(≤今天)，
        # 再次取不晚于今天的最近日期，最后退回 first_seen。与 effective_date() 一致。
        eff = (
            "CASE"
            " WHEN COALESCE(p.published_online,'')!='' AND p.published_online<=:today THEN p.published_online"
            " WHEN COALESCE(p.created,'')!='' AND p.created<=:today THEN p.created"
            " WHEN COALESCE(p.published_print,'')!='' AND p.published_print<=:today THEN p.published_print"
            " ELSE substr(p.first_seen,1,10) END"
        )
        where = [f"{eff} >= :since"]
        params: dict = {"today": today, "since": (date.today() - timedelta(days=days)).isoformat()}
        if journal:
            where.append("p.journal = :journal")
            params["journal"] = journal
        if source:
            where.append("p.source = :source")
            params["source"] = source
        if exclude_source:
            where.append("p.source != :exsource")
            params["exsource"] = exclude_source
        if favorites:
            where.append("r.state = 'liked'")
        elif not include_hidden:
            where.append("COALESCE(r.state, '') != 'hidden'")
        params["limit"] = limit
        params["offset"] = offset
        rows = self.conn.execute(
            f"""SELECT p.*, r.state AS reaction FROM papers p
                LEFT JOIN reactions r ON r.paper_key=p.paper_key
                WHERE {' AND '.join(where)}
                ORDER BY {eff} DESC, p.first_seen DESC
                LIMIT :limit OFFSET :offset""", params,
        ).fetchall()
        return [self._row_dict(row) for row in rows]

    def papers_as_models(self, days: int = 180) -> list[Paper]:
        rows = self.conn.execute(
            """SELECT p.* FROM papers p LEFT JOIN reactions r ON r.paper_key=p.paper_key
               WHERE COALESCE(r.state, '') != 'hidden'
                 AND COALESCE(p.published_online, p.published_print, p.first_seen) >= ?""",
            ((date.today() - timedelta(days=days)).isoformat(),),
        ).fetchall()
        papers = []
        for row in rows:
            authors = [Author(**a) for a in json.loads(row["authors_json"] or "[]")]
            created = row["created"] if "created" in row.keys() else None
            papers.append(Paper(
                doi=row["doi"] or "", title=row["title"], journal=row["journal"], issn=row["issn"],
                authors=authors, created=created,
                published_online=row["published_online"], published_print=row["published_print"],
                abstract=row["abstract"], fulltext=row["fulltext"], url=row["url"],
                is_early_access=bool(row["is_early_access"]),
            ))
        return papers

    def set_reaction(self, key: str, state: str) -> None:
        if state not in ("liked", "hidden", ""):
            raise ValueError("invalid reaction")
        if not state:
            self.conn.execute("DELETE FROM reactions WHERE paper_key=?", (key,))
        else:
            self.conn.execute(
                """INSERT INTO reactions(paper_key,state,updated_at) VALUES(?,?,?)
                   ON CONFLICT(paper_key) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at""",
                (key, state, datetime.now().isoformat(timespec="seconds")),
            )
        self.conn.commit()

    def mark_sync(self, source_key: str, synced_from: str, count: int, status: str = "ok", message: str = "") -> None:
        self.conn.execute(
            """INSERT INTO source_sync(source_key,synced_from,synced_at,count,status,message) VALUES(?,?,?,?,?,?)
               ON CONFLICT(source_key) DO UPDATE SET synced_from=excluded.synced_from,
               synced_at=excluded.synced_at,count=excluded.count,status=excluded.status,message=excluded.message""",
            (source_key, synced_from, datetime.now().isoformat(timespec="seconds"), count, status, message),
        )
        self.conn.commit()

    def sync_info(self, source_key: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM source_sync WHERE source_key=?", (source_key,)).fetchone()
        return dict(row) if row else None

    def journal_counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT journal,COUNT(*) AS count FROM papers GROUP BY journal").fetchall()
        return {row["journal"]: row["count"] for row in rows}

    def close(self) -> None:
        self.conn.close()
