#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HubSpotリード数（サービスページCV・WPダウンロード）をKPI設定シートから取得し、
data/hubspot_leads.json を更新するスクリプト。

出典: KPI設定シート「CV・受注集計」タブの「レコードソースの詳細1」別 月次CV表
  - svc_cv    = 「HP - 」で始まる行の合計（各サービスページの資料DL＋問い合わせ＋ご相談）
  - wp_doc_cv = 「お役立ち資料」で始まる行の合計（ホワイトペーパーDL）
  - form_cv   = 「.wpcf7-form」で始まる行の合計（記事内フォーム経由・フォーム名未設定のCV）
  - wp_cv     = wp_doc_cv + form_cv（KPIシート「リード数（SEO）」と同じ定義）

認証: Google Sheets API（spreadsheets.readonly）用のOAuth2認証情報
  - GitHub Actions: 環境変数 GSHEETS_CREDENTIALS_JSON
      {"client_id":..,"client_secret":..,"refresh_token":..}
  - ローカル: credentials-manager の gsheets_jagoo_oauth
認証情報が無い場合は何もせず終了コード2で終わる（collect_all.py 側は手動更新JSONにフォールバック）。

使い方:
  python3 scripts/fetch_hubspot_leads.py          # 取得して data/hubspot_leads.json を更新
  python3 scripts/fetch_hubspot_leads.py --check  # 取得結果を表示するだけ（ファイルは更新しない）
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
OUT_PATH = DATA_DIR / "hubspot_leads.json"
CRED_PATH = Path.home() / ".claude" / ".local" / "plugins" / "credentials-manager" / "credentials.json"

SHEET_ID = os.environ.get("HUBSPOT_LEADS_SHEET_ID", "1gk-i6K6CQq47OCg3fM84yzxarvAMGV52IsLfNjG1vj0")
SHEET_TAB = os.environ.get("HUBSPOT_LEADS_SHEET_TAB", "CV・受注集計")
HEADER_LABEL = "レコードソースの詳細1"


def log(msg):
    print("[hubspot_leads] " + str(msg), flush=True)


def load_cred():
    v = os.environ.get("GSHEETS_CREDENTIALS_JSON")
    if v:
        return json.loads(v)
    if not CRED_PATH.exists():
        return None
    with open(CRED_PATH, encoding="utf-8") as f:
        return json.load(f).get("credentials", {}).get("gsheets_jagoo_oauth")


def access_token(cred):
    r = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": cred["client_id"],
            "client_secret": cred["client_secret"],
            "refresh_token": cred["refresh_token"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError("Google Sheets のトークン取得に失敗（HTTP %d）" % r.status_code)
    return r.json()["access_token"]


def fetch_grid(cred):
    rng = urllib.parse.quote("'%s'" % SHEET_TAB, safe="")
    url = "https://sheets.googleapis.com/v4/spreadsheets/%s/values/%s" % (SHEET_ID, rng)
    r = requests.get(url, headers={"Authorization": "Bearer " + access_token(cred)},
                     params={"valueRenderOption": "UNFORMATTED_VALUE"}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError("Google Sheets API エラー HTTP %d: %s" % (r.status_code, r.text[:200]))
    return r.json().get("values", [])


def to_int(v):
    if v in ("", None):
        return 0
    try:
        return int(float(str(v).replace(",", "")))
    except ValueError:
        return 0


def ym_of(cell):
    """'2026/08' や 2026/8 形式 → '2026-08'。月ヘッダ以外は None"""
    m = re.match(r"^\s*(\d{4})[/\-](\d{1,2})\s*$", str(cell))
    if not m:
        return None
    return "%s-%02d" % (m.group(1), int(m.group(2)))


def parse(grid):
    """シートの値グリッドから {ym: {svc_cv, wp_cv, wp_doc_cv, form_cv}} を作る"""
    hdr_row = hdr_col = None
    for i, row in enumerate(grid):
        for j, c in enumerate(row):
            if str(c).strip() == HEADER_LABEL:
                hdr_row, hdr_col = i, j
                break
        if hdr_row is not None:
            break
    if hdr_row is None:
        raise RuntimeError("シートに「%s」の見出しが見つからない" % HEADER_LABEL)

    months = {}  # 列番号 → ym
    for j, c in enumerate(grid[hdr_row][hdr_col + 1:], start=hdr_col + 1):
        ym = ym_of(c)
        if ym:
            months[j] = ym
    if not months:
        raise RuntimeError("月ヘッダが見つからない")

    out = {ym: {"svc_cv": 0, "wp_doc_cv": 0, "form_cv": 0} for ym in months.values()}
    for row in grid[hdr_row + 1:]:
        name = str(row[hdr_col]).strip() if len(row) > hdr_col else ""
        if not name:
            continue
        if name.startswith("HP - "):
            key = "svc_cv"
        elif name.startswith("お役立ち資料"):
            key = "wp_doc_cv"
        elif name.startswith(".wpcf7"):
            key = "form_cv"
        else:
            continue
        for j, ym in months.items():
            out[ym][key] += to_int(row[j]) if len(row) > j else 0
    for ym, v in out.items():
        v["wp_cv"] = v["wp_doc_cv"] + v["form_cv"]
    return out


def build_json(monthly):
    today = dt.date.today()
    cur = today.strftime("%Y-%m")
    return {
        "updated": today.isoformat(),
        "source": "KPI設定シート（%s）「%s」タブ %s別CV（自動取得）" % (SHEET_ID, SHEET_TAB, HEADER_LABEL),
        "note": "HubSpotのリード数。svc_cv=「HP -」行の合計（各サービスページの資料DL＋問い合わせ＋ご相談）。"
                "wp_doc_cv=「お役立ち資料」行の合計（ホワイトペーパーDL）。form_cv=「.wpcf7-form」行の合計"
                "（記事内フォーム経由・フォーム名未設定のCV）。wp_cv=wp_doc_cv+form_cv（KPIシート「リード数（SEO）」と同じ定義）",
        "current_month_asof": today.isoformat() if cur in monthly else None,
        "monthly": {ym: {"svc_cv": v["svc_cv"], "wp_cv": v["wp_cv"],
                         "wp_doc_cv": v["wp_doc_cv"], "form_cv": v["form_cv"]}
                    for ym, v in sorted(monthly.items())},
    }


def fetch():
    """認証情報があれば取得してdictを返す。無ければNone"""
    cred = load_cred()
    if not cred:
        return None
    return build_json(parse(fetch_grid(cred)))


def main():
    check = "--check" in sys.argv
    data = fetch()
    if data is None:
        log("Google Sheets用の認証情報（GSHEETS_CREDENTIALS_JSON / gsheets_jagoo_oauth）が未設定のためスキップ")
        sys.exit(2)
    if check:
        for ym, v in data["monthly"].items():
            log("%s svc=%d wp=%d (doc=%d form=%d)" % (ym, v["svc_cv"], v["wp_cv"], v["wp_doc_cv"], v["form_cv"]))
        return
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    log("更新: %s（%d ヶ月分）" % (OUT_PATH, len(data["monthly"])))


if __name__ == "__main__":
    main()
