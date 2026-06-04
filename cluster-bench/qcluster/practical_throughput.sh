#!/bin/bash

for dir in "$@"; do
    if [[ ! -d "$dir" ]]; then
        echo "Correct usage: $(basename "$0") <benchmark_run_dir1> <benchmark_run_dir2> ..."
        exit 1
    fi

    # THROUGHPUT=$(grep -E --max-count=1 --only --no-filename "([\.0-9]+)\sGFLOPS" -r "$dir" | awk '{print $1}' | awk '{SUM += $1} END {print SUM}')

    # Dark magic
    # n = node, g = GFLOPS, f = FLOPs, min_t = min timestamp, max_t = max timestamp
    awk '
    FNR == 1 {
        n = g = f = min_t = max_t = ""
    }

    match($0, /node[[:space:]](krg-qcluster[0-9]+)/, m) && !n {
        n = m[1]
    }

    match($0, /([0-9.]+)[[:space:]]GFLOPS/, m) && !g {
        g = m[1]
    }

    match($0, /Total[[:space:]]FLOPs:[[:space:]]([0-9e.+]+)\\n/, m) && !f {
        f = m[1]
    }

    match($0, /start\):[[:space:]]([0-9]+)/, m) && !min_t {
        min_t = m[1]
    }

    match($0, /success\):[[:space:]]([0-9]+)/, m) && !max_t {
        max_t = m[1]
    }

    ENDFILE {
        if (n && g && f && min_t && max_t) {
            printf "%s %s %s %s %s\n", n, g, f, min_t, max_t
        }
    }
    ' "$dir"/* | awk '
    {
        GFLOPS[$1] += $2
        jobs[$1] += 1
        FLOPs[$1] += $3

        if ($1 in min_t && $1 in max_t) {
            if ($4 < min_t[$1]) {
                min_t[$1] = $4
            }
            if ($5 > max_t[$1]) {
                max_t[$1] = $5
            }
        } else {
            min_t[$1] = $4
            max_t[$1] = $5
        }

    }

    END {
        count = 0
        for (i in GFLOPS) {
            printf "Node: %s, FLOPs: %s, min_time: %s, max_time: %s, jobs: %s\n", \
                i, FLOPs[i], min_t[i], max_t[i], jobs[i]
            PRACTICAL_THROUGHPUT += GFLOPS[i] / jobs[i]
            EFFICIENCY += (FLOPs[i] / ((max_t[i] - min_t[i]) / 1e6)) \
                / (GFLOPS[i] * 1e6 / jobs[i])
            count++ 
        }
        printf "Practical Throughput: %s\n", PRACTICAL_THROUGHPUT
        printf "Average Efficiency: %s\n", EFFICIENCY / count
    }
    '

    # echo "Dir: ${dir}, Total Average Throughput: $AVG_THROUGHPUT"
done
