#!/bin/bash
set -e

# Configuration
SUBMISSION_SRC_DIR="./opencl"
TEMP_ZIP_DIR="/tmp/autograder_payload"

SSH_USER="luffy"
SSH_HOST="132.239.17.60"
CLUSTER_SCRIPT="/home/$SSH_USER/submit_job.sh"
STUDENT_NAME="SSH-Runner"
ASSIGNMENT_TITLE="PA0"

if [ ! -f "secret_token" ]; then
    echo "ERROR: secret_token not found."
    exit 1
fi
SECRET_TOKEN=$(cat secret_token)

# Times in microseconds to test
TIMES=(1000000)

for TIME_VAL in "${TIMES[@]}"; do
    echo "========================================"
    echo "Preparing job for Time: $TIME_VAL µs"
    
    # 1. Create a fresh temporary directory for zipping
    rm -rf "$TEMP_ZIP_DIR"
    mkdir -p "$TEMP_ZIP_DIR"
    
    # 2. Copy the student's source code to the temp dir
    cp -r "$SUBMISSION_SRC_DIR"/* "$TEMP_ZIP_DIR/"
    
    # 3. Dynamically generate the benchmark.sh script
    cat <<EOF > "$TEMP_ZIP_DIR/benchmark.sh"
#!/bin/bash
set -e
echo "Compiling..."
make
echo "Running solution with time: $TIME_VAL"
./solution $TIME_VAL
EOF
    chmod +x "$TEMP_ZIP_DIR/benchmark.sh"

# 3. Zip the workspace locally
    LOCAL_ZIP="/tmp/payload_${TIME_VAL}.zip"
    pushd "$TEMP_ZIP_DIR" > /dev/null
    zip -qr "$LOCAL_ZIP" .
    popd > /dev/null

    # 4. Transfer the zip to the cluster
    REMOTE_ZIP="/tmp/cluster_payload_${TIME_VAL}_$RANDOM.zip"
    echo "Transferring payload to cluster..."
    scp -q "$LOCAL_ZIP" "$SSH_USER@$SSH_HOST:$REMOTE_ZIP"

    # 5. Trigger the permanent script on the cluster
    echo "Triggering cluster submission..."
    ssh "$SSH_USER@$SSH_HOST" "$CLUSTER_SCRIPT '$REMOTE_ZIP' '$ASSIGNMENT_TITLE' '$SECRET_TOKEN' '$STUDENT_NAME'"

    # 6. Local cleanup
    rm "$LOCAL_ZIP"
done

echo "All benchmarks completed."
