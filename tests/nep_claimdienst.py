"""Nep-claimdienst en nep-HTTP voor de chatmailtests (Jared, r2, 21-10-2026).

`NepClaimdienst` volgt CLAIMDIENST-CONTRACT.md (run_19a258c6d1ade318) letterlijk,
inclusief het lockgedrag: elke claim en elke bevestiging loopt onder één echte
`threading.Lock`, zoals `LockService.getScriptLock()` in de Apps Script. Twee
gelijktijdige claims op dezelfde sleutel geven daardoor precies één `geclaimd`.

`NepRequests` vervangt `main._requests`: het noteert elke aanroep (met alle
trefwoordargumenten, dus ook `timeout`) en antwoordt per URL. Niets gaat het netwerk op.

Bewust zelfstandig (geen import uit test_chatmail.py): dezelfde regressietests draaien
ook tegen de r1-kandidaat, waar test_chatmail.py anders is.

r3 (22-10, route B van Tonny): `NepWebsite` speelt de websiteroute /api/nina-registratie
volgens het contract in sanayou-pages api/nina-registratie.js. Hij controleert de
handtekening zelf (onafhankelijk van main.py nagebouwd) en weigert een verkeerde.
"""
import hashlib
import threading
import time

import requests  # alleen voor de echte exceptieklassen (ReadTimeout); er gaat niets het net op

CLAIM_URL = "https://claim.voorbeeld.invalid/exec"
CLAIM_SECRET = "nep-claimgeheim"
LOG_URL = "https://log.voorbeeld.invalid/exec"
LIMIET_ONTVANGER = {"chatmail": 2, "studieadvies": 3}
LIMIET_BRON = 10

OMGEVING = {
    "HELPSCOUT_APP_ID": "nep-id", "HELPSCOUT_APP_SECRET": "nep-geheim",
    "HELPSCOUT_MAILBOX_ID": "363337",
    "NINA_LOG_URL": LOG_URL, "NINA_LOG_SECRET": "nep",
    "ANTHROPIC_API_KEY": "nep-anthropic",
    "CLAIM_URL": CLAIM_URL, "CLAIM_SECRET": CLAIM_SECRET,
}


REGISTRATIE_URL = "https://sanayou.com/api/nina-registratie"
# Gedeelde testvector met de website (test/nina-registratie-api.test.mjs), letterlijk uitgeschreven.
VECTOR = {"geheim": "proefgeheim", "ts": "1761170000", "email": " Petra@Voorbeeld.nl ", "naam": "Petra de Vries",
          "mac": "1aad8c31a9cbe6b5afa4d61a8220e832e0b254b5d04d52fe22889ea3504231ed"}
WEBSITE_UITKOMST = "contact 77: brontag gezet, op de Hoofdlijst"


def registratie_mac(geheim, ts, email, naam):
    """Contract api/nina-registratie.js (tekenTekst), hier zelfstandig nagebouwd."""
    import hmac
    t = "nina-registratie|v1|" + ts + "|" + email.strip().lower() + "|" + naam
    return hmac.new(geheim.encode("utf-8"), t.encode("utf-8"), hashlib.sha256).hexdigest()


class NepWebsite:
    """De websiteroute: controleert vorm, venster (300 s) en handtekening, en antwoordt dan
    met `status` en `body`. `modus`: "ok" | "timeout" | "geen_json"."""

    def __init__(self, secret=CLAIM_SECRET, klok=time.time):
        self.secret = secret
        self.klok = klok
        self.status = 200
        self.body = {"ok": True, "uitkomst": WEBSITE_UITKOMST}
        self.modus = "ok"
        self.verzoeken = []
        self.geweigerd = []

    def verwerk(self, kw):
        b = kw.get("json")
        self.verzoeken.append(b)
        if self.modus == "timeout":
            raise requests.exceptions.ReadTimeout("nep: website antwoordt niet")
        reden = None
        if not isinstance(b, dict) or set(b) != {"naam", "email", "ts", "mac"}:
            reden = "velden"
        elif not (isinstance(b["ts"], str) and b["ts"].isdigit()) or abs(self.klok() - int(b["ts"])) > 300:
            reden = "venster"
        elif b["mac"] != registratie_mac(self.secret, b["ts"], b["email"], b["naam"]):
            reden = "handtekening"
        if reden:
            self.geweigerd.append(reden)
            return 401, {"ok": False, "fout": "handtekening ongeldig"}
        if self.modus == "geen_json":
            return self.status, None
        return self.status, self.body


