#!/bin/bash

# 🔴 Alle Prozesse auf Port 5050 beenden
PID=$(lsof -t -i:5050)

if [ ! -z "$PID" ]; then
    echo "Beende alten Prozess auf Port 5050..."
    kill -9 $PID
fi

# 📁 In Projektordner wechseln
cd "$(dirname "$0")"

# 🔑 Anthropic API Key aus macOS Keychain laden
export ANTHROPIC_API_KEY=$(security find-generic-password -a "anthropic" -s "ANTHROPIC_API_KEY" -w 2>/dev/null)

# 🚀 App im Hintergrund starten, Ausgabe in Log-Datei umleiten
# (verhindert, dass Automator/Shell-Skript-Aktion auf Prozess-Ende wartet)
nohup "$(dirname "$0")/venv/bin/python3" app.py > "$HOME/mail_crm_agent.log" 2>&1 &
disown

# ⏳ kurz warten, bis der Server hochgefahren ist
sleep 2

# 🌐 Browser öffnen
open http://127.0.0.1:5050
