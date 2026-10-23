"""Regressies voor Jordans NO-GO op Nina-chatmail r1 (review 21-10-2026), plus F2/F3/F4.

Deze tests toetsen alleen GEDRAG via wat r1 ook al had (`main.chat`, `main._verwerk_chatmail`
met twee argumenten, `main._requests`). Zo draaien ze ongewijzigd tegen de r1-kandidaat
en falen ze daar als FAIL (een assert), niet als ERROR. Een r2-tekst wordt opgehaald met
`getattr(main, NAAM, ONTBREEKT)`: op r1 bestaat hij niet en wordt de vergelijking een FAIL.

Elke HTTP-aanroep loopt via `NepRequests`; de claimdienst is `NepClaimdienst` met een echte
lock (contract). Daarbuiten weigert en telt een netwerkvangnet elke socketverbinding; de
laatste test eist nul pogingen. Het harnas hangt er nog een vangnet omheen (sitecustomize).

Draaien vanuit de repo-wortel:  python3 -B -m unittest tests.test_chatmail_r2 -v
"""
import asyncio
import datetime
import os
import re
import socket
import sys
import threading
import types
import unittest
from unittest import mock

# ── Buitenste vangnet in dit proces ─────────────────────────────────────────
NETWERKPOGINGEN = []


def _geen_netwerk(*a, **k):
    NETWERKPOGINGEN.append(repr(a)[:120])
    raise OSError("netwerk geblokkeerd in test_chatmail_r2")


socket.socket.connect = _geen_netwerk
socket.create_connection = _geen_netwerk

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import tests.test_opstart  # noqa: E402,F401  — nep-pakketten + importeert main van DEZE repo
import main  # noqa: E402
from tests.nep_claimdienst import (  # noqa: E402
    NepClaimdienst, NepRequests, OMGEVING, CLAIM_SECRET, sha, hm)

assert os.path.dirname(os.path.abspath(main.__file__)) == REPO, main.__file__

ONTBREEKT = "<deze tekst bestaat niet in deze kandidaat>"
SUCCESZIN = "ik heb ons gesprek naar je gemaild"


def tekst(naam):
    return getattr(main, naam, ONTBREEKT)


def u(t): return {"role": "user", "content": t}
def a(t): return {"role": "assistant", "content": t}


AANBOD = ("Zal ik dit gesprek even naar je mailen? Dan heb je alles bij elkaar. Heb ik iets over "
          "het hoofd gezien, dan sturen we je nog een aanvulling. En als jij daarna nog een vraag "
          "hebt, reageer je gewoon op die mail.")
TOESTEMMING = ("Fijn! Mag ik je naam en e-mailadres? Je krijgt ons gesprek per mail en daarna af en "
               "toe een mail van SanaYou YOGAcademy. Afmelden kan altijd, via de link onderaan die "
               "mails. Meer lees je in onze [privacyverklaring](https://sanayou.com/privacy-policy).")
GESPREK = [
    u("Wat is het verschil tussen de klassikale en de online Yin-opleiding?"),
    a("De klassikale opleiding volg je in Zwolle, de online variant in je eigen tempo.\n\n" + AANBOD),
    u("Ja graag"),
    a(TOESTEMMING),
    u("Petra de Vries, petra@voorbeeld.nl"),
    a("Even checken: je naam is Petra de Vries en je e-mailadres is petra@voorbeeld.nl, klopt dat?"),
    u("Ja, klopt!"),
]
AFRONDING = "Top, ik heb ons gesprek naar je gemaild."
SESSIE = "test-chatmail-r2"


class NepAnthropic:
    """Hoofdmodel geeft `antwoord`. De taalcheck (haiku) wist elke [[..]]-tag als
    `taalcheck_stript` aan staat, zoals een model dat een tag 'opruimt'."""
    antwoord = ""
    taalcheck_stript = True

    def __init__(self, api_key=None, timeout=None):
        self.api_key = api_key
        self.messages = self

    def create(self, model="", messages=None, **kw):
        if model.startswith("claude-haiku"):
            t = messages[0]["content"].split("Tekst:\n", 1)[1]
            if NepAnthropic.taalcheck_stript:
                t = re.sub(r"\[\[[A-Z]+\]\]", "", t).strip()
            return types.SimpleNamespace(content=[types.SimpleNamespace(text=t)])
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=NepAnthropic.antwoord)])


def chatbeurt(gesprek, laatste, antwoord, session_id=SESSIE, taalcheck_stript=True):
    NepAnthropic.antwoord = antwoord
    NepAnthropic.taalcheck_stript = taalcheck_stript
    verzoek = types.SimpleNamespace(
        message=laatste, session_id=session_id,
        history=[types.SimpleNamespace(role=m["role"], content=m["content"]) for m in gesprek])
    with mock.patch.object(main.anthropic, "Anthropic", NepAnthropic):
        return asyncio.run(main.chat(verzoek))["response"]