def sha(t):
    """Kale sha256: alleen nog om te tonen dat de client dit NIET meer stuurt."""
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def hm(t, secret=CLAIM_SECRET):
    """HMAC-SHA256 (hex) met CLAIM_SECRET: wat de client volgens het contract (21-10 20:4x) stuurt."""
    import hmac
    return hmac.new(secret.encode("utf-8"), t.encode("utf-8"), hashlib.sha256).hexdigest()


class NepClaimdienst:
    """Contract: claim / bevestig, onder één lock. `modus` laat de dienst falen."""

    def __init__(self, secret=CLAIM_SECRET, klok=time.time):
        self.secret = secret
        self.klok = klok
        self.rijen = []          # dicts zoals de Sheet-rij
        self.berichten = []      # elk ontvangen bericht, zonder secret
        self._lock = threading.Lock()
        # "ok" | "http500" | "geen_json" | "ok_false" | "timeout" | "vreemde_uitkomst"
        self.modus = "ok"
        self.barriere = None     # threading.Barrier: claims wachten hier vóór de lock
        self.barriere_gehaald = 0
        self.vertraging = 0.0    # seconden binnen de lock (maakt de kritieke sectie breed)

    # ── hulp ──
    def rij(self, sleutel):
        return next((r for r in self.rijen if r["sleutel"] == sleutel), None)

    def zaai(self, sleutel, status, soort="chatmail", ontvanger="", bron="", tijd=None):
        self.rijen.append({"tijd": self.klok() if tijd is None else tijd, "soort": soort,
                           "sleutel": sleutel, "ontvanger": ontvanger, "bron": bron,
                           "status": status, "kenmerk": "", "registratie": ""})

    def claims(self):
        return [b for b in self.berichten if b.get("actie") == "claim"]

    def bevestigingen(self):
        return [b for b in self.berichten if b.get("actie") == "bevestig"]

    # ── het eindpunt ──
    def verwerk(self, body):
        """Geeft (http_status, json-body of None). Gooit bij modus 'timeout'."""
        body = dict(body or {})
        geheim = body.pop("secret", None)
        self.berichten.append(dict(body, _secret_klopt=(geheim == self.secret)))
        if self.modus == "timeout":
            raise requests.exceptions.ReadTimeout("nep: claimdienst antwoordt niet")
        if self.modus == "http500":
            return 500, {"ok": True, "uitkomst": "geclaimd"}
        if self.modus == "geen_json":
            return 200, None
        if self.modus == "ok_false" or geheim != self.secret:
            return 200, {"ok": False, "error": "bad secret"}
        actie = body.get("actie")
        if actie == "claim":
            if self.barriere is not None:
                self.barriere.wait(timeout=5)
                self.barriere_gehaald += 1
            antwoord = self._claim(body)
        elif actie == "bevestig":
            antwoord = self._bevestig(body)
        else:
            antwoord = {"ok": False, "error": "onbekende actie"}
        if self.modus == "vreemde_uitkomst" and actie == "claim":
            antwoord = {"ok": True, "uitkomst": "misschien"}
        return 200, antwoord

    def _claim(self, b):
        if not self._lock.acquire(timeout=10):  # waitLock(10000)
            return {"ok": False, "error": "lock"}
        try:
            if self.vertraging:
                time.sleep(self.vertraging)
            nu = self.klok()
            r = self.rij(b["sleutel"])
            if r is not None:
                if r["status"] == "verstuurd":
                    return {"ok": True, "uitkomst": "al_verstuurd"}
                if r["status"] == "bezig":
                    return {"ok": True, "uitkomst": "bezig"}
                if r["status"] == "mislukt":
                    r["status"] = "bezig"
                    return {"ok": True, "uitkomst": "geclaimd"}
            soort = b.get("soort")
            n_ontv = sum(1 for x in self.rijen
                         if x["soort"] == soort and x["ontvanger"] == b.get("ontvanger")
                         and nu - x["tijd"] <= 24 * 3600 and x["status"] in ("bezig", "verstuurd"))
            if n_ontv >= LIMIET_ONTVANGER.get(soort, 0):
                return {"ok": True, "uitkomst": "limiet_ontvanger"}
            if b.get("bron"):
                n_bron = sum(1 for x in self.rijen if x["bron"] == b["bron"] and nu - x["tijd"] <= 3600)
                if n_bron >= LIMIET_BRON:
                    return {"ok": True, "uitkomst": "limiet_bron"}
            self.zaai(b["sleutel"], "bezig", soort=soort, ontvanger=b.get("ontvanger", ""),
                      bron=b.get("bron", ""), tijd=nu)
            return {"ok": True, "uitkomst": "geclaimd"}
        finally:
            self._lock.release()

    def _bevestig(self, b):
        if not self._lock.acquire(timeout=10):
            return {"ok": False, "error": "lock"}
        try:
            r = self.rij(b.get("sleutel"))
            if r is None:
                return {"ok": False, "error": "onbekende sleutel"}
            r["status"] = b.get("status")
            r["kenmerk"] = b.get("kenmerk", "")
            r["registratie"] = b.get("registratie", "")
            return {"ok": True}
        finally:
            self._lock.release()


