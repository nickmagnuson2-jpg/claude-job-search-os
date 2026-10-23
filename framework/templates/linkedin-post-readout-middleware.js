export const config = { matcher: '/:path*' };

const COOKIE = 'readout_auth';

async function tokenFor(password) {
  const bytes = new TextEncoder().encode('__COOKIE_SALT__:' + password);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function passwordPage(wrong) {
  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>__HEADLINE__</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{--paper:#eef0ec;--ink:#15171a;--ink-2:#464b51;--ink-3:#757b81;--rule:#c6cac4;color-scheme:light}
@media (prefers-color-scheme: dark){:root{--paper:#101214;--ink:#eaece7;--ink-2:#adb2ab;--ink-3:#7c827b;--rule:#2e3236;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--paper);color:var(--ink);font:400 14px/1.5 "IBM Plex Mono",ui-monospace,Menlo,monospace;padding:16px}
form{width:100%;max-width:340px}
h1{font:400 40px/1.05 "Instrument Serif",Georgia,serif;margin:0 0 18px}
label{display:block;font-size:12px;color:var(--ink-2);margin-bottom:6px}
input{width:100%;font:inherit;padding:10px 12px;border:1px solid var(--rule);border-radius:3px;background:transparent;color:var(--ink)}
input:focus{outline:2px solid var(--ink-3);outline-offset:1px}
button{margin-top:12px;width:100%;font:inherit;padding:10px 12px;border:1px solid var(--ink);border-radius:3px;background:var(--ink);color:var(--paper);cursor:pointer}
.err{color:var(--ink);font-size:12px;margin:10px 0 0}
</style>
</head>
<body>
<form method="post">
  <h1>__HEADLINE__</h1>
  <label for="password">Password</label>
  <input id="password" name="password" type="password" autocomplete="current-password" autofocus required>
  <button type="submit">View</button>
  ${wrong ? '<p class="err">That password is not right. Try again.</p>' : ''}
</form>
</body>
</html>`;
  return new Response(html, {
    status: 401,
    headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' },
  });
}

export default async function middleware(request) {
  const expected = process.env.READOUT_PASSWORD;
  if (!expected) return passwordPage(false);
  const good = await tokenFor(expected);

  const cookie = request.headers.get('cookie') || '';
  const match = cookie.match(new RegExp('(?:^|;\\s*)' + COOKIE + '=([a-f0-9]+)'));
  if (match && match[1] === good) return;

  if (request.method === 'POST') {
    const form = await request.formData();
    if (String(form.get('password') || '') === expected) {
      return new Response(null, {
        status: 303,
        headers: {
          location: new URL(request.url).pathname,
          'set-cookie': `${COOKIE}=${good}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=2592000`,
          'cache-control': 'no-store',
        },
      });
    }
    return passwordPage(true);
  }
  return passwordPage(false);
}
