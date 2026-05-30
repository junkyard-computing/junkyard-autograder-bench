#!/bin/bash

for dir in "$@"; do
    if [[ ! -d "$dir" ]]; then
        echo "Correct usage: $(basename "$0") <benchmark_run_dir1> <benchmark_run_dir2> ..."
        exit 1
    fi

    # Max epoch time on 64-bit system
    MIN_TIME=$(( 2**63 - 1 ))

    OUT_DIR="rel_${dir}"
    echo "Relative timestamps stored in ./$OUT_DIR"
    IN_DIR="$dir"
    mkdir -p "$OUT_DIR"

    # Find minimum timestamp of the dir
    for f in "$IN_DIR"/*; do
        read -r FIRST_LINE < "$f"

        if [[ $FIRST_LINE =~ ([0-9]+$) ]]; then
            FIRST_TIMESTAMP="${BASH_REMATCH[1]}"
            MIN_TIME=$(( FIRST_TIMESTAMP < MIN_TIME ? FIRST_TIMESTAMP : MIN_TIME ))
        fi
    done

    # Convert all timestamps to relative timestamps
    for f in "$IN_DIR"/*; do
        BASENAME=$(basename "$f")
        sed -E "s/(.*: )([0-9]+)$/echo -n '\1\'; echo '\2 - $MIN_TIME' | bc/e" "$f" > "${OUT_DIR}/${BASENAME}"
    done
done