def datum_amsterdam():
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo("Europe/Amsterdam")).strftime("%Y-%m-%d")


class Basis(unittest.TestCase):
    omgeving = OMGEVING

    def setUp(self):
        self.claim = NepClaimdienst()
        self.nep = NepRequests(claimdienst=self.claim)
        self._oud = main._requests
        main._requests = self.nep
        main._hs_token_cache.update(access_token=None, expires_at=0)
        self._env = mock.patch.dict(os.environ, self.omgeving, clear=False)
        self._env.start()
        for weg in [k for k in ("CLAIM_URL", "CLAIM_SECRET") if k not in self.omgeving]:
            os.environ.pop(weg, None)

    def tearDown(self):
        main._requests = self._oud
        self._env.stop()

    def ontvangers(self):
        return [p["json"]["customer"]["email"] for p in self.nep.chatmail_posts()]

    def geen_succesclaim(self, resp):
        self.assertNotIn(SUCCESZIN, resp.lower(), resp)

    def geen_mislukt_melding(self, resp):
        """De bezoeker hoort niet dat het mislukte (r1-tekst "lukte … niet", Carrie's N1 "niet gelukt")."""
        self.assertNotEqual(resp, tekst("CHATMAIL_MISLUKT"))
        for w in ("lukte", "gelukt", "mislukt"):
            self.assertNotIn(w, resp.lower())


# ── J1–J5: Jordans reproducers als regressie ─────────────────────────────────
class J1SucceszinZonderTag(Basis):
    def test_j1_succeszin_zonder_tag_geen_succesclaim(self):
        """J1: het model claimt de mail zonder tag; er gaat niets weg."""
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING)
        self.assertEqual(self.nep.hs_posts(), [])
        self.geen_succesclaim(resp)
        self.assertEqual(resp, tekst("CHATMAIL_NIET_VERSTUURD"))


class J2RuweTags(Basis):
    def test_j2_beide_ruwe_tags_taalcheck_wist_ze(self):
        """J2: beide ruwe tags; de taalcheck wist ze; geen reddend escalatiewoord."""
        chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[ESCALATIE]] [[CHATMAIL]]")
        tags = sorted(p["json"]["tags"][0] for p in self.nep.hs_posts())
        self.assertEqual(tags, ["nina-chatmail", "nina-escalatie"])


class J3NogNietGemaild(Basis):
    def test_j3_nog_niet_gemaild_levert_eerste_mail(self):
        g = GESPREK + [a("Ik heb ons gesprek nog niet gemaild, maar kan dat doen."), u("Ja, graag nu.")]
        uit = main._verwerk_chatmail(g, AFRONDING)
        self.assertEqual(self.ontvangers(), ["petra@voorbeeld.nl"])
        self.assertTrue(uit["verstuurd"], uit)
        self.assertEqual(uit["antwoord"], AFRONDING)


class J4JaMetNieuwAdres(Basis):
    def test_j4_ja_met_nieuw_adres_nooit_naar_oud(self):
        uit = main._verwerk_chatmail(GESPREK[:-1] + [u("Ja, stuur het naar nieuw@voorbeeld.nl")], AFRONDING)
        self.assertNotIn("petra@voorbeeld.nl", self.ontvangers())
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertFalse(uit["verstuurd"])
        self.geen_succesclaim(uit["antwoord"])
        self.assertIn("nieuw@voorbeeld.nl", uit["antwoord"])
        self.assertIn("klopt dat?", uit["antwoord"])


class J5ZelfdeBeurtTweeKeer(Basis):
    def test_j5_zelfde_bevestigingsbeurt_twee_keer_een_mail(self):
        eerste = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        tweede = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(len(self.nep.hs_posts()), 1)
        self.assertEqual(eerste, AFRONDING)
        self.assertEqual(tweede, tekst("CHATMAIL_AL_VERSTUURD"))


