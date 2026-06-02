# File Structure

```
cluster-bench/
├── opencl/                          # OpenCL workload submitted as the benchmark job
│   ├── main.c                       # Entry point; runs matrix multiply for a given duration
│   ├── workload.cl                  # OpenCL kernel
│   ├── helper_lib/                  # Shared OpenCL utility library (device, kernel, matrix, img)
│   └── Makefile
├── qcluster/                        # Qualcomm cluster config
│   ├── Dockerfile                   # Container image for the qcluster autograder worker
│   └── cse-145-qcomm-server-config.yaml
├── strawhat/                        # Strawhat cluster config, benchmark scripts, and results
│   ├── Dockerfile                   # Container image for the strawhat autograder worker
│   ├── cse-145-strawhat-server-config.yaml
│   ├── run_benchmarks.sh            # Main benchmark driver: SSHes to jump host, submits jobs in parallel
│   ├── submit_job.sh                # Runs on the jump host: forwards zip to job server, polls for result
│   ├── extract_timestamps.sh        # Pulls nanosecond timestamps out of raw benchmark logs
│   ├── convert_to_relative_timestamps.sh # Converts absolute ns timestamps to relative offsets
│   ├── extract_latencies.py         # Parses log files and reports average latency breakdown
│   ├── rel_timestamps_to_excel.py   # Exports relative timestamps to Excel for analysis
│   ├── secret_token.example         # Template for the bearer token used to authenticate job submissions
│   ├── latency-results/             # Raw log files from latency benchmark runs
│   └── throughput-results/          # Raw log files from throughput benchmark runs
├── monitor_phone.sh                 # Live dashboard: GPU freq/util, CPU load, and temps on the phone
└── run_native_benchmarks.sh         # Runs the OpenCL workload locally N times and reports GFLOPS
```

# Instructions to Benchmark

## Junkyard Autograder

Follow the junkyard autograder setup instructions, and get the autograder running on the control plane

## Setup necessary files on the control plane/jump host

Copy cse-145-<cluster>-server-config.yml and submit_job.sh to the control plane/jump_host

```bash
kubectl apply -f cse-145-<cluster>-server-config.yml
```

## Run the benchmark

Simply call ./run_benchmark <NUM_ITERATIONS> <NUM_NODES> or edit
the script to change desired number of jobs/workload times

NUM_ITERATIONS controls how many iterations of the USED_TIMES
array (once per time inside the array) we run the benchmark on

NUM_NODES doesn't actually change the number of nodes used,
but changes the output directory name
