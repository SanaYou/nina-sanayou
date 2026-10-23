"""Toetst "Nina mailt het gesprek" (Sandy, 21-10-2026; r2 na Jordans review van 21-10).

r2: de 38 tests van r1 blijven bestaan. Waar een test een r1-gedrag vastlegde dat Jordan
afkeurde (woordmatch als verzendbewijs, succeszin zonder verzending) of de oude groet
van Carrie, staat een ⚑ r2-regel met de reden. De nieuwe regressies (J1-J5, F2, F3, F4)
staan in test_chatmail_r2.py, interne eenheden in test_chatmail_r2_intern.py.

Elke HTTP-aanroep loopt via een nep-`_requests` die alles noteert; daarbuiten hangt een
netwerkvangnet dat élke socketverbinding weigert en telt. De laatste test eist dat dat
vangnet nul pogingen zag, zodat een groene suite nooit een echte mail of contact kan
hebben gemaakt.

Draaien vanuit de repo-wortel:  python3 -m unittest tests.test_chatmail -v
"""
import asyncio
import os
import re
import socket
import sys
import types
import unittest
from unittest import mock

# ── Buitenste vangnet: geen enkele netwerkverbinding vanuit deze suite ──────────
NETWERKPOGINGEN = []


def _geen_netwerk(*a, **k):
    NETWERKPOGINGEN.append(repr(a)[:120])
    raise OSError("netwerk geblokkeerd in test_chatmail")


socket.socket.connect = _geen_netwerk
socket.create_connection = _geen_netwerk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.test_opstart  # noqa: E402,F401  — nep-pakketten + importeert main van DEZE repo
import main  # noqa: E402
from tests.nep_claimdienst import (  # noqa: E402
    NepClaimdienst, NepWebsite, CLAIM_URL, CLAIM_SECRET, REGISTRATIE_URL, hm)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
assert os.path.dirname(os.path.abspath(main.__file__)) == REPO, main.__file__


def u(t): return {"role": "user", "content": t}
def a(t): return {"role": "assistant", "content": t}


AANBOD = ("Zal ik dit gesprek even naar je mailen? Dan heb je alles bij elkaar. Heb ik iets over "
          "het hoofd gezien, dan sturen we je nog een aanvulling. En als jij daarna nog een vraag "
          "hebt, reageer je gewoon op die mail.")
GESPREK = [
    u("Wat is het verschil tussen de klassikale en de online Yin-opleiding?"),
    a("De klassikale opleiding volg je in Zwolle, de online variant in je eigen tempo. "
      "Kijk ook op [de opleidingspagina](https://sanayou.com/ryt200-yin-yoga-opleiding-klassikaal).\n\n"
      + AANBOD),
    u("Ja graag"),
    a("Fijn! Mag ik je naam en e-mailadres?"),
    u("Petra de Vries, petra@voorbeeld.nl"),
    a("Even checken: je naam is Petra de Vries en je e-mailadres is petra@voorbeeld.nl, klopt dat?"),
    u("Ja, klopt!"),
]
AFRONDING = "Top, ik heb ons gesprek naar je gemaild."


class Antwoord:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class NepRequests:
    """Noteert elke aanroep; antwoordt per URL-patroon."""

    def __init__(self, hs_status=201):
        self.aanroepen = []
        self.claimdienst = NepClaimdienst()  # r2: claimdienst volgens het contract
        self.hs_status = hs_status
        self.website = NepWebsite()  # r3: registratie via de websiteroute (route B)

    def _antwoord(self, methode, url, kw):
        self.aanroepen.append((methode, url, kw))
        if url == CLAIM_URL:
            status, body = self.claimdienst.verwerk(kw.get("json"))
            return Antwoord(status, body)
        if url.endswith("/oauth2/token"):
            return Antwoord(200, {"access_token": "nep-token", "expires_in": 7200})
        if url.endswith("/v2/conversations"):
            return Antwoord(self.hs_status, {}, {"Resource-Id": "4242"})
        if url == REGISTRATIE_URL:
            status, body = self.website.verwerk(kw)
            return Antwoord(status, body)
        return Antwoord(200, {})

    def post(self, url, **kw):
        return self._antwoord("POST", url, kw)

    def get(self, url, **kw):
        return self._antwoord("GET", url, kw)

    def urls(self):
        return [(m, u.split("/api/3")[-1] if "/api/3" in u else u) for m, u, _ in self.aanroepen]

    def hs_posts(self):
        return [kw for m, u, kw in self.aanroepen if u.endswith("/v2/conversations")]

    def mailblue(self):
        """Rechtstreekse MailBlue-aanroepen vanuit Nina: sinds r3 altijd leeg."""
        return [(m, u.split("/api/3")[-1]) for m, u, _ in self.aanroepen if "/api/3" in u]

    def registraties(self):
        return [kw for m, u, kw in self.aanroepen if u == REGISTRATIE_URL]


