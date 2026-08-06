#!/usr/bin/env python3
"""PaperPush 文献速递主程序（CLI 模式）。

用法:
  python main.py [--config-dir config] [--since-days N] [--no-push] [--no-report]

完整功能（网页看板 / Feed 管理 / 设置 / 定时任务）请用:
  python app.py   或双击 start.bat
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from paperpush.pipeline import run_once


def main() -> int:
    parser = argparse.ArgumentParser(description="PaperPush 文献速递")
    parser.add_argument("--config-dir", default=str(Path(__file__).parent / "config"))
    parser.add_argument("--since-days", type=int, default=None)
    parser.add_argument("--no-push", action="store_true", help="跳过微信推送")
    parser.add_argument("--no-report", action="store_true", help="跳过网页看板生成")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    result = run_once(
        args.config_dir,
        push=not args.no_push,
        report=not args.no_report,
        since_days=args.since_days,
    )
    if not result["ok"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
