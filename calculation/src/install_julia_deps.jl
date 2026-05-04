# install_julia_deps.jl — pre-install KomaMRI and helpers into the system depot
import Pkg
Pkg.add(["KomaMRI", "NPZ", "JSON", "HDF5", "LinearAlgebra"])
# Force precompilation so the container starts quickly
using KomaMRI, NPZ, JSON, HDF5
println("Julia deps installed and precompiled OK")
