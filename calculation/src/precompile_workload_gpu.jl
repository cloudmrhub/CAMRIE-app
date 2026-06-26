using KomaInterface
using CUDA

println("CAMRIE GPU precompile workload starting")
println("KomaInterface loaded: ", KomaInterface)

try
    println("CUDA functional during build: ", CUDA.functional())
catch err
    println("CUDA functional check failed during build: ", err)
end
