#!/bin/bash
# Satcom simulation with baseline <-> high-loss switching
# - Verbose mode prints extra output and qdisc verification after each apply
# - Uses tcset (tcconfig 0.29.1) to apply rules and tcdel to delete them
# - CLI overrides for all parameters, optional dst/src ports

# -----------------------------
# Defaults
# -----------------------------
IFACE="wlp1s0"

BASE_RATE="128kbps"
BASE_DELAY="400ms"
BASE_DELAY_DISTRO="50ms"
BASE_LOSS="0.2%"
BASE_DUP="0.01%"
BASE_CORRUPT="0.01%"
BASE_LIMIT="200"
BASE_DURATION=60

HIGHLOSS_RATE="128kbps"
HIGHLOSS_DELAY="400ms"
HIGHLOSS_DELAY_DISTRO="50ms"
HIGHLOSS_LOSS="70%"
HIGHLOSS_DUP="0.01%"
HIGHLOSS_CORRUPT="0.01%"
HIGHLOSS_LIMIT="200"
HIGHLOSS_DURATION=30

OUT_DST_PORT=""
IN_SRC_PORT=""

VERBOSE=0
REPEAT=1

# -----------------------------
# Help
# -----------------------------
print_help() {
    cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --iface IFACE                 Network interface (default: $IFACE)

  --base-rate RATE              Baseline rate (default: $BASE_RATE)
  --base-delay DELAY            Baseline delay (default: $BASE_DELAY)
  --base-delay-distro DISTRO    Baseline delay distribution (default: $BASE_DELAY_DISTRO)
  --base-loss LOSS              Baseline loss (default: $BASE_LOSS)
  --base-dup DUP                Baseline duplicate rate (default: $BASE_DUP)
  --base-corrupt CORRUPT        Baseline corruption rate (default: $BASE_CORRUPT)
  --base-limit LIMIT            Baseline packet limit (default: $BASE_LIMIT)
  --base-duration SECONDS       Baseline duration (default: $BASE_DURATION)

  --high-rate RATE              High-loss rate (default: $HIGHLOSS_RATE)
  --high-delay DELAY            High-loss delay (default: $HIGHLOSS_DELAY)
  --high-delay-distro DISTRO    High-loss delay distribution (default: $HIGHLOSS_DELAY_DISTRO)
  --high-loss LOSS              High-loss packet loss (default: $HIGHLOSS_LOSS)
  --high-dup DUP                High-loss duplicate rate (default: $HIGHLOSS_DUP)
  --high-corrupt CORRUPT        High-loss corruption rate (default: $HIGHLOSS_CORRUPT)
  --high-limit LIMIT            High-loss packet limit (default: $HIGHLOSS_LIMIT)
  --high-duration SECONDS       High-loss duration (default: $HIGHLOSS_DURATION)

  --dst-port PORT               Outgoing shaping destination port (default: none)
  --src-port PORT               Incoming shaping source port (default: none)

  --repeat N                    Repeat baseline→highloss→baseline sequence N times (default: $REPEAT)

  --verbose                     Enable verbose output
  --help                        Show this help message
EOF
}

# -----------------------------
# Parse CLI args
# -----------------------------
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        --iface) IFACE="$2"; shift 2 ;;
        --base-rate) BASE_RATE="$2"; shift 2 ;;
        --base-delay) BASE_DELAY="$2"; shift 2 ;;
        --base-delay-distro) BASE_DELAY_DISTRO="$2"; shift 2 ;;
        --base-loss) BASE_LOSS="$2"; shift 2 ;;
        --base-dup) BASE_DUP="$2"; shift 2 ;;
        --base-corrupt) BASE_CORRUPT="$2"; shift 2 ;;
        --base-limit) BASE_LIMIT="$2"; shift 2 ;;
        --base-duration) BASE_DURATION="$2"; shift 2 ;;
        --high-rate) HIGHLOSS_RATE="$2"; shift 2 ;;
        --high-delay) HIGHLOSS_DELAY="$2"; shift 2 ;;
        --high-delay-distro) HIGHLOSS_DELAY_DISTRO="$2"; shift 2 ;;
        --high-loss) HIGHLOSS_LOSS="$2"; shift 2 ;;
        --high-dup) HIGHLOSS_DUP="$2"; shift 2 ;;
        --high-corrupt) HIGHLOSS_CORRUPT="$2"; shift 2 ;;
        --high-limit) HIGHLOSS_LIMIT="$2"; shift 2 ;;
        --high-duration) HIGHLOSS_DURATION="$2"; shift 2 ;;
        --dst-port) OUT_DST_PORT="$2"; shift 2 ;;
        --src-port) IN_SRC_PORT="$2"; shift 2 ;;
        --repeat) REPEAT="$2"; shift 2 ;;
        --verbose) VERBOSE=1; shift ;;
        --help) print_help; exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# -----------------------------
# Build extra args arrays
# -----------------------------
OUT_EXTRA=()
IN_EXTRA=()
[[ -n "$OUT_DST_PORT" ]] && OUT_EXTRA+=(--dst-port "$OUT_DST_PORT")
[[ -n "$IN_SRC_PORT" ]] && IN_EXTRA+=(--src-port "$IN_SRC_PORT")

