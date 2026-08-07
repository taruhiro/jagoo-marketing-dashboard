#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
data/dashboard.json をテンプレートに埋め込んで dist/dashboard.html を生成する。

使い方:
  python3 scripts/build_dashboard.py
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "template" / "dashboard_template.html"
DATA = REPO_ROOT / "data" / "dashboard.json"
DIST = REPO_ROOT / "dist"
OUT = DIST / "dashboard.html"

MARK = "/*DATA@*/null/*@DATA*/"


def main():
    html = TEMPLATE.read_text(encoding="utf-8")
    if MARK not in html:
        raise SystemExit("テンプレートに埋め込み目印 %s がありません" % MARK)
    data = json.loads(DATA.read_text(encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # scriptタグ内に埋め込むため、閉じタグ文字列をエスケープしておく
    payload = payload.replace("</", "<\\/")
    html = html.replace(MARK, "/*DATA@*/" + payload + "/*@DATA*/")
    DIST.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print("[build] 完了: %s (%d KB)" % (OUT, OUT.stat().st_size // 1024))


if __name__ == "__main__":
    main()
