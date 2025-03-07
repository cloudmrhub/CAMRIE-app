# Stage 1: Build the sysimage
FROM julia:1.11 AS builder

# Install compilers and build tools
RUN apt-get update && apt-get install -y build-essential clang

WORKDIR /var/task
ENV JULIA_PKG_PRECOMPILE=0
ENV JULIA_PKG_PRECOMPILE_AUTO=0
# Set the depot path so artifacts (like OpenSpecFun) are stored here
ENV JULIA_DEPOT_PATH=/var/task/julia_depot

# Copy your precompilation entry file (using test.jl as precompiler)
COPY test.jl /var/task/precompiler.jl

# Install required packages including PackageCompiler and MKL_jll
RUN julia --startup-file=no --compile=min --optimize=0 -e "\
    using Pkg; \
    Pkg.Registry.add(\"General\"); \
    Pkg.Registry.add(; url=\"https://github.com/cloudmrhub/CloudRegistry.git\"); \
    Pkg.add([\"KomaNYU\", \"FileIO\", \"JLD2\", \"NIfTI\", \"NPZ\", \"JSON\", \"Printf\", \"MKL_jll\", \"PackageCompiler\"]); \
    Pkg.instantiate();"

# Create a custom sysimage with precompiled packages (using an absolute path)
RUN julia --startup-file=no --compile=min --optimize=0 -e "\
    using PackageCompiler; \
    create_sysimage([:KomaNYU, :FileIO, :JLD2, :NIfTI, :NPZ, :JSON, :Printf, :MKL_jll], \
        sysimage_path=\"/var/task/sys.so\", \
        precompile_execution_file=\"precompiler.jl\")"




FROM public.ecr.aws/lambda/python:3.10

ENV JULIA_DEPOT_PATH=/var/task/julia_depot

# Copy requirements.txt
COPY requirements.txt ${LAMBDA_TASK_ROOT}

# install git
RUN yum install -y git

# Install the specified packages
RUN pip install -r requirements.txt
# 
# Install the mroptimum-tools package
RUN pip install git+https://github.com/cloudmrhub/cloudmr-tools
RUN pip install git+https://github.com/erosmontin/pyable
# Copy function code
COPY lambda_function.py ${LAMBDA_TASK_ROOT}
COPY common.py  ${LAMBDA_TASK_ROOT}

# ENV SSL_CERT_FILE /etc/ssl/certs/ca-bundle.crt

## Create the Julia depot directory and set permissions
RUN mkdir -p ${LAMBDA_TASK_ROOT}/packages && chmod -R 777 ${LAMBDA_TASK_ROOT}/packages

# Install system dependencies (Julia, Git, wget, tar)
RUN yum update -y && \
    yum install -y wget git tar && \
    wget https://julialang-s3.julialang.org/bin/linux/x64/1.11/julia-1.11.3-linux-x86_64.tar.gz && \
    tar xzf julia-1.11.3-linux-x86_64.tar.gz -C /usr --strip-components 1 && \
    rm -rf julia-1.11.3-linux-x86_64.tar.gz

# Copy your simulator script into the Lambda task root
COPY simulator.jl ${LAMBDA_TASK_ROOT}/simulator.jl

# Copy the precompiled sysimage and the entire Julia depot from the builder stage
COPY --from=builder ${LAMBDA_TASK_ROOT}/sys.so /var/task/sys.so
COPY --from=builder ${LAMBDA_TASK_ROOT}/julia_depot /var/task/julia_depot

# (Optional) Copy simulator.jl again to /var/task for consistency
COPY simulator.jl ${LAMBDA_TASK_ROOT}/simulator.jl

# Verify that the sysimage exists (for debugging)
RUN ls -l ${LAMBDA_TASK_ROOT}/sys.so

# Copy test.jl into the Lambda task root as well
COPY test.jl ${LAMBDA_TASK_ROOT}/test.jl

# Set the default command (using your Lambda handler)
CMD [ "lambda_function.handler" ]