# ── F3: adresbevestiging als actuele, begrensde toestand ─────────────────────
class F3Adres(Basis):
    def test_f3_correctie_na_ja_ontkracht_bevestiging(self):
        g = GESPREK + [a("Fijn, dan zet ik het voor je klaar."),
                       u("Oh wacht, mijn adres is eigenlijk nieuw@voorbeeld.nl")]
        uit = main._verwerk_chatmail(g, AFRONDING)
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertFalse(uit["verstuurd"])
        self.assertIn("nieuw@voorbeeld.nl", uit["antwoord"])
        self.geen_succesclaim(uit["antwoord"])

    def test_f3_nee_over_adres_na_ja(self):
        g = GESPREK + [a("Fijn, dan zet ik het voor je klaar."), u("Nee, mail het toch maar niet naar dat adres")]
        uit = main._verwerk_chatmail(g, AFRONDING)
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertFalse(uit["verstuurd"])

    def test_f3_nieuwe_ja_ontsluit_alleen_nieuw_adres(self):
        """Twee beurten: ja + nieuw adres -> server vraagt bevestiging; dan ja -> nieuw adres."""
        g1 = GESPREK[:-1]
        resp1 = chatbeurt(g1, "Ja, stuur het naar nieuw@voorbeeld.nl", AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertIn("nieuw@voorbeeld.nl", resp1)
        g2 = g1 + [u("Ja, stuur het naar nieuw@voorbeeld.nl"), a(resp1)]
        resp2 = chatbeurt(g2, "Ja", AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(self.ontvangers(), ["nieuw@voorbeeld.nl"])
        self.assertEqual(resp2, AFRONDING)

    def test_f3_zelfde_adres_herhaald_blijft_bevestigd(self):
        g = GESPREK + [a("Fijn, dan zet ik het voor je klaar."), u("Ja, petra@voorbeeld.nl is goed")]
        uit = main._verwerk_chatmail(g, AFRONDING)
        self.assertEqual(self.ontvangers(), ["petra@voorbeeld.nl"])
        self.assertTrue(uit["verstuurd"])


# ── F2: één escalatietrigger, vóór de taalcheck; vier combinaties × taalcheck ─
NEUTRAAL = "Dank je wel, Petra."                   # geen tag, geen reddend escalatiewoord
ESCALATIETEKST = "Dank je wel, Sandy kijkt ernaar."  # idem: geen 'doorgestuurd' e.d.


def _f2_test(chatmail, escalatie, stript):
    def test(self):
        basis = AFRONDING if chatmail else (ESCALATIETEKST if escalatie else NEUTRAAL)
        antwoord = basis + (" [[ESCALATIE]]" if escalatie else "") + (" [[CHATMAIL]]" if chatmail else "")
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], antwoord, taalcheck_stript=stript)
        tags = sorted(p["json"]["tags"][0] for p in self.nep.hs_posts())
        verwacht = sorted((["nina-chatmail"] if chatmail else []) + (["nina-escalatie"] if escalatie else []))
        self.assertEqual(tags, verwacht)
        self.assertNotIn("[[", resp)
        self.assertEqual(self.nep.logregels()[0]["escalatie"], "ja" if escalatie else "")
    test.__doc__ = f"chatmail={chatmail} escalatie={escalatie} taalcheck_wist_tags={stript}"
    return test


class F2VierCombinaties(Basis):
    pass


for _cm in (False, True):
    for _esc in (False, True):
        for _str in (False, True):
            setattr(F2VierCombinaties,
                    f"test_f2_{'cm' if _cm else 'geencm'}_{'esc' if _esc else 'geenesc'}_"
                    f"{'taalcheckwist' if _str else 'taalcheckhoudt'}",
                    _f2_test(_cm, _esc, _str))


class F2Vangnet(Basis):
    def test_f2_zonder_tags_vangnet_blijft(self):
        """Het pad zonder tags houdt zijn bestaande vangnet op 'doorgestuurd' + adres."""
        for stript in (True, False):
            self.nep.aanroepen.clear()
            chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], "Ik heb je vraag doorgestuurd naar Sandy.",
                      taalcheck_stript=stript)
            self.assertEqual([p["json"]["tags"] for p in self.nep.hs_posts()], [["nina-escalatie"]])


# ── F4: claimdienst, één mail, fail closed, onzeker ≠ mislukt ─────────────────
class F4Gelijktijdig(Basis):
    def test_f4_gelijktijdig_een_post(self):
        """Twee gelijktijdige aanroepen, zelfde sessie en adres: de lock geeft één mail."""
        self.claim.barriere = threading.Barrier(2)
        self.claim.vertraging = 0.05
        uitkomsten, fouten = [], []

        def beurt():
            try:
                uitkomsten.append(asyncio.run(main.chat(types.SimpleNamespace(
                    message=GESPREK[-1]["content"], session_id=SESSIE,
                    history=[types.SimpleNamespace(**m) for m in GESPREK[:-1]])))["response"])
            except BaseException as e:  # noqa: BLE001 — wordt hieronder met eigen type opnieuw gegooid
                fouten.append(e)

        NepAnthropic.antwoord, NepAnthropic.taalcheck_stript = AFRONDING + " [[CHATMAIL]]", True
        with mock.patch.object(main.anthropic, "Anthropic", NepAnthropic):
            draden = [threading.Thread(target=beurt) for _ in range(2)]
            for d in draden:
                d.start()
            for d in draden:
                d.join(30)
        if fouten:
            raise fouten[0]
        self.assertEqual(self.claim.barriere_gehaald, 2, "beide claims waren tegelijk onderweg")
        self.assertEqual(len(self.nep.hs_posts()), 1)
        self.assertEqual(sorted(uitkomsten), sorted([AFRONDING, tekst("CHATMAIL_ONDERWEG")]))


