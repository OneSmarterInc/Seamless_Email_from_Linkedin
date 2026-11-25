"""Seamless.AI client plus input parsing and match checks."""

import csv
import io
import re
import time

import requests
from django.conf import settings

BATCH_SIZE = 100
POLL_WAIT = 5
POLL_TRIES = 36            # 3 minutes per batch
TERMINAL = {"done", "error", "missing", "not found"}


class SeamlessError(Exception):
    pass


class SeamlessClient:
    def __init__(self, api_key, log=print):
        self.api_key = api_key
        self.log = log
        self.base = settings.SEAMLESS_BASE_URL.rstrip("/")

    def _headers(self):
        return {settings.SEAMLESS_AUTH_HEADER: self.api_key,
                "Content-Type": "application/json", "Accept": "application/json"}

    def _call(self, method, path, **kw):
        for _ in range(5):
            r = requests.request(method, self.base + path, headers=self._headers(), timeout=60, **kw)
            if r.status_code == 429:
                self.log("Rate limit hit. Waiting 60 s.")
                time.sleep(60)
                continue
            if r.status_code in (401, 403):
                raise SeamlessError(f"Seamless refused the API key ({r.status_code}): {r.text[:300]}")
            if r.status_code >= 400:
                raise SeamlessError(f"Seamless error {r.status_code} on {path}: {r.text[:300]}")
            return r.json()
        raise SeamlessError("Too many rate-limit retries")

    @staticmethod
    def _items(data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "results", "requestIds", "items"):
                if key in data:
                    v = data[key]
                    return SeamlessClient._items(v) if isinstance(v, dict) else v
        return []

    def submit(self, people):
        """people: list of dicts with name, linkedin_url, college. Returns request IDs in order."""
        contacts = []
        for p in people:
            c = {"contactName": p["name"], "liProfileUrl": p["linkedin_url"]}
            if p.get("college"):
                c["companyName"] = p["college"]
            contacts.append(c)
        data = self._call("POST", "/contacts/research",
                          json={"contacts": contacts, "researchTypes": ["email"]})
        ids = [i if isinstance(i, str) else (i or {}).get("requestId") for i in self._items(data)]
        if len(ids) != len(people):
            self.log(f"Warning: sent {len(people)}, got {len(ids)} request IDs.")
        return ids

    def poll(self, ids):
        done, pending = {}, [i for i in ids if i]
        for _ in range(POLL_TRIES):
            if not pending:
                break
            time.sleep(POLL_WAIT)
            data = self._call("GET", "/contacts/research/poll", params={"requestIds": ",".join(pending)})
            for item in self._items(data):
                rid, st = item.get("requestId"), (item.get("status") or "").lower()
                if rid and st in TERMINAL:
                    done[rid] = item
            pending = [i for i in pending if i not in done]
            self.log(f"Checking results: {len(done)}/{len(ids)} ready.")
        for i in pending:
            done[i] = {"requestId": i, "status": "timeout"}
        return done


# ---------------------------------------------------------------- checks

def slug(url):
    m = re.search(r"linkedin\.com/in/([^/?#]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


def match_check(name, linkedin_url, contact):
    """linkedin_match = same profile (strong). name_match_only = names agree (weak)."""
    if not contact:
        return ""
    if slug(linkedin_url) and slug(linkedin_url) == slug(contact.get("lIProfileUrl")):
        return "linkedin_match"
    got = (contact.get("nameOriginal") or contact.get("fullName") or "").lower()
    parts = [p for p in re.split(r"[\s.]+", name.lower()) if len(p) > 1]
    if parts and parts[0] in got and parts[-1] in got:
        return "name_match_only"
    return "wrong_person"


def pick_email(contact):
    """Only emails Seamless marks valid. Returns (email, type, confidence)."""
    if contact.get("personalEmail"):
        return contact["personalEmail"], "personal", ""
    for i in (1, 2, 3):
        e = contact.get(f"email{i}")
        if e and (contact.get(f"email{i}EmailAI") or "").lower() == "valid":
            return e, "work/college", contact.get(f"email{i}TotalAI", "")
    return "", "", ""


def guessed(contact):
    return "; ".join(contact.get(f"email{i}") for i in (1, 2, 3)
                     if contact.get(f"email{i}") and (contact.get(f"email{i}EmailAI") or "").lower() != "valid")


# ---------------------------------------------------------------- input

def rows_from_upload(filename, data):
    """Turn an uploaded file into a list of rows (lists of strings).
    Supports .xlsx/.xlsm (all sheets) and CSV/TSV/TXT."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        return rows_from_xlsx(data)
    if name.endswith(".xls"):
        raise ValueError("Old .xls files are not supported. Save the file as .xlsx or CSV and upload again.")
    return rows_from_text(data.decode("utf-8-sig", errors="replace"))


def rows_from_xlsx(data):
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=False, data_only=True)
    rows = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            out = []
            for cell in row:
                val = "" if cell.value is None else str(cell.value).strip()
                link = cell.hyperlink.target if cell.hyperlink and cell.hyperlink.target else ""
                # A cell may show a name but link to the LinkedIn profile
                if link and "linkedin.com/in/" in link.lower() and "linkedin.com/in/" not in val.lower():
                    if val:
                        out.append(val)
                    out.append(link)
                else:
                    out.append(val)
            rows.append(out)
    return rows


def rows_from_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    delim = "\t" if "\t" in text[:2048] else ","
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
    except csv.Error:
        # Broken quoting: fall back to a plain split
        return [line.split(delim) for line in text.split("\n")]


def parse_rows(rows, default_college=""):
    """rows: list of lists, or raw text. Any column holding a linkedin.com/in/ URL is the profile.
    The first other column is the name, the second is the college."""
    if isinstance(rows, str):
        rows = rows_from_text(rows)
    people, seen = [], set()
    for r in rows:
        r = [str(c).strip() for c in r if c is not None]
        url_i = next((i for i, c in enumerate(r) if "linkedin.com/in/" in c.lower()), None)
        if url_i is None:
            continue
        url = r[url_i].split()[0].rstrip("/")
        if not url.startswith("http"):
            url = "https://" + url
        key = slug(url)
        if not key or key in seen:
            continue
        seen.add(key)
        others = [" ".join(c.split()) for i, c in enumerate(r) if i != url_i and c]
        name = others[0] if others else key.rsplit("-", 1)[0].replace("-", " ").title()
        college = others[1] if len(others) > 1 else default_college
        people.append({"name": name[:200], "linkedin_url": url, "college": college[:200]})
    return people
