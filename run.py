#!/usr/bin/env python3
"""Push BigSeller inventory syncs again when the failure looks like a rate limit."""

import json
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

LIST_URL = "https://www.bigseller.com/api/v2/inventory/sync/list/record/listing/pageList.json"
PUSH_URL = "https://www.bigseller.com/api/v2/inventory/sync/list/manualErrSync.json"
COOKIE_PATH = Path(os.environ.get("BIGSELLER_COOKIE_FILE", "/etc/pokejam-bigseller/cookie"))

LIST_BODY = {
    "pageNo": 1,
    "pageSize": 50,
    "searchType": "skuName",
    "searchContent": "",
    "inquireType": 0,
    "syncStatus": "failed",
    "platform": "",
    "shopGroup": "",
    "shopId": "",
    "failReasonCodeList": [],
    "shopIdList": [],
    "hour24": True,
}

NEEDLES = ("too many requests", "exceeded", "limit", "frequency", "seconds")

CSRF_TOKEN_COOKIES = ("muc_token", "XSRF-TOKEN", "csrf_token", "_csrf")
CSRF_TOKEN_HEADER = "X-XSRF-TOKEN"

COMMON_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "clienttype": "1",
    "Origin": "https://www.bigseller.com",
    "Referer": "https://www.bigseller.com/web/inventory/sync/sku/index.htm?activeDays=1&syncStatus=failed",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
    ),
}

INTERVAL_SECONDS = 60
RECHECK_SECONDS = 15
MAX_PUSHES_PER_CYCLE = 10
MAX_PAGES = 20
ERROR_PREVIEW = 140


class SessionExpired(Exception):
    pass


class ApiError(Exception):
    pass


class Stop(Exception):
    pass


def log(message: str) -> None:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    print(f"{stamp}  {message}", flush=True)


def install_signals() -> None:
    def handle(signum, frame):
        raise Stop()

    signal.signal(signal.SIGTERM, handle)
    signal.signal(signal.SIGINT, handle)


def preview(text: str, limit: int = ERROR_PREVIEW) -> str:
    one_line = " ".join(str(text).split())
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3] + "..."


def parse_cookie(raw: str) -> dict:
    text = raw.strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    jar = {}
    for part in text.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            jar[name] = value.strip()
    return jar


def format_cookie(jar: dict) -> str:
    return "; ".join(f"{name}={value}" for name, value in jar.items())


def get_csrf_token(jar: dict) -> str | None:
    """Extract CSRF token from cookie jar. BigSeller uses muc_token for CSRF protection."""
    for name in CSRF_TOKEN_COOKIES:
        if name in jar:
            return jar[name]
    return None


def apply_set_cookie(jar: dict, headers) -> bool:
    if headers is None:
        return False
    if hasattr(headers, "get_all"):
        values = headers.get_all("Set-Cookie") or []
    else:
        single = headers.get("Set-Cookie") if hasattr(headers, "get") else None
        values = [single] if single else []
    changed = False
    for raw in values:
        pair = str(raw).split(";", 1)[0]
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and jar.get(name) != value:
            jar[name] = value
            changed = True
    return changed


def error_texts(row: dict) -> list:
    texts = []
    error_msg = row.get("errorMsg")
    if error_msg:
        texts.append(str(error_msg))
    for item in row.get("errorMsgList") or []:
        if isinstance(item, dict):
            error = item.get("error")
            if error:
                texts.append(str(error))
        elif item:
            texts.append(str(item))
    return texts


def matching_error(row: dict):
    for text in error_texts(row):
        lowered = text.lower()
        for needle in NEEDLES:
            if needle in lowered:
                return needle, text
    return None


def classify(rows: list):
    matched = []
    skipped = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        found = matching_error(row)
        if found is None:
            skipped.append(row)
            continue
        row_id = row.get("id")
        if row_id is None or row_id in seen:
            continue
        seen.add(row_id)
        needle, text = found
        matched.append((row, needle, text))
    return matched, skipped


def push_form(ids: list) -> dict:
    return {"syncType": "1", "syncDetailsIds": ",".join(str(row_id) for row_id in ids)}


