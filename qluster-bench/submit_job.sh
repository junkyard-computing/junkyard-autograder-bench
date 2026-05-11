#!/bin/bash
set -e # Exit immediately if a command exits with a non-zero status

ZIP_FILE=$1
ASSIGNMENT_TITLE=$2
SECRET_TOKEN=$3
STUDENT_NAME=$4

POD_LABEL="app=job-server"
NAMESPACE="default"
TIMEOUT=300         # Max seconds to wait for job completion
INTERVAL=5          # Poll interval in seconds

echo "DEBUG: Starting internal submission script." | tee -a /dev/stderr

# 1. Find the Go Server Pod IP
POD_IP=$(kubectl get pods -n "$NAMESPACE" -l "$POD_LABEL" -o jsonpath="{.items[0].status.podIP}" 2>/dev/null)
if [ -z "$POD_IP" ]; then
    echo "ERROR: Could not find pod with label: $POD_LABEL" | tee -a /dev/stderr
    exit 1
fi
INTERNAL_URL="http://$POD_IP:5000"
echo "DEBUG: Go server found at internal IP: $INTERNAL_URL" | tee -a /dev/stderr

# 2. Submit the Job
echo "DEBUG: Submitting job to Go server at $INTERNAL_URL/submit" | tee -a /dev/stderr
SUBMIT_RESP=$(curl -s -w "\nHTTP_STATUS:%{http_code}" \
    -F "name=$STUDENT_NAME" \
    -H "Authorization: Bearer $SECRET_TOKEN" \
    -F "image=$ASSIGNMENT_TITLE" \
    -F "script=@$ZIP_FILE" \
    "$INTERNAL_URL/submit")

HTTP_STATUS=$(echo "$SUBMIT_RESP" | tail -n1 | sed -e 's/HTTP_STATUS://')
BODY_RESP=$(echo "$SUBMIT_RESP" | sed '$d')

echo "DEBUG: Submit HTTP Status: $HTTP_STATUS" | tee -a /dev/stderr

if [ "$HTTP_STATUS" -ne 202 ]; then
    echo "ERROR: Initial job submission failed with HTTP status $HTTP_STATUS." | tee -a /dev/stderr
    echo "Server Response: $BODY_RESP" | tee -a /dev/stderr
    rm -f "$ZIP_FILE" # Cleanup
    exit 1
fi

# Extract JOB_ID using jq
JOB_ID=$(echo "$BODY_RESP" | jq -r '.job_id // empty')
echo "DEBUG: Parsed JOB_ID: '$JOB_ID'. Polling for results..." | tee -a /dev/stderr

if [ -z "$JOB_ID" ]; then
    echo "ERROR: Failed to get valid JOB_ID from submission response." | tee -a /dev/stderr
    rm -f "$ZIP_FILE"
    exit 1
fi

# 3. Poll for Status
END_TIME=$(( SECONDS + TIMEOUT ))
JOB_COMPLETE=false
JOB_STATUS=""
JOB_RESULTS_OUTPUT=""
JOB_SERVER_ERROR=""

while [ $SECONDS -lt "$END_TIME" ]; do
    echo "DEBUG: Polling URL: $INTERNAL_URL/status/$JOB_ID (Time remaining: $(( END_TIME - SECONDS ))s)" | tee -a /dev/stderr
    STATUS_RESP=$(curl -s "$INTERNAL_URL/status/$JOB_ID" -H "Authorization: Bearer $SECRET_TOKEN")
    CURL_EXIT_CODE=$?

    if [ "$CURL_EXIT_CODE" -ne 0 ]; then
        echo "WARNING: Error polling job status (curl exit code $CURL_EXIT_CODE). Retrying..." | tee -a /dev/stderr
    else
        # Robustly parse JSON
        if ! PARSED_STATUS=$(echo "$STATUS_RESP" | jq -r '.status // empty'); then
            echo "ERROR: Failed to parse JSON status from polling response." | tee -a /dev/stderr
            JOB_STATUS="json_parse_error"
        else
            JOB_STATUS="$PARSED_STATUS"
            echo "DEBUG: Current Job Status: $JOB_STATUS" | tee -a /dev/stderr
        fi

        if [ "$JOB_STATUS" == "succeeded" ] || [ "$JOB_STATUS" == "failed" ]; then
            JOB_RESULTS_OUTPUT=$(echo "$STATUS_RESP" | jq -r '.results // empty')
            JOB_SERVER_ERROR=$(echo "$STATUS_RESP" | jq -r '.error // empty')
            JOB_COMPLETE=true
            break
        elif [ "$JOB_STATUS" == "json_parse_error" ] || [ "$JOB_STATUS" == "pending" ] || [ "$JOB_STATUS" == "" ]; then
            : # Continue waiting
        else
            echo "WARNING: Received unknown job status: '$JOB_STATUS'. Retrying..." | tee -a /dev/stderr
        fi
    fi
    sleep "$INTERVAL"
done

if [ "$JOB_COMPLETE" == "false" ]; then
    echo "ERROR: Job $JOB_ID timed out after $TIMEOUT seconds." | tee -a /dev/stderr
    jq -n '{score: 0, output: "Grading job timed out or encountered persistent polling issues."}'
    rm -f "$ZIP_FILE"
    exit 1
fi

# 4. Fetch Raw Logs and output the final JSON
echo "DEBUG: Job $JOB_ID completed. Fetching raw logs..." | tee -a /dev/stderr
RAW_LOGS_DIRTY=$(curl -s "$INTERNAL_URL/logs/$JOB_ID" -H "Authorization: Bearer $SECRET_TOKEN")

# Use sed to remove the JSON block and the Metadata block
CLEAN_LOGS=$(echo "$RAW_LOGS_DIRTY" | sed '/---JSON_START---/,$d' | sed '/---METADATA---/,$d')

if [ "$JOB_STATUS" == "succeeded" ]; then
    echo "DEBUG: Raw JOB_RESULTS_OUTPUT:" | tee -a /dev/stderr
    echo "$JOB_RESULTS_OUTPUT" | tee -a /dev/stderr

    echo "DEBUG: Job succeeded. Formatting final JSON." | tee -a /dev/stderr
    echo "$JOB_RESULTS_OUTPUT" | jq --arg logs "$CLEAN_LOGS" '.output = .output + "\n\n--- EXECUTION LOGS ---\n" + $logs'
else
    echo "DEBUG: Job failed. Formatting final error JSON." | tee -a /dev/stderr
    jq -n --arg logs "$CLEAN_LOGS" --arg err "$JOB_SERVER_ERROR" \
        '{score: 0, output: ("Job failed.\nServer Error: " + $err + "\n\n--- LOGS ---\n" + $logs)}'
fi

# Cleanup zip file
rm -f "$ZIP_FILE"
exit 0