OMGEVING = {
    "HELPSCOUT_APP_ID": "nep-id", "HELPSCOUT_APP_SECRET": "nep-geheim",
    "HELPSCOUT_MAILBOX_ID": "363337",
    "NINA_LOG_URL": "https://log.voorbeeld.invalid/exec", "NINA_LOG_SECRET": "nep",
    "ANTHROPIC_API_KEY": "nep-anthropic",
    "CLAIM_URL": CLAIM_URL, "CLAIM_SECRET": CLAIM_SECRET,  # r2: zonder claimdienst geen mail
}


class Basis(unittest.TestCase):
    hs_status = 201

    def setUp(self):
        self.nep = NepRequests(hs_status=self.hs_status)
        self._oud_requests = main._requests
        main._requests = self.nep
        main._hs_token_cache.update(access_token=None, expires_at=0)
        self._env = mock.patch.dict(os.environ, OMGEVING, clear=False)
        self._env.start()

    def enige_mailtekst(self):
        """De tekst van de ene Help Scout-post; eerst het aantal, zodat een ontbrekende post FAIL is, geen IndexError."""
        posts = self.nep.hs_posts()
        self.assertEqual(len(posts), 1, "verwacht precies één Help Scout-post")
        return posts[0]["json"]["threads"][0]["text"]

    def tearDown(self):
        main._requests = self._oud_requests
        self._env.stop()


# ── 1. De tag ───────────────────────────────────────────────────────────────
class T1Tag(unittest.TestCase):
    def test_tag_weg_en_herkend(self):
        tekst, gevonden = main._strip_chatmail_tag(AFRONDING + " [[CHATMAIL]]")
        self.assertTrue(gevonden)
        self.assertEqual(tekst, AFRONDING)

    def test_tag_tussen_backticks(self):
        tekst, gevonden = main._strip_chatmail_tag(AFRONDING + " `[[CHATMAIL]]`")
        self.assertTrue(gevonden)
        self.assertNotIn("[[", tekst)
        self.assertNotIn("`", tekst)

    def test_zonder_tag(self):
        self.assertEqual(main._strip_chatmail_tag(AFRONDING), (AFRONDING, False))

    def test_escalatietag_is_geen_chatmailtag(self):
        self.assertFalse(main._strip_chatmail_tag("Doorgestuurd. [[ESCALATIE]]")[1])


# ── 2. Alleen naar een bevestigd adres ───────────────────────────────────────
class T2Adres(unittest.TestCase):
    def test_bevestigd_adres_en_naam(self):
        self.assertEqual(main._bevestigde_gegevens(GESPREK), ("Petra de Vries", "petra@voorbeeld.nl"))

    def test_nee_op_bevestiging_geeft_niets(self):
        g = GESPREK[:-1] + [u("Nee, het is petra@anders.nl")]
        self.assertEqual(main._bevestigde_gegevens(g), (None, None))

    def test_ja_maar_niet_geeft_niets(self):
        g = GESPREK[:-1] + [u("Ja maar niet dat adres")]
        self.assertEqual(main._bevestigde_gegevens(g), (None, None))

    def test_geen_bevestigingsvraag_geen_adres(self):
        """Een los adres van de bezoeker is nooit genoeg (de escalatie valt daar wél op terug)."""
        g = [u("Mail het naar petra@voorbeeld.nl"), a("Prima."), u("ja")]
        self.assertEqual(main._bevestigde_gegevens(g), (None, None))

    def test_bevestigingsvraag_nog_onbeantwoord(self):
        self.assertEqual(main._bevestigde_gegevens(GESPREK[:-1]), (None, None))

    def test_laatste_bevestiging_wint(self):
        g = GESPREK[:-1] + [
            u("Nee, het moet petra@nieuw.nl zijn"),
            a("Even checken: je naam is Petra de Vries en je e-mailadres is petra@nieuw.nl, klopt dat?"),
            u("ja"),
        ]
        self.assertEqual(main._bevestigde_gegevens(g), ("Petra de Vries", "petra@nieuw.nl"))


