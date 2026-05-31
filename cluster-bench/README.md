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
