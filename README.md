# PokeJam BigSeller auto push again

This service runs on the DigitalOcean droplet.
Every minute it loads inventory syncs that failed in the last 24 hours.
It pushes a row again only when the error looks like a rate limit.
After a push it waits 15 seconds and checks again, until those rows are gone.

Rows such as "Operation Not Allowed" are logged and left alone.

The login cookie stays on the server.
It is not in this repository.

## 1. Install

On the droplet:

```bash
curl -fsSL https://raw.githubusercontent.com/bfintal/pokejam-bigseller-auto-push-again/main/install.sh -o /tmp/pokejam-install.sh && sudo bash /tmp/pokejam-install.sh
```

This installs Python, clones the repo to `/opt/pokejam-bigseller-auto-push`, and registers the systemd service.
It does not start the service.
Run the same command later to pull new code.
If the service is already running, that command restarts it.

## 2. Cookie

The cookie file is one line containing your full BigSeller session.
The install creates it empty here:

```text
/etc/pokejam-bigseller/cookie
```

### Getting the cookie (recommended method)

BigSeller uses CSRF protection.
The cookie string must include both session cookies (like `JSESSIONID`) and the CSRF token cookie (`muc_token`).
The browser console method does not work because login cookies are HttpOnly.

**Step by step:**

1. Log in to `https://www.bigseller.com` in your browser.
2. Open DevTools (F12 or right-click > Inspect).
3. Go to the **Network** tab.
4. Navigate to Inventory > Sync > Failed (the page that shows failed syncs), or refresh if already there.
5. In the Network list, click any request to `bigseller.com` (look for `pageList.json` or similar).
6. In the right panel, find **Request Headers**.
7. Find the `cookie:` line and copy its **entire value** (everything after `cookie: `).
   It should look like: `JSESSIONID=abc123; muc_token=xyz789; locale=en_US; ...`
8. Do **not** include the word `Cookie:` itself.

**On the droplet:**

```bash
sudo nano /etc/pokejam-bigseller/cookie
```

Paste the cookie string as a single line, save, then validate:

```bash
sudo -u pokejam python3 /opt/pokejam-bigseller-auto-push/run.py check
```

If validation passes, restart the service:

```bash
sudo systemctl restart pokejam-bigseller-auto-push
```

### Validating the cookie

Before starting the service, always validate:

```bash
sudo -u pokejam python3 /opt/pokejam-bigseller-auto-push/run.py check
```

This command:
- Shows which cookies were parsed from the file
- Warns if required cookies (JSESSIONID, muc_token) are missing
- Makes a test API call to BigSeller
- Reports success with a count of failed syncs, or shows the exact error

**Common errors:**

| Error | Cause | Fix |
|-------|-------|-----|
| `code 2001` | Missing CSRF token or session invalid | Copy full cookie string including `muc_token` |
| `Session expired` | Login timed out | Get fresh cookie from browser |
| `HTTP 401/403` | Not logged in | Log in to BigSeller, then copy cookie |

### Cookie expiration

BigSeller sessions expire.
When you see `session expired` in logs, get a fresh cookie from your browser.

The file is readable only by the `pokejam` service user.
The service re-reads it at the start of every check.
If you replace the file while a check is running, your edit is kept and used on the next check.
Restart applies it immediately.
If BigSeller sends a new session cookie, the service saves it back into this file.

## 3. Start

```bash
sudo systemctl enable --now pokejam-bigseller-auto-push
```

This starts it now and again after a reboot.

## 4. Stop

```bash
sudo systemctl disable --now pokejam-bigseller-auto-push
```

This stops it and leaves it stopped across reboots.

## Logs

This service uses an isolated journald namespace so its logs don't affect other services on the droplet.

**View logs:**

```bash
sudo journalctl --namespace=pokejam -u pokejam-bigseller-auto-push -f
```

**Log size limits (automatic, no manual cleanup needed):**

| Limit | Value |
|-------|-------|
| Maximum size | 50 MB |
| Maximum age | 7 days |

Old entries are automatically deleted when either limit is reached.
These limits apply only to this service; other services on the droplet are unaffected.

**Check current log size:**

```bash
sudo journalctl --namespace=pokejam --disk-usage
```

### Log output examples

When nothing needs pushing (quiet cycle), the service stays silent.
Every hour it logs a status line:

```text
2026-11-20 16:05:01 +0800  quiet (60 cycles, no failed syncs)
```

When there are failed syncs that aren't rate limits:

```text
2026-11-20 16:05:01 +0800  failed in last 24h: 4, to push: 0
2026-11-20 16:05:01 +0800  skip HAP_DOUBLE_RARE id 75326: Operation Not Allowed...
2026-11-20 16:05:01 +0800  next check in 60s
```

A push looks like this:

```text
2026-11-20 16:06:01 +0800  failed in last 24h: 2, to push: 2
2026-11-20 16:06:01 +0800  push 75326 HAP_DOUBLE_RARE matched 'seconds': Too many requests. Try again in 30 seconds
2026-11-20 16:06:02 +0800  manualErrSync 2 ids (75326, 7819): Successfully
2026-11-20 16:06:02 +0800  recheck in 15s
2026-11-20 16:06:17 +0800  recheck: clear
2026-11-20 16:06:17 +0800  next check in 43s
```

`session expired` means the cookie is dead.
Replace `/etc/pokejam-bigseller/cookie` and restart.

The service stops after 10 pushes in one cycle, then waits until a minute has passed since that cycle started.
That cap is there so a SKU that never clears cannot be pushed in a tight loop.
The next minute tries again.

### Legacy log access

If you have logs from before the namespace was set up, they're in the default journal:

```bash
sudo journalctl -u pokejam-bigseller-auto-push
```
