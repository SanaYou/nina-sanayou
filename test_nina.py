"""
Test Nina: stuur 3 vragen achter elkaar en check of ze allemaal een antwoord geeft.
Gebruik: python3 test_nina.py [url]
Standaard test tegen https://nina-sanayou.onrender.com
"""
import sys
import time
import requests

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "https://nina-sanayou.onrender.com"
CHAT_URL = f"{BASE_URL}/chat"

VRAGEN = [
    "Wat voor opleidingen bieden jullie aan?",
    "Wat kost de yin yoga opleiding?",
    "Kan ik ook in termijnen betalen?",
]

def test_nina():
    history = []
    fouten = []

    # Stap 0: health check
    print(f"🔗 Testen tegen: {BASE_URL}")
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=30)
        if r.status_code != 200:
            print(f"❌ Health check mislukt (status {r.status_code})")
            return False
        print("✅ Health check OK\n")
    except Exception as e:
        print(f"❌ Nina is niet bereikbaar: {e}")
        return False

    # Stap 1-3: drie vragen achter elkaar (zoals een echte bezoeker)
    for i, vraag in enumerate(VRAGEN, 1):
        print(f"📨 Vraag {i}: {vraag}")
        try:
            r = requests.post(CHAT_URL, json={"message": vraag, "history": history}, timeout=90)
            data = r.json()

            if "error" in data:
                print(f"❌ Fout: {data['error']}\n")
                fouten.append(f"Vraag {i}: {data['error']}")
                continue

            antwoord = data.get("response", "")
            if not antwoord:
                print(f"❌ Leeg antwoord\n")
                fouten.append(f"Vraag {i}: leeg antwoord")
                continue

            # Toon eerste 120 tekens van het antwoord
            print(f"✅ Antwoord: {antwoord[:120]}{'...' if len(antwoord) > 120 else ''}\n")

            # Voeg toe aan history (zoals de widget doet)
            history.append({"role": "user", "content": vraag})
            history.append({"role": "assistant", "content": antwoord})

        except requests.exceptions.Timeout:
            print(f"❌ Timeout na 90 seconden\n")
            fouten.append(f"Vraag {i}: timeout")
        except Exception as e:
            print(f"❌ Fout: {e}\n")
            fouten.append(f"Vraag {i}: {e}")

        # Pauze tussen vragen (realistisch: bezoeker leest antwoord + typt)
        if i < len(VRAGEN):
            time.sleep(10)

    # Resultaat
    print("=" * 50)
    if fouten:
        print(f"❌ MISLUKT — {len(fouten)} van {len(VRAGEN)} vragen faalden:")
        for f in fouten:
            print(f"   • {f}")
        return False
    else:
        print(f"✅ GESLAAGD — alle {len(VRAGEN)} vragen beantwoord")
        return True


if __name__ == "__main__":
    ok = test_nina()
    sys.exit(0 if ok else 1)
