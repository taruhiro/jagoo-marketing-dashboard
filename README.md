# jagoo マーケティングダッシュボード

GA4・Google Search Console・Ahrefs のデータを毎朝自動で取得し、
パスワード保護つきのWebページ（GitHub Pages）として公開するダッシュボード。

## 見られる数値（月次・直近13ヶ月。画面右上で開始月〜終了月を選べる）

- セッション・CV・新規ユーザー・チャネル別流入（GA4。SEO記事セッション以外は国=日本のみで集計）
- SEO記事セッション（/column/配下の記事を閲覧したオーガニック検索セッション・国フィルタなし。
  KPI設定シート「新規セッション数」と同じ条件）とSEO記事CV（記事に着地したオーガニック検索セッション中のキーイベント）
- SEO記事 → 資料DLページ（/document/）への遷移率
- 資料DL完了率（サンクスページ＝URLに complete/ を含むページへの到達で実測）
- サービスページ別ファネル: /service/◯◯/ → /service/◯◯/document/ → …/document/thanks/ の
  各セッション数と遷移率（8サービス）
- GSC クリック・表示回数・平均掲載順位・指名検索クリック
- Ahrefs キーワード総数・1〜3位KW数・ドメインレーティング（月末スナップショット）
- HubSpotリード数（サービスページCV・WPダウンロード。TOP・サービス流入タブ）。出典はKPI設定シート
  「CV・受注集計」タブ。Google Sheets用の認証情報（Secret GSHEETS_CREDENTIALS_JSON）があれば毎朝自動取得、
  無い間は Secret HUBSPOT_LEADS_JSON（手動更新値）を使う → 下記「HubSpotリード数の更新」参照
- HubSpot APIからの直接取得は現在未使用（HUBSPOT_TOKEN を設定すれば再開できる）

## 仕組み

```
GitHub Actions（毎朝 JST 7:30）
  → scripts/collect_all.py   各APIからデータ取得 → data/dashboard.json（コミットしない）
  → scripts/build_dashboard.py  テンプレートにデータを埋め込み → dist/dashboard.html（コミットしない）
  → scripts/lock_dashboard.js   パスワードで暗号化 → docs/index.html（これだけコミット）
  → GitHub Pages が docs/ を公開
```

数値の入った生データはコミットせず、公開されるのは暗号化済みのページだけ。
正しいID・パスワードを入力した人のブラウザ内でだけ復号されて表示される。

## 必要なSecrets（リポジトリ Settings → Secrets and variables → Actions）

| 名前 | 中身 |
|------|------|
| GA4_CREDENTIALS_JSON | `{"client_id":..,"client_secret":..,"refresh_token":..,"property_id":"444624302"}` |
| GSC_CREDENTIALS_JSON | `{"client_id":..,"client_secret":..,"refresh_token":..,"site_url":"https://jagoo.co.jp/"}` |
| AHREFS_TOKEN | AhrefsのAPIトークン |
| HUBSPOT_TOKEN | （任意・現在未使用）HubSpotプライベートアプリのトークン |
| GSHEETS_CREDENTIALS_JSON | （推奨）`{"client_id":..,"client_secret":..,"refresh_token":..}` Google Sheets読み取り用OAuth（spreadsheets.readonly）。設定するとHubSpotリード数を毎朝自動取得 |
| HUBSPOT_LEADS_JSON | （GSHEETS未設定時の代替）data/hubspot_leads.json と同じ形式のJSON。手動更新のため古くなりやすい |
| DASH_ID / DASH_PW | ダッシュボード閲覧用のIDとパスワード |

## ローカルでの実行

ローカルでは credentials-manager の認証情報を自動で読むため、Secrets設定なしで動く。

```
pip install -r requirements.txt
python3 scripts/collect_all.py      # データ取得
python3 scripts/build_dashboard.py  # dist/dashboard.html 生成（ブラウザで開いて確認）
DASH_ID=xxx DASH_PW=yyy node scripts/lock_dashboard.js dist/dashboard.html docs/index.html
```

## HubSpotリード数の更新

自動取得（推奨）: Google Sheets API の読み取り権限つきOAuth認証情報を credentials-manager に
`gsheets_jagoo_oauth` として保存し、同じ内容を Secret GSHEETS_CREDENTIALS_JSON に登録する。
以後は collect_all.py が毎朝 scripts/fetch_hubspot_leads.py 経由でシートから取得する。

```
python3 scripts/fetch_hubspot_leads.py --check   # 取得結果を確認（ファイル更新なし）
python3 scripts/fetch_hubspot_leads.py           # data/hubspot_leads.json を更新
```

手動更新（認証情報が無い間の暫定）: シート「CV・受注集計」タブの値で data/hubspot_leads.json を
書き換え、`gh secret set HUBSPOT_LEADS_JSON < data/hubspot_leads.json` で Secret も更新する。
月が締まった後に更新しないと、月途中の値が当月確定値として表示され続ける（2026年8月度で発生）。

## パスワードの変更

Secrets の DASH_ID / DASH_PW を変えて、Actionsタブから update-dashboard を手動実行する。
次回の自動更新以降は新しいパスワードでしか開けなくなる。

## 注意

- data/ahrefs_history.json はコミットされる（月次履歴の保存用）。中身はAhrefsで誰でも調べられる
  公開サイトのSEO指標のみで、クライアントの売上・リード数などは含まない
- 「記事→資料DLページ遷移率」は、ブラウザ設定で移動元が取れない閲覧があるため低めに出る近似値。
  資料DL完了（complete/ 到達）とサービス別ファネルはページ到達ベースの実測値
- サービスページの一覧はサイトマップシート（統合管理スプレッドシートの
  「サイトマップ・ページ構成」タブ）に基づく。サービスが増えたら scripts/collect_all.py の
  SERVICES に1行追加する