class F4ZonderClaimInstellingen(Basis):
    omgeving = {k: v for k, v in OMGEVING.items() if k != "CLAIM_URL"}

    def test_f4_zonder_claim_url_geen_mail(self):
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertEqual(self.nep.hs_aanroepen(), [])
        self.assertEqual(resp, tekst("CHATMAIL_MISLUKT"))


class F4ZonderClaimSecret(Basis):
    omgeving = {k: v for k, v in OMGEVING.items() if k != "CLAIM_SECRET"}

    def test_f4_zonder_claim_secret_geen_mail(self):
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertEqual(resp, tekst("CHATMAIL_MISLUKT"))


def _claimfout_test(modus):
    def test(self):
        self.claim.modus = modus
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertEqual(self.nep.hs_aanroepen(), [])
        self.geen_succesclaim(resp)
        self.assertEqual(resp, tekst("CHATMAIL_MISLUKT"))
    test.__doc__ = f"claimdienst-modus {modus}: geen mail, eerlijke zin"
    return test


class F4Claimfout(Basis):
    pass


for _modus in ("http500", "geen_json", "ok_false", "timeout", "vreemde_uitkomst"):
    setattr(F4Claimfout, f"test_f4_claimfout_{_modus}_geen_mail", _claimfout_test(_modus))


class F4Onzeker(Basis):
    def setUp(self):
        super().setUp()
        self.nep.hs_reeks = ["timeout"]

    def test_f4_onzeker_geen_succesclaim(self):
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(len(self.nep.hs_posts()), 1)  # de poging vertrok
        self.geen_succesclaim(resp)
        self.assertEqual(resp, tekst("CHATMAIL_ONDERWEG"))

    def test_f4_onzeker_geen_mislukt_bevestiging(self):
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.geen_mislukt_melding(resp)
        self.assertEqual([b for b in self.claim.bevestigingen() if b.get("status") == "mislukt"], [])
        self.assertEqual([r["status"] for r in self.claim.rijen], ["bezig"])
        # Herhalen geeft `bezig`: geen tweede verzendpoging.
        chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(len(self.nep.hs_posts()), 1)


class F4ClaimdienstWeetHetAl(Basis):
    def sleutel(self):
        return hm(f"chatmail|{SESSIE}|petra@voorbeeld.nl")

    def test_f4_al_verstuurd_geen_nieuwe_post(self):
        self.claim.zaai(self.sleutel(), "verstuurd", ontvanger=hm("petra@voorbeeld.nl"))
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertEqual(resp, tekst("CHATMAIL_AL_VERSTUURD"))

    def test_f4_bezig_geen_post(self):
        self.claim.zaai(self.sleutel(), "bezig", ontvanger=hm("petra@voorbeeld.nl"))
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(self.nep.hs_posts(), [])
        self.geen_succesclaim(resp)
        self.assertEqual(resp, tekst("CHATMAIL_ONDERWEG"))

    def test_f4_limiet_ontvanger_geen_mail(self):
        for i in range(2):
            self.claim.zaai(hm(f"chatmail|andere-sessie-{i}|petra@voorbeeld.nl"), "verstuurd",
                            ontvanger=hm("petra@voorbeeld.nl"))
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertEqual(resp, tekst("CHATMAIL_LIMIET"))

    def test_f4_claimsleutel_volgt_contract(self):
        chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        claims = self.claim.claims()
        self.assertEqual(len(claims), 1)
        c = claims[0]
        self.assertTrue(c["_secret_klopt"])
        self.assertEqual((c["soort"], c["sleutel"], c["ontvanger"], c["bron"]),
                         ("chatmail", self.sleutel(), hm("petra@voorbeeld.nl"), ""))
        # r2.2: HMAC met CLAIM_SECRET, niet de kale sha256 (contract § Sleutels, 21-10 20:4x).
        self.assertNotEqual(c["sleutel"], sha(f"chatmail|{SESSIE}|petra@voorbeeld.nl"))
        self.assertNotEqual(c["ontvanger"], sha("petra@voorbeeld.nl"))
        bev = self.claim.bevestigingen()
        self.assertEqual([(b["sleutel"], b["status"], b["kenmerk"], b["registratie"]) for b in bev],
                         [(self.sleutel(), "verstuurd", "4242", "ok")])
        self.assertEqual(self.claim.rij(self.sleutel())["status"], "verstuurd")


