#!/usr/bin/env bash
# Start the database, wait for it, then launch the app from source.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "No .env found - copying .env.example"
    cp .env.example .env
fi

echo "Starting MySQL..."
docker compose up -d db

echo "Waiting for MySQL to accept connections..."
until docker compose exec -T db mysqladmin ping -h localhost --silent 2>/dev/null; do
    sleep 2
done

echo "Launching SmartPOS..."
PYTHONPATH=src python -m smartpos
