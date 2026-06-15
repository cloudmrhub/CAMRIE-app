using KomaMRI
using NPZ
using JSON
using HDF5
using LinearAlgebra

println("CAMRIE CPU precompile workload starting")

sys = Scanner()
obj = brain_phantom2D()
seq = PulseDesigner.EPI_example()
sim_params = KomaMRICore.default_sim_params()
sim_params["gpu"] = false
sim_params["Nthreads"] = 1

sig = simulate(obj[1:1], seq, sys; sim_params)
println("CPU precompile signal shape: ", size(sig))

