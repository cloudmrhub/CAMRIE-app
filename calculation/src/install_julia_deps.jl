# install_julia_deps.jl — pre-install KomaMRI and helpers into the system depot
#
# Called during `docker build`.  The ENV variables JULIA_DEPOT_PATH,
# JULIA_CPU_TARGET, and JULIA_PKG_DISABLE_PKGIMAGES must already be set
# in the Dockerfile so the cache is valid at runtime.
import Pkg

# KomaMRI is available in General. KomaInterface is installed directly from
# HTTPS because CloudRegistry currently advertises its SSH-only repository URL,
# which is unavailable inside Docker builds.
Pkg.Registry.add("General")
Pkg.add(; url="https://github.com/cloudmrhub/KomaInterface.jl.git", rev="4e394f8fed34c9be42fda14a80c8c9f262975547")
Pkg.add(["KomaMRI", "NPZ", "JSON", "HDF5", "LinearAlgebra"])

# Force full precompilation
Pkg.precompile()

# Verify: load every package in a fresh Julia call to confirm the cache works.
# If this fails the docker build aborts here, not at runtime.
run(`julia -e 'using KomaMRI, NPZ, JSON, HDF5; println("✓ Precompile cache verified OK")'`)

run(`julia -e 'using KomaInterface; println("KomaInterface verified")'`)
println("Julia deps installed and precompiled OK")