def decode_payload(body: bytes):
    text = body.decode("utf-8", errors="replace").strip()
    if not text or text.startswith("<"):
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def session_dead(status: int, body: bytes, payload) -> bool:
    if status in (401, 403):
        return True
    stripped = body.lstrip()
    if stripped.startswith(b"<") or stripped.lower().startswith(b"<!doctype"):
        return True
    if isinstance(payload, dict):
        msg = str(payload.get("msg") or "").lower()
        if "login" in msg or "not logged" in msg or "unauthorized" in msg:
            return True
    return False


def post(url: str, jar: dict, *, json_body=None, form=None, timeout: int = 30):
    headers = dict(COMMON_HEADERS)
    headers["Cookie"] = format_cookie(jar)
    csrf_token = get_csrf_token(jar)
    if csrf_token:
        headers[CSRF_TOKEN_HEADER] = csrf_token
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    else:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            status = response.status
            response_headers = response.headers
    except urllib.error.HTTPError as err:
        body = err.read()
        status = err.code
        response_headers = err.headers
    apply_set_cookie(jar, response_headers)
    payload = decode_payload(body)
    if session_dead(status, body, payload):
        raise SessionExpired(f"HTTP {status}")
    return status, payload, body


def fetch_failed_rows(jar: dict) -> list:
    rows = []
    page = 1
    while page <= MAX_PAGES:
        body_json = dict(LIST_BODY)
        body_json["pageNo"] = page
        status, payload, body = post(LIST_URL, jar, json_body=body_json)
        if status != 200 or payload is None:
            snippet = preview(body.decode("utf-8", errors="replace"))
            raise ApiError(f"list page {page} returned HTTP {status}: {snippet}")
        if payload.get("code") != 0:
            code = payload.get("code")
            msg = payload.get("msg") or ""
            body_text = body.decode("utf-8", errors="replace")
            hint = ""
            if code == 2001:
                hint = (
                    " (code 2001 usually means authentication failed - "
                    "check that cookie contains muc_token and session is valid)"
                )
            raise ApiError(
                f"list page {page} returned code {code}: {msg or '(empty message)'}{hint}\n"
                f"Full response: {preview(body_text, 500)}"
            )
        data = payload.get("data") or {}
        batch = data.get("rows") or []
        rows.extend(batch)
        total_page = int(data.get("totalPage") or 1)
        if page >= total_page or not batch:
            break
        page += 1
    return rows


def push_ids(jar: dict, ids: list) -> str:
    status, payload, body = post(PUSH_URL, jar, form=push_form(ids))
    if status != 200 or payload is None:
        snippet = preview(body.decode("utf-8", errors="replace"))
        raise ApiError(f"manualErrSync returned HTTP {status}: {snippet}")
    msg = str(payload.get("msg") or "")
    if payload.get("code") != 0:
        raise ApiError(f"manualErrSync code {payload.get('code')}: {msg}")
    return msg


def sku_label(row: dict) -> str:
    return str(row.get("sku") or row.get("shopSku") or "?")


def read_cookie_text() -> str:
    if not COOKIE_PATH.exists():
        return ""
    return COOKIE_PATH.read_text()