# ── 3. Eén mail per gesprek ──────────────────────────────────────────────────
# ⚑ r2 (Jordan F4): r1 toetste hier `_chatmail_al_verstuurd`, een woordmatch op de historie
# die als verzendbewijs gold. Die bestaat niet meer; de claimdienst beslist. Dezelfde vier
# situaties toetsen nu de herkenning van een verzendclaim (die alleen nog bepaalt welke
# modelzin weg moet als er geen bewezen verzending achter zit).
class T3Dubbel(unittest.TestCase):
    def test_eerdere_afronding_bewijst_niets(self):
        """r1-naam test_eerdere_afronding_telt. Een afrondingszin in de (client)historie
        blokkeert niets meer: de claimdienst beslist, en die kent deze sessie nog niet."""
        nep = NepRequests()
        oud, main._requests = main._requests, nep
        main._hs_token_cache.update(access_token=None, expires_at=0)
        try:
            with mock.patch.dict(os.environ, OMGEVING):
                uit = main._verwerk_chatmail(GESPREK + [a(AFRONDING), u("Kun je het nog eens mailen?")],
                                             AFRONDING, session_id="t3")
        finally:
            main._requests = oud
        self.assertEqual(len(nep.claimdienst.claims()), 1)
        self.assertTrue(uit["verstuurd"], uit)

    def test_lesreader_gemaild_telt_niet(self):
        self.assertFalse(main._is_claimzin("Ja, de lesreader krijg je gemaild na je aanmelding."))

    def test_bevestigingsvraag_telt_niet(self):
        tekst = "Even checken: je e-mailadres is p@v.nl, klopt dat? Dan krijg je ons gesprek gemaild."
        self.assertEqual(main._zonder_claimzinnen(tekst), (tekst, 0))

    def test_mislukt_tekst_laat_opnieuw_proberen_toe(self):
        self.assertFalse(main._is_claimzin(main.CHATMAIL_MISLUKT))
        self.assertEqual(main._bevestigde_gegevens(GESPREK + [a(main.CHATMAIL_MISLUKT), u("Probeer maar")]),
                         ("Petra de Vries", "petra@voorbeeld.nl"))


