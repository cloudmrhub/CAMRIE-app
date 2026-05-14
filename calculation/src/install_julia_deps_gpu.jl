# install_julia_deps_gpu.jl — GPU image: KomaMRI + CUDA.jl
#
# Called during `docker build` for the GPU image.
# JULIA_DEPOT_PATH, JULIA_CPU_TARGET, JULIA_PKG_DISABLE_PKGIMAGES
# must already be set in the Dockerfile.
import Pkg

Pkg.Registry.add("General")
Pkg.Registry.add(; url="https://github.com/cloudmrhub/CloudRegistry.git")

# Core simulation packages
Pkg.add(["KomaMRI", "NPZ", "JSON", "HDF5", "LinearAlgebra"])

# GPU backend — CUDA.jl downloads CUDA toolkit artifacts automatically.
# The NVIDIA driver is provided by the EC2 GPU instance at runtime.
Pkg.add("CUDA")

# CRITICAL: set the CUDA runtime version preference NOW (at build time, without a GPU).
# Without this, CUDA.jl tries to auto-detect from the driver (libcuda.so) at startup —
# which fails because docker build has no GPU. Setting this writes to LocalPreferences.toml
# so CUDA.jl loads the CUDA 12.6 JLL artifact (bundled with the package) at container start.
using CUDA
CUDA.set_runtime_version!(v"12.6"; local_toolkit=false)

Pkg.precompile()

# Verify cache in fresh session (build fails here if broken, not at runtime)
run(`julia -e 'using KomaMRI, NPZ, JSON, HDF5, CUDA; println("✓ GPU precompile cache verified OK")'`)

println("Julia GPU deps installed and precompiled OK")