class F4HmacNietKaleSha(Basis):
    def test_f4_claim_draagt_hmac_niet_kale_sha256(self):
        """r2.2: sleutel en ontvanger zijn HMAC-SHA256(CLAIM_SECRET, …), 64 hex, en hangen van het secret af."""
        chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        ander = "een-ander-geheim"
        with mock.patch.dict(os.environ, {"CLAIM_SECRET": ander}):
            self.claim.secret = ander
            chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]", session_id="s-ander")
        claims = self.claim.claims()
        self.assertEqual(len(claims), 2)  # eerst tellen: geen claim is een FAIL, geen unpack-fout
        c1, c2 = claims
        invoer = f"chatmail|{SESSIE}|petra@voorbeeld.nl"
        self.assertNotIn(c1["sleutel"], (sha(invoer), invoer))
        self.assertNotIn(c1["ontvanger"], (sha("petra@voorbeeld.nl"), "petra@voorbeeld.nl"))
        self.assertEqual(c1["sleutel"], hm(invoer))
        self.assertEqual(c1["ontvanger"], hm("petra@voorbeeld.nl"))
        self.assertRegex(c1["sleutel"], r"^[0-9a-f]{64}$")
        self.assertEqual(c2["ontvanger"], hm("petra@voorbeeld.nl", secret=ander))
        self.assertNotEqual(c2["ontvanger"], c1["ontvanger"])


class F4Herhaalroute(Basis):
    def test_f4_mislukt_houdt_herhaalroute(self):
        """Help Scout weigert zeker (4xx): `mislukt`; een volgende poging mag en slaagt, één keer."""
        self.nep.hs_reeks = [400, 201]
        eerste = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(eerste, tekst("CHATMAIL_MISLUKT"))
        self.assertEqual([b["status"] for b in self.claim.bevestigingen()], ["mislukt"])
        tweede = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(tweede, AFRONDING)
        self.assertEqual([b["status"] for b in self.claim.bevestigingen()], ["mislukt", "verstuurd"])
        derde = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(len(self.nep.hs_posts()), 2)  # 1 geweigerd + 1 geslaagd, geen derde
        self.assertEqual(derde, tekst("CHATMAIL_AL_VERSTUURD"))


class F4Help5xx(Basis):
    """Contract 21-10 19:5x: alleen 400-499 is zeker mislukt. Een 5xx is onzeker."""

    def test_f4_help_scout_5xx_onzeker(self):
        for status in (502, 504, 500):
            with self.subTest(status):
                self.claim = NepClaimdienst()
                self.nep = NepRequests(claimdienst=self.claim, hs_reeks=[status, 201])
                main._requests = self.nep
                main._hs_token_cache.update(access_token=None, expires_at=0)
                resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
                self.assertEqual(len(self.nep.hs_posts()), 1)
                self.geen_succesclaim(resp)
                self.geen_mislukt_melding(resp)
                self.assertEqual([b for b in self.claim.bevestigingen() if b.get("status") == "mislukt"], [])
                self.assertEqual([r["status"] for r in self.claim.rijen], ["bezig"])
                self.assertEqual(resp, tekst("CHATMAIL_ONDERWEG"))
                # Tweede poging: `bezig`, geen nieuwe post (de 201 in de reeks wordt niet gebruikt).
                tweede = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
                self.assertEqual(len(self.nep.hs_posts()), 1)
                self.assertEqual(tweede, tekst("CHATMAIL_ONDERWEG"))

    def test_f4_help_scout_4xx_blijft_mislukt(self):
        for status in (400, 422, 499):
            with self.subTest(status):
                self.claim = NepClaimdienst()
                self.nep = NepRequests(claimdienst=self.claim, hs_reeks=[status, 201])
                main._requests = self.nep
                main._hs_token_cache.update(access_token=None, expires_at=0)
                eerste = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
                self.assertEqual(eerste, tekst("CHATMAIL_MISLUKT"))
                self.assertEqual([b["status"] for b in self.claim.bevestigingen()], ["mislukt"])
                tweede = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
                self.assertEqual(tweede, AFRONDING)
                self.assertEqual(len(self.nep.chatmail_posts()), 2)  # 1 geweigerd + 1 geslaagd


class F4ZonderSessie(Basis):
    def test_f4_zonder_sessie_een_mail_per_adres_per_dag(self):
        voor = datum_amsterdam()
        main._verwerk_chatmail(GESPREK, AFRONDING)
        main._verwerk_chatmail(GESPREK, AFRONDING)
        na = datum_amsterdam()
        self.assertEqual(len(self.nep.hs_posts()), 1)
        sleutels = {c["sleutel"] for c in self.claim.claims()}
        verwacht = {hm(f"chatmail|geen-sessie|petra@voorbeeld.nl|{d}") for d in (voor, na)}
        self.assertEqual(len(sleutels), 1)
        self.assertTrue(sleutels <= verwacht, (sleutels, verwacht))


