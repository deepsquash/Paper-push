"""Feed 匹配引擎：关键词（标题/摘要/全文）、第一作者、最后作者，支持与/或/非逻辑。

fulltext 说明：本地匹配只覆盖 title/abstract；fulltext 通道依赖 main 中预先用
PMC 全文索引算出的命中 DOI 集合（按 feed 整组关键词 OR 查询）。因此：
- match=any：本地命中 或 PMC 全文命中 任一即命中
- match=all：必须所有词都在本地 title/abstract 中命中（全文通道无法逐词证实）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

from .models import FeedRule, Paper
from .query import QuerySyntaxError, evaluate, parse_query


@dataclass
class MatchResult:
    feed_name: str
    matched_keywords: List[str] = field(default_factory=list)
    matched_fields: List[str] = field(default_factory=list)
    matched_first_authors: List[str] = field(default_factory=list)
    matched_last_authors: List[str] = field(default_factory=list)

    @property
    def reasons(self) -> List[str]:
        reasons = []
        if self.matched_keywords:
            field_note = "/".join(dict.fromkeys(self.matched_fields))
            reasons.append(f"关键词『{"、".join(self.matched_keywords)}』(命中{field_note})")
        if self.matched_first_authors:
            reasons.append(f"第一作者匹配: {', '.join(self.matched_first_authors)}")
        if self.matched_last_authors:
            reasons.append(f"最后作者匹配: {', '.join(self.matched_last_authors)}")
        return reasons


def _contains(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def _author_hits(authors, fragments: List[str]) -> List[str]:
    """作者全名中做不区分大小写子串匹配，返回命中的作者名。"""
    hits = []
    for frag in fragments:
        f = frag.strip().lower()
        if not f:
            continue
        for a in authors:
            haystack = f"{a.given} {a.family}".lower()
            if f in haystack:
                hits.append(a.full_name or a.family)
                break
    return hits


def match_paper(
    paper: Paper, feed: FeedRule, fulltext_dois: Optional[Set[str]] = None
) -> Optional[MatchResult]:
    """判断一篇论文是否命中 feed 规则。未命中返回 None。"""
    # 期刊过滤（feed.journals 为空 = 全部）
    if feed.journals and paper.journal not in feed.journals:
        return None

    result = MatchResult(feed_name=feed.name)

    # 新版复杂表达式优先。期刊也可通过 SO= 写入表达式；feed.journals 仍作为快速白名单。
    if feed.query:
        try:
            query_result = evaluate(parse_query(feed.query), paper)
        except QuerySyntaxError:
            return None
        if not query_result.matched:
            return None
        result.matched_keywords = query_result.hits
        result.matched_fields = ["布尔表达式"]
        return result

    # 排除词（非逻辑）：标题或摘要中出现即不命中
    combined_text = f"{paper.title} {paper.abstract}"
    for t in feed.exclude_terms:
        if t and _contains(combined_text, t):
            return None

    # ---- 关键词逻辑（本地字段 + PMC 全文通道）----
    wants_fulltext = "fulltext" in feed.keyword_fields
    ft_hit = (
        wants_fulltext
        and fulltext_dois is not None
        and paper.doi.lower() in fulltext_dois
    )

    local_hits: List[tuple] = []  # (term, field)
    if feed.keyword_terms:
        local_fields = {
            name: text
            for name, text in (("title", paper.title), ("abstract", paper.abstract))
            if name in feed.keyword_fields and text
        }
        seen_terms = set()
        for term in feed.keyword_terms:
            if term in seen_terms:
                continue
            for field_name, text in local_fields.items():
                if _contains(text, term):
                    local_hits.append((term, field_name))
                    seen_terms.add(term)
                    break

        if feed.keyword_match == "all":
            hit_term_set = {t for t, _ in local_hits}
            if not all(t in hit_term_set for t in feed.keyword_terms):
                return None
        else:  # any
            if not local_hits and not ft_hit:
                return None

        if feed.keyword_match == "any" and ft_hit and not local_hits:
            pass  # 仅全文命中的词，matched_keywords 无法枚举（PMC 只给了集合）
        result.matched_keywords = [t for t, _ in local_hits]
        result.matched_fields = [f for _, f in local_hits]
        if ft_hit and "fulltext" not in result.matched_fields:
            result.matched_fields.append("fulltext(PMC)")
            if not result.matched_keywords:
                result.matched_keywords = [f"<全文命中: {feed.keyword_terms[0]} 等>"]
    else:
        # 无关键词条件但 feed 开了 fulltext 字段——无意义，忽略
        pass

    # ---- 作者逻辑 ----
    if feed.first_authors:
        candidates = [a for a in paper.authors if a.sequence == "first"] or (paper.authors[:1] if paper.authors else [])
        result.matched_first_authors = _author_hits(candidates, feed.first_authors)
    if feed.last_authors:
        candidates = [a for a in paper.authors if a.sequence == "last"] or (paper.authors[-1:] if paper.authors else [])
        result.matched_last_authors = _author_hits(candidates, feed.last_authors)

    if feed.author_match == "all":
        if feed.first_authors and not result.matched_first_authors:
            return None
        if feed.last_authors and not result.matched_last_authors:
            return None
    else:  # any
        if (feed.first_authors or feed.last_authors) and not (
            result.matched_first_authors or result.matched_last_authors
        ):
            return None

    # 一个条件都没有配置的 feed 不应命中任何文章
    if not feed.has_any_condition:
        return None

    return result
