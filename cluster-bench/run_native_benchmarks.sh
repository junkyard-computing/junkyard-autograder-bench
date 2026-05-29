N=${1:-50}
  DURATION=1000000
  TOTAL_GFLOPS=0
  TOTAL_ELAPSED_S=0
  GFLOPS_SUM=0
  START=$(date +%s%N)

  for i in $(seq 1 $N); do
      OUTPUT=$(./solution $DURATION)
      PERF=$(echo "$OUTPUT" | grep Performance | awk '{print $2}')
      ELAPSED=$(echo "$OUTPUT" | grep Elapsed | awk '{print $2}')
      FLOPS=$(echo "$OUTPUT" | grep "Total FLOPs" | awk '{print $3}')
      ELAPSED_MS=$(awk "BEGIN {printf \"%.1f\", $ELAPSED / 1000}")
      GFLOPS_JOB=$(awk "BEGIN {printf \"%.2f\", $FLOPS / 1e9}")
      echo "Job $i: ${ELAPSED_MS} ms  ${GFLOPS_JOB} GFLOPs  ${PERF} GFLOPS"
      TOTAL_GFLOPS=$(awk "BEGIN {print $TOTAL_GFLOPS + $FLOPS / 1e9}")
      TOTAL_ELAPSED_S=$(awk "BEGIN {print $TOTAL_ELAPSED_S + $ELAPSED / 1e6}")
      GFLOPS_SUM=$(awk "BEGIN {print $GFLOPS_SUM + $PERF}")
  done

  END=$(date +%s%N)
  TOTAL_TIME_S=$(awk "BEGIN {print ($END - $START) / 1e9}")
  GFLOPS_PER_JOB=$(awk "BEGIN {print $TOTAL_GFLOPS / $N}")
  GFLOPS_WALL=$(awk "BEGIN {print $TOTAL_GFLOPS / $TOTAL_TIME_S}")
  GFLOPS_ELAPSED=$(awk "BEGIN {print $TOTAL_GFLOPS / $TOTAL_ELAPSED_S}")
  GFLOPS_AVG=$(awk "BEGIN {print $GFLOPS_SUM / $N}")
  echo "=== NATIVE BASELINE ==="
  echo "Jobs:              $N"
  echo "Total time:        $TOTAL_TIME_S s"
  echo "GFLOPs/job:        $GFLOPS_PER_JOB"
  echo "Throughput:        $GFLOPS_WALL GFLOPS (total GFLOPs/total wall time)"
  echo "GPU active rate:   $GFLOPS_ELAPSED GFLOPS (total GFLOPs/sum of elapsed)"
  echo "Avg per-job:       $GFLOPS_AVG GFLOPS (avg of per-job GFLOPS)"
