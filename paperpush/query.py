"""Web of Science 风格的布尔查询解析与执行。

支持：AND / OR / NOT、任意嵌套括号、引号短语、通配符 * 和字段限定。

字段：
  TS  主题（标题 + 摘要 + 全文）
  TI  标题
  AB  摘要
  FT  全文
  AU  任意作者
  FA  第一作者
  LA  最后作者
  SO  期刊 / 来源

示例：
  ("synaptic plasticity" OR hippocamp*) AND NOT review
  TS=((optogenetic* OR chemogenetic*) AND memory) AND LA=(Tonegawa OR Buzsaki)
  (TI=(astrocyte OR microglia) OR AB="glial cell") AND SO=(Nature OR Neuron)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from .models import Paper


class QuerySyntaxError(ValueError):
    pass


@dataclass
class EvalResult:
    matched: bool
    hits: List[str] = field(default_factory=list)


@dataclass
class Node:
    kind: str
    value: str = ""
    field_name: str = "TS"
    left: Optional["Node"] = None
    right: Optional["Node"] = None


TOKEN_RE = re.compile(
    r'\s*(?:(AND|OR|NOT)\b|([A-Za-z]{2})\s*=|(\()|(\))|("(?:\\.|[^"\\])*")|([^\s()=]+))',
    re.IGNORECASE,
)
FIELDS = {"TS", "TI", "AB", "FT", "AU", "FA", "LA", "SO"}


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = TOKEN_RE.match(text, pos)
        if not match:
            raise QuerySyntaxError(f"无法识别第 {pos + 1} 个字符附近的内容")
        op, field_name, lp, rp, quoted, word = match.groups()
        if op:
            tokens.append((op.upper(), op.upper()))
        elif field_name:
            field_name = field_name.upper()
            if field_name not in FIELDS:
                raise QuerySyntaxError(f"不支持的字段 {field_name}；可用字段：{', '.join(sorted(FIELDS))}")
            tokens.append(("FIELD", field_name))
        elif lp:
            tokens.append(("LP", lp))
        elif rp:
            tokens.append(("RP", rp))
        elif quoted:
            tokens.append(("TERM", bytes(quoted[1:-1], "utf-8").decode("unicode_escape") if "\\" in quoted else quoted[1:-1]))
        elif word:
            tokens.append(("TERM", word))
        pos = match.end()
    return tokens


class Parser:
    def __init__(self, text: str):
        self.tokens = _tokenize(text.strip())
        self.pos = 0

    def parse(self) -> Node:
        if not self.tokens:
            raise QuerySyntaxError("查询表达式不能为空")
        node = self._or("TS")
        if self.pos != len(self.tokens):
            raise QuerySyntaxError(f"意外的内容：{self.tokens[self.pos][1]}")
        return node

    def _peek(self, kind: str) -> bool:
        return self.pos < len(self.tokens) and self.tokens[self.pos][0] == kind

    def _take(self, kind: str) -> tuple[str, str]:
        if not self._peek(kind):
            got = self.tokens[self.pos][1] if self.pos < len(self.tokens) else "表达式结尾"
            raise QuerySyntaxError(f"期待 {kind}，实际为 {got}")
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def _or(self, context: str) -> Node:
        node = self._and(context)
        while self._peek("OR"):
            self.pos += 1
            node = Node("OR", left=node, right=self._and(context))
        return node

    def _and(self, context: str) -> Node:
        node = self._not(context)
        while self._peek("AND"):
            self.pos += 1
            node = Node("AND", left=node, right=self._not(context))
        return node

    def _not(self, context: str) -> Node:
        if self._peek("NOT"):
            self.pos += 1
            return Node("NOT", left=self._not(context))
        return self._primary(context)

    def _primary(self, context: str) -> Node:
        if self._peek("FIELD"):
            field_name = self._take("FIELD")[1]
            return self._primary(field_name)
        if self._peek("LP"):
            self.pos += 1
            node = self._or(context)
            self._take("RP")
            return node
        if self._peek("TERM"):
            return Node("TERM", value=self._take("TERM")[1], field_name=context)
        got = self.tokens[self.pos][1] if self.pos < len(self.tokens) else "表达式结尾"
        raise QuerySyntaxError(f"此处应为关键词、字段或括号，实际为 {got}")


def parse_query(text: str) -> Node:
    return Parser(text).parse()


def _wildcard_match(text: str, term: str) -> bool:
    if "*" not in term and "?" not in term:
        return term.casefold() in text.casefold()
    pattern = re.escape(term).replace(r"\*", ".*").replace(r"\?", ".")
    return re.search(pattern, text, re.IGNORECASE) is not None


def _field_values(paper: Paper, field_name: str) -> Iterable[str]:
    authors = [a.full_name for a in paper.authors if a.full_name]
    if field_name == "TI":
        return [paper.title]
    if field_name == "AB":
        return [paper.abstract]
    if field_name == "FT":
        return [paper.fulltext]
    if field_name == "AU":
        return authors
    if field_name == "FA":
        return authors[:1]
    if field_name == "LA":
        return authors[-1:]
    if field_name == "SO":
        return [paper.journal]
    return [paper.title, paper.abstract, paper.fulltext]


def evaluate(node: Node, paper: Paper) -> EvalResult:
    if node.kind == "TERM":
        matched = any(_wildcard_match(value or "", node.value) for value in _field_values(paper, node.field_name))
        label = f"{node.field_name}={node.value}"
        return EvalResult(matched, [label] if matched else [])
    if node.kind == "NOT":
        child = evaluate(node.left, paper)
        return EvalResult(not child.matched, [f"NOT({', '.join(child.hits) or '条件'})"] if not child.matched else [])
    left = evaluate(node.left, paper)
    right = evaluate(node.right, paper)
    if node.kind == "AND":
        return EvalResult(left.matched and right.matched, left.hits + right.hits if left.matched and right.matched else [])
    if node.kind == "OR":
        return EvalResult(left.matched or right.matched, (left.hits if left.matched else []) + (right.hits if right.matched else []))
    raise ValueError(f"未知节点：{node.kind}")


def validate_query(text: str) -> dict:
    try:
        parse_query(text)
        return {"valid": True, "message": "表达式有效"}
    except QuerySyntaxError as exc:
        return {"valid": False, "message": str(exc)}
