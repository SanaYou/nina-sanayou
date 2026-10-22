"""Interne eenheden van chatmail r2 (Jared, 21-10-2026). Alleen voor r2: deze tests
gebruiken functies die r1 niet had. Gedrag tegen r1 staat in test_chatmail_r2.py.

Draaien vanuit de repo-wortel:  python3 -B -m unittest tests.test_chatmail_r2_intern -v
"""
import asyncio
import datetime as dt
import os
import socket
import sys
import types
import unittest
from unittest import mock

NETWERKPOGINGEN = []


def _geen_netwerk(*a, **k):
    NETWERKPOGINGEN.append(repr(a)[:120])
    raise OSError("netwerk geblokkeerd in test_chatmail_r2_intern")


socket.socket.connect = _geen_netwerk
socket.create_connection = _geen_netwerk

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import tests.test_opstart  # noqa: E402,F401
import main  # noqa: E402
import requests  # noqa: E402  — alleen de exceptieklassen
from tests.nep_claimdienst import NepClaimdienst, NepRequests, Antwoord, OMGEVING, sha, hm  # noqa: E402
from tests.test_chatmail_r2 import (  # noqa: E402
    GESPREK, AFRONDING, AANBOD, TOESTEMMING, SESSIE, NepAnthropic, u, a)

assert os.path.dirname(os.path.abspath(main.__file__)) == REPO, main.__file__
UTC = dt.timezone.utc


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
        for k in ("HELPSCOUT_APP_ID", "MAILBLUE_API_KEY"):
            if k not in self.omgeving:
                os.environ.pop(k, None)
        self._tag = mock.patch.object(main, "MAILBLUE_TAG_NINA_CHAT", 999)
        self._tag.start()

    def tearDown(self):
        main._requests = self._oud
        self._env.stop()
        self._tag.stop()


class DatumAmsterdam(unittest.TestCase):
    def test_datum_amsterdam_gelijk_aan_zoneinfo(self):
        from zoneinfo import ZoneInfo
        ams = ZoneInfo("Europe/Amsterdam")
        t, eind, n = dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2029, 1, 1, tzinfo=UTC), 0
        while t < eind:
            self.assertEqual(main._datum_amsterdam(t), t.astimezone(ams).strftime("%Y-%m-%d"), t)
            t += dt.timedelta(hours=1)
            n += 1
        # Rond middernacht op de wisseldagen, per minuut.
        for dag in ("2026-03-28", "2026-03-29", "2026-10-24", "2026-10-25", "2027-03-27", "2027-10-30"):
            basis = dt.datetime.fromisoformat(dag + "T21:00:00+00:00")
            for m in range(0, 4 * 60):
                t = basis + dt.timedelta(minutes=m)
                self.assertEqual(main._datum_amsterdam(t), t.astimezone(ams).strftime("%Y-%m-%d"), t)
        self.assertGreater(n, 35000)


class Sleutel(unittest.TestCase):
    def setUp(self):
        self._env = mock.patch.dict(os.environ, {"CLAIM_SECRET": OMGEVING["CLAIM_SECRET"]})
        self._env.start()

    def tearDown(self):
        self._env.stop()

    def test_chatmailsleutel(self):
        self.assertEqual(main._chatmail_sleutel("abc", " Petra@Voorbeeld.NL "),
                         hm("chatmail|abc|petra@voorbeeld.nl"))
        laat = dt.datetime(2026, 10, 21, 21, 59, tzinfo=UTC)   # 23:59 in Amsterdam
        middernacht = dt.datetime(2026, 10, 21, 22, 0, tzinfo=UTC)  # 00:00 op 22-10
        for leeg in (None, "", "   "):
            self.assertEqual(main._chatmail_sleutel(leeg, "petra@voorbeeld.nl", nu=laat),
                             hm("chatmail|geen-sessie|petra@voorbeeld.nl|2026-10-21"))
            self.assertEqual(main._chatmail_sleutel(leeg, "petra@voorbeeld.nl", nu=middernacht),
                             hm("chatmail|geen-sessie|petra@voorbeeld.nl|2026-10-22"))

    def test_zonder_secret_geen_sleutel_en_geen_bron(self):
        os.environ.pop("CLAIM_SECRET", None)
        with self.assertRaises(main.ClaimFout):
            main._chatmail_sleutel("abc", "petra@voorbeeld.nl")
        self.assertEqual(main._bron_hash(types.SimpleNamespace(headers={"x-real-ip": "198.51.100.2"})), "")


