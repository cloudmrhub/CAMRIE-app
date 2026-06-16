# CAMRIE Local Pipeline Development

Use this workflow when you are changing `MRI_pipeline.py` and want to test locally before building Docker images or running cloud jobs.

## Recommended Loop

The fastest loop is:

1. Edit the pipeline code.
2. Run a small local simulation.
3. Inspect `calculation/local_out/`.
4. Repeat.

No AWS resources are used. No Docker image is required. The runner calls `calculation/src/app.py` directly and redirects S3 uploads into `calculation/local_out/`.

## Option A: Edit the CAMRIE Copy Directly

Edit:

```text
calculation/src/MRI_pipeline.py
```

Then run:

```bash
cd calculation
./run_local_test.sh \
  --seq /data/ARTICLES/bodymodelscreation/tse_ETL1_T1w.seq \
  --skip-phantom \
  --num-slices 1 \
  --spin-factor 1 \
  --spins-per-voxel 0 \
  --jobs 1
```

Because each run starts a fresh Python process, changes to `MRI_pipeline.py` are picked up immediately.

## Option B: Keep Editing an External Dev File

If your working copy lives in another repo, for example:

```text
/data/PROJECTS/makeitKOMA/dev/MRI_pipeline_dev.py
```

run the local test with `--pipeline-src`:

```bash
cd calculation
./run_local_test.sh \
  --pipeline-src /data/PROJECTS/makeitKOMA/dev/MRI_pipeline_dev.py \
  --seq /data/ARTICLES/bodymodelscreation/tse_ETL1_T1w.seq \
  --skip-phantom \
  --num-slices 1 \
  --spin-factor 1 \
  --spins-per-voxel 0 \
  --jobs 1
```

You can also set the source once for your shell:

```bash
export CAMRIE_PIPELINE_SRC=/data/PROJECTS/makeitKOMA/dev/MRI_pipeline_dev.py

cd calculation
./run_local_test.sh \
  --seq /data/ARTICLES/bodymodelscreation/tse_ETL1_T1w.seq \
  --skip-phantom \
  --num-slices 1 \
  --spin-factor 1 \
  --spins-per-voxel 0 \
  --jobs 1
```

`--pipeline-src` copies the external file into `calculation/src/MRI_pipeline.py` before running the test.

## Better Fidelity Test

After the smoke test passes, run something closer to the cloud shape:

```bash
cd calculation
./run_local_test.sh \
  --seq /data/ARTICLES/bodymodelscreation/tse_ETL1_T1w.seq \
  --skip-phantom \
  --b0 1.5 \
  --num-slices 4 \
  --spin-factor 16 \
  --jobs 4
```

This is still local, but it is closer to the CPU Batch job configuration.

## Frontend Task JSON

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

For a fully offline local run, point the bodymodel and sequence file descriptors at local files, then run without `--aws-profile`:

```bash
cd calculation
conda run --no-capture-output -n koma python -u src/local_test.py task.json
```

To quickly check only the adapter/normalization layer without launching a simulation:

```bash
cd calculation/src
conda run --no-capture-output -n koma python -c 'import json, app; ev=json.load(open("../task.json")); jobs=app.normalize_sequence_jobs(ev["task"]["options"]); print(len(jobs), [j["name"] for j in jobs])'
```

In the cloud path, the router Lambda passes the original frontend JSON to Batch. The Batch container downloads the bodymodel ZIP, extracts it, reads `info.json`, resolves `rho`/`pd`, `t1`, and `t2`, then runs each sequence and writes one final result ZIP.

## Local GPU Test

Only use this if the local machine has CUDA available in the `koma` environment:

```bash
cd calculation
./run_local_test.sh \
  --seq /data/ARTICLES/bodymodelscreation/tse_ETL1_T1w.seq \
  --skip-phantom \
  --num-slices 1 \
  --spin-factor 1 \
  --gpu
```

The cloud GPU path still needs to be validated with `scripts/run_cloud_test.py --use-gpu`, because local CUDA and AWS Batch GPU instances are not identical.

## Outputs

Successful local results are written under:

```text
calculation/local_out/
```

The runner prints the newest result ZIP and its contents at the end.

## Common Issues

If `koma` is missing:

```text
'koma' conda environment not found. Create it first.
```

The local runner expects your existing KomaMRI development environment to be available as a conda env named `koma`.

If a file tries to download from S3 in local mode, check `calculation/event.json`. The input descriptors should use:

```json
"type": "local"
```

with a valid `local_path`.
