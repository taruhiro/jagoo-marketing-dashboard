#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jagoo マーケティングダッシュボード データ収集スクリプト

GA4 / Google Search Console / Ahrefs / HubSpot から直近13ヶ月の月次データを取得し、
data/dashboard.json に保存する。

使い方:
  python3 scripts/collect_all.py

認証情報の解決順:
  1. 環境変数（GitHub Actions用）
     GA4_CREDENTIALS_JSON / GSC_CREDENTIALS_JSON（client_id, client_secret,
     refresh_token, property_id / site_url を含むJSON文字列）
     AHREFS_TOKEN / HUBSPOT_TOKEN（トークン文字列）
  2. ローカルの credentials-manager ストア
     ~/.claude/.local/plugins/credentials-manager/credentials.json
     （キー: ga4_jagoo_oauth / gsc_jagoo_oauth / ahrefs_jagoo / hubspot_jagoo）

注意（jagoo-seo-datapack から引き継いだ既知の落とし穴）:
  - GA4 は全クエリに country == Japan を強制（海外ボット除外）
  - GA4 の期間比較は dateRanges 複数指定を使わない（ラベル逆転バグ）。
    本スクリプトは単一期間 + yearMonth ディメンションで月次を取るため影響なし
  - CV指標は keyEvents。HTTP 400 なら conversions にフォールバック
  - 認証情報の値はログに一切出さない
