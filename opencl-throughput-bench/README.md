# OpenCL Throughput Benchmark

## TLDR

Runs a FMA like workload for a given size many times until it hits a set time in $\mu s$

The kernel pattern is similar to FMA, fast relaxed math and unsafe optimization flags are turned on, so ideally the compiler will optimize for hardware that supports FMA

Note that this only calculates the practical throughput after optimizing the tile sizes

## Quickstart

Building:

```bash
make
```

Running:

```bash
./solution <durnation in us>
```

## Overview

This benchmark runs a kernel that performs floating-point operations (FMA-like operations) on a large dataset. It measures the performance in GFLOPS (giga floating-point operations per second) by:

1. Initializing OpenCL context and command queue
2. Compiling and executing a custom kernel
3. Running the kernel in a loop for a specified duration
4. Measuring execution time and calculating performance metrics

The program is designed to stress-test OpenCL devices and provide a baseline for comparing hardware performance.

---

## Requirements

To run this program, you'll need:

- **OpenCL driver and implementation** (e.g., NVIDIA CUDA, AMD ROCm, Intel OpenCL, Qualcomm Adreno)
- **C compiler** (GCC or Clang)
- **helper_lib** (a helper library for OpenCL device management)
- **POSIX-compliant system** (Linux, macOS, or compatible environment)

---

##  Installation

1. **Compile the program**:
   ```bash
   make
   ```

   > The Makefile automatically detects your OS and configures the correct OpenCL paths.

---

## Usage

```bash
./solution [duration_in_seconds]
```

- **`duration_in_seconds`** (optional): Time in seconds to run the benchmark (default is 0.5 seconds)
- **Output** includes:
  - Number of kernel calls
  - Total elapsed time (in microseconds)
  - Total FLOPs executed
  - Performance in GFLOPS (FP32)

Example:
```bash
./solution 5
```

---

## Implementation Details

### `main.c`

- **OpenCL Initialization**: Sets up the OpenCL context, command queue, and loads the kernel from `workload.cl`
- **Kernel Execution**: Runs the kernel in a loop for the specified duration
- **Performance Measurement**: Uses OpenCL profiling events to measure execution time
- **FLOPS Calculation**: Computes GFLOPS based on the number of operations and elapsed time

### `workload.cl`

- **Kernel Logic**: Performs the following operation in a loop:
  ```c
  a = a * b + c; // 2 FLOPs (multiply + add)
  ```
- **Prevents Optimization**: Uses `volatile` to prevent the compiler from optimizing away the computation
- **Output**: Stores the final value of `a` in the output buffer

## Performance Considerations

- **FMA Operations**: The kernel uses a pattern similar to FMA (fused multiply-add) for high throughput
- **Global Work Size**: Set to `1024 * 1024 * 8` to maximize parallelism
- **Compiler Flags**:
  - `-cl-fast-relaxed-math`: Enables aggressive math optimizations
  - `-cl-mad-enable`: Enables MAD (Multiply-Add) instructions
  - `-cl-unsafe-math-optimizations`: Allows non-IEEE-compliant optimizations

---

## Examples

### Sample Run
```bash
./solution 5
```

### Sample Output
```
==============Starting Work==============
Calls: 1234
Elapsed: 5000123.456 us
Total FLOPs: 2.567e+12
Performance: 513.45 GFLOPS (FP32)
===============End of Work===============
```
---

## Troubleshooting

### Common Issues

1. **Missing OpenCL libraries**:
   - Ensure OpenCL is installed for your platform
   - Check the Makefile paths for OpenCL headers and libraries

2. **helper_lib not found**:
   - Navigate to `helper_lib` and run `make` manually

3. **Compiler errors**:
   - Ensure you're using a C99-compliant compiler
   - Check for missing includes or incorrect flags
