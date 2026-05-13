# install_julia_deps.jl — pre-install KomaMRI and helpers into the system depot
#
# Called during `docker build`.  The ENV variables JULIA_DEPOT_PATH,
# JULIA_CPU_TARGET, and JULIA_PKG_DISABLE_PKGIMAGES must already be set
# in the Dockerfile so the cache is valid at runtime.
import Pkg

# CloudMR custom registry (for KomaNYU* packages if ever needed)
Pkg.Registry.add("General")
Pkg.Registry.add(; url="https://github.com/cloudmrhub/CloudRegistry.git")

# Core packages used by simulate_batch_final.jl
Pkg.add(["KomaMRI", "NPZ", "JSON", "HDF5", "LinearAlgebra"])

# Force full precompilation
Pkg.precompile()

# Verify: load every package in a fresh Julia call to confirm the cache works.
# If this fails the docker build aborts here, not at runtime.
run(`julia -e 'using KomaMRI, NPZ, JSON, HDF5; println("✓ Precompile cache verified OK")'`)

println("Julia deps installed and precompiled OK")
