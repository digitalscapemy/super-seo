# IndexNow extension

Free protocol supported by Bing, Yandex, Naver, Seznam and others (not Google). Script:
`extensions/indexnow/indexnow.py` (stdlib only). One POST tells all participating engines which URLs changed.

## Setup
1. `python extensions/indexnow/indexnow.py keygen --public-dir public` creates a random 32-hex key and writes
   `public/<key>.txt` containing exactly the key. Leave out `--public-dir` to only print it. No env var is needed.
2. Deploy so the file is served at `https://<host>/<key>.txt` with `Content-Type: text/plain`.
   SPA catch-all routes often return the HTML shell instead; `verify` catches that.
3. Set `INDEXNOW_KEY=<key>` (or pass `--key`), then run `indexnow.py verify --host example.com`.
   Multi-tenant custom domains: one key file per host.

## Commands
| Command | What it does |
|---|---|
| `keygen [--public-dir DIR]` | New key; prints the file name, content and URL to host it at. Never copied into `.seo/`. |
| `verify --host H [--key K] [--key-location URL]` | Fetches the key file through the core crawler's guarded `fetch()`. Findings: 404 / unreachable / other status = high, body not equal to the key = high, not `text/plain` = medium, redirect to another host = medium. Exit 1 when not valid. |
| `submit --host H [URL...] [--file F] [--from-sitemap URL [--since ISO]] [--dry-run]` | POSTs `{host, key, keyLocation, urlList}` to `https://api.indexnow.org/indexnow` (`--endpoint` to change), at most 10,000 URLs per request (larger lists are split). Only URLs on `--host` are sent. `--since` keeps sitemap URLs whose `<lastmod>` is on or after the date; URLs without lastmod are skipped and counted. |

Response codes: **200** submitted, **202** accepted while the key is being validated, **400** bad format,
**403** key file missing or wrong, **422** URLs not on the host or key mismatch, **429** too many requests (slow down).
After 403/422/429 the remaining batches are not sent. All commands take `--json` and `--no-write`
(otherwise `.seo/audits/indexnow-<cmd>-<host>-<stamp>.json|md`).

## Good practice
- Submit only URLs that were added, changed or removed (removed URLs should return 404/410). Do not resubmit
  unchanged URLs on a schedule.
- Debounce: queue changes and send at most every few minutes. A CI step after deploy with
  `--from-sitemap ... --since <last deploy time>` is a simple pattern.
- IndexNow does not reach Google; use sitemaps and Search Console for Google.
