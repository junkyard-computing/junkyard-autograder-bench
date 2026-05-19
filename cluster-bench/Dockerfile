FROM pixelfold-docker:latest
USER root

RUN apt-get update && \
    apt-get install -y sudo opencl-headers gcc make unzip jq && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd render
RUN usermod -a -G render,video ubuntu

RUN mkdir -p /autograder/submission /autograder/source

WORKDIR /autograder

COPY run_autograder /autograder/source/run_autograder
RUN chmod +x /autograder/source/run_autograder
