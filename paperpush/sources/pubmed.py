"""PubMed E-utilities：为论文补充摘要；通过 PMC 全文索引实现 fulltext 关键词匹配。
PMC 收录开放获取全文，仅对 OA 文章的全文搜索有效（非 OA 期刊文章请用 title/abstract）。
"""
from __future__ import annotations

import logging
import re
import time
from typing import Dict, Iterable, List, Set

import requests

from ..models import Paper

log = logging.getLogger(__name__)

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# config 中的期刊 key -> PMC 使用的正式期刊名（缺失时退回原名称）
PMC_JOURNAL_NAMES = {
    "PNAS": "Proceedings of the National Academy of Sciences of the United States of America",
    "Science Advances": "Science advances",
    "Nature Reviews Neuroscience": "Nature reviews. Neuroscience",
    "Annual Review of Neuroscience": "Annual review of neuroscience",
    "Current Biology": "Current biology",
    "Journal of Neuroscience": "The Journal of neuroscience",
}
_RATE = 0.4


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "PaperPush/0.1 (mailto:paperpush@example.com)"})
    return s


def _get_json(session: requests.Session, path: str, params: dict) -> dict:
    for attempt in range(3):
        try:
            resp = session.get(f"{EUTILS}/{path}", params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("E-utilities 请求失败 (%s): %s，重试 %d/3", path, e, attempt + 1)
            time.sleep(_RATE * (attempt + 2))
    return {}


def _doi_to_pmid(session: requests.Session, dois: List[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for i in range(0, len(dois), 50):
        batch = dois[i : i + 50]
        term = " OR ".join(f"{d}[DOI]" for d in batch)
        data = _get_json(session, "esearch.fcgi", {"db": "pubmed", "term": term, "retmode": "json", "retmax": 500})
        ids = ((data.get("esearchresult") or {}).get("idlist")) or []
        if not ids:
            time.sleep(_RATE)
            continue
        summary = _get_json(
            session, "esummary.fcgi", {"db": "pubmed", "id": ",".join(ids), "retmode": "json"}
        )
        for pmid, info in (summary.get("result") or {}).items():
            if pmid == "uids":
                continue
            for aid in info.get("articleids") or []:
                if aid.get("idtype") == "doi":
                    mapping[str(aid.get("value", "")).lower()] = pmid
        time.sleep(_RATE)
    return mapping


def _fetch_abstracts(session: requests.Session, pmids: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for i in range(0, len(pmids), 100):
        batch = pmids[i : i + 100]
        try:
            resp = session.get(
                f"{EUTILS}/efetch.fcgi",
                params={"db": "pubmed", "id": ",".join(batch), "rettype": "abstract", "retmode": "xml"},
                timeout=30,
            )
            resp.raise_for_status()
            xml = resp.text
        except requests.RequestException as e:
            log.warning("efetch 失败: %s", e)
            time.sleep(_RATE)
            continue
        for block in re.findall(r"<PubmedArticle>.*?</PubmedArticle>", xml, re.S):
            pmid_m = re.search(r"<PMID[^>]*>(\d+)</PMID>", block)
            if not pmid_m:
                continue
            texts = re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", block, re.S)
            if texts:
                clean = re.sub(r"<[^>]+>", " ", " ".join(texts))
                out[pmid_m.group(1)] = re.sub(r"\s+", " ", clean).strip()
        time.sleep(_RATE)
    return out


def enrich_abstracts(papers: List[Paper]) -> None:
    """批量按 DOI 查找 PMID 并补充摘要（原地修改）。"""
    missing = [p for p in papers if not p.abstract]
    if not missing:
        return
    session = _session()
    mapping = _doi_to_pmid(session, [p.doi for p in missing])
    pmid_to_doi = {v: k for k, v in mapping.items()}
    abstracts = _fetch_abstracts(session, list(pmid_to_doi.keys()))
    by_doi = {p.doi.lower(): p for p in missing}
    for pmid, text in abstracts.items():
        doi = pmid_to_doi.get(pmid)
        paper = by_doi.get(doi)
        if paper is not None:
            paper.abstract = text


def fulltext_hit_dois(terms: Iterable[str], journal_keys: Iterable[str]) -> Set[str]:
    """在 PMC 全文索引中搜索关键词，返回命中论文的 DOI 集合。"""
    terms = [t.strip() for t in terms if t.strip()]
    if not terms:
        return set()
    journal_keys = [j for j in journal_keys if j.strip()]
    term_clause = "(" + " OR ".join(f'"{t}"' for t in terms) + ")"
    if journal_keys:
        journal_clause = " OR ".join(
            f"{PMC_JOURNAL_NAMES.get(j, j)}[Journal]" for j in journal_keys
        )
        query = f"{term_clause} AND ({journal_clause})"
    else:
        query = term_clause

    session = _session()
    data = _get_json(session, "esearch.fcgi", {"db": "pmc", "term": query, "retmode": "json", "retmax": 1000})
    pmids = ((data.get("esearchresult") or {}).get("idlist")) or []
    if not pmids:
        return set()
    time.sleep(_RATE)
    summary = _get_json(
        session, "esummary.fcgi", {"db": "pmc", "id": ",".join(pmids), "retmode": "json"}
    )
    dois: Set[str] = set()
    for pmid, info in (summary.get("result") or {}).items():
        if pmid == "uids":
            continue
        for aid in info.get("articleids") or []:
            if aid.get("idtype") == "doi":
                dois.add(str(aid.get("value", "")).lower())
    return dois
