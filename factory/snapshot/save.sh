#!/usr/bin/env bash
set -euo pipefail

timestamp=$(date +"%Y%m%d-%H%M%S")
mkdir -p snapshots/$timestamp

cp -a workspace snapshots/$timestamp/
cp -a runtime snapshots/$timestamp/
cp -a factory snapshots/$timestamp/

echo "SNAPSHOT SAVED: $timestamp"