# -----------------------------
# Helper: run a tcset command (prints command always; captures stderr in verbose)
# -----------------------------
run_tcset() {
    local cmd=("$@")
    echo "[CMD] ${cmd[*]}"
    if [[ $VERBOSE -eq 1 ]]; then
        "${cmd[@]}"
        return $?
    else
        "${cmd[@]}" 2>/dev/null
        return $?
    fi
}

# -----------------------------
# Helper: apply scenario with timing & optional extra args
# -----------------------------
apply_scenario() {
    local direction=$1
    local rate=$2
    local delay=$3
    local delay_distro=$4
    local loss=$5
    local dup=$6
    local corrupt=$7
    local limit=$8
    local action=$9
    shift 9
    local extra_args=("$@")

    local start_ts=$(date +%s.%3N)

    # Build command array
    cmd=(tcset "${extra_args[@]}" --direction "$direction" \
         --rate "$rate" --delay "$delay" --delay-distro "$delay_distro" \
         --loss "$loss" --duplicate "$dup" --corrupt "$corrupt" \
         --limit "$limit" "$action" "$IFACE")

    # One-line combined log
    local timestamp="[$(date +'%Y-%m-%d %H:%M:%S')]"
    local cmd_str="${cmd[*]}"
    echo "$timestamp $direction ($action): rate=$rate, loss=$loss | $cmd_str"

    # Execute command
    if [[ $VERBOSE -eq 1 ]]; then
        "${cmd[@]}"
    else
        "${cmd[@]}" 2>/dev/null
    fi
    rc=$?
}


# -----------------------------
# Pre-create qdiscs (overwrite) - do outgoing then incoming
# -----------------------------
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Pre-creating qdiscs..."
apply_scenario outgoing "$BASE_RATE" "$BASE_DELAY" "$BASE_DELAY_DISTRO" "$BASE_LOSS" "$BASE_DUP" "$BASE_CORRUPT" "$BASE_LIMIT" "--overwrite" "${OUT_EXTRA[@]}"
apply_scenario incoming "$BASE_RATE" "$BASE_DELAY" "$BASE_DELAY_DISTRO" "$BASE_LOSS" "$BASE_DUP" "$BASE_CORRUPT" "$BASE_LIMIT" "--overwrite" "${IN_EXTRA[@]}"

# -----------------------------
# Repeat loop
# -----------------------------
for ((i=1; i<=REPEAT; i++)); do

    apply_scenario outgoing "$BASE_RATE" "$BASE_DELAY" "$BASE_DELAY_DISTRO" "$BASE_LOSS" "$BASE_DUP" "$BASE_CORRUPT" "$BASE_LIMIT" "--change" "${OUT_EXTRA[@]}"
    apply_scenario incoming "$BASE_RATE" "$BASE_DELAY" "$BASE_DELAY_DISTRO" "$BASE_LOSS" "$BASE_DUP" "$BASE_CORRUPT" "$BASE_LIMIT" "--change" "${IN_EXTRA[@]}"

    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Baseline active for $BASE_DURATION seconds..."
    sleep "$BASE_DURATION"

    apply_scenario outgoing "$HIGHLOSS_RATE" "$HIGHLOSS_DELAY" "$HIGHLOSS_DELAY_DISTRO" "$HIGHLOSS_LOSS" "$HIGHLOSS_DUP" "$HIGHLOSS_CORRUPT" "$HIGHLOSS_LIMIT" "--change" "${OUT_EXTRA[@]}"
    apply_scenario incoming "$HIGHLOSS_RATE" "$HIGHLOSS_DELAY" "$HIGHLOSS_DELAY_DISTRO" "$HIGHLOSS_LOSS" "$HIGHLOSS_DUP" "$HIGHLOSS_CORRUPT" "$HIGHLOSS_LIMIT" "--change" "${IN_EXTRA[@]}"

    echo "[$(date +'%Y-%m-%d %H:%M:%S')] High-loss active for $HIGHLOSS_DURATION seconds..."
    sleep "$HIGHLOSS_DURATION"

done

# -----------------------------
# Cleanup - delete all rules using tcdel
# -----------------------------
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Deleting all tc rules on $IFACE..."
# tcdel <device> --all
echo "[CMD] tcdel $IFACE --all"
if [[ $VERBOSE -eq 1 ]]; then
    tcdel "$IFACE" --all
    rc=$?
else
    tcdel "$IFACE" --all 2>/dev/null
    rc=$?
fi

if [[ $rc -ne 0 ]]; then
    echo "[WARNING] tcdel returned rc=$rc (non-zero). Check manually with 'tc qdisc show dev $IFACE' or 'tcshow $IFACE'."
fi

# Verify cleanup using tcshow
echo "[$(date +'%Y-%m-%d %H:%M:%S')] Verifying cleanup..."
tcshow "$IFACE"

# Simple check: if tcshow shows empty outgoing and incoming, report success
state_json=$(tcshow "$IFACE" 2>/dev/null)
if echo "$state_json" | grep -q '"outgoing": {}' && echo "$state_json" | grep -q '"incoming": {}'; then
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] Cleanup verified: no rules present."
else
    echo "[WARNING] Cleanup verification indicates qdisc(s) may still be present. Output above."
fi

echo "[$(date +'%Y-%m-%d %H:%M:%S')] Scenario complete."

