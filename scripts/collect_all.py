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
import unicodedata
import urllib.parse
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

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

# SEOのメインKW（2026-08-24 Hiroto提供の正式リスト・この並び順のまま画面に表示する）。
# GSCの検索キーワード別に月次のクリック・表示回数・CTR・掲載順位を集計する。
# 空白（半角・全角）の有無と大小文字・全半角の違いは同じKWとみなして合算する
# （例: 「amazon 倉庫」「amazon倉庫」「Amazon倉庫」→ すべて「amazon 倉庫」に合算）
MAIN_KEYWORDS = [
    "楽天 最強配送",
    "楽天 SEO",
    "rpp広告",
    "tda広告",
    "楽天 運営代行",
    "楽天 コンサル",
    "楽天 広告運用代行",
    "楽天 広告",
    "楽天 LP",
    "楽天 商品 ページ",
    "楽天 バナー",
    "楽天　skuプロジェクト",
    "楽天　メルマガ",
    "楽天 ec",
    "楽天 出店",
    "楽天 amazon 比較",
    "楽天 転換率",
    "楽天 レビュー クーポン 設定方法",
    "rpp 運用代行",
    "yahoo!ショッピング 優良配送",
    "amazon fba",
    "Yahoo!ショッピング　コンサル",
    "Yahoo!ショッピング　運営代行",
    "Yahoo!ショッピング 売上",
    "Yahoo!ショッピング　広告",
    "yahoo ショッピングvipスタンプ",
    "YCA広告",
    "メーカーアイテムマッチ",
    "Yahoo!ショッピング prオプション",
    "Yahoo!ショッピング 出店",
    "Yahoo!ショッピング seo",
    "ヤマトフルフィルメント",
    "楽天CSV",
    "楽天スーパーセールサーチ",
    "楽天 line",
    "楽天スーパーロジスティクス",
    "R-SNS",
    "クーポンアドバンス広告",
    "楽天 CPA広告",
    "楽天出店代行",
    "楽天 転売対策",
    "amazon 運用代行",
    "amazon コンサル",
    "amazon 広告",
    "amazon seo",
    "amazon 出品 方法",
    "amazon 出品 代行",
    "amazon 出店",
    "qoo10 出店",
    "qoo10 広告",
    "qoo10 運用代行",
    "qoo10 コンサル",
    "楽天 スーパーアフィリエイト",
    "楽天市場 イベント",
    "amazon広告運用代行",
    "amazon 広告運用",
    "amazon レビュー 依頼",
    "楽天 定期購入",
    "ec コンサル",
    "ec 運営代行",
    "ecモール",
    "ecモール 運営",
    "amazon 倉庫",
    "amazon セラーセントラル",
    "fba 料金シミュレーター",
    "QSM",
    "qoo10 メガポ",
    "amazon 返品理由",
    "amazon ギフト 設定",
    "amazon マーケットプレイス保証",
    "amazon 予約販売",
    "amazon ストアページ",
    "amazon 出品手数料",
    "amazon 検索キーワード",
    "amazon ベストセラー",
    "amazon 売上アップ",
    "amazon 商品ページ",
    "amazon 集客",
    "amazon 競合調査",
    "39ショップ",
    "oem odm 違い",
    "amazon ブランド登録",
    "acosとは",
    "楽天 違反",
    "楽天 コンテンツページ",
    "楽天市場 売れる",
    "スポンサーブランド広告",
    "楽天 サンキュークーポン",
    "amazon ABテスト",
    "楽天 ギフト対策",
    "amazon 売れない",
    "amazon カートボックス",
    "楽天 アクセス数",
    "スポンサーディスプレイ広告",
    "スポンサープロダクト広告",
    "amazon 値段推移",
    "amazonサジェスト",
    "amazon限定とは",
    "amazon プロモーション割引",
    "amazon asin",
    "amazon セッション",
    "amazon 出品者検索",
    "amazon 商品登録",
    "amazon 商品名ルール",
    "amazon ランキング 調べ方",
    "amazon レビュー削除",
    "amazon 在庫切れ",
    "商品紹介コンテンツ",
    "amazon dsp",
    "amazon ブランド分析",
    "TikTok Shop",
    "TikTok Shop 運営代行",
    "楽天 gold",
    "楽天 組み合わせ販売",
    "楽天 予約商品",
    "amazon 動画広告",
    "RMP広告",
    "楽天 外部リンク申請",
    "楽天 レビュー 増やす",
    "楽天スーパーDEAL",
    "amazon プライムマーク",
    "Amazon定期おトク便",
    "楽天ルームとは",
    "Amazonマーケットプレイスとは",
    "ECコンサル実態調査レポート",
    "Tiktok Shop 出店",
    "Amazon出品制限",
    "アマゾン楽天ヤフーどこが安い",
    "FBAマルチチャネル",
    "ブランドストア",
    "コマースアドマネージャー",
    "tiktok shop ライブコマース",
    "Amazonグローバルセリング",
    "amazon ブランドストーリー",
    "Amazon タイムセール",
    "TikTok広告",
    "Amazonベンダーセントラル",
    "Amazon 広告代理店",
    "Tiktok shop アフィリエイト",
    "TikTok Shop 事例",
    "gmv max tiktok",
    "TikTok Shopセラーセンター",
    "amazon おすすめ",
    "amazon クーポン",
    "amazon 代引き",
    "amazon 転売対策",
    "楽天 ソーシャルギフト",
    "qoo10 キャンセル要請",
    "qoo10 パワーセラー",
    "メガ割 とは",
    "楽天 billpay",
    "楽天 rms",
    "楽天 YouTube",
    "amazon vine",
    "tiktok ビジネスアカウントとは",
    "トクトクセールとは",
    "楽天 お買い物マラソン",
    "amazon キャンセル ペナルティ",
    "相乗り出品",
    "ギフティングとは",
    "ストアクリエイターPro ログイン",
    "TikTok for Business",
    "RFM分析",
    "楽天　AIコンシェルジュ",
    "TikTok Shop Liveオークション",
    "Amazonコンディションガイドライン",
]


