# jagoo マーケティングダッシュボード

GA4・Google Search Console・Ahrefs・HubSpot のデータを毎朝自動で取得し、
パスワード保護つきのWebページ（GitHub Pages）として公開するダッシュボード。

## 見られる数値（月次・直近13ヶ月）

- セッション・CV・新規ユーザー・チャネル別流入（GA4。国=日本のみで集計）
- SEO記事（/column/×オーガニック）のセッション・CV
- SEO記事 → 資料DLページ（/document/）への遷移率
- 資料DLページ → CV率（近似値）
- GSC クリック・表示回数・平均掲載順位・指名検索クリック
- Ahrefs キーワード総数・1〜3位KW数・ドメインレーティング（月末スナップショット）
- HubSpot 新規リード・新規商談・成約（APIトークン設定後に表示）

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
| HUBSPOT_TOKEN | HubSpotプライベートアプリのトークン（未設定でも動く。HubSpotタブが「未接続」表示になるだけ） |
| DASH_ID / DASH_PW | ダッシュボード閲覧用のIDとパスワード |

## ローカルでの実行

ローカルでは credentials-manager の認証情報を自動で読むため、Secrets設定なしで動く。

```
pip install -r requirements.txt
python3 scripts/collect_all.py      # データ取得
python3 scripts/build_dashboard.py  # dist/dashboard.html 生成（ブラウザで開いて確認）
DASH_ID=xxx DASH_PW=yyy node scripts/lock_dashboard.js dist/dashboard.html docs/index.html
```

## パスワードの変更

Secrets の DASH_ID / DASH_PW を変えて、Actionsタブから update-dashboard を手動実行する。
次回の自動更新以降は新しいパスワードでしか開けなくなる。

## 注意

- data/ahrefs_history.json はコミットされる（月次履歴の保存用）。中身はAhrefsで誰でも調べられる
  公開サイトのSEO指標のみで、クライアントの売上・リード数などは含まない
- 「資料DLページ→CV率」は近似値。GA4側で資料DL完了イベントが識別できるようになったら
  scripts/collect_all.py の定義を置き換える
