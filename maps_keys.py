"""Two Google Maps keys, because one key cannot do both jobs safely.

The Maps JavaScript API runs in the browser, so the browser must be given a
key. dashboard.html and bulletin.html put it straight into a <script src>,
which means every parent in the carpool can read it out of the page source.
That is not a leak to be plugged; it is how an interactive map works on every
site. Secrecy was never the protection.

The protection is restriction, and this is where a single key fails. A browser
key wants an HTTP-referrer restriction so it only works on pages served from
carpoolsync.com. Server-side geocoding and route calls send no referrer, so
that same restriction would break them. One key can only ever be locked down
to whichever job is weaker, which in practice means not locked down at all.

So there are two:

  GOOGLE_MAPS_BROWSER_KEY  goes into the page. Restrict by HTTP referrer to
                           carpoolsync.com/*, and allow only the Maps
                           JavaScript API. Stolen, it draws maps on your own
                           domain and nothing else.

  GOOGLE_MAPS_SERVER_KEY   never leaves the server. Allow only Geocoding and
                           Routes. This is the one that can spend money, so it
                           is the one that must not reach a browser.

Both fall back to the original GOOGLE_MAPS_API_KEY so nothing breaks while the
new keys are being set up. Keys are read at call time, so changing a Railway
variable needs no code change.
"""

import os

LEGACY_ENV = "GOOGLE_MAPS_API_KEY"
BROWSER_ENV = "GOOGLE_MAPS_BROWSER_KEY"
SERVER_ENV = "GOOGLE_MAPS_SERVER_KEY"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def browser_key() -> str:
    """The key that is allowed to appear in a page. Never use this for
    geocoding or routing: it will be referrer-restricted and will fail."""
    return _env(BROWSER_ENV) or _env(LEGACY_ENV)


def server_key() -> str:
    """The key for geocoding and route calls. Must never be rendered into a
    template, returned by a route, or logged."""
    return _env(SERVER_ENV) or _env(LEGACY_ENV)


def keys_are_shared() -> bool:
    """True when the browser and the server are using the same key.

    Worth surfacing, because it is the state that looks fine and is not: the
    key in the page is the key that can run billable geocoding and route
    calls, so anyone who reads it out of the HTML can spend against the card
    on the account.
    """
    b, s = browser_key(), server_key()
    return bool(b) and b == s


def status() -> dict:
    """For /health and /admin/system, so a misconfiguration is visible before
    it costs anything. Never includes a key, or any part of one."""
    b, s = browser_key(), server_key()
    problems = []
    if not b:
        problems.append(f"{BROWSER_ENV} is not set, so maps will not render")
    if not s:
        problems.append(f"{SERVER_ENV} is not set, so routes cannot be built")
    if keys_are_shared():
        problems.append(
            "the browser and server keys are the same value — the key in the "
            f"page can run billable geocoding, so set {BROWSER_ENV} to a "
            "referrer-restricted Maps-JavaScript-only key")
    return {
        "browser_set": bool(b),
        "server_set": bool(s),
        "shared": keys_are_shared(),
        "ready": not problems,
        "problems": problems,
    }
