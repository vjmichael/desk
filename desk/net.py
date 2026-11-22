"""One small HTTP helper. Everything that touches the network goes through
http_json so tests can replace it with a fake.

TLS: sites are always verified. If this computer's own certificate store cannot
verify a site (common on Windows when a root certificate has not been fetched
yet), the request is retried once using the bundled Mozilla root list in
certs/cacert.pem. Verification is never turned off."""
import http.client
import json
import os
import ssl
import urllib.error
import urllib.request

BUNDLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs", "cacert.pem")

CERT_HELP = ("the site's security certificate could not be verified, even with the bundled "
             "certificates")


def _cert_reason(err):
    """OpenSSL's own explanation, e.g. 'unable to get local issuer certificate'.
    Contains no URL and no secrets."""
    return (getattr(getattr(err, "reason", None), "verify_message", "") or "unknown reason")[:120]


class NetError(Exception):
    """Network failure. The message never contains the URL, because Telegram
    puts the bot token in the URL."""


def _request(req, timeout, context=None):
    """Return (status, raw_bytes). HTTP error statuses are returned, not raised."""
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _is_cert_error(err):
    return isinstance(getattr(err, "reason", None), ssl.SSLCertVerificationError)


def http_json(method, url, headers=None, body=None, timeout=20):
    """Return (status_code, parsed_json_or_text). Only connection-level
    problems raise NetError."""
    h = {"User-Agent": "desk/0.1"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        try:
            status, raw = _request(req, timeout)
        except urllib.error.URLError as e:
            if not (_is_cert_error(e) and os.path.exists(BUNDLE)):
                raise
            ctx = ssl.create_default_context(cafile=BUNDLE)
            status, raw = _request(req, timeout, ctx)
    except urllib.error.URLError as e:
        if _is_cert_error(e):
            raise NetError("%s. OpenSSL says: %s" % (CERT_HELP, _cert_reason(e))) from None
        raise NetError("could not connect (%s)" % type(e.reason).__name__) from None
    except TimeoutError:
        raise NetError("timed out") from None
    except OSError as e:
        raise NetError("network error (%s)" % type(e).__name__) from None
    except (http.client.HTTPException, ValueError):
        # includes invalid URL / bad characters; str(e) can contain the token, so never print it
        raise NetError("the request could not be made. A pasted key or token probably "
                       "contains a space or a stray character: copy it again") from None
    text = raw.decode("utf-8", "replace")
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text[:300]


def http_raw(method, url, headers=None, timeout=60):
    """Like http_json but returns (status, headers_dict, raw_bytes). Used for
    byte-range downloads. Same TLS rules and the same URL-hiding error messages."""
    h = {"User-Agent": "desk/0.1"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, method=method)

    def go(context=None):
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
                return resp.status, dict(resp.headers.items()), (b"" if method == "HEAD" else resp.read())
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers.items()), e.read()

    try:
        try:
            return go()
        except urllib.error.URLError as e:
            if not (_is_cert_error(e) and os.path.exists(BUNDLE)):
                raise
            return go(ssl.create_default_context(cafile=BUNDLE))
    except urllib.error.URLError as e:
        if _is_cert_error(e):
            raise NetError("%s. OpenSSL says: %s" % (CERT_HELP, _cert_reason(e))) from None
        raise NetError("could not connect (%s)" % type(e.reason).__name__) from None
    except TimeoutError:
        raise NetError("timed out") from None
    except OSError as e:
        raise NetError("network error (%s)" % type(e).__name__) from None
    except (http.client.HTTPException, ValueError):
        raise NetError("the request could not be made") from None