def norm_kw(s):
    """KWの表記ゆれをまとめるための正規化。
    全角→半角（NFKC）・小文字化・空白（半角/全角）を全て除去"""
    s = unicodedata.normalize("NFKC", s).lower()
    return "".join(s.split())


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


# SEO記事セッション＝KPI設定シート「新規セッション数」と同じ定義（2026-09-03 Hiroto指示で統一）:
#   /column/ を含むページを閲覧したセッション × セッションのデフォルトチャネル＝Organic Search × 国フィルタなし。
#   （旧: 着地ページ=/column/ × 全チャネル × 国=日本。KPIシートと数千件ずれていた）
KPI_SEO_SESSIONS = f_and(f_contains("pagePath", "/column/"),
                         f_exact("sessionDefaultChannelGroup", "Organic Search"))
# SEO記事CV＝記事に着地したオーガニック検索セッション中に発生したキーイベント（CVはページ単位で数えられないため着地ベース）
COLUMN_ARTICLES = f_and(f_contains("landingPage", "/column/"),
                        f_exact("sessionDefaultChannelGroup", "Organic Search"))


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
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth"], ["sessions"], KPI_SEO_SESSIONS, japan=False):
            seo[ym_key(d[0])] = {"sessions": int(mt[0]), "cv": 0.0}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth"], [CV], COLUMN_ARTICLES, japan=False):
            seo.setdefault(ym_key(d[0]), {"sessions": 0, "cv": 0.0})["cv"] = mt[0]

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

        # TOP・サービスページの着地セッションとCVR（2026-08-20追加。SEOシミュレーション②の管理指標）
        # 着地＝そのページがサイト到着時の最初のページだったセッション（landingPageは末尾スラッシュなしで
        #   記録されるため、スラッシュあり・なし両方をリストに入れる）
        # reach（合計セッション）＝TOP直接着地＋サービスページ直接着地（2026-09-03 Hiroto指示で着地ベースに統一。
        #   旧: ページを見たセッションの閲覧ベース合計）
        svc_paths = ["/service/%s" % k for k in SERVICES]
        entry_pages = ["/"] + svc_paths + [p + "/" for p in svc_paths]
        service_entry = {ym: {"reach": 0, "land_top": 0, "land_svc": 0, "svc_cv": 0.0} for ym in months}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "landingPage"], ["sessions"],
                              f_inlist("landingPage", entry_pages)):
            ym = ym_key(d[0])
            if ym not in service_entry:
                continue
            key = "land_top" if to_path(d[1]) == "/" else "land_svc"
            service_entry[ym][key] += int(mt[0])
            service_entry[ym]["reach"] += int(mt[0])
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

        # SEO記事別: 月次セッション（その記事を閲覧したオーガニック検索セッション・国フィルタなし。KPIシート定義）
        # 複数記事を見たセッションは記事ごとに数えるため、記事の合計は seo[ym]["sessions"]（重複なし）より大きい
        articles = {}
        for d, mt in ga4.rows(range_start, range_end, ["yearMonth", "pagePath"], ["sessions"],
                              KPI_SEO_SESSIONS, japan=False):
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

        # メインKW別: 検索キーワード単位の月次クリック・表示回数・掲載順位
        # 月ごとに全クエリを取得し、正規化（norm_kw）が一致したものをKWに合算する。
        # ※GSCは件数の少ない検索語を「匿名クエリ」として返さないため、実際より少なめに出ることがある
        kw_by_norm = {}
        for kw in MAIN_KEYWORDS:
            kw_by_norm.setdefault(norm_kw(kw), kw)
        keyword_gsc = {kw: {} for kw in MAIN_KEYWORDS}
        # 表記ゆれの内訳（KWごとに、実際に検索された表記別の期間合計クリック・表示回数）
        kw_variants = {kw: {} for kw in MAIN_KEYWORDS}
        for ym in months:
            s = ym + "-01"
            e = min(dt.date(int(ym[:4]) + (1 if ym[5:] == "12" else 0),
                            1 if ym[5:] == "12" else int(ym[5:]) + 1, 1) - dt.timedelta(days=1),
                    yesterday).isoformat()
            if s > e:
                continue
            start_row = 0
            while True:
                rows = gsc.query({"startDate": s, "endDate": e, "dimensions": ["query"],
                                  "rowLimit": 25000, "startRow": start_row})
                for r in rows:
                    kw = kw_by_norm.get(norm_kw(r["keys"][0]))
                    if not kw:
                        continue
                    g = keyword_gsc[kw].setdefault(
                        ym, {"clicks": 0, "impressions": 0, "_pos_w": 0.0})
                    g["clicks"] += r.get("clicks", 0)
                    g["impressions"] += r.get("impressions", 0)
                    g["_pos_w"] += r.get("position", 0) * r.get("impressions", 0)
                    v = kw_variants[kw].setdefault(r["keys"][0], [0, 0])
                    v[0] += r.get("clicks", 0)
                    v[1] += r.get("impressions", 0)
                if len(rows) < 25000:
                    break
                start_row += 25000
        for kw, ms in keyword_gsc.items():
            for ym, g in ms.items():
                w = g.pop("_pos_w")
                g["position"] = round(w / g["impressions"], 1) if g["impressions"] else None
        out["keyword_gsc"] = keyword_gsc
        out["main_keywords"] = MAIN_KEYWORDS
        # 内訳は表示回数の多い順に最大10表記まで保存（[クリック, 表示回数]）
        out["keyword_variants"] = {
            kw: sorted(vs.items(), key=lambda x: -x[1][1])[:10]
            for kw, vs in kw_variants.items()}
        log("GSC メインKW別: %d/%dKWでデータあり" % (
            sum(1 for ms in keyword_gsc.values() if ms), len(MAIN_KEYWORDS)))

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

    # HubSpotリード数（WPダウンロード・サービスページCV）。手動更新
    # クライアント数値のためリポジトリには置かない。GitHub Actionsでは Secret HUBSPOT_LEADS_JSON、
    # ローカルでは data/hubspot_leads.json（.gitignore済み）から読む。
    # （HubSpotのAPIトークンが未設定のため自動取得できない。トークン設定後は collect_hubspot 側での自動化に置き換える）
    # 優先順位: (1) KPI設定シート「CV・受注集計」タブからの自動取得（Google Sheets用認証情報がある場合）
    #           (2) Secret HUBSPOT_LEADS_JSON  (3) data/hubspot_leads.json  ※(2)(3)は手動更新のため古くなりうる
    hs_env = os.environ.get("HUBSPOT_LEADS_JSON")
    hs_path = DATA_DIR / "hubspot_leads.json"
    out["hubspot_leads"] = {}
    try:
        import fetch_hubspot_leads
        auto = fetch_hubspot_leads.fetch()
    except Exception as e:
        auto = None
        errors.append("HubSpotリード数（シート自動取得）: %s" % e)
        log("HubSpotリード数 シート自動取得エラー: %s" % e)
    if auto:
        out["hubspot_leads"] = auto
        log("HubSpotリード数: KPI設定シートから自動取得（%d ヶ月分）" % len(auto.get("monthly", {})))
    elif hs_env:
        out["hubspot_leads"] = json.loads(hs_env)
        log("HubSpotリード数: Secret HUBSPOT_LEADS_JSON（手動更新値）を使用")
    elif hs_path.exists():
        with open(hs_path, encoding="utf-8") as f:
            out["hubspot_leads"] = json.load(f)
        log("HubSpotリード数: data/hubspot_leads.json（手動更新値）を使用")

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
