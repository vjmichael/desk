import argparse
import os
import secrets
import signal
import webbrowser

from . import __version__
from .server import DeskServer
from .store import Store


def load_token(path):
    """A token that survives restarts, for running as a service. Kept in a private file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            t = f.read().strip()
        if len(t) >= 20:
            return t
    except OSError:
        pass
    t = secrets.token_urlsafe(24)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(t + "\n")
    return t


def main():
    ap = argparse.ArgumentParser(prog="desk", description="Maj Vaj Desk: the page, keys, fundamentals and reports for the Freqtrade bot.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--token-file", help="keep the access token in this file so it survives restarts (server use)")
    ap.add_argument("--resume", action="store_true", help="(ignored; kept so older service files still start)")
    ap.add_argument("--majvaj-config", help="Maj Vaj settings file (Freqtrade API, verdict and money files); enables the bot layer if present")
    args = ap.parse_args()
    store = Store(args.data_dir)
    token = load_token(args.token_file) if args.token_file else None
    majvaj = None
    if args.majvaj_config and os.path.exists(args.majvaj_config):
        from .majvaj import MajVaj
        try:
            majvaj = MajVaj(store, args.majvaj_config)
        except (OSError, ValueError, KeyError) as e:
            print("Maj Vaj layer not started: %s" % type(e).__name__)
    server = DeskServer(store, port=args.port, token=token, majvaj=majvaj)
    if majvaj is not None:
        majvaj.start()
        print("Maj Vaj layer running (fundamentals verdicts, reports).")
    url = "http://127.0.0.1:%d/?t=%s" % (server.port, server.token)
    print("Desk version %s. Open this address in your browser (it only works on this computer):" % __version__)
    print(url)
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    if majvaj is not None:
        # The old Desk paper engine is retired: make sure it never starts again after an update.
        try:
            server.engine.account.set_meta("wanted_running", "0")
        except Exception:
            pass
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
