"""Server酱（方糖）微信推送。https://sct.ftqq.com"""
from __future__ import annotations

import logging
import os
from typing import List, Tuple

import requests

from ..config import WeChatSettings
from ..models import Paper

log = logging.getLogger(__name__)

API_TMPL = "https://sctapi.ftqq.com/{sendkey}.send"


def _author_line(p: Paper, max_n: int = 6) -> str:
    """作者展示：6 位以内全显示，超过 6 位显示前 3 + 后 3。"""
    names = [a.full_name for a in p.authors if a.full_name]
    if not names:
        return ""
    if len(names) <= max_n:
        return ", ".join(names)
    return f"{', '.join(names[:3])} … {', '.join(names[-3:])}（共 {len(names)} 位作者）"


def push_wechat(
    settings: WeChatSettings,
    feed_results: List[Tuple[str, List[Paper]]],
    report_url: str = "",
) -> bool:
    """推送微信消息。feed_results: [(feed_name, papers)]，已按 feed 分组排序。"""
    sendkey = os.environ.get(settings.sendkey_env, "").strip()
    if not settings.enabled or not sendkey:
        log.info("微信推送未启用或缺少 %s 环境变量，跳过", settings.sendkey_env)
        return False

    total = sum(len(papers) for _, papers in feed_results)
    if total == 0:
        log.info("本次没有新命中，不推送")
        return True

    title = f"{settings.title_prefix}：今日 {total} 篇新文献"
    # Server酱 title 建议 ≤ 32 字符
    title = title[:32]

    lines = []
    remaining = settings.max_papers_per_day
    for feed_name, papers in feed_results:
        if remaining <= 0:
            break
        lines.append(f"## 📌 {feed_name}（{len(papers)} 篇）")
        for p in papers:
            if remaining <= 0:
                lines.append(f"> 还有更多，见网页看板：{report_url}" if report_url else "")
                break
            remaining -= 1
            tag = "🟢提前在线" if p.is_early_access else ""
            lines.append(
                f"- **{p.title}** {tag}\n"
                f"  - {p.journal} | {p.published_online or ''}\n"
                f"  - {_author_line(p)}\n"
                f"  - [打开原文]({p.url or ('https://doi.org/' + p.doi)})"
            )
    if report_url:
        lines.append(f"\n---\n[查看网页看板完整列表]({report_url})")

    desp = "\n\n".join(lines)
    try:
        resp = requests.post(
            API_TMPL.format(sendkey=sendkey),
            data={"title": title, "desp": desp},
            timeout=20,
        )
        data = resp.json()
        if data.get("code") == 0:
            log.info("微信推送成功：%s", title)
            return True
        log.error("微信推送失败：%s", data)
        return False
    except (requests.RequestException, ValueError) as e:
        log.error("微信推送异常：%s", e)
        return False
