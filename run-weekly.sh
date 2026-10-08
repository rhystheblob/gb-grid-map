#!/bin/zsh
# Weekly refresh of the SSEN grid capacity map data.
# Run weekly by a cloud agent (Mondays 07:00), which commits the refreshed data/ afterwards.
# Needs SSEN_API_KEY in the environment or in .env.
#   1. public ArcGIS layers (GSP/BSP/Primary capacity) - re-pulled every run
#   2. secondary supply areas - re-downloaded only if SSEN republished
#   3. smart meter peaks - recomputed from the newest 7 daily CSVs in smart_meter_uploads/
set -u
DIR="${0:A:h}"
LOG_DIR="$DIR/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/weekly-$(date +%Y-%m-%d).log"
PY=python3

{
  echo "=== SSEN weekly refresh $(date '+%Y-%m-%d %H:%M:%S') ==="
  cd "$DIR" || exit 1
  status=0
  $PY fetch_public.py || { echo "!! fetch_public.py failed"; status=1; }
  $PY fetch_portal.py || { echo "!! fetch_portal.py failed"; status=1; }

  newest=$(ls -1 smart_meter_uploads/*.csv 2>/dev/null | tail -1)
  if [[ -z "$newest" ]]; then
    echo "note: no smart meter CSVs yet - upload daily files from the portal's Raw files tab"
  elif [[ -n $(find "$newest" -mtime +8) ]]; then
    echo "note: newest smart meter file ($newest) is over a week old - download the latest days"
  fi
  echo "=== done (status $status) $(date '+%H:%M:%S') ==="
  exit $status
} 2>&1 | tee -a "$LOG"
exit ${pipestatus[1]}