def persist_cookie(snapshot: str, jar: dict) -> None:
    current = read_cookie_text()
    if format_cookie(parse_cookie(current)) != format_cookie(parse_cookie(snapshot)):
        log("cookie file changed on disk, leaving your edit in place")
        return
    updated = format_cookie(jar)
    if updated == format_cookie(parse_cookie(snapshot)):
        return
    COOKIE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = COOKIE_PATH.with_suffix(".tmp")
    temporary.write_text(updated + "\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, COOKIE_PATH)
    log("saved a refreshed session cookie")


def log_skips(skipped: list) -> None:
    shown = skipped[:10]
    for row in shown:
        texts = error_texts(row)
        reason = preview(texts[0]) if texts else "no error text"
        log(f"skip {sku_label(row)} id {row.get('id')}: {reason}")
    extra = len(skipped) - len(shown)
    if extra > 0:
        log(f"skip {extra} more failed rows that are not rate limits")


def run_cycle() -> None:
    snapshot = read_cookie_text()
    jar = parse_cookie(snapshot)
    if not jar:
        log(f"cookie file is empty: {COOKIE_PATH}")
        return

    for attempt in range(1, MAX_PUSHES_PER_CYCLE + 1):
        rows = fetch_failed_rows(jar)
        matched, skipped = classify(rows)
        if attempt == 1:
            log(f"failed in last 24h: {len(rows)}, to push: {len(matched)}")
            log_skips(skipped)
        elif not matched:
            log("recheck: clear")
        else:
            log(f"recheck: {len(matched)} still matching")

        if not matched:
            persist_cookie(snapshot, jar)
            return

        for row, needle, text in matched:
            log(
                f"push {row.get('id')} {sku_label(row)} matched '{needle}': {preview(text)}"
            )
        ids = [row.get("id") for row, _needle, _text in matched]
        msg = push_ids(jar, ids)
        log(
            f"manualErrSync {len(ids)} ids ({', '.join(str(row_id) for row_id in ids)}): {msg}"
        )

        if attempt == MAX_PUSHES_PER_CYCLE:
            log(f"stopped after {MAX_PUSHES_PER_CYCLE} pushes this cycle")
            persist_cookie(snapshot, jar)
            return

        log(f"recheck in {RECHECK_SECONDS}s")
        time.sleep(RECHECK_SECONDS)

    persist_cookie(snapshot, jar)


def main() -> int:
    install_signals()
    log("service started")
    log(f"cookie file: {COOKIE_PATH}")
    while True:
        started = time.monotonic()
        try:
            run_cycle()
        except Stop:
            log("service stopped")
            return 0
        except SessionExpired as exc:
            log(
                f"session expired ({exc}). Replace {COOKIE_PATH} and restart, "
                "or wait for the next check."
            )
        except (ApiError, urllib.error.URLError, TimeoutError, OSError) as exc:
            log(f"check failed: {exc}")

        remaining = INTERVAL_SECONDS - (time.monotonic() - started)
        try:
            if remaining > 0:
                log(f"next check in {int(remaining)}s")
                time.sleep(remaining)
            else:
                log("next check now")
        except Stop:
            log("service stopped")
            return 0


def check_cookie() -> int:
    """Validate the cookie file and test the BigSeller session. Exit 0 if OK, 1 if not."""
    print(f"Cookie file: {COOKIE_PATH}")
    if not COOKIE_PATH.exists():
        print(f"ERROR: Cookie file does not exist: {COOKIE_PATH}")
        return 1

    raw = read_cookie_text()
    if not raw.strip():
        print("ERROR: Cookie file is empty")
        return 1

    jar = parse_cookie(raw)
    if not jar:
        print("ERROR: Cookie file contains no valid cookies")
        return 1

    print(f"Parsed {len(jar)} cookie(s):")
    for name in sorted(jar.keys()):
        value = jar[name]
        display = value[:20] + "..." if len(value) > 20 else value
        print(f"  {name}={display}")

    csrf_token = get_csrf_token(jar)
    if csrf_token:
        print(f"\nCSRF token found: {CSRF_TOKEN_HEADER} header will be set from cookie")
    else:
        print(f"\nWARNING: No CSRF token cookie found (looked for: {', '.join(CSRF_TOKEN_COOKIES)})")
        print("The session may fail with code 2001. Make sure to copy the FULL cookie string from DevTools.")

    required_cookies = ["JSESSIONID", "muc_token"]
    missing = [name for name in required_cookies if name not in jar]
    if missing:
        print(f"\nWARNING: Expected cookies missing: {', '.join(missing)}")
        print("Copy the full cookie string from browser DevTools (Network tab > Request Headers > cookie)")

    print("\nTesting BigSeller API...")
    try:
        rows = fetch_failed_rows(jar)
        print(f"SUCCESS: API returned {len(rows)} failed sync(s) in last 24h")
        if rows:
            print("Sample rows:")
            for row in rows[:3]:
                row_id = row.get("id")
                sku = row.get("sku") or row.get("shopSku") or "?"
                print(f"  id={row_id} sku={sku}")
        return 0
    except SessionExpired as exc:
        print(f"ERROR: Session expired - {exc}")
        print("Get a fresh cookie from your browser while logged in.")
        return 1
    except ApiError as exc:
        print(f"ERROR: API call failed - {exc}")
        return 1
    except Exception as exc:
        print(f"ERROR: Unexpected error - {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        raise SystemExit(check_cookie())
    try:
        raise SystemExit(main())
    except Stop:
        log("service stopped")
        raise SystemExit(0)
