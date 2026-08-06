"""生成静态 HTML 看板（GitHub Pages 部署），含 Zotero 一键抓取按钮。"""
from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

from ..matcher import MatchResult
from ..models import Paper

CSS = """
:root { --bg:#0f1419; --card:#1a2029; --fg:#e6e9ee; --muted:#9aa4b2; --accent:#4da3ff;
        --tag:#253244; --tag-fg:#8fc4ff; --new:#2e7d32; --early:#b26a00; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }
header { padding:24px 32px 16px; border-bottom:1px solid #262d38; }
h1 { margin:0 0 4px; font-size:22px; }
.sub { color:var(--muted); font-size:13px; }
.stats { display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }
.stat { background:var(--card); border:1px solid #262d38; border-radius:8px; padding:8px 14px; font-size:13px; }
.stat b { color:var(--accent); }
main { max-width:960px; margin:0 auto; padding:20px 16px 60px; }
section { margin-bottom:28px; }
h2 { font-size:18px; border-bottom:1px solid #262d38; padding-bottom:8px; }
h2 .cnt { color:var(--muted); font-weight:normal; font-size:13px; }
.paper { background:var(--card); border:1px solid #262d38; border-radius:10px;
         padding:14px 16px; margin:10px 0; }
.paper .title { font-size:15px; font-weight:600; line-height:1.45; }
.paper .title a { color:var(--fg); text-decoration:none; }
.paper .title a:hover { color:var(--accent); }
.meta { color:var(--muted); font-size:12.5px; margin:6px 0; }
.badge { display:inline-block; font-size:11px; border-radius:4px; padding:1px 7px; margin-left:6px; }
.early { background:rgba(178,106,0,.18); color:#ffb84d; border:1px solid #b26a00; }
.new   { background:rgba(46,125,50,.18); color:#7ee07e; border:1px solid #2e7d32; }
.reason { display:inline-block; background:var(--tag); color:var(--tag-fg); font-size:11.5px;
          border-radius:10px; padding:2px 9px; margin:2px 4px 2px 0; }
.abstract { color:#c3cad4; font-size:13px; line-height:1.6; margin:8px 0 10px;
            display:-webkit-box; -webkit-line-clamp:3; -webkit-box-orient:vertical; overflow:hidden; }
.abstract:hover { -webkit-line-clamp:unset; }
.actions { display:flex; gap:8px; flex-wrap:wrap; }
.actions a { font-size:12.5px; padding:5px 12px; border-radius:6px; text-decoration:none; }
.btn-doi { background:transparent; border:1px solid #3a4454; color:var(--fg); }
.btn-zotero { background:var(--accent); color:#0b1520; font-weight:600; }
.hint { color:var(--muted); font-size:12px; margin-top:10px; }
.empty { color:var(--muted); padding:8px 2px; }
footer { color:#5b6472; font-size:12px; text-align:center; padding:20px; }
@media (prefers-color-scheme: light) {
  :root { --bg:#f6f7f9; --card:#fff; --fg:#1c2430; --muted:#68727f; --tag:#e8eef6; --tag-fg:#2563a8; }
  body { background:var(--bg); }
  .paper, .stat, header { border-color:#dfe4ea; }
  .abstract { color:#4a5564; }
}
"""


def _fmt_date(d: str) -> str:
    return d or "—"


def render_report(
    feed_results: Dict[str, List[Tuple[Paper, MatchResult]]],
    report_dir: str,
    run_date: date,
    new_dois: set = None,
    report_url: str = "",
) -> Path:
    """生成 index.html 与 data.json，返回 index.html 路径。"""
    new_dois = new_dois or set()
    out_dir = Path(report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sections = []
    total = 0
    for feed_name, items in feed_results.items():
        total += len(items)
        cards = []
        for paper, match in items:
            is_new = paper.doi.lower() in new_dois
            badges = ""
            if paper.is_early_access:
                badges += '<span class="badge early">🟢 提前在线</span>'
            if is_new:
                badges += '<span class="badge new">NEW 本次新增</span>'
            reasons = "".join(
                f'<span class="reason">{html.escape(r)}</span>' for r in match.reasons
            ) or '<span class="reason">feed 命中</span>'
            authors = ", ".join(a.full_name for a in paper.authors if a.full_name) or "—"
            if len(authors) > 220:
                authors = authors[:220] + " …"
            doi_url = f"https://doi.org/{paper.doi}"
            cards.append(
                f'<div class="paper">'
                f'<div class="title"><a href="{doi_url}" target="_blank">{html.escape(paper.title)}</a>{badges}</div>'
                f'<div class="meta">{html.escape(paper.journal)} · 在线发布 {_fmt_date(paper.published_online)}'
                f' · {html.escape(authors)}</div>'
                f'<div>{reasons}</div>'
                f'<div class="abstract">{html.escape(paper.abstract or "(无摘要，点击标题查看原文)")}</div>'
                f'<div class="actions">'
                f'<a class="btn-doi" href="{doi_url}" target="_blank">打开原文</a>'
                f'<a class="btn-zotero" href="{doi_url}" target="_blank">+ 添加至 Zotero</a>'
                f'</div></div>'
            )
        body = "\n".join(cards) if cards else '<div class="empty">本 feed 暂无命中</div>'
        sections.append(
            f'<section id="feed-{html.escape(feed_name)}">'
            f"<h2>{html.escape(feed_name)} <span class=\"cnt\">（{len(items)} 篇）</span></h2>"
            f"{body}</section>"
        )

    stats_blocks = "".join(
        f'<div class="stat">{html.escape(name)}: <b>{len(items)}</b> 篇</div>'
        for name, items in feed_results.items()
    )
    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>文献速递 · {run_date.isoformat()}</title>
<style>{CSS}</style>
</head>
<body>
<header>
  <h1>📚 文献速递看板</h1>
  <div class="sub">运行日期：{run_date.isoformat()} · 共命中 {total} 篇 · 数据来源：Crossref + PubMed</div>
  <div class="stats">{stats_blocks}</div>
</header>
<main>
  {"".join(sections)}
</main>
<footer>
  <div class="hint">💡 Zotero 一键添加：点击「+ 添加至 Zotero」，浏览器需安装
  <a href="https://www.zotero.org/download/connectors" target="_blank" style="color:var(--accent)">Zotero Connector</a> 插件，
  打开后自动抓取该文章元数据入库。每日 09:00（北京时间）自动更新。</div>
</footer>
</body>
</html>"""
    (out_dir / "index.html").write_text(page, encoding="utf-8")

    data = {
        "date": run_date.isoformat(),
        "total": total,
        "feeds": {
            name: [
                {
                    "doi": p.doi,
                    "title": p.title,
                    "journal": p.journal,
                    "published_online": p.published_online,
                    "is_early_access": p.is_early_access,
                    "authors": [a.full_name for a in p.authors],
                    "abstract": p.abstract[:2000],
                    "url": p.url,
                    "reasons": m.reasons,
                    "is_new": p.doi.lower() in new_dois,
                }
                for p, m in items
            ]
            for name, items in feed_results.items()
        },
    }
    (out_dir / "data.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out_dir / "index.html"
