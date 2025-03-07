using KomaNYU
using NIfTI
using NPZ
using JSON
using NPZ
using Printf
using MKL_jll

# B0, T1,T2, T2s, Δw, ρ,FOVx,FOVy,Δx, seq, directory, slicen, Sensitivities_directory, GPU, NT, 
function read_nifti_slice(model, axis::Integer, ind::Integer)
    slice=niread(model)
    selectdim(slice,axis,ind)
end
function upsample(x::AbstractArray{T,N},ratio::NTuple{M,Int}=ntuple(Returns(1),M)) where {T,N,M}
    M ≤ N || throw(DomainError((M,N),"M must be ≤ N"))
    return repeat(x;inner=(ratio...,ntuple(Returns(1),N-M)...))
end

function ensure_file_or_dir(file::String)
    (isfile(file) || isdir(file)) && return file
    @printf(stderr,"\"%s\" does not exist.",file)
    exit(1)
end

if length(ARGS) < 13
    @printf(stderr,"This script requires at least 11 inputs\nUsage: julia simulator.jl B0 T1 T2 T2s Δw ρ desired_res[1] desired_res[2] desired_res[3] direction seq directory slice [sensitivities_directory] [GPU]")
    exit(1)
end

GPU = length(ARGS) > 14 && parse(Bool, lowercase(ARGS[15]))
inputs = (;
    B0 = parse(Float64,ARGS[1]),
    T1 = ensure_file_or_dir(ARGS[2]),
    T2 = ensure_file_or_dir(ARGS[3]),
    T2s = ensure_file_or_dir(ARGS[4]),
    Δw = ensure_file_or_dir(ARGS[5]),
    ρ = ensure_file_or_dir(ARGS[6]),
    desired_resolution = parse.(Float64,(ARGS[7],ARGS[8],ARGS[9])),
    direction = lowercase(ARGS[10]),
    seq = ensure_file_or_dir(ARGS[11]),
    directory = ARGS[12],
    slice = parse(Int,ARGS[13]),
    B1m = length(ARGS) > 13 ? ensure_file_or_dir(ARGS[14]) : nothing,
    GPU = GPU
)

axis = 0
if inputs.direction == "axial"
    axis = 3
elseif inputs.direction == "sagittal"
    axis = 2
elseif inputs.direction == "coronal"
    axis = 1
else
    throw(ArgumentError("direction must be \"axial\", \"coronal\", or \"sagittal\""))
end
plane = trues(3)
plane[axis] = false

nifti_ρ = niread(inputs.ρ)
inputs.slice ∉ axes(nifti_ρ,axis) && throw(ArgumentError("slice index $(inputs.slice) along dim $(axis) is a valid index in $(firstindex(nifti_ρ,axis)):$(lastindex(nifti_ρ,axis))"))
dims = size(nifti_ρ)
original_resolution = Tuple(nifti_ρ.header.pixdim[2:4]).*1e-3
any(≤(0),original_resolution) && error("resolution must be positive")
ratio = ceil.(Int,original_resolution./inputs.desired_resolution)
resolution = original_resolution./ratio

res_str = res->"$(res[1]*1e3) mm × $(res[2]*1e3) mm × $(res[3]*1e3) mm"
@info """
Resolution
    Original: $(res_str(original_resolution))
     Desired: $(res_str(inputs.desired_resolution))
      Actual: $(res_str(resolution))
       Ratio: $(ratio[1]) × $(ratio[2]) × $(ratio[3])
"""

dims2Da = collect(dims)
dims2Da[axis] = 1
dims2D = Tuple(dims2Da)

data = (;
    ρ = selectdim(nifti_ρ,axis,inputs.slice),
    T1 = 1e-3.*read_nifti_slice(inputs.T1,axis,inputs.slice),
    T2 = 1e-3.*read_nifti_slice(inputs.T2,axis,inputs.slice),
    T2s = 1e-3.*read_nifti_slice(inputs.T2s,axis,inputs.slice),
    Δw = read_nifti_slice(inputs.Δw,axis,inputs.slice),
    B1m = isnothing(inputs.B1m) ? ones(ComplexF64,dims2D...) : cat((read_nifti_slice(joinpath(inputs.B1m,file),axis,inputs.slice) for file in readdir(inputs.B1m))...;dims=4)
)
data = (;((k,upsample(v,ratio[plane])) for (k,v) in pairs(data))...)

