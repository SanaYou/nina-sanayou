"""Opstartproef: voert de HELE module-body van main.py uit met nagemaakte externe
pakketten (fastapi, anthropic, pydantic, dotenv hoeven niet geïnstalleerd te zijn).

⚑ Waarom dit bestaat — 31-8-2026. Bij het repareren van de zoeklaag stond het blok dat
WOORDGEWICHTEN berekent per ongeluk vóór de definitie van normalize(). Op module-niveau
is dat een NameError: Nina zou niet meer opstarten. De meetlat in meet_zoeklaag.py zag
dat NIET, want die licht losse functies met ast uit het bestand en voert ze in eigen
volgorde uit — precies de volgorde-informatie die je wilt toetsen gooit hij weg.
Gemeten bewijs: met de foute volgorde gaf deze proef "NameError: name 'normalize' is
not defined", met de goede volgorde 190 artikelen en 3747 woordgewichten.

Les: een testopstelling die code herschikt, kan geen volgordefouten vangen. Draai er
altijd één proef naast die het bestand uitvoert zoals het is.

Draaien vanuit de repo-wortel:  python3 tests/test_opstart.py
"""
import sys, types, os

def nep(naam, attrs=()):
    m = types.ModuleType(naam)
    for a in attrs:
        setattr(m, a, type(a, (), {"__init__": lambda self,*a,**k: None,
                                   "__call__": lambda self,*a,**k: None}))
    m.__getattr__ = lambda n: (lambda *a, **k: None)
    sys.modules[naam] = m
    return m

class Alles:
    def __init__(self,*a,**k): pass
    def __call__(self,*a,**k): return self
    def __getattr__(self,n): return Alles()
    def add_middleware(self,*a,**k): pass
    def mount(self,*a,**k): pass
    def get(self,*a,**k): return lambda f: f
    def post(self,*a,**k): return lambda f: f

for naam in ["fastapi","fastapi.middleware","fastapi.middleware.cors",
             "fastapi.staticfiles","fastapi.responses","pydantic","anthropic","dotenv"]:
    m = types.ModuleType(naam); m.__getattr__ = lambda n: Alles
    sys.modules[naam] = m
sys.modules["fastapi"].FastAPI = Alles
sys.modules["fastapi.middleware.cors"].CORSMiddleware = Alles
sys.modules["fastapi.staticfiles"].StaticFiles = Alles
sys.modules["fastapi.responses"].FileResponse = Alles
sys.modules["pydantic"].BaseModel = Alles
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None
sys.modules["anthropic"].Anthropic = Alles

os.chdir("/Users/skarsten/nina-chatbot")
sys.path.insert(0, "/Users/skarsten/nina-chatbot")
import main
print("✅ module-body volledig uitgevoerd — geen NameError")
print("   artikelen:", len(main.ARTICLES_INDEX), "| woordgewichten:", len(main.WOORDGEWICHTEN))
_t, titels = main.retrieve_articles("wat leer ik precies in de module Yoga Nidra?", [])
print("   top3:", titels)
_t, titels = main.retrieve_articles("kun je me vertellen wat ik leer tijdens Yin Yoga 2?", [])
print("   top3:", titels)