class Claimzin(unittest.TestCase):
    JA = [AFRONDING, "Ons gesprek is al eerder naar je gemaild.", "Ik heb het gesprek naar je verstuurd.",
          "De mail met ons gesprek is onderweg.", "Ons gesprek staat in je mailbox.",
          "Ik heb ons chatgesprek zojuist naar je toegestuurd!", "Het gesprek is gemaild."]
    NEE = ["Ik heb ons gesprek nog niet gemaild, maar kan dat doen.", "Het gesprek is niet gemaild.",
           "Dan krijg je ons gesprek gemaild.", "Ja, de lesreader krijg je gemaild na je aanmelding.",
           "Ik heb je vraag doorgestuurd naar Sandy.", "Ik heb het gesprek naar Sandy gestuurd.",
           "Zal ik dit gesprek even naar je mailen?", "Je krijgt ons gesprek per mail."]

    # r2.3: alleen in de mailbevestigingsfase herkend (buiten de fase begrensd, zie LEESMIJ).
    JA_FASE = ["Het gesprek is verzonden.", "Ik heb je de chat verstuurd.", "De chat is verzonden.",
               "Ik heb je ons gesprek gestuurd.", "De mail is verstuurd.", "Ik heb je het gesprek toegestuurd."]
    NEE_FASE = ["Ik heb het gesprek naar Sandy gestuurd.", "Ik heb je vraag doorgestuurd naar Sandy.",
                "Ik heb ons gesprek nog niet verstuurd.", "Dan krijg je ons gesprek gemaild.",
                "Zal ik dit gesprek even naar je mailen?", "Je krijgt ons gesprek per mail."]

    def test_is_claimzin_tabel(self):
        for z in self.JA + self.JA_FASE:
            with self.subTest("fase: " + z):
                self.assertTrue(main._is_claimzin(z, fase=True))
        for z in self.JA:
            with self.subTest(z):
                self.assertTrue(main._is_claimzin(z))
        for z in self.NEE + self.NEE_FASE:
            with self.subTest(z):
                self.assertFalse(main._is_claimzin(z))
        for z in self.NEE_FASE:
            with self.subTest("fase: " + z):
                self.assertFalse(main._is_claimzin(z, fase=True))

    def test_servertekst_zonder_bewijs_claimt_niets(self):
        vraag = main.CHATMAIL_BEVESTIGINGSVRAAG.format(naam="Petra", email="p@v.nl")
        for z in (main.CHATMAIL_MISLUKT, main.CHATMAIL_GEEN_ADRES, main.CHATMAIL_ONDERWEG,
                  main.CHATMAIL_LIMIET, main.CHATMAIL_NIET_VERSTUURD, vraag, AANBOD, TOESTEMMING):
            for fase in (False, True):
                with self.subTest(f"{z[:40]} fase={fase}"):
                    self.assertEqual(main._zonder_claimzinnen(z, fase=fase), (z, 0))

    def test_alinea_blijft_staan_zin_gaat_weg(self):
        t = "Fijn dat je het vroeg.\n\nTop, ik heb ons gesprek naar je gemaild. Fijne dag!"
        self.assertEqual(main._zonder_claimzinnen(t), ("Fijn dat je het vroeg.\n\nFijne dag!", 1))


