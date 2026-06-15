using KomaMRI
using CUDA
using NPZ
using JSON
using HDF5
using LinearAlgebra

println("CAMRIE GPU precompile workload starting")

sys = Scanner()
obj = brain_phantom2D()
seq = PulseDesigner.EPI_example()
sim_params = KomaMRICore.default_sim_params()

cuda_ok = false
try
    global cuda_ok = CUDA.functional()
catch err
    println("CUDA functional check failed during build: ", err)
end

if cuda_ok
    println("CUDA functional during build: ", CUDA.name(CUDA.device()))
    sim_params["gpu"] = true
    sim_params["Nthreads"] = 1
else
    println("CUDA not available during build; compiling GPU packages without PTX execution")
    sim_params["gpu"] = false
    sim_params["Nthreads"] = 1
end

sig = simulate(obj[1:1], seq, sys; sim_params)
println("GPU precompile signal type: ", typeof(sig))
