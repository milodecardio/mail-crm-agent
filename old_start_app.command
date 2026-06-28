#!/bin/bash
# Startet das mail-crm-agent Dashboard. Doppelklickbar im Finder.

# In den Ordner DIESES Scripts wechseln (egal wo der Agent liegt) — behebt den
# früheren Fehler 'cd ~/ai-agent', der ins Leere lief.
cd "$(dirname "$0")" || exit 1

# Abhängigkeiten sicherstellen (still, einmalig schnell)
python3 -m pip install -q -r requirements.txt 2>/dev/null

# App starten — sie wählt selbst einen freien Port (Default 5050) und öffnet den Browser.
python3 app.py
