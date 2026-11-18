#!/usr/bin/env bash
# Retain native exit/OOM diagnostics before removing this gate's isolated container.
set -euo pipefail

gate="${1:?Usage: run-native-gate.sh <container-script> [image]}"
image="${2:-modelryx-freecad-ci}"
if (( $# >= 2 )); then shift 2; else shift; fi
temporary=$(mktemp -d)
cid_file="$temporary/container-id"

finish() {
  status=$?
  trap - EXIT
  set +e
  if [[ -s "$cid_file" ]]; then
    cid=$(cat "$cid_file")
    docker inspect --format '{{json .State}}' "$cid"
    oom=$(docker inspect --format '{{.State.OOMKilled}}' "$cid")
    if [[ "$oom" == true ]]; then
      echo "::error::Native gate $gate was killed by the container memory limit (OOMKilled=true)."
    elif [[ "$status" == 137 ]]; then
      echo "::error::Native gate $gate received SIGKILL (137), but Docker did not report an OOM kill. Check runner kernel diagnostics."
    fi
    docker rm --force "$cid" >/dev/null
  fi
  rm -f "$cid_file"
  rmdir "$temporary"
  exit "$status"
}
trap finish EXIT

printf 'Running native gate %s (4 GiB RAM, 8 GiB RAM+swap, 2 CPUs)\n' "$gate"
docker create --cidfile "$cid_file" --memory 4g --memory-swap 8g --cpus 2   --env OMP_NUM_THREADS=1 --env OPENBLAS_NUM_THREADS=1 --env MKL_NUM_THREADS=1   --env NUMEXPR_NUM_THREADS=1 --env MALLOC_ARENA_MAX=2   "$image" python -u "$gate" "$@" >/dev/null
cid=$(cat "$cid_file")
set +e
docker start --attach "$cid"
attach_status=$?
set -e
container_status=$(docker inspect --format '{{.State.ExitCode}}' "$cid")
if [[ "$attach_status" != 0 ]]; then
  exit "$attach_status"
fi
exit "$container_status"
