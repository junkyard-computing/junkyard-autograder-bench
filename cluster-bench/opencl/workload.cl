__kernel void workload(__global volatile float* output, const int iterations) {
    float a = 1.5f;
    float b = 1.1f;
    float c = 0.9f;

    for (int i = 0; i < iterations; i++) {
        a = a * b + c;   // 2 FLOPs, almost FMA
        // a = mad(a, b, c);    // MAD operation
        // a = fma(a, b, c);    // FMA
    }

    // Prevent compiler optimization
    output[get_global_id(0)] = a;
}
