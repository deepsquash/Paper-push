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
UA = "PaperPush/1.0 (https://github.com/deepsquash/Paper-push; mailto:%s)"
DEFAULT_MAILTO = "paperpush@example.com"


def _get_with_retry(session, url, params, retries=5, base_sleep=1.0):
    """带指数退避的 Crossref 请求，处理 429 / 超时 / 连接重置。"""
    last_err = None
    for attempt in range(retries):
        try:
            resp = session.get(url, params=params, timeout=45)
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", base_sleep * (2 ** attempt)))
                log.warning("Crossref 429 限流，%.1fs 后重试（%d/%d）", wait, attempt + 1, retries)
                time.sleep(min(wait, 30))
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_err = e
            wait = base_sleep * (2 ** attempt)
            log.warning("Crossref 请求异常（%d/%d），%.1fs 后重试：%s", attempt + 1, retries, wait, e)
            time.sleep(wait)
    if last_err:
        raise last_err
    return None

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

    # 只用真正的 online 日期，不再退回 issued（issued 可能是未来的卷期日期）
    published_online = _parse_date((item.get("published-online") or {}).get("date-parts", []))
    published_print = _parse_date((item.get("published-print") or {}).get("date-parts", []))
    # created = DOI 记录首次创建时间，最接近真实上线时间，且从不为未来
    created = _parse_date((item.get("created") or {}).get("date-parts", []))
    # 提前在线判定：有 online 日期但无正式卷期/印刷日期
    is_early = bool(published_online and not (item.get("volume") and item.get("issue")))

    return Paper(
        doi=item.get("DOI", ""),
        title=title,
        journal=journal_name,
        issn=",".join(item.get("ISSN", []) or []),
        authors=authors,
        created=created,
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
    date_filter: str = "online",
) -> List[Paper]:
    """按期刊查询文章。

    date_filter="online"：以 from-online-pub-date 过滤，专抓提前在线文章（每日增量用）。
    date_filter="any"：同时用 from-online-pub-date 与 from-pub-date 各查一遍再合并，
        兼顾提前在线文章与仅有卷期日期的期刊（如 Science），用于半年回填。

    同一篇文章可能以多个 DOI 出现（eLife 版本化 DOI、print/online 双收录），
    按 (期刊, 归一化标题) 合并，保留发布时间较晚的一条。
    """
    excluded_doi_prefixes = excluded_doi_prefixes or DEFAULT_EXCLUDED_DOI_PREFIXES
    mailto = mailto or DEFAULT_MAILTO
    session = requests.Session()
    session.headers.update({"User-Agent": UA % mailto})
    papers: Dict[tuple, Paper] = {}

    def _add(p: Paper) -> None:
        key = (p.journal, p.title.strip().lower() or p.doi)
        existing = papers.get(key)
        best = (p.published_online or "") or (p.published_print or "")
        prev = (existing.published_online or "") or (existing.published_print or "") if existing else ""
        if existing is None or best > prev:
            papers[key] = p

    filters = ["from-online-pub-date"] if date_filter == "online" else ["from-online-pub-date", "from-pub-date"]

    for journal_name, issns in issns_by_journal.items():
        # 去重 ISSN（print 与 online 可能相同），避免重复请求触发限流
        for issn in list(dict.fromkeys(i for i in issns if i)):
            url = f"{API_BASE}/journals/{issn}/works"
            for filter_field in filters:
                cursor = "*"
                for _ in range(max_pages_per_journal):
                    try:
                        resp = _get_with_retry(session, url, {
                            "filter": f"{filter_field}:{since.isoformat()},type:journal-article",
                            "rows": 200,
                            "sort": "published",
                            "order": "desc",
                            "cursor": cursor,
                            "mailto": mailto,
                        })
                    except requests.RequestException as e:
                        log.warning("Crossref 最终失败 %s/%s (%s): %s", journal_name, issn, filter_field, e)
                        break
                    if resp is None:
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
                    time.sleep(0.5)  # 礼貌间隔，降低 429 概率

    time.sleep(0.2)
    return list(papers.values())


def fetch_journal(
    journal_name: str,
    issns: List[str],
    since: date,
    mailto: str = "",
    max_pages: int = 8,
    excluded_doi_prefixes: List[str] = None,
) -> List[Paper]:
    """按单一期刊回填较长时间窗口；供期刊详情首次加载半年文献。"""
    return fetch_recent(
        {journal_name: [i for i in issns if i]}, since, mailto, date_filter="any",
        max_pages_per_journal=max_pages,
        excluded_doi_prefixes=excluded_doi_prefixes,
    )