class F4ElkeWachtBegrensd(Basis):
    def test_f4_elke_wacht_begrensd(self):
        """Beurt met beide tags: claim, token, twee Help Scout-posts, registratie, bevestig, log.
        ⚑ r3 (route B): de vier MailBlue-aanroepen zijn één aanroep van de websiteroute geworden,
        dus 7 in plaats van 9; dat die registratie in de beurt zat, eist de toets nu apart."""
        chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[ESCALATIE]] [[CHATMAIL]]")
        soorten = {re.sub(r"https://[^/]+", "", u_) or u_ for m, u_, kw in self.nep.aanroepen}
        self.assertGreaterEqual(len(self.nep.aanroepen), 7, soorten)
        self.assertIn("/api/nina-registratie", soorten)
        for m, u_, kw in self.nep.aanroepen:
            t = kw.get("timeout")
            self.assertIsNotNone(t, f"{m} {u_} zonder timeout")
            delen = t if isinstance(t, tuple) else (t,)
            self.assertTrue(all(isinstance(x, (int, float)) and 0 < x <= 30 for x in delen), (u_, t))


# ── C1/F1: de server bepaalt de zichtbare verzendclaim ────────────────────────
class C1Claim(Basis):
    def test_c1_succes_alleen_na_bewezen_verzending(self):
        """Invariant: 'ik heb ons gesprek naar je gemaild' alleen bij een geslaagde post in deze beurt."""
        scenario = {
            "geslaagd": dict(hs=[201], tag=True),
            "help scout 400": dict(hs=[400], tag=True),
            "help scout 502": dict(hs=[502], tag=True),
            "help scout time-out": dict(hs=["timeout"], tag=True),
            "claimfout": dict(hs=[201], tag=True, modus="http500"),
            "geen tag": dict(hs=[201], tag=False),
        }
        for naam, s in scenario.items():
            with self.subTest(naam):
                # Per scenario een verse claimdienst en nep-HTTP (de omgeving blijft gelijk).
                self.claim = NepClaimdienst()
                self.nep = NepRequests(claimdienst=self.claim, hs_reeks=s["hs"])
                main._requests = self.nep
                main._hs_token_cache.update(access_token=None, expires_at=0)
                self.claim.modus = s.get("modus", "ok")
                resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"],
                                 AFRONDING + (" [[CHATMAIL]]" if s["tag"] else ""))
                gelukt = s["tag"] and s.get("modus", "ok") == "ok" and s["hs"][0] == 201
                self.assertEqual(SUCCESZIN in resp.lower(), gelukt, (naam, resp))
                self.assertEqual(len(self.nep.chatmail_posts()) == 1 and gelukt, gelukt)

    def test_c1_ontkenning_en_aanbod_blijven_staan(self):
        for t in ("Ik heb ons gesprek nog niet gemaild, maar kan dat doen.", AANBOD, TOESTEMMING,
                  "Even checken: je naam is Petra de Vries en je e-mailadres is petra@voorbeeld.nl, klopt dat?"):
            with self.subTest(t[:30]):
                self.assertEqual(chatbeurt(GESPREK[:3], "Ja", t), t)
        # r2.3: ook in de mailbevestigingsfase (adres bevestigd) blijft een ontkenning staan.
        for t in ("Ik heb ons gesprek nog niet gemaild, maar kan dat doen.",
                  "Ik heb ons gesprek nog niet verstuurd."):
            with self.subTest("fase: " + t[:30]):
                self.assertEqual(chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], t), t)


