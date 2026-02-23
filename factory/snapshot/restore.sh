#!/usr/bin/env bash
set -euo pipefail

latest=$(ls -1 snapshots | sort | tail -n1)

if [ -z "$latest" ]; then
    echo "No snapshot found"
    exit 1
fi

rm -rf workspace runtime factory

cp -a snapshots/$latest/workspace .
cp -a snapshots/$latest/runtime .
cp -a snapshots/$latest/factory .

echo "SNAPSHOT RESTORED: $latest"
