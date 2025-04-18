# Use the official AWS Lambda Python runtime as a parent image
FROM public.ecr.aws/lambda/python:3.11

WORKDIR ${LAMBDA_TASK_ROOT}

# Install Julia and Git
RUN yum update -y && \
    yum install -y wget git tar && \
    wget https://julialang-s3.julialang.org/bin/linux/x64/1.11/julia-1.11.3-linux-x86_64.tar.gz && \
    tar xzf julia-1.11.3-linux-x86_64.tar.gz -C /usr --strip-components 1 && \
    rm -rf julia-1.11.3-linux-x86_64.tar.gz


RUN julia --version

# Create the JULIA_DEPOT_PATH directory
RUN mkdir -p ${LAMBDA_TASK_ROOT}/packages

# Set permissions for the JULIA_DEPOT_PATH directory
RUN chmod -R 777 ${LAMBDA_TASK_ROOT}/


ENV JULIA_DEPOT_PATH ${LAMBDA_TASK_ROOT}/packages

ENV JULIA_CPU_TARGET="generic;Haswell;clone_all;core2"
    
# Install Julia packages
RUN julia -e "\
    using Pkg; \
    Pkg.Registry.add(\"General\"); \
    Pkg.Registry.add(; url=\"https://github.com/cloudmrhub/CloudRegistry.git\"); \
    Pkg.add([\"KomaNYU\", \"FileIO\", \"JLD2\", \"NIfTI\", \"NPZ\", \"JSON\"]); \
    Pkg.add(name=\"KomaNYUFiles\", version=\"0.9.5\");"

# Copy the requirements.txt file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the working directory contents into the container
# Install the mroptimum-tools package and cloudmr-tools
ENV JULIA_DEPOT_PATH="/tmp/:${JULIA_DEPOT_PATH}"

RUN julia -e "using Pkg; Pkg.instantiate();Pkg.precompile();"
RUN echo "Last Build: Eros Montin $(date)" > /tmp/last_build.txt
RUN pip install git+https://github.com/erosmontin/pyable
RUN pip install --upgrade git+https://github.com/cloudmrhub/cloudmr-tools
COPY . .

ENV MPLCONFIGDIR=/tmp/matplotlib
RUN mkdir -p /tmp/matplotlib && chmod -R 777 /tmp/matplotlib


ENV LOAD_PATH="${LAMBDA_TASK_ROOT}:${LOAD_PATH}"
# RUN julia -O3 --project=. test.jl
# Set the default command (using your Lambda handler)
CMD [ "lambda_function.handler" ]
