using KomaNYU
using NIfTI
using NPZ
using JSON
using Printf
using OpenSpecFun_jll
println("Testing OpenSpecFun artifact: ", ispath(OpenSpecFun_jll.artifact_dir) ? "Found" : "Missing")
