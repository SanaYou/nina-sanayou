"""Registratie van een chatmail via de website (r3, route B van Tonny, 22-10-2026).

Nina houdt geen MailBlue-sleutel. Na een geslaagde chatmail roept zij de websiteroute
/api/nina-registratie aan met alleen naam en adres, ondertekend met CLAIM_SECRET. De
website zet brontag 454 en Hoofdlijst 80 (getoetst in sanayou-pages
test/nina-registratie-api.test.mjs). Hier: wat Nina stuurt, wanneer, en wat er gebeurt als
de website iets anders dan 200 + ok geeft (registratie: mislukt, geen tweede mail).

`NepWebsite` controleert de handtekening zelf (onafhankelijk nagebouwd) en weigert een
verkeerde; elke toets eist daarom ook `geweigerd == []` waar een geldige aanroep hoort.

Draaien vanuit de repo-wortel:  python3 -B -m unittest tests.test_registratie_website -v
"""
import os
import socket
import sys
import unittest
from unittest import mock

NETWERKPOGINGEN = []


def _geen_netwerk(*a, **k):
    NETWERKPOGINGEN.append(repr(a)[:120])
    raise OSError("netwerk geblokkeerd in test_registratie_website")


socket.socket.connect = _geen_netwerk
socket.create_connection = _geen_netwerk

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import tests.test_opstart  # noqa: E402,F401
import main  # noqa: E402
from tests.nep_claimdienst import (  # noqa: E402
    NepClaimdienst, NepRequests, NepWebsite, OMGEVING, REGISTRATIE_URL, VECTOR, WEBSITE_UITKOMST,
    registratie_mac, hm)
from tests.test_chatmail_r2 import GESPREK, AFRONDING, u, a  # noqa: E402

assert os.path.dirname(os.path.abspath(main.__file__)) == REPO, main.__file__


class Basis(unittest.TestCase):
    omgeving = OMGEVING

    def setUp(self):
        self.claim = NepClaimdienst()
        self.site = NepWebsite()
        self.nep = NepRequests(claimdienst=self.claim, website=self.site)
        self._oud = main._requests
        main._requests = self.nep
        main._hs_token_cache.update(access_token=None, expires_at=0)
        self._env = mock.patch.dict(os.environ, self.omgeving, clear=False)
        self._env.start()
        os.environ.pop("MAILBLUE_API_KEY", None)

    def tearDown(self):
        main._requests = self._oud
        self._env.stop()

    def bevestigd(self):
        return [(b["status"], b["registratie"]) for b in self.claim.bevestigingen()]

    def website_faalt(self, status, body):
        self.site.status, self.site.body = status, body
        uit = main._verwerk_chatmail(GESPREK, AFRONDING, session_id="f1")
        self.assertTrue(uit["verstuurd"], uit)
        self.assertEqual(uit["antwoord"], AFRONDING)
        self.assertTrue(uit["mailblue"].startswith("mislukt"), uit["mailblue"])
        self.assertEqual(self.bevestigd(), [("verstuurd", "mislukt")])
        self.assertEqual(len(self.nep.chatmail_posts()), 1)
        self.assertEqual(len(self.nep.registraties()), 1)  # geen tweede poging
        return uit


class Contract(unittest.TestCase):
    def test_testvector_gelijk_aan_website(self):
        """Zelfde vector als test/nina-registratie-api.test.mjs: verandert de vorm aan één kant, dan is één rood."""
        self.assertEqual(main._registratie_handtekening(VECTOR["geheim"], VECTOR["ts"], VECTOR["email"], VECTOR["naam"]),
                         VECTOR["mac"])
        self.assertEqual(registratie_mac(VECTOR["geheim"], VECTOR["ts"], VECTOR["email"], VECTOR["naam"]), VECTOR["mac"])

    def test_route_en_wachtgrens_letterlijk(self):
        self.assertEqual(main.REGISTRATIE_URL, "https://sanayou.com/api/nina-registratie")
        self.assertEqual(REGISTRATIE_URL, main.REGISTRATIE_URL)
        self.assertEqual(main.REGISTRATIE_TIMEOUT, (5, 12))


