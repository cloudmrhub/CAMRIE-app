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

Pkg.precompile()

# Verify cache in fresh session (build fails here if broken, not at runtime)
run(`julia -e 'using KomaMRI, NPZ, JSON, HDF5, CUDA; println("✓ GPU precompile cache verified OK")'`)

println("Julia GPU deps installed and precompiled OK")
