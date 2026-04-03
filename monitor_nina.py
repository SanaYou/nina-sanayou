"""
Nina Monitor: test elke X minuten of Nina werkt en stuur Telegram-alert bij problemen.
Gebruik: python3 monitor_nina.py
"""
import os
import requests
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("nina-monitor")

NINA_URL = "https://nina-sanayou.onrender.com"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Voorkom alert-spam: max 1 alert per uur
_last_alert_time = 0
ALERT_COOLDOWN = 3600  # seconden


def send_telegram(message):
    """Stuur een Telegram-bericht naar Sandy."""
    global _last_alert_time
    now = time.time()
    if now - _last_alert_time < ALERT_COOLDOWN:
        logger.info("Alert overgeslagen (cooldown actief)")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        _last_alert_time = now
        logger.info("Telegram-alert verstuurd")
    except Exception as e:
        logger.error(f"Telegram-alert mislukt: {e}")


def check_nina():
    """Test of Nina reageert op een vraag."""
    # Stap 1: health check
    try:
        r = requests.get(f"{NINA_URL}/health", timeout=15)
        if r.status_code != 200:
            return False, f"Health check mislukt (status {r.status_code})"
    except Exception as e:
        return False, f"Nina is niet bereikbaar: {e}"

    # Stap 2: echte vraag stellen (max 2 pogingen bij lege response / cold start)
    for attempt in range(2):
        try:
            r = requests.post(
                f"{NINA_URL}/chat",
                json={"message": "Welke opleidingen bieden jullie aan?", "history": []},
                timeout=90,
            )

            # Lege response afvangen voordat we JSON parsen
            if not r.text or not r.text.strip():
                if attempt == 0:
                    logger.warning(f"Lege response (HTTP {r.status_code}), retry na 5s (cold start?)...")
                    time.sleep(5)
                    continue
                return False, f"Lege response van Render (HTTP {r.status_code})"

            try:
                data = r.json()
            except ValueError:
                if attempt == 0:
                    logger.warning(f"Geen geldige JSON (HTTP {r.status_code}), retry na 5s...")
                    time.sleep(5)
                    continue
                return False, f"Ongeldige JSON response (HTTP {r.status_code}): {r.text[:100]}"

            if "error" in data:
                return False, f"Nina geeft foutmelding: {data['error']}"

            antwoord = data.get("response", "")
            if not antwoord:
                return False, "Nina geeft een leeg antwoord"

            return True, antwoord[:80]

        except requests.exceptions.Timeout:
            return False, "Nina timeout na 90 seconden"
        except Exception as e:
            return False, f"Onverwachte fout: {e}"

    return False, "Alle pogingen mislukt"


def run_check():
    """Voer een check uit en stuur alert als er iets mis is."""
    ok, detail = check_nina()
    if ok:
        logger.info(f"Nina OK: {detail}...")
    else:
        logger.warning(f"Nina FOUT: {detail}")
        send_telegram(
            f"⚠️ <b>Nina-alert</b>\n\n"
            f"Nina reageert niet goed op vragen.\n\n"
            f"<b>Probleem:</b> {detail}\n\n"
            f"Laat dit even checken door Claude Code."
        )


if __name__ == "__main__":
    run_check()