# ── r2.3 (Jordan closing C1/F1): gewone verzendclaims in de mailbevestigingsfase ──
class C1Varianten(Basis):
    """Jordans twee zinnen en varianten: na de adresbevestiging, zonder tag, 0 posts.
    Zijn reproducer: de bezoeker mag de modelzin niet ongewijzigd terugkrijgen."""

    def controleer(self, zin):
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], zin)
        self.assertEqual(len(self.nep.hs_posts()), 0)
        self.assertNotEqual(resp, zin, "onbewezen verzendclaim bleef staan")
        self.assertNotIn(zin.rstrip(".!").lower(), resp.lower())
        self.assertEqual(resp, tekst("CHATMAIL_NIET_VERSTUURD"))

    def test_c1_variant_gesprek_is_verzonden(self):
        self.controleer("Het gesprek is verzonden.")

    def test_c1_variant_chat_verstuurd(self):
        self.controleer("Ik heb je de chat verstuurd.")

    def test_c1_varianten_in_bevestigingsfase(self):
        for zin in ("Ons gesprek is verstuurd!", "Het gesprek is verstuurd.", "De chat is verzonden.",
                    "Ik heb je ons gesprek gestuurd.", "Ik heb je het gesprek toegestuurd.",
                    "Ik heb je ons gesprekje verstuurd.", "Het chatgesprek is verzonden.",
                    "De mail is verstuurd.", "Ik heb je de mail met ons gesprek gestuurd."):
            with self.subTest(zin):
                self.nep.aanroepen.clear()
                self.controleer(zin)

    def test_c1_variant_met_andere_inhoud_houdt_de_rest(self):
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"],
                         "Fijn dat ik je kon helpen. Het gesprek is verzonden.")
        self.assertEqual(self.nep.hs_posts(), [])
        self.assertEqual(resp, "Fijn dat ik je kon helpen.\n\n" + tekst("CHATMAIL_NIET_VERSTUURD"))

    def test_c1_fase_laat_escalatiezin_staan(self):
        """Geen te brede herkenning: een escalatiezin naar Sandy blijft staan en escaleert."""
        zin = "Ik heb je vraag doorgestuurd naar Sandy."
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], zin)
        self.assertEqual(resp, zin)
        self.assertEqual([p["json"]["tags"] for p in self.nep.hs_posts()], [["nina-escalatie"]])

    def test_c1_tagbeurt_alleen_servertekst(self):
        """In een gewone chatmailbeurt bepaalt de modeltekst niet wat de bezoeker over de
        verzending leest: ook een niet-herkende claim ("Check je inbox") verdwijnt."""
        model = "Klaar! Check je inbox. [[CHATMAIL]]"
        for naam, hs, verwacht in (("geslaagd", [201], "CHATMAIL_AFRONDING"),
                                    ("onzeker", ["timeout"], "CHATMAIL_ONDERWEG"),
                                    ("geweigerd", [400], "CHATMAIL_MISLUKT")):
            with self.subTest(naam):
                self.claim = NepClaimdienst()
                self.nep = NepRequests(claimdienst=self.claim, hs_reeks=hs)
                main._requests = self.nep
                main._hs_token_cache.update(access_token=None, expires_at=0)
                resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], model)
                # De modelzinnen mogen er niet staan (de serverzin N4 noemt zelf wél "inbox").
                self.assertNotIn("klaar!", resp.lower())
                self.assertNotIn("check je inbox", resp.lower())
                self.assertEqual(resp, tekst(verwacht))


