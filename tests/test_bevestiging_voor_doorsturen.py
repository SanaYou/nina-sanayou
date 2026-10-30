"""Deel B (Olivia/Sandy, 30-10-2026): een escalatie gaat pas naar Sandy na bevestiging van naam en e-mailadres.

Aanleiding: Rosita, gesprek 27-10 10:29. Zij gaf naam en e-mailadres, Nina vroeg "Even checken: ... klopt dat?",
Rosita antwoordde niet meer en er ging niets naar Sandy. Sandy wil dat de bezoeker in de bevestigingsvraag leest dat
het pas na bevestiging wordt doorgestuurd. Die zin mag het vangnet (escaleren zonder tag op woorden als
"doorgestuurd" of "neemt contact") niet laten afgaan: dan zou een onbevestigd adres alsnog een ticket worden.

Draaien: python3 -m unittest tests.test_bevestiging_voor_doorsturen
"""
import os
import re
import socket
import sys
import unittest

NETWERKPOGINGEN = []


def _geen_netwerk(*a, **k):
    NETWERKPOGINGEN.append(repr(a)[:120])
    raise OSError("netwerk geblokkeerd in test_bevestiging_voor_doorsturen")


socket.socket.connect = _geen_netwerk
socket.create_connection = _geen_netwerk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tests.test_opstart  # noqa: E402,F401  — nep-pakketten + importeert main van DEZE repo
import main  # noqa: E402


def u(t): return {"role": "user", "content": t}
def a(t): return {"role": "assistant", "content": t}


VRAAG = "Goedemorgen als ik de 70 uur yin online ga doen en ik wil door naar de 200 uur online word deze dan als korting verrekend?"
GEGEVENS = "Rosita rosita@voorbeeld.nl"
GESPREK_TOT_GEGEVENS = [
    u(VRAAG),
    a("Pakketkorting geldt alleen als je het volledige pakket in één keer aanschaft. Daarom stuur ik je vraag graag "
      "door naar Sandy. Mag ik je naam en e-mailadres?"),
    u(GEGEVENS),
]


class Bevestiging(unittest.TestCase):
    def escaleert(self, gesprek, antwoord, force):
        verstuurd = []
        origineel = main._send_escalation
        main._send_escalation = lambda name, email, summary, msgs: verstuurd.append((name, email)) or True
        try:
            main._detect_and_escalate(gesprek[-1]["content"], antwoord, gesprek, force=force)
        except Exception as e:  # een crash is een fout van de poort, geen geldige uitkomst
            self.fail(f"_detect_and_escalate gooide {type(e).__name__}: {e}")
        finally:
            main._send_escalation = origineel
        return verstuurd

    def test_vangnet_vuurt_niet_op_bevestigingsvraag_met_doorgestuurd(self):
        antwoord = ("Even checken: je naam is Rosita en je e-mailadres is rosita@voorbeeld.nl, klopt dat? "
                    "Pas als je dit bevestigt, wordt je vraag doorgestuurd naar Sandy.")
        self.assertEqual(self.escaleert(GESPREK_TOT_GEGEVENS, antwoord, force=False), [])

    def test_vangnet_vuurt_niet_op_bevestigingsvraag_met_neemt_contact(self):
        antwoord = ("Even checken: je naam is Rosita en je e-mailadres is rosita@voorbeeld.nl, klopt dat? "
                    "Sandy neemt contact met je op zodra je dit bevestigt.")
        self.assertEqual(self.escaleert(GESPREK_TOT_GEGEVENS, antwoord, force=False), [])

    def test_herhaalde_vraag_zonder_bevestiging_escaleert_niet(self):
        gesprek = GESPREK_TOT_GEGEVENS + [
            a("Even checken: je naam is Rosita en je e-mailadres is rosita@voorbeeld.nl, klopt dat? "
              "Pas als je dit bevestigt, stuur ik het door naar Sandy."),
            u("Hoe lang heb ik toegang tot de Basics?"),
        ]
        antwoord = ("Je hebt 24 maanden toegang. Even checken: je naam is Rosita en je e-mailadres is "
                    "rosita@voorbeeld.nl, klopt dat? Pas als je dit bevestigt, wordt je vraag doorgestuurd naar Sandy.")
        self.assertEqual(self.escaleert(gesprek, antwoord, force=False), [])

    def test_bevestigde_afronding_met_tag_escaleert_wel(self):
        gesprek = GESPREK_TOT_GEGEVENS + [
            a("Even checken: je naam is Rosita en je e-mailadres is rosita@voorbeeld.nl, klopt dat? "
              "Pas als je dit bevestigt, stuur ik het door naar Sandy."),
            u("Ja klopt"),
        ]
        verstuurd = self.escaleert(gesprek, "Top, ik heb het doorgestuurd naar Sandy. Ze neemt zo snel mogelijk "
                                            "contact met je op.", force=True)
        self.assertEqual(verstuurd, [("Rosita", "rosita@voorbeeld.nl")])

    def test_bevestigde_afronding_zonder_tag_vangnet_escaleert_wel(self):
        gesprek = GESPREK_TOT_GEGEVENS + [
            a("Even checken: je naam is Rosita en je e-mailadres is rosita@voorbeeld.nl, klopt dat? "
              "Pas als je dit bevestigt, stuur ik het door naar Sandy."),
            u("Ja klopt"),
        ]
        verstuurd = self.escaleert(gesprek, "Top, ik heb het doorgestuurd naar Sandy.", force=False)
        self.assertEqual(verstuurd, [("Rosita", "rosita@voorbeeld.nl")])

    def test_bevestigde_afronding_met_naam_en_mail_wordt_bezorgd(self):
        # Review Jordan 30-10, F1: "je naam en e-mailadres" in een afronding is geen vraag.
        gesprek = GESPREK_TOT_GEGEVENS + [
            a("Even checken: je naam is Rosita en je e-mailadres is rosita@voorbeeld.nl, klopt dat? "
              "Pas als je dit bevestigt, stuur ik het door naar Sandy."),
            u("Ja klopt"),
        ]
        verstuurd = self.escaleert(gesprek, "Dankjewel, ik heb je naam en e-mailadres doorgegeven aan Sandy. Ze neemt zo "
                                            "snel mogelijk contact met je op.", force=False)
        self.assertEqual(verstuurd, [("Rosita", "rosita@voorbeeld.nl")])

    def test_instructie_draagt_de_bevestigingszin(self):
        zin = "Pas als je dit bevestigt, stuur ik het door naar Sandy."
        norm = re.sub(r"\s+", " ", main.SYSTEM_PROMPT_BASE)
        met = norm.count("[e-mailadres], klopt dat? " + zin)
        zonder = norm.count("[e-mailadres], klopt dat?") - met
        # 6 = naam-email-verzamelen (flow 1, flow 2, bevestigingsregel), koopsignaal, gezondheid, consumentenprogramma's.
        # 1 = de chatmail-bevestiging in gesprek-mailen: die mail gaat naar de bezoeker zelf, niet naar Sandy.
        self.assertEqual((met, zonder), (6, 1))
        # De chatmail-bevestiging gaat naar de bezoeker zelf, niet naar Sandy: daar hoort de zin niet bij.
        self.assertNotIn("Sandy", main.CHATMAIL_BEVESTIGINGSVRAAG)

    def test_geen_netwerk(self):
        self.assertEqual(NETWERKPOGINGEN, [])


if __name__ == "__main__":
    unittest.main()