# ── 4. De verzending ─────────────────────────────────────────────────────────
class T4Verzending(Basis):
    def test_help_scout_nieuw_uitgaand_gesloten(self):
        uit = main._verwerk_chatmail(GESPREK, AFRONDING)
        self.assertTrue(uit["verstuurd"], uit)
        self.assertEqual(uit["antwoord"], AFRONDING)
        self.assertEqual(uit["conversation_id"], 4242)
        posts = self.nep.hs_posts()
        self.assertEqual(len(posts), 1)
        j = posts[0]["json"]
        self.assertEqual(j["status"], "closed")
        self.assertEqual(j["type"], "email")
        self.assertIs(j["imported"], False)
        self.assertEqual(j["mailboxId"], 363337)
        self.assertEqual(j["customer"], {"email": "petra@voorbeeld.nl", "firstName": "Petra de Vries"})
        self.assertEqual(j["tags"], ["nina-chatmail"])
        self.assertEqual(len(j["threads"]), 1)
        self.assertEqual(j["threads"][0]["type"], "reply")
        self.assertEqual(j["threads"][0]["customer"], {"email": "petra@voorbeeld.nl"})
        self.assertNotIn("draft", j["threads"][0])
        self.assertIn("timeout", posts[0])

    def test_mail_bevat_het_hele_gesprek(self):
        main._verwerk_chatmail(GESPREK, AFRONDING)
        tekst = self.enige_mailtekst()
        for regel in ("verschil tussen de klassikale en de online Yin-opleiding",
                      "Petra de Vries, petra@voorbeeld.nl", "Ja, klopt!", AFRONDING):
            self.assertIn(regel, tekst)
        self.assertIn("Hallo Petra,", tekst)
        # ⚑ r2: Carries tekst zonder groet (21-10); de mail leest onder Sandy's handtekening.
        self.assertIn("je gesprek met Nina, onze digitale assistent", tekst)
        self.assertNotIn("Hartelijke groet", tekst)
        self.assertIn('<a href="https://sanayou.com/ryt200-yin-yoga-opleiding-klassikaal">de opleidingspagina</a>', tekst)
        self.assertEqual(tekst.count("<strong style"), 7 + 1)

    def test_bezoekerstekst_wordt_geescaped(self):
        g = [u("<script>alert(1)</script> en [klik](javascript:alert(1))")] + GESPREK[1:]
        main._verwerk_chatmail(g, AFRONDING)
        tekst = self.enige_mailtekst()
        self.assertNotIn("<script>", tekst)
        self.assertIn("&lt;script&gt;", tekst)
        self.assertNotIn('href="javascript', tekst)

    def test_mailblue_contact_tag_en_hoofdlijst(self):
        """⚑ r3 (route B, Tonny 22-10): Nina praat niet meer met MailBlue. Eén aanroep van de
        websiteroute met naam en adres; contact, brontag 454 en Hoofdlijst 80 zet de website
        (toetsen in sanayou-pages test/nina-registratie-api.test.mjs)."""
        uit = main._verwerk_chatmail(GESPREK, AFRONDING)
        self.assertEqual(self.nep.mailblue(), [])
        regs = self.nep.registraties()
        self.assertEqual(len(regs), 1)
        self.assertEqual({k: regs[0]["json"][k] for k in ("naam", "email")},
                         {"naam": "Petra de Vries", "email": "petra@voorbeeld.nl"})
        self.assertEqual(self.nep.website.geweigerd, [])
        self.assertIn("op de Hoofdlijst", uit["mailblue"])

    def test_mail_gaat_voor_mailblue(self):
        main._verwerk_chatmail(GESPREK, AFRONDING)
        urls = [u_ for m, u_, kw in self.nep.aanroepen]
        eerste_hs = min(i for i, x in enumerate(urls) if x.endswith("/v2/conversations"))
        eerste_reg = min(i for i, x in enumerate(urls) if x == REGISTRATIE_URL)
        self.assertLess(eerste_hs, eerste_reg)

    def test_zonder_bevestigd_adres_geen_enkele_aanroep(self):
        uit = main._verwerk_chatmail(GESPREK[:-1] + [u("nee")], AFRONDING)
        self.assertFalse(uit["verstuurd"])
        self.assertEqual(uit["antwoord"], main.CHATMAIL_GEEN_ADRES)
        self.assertEqual(self.nep.aanroepen, [])

    def test_al_gemaild_geen_enkele_aanroep(self):
        """⚑ r2 (F4): 'al gemaild' komt van de claimdienst, niet uit de historie. Dus wél
        één claim-aanroep, maar geen enkele Help Scout- of MailBlue-aanroep."""
        self.nep.claimdienst.zaai(hm("chatmail|s1|petra@voorbeeld.nl"), "verstuurd")
        g = GESPREK + [a(AFRONDING), u("Kun je het nog een keer mailen?")]
        uit = main._verwerk_chatmail(g, AFRONDING, session_id="s1")
        self.assertFalse(uit["verstuurd"])
        self.assertEqual(uit["reden"], "claimdienst: al_verstuurd")
        self.assertEqual(uit["antwoord"], main.CHATMAIL_AL_VERSTUURD)
        self.assertEqual([u_ for m, u_, kw in self.nep.aanroepen], [CLAIM_URL])

    # ⚑ r3: test_brontag_niet_ingesteld_geen_mailblue en test_hoofdlijst_uitgeschakeld zijn weg:
    # brontag en Hoofdlijst liggen sinds route B vast in de websiteroute, niet meer in Nina.


class T4bAlleenVoornaam(Basis):
    def test_geen_lege_achternaam_naar_mailblue(self):
        g = [m if "klopt dat?" not in m["content"] else
             a("Even checken: je naam is Petra en je e-mailadres is petra@voorbeeld.nl, klopt dat?")
             for m in GESPREK]
        main._verwerk_chatmail(g, AFRONDING)
        regs = self.nep.registraties()
        self.assertEqual(len(regs), 1)
        # ⚑ r3: de website splitst de naam; "geen lege achternaam" toetst de website zelf.
        self.assertEqual(regs[0]["json"]["naam"], "Petra")


