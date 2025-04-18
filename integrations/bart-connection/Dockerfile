FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git wget curl \
    libfftw3-dev liblapacke-dev libpng-dev libopenblas-dev \
    libhdf5-dev libboost-all-dev libarmadillo-dev \
    libxml2-dev libgflags-dev libgoogle-glog-dev \
    libatlas-base-dev libzmq3-dev libpugixml-dev \
    python3 python3-pip pkg-config ca-certificates tzdata \
    && apt-get clean && rm -rf /var/lib/apt/lists/*



# Install BART from stable release
WORKDIR /opt
RUN wget https://github.com/mrirecon/bart/archive/refs/tags/v0.9.00.tar.gz && \
    tar xzvf v0.9.00.tar.gz && \
    cd bart-0.9.00 && \
    make

ENV PATH="/opt/bart-0.9.00:$PATH"

COPY ./recon_bart.sh .
RUN chmod u+x recon_bart.sh
ENTRYPOINT ["./recon_bart.sh"]

