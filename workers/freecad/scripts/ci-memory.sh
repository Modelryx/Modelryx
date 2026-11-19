#!/usr/bin/env bash
# GitHub-hosted VM resource setup; never alter a developer's host swap configuration.
set -euo pipefail

if [[ "${GITHUB_ACTIONS:-}" != true ]]; then
  echo 'This resource setup is for GitHub Actions runners only.' >&2
  exit 1
fi

case "${1:-}" in
  prepare)
    free -h
    df -h "${RUNNER_TEMP:?}"
    swap_kib=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)
    # Provision before the image build: layer export can also pressure runner memory.
    if (( swap_kib < 4 * 1024 * 1024 )); then
      swapfile=$(mktemp "$RUNNER_TEMP/freecad-ci-swap.XXXXXX")
      printf 'FREECAD_CI_SWAPFILE=%s\n' "$swapfile" >> "${GITHUB_ENV:?}"
      sudo fallocate -l 4G "$swapfile"
      sudo chmod 600 "$swapfile"
      sudo mkswap "$swapfile"
      sudo swapon "$swapfile"
    fi
    free -h
    ;;
  cleanup)
    if [[ -n "${FREECAD_CI_SWAPFILE:-}" && -f "$FREECAD_CI_SWAPFILE" ]]; then
      case "$FREECAD_CI_SWAPFILE" in
        "${RUNNER_TEMP:?}"/freecad-ci-swap.*) ;;
        *) echo 'Refusing to remove an unexpected swap path.' >&2; exit 1 ;;
      esac
      if swapon --show=NAME --noheadings | grep -Fxq "$FREECAD_CI_SWAPFILE"; then
        sudo swapoff "$FREECAD_CI_SWAPFILE"
      fi
      sudo rm -f "$FREECAD_CI_SWAPFILE"
    fi
    ;;
  *) echo 'Usage: ci-memory.sh prepare|cleanup' >&2; exit 1 ;;
esac