# ⚑ r3: T5BestaandLijstrecord ("afgemeld blijft afgemeld") staat nu in de websitetoetsen
# (sanayou-pages test/nina-registratie-api.test.mjs: 'afgemeld Hoofdlijst-record blijft afgemeld').


class T6HelpScoutFaalt(Basis):
    # ⚑ r2 (contract 21-10 19:5x): alleen 400-499 is zeker mislukt; r1 gebruikte hier 500,
    # dat is nu onzeker (zie test_chatmail_r2.F4Help5xx).
    hs_status = 400

    def test_eerlijk_antwoord_en_geen_mailblue(self):
        uit = main._verwerk_chatmail(GESPREK, AFRONDING)
        self.assertFalse(uit["verstuurd"])
        self.assertEqual(uit["antwoord"], main.CHATMAIL_MISLUKT)
        self.assertNotIn("gemaild", uit["antwoord"])
        self.assertEqual(self.nep.mailblue(), [])
        self.assertEqual(self.nep.registraties(), [])


class T7MailBlueFaalt(Basis):
    def test_mail_staat_bezoeker_ziet_succes(self):
        self.nep.website.status, self.nep.website.body = 502, {"ok": False, "fout": "MailBlue: sync 500"}
        uit = main._verwerk_chatmail(GESPREK, AFRONDING)
        self.assertTrue(uit["verstuurd"])
        self.assertEqual(uit["antwoord"], AFRONDING)
        self.assertTrue(uit["mailblue"].startswith("mislukt"), uit["mailblue"])


# ── 8. Het /chat-eindpunt ────────────────────────────────────────────────────
class NepAnthropic:
    """Hoofdmodel geeft `antwoord`; de taalcheck (haiku) gooit elke [[..]]-tag weg,
    zoals een model dat een tag 'opruimt' zou doen."""
    antwoord = ""

    def __init__(self, api_key=None, timeout=None):
        self.api_key = api_key
        self.messages = self

    def create(self, model="", messages=None, **kw):
        if model.startswith("claude-haiku"):
            tekst = messages[0]["content"].split("Tekst:\n", 1)[1]
            return types.SimpleNamespace(content=[types.SimpleNamespace(
                text=re.sub(r"\[\[[A-Z]+\]\]", "", tekst).strip())])
        return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=NepAnthropic.antwoord)])


def chatbeurt(gesprek, laatste, antwoord):
    NepAnthropic.antwoord = antwoord
    verzoek = types.SimpleNamespace(
        message=laatste, session_id="test-chatmail",
        history=[types.SimpleNamespace(role=m["role"], content=m["content"]) for m in gesprek])
    with mock.patch.object(main.anthropic, "Anthropic", NepAnthropic):
        return asyncio.run(main.chat(verzoek))


class T8Eindpunt(Basis):
    def logregels(self):
        return [kw["json"] for m, u_, kw in self.nep.aanroepen if u_ == OMGEVING["NINA_LOG_URL"]]

    def test_chatmailbeurt_end_to_end(self):
        uit = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(uit, {"response": AFRONDING})
        self.assertEqual(len(self.nep.hs_posts()), 1)
        log = self.logregels()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["chatmail"], "ja")
        self.assertTrue(log[0]["artikelen"].endswith(main.CHATMAIL_LOGMARKERING))
        self.assertEqual(log[0]["escalatie"], "")

    def test_gemailde_tekst_is_het_volledige_gesprek(self):
        """Niet de tot 20 berichten ingekorte modelhistorie: het hele gesprek."""
        lang = []
        for i in range(12):
            lang += [u(f"Vraag nummer {i} over Yin"), a(f"Antwoord nummer {i}.")]
        chatbeurt(lang + GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        tekst = self.enige_mailtekst()
        self.assertIn("Vraag nummer 0 over Yin", tekst)

    def test_chatmail_met_doorgestuurd_maakt_geen_escalatie(self):
        chatbeurt(GESPREK[:-1], GESPREK[-1]["content"],
                  "Top, ik heb ons gesprek naar je doorgestuurd en gemaild. [[CHATMAIL]]")
        self.assertEqual(len(self.nep.hs_posts()), 1)
        self.assertEqual(self.nep.hs_posts()[0]["json"]["tags"], ["nina-chatmail"])
        self.assertEqual(self.logregels()[0]["escalatie"], "")

    def test_beide_tags_escaleert_wel(self):
        chatbeurt(GESPREK[:-1], GESPREK[-1]["content"],
                  "Ik heb het doorgestuurd naar Sandy en ons gesprek naar je gemaild. [[ESCALATIE]] [[CHATMAIL]]")
        tags = sorted(p["json"]["tags"][0] for p in self.nep.hs_posts())
        self.assertEqual(tags, ["nina-chatmail", "nina-escalatie"])
        self.assertEqual(self.logregels()[0]["escalatie"], "ja")

    def test_zonder_tag_geen_chatmail(self):
        """⚑ r2 (Jordan C1, r1-regels 409-413): r1 keurde hier de succeszin zonder mail goed.
        Zonder tag gaat er niets weg, dus mag de bezoeker ook niet lezen dat het gemaild is."""
        uit = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING)
        self.assertNotEqual(uit, {"response": AFRONDING})
        self.assertEqual(uit, {"response": main.CHATMAIL_NIET_VERSTUURD})
        self.assertEqual([p for p in self.nep.hs_posts() if p["json"]["tags"] == ["nina-chatmail"]], [])
        self.assertEqual(self.logregels()[0]["chatmail"], "")
        self.assertFalse(self.logregels()[0]["artikelen"].endswith(main.CHATMAIL_LOGMARKERING))

    def test_tag_nooit_zichtbaar(self):
        uit = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " `[[CHATMAIL]]`")
        self.assertNotIn("CHATMAIL", uit["response"])


