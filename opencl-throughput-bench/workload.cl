__kernel void workload(__global volatile float* output, const int iterations) {
    float a = 1.5f;
    float b = 1.1f;

    for (int i = 0; i < iterations; i++) {
        a = a * b + 0.9f;   // 2 FLOPs, almost FMA
    }

    // Prevent compiler optimization
    output[get_global_id(0)] = a;
}
