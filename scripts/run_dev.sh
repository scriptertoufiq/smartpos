#!/usr/bin/env bash
# Start SmartPOS against whatever database .env points at.
#
# If DB_HOST is a MySQL you already run (Herd, DBngin, a local install), this
# just launches the app. If nothing answers and docker-compose.yml is present,
# it offers to start the container instead.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "No .env found - copying .env.example"
    cp .env.example .env
fi

VENV="./venv/bin/python"
[ -x "$VENV" ] || VENV="python3"

# Ask the application itself whether the database answers; it already knows how
# to read .env and say why it cannot connect.
if ! PYTHONPATH=src "$VENV" -m smartpos --check >/dev/null 2>&1; then
    echo "The database did not answer."
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        echo "Starting MySQL in Docker..."
        docker compose up -d db
        printf "Waiting for it"
        for _ in $(seq 1 30); do
            if PYTHONPATH=src "$VENV" -m smartpos --check >/dev/null 2>&1; then
                echo " ready."
                break
            fi
            printf "."
            sleep 2
        done
    else
        echo
        PYTHONPATH=src "$VENV" -m smartpos --check || true
        echo
        echo "Fix the connection in .env, or start Docker and try again."
        exit 1
    fi
fi

echo "Launching SmartPOS..."
PYTHONPATH=src "$VENV" -m smartpos