class Adresstatus(unittest.TestCase):
    def status(self, g):
        s = main._adresstatus(g)
        return s["bevestigd"], s["nieuw"]

    def test_adresstatus_tabel(self):
        oud = ("Petra de Vries", "petra@voorbeeld.nl")
        vraag_nieuw = a("Even checken: je naam is Petra de Vries en je e-mailadres is nieuw@voorbeeld.nl, klopt dat?")
        gevallen = {
            "gewoon ja": (GESPREK, (oud, None)),
            "ja + nieuw adres": (GESPREK[:-1] + [u("Ja, stuur het naar nieuw@voorbeeld.nl")], (None, "nieuw@voorbeeld.nl")),
            "ja + zelfde adres": (GESPREK[:-1] + [u("Ja, petra@voorbeeld.nl")], (oud, None)),
            "correctie na ja": (GESPREK + [a("Fijn."), u("Toch liever nieuw@voorbeeld.nl")], (None, "nieuw@voorbeeld.nl")),
            "nee over adres na ja": (GESPREK + [a("Fijn."), u("Nee, niet naar dat mailadres")], (None, None)),
            "nee zonder adres na ja": (GESPREK + [a("Fijn."), u("Nee, verder geen vragen")], (oud, None)),
            "nieuwe vraag zonder antwoord": (GESPREK + [vraag_nieuw], (None, None)),
            "nieuwe vraag + ja": (GESPREK + [vraag_nieuw, u("ja")], (("Petra de Vries", "nieuw@voorbeeld.nl"), None)),
            "twee adressen in vraag": (GESPREK[:-2] + [a("Even checken: a@x.nl of b@y.nl, klopt dat?"), u("ja")], (None, None)),
            "Nina noemt ander adres": (GESPREK + [a("Ik mail het naar ander@voorbeeld.nl.")], (None, None)),
        }
        for naam, (g, verwacht) in gevallen.items():
            with self.subTest(naam):
                self.assertEqual(self.status(g), verwacht)