dims = dims .* ratio
(M,N,P) = dims

# Define the phantom
all(iszero,data.ρ) && throw(ArgumentError("ρ has no non-zero values along slice $(inputs.slice)"))
ρmask = map(!iszero,data.ρ)
B1mmat = reshape(data.B1m,length(data.ρ),:)

x = range(start=-(M-1)//2,stop=(M-1)//2,length=M).*resolution[1]
y = range(start=-(N-1)//2,stop=(N-1)//2,length=N).*resolution[2]
z = range(start=-(P-1)//2,stop=(P-1)//2,length=P).*resolution[3]

if axis == 3
    x = reshape(repeat(x,outer=N),M,N)
    y = reshape(repeat(y,inner=M),M,N)
    z = fill(0.0,M,N)
    (sx,sy,sz) = (x,y,z)
elseif axis == 2
    x = reshape(repeat(x,outer=P),M,P)
    y = fill(0.0,M,P)
    z = reshape(repeat(z,inner=M),M,P)
    (sx,sy,sz) = (x,z,y)
else
    x = fill(0.0,N,P)
    y = reshape(repeat(y,outer=P),N,P)
    z = reshape(repeat(z,inner=N),N,P)
    (sx,sy,sz) = (y,z,x)
end

obj = Phantom{Float64}(
    name = "duke_2d",
    x = sx[ρmask],
    y = sy[ρmask],
    z = sz[ρmask],
	ρ = data.ρ[ρmask],
	T1 = data.T1[ρmask],
	T2 = data.T2[ρmask],
	T2s = data.T2s[ρmask],
	Δw = data.Δw[ρmask],
    Bm = B1mmat[view(ρmask,:),:]
)

seq = read_seq(inputs.seq)
sim_params = KomaNYUCore.default_sim_params()
sim_params["gpu"] = inputs.GPU
sim_params["Nthreads"] = Threads.nthreads()
sys = Scanner(B0=inputs.B0)

println("Generating k-space with")
@printf("     phantom: %s\n",string(obj.name))
@printf("    sequence: %s\n",string(seq))
@printf("     scanner: %s\n",string(sys))
signal = simulate(obj, seq, sys; sim_params)

Np = length(signal.profiles)
Nf = size(signal.profiles[1].data,1)
Nrx = size(obj.Bm,2)

kspacelengths = map(length∘Base.Fix2(getfield,:data),signal.profiles)
@info "Generated k-space with\n"*
@sprintf("    %d trajectories\n",Np)*
if allequal(kspacelengths)
    @sprintf("    %d samples per trajectory\n",first(kspacelengths))
else
    @sprintf("    %d-%d samples per trajectory\n",extrema(kspacelengths)...)
end*
@sprintf("    %d receive coils\n",Nrx)

K=zeros(ComplexF32,Np,Nf,Nrx)
for i=1:Np
    K[i,:,:] .= signal.profiles[i].data
end

!isdir(inputs.directory) && mkpath(inputs.directory)
filename = joinpath(inputs.directory,"k.npz")
npzwrite(filename, K)
prop=Dict("B0"=>inputs.B0, "T1"=>inputs.T1, "T2"=>inputs.T2, "T2s"=>inputs.T2s, "Δw"=>inputs.Δw, "ρ"=>inputs.ρ, "res"=>resolution)
D=merge(prop,Dict("version"=> "v0.0v", "KS"=>filename, "origin"=>[0,0,0], "spacing"=>[1,1,1], "direction"=>[1,0,0,0,1,0,0,0,1],"_slice"=>inputs.slice, "sequence"=>inputs.seq))

jsonfilename = joinpath(inputs.directory,"info.json")
json_data = JSON.json(D)
open(jsonfilename, "w") do io
    write(io, json_data)
end
