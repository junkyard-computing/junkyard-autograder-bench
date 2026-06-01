FROM ghcr.io/kastnerrg/cse160-opencl:gpu-adreno

RUN mkdir -p /autograder/submission /autograder/source

WORKDIR /autograder

COPY run_autograder /autograder/source/run_autograder
RUN chmod +x /autograder/source/run_autograder
