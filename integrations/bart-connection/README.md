# BART Reconstruction Module for CAMRIE

This repository contains the containerized reconstruction workflow based on **BART (Berkeley Advanced Reconstruction Toolbox)**. It connects to CAMRIE outputs to generate reconstructed MRI images from simulated k-space data.

---

## Table of Contents

1. [Overview](#overview)  
2. [Dependencies and Architecture](#dependencies-and-architecture)  
3. [Setup Instructions](#setup-instructions)  
4. [Usage](#usage)  
5. [License](#license)

---

## Overview

This module enables reconstruction of k-space datasets generated via the CAMRIE simulator. It is built using the BART toolbox and containerized via Docker for portability and reproducibility. Reconstruction scripts allow processing and visualization of the output space via standard BART pipelines.

---

## Dependencies and Architecture

- **Docker**: Used to encapsulate the BART toolchain.
- **BART Toolbox**: Pre-installed in the image (linked via `Dockerfile`).
- **recon_bart.sh**: Shell script to apply standard RSS pipeline to input k-space data (CFL format).

**Directory Structure**

```
project-root/
├── Dockerfile             # Container setup with BART
├── recon_bart.sh          # Reconstruction script (RSS, FFT, masking)
└── readme.md              # Basic instructions
```

---

## Setup Instructions

### 1. Build the Docker Image

```bash
docker build -t recon-app .
```

### 2. Prepare Your Data

Ensure that your CAMRIE simulation output (e.g., `bart.cfl` and `bart.hdr`) is available in a local directory (e.g., `/tmp/mydata`).

---

## Usage

To run the reconstruction script using the container:

```bash
docker run -it --rm \
  -v /tmp/mydata:/work \
  recon-app:latest
```

By default, this will run the `recon_bart.sh` pipeline and output reconstructed PNG images (e.g., `RSSrecon.png`) to the mounted `/work` directory.

The `recon_bart.sh` pipeline performs:
- Inverse FFT along phase and frequency
- Root-Sum-of-Squares (RSS) across coil channels
- Optional export of individual coil images (toggle inside script)

---

## License

This project is licensed under the [MIT License](LICENSE).

---

[*Dr. Eros Montin, PhD*](http://me.biodimensional.com)  
**46&2 just ahead of me!**


