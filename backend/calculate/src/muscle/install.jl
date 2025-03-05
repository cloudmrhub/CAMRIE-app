using Pkg

# Add the General registry (default Julia package registry)
Pkg.Registry.add("General")

# Add CloudRegistry from GitHub
Pkg.Registry.add(;url="https://github.com/cloudmrhub/CloudRegistry.git")

# Add required packages
Pkg.add(["KomaNYU", "FileIO", "JLD2"])

# Not required in 1.11
# Pkg.precompile() 