class T9EindpuntHelpScoutFaalt(Basis):
    # ⚑ r2 (contract 21-10 19:5x): r1 gebruikte 503; een 5xx is nu onzeker. 422 = zeker geweigerd.
    hs_status = 422

    def test_bezoeker_hoort_dat_het_niet_lukte(self):
        uit = chatbeurt(GESPREK[:-1], GESPREK[-1]["content"], AFRONDING + " [[CHATMAIL]]")
        self.assertEqual(uit, {"response": main.CHATMAIL_MISLUKT})
        log = [kw["json"] for m, u_, kw in self.nep.aanroepen if u_ == OMGEVING["NINA_LOG_URL"]]
        self.assertEqual(log[0]["chatmail"], "")


# ── 10. Instructie en lader ──────────────────────────────────────────────────
class T10Instructie(unittest.TestCase):
    def test_instructie_altijd_geladen_met_sandys_tekst(self):
        self.assertIn("[[CHATMAIL]]", main.BASE_KNOWLEDGE)
        self.assertIn(AANBOD, main.BASE_KNOWLEDGE)
        self.assertIn("Je krijgt ons gesprek per mail en daarna af en toe een mail van SanaYou YOGAcademy. "
                      "Afmelden kan altijd, via de link onderaan die mails.", main.BASE_KNOWLEDGE)

    def test_voorbeeldafronding_wordt_als_gedaan_herkend(self):
        """⚑ r2 (C1/F4): de zin uit de instructie is precies de succeszin van de server, en
        het systeem herkent hem als verzendclaim, zodat hij weggaat als er niets verstuurd is.
        (r1 gebruikte deze herkenning als bewijs van een eerdere mail; dat doet r2 niet meer.)"""
        pad = os.path.join(REPO, "knowledge", "instructies", "gesprek-mailen.md")
        with open(pad, encoding="utf-8") as f:
            tekst = f.read()
        m = re.search(r'"(Top, ik heb ons gesprek naar je gemaild\.) \[\[CHATMAIL\]\]"', tekst)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), main.CHATMAIL_AFRONDING)
        self.assertTrue(main._is_claimzin(m.group(1)))


class T11Constanten(unittest.TestCase):
    def test_registratieroute_letterlijk(self):
        """⚑ r3: brontag 454 en Hoofdlijst 80 staan in de websiteroute (en worden daar letterlijk
        getoetst). Hier: het adres van die route en het doelvoorvoegsel van de handtekening."""
        self.assertEqual(main.REGISTRATIE_URL, "https://sanayou.com/api/nina-registratie")
        self.assertEqual(main.REGISTRATIE_DOEL, "nina-registratie|v1")
        self.assertFalse(hasattr(main, "MAILBLUE_TAG_NINA_CHAT"))


class ZZVangnet(unittest.TestCase):
    def test_zz_geen_enkele_netwerkpoging(self):
        self.assertEqual(NETWERKPOGINGEN, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