class Bron(unittest.TestCase):
    def test_bron_hash(self):
        ns = types.SimpleNamespace
        with mock.patch.dict(os.environ, {"CLAIM_SECRET": OMGEVING["CLAIM_SECRET"]}):
            b = main._bron_hash(ns(headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1"}))
            self.assertEqual(b, hm("203.0.113.7"))
            self.assertNotEqual(b, sha("203.0.113.7"))
            self.assertEqual(main._bron_hash(ns(headers={"x-real-ip": "198.51.100.2"})), hm("198.51.100.2"))
        self.assertEqual(main._bron_hash(ns(headers={})), "")
        self.assertEqual(main._bron_hash(None), "")


class ChatMetHttpRequest(Basis):
    def test_chat_met_http_request_geeft_bron(self):
        NepAnthropic.antwoord, NepAnthropic.taalcheck_stript = AFRONDING + " [[CHATMAIL]]", True
        verzoek = types.SimpleNamespace(message="Ja, klopt!", session_id=SESSIE,
                                        history=[types.SimpleNamespace(**m) for m in GESPREK[:-1]])
        http = types.SimpleNamespace(headers={"x-forwarded-for": "203.0.113.7"})
        with mock.patch.object(main.anthropic, "Anthropic", NepAnthropic):
            resp = asyncio.run(main.chat(verzoek, http))["response"]
        self.assertEqual(resp, AFRONDING)
        self.assertEqual([c["bron"] for c in self.claim.claims()], [hm("203.0.113.7")])

    def test_tags_gaan_niet_naar_de_taalcheck(self):
        gezien = []

        class Kijker(NepAnthropic):
            def create(self, model="", messages=None, **kw):
                if model.startswith("claude-haiku"):
                    gezien.append(messages[0]["content"].split("Tekst:\n", 1)[1])
                return super().create(model=model, messages=messages, **kw)

        Kijker.antwoord = AFRONDING + " [[ESCALATIE]] [[CHATMAIL]]"
        verzoek = types.SimpleNamespace(message="Ja, klopt!", session_id=SESSIE,
                                        history=[types.SimpleNamespace(**m) for m in GESPREK[:-1]])
        with mock.patch.object(main.anthropic, "Anthropic", Kijker):
            asyncio.run(main.chat(verzoek))
        self.assertEqual(len(gezien), 1)
        self.assertNotIn("[[", gezien[0])


class StuurChatmail(Basis):
    def post_met(self, gedrag):
        def post(url, **kw):
            if url.endswith("/oauth2/token"):
                return Antwoord(200, {"access_token": "t", "expires_in": 7200})
            if isinstance(gedrag, Exception):
                raise gedrag
            return gedrag
        return types.SimpleNamespace(post=post, get=post)

    def test_stuur_chatmail_classificatie(self):
        onzeker = {"connect-timeout": requests.exceptions.ConnectTimeout("x"),
                   "read-timeout": requests.exceptions.ReadTimeout("x"),
                   "verbindingsfout": requests.exceptions.ConnectionError("x"),
                   "302": Antwoord(302, {}), "500": Antwoord(500, {}), "502": Antwoord(502, {}),
                   "503": Antwoord(503, {}), "504": Antwoord(504, {})}
        zeker = {"400": Antwoord(400, {}), "404": Antwoord(404, {}), "422": Antwoord(422, {}),
                 "499": Antwoord(499, {})}
        for naam, g in onzeker.items():
            with self.subTest(naam):
                main._requests = self.post_met(g)
                main._hs_token_cache.update(access_token=None, expires_at=0)
                with self.assertRaises(main.OnzekereVerzending):
                    main._stuur_chatmail("Petra", "p@v.nl", GESPREK)
        for naam, g in zeker.items():
            with self.subTest(naam):
                main._requests = self.post_met(g)
                with self.assertRaises(RuntimeError):
                    main._stuur_chatmail("Petra", "p@v.nl", GESPREK)
        main._requests = self.post_met(Antwoord(201, {}, {"Resource-Id": "geen-getal"}))
        self.assertEqual(main._stuur_chatmail("Petra", "p@v.nl", GESPREK), 0)

    def test_tokenfout_is_zeker_mislukt(self):
        def post(url, **kw):
            raise requests.exceptions.ReadTimeout("token")
        main._requests = types.SimpleNamespace(post=post, get=post)
        with self.assertRaises(requests.exceptions.ReadTimeout):
            main._stuur_chatmail("Petra", "p@v.nl", GESPREK)


class Claimdienst(Basis):
    def met(self, antwoord):
        main._requests = types.SimpleNamespace(post=lambda url, **kw: antwoord)

    def test_claimdienst_validatie(self):
        self.met(Antwoord(200, {"ok": True, "uitkomst": "geclaimd"}))
        self.assertEqual(main._claimdienst({"actie": "claim"})["uitkomst"], "geclaimd")
        fout = {"ok als tekst": Antwoord(200, {"ok": "true"}), "lijst": Antwoord(200, [1]),
                "geen json": Antwoord(200, None, geen_json=True), "302": Antwoord(302, {"ok": True}),
                "500": Antwoord(500, {"ok": True}), "ok false": Antwoord(200, {"ok": False, "error": "lock"})}
        for naam, antw in fout.items():
            with self.subTest(naam):
                self.met(antw)
                with self.assertRaises(main.ClaimFout):
                    main._claimdienst({"actie": "claim"})

    def test_secret_gaat_mee_en_nooit_in_de_uitslag(self):
        """Wat er de deur uit gaat, los van wat de dienst terugzegt: het secret zit erin."""
        verstuurd = []

        def post(url, **kw):
            verstuurd.append((url, kw))
            return Antwoord(200, {"ok": True, "uitkomst": "geclaimd"})
        main._requests = types.SimpleNamespace(post=post)
        data = main._claimdienst({"actie": "claim", "soort": "chatmail", "sleutel": "s", "ontvanger": "o", "bron": ""})
        self.assertEqual(len(verstuurd), 1)
        self.assertEqual(verstuurd[0][0], OMGEVING["CLAIM_URL"])
        self.assertEqual(verstuurd[0][1]["json"].get("secret"), OMGEVING["CLAIM_SECRET"])
        self.assertNotIn(OMGEVING["CLAIM_SECRET"], repr(data))


class NaDeVerzending(Basis):
    def test_bevestig_faalt_nooit_naar_buiten(self):
        echt = self.claim.verwerk

        def bevestig_faalt(body):
            if (body or {}).get("actie") == "bevestig":
                return 200, {"ok": False, "error": "lock"}
            return echt(body)
        self.claim.verwerk = bevestig_faalt
        uit = main._verwerk_chatmail(GESPREK, AFRONDING, session_id="b1")
        self.assertTrue(uit["verstuurd"])
        self.assertEqual(uit["antwoord"], AFRONDING)
        self.assertIs(uit["bevestigd"], False)
        # De rij blijft `bezig`: een herhaling verstuurt niets.
        main._verwerk_chatmail(GESPREK, AFRONDING, session_id="b1")
        self.assertEqual(len(self.nep.chatmail_posts()), 1)

    def test_bevestig_fout_raakt_bezoeker_niet(self):
        """Na een geslaagde mail faalt `bevestig`: de bezoeker ziet gewoon de succeszin."""
        echt = self.claim.verwerk

        def bevestig_faalt(body):
            if (body or {}).get("actie") == "bevestig":
                return 200, {"ok": False, "error": "lock"}
            return echt(body)
        self.claim.verwerk = bevestig_faalt
        NepAnthropic.antwoord, NepAnthropic.taalcheck_stript = AFRONDING + " [[CHATMAIL]]", True
        verzoek = types.SimpleNamespace(message="Ja, klopt!", session_id="b2",
                                        history=[types.SimpleNamespace(**m) for m in GESPREK[:-1]])
        # Echte exceptieklassen op de nep-anthropic, zoals het echte pakket ze heeft: dan
        # vangt de vangnet-`except Exception` van /chat een fout zoals in productie.
        fouten = {n: type(n, (Exception,), {}) for n in
                  ("RateLimitError", "APIStatusError", "APITimeoutError", "AuthenticationError")}
        with mock.patch.object(main.anthropic, "Anthropic", NepAnthropic), \
                mock.patch.multiple(main.anthropic, **fouten):
            uit = asyncio.run(main.chat(verzoek))
        self.assertEqual(uit, {"response": AFRONDING})
        self.assertEqual(len(self.nep.chatmail_posts()), 1)

    def test_onleesbaar_kenmerk_blijft_verstuurd(self):
        """Help Scout gaf 201 maar een vreemd Resource-Id: de mail is weg, dus `verstuurd`."""
        self.nep.hs_resource_id = "geen-getal"
        uit = main._verwerk_chatmail(GESPREK, AFRONDING, session_id="k1")
        self.assertTrue(uit["verstuurd"], uit)
        self.assertEqual(uit["antwoord"], AFRONDING)
        self.assertEqual([(b["status"], b["kenmerk"]) for b in self.claim.bevestigingen()], [("verstuurd", "")])

    def test_mailblue_fout_registratie_mislukt(self):
        self.nep.mb_sync_status = 500
        uit = main._verwerk_chatmail(GESPREK, AFRONDING, session_id="m1")
        self.assertTrue(uit["verstuurd"])
        self.assertEqual([(b["status"], b["registratie"]) for b in self.claim.bevestigingen()],
                         [("verstuurd", "mislukt")])

    def test_tagbeurt_servertekst_en_escalatie_houdt_rest(self):
        """r2.3: gewone chatmailbeurt = alleen de serverzin; met escalatie blijven de
        modelzinnen zonder verzendclaim staan, met de serverzin erachter."""
        uit = main._verwerk_chatmail(GESPREK, "Top!", session_id="s2")
        self.assertTrue(uit["verstuurd"])
        self.assertEqual(uit["antwoord"], main.CHATMAIL_AFRONDING)
        uit = main._verwerk_chatmail(GESPREK, "Ik heb je vraag doorgestuurd naar Sandy. Het gesprek is verzonden.",
                                     session_id="s3", modeltekst_houden=True)
        self.assertEqual(uit["antwoord"], "Ik heb je vraag doorgestuurd naar Sandy.\n\n" + main.CHATMAIL_AFRONDING)


class ZonderMailBlueSleutel(Basis):
    omgeving = {k: v for k, v in OMGEVING.items() if k != "MAILBLUE_API_KEY"}

    def test_mailblue_overgeslagen_registratie_leeg(self):
        uit = main._verwerk_chatmail(GESPREK, AFRONDING, session_id="m2")
        self.assertTrue(uit["verstuurd"])
        self.assertEqual([(b["status"], b["registratie"]) for b in self.claim.bevestigingen()],
                         [("verstuurd", "")])


class ZonderHelpScout(Basis):
    omgeving = {k: v for k, v in OMGEVING.items() if k != "HELPSCOUT_APP_ID"}

    def test_help_scout_niet_ingesteld_geen_claim(self):
        uit = main._verwerk_chatmail(GESPREK, AFRONDING, session_id="h1")
        self.assertFalse(uit["verstuurd"])
        self.assertEqual(uit["antwoord"], main.CHATMAIL_MISLUKT)
        self.assertEqual(self.nep.aanroepen, [])


class ZZVangnet(unittest.TestCase):
    def test_zz_geen_enkele_netwerkpoging(self):
        self.assertEqual(NETWERKPOGINGEN, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
