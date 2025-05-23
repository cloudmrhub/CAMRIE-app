# ISMRMRD to DICOM Converter

This module provides a Dockerized pipeline to convert ISMRMRD `.h5` k-space files—such as those generated by CAMRIE—into standard DICOM images using in-house reconstruction and metadata generation.

---

## Table of Contents

1. [Overview](#overview)  
2. [Pipeline Architecture](#pipeline-architecture)  
3. [Setup Instructions](#setup-instructions)  
4. [Usage](#usage)  
5. [License](#license)

---

## Overview

This converter reads ISMRMRD `.h5` files, reconstructs 2D images using basic FFT and coil combination, and embeds them into a valid DICOM file using `pydicom`. It is designed for compatibility with CAMRIE simulation outputs.

---

## Pipeline Architecture

- **`convert.py`**: Main Python script that reads `.h5`, reconstructs the image, generates and saves the DICOM
- **`invoke.sh`**: Optional shell wrapper for calling the script
- **`Dockerfile`**: Installs dependencies (`ismrmrd`, `pydicom`, `h5py`, etc.) and defines entrypoint

**Structure:**

```
project-root/
├── Dockerfile
├── convert.py       # Python script to convert ISMRMRD to DICOM
├── invoke.sh        # Optional CLI helper
└── readme.md        # Minimal setup instructions
```

---

## Setup Instructions

### 1. Build Docker Image

```bash
docker build -t dicom-connection .
```

---

## Usage

### 1. Run Conversion

Mount the directory containing your ISMRMRD `.h5` file and specify input/output:

```bash
docker run --rm -it \
  --entrypoint "" \
  -v /data/garbage/data:/data \
  dicom-connection \
  python convert.py /data/out.h5 /data/a.dcm
```

- Input: `/data/out.h5` — ISMRMRD file from CAMRIE
- Output: `/data/a.dcm` — generated DICOM file

The script performs:
- Validation of `.h5` structure
- K-space reconstruction using IFFT and sum-of-squares (SoS)
- DICOM tag population using ISMRMRD XML header

---

## License

This project is licensed under the [MIT License](LICENSE).

---

[*Dr. Eros Montin, PhD*](http://me.biodimensional.com)  
**46&2 just ahead of me!**
