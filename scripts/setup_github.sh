#!/bin/bash
# GitHubへの初回登録を一括で行うスクリプト（gh auth login 済みであること）
#
# 使い方:
#   bash scripts/setup_github.sh public    # 公開リポジトリ（無料プラン向け。暗号化ページのみ公開なので数値は見えない）
#   bash scripts/setup_github.sh private   # 非公開リポジトリ（GitHub Pro以上。Pagesも非公開リポジトリで使える）
#
# やること:
#   1. GitHubにリポジトリ jagoo-marketing-dashboard を作成してプッシュ
#   2. Secrets（GA4/GSC/Ahrefs/閲覧ID・PW）を credentials-manager のファイルから直接登録（画面に値は出さない）
#   3. GitHub Pages を有効化（main ブランチの docs/ フォルダ）
#   4. 動作確認用にワークフローを1回起動

set -e
cd "$(dirname "$0")/.."
VIS="${1:-public}"
REPO="jagoo-marketing-dashboard"
CRED="$HOME/.claude/.local/plugins/credentials-manager/credentials.json"

if ! gh auth status >/dev/null 2>&1; then
  echo "先に gh auth login でログインしてください"; exit 1
fi
if [ ! -f "$CRED" ]; then
  echo "認証情報ファイルが見つかりません: $CRED"; exit 1
fi

OWNER=$(gh api user --jq .login)
echo "== 1/4 リポジトリ作成（$VIS）とプッシュ =="
if gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  echo "リポジトリは既に存在します。プッシュのみ行います"
  git remote get-url origin >/dev/null 2>&1 || git remote add origin "https://github.com/$OWNER/$REPO.git"
  git push -u origin main
else
  gh repo create "$REPO" "--$VIS" --source=. --push
fi

echo "== 2/4 Secrets登録（値は表示しません） =="
python3 -c "
import json
c = json.load(open('$CRED'))['credentials']['ga4_jagoo_oauth']
print(json.dumps({'client_id': c['client_id'], 'client_secret': c['client_secret'],
                  'refresh_token': c['refresh_token'], 'property_id': c['property_id']}))
" | gh secret set GA4_CREDENTIALS_JSON --repo "$OWNER/$REPO"
python3 -c "
import json
c = json.load(open('$CRED'))['credentials']['gsc_jagoo_oauth']
print(json.dumps({'client_id': c['client_id'], 'client_secret': c['client_secret'],
                  'refresh_token': c['refresh_token'],
                  'site_url': c.get('site_url', 'https://jagoo.co.jp/')}))
" | gh secret set GSC_CREDENTIALS_JSON --repo "$OWNER/$REPO"
python3 -c "
import json
c = json.load(open('$CRED'))['credentials']['ahrefs_jagoo']
print(c.get('token') or c.get('api_key') or c.get('value'))
" | gh secret set AHREFS_TOKEN --repo "$OWNER/$REPO"
python3 -c "
import json
print(json.load(open('$CRED'))['credentials']['dash_jagoo_login']['username'])
" | gh secret set DASH_ID --repo "$OWNER/$REPO"
python3 -c "
import json
print(json.load(open('$CRED'))['credentials']['dash_jagoo_login']['password'])
" | gh secret set DASH_PW --repo "$OWNER/$REPO"
echo "登録済みSecrets:"
gh secret list --repo "$OWNER/$REPO"

echo "== 3/4 GitHub Pages 有効化 =="
if gh api "repos/$OWNER/$REPO/pages" >/dev/null 2>&1; then
  echo "Pagesは有効化済みです"
else
  gh api -X POST "repos/$OWNER/$REPO/pages" \
    -f "source[branch]=main" -f "source[path]=/docs" >/dev/null
  echo "有効化しました"
fi

echo "== 4/4 自動更新ワークフローを1回起動 =="
gh workflow run update-dashboard --repo "$OWNER/$REPO" 2>/dev/null || \
  echo "（ワークフローの認識に数分かかることがあります。あとで Actions タブから Run workflow でも可）"

echo ""
echo "完了。数分後に以下のURLでダッシュボードが開けます:"
echo "  https://$OWNER.github.io/$REPO/"
echo "閲覧ID・パスワードは credentials-manager の dash_jagoo_login に保存されています"
