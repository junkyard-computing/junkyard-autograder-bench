#!/bin/bash

for dir in "$@"; do
    if [[ ! -d "$dir" ]]; then
        echo "Correct usage: $(basename "$0") <benchmark_run_dir1> <benchmark_run_dir2> ..."
        exit 1
    fi

    # Find + extract every timestamp in each file of the directories.
    OUT_DIR="timestamps_${dir}"
    echo "Timestamps stored in ./$OUT_DIR"
    IN_DIR="$dir"
    mkdir -p "$OUT_DIR"
    for f in "$IN_DIR"/*; do
        BASENAME=$(basename "$f")
        grep "Timestamp" "$f" > "${OUT_DIR}/${BASENAME%.log}.txt"
    done
done
