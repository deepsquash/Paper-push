"""Crossref REST API 抓取。以 from-online-pub-date 为过滤基准，
可以覆盖提前在线 (ahead of print) 文章——这些文章在期刊官网"Early Online"
区域上线即被 Crossref 收录，晚于正式卷期文章。
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date, timedelta
from typing import Dict, List

import requests

from ..models import Author, Paper

log = logging.getLogger(__name__)

API_BASE = "https://api.crossref.org"
UA = "PaperPush/0.1 (mailto:%s)"

# 这些 DOI 前缀对应期刊的新闻/评论类内容（非研究文章），Crossref 误标为 journal-article
DEFAULT_EXCLUDED_DOI_PREFIXES = [
    "10.1038/d41586",  # Nature News / editorials
]


def _strip_abstract(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(date_parts) -> str:
    """date_parts 形如 [[2026, 8, 6]]。"""
    if not date_parts or not date_parts[0]:
        return ""
    dp = date_parts[0]
    try:
        y = int(dp[0])
        m = int(dp[1]) if len(dp) > 1 else 1
        d = int(dp[2]) if len(dp) > 2 else 1
        return f"{y:04d}-{m:02d}-{d:02d}"
    except (TypeError, ValueError):
        return ""


def _to_paper(item: dict, journal_name: str) -> Paper:
    title = ""
    if item.get("title"):
        title = item["title"][0]
    authors = []
    for i, a in enumerate(item.get("author", []) or []):
        seq = "first" if i == 0 else ("last" if i == len(item["author"]) - 1 else "middle")
        authors.append(Author(given=a.get("given", ""), family=a.get("family", ""), sequence=seq))

    published_online = _parse_date(
        (item.get("published-online") or item.get("issued") or {}).get("date-parts", [])
    )
    published_print = _parse_date((item.get("published-print") or {}).get("date-parts", []))
    # 提前在线判定：有 online 日期但无正式卷期/印刷日期
    is_early = bool(published_online and not (item.get("volume") and item.get("issue")))

    return Paper(
        doi=item.get("DOI", ""),
        title=title,
        journal=journal_name,
        issn=",".join(item.get("ISSN", []) or []),
        authors=authors,
        published_online=published_online,
        published_print=published_print,
        abstract=_strip_abstract(item.get("abstract", "")),
        url=item.get("URL", "") or f"https://doi.org/{item.get('DOI', '')}",
        is_early_access=is_early,
    )


def fetch_recent(
    issns_by_journal: Dict[str, List[str]],
    since: date,
    mailto: str = "",
    max_pages_per_journal: int = 2,
    excluded_doi_prefixes: List[str] = None,
) -> List[Paper]:
    """按期刊查询最近上线的文章（含提前在线）。

    同一篇文章可能以多个 DOI 出现（如 eLife 的版本化 DOI、print/online 双收录），
    按 (期刊, 归一化标题) 合并，保留在线发布时间较晚的一条。
    """
    excluded_doi_prefixes = excluded_doi_prefixes or DEFAULT_EXCLUDED_DOI_PREFIXES
    session = requests.Session()
    session.headers.update({"User-Agent": UA % (mailto or "anonymous@example.com")})
    papers: Dict[tuple, Paper] = {}

    def _add(p: Paper) -> None:
        key = (p.journal, p.title.strip().lower() or p.doi)
        existing = papers.get(key)
        if existing is None or (p.published_online or "") > (existing.published_online or ""):
            papers[key] = p

    for journal_name, issns in issns_by_journal.items():
        for issn in issns:
            url = f"{API_BASE}/journals/{issn}/works"
            cursor = "*"
            for _ in range(max_pages_per_journal):
                try:
                    resp = session.get(
                        url,
                        params={
                            "filter": f"from-online-pub-date:{since.isoformat()},type:journal-article",
                            "rows": 200,
                            "sort": "published",
                            "order": "desc",
                            "cursor": cursor,
                            "mailto": mailto,
                        },
                        timeout=30,
                    )
                    resp.raise_for_status()
                except requests.RequestException as e:
                    log.warning("Crossref 请求失败 %s/%s: %s", journal_name, issn, e)
                    break

                data = resp.json()["message"]
                items = data.get("items", [])
                for it in items:
                    doi = it.get("DOI", "")
                    if any(doi.startswith(pfx) for pfx in excluded_doi_prefixes):
                        continue
                    p = _to_paper(it, journal_name)
                    if p.doi:
                        _add(p)
                cursor = data.get("next-cursor")
                if not items or not cursor:
                    break
                time.sleep(0.3)

    time.sleep(0.2)
    return list(papers.values())
