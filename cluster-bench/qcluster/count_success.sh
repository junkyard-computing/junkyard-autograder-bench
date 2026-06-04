#!/bin/bash

REGEX="^benchmark_run_([0-9]+)"

COUNT=$(grep "success" -r "$1" | wc -l)

if [[ "$1" =~ $REGEX && $COUNT -eq ${BASH_REMATCH[1]} ]]; then
    NEW_DIR="throughput-results/benchmark-2s-${COUNT}j-$(( COUNT / 5  ))n"
    mkdir -p "$NEW_DIR"
    mv "$1" "$NEW_DIR"
    ./practical_throughput.sh "${NEW_DIR}/$1"
else
    echo "Not all jobs succeeded. Count succeeded: ${COUNT}"
fi
