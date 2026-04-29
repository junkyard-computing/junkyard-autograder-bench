#define _POSIX_C_SOURCE 199309L
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#include "device.h"
#include "kernel.h"
#include "matrix.h"

#define CHECK_ERR(err, msg)                           \
    if (err != CL_SUCCESS)                            \
    {                                                 \
        fprintf(stderr, "%s failed: %d\n", msg, err); \
        exit(EXIT_FAILURE);                           \
    }

#define DEFAULT_DURATION 500.0
#define ITERATIONS_PER_ITEM 1024
#define GLOBAL_WORK_SIZE (1024 * 1024 * 8)

#define FLOPS_PER_CALL ((double)2 * ITERATIONS_PER_ITEM * GLOBAL_WORK_SIZE)

// Gets the time in ns
static double get_time(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1e6 + ts.tv_nsec * 1e-3;
}

void initializeOpenCL(cl_device_id* device_id, cl_context* context, cl_command_queue* queue) {
    cl_int err;

    // Find platforms and devices
    OclPlatformProp *platforms = NULL;
    cl_uint num_platforms;
    err = OclFindPlatforms((const OclPlatformProp **)&platforms, &num_platforms);
    CHECK_ERR(err, "OclFindPlatforms");

    // Get the ID for the specified kind of device type.
    err = OclGetDeviceWithFallback(device_id, OCL_DEVICE_TYPE);
    CHECK_ERR(err, "OclGetDeviceWithFallback");

    // Create a context
    *context = clCreateContext(0, 1, device_id, NULL, NULL, &err);
    CHECK_ERR(err, "clCreateContext");

    // Create a command queue
    cl_queue_properties props[] = { CL_QUEUE_PROPERTIES, CL_QUEUE_PROFILING_ENABLE, 0 };
    *queue = clCreateCommandQueueWithProperties(*context, *device_id, props, &err);
    CHECK_ERR(err, "clCreateCommandQueueWithProperties");

    free(platforms);
}

int main(int argc, char *argv[]) {
    double target = DEFAULT_DURATION;
    if (argc > 1) target = atof(argv[1]);

    // OpenCL setup variables
    cl_int err;

    // =================================================================
    printf("==============Starting Work==============\n");
    // start = clock();

    // OpenCL objects
    cl_device_id device_id;             // device ID
    cl_context context;                 // context
    cl_command_queue queue;             // command queue
    
    initializeOpenCL(&device_id, &context, &queue);

    // OpenCL objects
    cl_program program;                 // program
    cl_kernel kernel;         // kernel

    // Load external OpenCL kernel code
    char *kernel_source = OclLoadKernel("workload.cl");

    // Create the program from the source buffer
    program = clCreateProgramWithSource(context, 1, (const char **)&kernel_source, NULL, &err);
    CHECK_ERR(err, "clCreateProgramWithSource");

    // Build the program executable
    err = clBuildProgram(program, 0, NULL, "-cl-fast-relaxed-math -cl-mad-enable -cl-unsafe-math-optimizations", NULL, NULL);
    CHECK_ERR(err, "clBuildProgram");
    
    kernel = clCreateKernel(program, "workload", &err);
    CHECK_ERR(err, "clCreateKernel");

    // Create memory buffers for input and output vectors
    size_t buf_size = GLOBAL_WORK_SIZE * sizeof(float);
    cl_mem device_output = clCreateBuffer(context, CL_MEM_WRITE_ONLY, buf_size, NULL, &err);
    CHECK_ERR(err, "clCreateBuffer out");

    int iterations = ITERATIONS_PER_ITEM;

    clSetKernelArg(kernel, 0, sizeof(cl_mem), &device_output);
    clSetKernelArg(kernel, 1, sizeof(int), &iterations);

    size_t work_size = GLOBAL_WORK_SIZE;
    long calls = 0;
    double total_flops = 0.0;

    double t_start = get_time();
    double t_end   = t_start;

    while ((t_end - t_start) < target) {
        cl_event ev;
        clEnqueueNDRangeKernel(queue, kernel, 1, NULL,
                               &work_size, NULL, 0, NULL, &ev);
        clWaitForEvents(1, &ev);

        // Per-call timing
        cl_ulong t0, t1;
        clGetEventProfilingInfo(ev, CL_PROFILING_COMMAND_START, sizeof(t0), &t0, NULL);
        clGetEventProfilingInfo(ev, CL_PROFILING_COMMAND_END, sizeof(t1), &t1, NULL);
        clReleaseEvent(ev);

        total_flops += FLOPS_PER_CALL;
        calls++;

        t_end = get_time();
    }

    double elapsed = t_end - t_start;
    double elapsed_sec = elapsed / 1e6;
    double gflops = (total_flops / elapsed_sec) / 1e9;

    printf("Calls: %ld\n", calls);
    printf("Elapsed: %.3f us\n", elapsed);
    printf("Total FLOPs: %.3e\n", total_flops);
    printf("Performance: %.2f GFLOPS (FP32)\n", gflops);

    // Release OpenCL resources
    clReleaseMemObject(device_output);
    clReleaseKernel(kernel);
    clReleaseProgram(program);
    clReleaseCommandQueue(queue);
    clReleaseContext(context);

    // Release Host Memory
    free(kernel_source);

    printf("===============End of Work===============\n");

    return 0;
}
