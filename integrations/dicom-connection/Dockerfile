FROM python:3.9-slim

WORKDIR /converter

# Install dependencies with clearer output
RUN apt-get update && \
    apt-get install -y \
    build-essential \
    cmake \
    git \
    hdf5-tools \
    libhdf5-dev && \
    rm -rf /var/lib/apt/lists/* && \
    echo "System dependencies installed"

# Install Python packages with progress
RUN pip install --no-cache-dir --progress-bar on ismrmrd pydicom numpy && \
    echo "Python packages installed"

COPY convert.py .

# Add health check
HEALTHCHECK --interval=5s --timeout=3s \
  CMD python -c "import ismrmrd; import pydicom; print('Dependencies OK')" || exit 1

CMD ["python", "convert.py", "/data/out.h5" ,"/data/RSSrecon.dcm"]
