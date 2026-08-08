"""bioRxiv API 数据源。"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List

import requests

from ..models import Author, Paper

log = logging.getLogger(__name__)
API = "https://api.biorxiv.org/details/biorxiv"


def _authors(raw: str) -> list[Author]:
    parts = [p.strip() for p in (raw or "").split(";") if p.strip()]
    result = []
    for index, name in enumerate(parts):
        bits = name.rsplit(" ", 1)
        given, family = (bits[0], bits[1]) if len(bits) == 2 else ("", bits[0])
        sequence = "first" if index == 0 else ("last" if index == len(parts) - 1 else "middle")
        result.append(Author(given=given, family=family, sequence=sequence))
    return result


def fetch(since: date, until: date, category: str = "neuroscience", max_pages: int = 20,
          journal_name: str = "bioRxiv · Neuroscience", max_records: int = 1200) -> List[Paper]:
    papers = {}
    cursor = 0
    session = requests.Session()
    session.headers.update({"User-Agent": "PaperPush/1.0"})
    # 大窗口按月切分，避免 bioRxiv 全站 API 返回数万条后才能筛出 neuroscience。
    chunk_start = since
    while chunk_start <= until:
        next_month = (chunk_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        chunk_end = min(until, next_month - timedelta(days=1))
        cursor = 0
        for _ in range(max_pages):
            if len(papers) >= max_records:
                return list(papers.values())
            url = f"{API}/{chunk_start.isoformat()}/{chunk_end.isoformat()}/{cursor}"
            try:
                response = session.get(url, params={"category": category.replace(" ", "_")} if category else None, timeout=40)
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError) as exc:
                log.warning("bioRxiv 请求失败：%s", exc)
                break
            collection = data.get("collection") or []
            if not collection:
                break
            for item in collection:
                if category and str(item.get("category", "")).casefold() != category.replace("_", " ").casefold():
                    continue
                doi = str(item.get("doi", ""))
                paper = Paper(
                    doi=doi, title=str(item.get("title", "")), journal=journal_name,
                    authors=_authors(str(item.get("authors", ""))), published_online=str(item.get("date", "")),
                    abstract=str(item.get("abstract", "")), url=f"https://www.biorxiv.org/content/{doi}v{item.get('version', '1')}",
                    is_early_access=True,
                )
                papers[doi.casefold()] = paper
            messages = data.get("messages") or []
            total = int(messages[0].get("total", 0)) if messages else 0
            cursor += len(collection)
            if cursor >= total:
                break
        chunk_start = next_month
    return list(papers.values())
