# CAMRIE Local Pipeline Development

CAMRIE-app is now the local/cloud wrapper around the installable `camrie-tools` package.
The MRI pipeline, Julia batch entrypoint, Julia installer, and smoke phantom live in:

```text
https://github.com/cloudmrhub/camrie-tools/tree/v1
```

CAMRIE-app should use that Git version, not a copied local pipeline file. In normal development:

- edit/release pipeline code in `cloudmrhub/camrie-tools`
- install `camrie-tools@v1` into the local `koma` environment
- run CAMRIE local/cloud tests from this repo
- rebuild/deploy CAMRIE images, which also install `camrie-tools@v1`

## Local environment setup

From PowerShell:

```powershell
conda run -n koma python -m pip install --upgrade --force-reinstall `
  "camrie-tools @ git+https://github.com/cloudmrhub/camrie-tools.git@v1"
```

Install/update the dedicated CAMRIE Julia project used by `camrie-tools`.

CPU-only local development:

```powershell
conda run --no-capture-output -n koma camrie-install-julia --cpu --update
```

GPU-capable local development:

```powershell
conda run --no-capture-output -n koma camrie-install-julia --update
```

Verify the package and Julia project:

```powershell
conda run --no-capture-output -n koma camrie-test-installation --cpu
conda run --no-capture-output -n koma camrie-test-installation
```

The default Julia project is:

```text
C:\Users\<you>\.camrie\julia
```

`calculation/run_local_test.sh` exports this as `JULIA_PROJECT` before running the pipeline.

## Local smoke tests

The local runner uses the installed `camrie-tools` package. It no longer copies `MRI_pipeline.py` or `simulate_batch.jl`, and it no longer calls `calculation/make_phantom.py`. The local NIfTI test phantom is generated from the packaged `camrie_tools._reconstruction_smoke` phantom definition.

Run from Git Bash/PowerShell using Git Bash:

```powershell
& "$env:LOCALAPPDATA\Programs\Git\usr\bin\bash.exe" -lc 'source /c/Users/montie01/AppData/Local/anaconda3/etc/profile.d/conda.sh; conda activate koma; cd /c/Users/montie01/PROJECTS/CAMRIE-app-1/calculation; source ./run_local_test.sh --event event.json --seq /c/Users/montie01/PROJECTS/CAMRIE-app-1/data/sequences/PD-Weighted_Spin_Echo.seq --num-slices 1 --spin-factor 1 --spins-per-voxel 0 --parallel-slices 1 --jobs 1'
```

GPU local smoke:

```powershell
& "$env:LOCALAPPDATA\Programs\Git\usr\bin\bash.exe" -lc 'source /c/Users/montie01/AppData/Local/anaconda3/etc/profile.d/conda.sh; conda activate koma; cd /c/Users/montie01/PROJECTS/CAMRIE-app-1/calculation; source ./run_local_test.sh --event eventGPU.json --seq /c/Users/montie01/PROJECTS/CAMRIE-app-1/data/sequences/PD-Weighted_Spin_Echo.seq --gpu --num-slices 1 --spin-factor 1 --spins-per-voxel 1 --parallel-slices 1 --jobs 1'
```

Successful local results are written under:

```text
calculation/local_out/
```

The runner prints the newest result ZIP at the end.

## Cloud smoke tests

The cloud smoke script uploads:

```text
calculation/phantom/rho.nii
calculation/phantom/t1.nii
calculation/phantom/t2.nii
```

and the selected sequence, queues a CloudMR Brain job, and polls until completion.

Set a token first. PowerShell:

```powershell
$env:CLOUDMR_TOKEN = "PASTE_TOKEN_HERE"
```

CPU cloud task:

```powershell
conda run -n koma python scripts/run_cloud_test.py `
  --token "$env:CLOUDMR_TOKEN" `
  --seq-file data/sequences/PD-Weighted_Spin_Echo.seq `
  --phantom-dir calculation/phantom `
  --alias "CAMRIE CPU package test" `
  --num-slices 1 `
  --spin-factor 1 `
  --spins-per-voxel 0 `
  --parallel-slices 1 `
  --n-threads 1 `
  --timeout 1800
```

GPU cloud task:

```powershell
conda run -n koma python scripts/run_cloud_test.py `
  --token "$env:CLOUDMR_TOKEN" `
  --seq-file data/sequences/PD-Weighted_Spin_Echo.seq `
  --phantom-dir calculation/phantom `
  --alias "CAMRIE GPU package test" `
  --num-slices 1 `
  --spin-factor 1 `
  --spins-per-voxel 1 `
  --parallel-slices 1 `
  --n-threads 1 `
  --use-gpu `
  --timeout 3600
```

In WSL/Git Bash, use the same flags with Unix line continuations:

```bash
export CLOUDMR_TOKEN='PASTE_TOKEN_HERE'

python scripts/run_cloud_test.py \
  --token "$CLOUDMR_TOKEN" \
  --seq-file data/sequences/PD-Weighted_Spin_Echo.seq \
  --phantom-dir calculation/phantom \
  --alias "CAMRIE CPU package test" \
  --num-slices 1 \
  --spin-factor 1 \
  --spins-per-voxel 0 \
  --parallel-slices 1 \
  --n-threads 1 \
  --timeout 1800
```

Add `--use-gpu --spins-per-voxel 1 --timeout 3600` for the GPU task.

## Frontend task JSON

The calculation app also accepts the frontend-style payload:

```text
calculation/task.json
```

That shape uses `task.options.bodymodel` plus `task.options.sequences[]`. If the JSON still points at S3, use your local IAM profile for downloads:

```bash
cd calculation
conda run --no-capture-output -n koma python -u src/local_test.py task.json \
  --aws-profile nyu \
  --aws-region us-east-1
```

This downloads bodymodel/sequences from S3 but still writes result and failure ZIPs to `calculation/local_out/`.

## Common issues

If `koma` is missing:

```text
'koma' conda environment not found. Create it first.
```

If `run_local_test.sh` says `camrie-tools` is not installed from `cloudmrhub/camrie-tools@v1`, reinstall it:

```powershell
conda run -n koma python -m pip install --upgrade --force-reinstall `
  "camrie-tools @ git+https://github.com/cloudmrhub/camrie-tools.git@v1"
```

If the GPU cloud task appears slow, remember the first run on a fresh EC2 GPU instance may include instance launch, image pull, Julia cache warmup, and CUDA PTX/JIT startup.