class Geslaagd(Basis):
    def test_een_aanroep_met_alleen_naam_adres_en_handtekening(self):
        uit = main._verwerk_chatmail(GESPREK, AFRONDING, session_id="g1")
        self.assertTrue(uit["verstuurd"], uit)
        regs = self.nep.registraties()
        self.assertEqual(len(regs), 1)
        methode, kw = regs[0]
        self.assertEqual(methode, "POST")
        self.assertEqual(sorted(kw["json"]), ["email", "mac", "naam", "ts"])  # geen transcript, geen tag of lijst
        self.assertEqual((kw["json"]["naam"], kw["json"]["email"]), ("Petra de Vries", "petra@voorbeeld.nl"))
        self.assertIs(kw.get("allow_redirects"), False)
        self.assertEqual(kw.get("timeout"), (5, 12))
        self.assertEqual(self.site.geweigerd, [])
        self.assertEqual(uit["mailblue"], WEBSITE_UITKOMST)
        self.assertEqual(self.bevestigd(), [("verstuurd", "ok")])

    def test_nooit_rechtstreeks_naar_mailblue_ook_niet_met_sleutel(self):
        os.environ["MAILBLUE_API_KEY"] = "nep-mb"
        main._verwerk_chatmail(GESPREK, AFRONDING, session_id="g2")
        self.assertEqual([u_ for m, u_, kw in self.nep.aanroepen if "/api/3" in u_ or "api-us1" in u_], [])
        self.assertEqual(len(self.nep.registraties()), 1)

    def test_mail_eerst_dan_registratie_dan_bevestig(self):
        main._verwerk_chatmail(GESPREK, AFRONDING, session_id="g3")
        soort = []
        for m, u_, kw in self.nep.aanroepen:
            if u_.endswith("/v2/conversations"):
                soort.append("mail")
            elif u_ == REGISTRATIE_URL:
                soort.append("registratie")
            elif (kw.get("json") or {}).get("actie") == "bevestig":
                soort.append("bevestig")
        self.assertEqual(soort, ["mail", "registratie", "bevestig"])

    def test_naam_met_extra_spaties_gaat_genormaliseerd_en_geldig_ondertekend(self):
        g = [m if "klopt dat?" not in m["content"] else
             a("Even checken: je naam is Petra  de   Vries en je e-mailadres is petra@voorbeeld.nl, klopt dat?")
             for m in GESPREK]
        uit = main._verwerk_chatmail(g, AFRONDING, session_id="g4")
        self.assertTrue(uit["verstuurd"], uit)
        self.assertEqual(self.nep.registraties()[0][1]["json"]["naam"], "Petra de Vries")
        self.assertEqual(self.site.geweigerd, [])


class Herhaling(Basis):
    def test_tweede_vraag_zelfde_sessie_geen_mail_geen_registratie(self):
        main._verwerk_chatmail(GESPREK, AFRONDING, session_id="h1")
        uit = main._verwerk_chatmail(GESPREK + [a(AFRONDING), u("Kun je het nog een keer mailen?")], AFRONDING,
                                     session_id="h1")
        self.assertEqual(uit["reden"], "claimdienst: al_verstuurd")
        self.assertEqual(len(self.nep.chatmail_posts()), 1)
        self.assertEqual(len(self.nep.registraties()), 1)

    def test_na_mislukte_registratie_geen_tweede_mail_en_geen_tweede_registratie(self):
        self.site.status, self.site.body = 502, {"ok": False, "fout": "MailBlue: sync 500"}
        main._verwerk_chatmail(GESPREK, AFRONDING, session_id="h2")
        self.site.status, self.site.body = 200, {"ok": True, "uitkomst": WEBSITE_UITKOMST}
        uit = main._verwerk_chatmail(GESPREK + [a(AFRONDING), u("Mail je het nog een keer?")], AFRONDING,
                                     session_id="h2")
        self.assertEqual(uit["reden"], "claimdienst: al_verstuurd")
        self.assertEqual(len(self.nep.chatmail_posts()), 1)
        self.assertEqual(len(self.nep.registraties()), 1)
        # De fout blijft zichtbaar in de claimrij (de onzeker-controle van 08:30 leest die).
        rij = self.claim.rij(hm("chatmail|h2|petra@voorbeeld.nl"))
        self.assertIsNotNone(rij)
        self.assertEqual((rij["status"], rij["registratie"]), ("verstuurd", "mislukt"))


class WebsiteFaalt(Basis):
    def test_502_mailblue_fout(self):
        self.website_faalt(502, {"ok": False, "fout": "MailBlue: sync 500"})

    def test_503_configuratie(self):
        self.website_faalt(503, {"ok": False, "fout": "configuratie ontbreekt"})

    def test_404_route_nog_niet_live(self):
        self.website_faalt(404, {"error": "not found"})

    def test_308_doorverwijzing_wordt_niet_gevolgd(self):
        self.website_faalt(308, {})

    def test_ok_true_maar_geen_200(self):
        self.website_faalt(500, {"ok": True, "uitkomst": "x"})

    def test_200_maar_ok_niet_true(self):
        self.website_faalt(200, {"ok": False})

    def test_200_met_ok_als_tekst(self):
        self.website_faalt(200, {"ok": "true", "uitkomst": "x"})

    def test_200_zonder_json(self):
        self.site.modus = "geen_json"
        self.website_faalt(200, None)

    def test_time_out(self):
        self.site.modus = "timeout"
        self.website_faalt(200, {})

    def test_verkeerde_handtekening_wordt_geweigerd_en_mislukt(self):
        self.site.secret = "ander-geheim"
        self.website_faalt(200, {"ok": True, "uitkomst": WEBSITE_UITKOMST})
        self.assertEqual(self.site.geweigerd, ["handtekening"])


class ZonderClaimgeheim(Basis):
    def test_registreren_zonder_geheim_gooit_en_roept_niets_aan(self):
        os.environ.pop("CLAIM_SECRET", None)
        with self.assertRaises(Exception) as cm:
            main._chatmail_naar_mailblue("Petra de Vries", "petra@voorbeeld.nl")
        self.assertIn("CLAIM_SECRET ontbreekt", str(cm.exception))
        self.assertEqual(self.nep.aanroepen, [])


class ZZVangnet(unittest.TestCase):
    def test_zz_geen_enkele_netwerkpoging(self):
        self.assertEqual(NETWERKPOGINGEN, [])


if __name__ == "__main__":
    unittest.main()
