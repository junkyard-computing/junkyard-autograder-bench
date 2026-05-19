# OpenCL Throughput Benchmark

Runs a FMA like workload for a given size many times until it hits a set time in $\mu s$

Note that this only calculates the practical throughput after optimizing the tile sizes

## Usage

Building:

```bash
make
```

Running:

```bash
./solution <durnation in us>
```

