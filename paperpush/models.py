from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Author:
    given: str = ""
    family: str = ""
    sequence: str = "first"

    @property
    def full_name(self) -> str:
        return f"{self.given} {self.family}".strip()

    @property
    def surname(self) -> str:
        return self.family.strip()


@dataclass
class Paper:
    doi: str
    title: str = ""
    journal: str = ""
    issn: str = ""
    authors: list = field(default_factory=list)
    published_online: Optional[str] = None  # ISO 日期
    published_print: Optional[str] = None
    abstract: str = ""
    fulltext: str = ""  # 仅 PMC 开放获取时可用
    url: str = ""
    is_early_access: bool = False  # 是否为提前在线文章（无卷期页码）

    def has_volume_issue(self) -> bool:
        return bool(self.journal and self.volume)


@dataclass
class FeedRule:
    name: str
    journals: list = field(default_factory=list)  # 空 = 全部
    keyword_terms: list = field(default_factory=list)
    keyword_match: str = "any"  # any | all
    keyword_fields: list = field(default_factory=lambda: ["title", "abstract"])
    exclude_terms: list = field(default_factory=list)
    first_authors: list = field(default_factory=list)
    last_authors: list = field(default_factory=list)
    author_match: str = "any"  # any | all

    @property
    def has_any_condition(self) -> bool:
        return bool(
            self.keyword_terms
            or self.first_authors
            or self.last_authors
            or self.exclude_terms
        )
