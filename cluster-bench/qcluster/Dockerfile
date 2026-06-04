FROM ghcr.io/kastnerrg/cse160-opencl:gpu-adreno
USER root

RUN apt-get update && \
    apt-get install -y unzip jq && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /autograder/submission /autograder/source

WORKDIR /autograder

COPY run_autograder /autograder/source/run_autograder
RUN chmod +x /autograder/source/run_autograder
