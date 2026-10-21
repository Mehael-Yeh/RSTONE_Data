"""Anonymous bounded HTTPS GETs, same signed target across mirrors."""

import time
import urllib.error
import urllib.parse
import urllib.request

from .protocol import DataError, decode_json, require, sha256, validate_latest
from .signing import verify_release

MAX_MANIFEST = 32 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise DataError("mirror redirect rejected; configure final HTTPS baseUrl")


def validate_base(base, allow_local_http=False):
    parsed = urllib.parse.urlsplit(base)
    local = allow_local_http and parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    require(parsed.scheme == "https" or local, "mirror baseUrl must use HTTPS")
    require(parsed.netloc and not parsed.username and not parsed.password and
            not parsed.query and not parsed.fragment, "invalid mirror baseUrl")
    require(".." not in urllib.parse.unquote(parsed.path).split("/"), "unsafe mirror baseUrl")
    return base.rstrip("/")


def get_bytes(base, relative, limit, *, allow_local_http=False, require_cors=False, fresh=False):
    base = validate_base(base, allow_local_http)
    require(not relative.startswith("/") and ".." not in relative.split("/") and
            not any(c in relative for c in ("?", "#", "\\", ":")), "invalid resource path")
    url = base + "/" + urllib.parse.quote(relative, safe="/")
    if fresh:
        url += "?rstone=" + str(time.time_ns())
    request = urllib.request.Request(url, headers={"User-Agent": "RSTONE-Data/1", "Accept-Encoding": "identity",
                                                 "Cache-Control": "no-cache", "Origin": "https://rstone-client.invalid"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=20) as response:
            require(response.status == 200, "unexpected mirror HTTP status")
            if require_cors:
                require(response.headers.get("Access-Control-Allow-Origin") == "*", "mirror missing anonymous CORS header")
            data = response.read(limit + 1)
            require(len(data) <= limit, "mirror response exceeds size limit")
            return data
    except (OSError, urllib.error.URLError) as exc:
        raise DataError(f"mirror resource unavailable: {relative}") from exc


def fetch_release(base, keys, latest=None, *, allow_local_http=False, require_cors=False,
                  verify_objects=True):
    options = {"allow_local_http": allow_local_http, "require_cors": require_cors, "fresh": True}
    if latest is None:
        latest = decode_json(get_bytes(base, "latest.json", 16 * 1024, **options))
    validate_latest(latest)
    raw = get_bytes(base, latest["manifestPath"], MAX_MANIFEST, **options)
    signature = get_bytes(base, latest["signaturePath"], 512, **options)
    manifest = verify_release(latest, raw, signature, keys)
    total = sum(r["size"] for r in manifest["files"])
    require(total <= 512 * 1024 * 1024, "dataset exceeds client size limit")
    if verify_objects:
        checked = set()
        for record in manifest["files"]:
            digest = record["sha256"]
            if digest in checked:
                continue
            content = get_bytes(base, "objects/" + digest, record["size"], **options)
            require(len(content) == record["size"] and sha256(content) == digest,
                    "mirror object integrity check failed")
            checked.add(digest)
    return latest, manifest, raw, signature
