# Jordans tegenproef uit zijn closing op chatmail r2 (team-log 2026-10-21-closing-nina-chatmail-r2-niet-af-jordan.md,
# § "Reproduceerbare eigen proeven"), letterlijk overgenomen door Jared (r2.3). Alleen deze kopregels zijn toegevoegd.
# Op de r2.2-kandidaat (main.py b08d490a…): 2 FAIL, 0 ERROR. Op r2.3: groen.
import os, unittest
from unittest import mock
from tests.test_chatmail_r2 import main, NepRequests, OMGEVING, GESPREK, chatbeurt

class Jordan(unittest.TestCase):
    def controleer(self, zin):
        nep = NepRequests()
        main._hs_token_cache.update(access_token=None, expires_at=0)
        with mock.patch.dict(os.environ, OMGEVING), mock.patch.object(main, '_requests', nep):
            uit = chatbeurt(GESPREK[:-1], GESPREK[-1]['content'], zin)
        self.assertEqual(len(nep.hs_posts()), 0)
        print(repr(zin), repr(uit), 'HS_POSTS', len(nep.hs_posts()))
        self.assertNotEqual(uit, zin, 'onbewezen verzendclaim bleef staan')

    def test_gesprek_is_verzonden(self):
        self.controleer('Het gesprek is verzonden.')

    def test_chat_verstuurd(self):
        self.controleer('Ik heb je de chat verstuurd.')

if __name__ == '__main__':
    unittest.main(verbosity=2)
