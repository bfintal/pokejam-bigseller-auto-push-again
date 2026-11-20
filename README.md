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

The cookie file is one line.
The install creates it empty here:

```text
/etc/pokejam-bigseller/cookie
```

### Browser console

Open BigSeller while you are logged in.
Open the developer console on that tab and run:

```javascript
copy(document.cookie)
```

That copies the cookies JavaScript is allowed to see.
BigSeller's login cookies are HttpOnly, so this value is usually not enough on its own.

### Cookie the API actually accepts

Use this when the console value is rejected.

1. Stay logged in at `https://www.bigseller.com`.
2. Open DevTools, then the Network tab.
3. Open the failed inventory-sync page, or refresh it.
4. Click the request named `pageList.json`.
5. Under Request Headers, copy the entire `cookie` value.
   Do not include the word `Cookie:`.

On the droplet, replace the file with that one line:

```bash
sudo nano /etc/pokejam-bigseller/cookie
```

Save it, then restart so the new login is used immediately:

```bash
sudo systemctl restart pokejam-bigseller-auto-push
```

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

```bash
sudo journalctl -u pokejam-bigseller-auto-push -f
```

A quiet minute looks like this:

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
