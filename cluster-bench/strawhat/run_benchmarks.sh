#!/bin/bash
set -e

# Configuration
SUBMISSION_SRC_DIR="../opencl"

SSH_USER="luffy"
SSH_HOST="132.239.17.60"
CLUSTER_SCRIPT="/home/$SSH_USER/submit_job.sh"
STUDENT_NAME="redacted"
ASSIGNMENT_TITLE="PA0"

JUMP_HOST="$SSH_USER@$SSH_HOST"
MUX_DIR=$(mktemp -d "/tmp/ssh_mux_XXXXXX") || exit 1
PORT=8888
REMOTE_PORT=5000

# Each socket can multiplex up to 10* connections (jobs) by default
# The true number of connections per socket depends on 'MaxSessions'
#   inside of .ssh/ssh/sshd_config of jump host
NUM_SOCKETS=3

pkill -f "ssh -M -S /tmp/.*luffy@132.239.17.60" || true

sleep 0.1

# Create master tunnels
echo "Establishing SSH ControlMaster tunnels..."
for s in $(seq 1 $NUM_SOCKETS); do
    ssh -M -S "$MUX_DIR/socket_$s" -f -N "$JUMP_HOST"
done

#sleep 1

# Teardown port forward + master tunnel on ./run_benchmark exit
trap 'set +e; \
    echo "Cleaning up SSH and remote processes..."; \
    ssh -S "$MUX_DIR/socket_1" "$JUMP_HOST" "pkill -f \"kubectl port-forward\" || true; \
    sudo ss -lptn \"sport = :8888\" | \
    awk \"
    match($0, /pid=([0-9]+)/, m) && !p {
        p = m[1]
    } 
    END {
        if (p) {print p}
    }\" | xargs kill || true"; \
    for s in $(seq 1 '$NUM_SOCKETS'); do ssh -S "$MUX_DIR/socket_$s" -O exit "$JUMP_HOST" 2>/dev/null || true; done; \
    rm -rf "$MUX_DIR"' EXIT

echo "Clearing old remote port-forwards..."
ssh -S "$MUX_DIR/socket_1" "$JUMP_HOST" "pkill -f 'kubectl port-forward' || true; \
    sudo ss -lptn 'sport = :8888' | \
    awk '
    match($0, /pid=([0-9]+)/, m) && !p {
        p = m[1]
    } 
    END {
        if (p) {print p}
    }' | xargs kill " || true

sleep 2

echo "Starting new remote port-forward..."
ssh -S "$MUX_DIR/socket_1" "$JUMP_HOST" \
    "export KUBECONFIG=/home/luffy/cluster-b.kubeconfig && nohup kubectl port-forward deployment/job-server $PORT:$REMOTE_PORT </dev/null >/dev/null 2>&1 &" || true
sleep 3

if [ ! -f "secret_token" ]; then
    echo "ERROR: secret_token not found."
    exit 1
fi
SECRET_TOKEN=$(cat secret_token)

# Times in microseconds to test
TIMES=(1000000 2000000 4000000 8000000 16000000 32000000 64000000 124000000)

USED_TIMES=("${TIMES[@]:0:3}")
NUM_ITERATIONS=${1:-0}
TOTAL_JOBS=$(( NUM_ITERATIONS * ${#USED_TIMES[@]} ))

NUM_NODES=${2:-0}

RUN_DIR="benchmark_run_${TOTAL_JOBS}_${NUM_NODES}_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RUN_DIR"

echo "Output for this run will be saved to: $RUN_DIR"
echo "Running workload with these times (μs): (${USED_TIMES[*]})"

echo "Creating base payload..."
BASE_ZIP="/tmp/base_payload_$RANDOM.zip"
pushd "$SUBMISSION_SRC_DIR" > /dev/null
zip -qr "$BASE_ZIP" .
popd > /dev/null

for ((i = 1; i <= NUM_ITERATIONS; i++)) do
    for TIME_VAL in "${USED_TIMES[@]}"; do
        SOCKET_NUM=$(( ((i - 1) % NUM_SOCKETS) + 1))
        CURRENT_SOCKET="$MUX_DIR/socket_$SOCKET_NUM"

        (
            RUN_LOG="$RUN_DIR/${TIME_VAL}_${i}.log"
            echo "Output for this job will be saved to: $RUN_LOG"

            echo "Timestamp (gradescope start): $(date +%s%N)" | tee -a "$RUN_LOG"
            echo "========================================" | tee -a "$RUN_LOG"
            echo "Preparing job for Time: $TIME_VAL µs" | tee -a "$RUN_LOG"

            # 1. Create a fresh temporary directory for zipping
            TEMP_ZIP_DIR="/tmp/autograder_payload_${i}_${TIME_VAL}"
            rm -rf "$TEMP_ZIP_DIR"
            mkdir -p "$TEMP_ZIP_DIR"

            # 2. Dynamically generate the benchmark.sh script
            cat <<EOF > "$TEMP_ZIP_DIR/benchmark.sh"
#!/bin/bash
set -e
echo "Compiling..."
make
echo "Running solution with time: $TIME_VAL"
./solution $TIME_VAL
EOF
            chmod +x "$TEMP_ZIP_DIR/benchmark.sh"

            # 3. Copy the base payload zip and update with custom benchmark.sh
            LOCAL_ZIP="/tmp/payload_${i}_${TIME_VAL}.zip"
            cp "$BASE_ZIP" "$LOCAL_ZIP"

            pushd "$TEMP_ZIP_DIR" > /dev/null
            zip -qu "$LOCAL_ZIP" benchmark.sh 
            popd > /dev/null

            # 4. Transfer the zip to the cluster
            REMOTE_ZIP="/tmp/cluster_payload_${i}_${TIME_VAL}_${RANDOM}.zip"
            echo "Transferring payload to cluster..." | tee -a "$RUN_LOG"
            scp -o ControlPath="$CURRENT_SOCKET" -q "$LOCAL_ZIP" "$JUMP_HOST:$REMOTE_ZIP"

            # 5. Trigger the permanent script on the cluster
            echo "Timestamp (gradescope -> junkyard, start): $(date +%s%N)" | tee -a "$RUN_LOG"
            ssh -S "$CURRENT_SOCKET" "$JUMP_HOST" "'$CLUSTER_SCRIPT' '$REMOTE_ZIP' '$ASSIGNMENT_TITLE' '$SECRET_TOKEN' '$STUDENT_NAME'" 2>&1 | tee -a "$RUN_LOG"

            # 6. Local cleanup
            rm "$LOCAL_ZIP"
        ) &
    done
done

echo "All benchmarks submitted. Waiting for tasks to finish..."
wait
echo "All benchmarks completed."
