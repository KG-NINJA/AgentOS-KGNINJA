#!/usr/bin/env bash
set -e

case "$1" in
  save)
    ./factory/snapshot/save.sh
    ;;
  restore)
    ./factory/snapshot/restore.sh
    ;;
  *)
    echo "Usage: factory snapshot [save|restore]"
    exit 1
    ;;
esac
