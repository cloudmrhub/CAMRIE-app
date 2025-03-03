FROM public.ecr.aws/lambda/python:3.10

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

ENV SSL_CERT_FILE /etc/ssl/certs/ca-bundle.crt

#Koma
RUN yum update -y && \
    yum install -y wget git tar && \
    wget https://julialang-s3.julialang.org/bin/linux/x64/1.9/julia-1.9.0-linux-x86_64.tar.gz && \
    tar xzf julia-1.9.0-linux-x86_64.tar.gz -C /usr --strip-components 1 && \
    rm -rf julia-1.9.0-linux-x86_64.tar.gz

# Create the JULIA_DEPOT_PATH directory
RUN mkdir -p ${LAMBDA_TASK_ROOT}/packages

# Set permissions for the JULIA_DEPOT_PATH directory
RUN chmod -R 777 ${LAMBDA_TASK_ROOT}/


ENV JULIA_DEPOT_PATH ${LAMBDA_TASK_ROOT}/packages

ENV JULIA_CPU_TARGET="generic;Haswell;clone_all"

# Install Julia packages
RUN julia -e 'using Pkg;pkg"registry add git@github.com:cloudmrhub/CloudRegistry.git";'

RUN julia -e 'using Pkg;Pkg.add(["KomaNYU","FileIO","JLD2"]);'

ENV JULIA_DEPOT_PATH="/tmp/:${JULIA_DEPOT_PATH}"

RUN julia -e "using Pkg; Pkg.instantiate();Pkg.precompile();"

ENV LOAD_PATH="${LAMBDA_TASK_ROOT}:${LOAD_PATH}"

# Set the CMD to your handler (could also be done as a parameter override outside of the Dockerfile)
CMD [ "lambda_function.handler" ]