# ── Vervolgafspraken: Carries tekst, instructie vastgesteld, logmarkering ─────
class VervolgAfspraken(Basis):
    CARRIE_SHA = "4a49fbea6d91ed6b1fb2f337b192748a127cf4874cfbed5ca46eca10b30cca7e"

    def test_carrie_tekst_letterlijk(self):
        import hashlib
        b = ("\n\n".join(main.CHATMAIL_BEGELEIDING) + "\n").encode("utf-8")
        self.assertEqual(hashlib.sha256(b).hexdigest(), self.CARRIE_SHA)

    def test_mail_zonder_eigen_groet(self):
        main._verwerk_chatmail(GESPREK, AFRONDING)
        posts = self.nep.chatmail_posts()
        self.assertEqual(len(posts), 1)
        html = posts[0]["json"]["threads"][0]["text"]
        self.assertIn("Hallo Petra,", html)
        self.assertIn("je gesprek met Nina, onze digitale assistent", html)
        self.assertNotIn("Hartelijke groet", html)
        self.assertNotIn("Nina, de digitale assistent van SanaYou YOGAcademy", html)

    def test_instructie_vastgesteld_met_privacyvoorwaarde(self):
        with open(os.path.join(REPO, "knowledge", "instructies", "gesprek-mailen.md"), encoding="utf-8") as f:
            t = f.read()
        self.assertIn("## STATUS: VASTGESTELD (Olivia, 21-10)", t)
        self.assertIn("Live pas als ook de privacyverklaring deze route noemt", t)
        self.assertNotIn("CONCEPT (Jared", t)

    def test_instructie_zonder_woordmatch(self):
        with open(os.path.join(REPO, "knowledge", "instructies", "gesprek-mailen.md"), encoding="utf-8") as f:
            t = f.read()
        self.assertNotIn("Daaraan herkent het systeem dat dit gesprek al gemaild is", t)
        self.assertNotIn("Zet de tag dan niet opnieuw", t)
        self.assertIn("Top, ik heb ons gesprek naar je gemaild. [[CHATMAIL]]", t)
        # r2.3: geen universele belofte over vrije modeltekst; de instructie zegt dat het filter begrensd is.
        self.assertNotIn("Zo'n zin haalt het systeem weg, want", t)
        self.assertIn("die herkenning is beperkt", t)

    CARRIE = "/Users/skarsten/.flashbag/output/carrie/sandy-schrijverskamer/2026-10-21/nina-wijzer-negen-meldingen"
    MELDINGEN = {  # r2.4: Carrie's definitieve teksten, byte-gelijk (sha256 van haar bestand)
        "CHATMAIL_MISLUKT": ("N1-CHATMAIL_MISLUKT.txt", "8534a35dad6ee7a95c2d82974d9860598c69f1294af769f429d6090aa8d90d76"),
        "CHATMAIL_GEEN_ADRES": ("N2-CHATMAIL_GEEN_ADRES.txt", "a0124a90c608ed9ea510615d1a567f7963e056adfa8ad09330f34e6a3a183370"),
        "CHATMAIL_AL_VERSTUURD": ("N3-CHATMAIL_AL_VERSTUURD.txt", "76626f21dbc09fa9975360de729e0590cccb66013e5a6d4e082f2fc312551260"),
        "CHATMAIL_LIMIET": ("N5-CHATMAIL_LIMIET.txt", "6c4e7d6d699b0cd4cb496efe066024fefd3760416c1378794d482abd363e0d12"),
        "CHATMAIL_NIET_VERSTUURD": ("N6-CHATMAIL_NIET_VERSTUURD.txt", "1e21c064a16005404c4887c2b3b97d31f85920c697939f0096b904a956eeb402"),
    }
    N4_CARRIE_SHA = "b0ca73cc2ce1cf1eaba1194756568755b1a35c32b362bf7daded845b01cc1bf6"
    N4_NU = ("Ik weet nog niet zeker of de mail met ons gesprek goed is aangekomen. "
             "Kijk over een paar minuten even in je inbox. We kijken het ook na. Je hoeft het dus niet opnieuw te vragen.")

    def test_carrie_meldingen_byte_gelijk(self):
        """N1, N2, N3, N5, N6: sha256 van de constante = sha256 van Carrie's bestand; is het bestand er
        (zoals in dit pakket), dan ook byte voor byte gelijk aan dat bestand."""
        import hashlib
        for naam, (bestand, h) in self.MELDINGEN.items():
            with self.subTest(naam):
                waarde = tekst(naam).encode("utf-8")
                self.assertEqual(hashlib.sha256(waarde).hexdigest(), h)
                pad = os.path.join(self.CARRIE, bestand)
                if os.path.exists(pad):
                    with open(pad, "rb") as f:
                        self.assertEqual(waarde, f.read())

    def test_onderweg_is_carrie_n4(self):
        """N4 letterlijk, mét "We kijken het ook na." en "dus" (terug sinds 23-10: de onzeker-controle draait,
        eigenaar Olivia). sha256 van de constante = Carrie's opgave; is haar bestand er, dan byte-gelijk."""
        import hashlib
        self.assertEqual(tekst("CHATMAIL_ONDERWEG"), self.N4_NU)
        self.assertEqual(hashlib.sha256(tekst("CHATMAIL_ONDERWEG").encode("utf-8")).hexdigest(), self.N4_CARRIE_SHA)
        pad = os.path.join(self.CARRIE, "N4-CHATMAIL_ONDERWEG.txt")
        if os.path.exists(pad):
            with open(pad, "rb") as f:
                self.assertEqual(tekst("CHATMAIL_ONDERWEG").encode("utf-8"), f.read())

    def test_geen_concept_markering_meer(self):
        with open(main.__file__, encoding="utf-8") as f:
            self.assertNotIn("CONCEPT-TEKST", f.read())

    def test_n3_servertekst_blijft_staan_en_blokkeert_niets(self):
        """Carrie's meetpunt 1: N3 is zelf een claimzin. (a) In de beurt waarin de server hem neerzet,
        filtert niets hem weg. (b) In een volgende beurt (in de geschiedenis) veroorzaakt hij geen
        verzending en (c) blokkeert hij er ook geen: de claimdienst beslist."""
        n3 = tekst("CHATMAIL_AL_VERSTUURD")
        self.claim.zaai(hm(f"chatmail|{SESSIE}|petra@voorbeeld.nl"), "verstuurd")
        resp = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(resp, n3)                                   # (a) staat er volledig
        self.assertEqual(self.nep.hs_posts(), [])
        historie = GESPREK + [a(n3)]
        # (b) volgende beurt zonder tag, neutraal antwoord: geen post, antwoord ongewijzigd
        resp2 = chatbeurt(historie, "Dankjewel!", "Graag gedaan, Petra.")
        self.assertEqual(resp2, "Graag gedaan, Petra.")
        self.assertEqual(self.nep.hs_posts(), [])
        # (c) volgende beurt mét tag in een andere sessie: N3 in de geschiedenis blokkeert niets
        resp3 = chatbeurt(historie, "Kun je hem toch nog eens sturen?", AFRONDING + " [[CHATMAIL]]",
                          session_id="andere-sessie-n3")
        self.assertEqual(len(self.nep.chatmail_posts()), 1)
        self.assertEqual(resp3, AFRONDING)

    def test_a1_geen_logmarkering_zonder_verzending(self):
        self.claim.zaai(hm(f"chatmail|{SESSIE}|petra@voorbeeld.nl"), "verstuurd")
        chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        log = self.nep.logregels()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["chatmail"], "")
        self.assertNotIn("gesprek gemaild", log[0]["artikelen"])


class ZZVangnet(unittest.TestCase):
    def test_zz_geen_enkele_netwerkpoging(self):
        self.assertEqual(NETWERKPOGINGEN, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