class Antwoord:
    def __init__(self, status=200, body=None, headers=None, geen_json=False):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self._geen_json = geen_json

    def json(self):
        if self._geen_json:
            raise ValueError("nep: geen JSON")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class NepRequests:
    """Noteert elke aanroep; antwoordt per URL-patroon.

    hs_reeks: lijst gedragingen voor opeenvolgende POSTs naar /v2/conversations
    (int = HTTP-status, "timeout" = ReadTimeout). Leeg = altijd 201.
    """

    def __init__(self, claimdienst=None, hs_reeks=None, website=None):
        self.aanroepen = []
        self.claimdienst = claimdienst if claimdienst is not None else NepClaimdienst()
        self.hs_reeks = list(hs_reeks or [])
        self.website = website if website is not None else NepWebsite()
        self.hs_resource_id = "4242"
        self._lock = threading.Lock()

    def _antwoord(self, methode, url, kw):
        with self._lock:
            self.aanroepen.append((methode, url, kw))
        if url == CLAIM_URL:
            status, body = self.claimdienst.verwerk(kw.get("json"))
            return Antwoord(status, body, geen_json=body is None)
        if url.endswith("/oauth2/token"):
            return Antwoord(200, {"access_token": "nep-token", "expires_in": 7200})
        if url.endswith("/v2/conversations"):
            with self._lock:
                gedrag = self.hs_reeks.pop(0) if self.hs_reeks else 201
            if gedrag == "timeout":
                raise requests.exceptions.ReadTimeout("nep: Help Scout antwoordt niet")
            return Antwoord(gedrag, {}, {"Resource-Id": self.hs_resource_id} if gedrag < 300 else {})
        if url == REGISTRATIE_URL:
            status, body = self.website.verwerk(kw)
            return Antwoord(status, body, geen_json=body is None)
        return Antwoord(200, {})

    def post(self, url, **kw):
        return self._antwoord("POST", url, kw)

    def get(self, url, **kw):
        return self._antwoord("GET", url, kw)

    # ── uitlezen ──
    def registraties(self):
        """Elke aanroep van de websiteroute, met alle trefwoordargumenten."""
        return [(m, kw) for m, u, kw in self.aanroepen if u == REGISTRATIE_URL]

    def hs_posts(self):
        """Elke POST-poging naar /v2/conversations (ook een die faalde of time-out gaf)."""
        return [kw for m, u, kw in self.aanroepen if u.endswith("/v2/conversations")]

    def hs_posts_met_tag(self, tag):
        return [kw for kw in self.hs_posts() if kw.get("json", {}).get("tags") == [tag]]

    def chatmail_posts(self):
        return self.hs_posts_met_tag("nina-chatmail")

    def mailblue(self):
        return [(m, u.split("/api/3")[-1]) for m, u, _ in self.aanroepen if "/api/3" in u]

    def hs_aanroepen(self):
        return [u for m, u, kw in self.aanroepen if "helpscout" in u]

    def logregels(self):
        return [kw["json"] for m, u, kw in self.aanroepen if u == LOG_URL]
