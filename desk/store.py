"""Key store. Files live in the data directory, which .gitignore excludes.

Write-only from the page. Each service (Kraken, Kraken trade, Grok, FRED, the two Telegram bots) keeps a
list of labeled keys, and one of them is "in use". A key can be added, relabeled, replaced, put in use or
deleted, but its value is never read back: the page only learns labels, which fields are filled, and when
each key was saved. The rest of Desk asks for a field (for example "grok_key") and gets the value from the
key in use for that service."""
import json
import os
import secrets
import tempfile
import time

SERVICES = {
    "kraken": ("kraken_key", "kraken_secret"),
    "kraken_trade": ("kraken_trade_key", "kraken_trade_secret"),
    "grok": ("grok_key",),
    "fred": ("fred_key",),
    "telegram_desk": ("telegram_desk_token", "telegram_desk_chat_id"),
    "telegram_recovery": ("telegram_recovery_token", "telegram_recovery_chat_id"),
}
FIELD_SERVICE = {f: s for s, fields in SERVICES.items() for f in fields}
SECRET_FIELDS = tuple(FIELD_SERVICE)
MAX_LABEL, MAX_PER_SERVICE = 40, 20


def load_entries(path):
    """The key list from a secrets file, converting the older one-key-per-field layout on the fly."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if isinstance(raw.get("entries"), list):
        return [e for e in raw["entries"] if isinstance(e, dict) and e.get("service") in SERVICES]
    stamps = raw.get("_saved_at") if isinstance(raw.get("_saved_at"), dict) else {}
    out = []
    for svc, fields in SERVICES.items():
        vals = {f: raw[f] for f in fields if isinstance(raw.get(f), str) and raw[f]}
        if vals:
            out.append({"id": "original-" + svc, "service": svc, "label": "Original", "values": vals,  # stable id
                        "saved_at": max([stamps.get(f) or 0 for f in vals]) or None, "active": True})
    return out


def active_value(entries, field):
    svc = FIELD_SERVICE.get(field)
    for e in entries:
        if e.get("service") == svc and e.get("active"):
            return (e.get("values") or {}).get(field, "")
    return ""


def _clean_label(label):
    label = " ".join(str(label or "").split())[:MAX_LABEL]
    return "".join(c for c in label if c.isprintable())


class Store:
    def __init__(self, data_dir):
        self.dir = os.path.abspath(data_dir)
        os.makedirs(self.dir, exist_ok=True)
        try:
            os.chmod(self.dir, 0o700)
        except OSError:
            pass
        self.path = os.path.join(self.dir, "secrets.json")

    # ------------------------------------------------------------ file
    def _entries(self):
        return load_entries(self.path)

    def _write(self, entries):
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".secrets-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"version": 2, "entries": entries}, f)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------ used by the rest of Desk
    def get(self, field):
        """Value of a field from the key in use for its service ('' if none)."""
        return active_value(self._entries(), field) if field in FIELD_SERVICE else ""

    def saved(self):
        entries = self._entries()
        return {f: bool(active_value(entries, f)) for f in SECRET_FIELDS}

    def saved_at(self):
        entries = self._entries()
        out = {}
        for e in entries:
            if e.get("active"):
                for f, v in (e.get("values") or {}).items():
                    if v:
                        out[f] = e.get("saved_at")
        return out

    def update(self, values, clear=()):
        """Older one-slot interface: write fields into the key in use (creating one if needed); clear deletes
        fields from it, and the key itself once it is empty."""
        entries, now = self._entries(), time.time()
        for svc, fields in SERVICES.items():
            vals = {f: v.strip() for f, v in values.items() if f in fields and isinstance(v, str) and v.strip()}
            gone = [f for f in clear if f in fields]
            if not vals and not gone:
                continue
            cur = next((e for e in entries if e["service"] == svc and e.get("active")), None)
            if cur is None and vals:
                cur = {"id": secrets.token_hex(6), "service": svc, "label": "Default", "values": {}, "saved_at": now,
                       "active": True}
                entries.append(cur)
            if cur is None:
                continue
            cur["values"].update(vals)
            for f in gone:
                cur["values"].pop(f, None)
            if vals:
                cur["saved_at"] = now
            if not any(cur["values"].values()):
                entries.remove(cur)
        self._write(entries)

    # ------------------------------------------------------------ the key list (page)
    def listing(self):
        """Labels and status only. Never values."""
        out = {svc: [] for svc in SERVICES}
        for e in self._entries():
            out[e["service"]].append({"id": e["id"], "label": e.get("label") or "", "active": bool(e.get("active")),
                                      "saved_at": e.get("saved_at"),
                                      "filled": {f: bool((e.get("values") or {}).get(f)) for f in SERVICES[e["service"]]}})
        return out

    def find(self, entry_id):
        """(service, values) of one key, for testing it. Internal use only; never sent to the page."""
        e = next((e for e in self._entries() if e.get("id") == entry_id), None)
        return (e["service"], dict(e.get("values") or {})) if e else (None, None)

    def add(self, service, label, values):
        if service not in SERVICES:
            return {"ok": False, "message": "Unknown service."}
        label = _clean_label(label)
        vals = {f: v.strip() for f, v in (values or {}).items()
                if f in SERVICES[service] and isinstance(v, str) and v.strip()}
        if not label:
            return {"ok": False, "message": "Give the key a label, e.g. 'Main account'."}
        if not vals:
            return {"ok": False, "message": "Paste the key first."}
        entries = self._entries()
        mine = [e for e in entries if e["service"] == service]
        if len(mine) >= MAX_PER_SERVICE:
            return {"ok": False, "message": "That is %d keys for this service already; delete one first." % MAX_PER_SERVICE}
        if any(e.get("label", "").lower() == label.lower() for e in mine):
            return {"ok": False, "message": "A key with that label already exists for this service."}
        first = not any(e.get("active") for e in mine)
        new = {"id": secrets.token_hex(6), "service": service, "label": label, "values": vals,
               "saved_at": time.time(), "active": first}
        entries.append(new)
        self._write(entries)
        return {"ok": True, "id": new["id"],
                "message": "Saved%s." % (" and in use" if first else ". Press Use to switch to it")}

    def edit(self, entry_id, label=None, values=None):
        """Relabel and/or replace the filled-in fields of one key. Empty fields keep what is saved."""
        entries = self._entries()
        e = next((e for e in entries if e.get("id") == entry_id), None)
        if e is None:
            return {"ok": False, "message": "That key no longer exists."}
        changed = False
        if label is not None:
            label = _clean_label(label)
            if not label:
                return {"ok": False, "message": "A label cannot be empty."}
            if any(o is not e and o["service"] == e["service"] and o.get("label", "").lower() == label.lower() for o in entries):
                return {"ok": False, "message": "Another key already has that label."}
            changed = changed or label != e.get("label")
            e["label"] = label
        vals = {f: v.strip() for f, v in (values or {}).items()
                if f in SERVICES[e["service"]] and isinstance(v, str) and v.strip()}
        if vals:
            e["values"].update(vals)
            e["saved_at"] = time.time()
            changed = True
        if not changed:
            return {"ok": False, "message": "Nothing to change: type a new label or paste a new value."}
        self._write(entries)
        return {"ok": True, "message": "Replaced." if vals else "Renamed."}

    def use(self, entry_id):
        entries = self._entries()
        e = next((e for e in entries if e.get("id") == entry_id), None)
        if e is None:
            return {"ok": False, "message": "That key no longer exists."}
        for o in entries:
            if o["service"] == e["service"]:
                o["active"] = o is e
        self._write(entries)
        return {"ok": True, "message": "'%s' is now in use." % e.get("label")}

    def delete(self, entry_id):
        entries = self._entries()
        e = next((e for e in entries if e.get("id") == entry_id), None)
        if e is None:
            return {"ok": False, "message": "That key no longer exists."}
        entries.remove(e)
        self._write(entries)
        left = [o for o in entries if o["service"] == e["service"]]
        msg = "Deleted."
        if e.get("active"):
            msg += " No key is in use for this service now%s." % (": press Use on another one" if left else "")
        return {"ok": True, "message": msg}

    def entry(self, entry_id):
        """(service, active) of a key, without values."""
        e = next((e for e in self._entries() if e.get("id") == entry_id), None)
        return (e["service"], bool(e.get("active"))) if e else (None, False)