"""

import datetime as dt
import json
import os
import sys
import urllib.parse
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
OUT_PATH = DATA_DIR / "dashboard.json"
AHREFS_HISTORY = DATA_DIR / "ahrefs_history.json"
CRED_PATH = Path.home() / ".claude/.local/plugins/credentials-manager/credentials.json"

SITE_HOST = "jagoo.co.jp"
MONTHS_BACK = 13  # 当月含む13ヶ月（前年同月比が見られる長さ）
JST = dt.timezone(dt.timedelta(hours=9))

# 指名検索とみなす検索語（正規表現）。GSCのクエリに部分一致
BRAND_REGEX = "ジャグー株式会社|ジャグー|jagoo"


def log(msg):
    print("[collect] " + str(msg), flush=True)


# ---------------------------------------------------------------- 認証情報
def _local_store():
    if not CRED_PATH.exists():
        return {}
    with open(CRED_PATH, encoding="utf-8") as f:
        return json.load(f).get("credentials", {})


def cred_json(env_var, store_key):
    """JSONオブジェクト型の認証情報（GA4/GSC）"""
    v = os.environ.get(env_var)
    if v:
        return json.loads(v)
    return _local_store().get(store_key)


def cred_token(env_var, store_key, field="token"):
    """トークン文字列型の認証情報（Ahrefs/HubSpot）"""
    v = os.environ.get(env_var)
    if v:
        return v.strip()
    c = _local_store().get(store_key)
    if not c:
        return None
    return c.get(field) or c.get("api_key") or c.get("value")


def google_access_token(cred, label):
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
        err = ""
        try:
            err = r.json().get("error", "")
        except Exception:
            pass
        raise RuntimeError("%s のトークン取得に失敗（HTTP %d, %s）" % (label, r.status_code, err))
    return r.json()["access_token"]


# ---------------------------------------------------------------- GA4
def f_contains(field, value):
    return {"filter": {"fieldName": field, "stringFilter": {"matchType": "CONTAINS", "value": value}}}


def f_exact(field, value):
    return {"filter": {"fieldName": field, "stringFilter": {"matchType": "EXACT", "value": value}}}


def f_and(*filters):
    return {"andGroup": {"expressions": list(filters)}}


def f_inlist(field, values):
    return {"filter": {"fieldName": field, "inListFilter": {"values": values}}}


class GA4:
    def __init__(self, cred):
        self.property_id = cred["property_id"]
        self.token = google_access_token(cred, "GA4")
        self.cv_metric = "keyEvents"

    def run_report(self, body):
        url = "https://analyticsdata.googleapis.com/v1beta/properties/%s:runReport" % self.property_id
        r = requests.post(url, headers={"Authorization": "Bearer " + self.token}, json=body, timeout=60)
        if r.status_code == 400 and self.cv_metric == "keyEvents" and "keyEvents" in r.text:
            self.cv_metric = "conversions"
            body = json.loads(json.dumps(body).replace("keyEvents", "conversions"))
            r = requests.post(url, headers={"Authorization": "Bearer " + self.token}, json=body, timeout=60)
        if r.status_code != 200:
            raise RuntimeError("GA4 API エラー HTTP %d: %s" % (r.status_code, r.text[:300]))
        return r.json()

    def rows(self, start, end, dimensions, metrics, dim_filter=None, limit=100000, japan=True):
        """単一期間クエリ。country == Japan を既定で強制（japan=Falseで解除。
        CVR計測シートと突合する値など、シート側が国フィルタなしの場合のみ使う）"""
        combined = dim_filter
        if japan:
            jp = f_exact("country", "Japan")
            combined = f_and(dim_filter, jp) if dim_filter else jp
        body = {
            "dateRanges": [{"startDate": start, "endDate": end}],
            "metrics": [{"name": m} for m in metrics],
            "limit": limit,
        }
        if combined:
            body["dimensionFilter"] = combined
        if dimensions:
            body["dimensions"] = [{"name": d} for d in dimensions]
        res = self.run_report(body)
        out = []
        for row in res.get("rows", []):
            dims = [d["value"] for d in row.get("dimensionValues", [])]
            mets = [float(m["value"] or 0) for m in row.get("metricValues", [])]
            out.append((dims, mets))
        return out


# SEO記事＝到着時の最初のページが/column/配下のセッション。
# 流入経路（チャネル）では絞らない（2026-08-08 Hiroto指示でOrganic Search限定を撤廃）
COLUMN_ARTICLES = f_contains("landingPage", "/column/")


def ym_key(yearmonth):
    """GA4のyearMonth値 '202608' → '2026-08'"""
    return yearmonth[:4] + "-" + yearmonth[4:]


def to_path(url_or_path):
    """URL/パスを正規化（クエリ除去・先頭スラッシュ・末尾スラッシュなしに統一）"""
    s = url_or_path
    if s.startswith("http"):
        s = urllib.parse.urlparse(s).path or "/"
    if "?" in s:
        s = s.split("?")[0]
    if not s.startswith("/"):
        s = "/" + s
    if len(s) > 1 and s.endswith("/"):
        s = s[:-1]
    return s


# サービスページの一覧（サイトマップシートより。/service/{key}/ 配下に document/ と document/thanks/ を持つ）
SERVICES = {
    "rakuten": "楽天",
    "amazon": "Amazon",
    "yahoo": "Yahoo!",
    "qoo10": "Qoo10",
    "tiktok": "TikTok Shop",
    "ec-site": "ECサイト構築",
    "lp": "LP制作",
    "advertising": "広告運用",
}


def svc_stage(path):
    """/service/配下のパスを（サービスキー, 段階）に分類。対象外は (None, None)"""
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "service" and parts[1] in SERVICES:
        rest = parts[2:]
        if not rest:
            return parts[1], "top"
        if rest == ["document"]:
            return parts[1], "document"
        if rest == ["document", "thanks"]:
            return parts[1], "thanks"
    return None, None


# モールジャンルの判別ルール（URLに含まれる文字列。判定順が重要）
GENRE_RULES = [
    ("rakuten", "楽天"),
    ("amazon", "Amazon"),
    ("yahoo", "Yahoo"),
    ("qoo10", "Qoo10"),
    ("tiktok", "TikTok Shop"),
]
GENRES = [g for _, g in GENRE_RULES] + ["その他"]


def genre_of(path):
    p = path.lower()
    for kw, g in GENRE_RULES:
        if kw in p:
            return g
    return "その他"


# ---------------------------------------------------------------- GSC
class GSC:
    def __init__(self, cred):
        self.site = cred.get("site_url", "https://jagoo.co.jp/")
        self.token = google_access_token(cred, "GSC")

    def query(self, body):
        url = "https://searchconsole.googleapis.com/webmasters/v3/sites/%s/searchAnalytics/query" % (
            urllib.parse.quote(self.site, safe="")
        )
        r = requests.post(url, headers={"Authorization": "Bearer " + self.token}, json=body, timeout=60)
        if r.status_code != 200:
            raise RuntimeError("GSC API エラー HTTP %d: %s" % (r.status_code, r.text[:300]))
        return r.json().get("rows", [])


# ---------------------------------------------------------------- Ahrefs
def ahrefs_get(token, endpoint, params):
    r = requests.get(
        "https://api.ahrefs.com/v3/" + endpoint,
        params=params,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError("Ahrefs API エラー HTTP %d: %s" % (r.status_code, r.text[:300]))
    return r.json()


def fetch_ahrefs_snapshot(token, date_str):
    m = ahrefs_get(token, "site-explorer/metrics",
                   {"target": SITE_HOST, "mode": "subdomains", "date": date_str, "output": "json"}
                   ).get("metrics", {})
    snap = {
        "org_keywords": m.get("org_keywords"),
        "org_keywords_1_3": m.get("org_keywords_1_3"),
        "org_traffic": m.get("org_traffic"),
    }
    try:
        dr = ahrefs_get(token, "site-explorer/domain-rating",
                        {"target": SITE_HOST, "date": date_str, "output": "json"})
        snap["domain_rating"] = (dr.get("domain_rating") or {}).get("domain_rating")
    except Exception:
        snap["domain_rating"] = None
    return snap


# ---------------------------------------------------------------- HubSpot
def hubspot_count(token, object_type, filters):
    """検索APIの total だけを使って件数を数える"""
    r = requests.post(
        "https://api.hubapi.com/crm/v3/objects/%s/search" % object_type,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        json={"filterGroups": [{"filters": filters}], "limit": 1},
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError("HubSpot API エラー HTTP %d（%s）: %s" % (r.status_code, object_type, r.text[:200]))
    return r.json().get("total", 0)


def month_range_ms(ym):
    """'2026-08' → (月初0時, 翌月初0時) のUNIXミリ秒（JST基準）"""
    y, m = int(ym[:4]), int(ym[5:])
    start = dt.datetime(y, m, 1, tzinfo=JST)
    end = dt.datetime(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1, tzinfo=JST)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def collect_hubspot(token, months):
    monthly = {}
    for ym in months:
        s, e = month_range_ms(ym)
        between = lambda prop: [
            {"propertyName": prop, "operator": "GTE", "value": str(s)},
            {"propertyName": prop, "operator": "LT", "value": str(e)},
        ]
        monthly[ym] = {
            "contacts": hubspot_count(token, "contacts", between("createdate")),
            "deals": hubspot_count(token, "deals", between("createdate")),
            "won": hubspot_count(
                token, "deals",
                between("closedate") + [{"propertyName": "hs_is_closed_won", "operator": "EQ", "value": "true"}],
            ),
        }
    return monthly


# ---------------------------------------------------------------- main
def main():
    now = dt.datetime.now(JST)
    today = now.date()
    yesterday = today - dt.timedelta(days=1)

    # 当月を含む直近13ヶ月のリスト（"YYYY-MM"）
    months = []
    y, m = today.year, today.month
    for _ in range(MONTHS_BACK):
        months.append("%04d-%02d" % (y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    range_start = months[0] + "-01"
    range_end = yesterday.isoformat()

    log("対象期間: %s 〜 %s（%dヶ月）" % (range_start, range_end, len(months)))
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    out = {
        "generated_at": now.isoformat(timespec="seconds"),
        "site_host": SITE_HOST,
        "months": months,
        "current_month": months[-1],  # 当月は月途中の数値
        "range": {"start": range_start, "end": range_end},
    }
    errors = []

    # ============================================================ GA4
    try:
        log("GA4 取得中...")
        ga4 = GA4(cred_json("GA4_CREDENTIALS_JSON", "ga4_jagoo_oauth"))
        CV = ga4.cv_metric

        site = {}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth"],
                              ["sessions", CV, "newUsers", "engagementRate"]):
            site[ym_key(d[0])] = {"sessions": int(mt[0]), "cv": mt[1],
                                  "new_users": int(mt[2]), "eng_rate": mt[3]}

        # CVに含まれるキーイベント名の一覧（画面の注記に表示する）
        cv_events = sorted(
            [(d[0], mt[0]) for d, mt in ga4.rows(range_start, range_end, ["eventName"], [CV])
             if mt[0] > 0], key=lambda x: -x[1])
        cv_event_names = [name for name, _ in cv_events]

        channels = {}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "sessionDefaultChannelGroup"],
                              ["sessions", CV]):
            channels.setdefault(ym_key(d[0]), {})[d[1]] = {"sessions": int(mt[0]), "cv": mt[1]}

        seo = {}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth"], ["sessions", CV], COLUMN_ARTICLES):
            seo[ym_key(d[0])] = {"sessions": int(mt[0]), "cv": mt[1]}

        # 遷移率ファネル
        col_pv = {ym_key(d[0]): int(mt[0]) for d, mt in ga4.rows(
            range_start, range_end, ["yearMonth"], ["screenPageViews"],
            f_contains("pagePath", "/column/"))}
        col_to_doc = {ym_key(d[0]): int(mt[0]) for d, mt in ga4.rows(
            range_start, range_end, ["yearMonth"], ["screenPageViews"],
            f_and(f_contains("pageReferrer", "/column/"), f_contains("pagePath", "/document/")))}
        # 資料DLページ（/document/）のセッションと、DL完了（URLに complete/ を含むサンクスページ）のセッション
        doc_sessions = {ym_key(d[0]): int(mt[0]) for d, mt in ga4.rows(
            range_start, range_end, ["yearMonth"], ["sessions"],
            f_contains("pagePath", "/document/"))}
        complete_sessions = {ym_key(d[0]): int(mt[0]) for d, mt in ga4.rows(
            range_start, range_end, ["yearMonth"], ["sessions"],
            f_contains("pagePath", "complete/"))}

        funnel = {}
        for ym in months:
            cp = col_pv.get(ym, 0)
            cd = col_to_doc.get(ym, 0)
            ds = doc_sessions.get(ym, 0)
            cs = complete_sessions.get(ym, 0)
            funnel[ym] = {
                "col_pv": cp,
                "col_to_doc_pv": cd,
                "col_to_doc_rate": (cd / cp * 100.0) if cp else None,
                "doc_sessions": ds,
                "complete_sessions": cs,
                "complete_rate": (cs / ds * 100.0) if ds else None,
            }

        # サービス別ファネル: /service/X/ → /service/X/document/ → /service/X/document/thanks/
        services = {}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "pagePath"], ["sessions"],
                              f_contains("pagePath", "/service/")):
            svc, stage = svc_stage(to_path(d[1]))
            if not svc:
                continue
            s = services.setdefault(ym_key(d[0]), {}).setdefault(
                svc, {"top": 0, "document": 0, "thanks": 0})
            s[stage] += int(mt[0])

        # TOP・サービスページの流入経路分解とCVR（2026-08-20追加。SEOシミュレーション②の管理指標）
        # 到達＝TOPまたはサービス8ページを見たセッション（ページごとに数えるため複数ページ閲覧は重複あり。
        #   KPIシートのCVRツリーと同じ基準）
        # 着地＝そのページがサイト到着時の最初のページだったセッション（landingPageは末尾スラッシュなしで
        #   記録されるため、スラッシュあり・なし両方をリストに入れる）
        # 回遊到達（記事などサイト内から移動して到達した分）＝到達−着地。画面側で算出する
        svc_paths = ["/service/%s" % k for k in SERVICES]
        entry_pages = ["/"] + svc_paths + [p + "/" for p in svc_paths]
        service_entry = {ym: {"reach": 0, "land_top": 0, "land_svc": 0, "svc_cv": 0.0} for ym in months}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "pagePath"], ["sessions"],
                              f_inlist("pagePath", entry_pages)):
            ym = ym_key(d[0])
            if ym in service_entry:
                service_entry[ym]["reach"] += int(mt[0])
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "landingPage"], ["sessions"],
                              f_inlist("landingPage", entry_pages)):
            ym = ym_key(d[0])
            if ym not in service_entry:
                continue
            key = "land_top" if to_path(d[1]) == "/" else "land_svc"
            service_entry[ym][key] += int(mt[0])
        # サービスページCV＝GA4キーイベントのうち、各サービスの資料DL/問い合わせCV＋Eコマース総合資料CV。
        # CVR計測シート（GA4キーイベント数実績）と同じ定義。シートと突合できるよう国フィルタなしで集計する
        svc_cv_heads = ("Amazon支援", "Yahoo支援", "Qoo10支援", "TikTokShop支援", "楽天市場支援",
                        "モール広告運用", "自社ECサイト立ち上げ", "LP制作支援", "Eコマース事業総合支援")
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "eventName"], [CV], japan=False):
            name = d[1]
            if not (name.endswith("CV") and name.startswith(svc_cv_heads)):
                continue
            ym = ym_key(d[0])
            if ym in service_entry:
                service_entry[ym]["svc_cv"] += mt[0]
        out["service_entry"] = service_entry

        # SEO記事別: 月次セッション（流入ページ=/column/×オーガニック）
        articles = {}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "landingPage"], ["sessions"],
                              COLUMN_ARTICLES):
            path = to_path(d[1])
            ym = ym_key(d[0])
            a = articles.setdefault(path, {})
            a[ym] = a.get(ym, 0) + int(mt[0])

        # 記事別ファネル: 記事のPVと、その記事から/document/へ遷移したPV（月次）
        article_funnel = {}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "pagePath"], ["screenPageViews"],
                              f_contains("pagePath", "/column/")):
            path, ym = to_path(d[1]), ym_key(d[0])
            a = article_funnel.setdefault(path, {}).setdefault(ym, {"pv": 0, "to_doc": 0})
            a["pv"] += int(mt[0])
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "pageReferrer"], ["screenPageViews"],
                              f_and(f_contains("pageReferrer", "/column/"),
                                    f_contains("pagePath", "/document/"))):
            path, ym = to_path(d[1]), ym_key(d[0])
            if not path.startswith("/column/"):
                continue
            a = article_funnel.setdefault(path, {}).setdefault(ym, {"pv": 0, "to_doc": 0})
            a["to_doc"] += int(mt[0])

        # モールジャンル別ファネル（URLに rakuten/amazon/yahoo/qoo10/tiktok を含むかで判別）
        # 全段階をセッション数で統一（2026-08-10 Hiroto指示。記事・遷移は記事URLのジャンル、
        # 資料DL・DL完了はそのページ自身のURLのジャンルで集計する）
        genre_funnel = {ym: {g: {"col_sessions": 0, "to_doc_sessions": 0,
                                 "doc_sessions": 0, "complete_sessions": 0}
                             for g in GENRES} for ym in months}
        # 記事セッション数（そのジャンルの記事を見たセッション。複数記事を見たセッションは記事ごとに数える）
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "pagePath"], ["sessions"],
                              f_contains("pagePath", "/column/")):
            ym = ym_key(d[0])
            if ym in genre_funnel:
                genre_funnel[ym][genre_of(to_path(d[1]))]["col_sessions"] += int(mt[0])
        # 記事→資料DLのセッション数（その記事から/document/へ移動したことのあるセッション）
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "pageReferrer"], ["sessions"],
                              f_and(f_contains("pageReferrer", "/column/"),
                                    f_contains("pagePath", "/document/"))):
            path = to_path(d[1])
            if not path.startswith("/column/"):
                continue
            ym = ym_key(d[0])
            if ym in genre_funnel:
                genre_funnel[ym][genre_of(path)]["to_doc_sessions"] += int(mt[0])
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "pagePath"], ["sessions"],
                              f_contains("pagePath", "/document/")):
            ym = ym_key(d[0])
            if ym in genre_funnel:
                genre_funnel[ym][genre_of(to_path(d[1]))]["doc_sessions"] += int(mt[0])
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "pagePath"], ["sessions"],
                              f_contains("pagePath", "complete/")):
            ym = ym_key(d[0])
            if ym in genre_funnel:
                genre_funnel[ym][genre_of(to_path(d[1]))]["complete_sessions"] += int(mt[0])

        out["ga4"] = {"cv_metric": CV, "cv_events": cv_event_names,
                      "site": site, "channels": channels, "seo": seo}
        out["funnel"] = funnel
        out["services"] = services
        out["service_labels"] = SERVICES
        out["articles"] = articles
        out["article_funnel"] = article_funnel
        out["genre_funnel"] = genre_funnel
        out["genres"] = GENRES
    except Exception as e:
        errors.append("GA4: %s" % e)
        log("GA4 取得エラー: %s" % e)

    # ============================================================ GSC
    try:
        log("GSC 取得中...")
        gsc = GSC(cred_json("GSC_CREDENTIALS_JSON", "gsc_jagoo_oauth"))

        gsc_monthly = {}
        daily = gsc.query({"startDate": range_start, "endDate": range_end,
                           "dimensions": ["date"], "rowLimit": 5000})
        for row in daily:
            ym = row["keys"][0][:7]
            g = gsc_monthly.setdefault(ym, {"clicks": 0, "impressions": 0, "_pos_w": 0.0})
            g["clicks"] += row.get("clicks", 0)
            g["impressions"] += row.get("impressions", 0)
            g["_pos_w"] += row.get("position", 0) * row.get("impressions", 0)
        for ym, g in gsc_monthly.items():
            g["ctr"] = (g["clicks"] / g["impressions"] * 100.0) if g["impressions"] else 0.0
            g["position"] = (g.pop("_pos_w") / g["impressions"]) if g["impressions"] else None

        # 指名検索（月ごとに1クエリ）
        for ym in months:
            s = ym + "-01"
            e = min(dt.date(int(ym[:4]) + (1 if ym[5:] == "12" else 0),
                            1 if ym[5:] == "12" else int(ym[5:]) + 1, 1) - dt.timedelta(days=1),
                    yesterday).isoformat()
            if s > e:
                continue
            rows = gsc.query({
                "startDate": s, "endDate": e, "dimensions": ["query"], "rowLimit": 1000,
                "dimensionFilterGroups": [{"filters": [
                    {"dimension": "query", "operator": "includingRegex", "expression": BRAND_REGEX}]}],
            })
            brand = sum(r.get("clicks", 0) for r in rows)
            gsc_monthly.setdefault(ym, {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": None})
            gsc_monthly[ym]["brand_clicks"] = brand

        # SEO記事ごとの平均掲載順位（月別・表示回数で加重平均）
        article_positions = {}
        for ym in months:
            s = ym + "-01"
            e = min(dt.date(int(ym[:4]) + (1 if ym[5:] == "12" else 0),
                            1 if ym[5:] == "12" else int(ym[5:]) + 1, 1) - dt.timedelta(days=1),
                    yesterday).isoformat()
            if s > e:
                continue
            rows = gsc.query({
                "startDate": s, "endDate": e, "dimensions": ["page"], "rowLimit": 5000,
                "dimensionFilterGroups": [{"filters": [
                    {"dimension": "page", "operator": "contains", "expression": "/column/"}]}],
            })
            agg = {}
            for r in rows:
                path = to_path(r["keys"][0])
                a = agg.setdefault(path, {"pos_w": 0.0, "impr": 0})
                a["pos_w"] += r.get("position", 0) * r.get("impressions", 0)
                a["impr"] += r.get("impressions", 0)
            for path, a in agg.items():
                if a["impr"]:
                    article_positions.setdefault(path, {})[ym] = round(a["pos_w"] / a["impr"], 1)
        out["article_positions"] = article_positions

        # SEO記事ごとの表示回数・クリック（日別で取得。月別/週別は画面側で束ねる）
        article_gsc = {}
        start_row = 0
        while True:
            rows = gsc.query({
                "startDate": range_start, "endDate": range_end,
                "dimensions": ["page", "date"], "rowLimit": 25000, "startRow": start_row,
                "dimensionFilterGroups": [{"filters": [
                    {"dimension": "page", "operator": "contains", "expression": "/column/"}]}],
            })
            for r in rows:
                path = to_path(r["keys"][0])
                day = r["keys"][1]
                d0 = article_gsc.setdefault(path, {})
                prev = d0.get(day)
                c, i = r.get("clicks", 0), r.get("impressions", 0)
                d0[day] = [prev[0] + c, prev[1] + i] if prev else [c, i]
            if len(rows) < 25000:
                break
            start_row += 25000
        out["article_gsc"] = article_gsc
        log("GSC 記事別日次: %d記事" % len(article_gsc))

        out["gsc"] = gsc_monthly
    except Exception as e:
        errors.append("GSC: %s" % e)
        log("GSC 取得エラー: %s" % e)

    # ============================================================ Ahrefs
    try:
        log("Ahrefs 取得中...")
        token = cred_token("AHREFS_TOKEN", "ahrefs_jagoo")
        if not token:
            raise RuntimeError("Ahrefsトークンが見つかりません")

        history = {}
        if AHREFS_HISTORY.exists():
            with open(AHREFS_HISTORY, encoding="utf-8") as f:
                history = json.load(f)

        # 各月の月末（当月は昨日）のスナップショットを、未取得分だけ取得
        want_dates = []
        for ym in months:
            yy, mm = int(ym[:4]), int(ym[5:])
            month_end = (dt.date(yy + (1 if mm == 12 else 0), 1 if mm == 12 else mm + 1, 1)
                         - dt.timedelta(days=1))
            want_dates.append(min(month_end, yesterday).isoformat())
        # 取得失敗しても他の日付・前回までの履歴は活かす（Ahrefs側の一時エラー対策）
        fetch_errors = 0
        for d in want_dates:
            if d not in history or d == want_dates[-1]:  # 当月分は毎回更新
                try:
                    history[d] = fetch_ahrefs_snapshot(token, d)
                    log("Ahrefs スナップショット取得: %s" % d)
                except Exception as e:
                    fetch_errors += 1
                    log("Ahrefs %s の取得に失敗（履歴があればそれを使用）: %s" % (d, e))

        with open(AHREFS_HISTORY, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=1, sort_keys=True)
        out["ahrefs"] = {d: history[d] for d in want_dates if d in history}
        if fetch_errors and not out["ahrefs"]:
            raise RuntimeError("Ahrefsの取得にすべて失敗しました")
    except Exception as e:
        errors.append("Ahrefs: %s" % e)
        log("Ahrefs 取得エラー: %s" % e)

    # ============================================================ HubSpot
    hs_token = cred_token("HUBSPOT_TOKEN", "hubspot_jagoo")
    if hs_token:
        try:
            log("HubSpot 取得中...")
            out["hubspot"] = {"available": True, "monthly": collect_hubspot(hs_token, months)}
        except Exception as e:
            errors.append("HubSpot: %s" % e)
            out["hubspot"] = {"available": False, "note": "取得エラー。詳細はerrors参照"}
            log("HubSpot 取得エラー: %s" % e)
    else:
        out["hubspot"] = {"available": False,
                          "note": "HubSpotのAPIトークン未設定。jagoo側でプライベートアプリのトークン発行後、"
                                  "HUBSPOT_TOKEN を設定すると自動で表示されます"}
        log("HubSpot: トークン未設定のためスキップ")

    # 記事URL×注力KWの対応表（シート「SEO記事アクセス計測」から抽出した data/article_keywords.json）
    kw_path = DATA_DIR / "article_keywords.json"
    if kw_path.exists():
        with open(kw_path, encoding="utf-8") as f:
            out["article_keywords"] = json.load(f)
    else:
        out["article_keywords"] = {}

    out["errors"] = errors

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    log("完了: %s" % OUT_PATH)
    if errors:
        log("取得エラーあり: " + " / ".join(errors))
        # 主要ソース（GA4/GSC）が両方失敗したときだけ異常終了にする
        if any(e.startswith("GA4") for e in errors) and any(e.startswith("GSC") for e in errors):
            sys.exit(1)


if __name__ == "__main__":
    main()
