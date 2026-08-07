// jagooマーケダッシュボードに閲覧パスワードを付けるビルドスクリプト（W2方式の移植）
// 使い方: DASH_ID=... DASH_PW=... node scripts/lock_dashboard.js <元HTML> <出力HTML>
// 仕組み: 本体HTMLをgzip圧縮し、ID+パスワードから導出した鍵(PBKDF2 310,000回)でAES-256-GCM暗号化。
//   出力HTMLにはログイン画面と暗号文だけが入り、ID・パスワードの平文もハッシュも一切含まれない。
//   正しいID・パスワードを入力した時だけブラウザ内で復号されて表示される。
//   → 暗号文しか載らないため、GitHub Pagesで公開しても中身は見えない。

const fs = require('fs');
const zlib = require('zlib');
const crypto = require('crypto');

const [, , srcPath, outPath] = process.argv;
const id = process.env.DASH_ID;
const pw = process.env.DASH_PW;
if (!srcPath || !outPath) {
  console.error('usage: DASH_ID=... DASH_PW=... node scripts/lock_dashboard.js <src.html> <out.html>');
  process.exit(1);
}
if (!id || !pw) {
  console.error('環境変数 DASH_ID / DASH_PW が設定されていません');
  process.exit(1);
}

const html = fs.readFileSync(srcPath, 'utf8');
const gz = zlib.gzipSync(Buffer.from(html, 'utf8'), { level: 9 });

const iterations = 310000;
const salt = crypto.randomBytes(16);
const iv = crypto.randomBytes(12);
const key = crypto.pbkdf2Sync(id + '\u0000' + pw, salt, iterations, 32, 'sha256');
const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
const enc = Buffer.concat([cipher.update(gz), cipher.final(), cipher.getAuthTag()]);

const payload = JSON.stringify({
  v: 1,
  it: iterations,
  salt: salt.toString('base64'),
  iv: iv.toString('base64'),
  data: enc.toString('base64'),
});

const page = `<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>jagoo マーケティングダッシュボード</title>
<style>
:root{
  --bg:#F4F6F8; --surface:#FFFFFF; --ink:#1C2733; --muted:#5E6E7C;
  --line:#D9E0E6; --accent:#0E6E8C;
  --bad:#C8442C; --bad-bg:#F8E9E4;
  --shadow:0 1px 3px rgba(28,39,51,.08);
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#10161D; --surface:#1A222C; --ink:#E6EDF3; --muted:#93A3B1;
    --line:#2E3B48; --accent:#5BAECB;
    --bad:#E4785C; --bad-bg:#3A2620;
    --shadow:0 1px 3px rgba(0,0,0,.4);
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN","Hiragino Sans",Meiryo,sans-serif;
  font-size:14px;line-height:1.6;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;box-shadow:var(--shadow);
  width:100%;max-width:380px;padding:32px 28px;}
.card h1{font-size:17px;margin:0 0 4px;letter-spacing:.02em;}
.card p.note{margin:0 0 22px;color:var(--muted);font-size:12px;}
label{display:block;font-size:12px;font-weight:700;color:var(--muted);margin:14px 0 4px;letter-spacing:.04em;}
input{width:100%;padding:10px 12px;font-size:14px;border:1px solid var(--line);border-radius:6px;
  background:var(--bg);color:var(--ink);font-family:inherit;}
input:focus{outline:2px solid var(--accent);outline-offset:0;border-color:var(--accent);}
button{width:100%;margin-top:22px;padding:11px;font-size:14px;font-weight:700;font-family:inherit;
  color:#fff;background:var(--accent);border:0;border-radius:6px;cursor:pointer;letter-spacing:.06em;}
button:disabled{opacity:.6;cursor:wait;}
.err{display:none;margin-top:14px;padding:9px 12px;font-size:12px;border-radius:6px;
  color:var(--bad);background:var(--bad-bg);}
.err.show{display:block;}
</style></head><body>
<div class="card">
  <h1>jagoo マーケティングダッシュボード</h1>
  <p class="note">閲覧にはIDとパスワードが必要です。</p>
  <form id="f">
    <label for="uid">ID</label>
    <input id="uid" autocomplete="username" required autofocus>
    <label for="upw">パスワード</label>
    <input id="upw" type="password" autocomplete="current-password" required>
    <button id="btn" type="submit">ログイン</button>
    <div class="err" id="err">IDまたはパスワードが違います。</div>
  </form>
</div>
<script>
const P = ${payload};
const b64 = s => {
  const bin = atob(s), n = bin.length, out = new Uint8Array(n);
  for (let i = 0; i < n; i++) out[i] = bin.charCodeAt(i);
  return out;
};
async function deriveKey(id, pw){
  const km = await crypto.subtle.importKey('raw', new TextEncoder().encode(id + '\\u0000' + pw), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey({name:'PBKDF2', salt:b64(P.salt), iterations:P.it, hash:'SHA-256'}, km,
    {name:'AES-GCM', length:256}, true, ['decrypt']);
}
async function decryptAndRender(key){
  const gz = await crypto.subtle.decrypt({name:'AES-GCM', iv:b64(P.iv)}, key, b64(P.data));
  const html = await new Response(new Blob([gz]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
  try{ sessionStorage.setItem('jagoodash_k', btoa(String.fromCharCode(...new Uint8Array(await crypto.subtle.exportKey('raw', key))))); }catch(e){}
  const fr = document.createElement('iframe');
  fr.setAttribute('style', 'position:fixed;inset:0;width:100%;height:100%;border:0;background:var(--bg)');
  fr.setAttribute('title', 'dashboard');
  fr.srcdoc = html;
  document.body.replaceChildren(fr);
  document.body.style.padding = '0';
}
(async () => {
  const saved = sessionStorage.getItem('jagoodash_k');
  if(saved){
    try{
      const key = await crypto.subtle.importKey('raw', b64(saved), 'AES-GCM', true, ['decrypt']);
      await decryptAndRender(key);
    }catch(e){ try{ sessionStorage.removeItem('jagoodash_k'); }catch(_){} }
  }
})();
document.getElementById('f').addEventListener('submit', async ev => {
  ev.preventDefault();
  const btn = document.getElementById('btn'), err = document.getElementById('err');
  err.classList.remove('show');
  btn.disabled = true; btn.textContent = '確認中…';
  try{
    const key = await deriveKey(document.getElementById('uid').value.trim(), document.getElementById('upw').value);
    await decryptAndRender(key);
  }catch(e){
    err.classList.add('show');
    btn.disabled = false; btn.textContent = 'ログイン';
    document.getElementById('upw').value = '';
    document.getElementById('upw').focus();
  }
});
</script></body></html>
`;

fs.writeFileSync(outPath, page, 'utf8');
console.log('OK: ' + outPath + ' (' + Math.round(fs.statSync(outPath).size / 1024) + ' KB, 元 ' + Math.round(html.length / 1024) + ' KB)');
