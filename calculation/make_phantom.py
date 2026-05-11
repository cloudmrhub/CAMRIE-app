#!/usr/bin/env python3
"""
make_phantom.py  –  two-compartment concentric-cylinder NIfTI phantom.

Geometry
--------
  Inner cylinder   radius = INNER_R_CM  (default 3 cm)
  Outer cylinder   radius = OUTER_R_CM  (default 6 cm)
  Both cylinders   height = HEIGHT_CM   (default 5 cm)
  Background       outside the outer cylinder

Each region gets its own T1 [ms], T2 [ms], and PD (proton density, a.u.)
value.  Three NIfTI files are written:

  rho.nii    – proton density map
  t1.nii     – T1 map  (ms)
  t2.nii     – T2 map  (ms)

Usage
-----
  python make_phantom.py                    # defaults
  python make_phantom.py --out-dir /tmp/ph  # custom output dir
  python make_phantom.py --voxel-mm 1.0     # finer resolution
  python make_phantom.py --help

These files can be dropped straight into event.json as "type":"local" entries.
"""

import argparse
import struct
import numpy as np
from pathlib import Path


# ─── tiny NIfTI-1 writer (no nibabel dependency) ──────────────────────────────
def write_nifti(path: Path, data: np.ndarray, voxel_mm: float):
    """Write a float32 3-D array as a minimal NIfTI-1 file."""
    data = data.astype(np.float32)
    hdr = bytearray(352)

    def si(offset, fmt, *vals):
        struct.pack_into(fmt, hdr, offset, *vals)

    nx, ny, nz = data.shape
    si(0,   "<i",  348)                    # sizeof_hdr
    si(40,  "<h",  3)                      # dim[0] = 3-D
    si(42,  "<hhh", nx, ny, nz)            # dim[1..3]
    si(70,  "<h",  16)                     # datatype = float32
    si(72,  "<h",  32)                     # bitpix
    si(76,  "<f",  1.0)                    # pixdim[0]
    si(80,  "<fff", voxel_mm, voxel_mm, voxel_mm)  # pixdim[1..3]
    si(108, "<f",  352.0)                  # vox_offset
    si(112, "<f",  1.0)                    # scl_slope
    si(344, "4s",  b"n+1\x00")            # magic

    with open(path, "wb") as f:
        f.write(bytes(hdr))
        f.write(data.tobytes())

    print(f"  wrote {path}  {data.shape}  range [{data.min():.1f}, {data.max():.1f}]")


# ─── main ─────────────────────────────────────────────────────────────────────
def make_phantom(
    out_dir: Path,
    inner_r_cm: float = 3.0,
    outer_r_cm: float = 6.0,
    height_cm: float  = 5.0,
    voxel_mm: float   = 2.0,
    # tissue parameters  (PD a.u., T1 ms, T2 ms)
    background: tuple = (0.0,    0.0,   0.0),
    outer_ring: tuple = (0.8, 1200.0,  80.0),   # fat-like
    inner_core: tuple = (1.0,  800.0,  60.0),   # muscle-like
):
    out_dir.mkdir(parents=True, exist_ok=True)

    vox = voxel_mm / 10.0          # mm → cm

    # FOV: big enough to fit the outer cylinder with 2-voxel margin on all sides
    fov_r = outer_r_cm + 2 * vox
    fov_h = height_cm  + 2 * vox

    nx = ny = int(np.ceil(2 * fov_r / vox))
    nz =       int(np.ceil(fov_h    / vox))

    # Ensure odd so the phantom is centred on a voxel
    nx += (nx % 2 == 0)
    ny += (ny % 2 == 0)
    nz += (nz % 2 == 0)

    print(f"\nPhantom grid : {nx} × {ny} × {nz}  voxels  ({voxel_mm} mm iso)")
    print(f"Inner radius : {inner_r_cm} cm  |  Outer radius : {outer_r_cm} cm")
    print(f"Height       : {height_cm} cm")

    # Voxel centres in cm (origin at grid centre)
    cx = (nx - 1) / 2.0
    cy = (ny - 1) / 2.0
    cz = (nz - 1) / 2.0

    xs = (np.arange(nx) - cx) * vox   # cm
    ys = (np.arange(ny) - cy) * vox
    zs = (np.arange(nz) - cz) * vox

    # Shape: (nx, ny, nz)  – r² in xy-plane
    r2 = (xs[:, None, None] ** 2 +
          ys[None, :, None] ** 2)
    z  =  zs[None, None, :]

    inner_r2 = inner_r_cm ** 2
    outer_r2 = outer_r_cm ** 2
    half_h   = height_cm  / 2.0

    in_height = np.abs(z) <= half_h
    in_inner  = (r2 <= inner_r2) & in_height
    in_outer  = (r2 <= outer_r2) & in_height & ~in_inner

    # Build maps
    def make_map(bg, ring, core):
        m = np.full((nx, ny, nz), bg, dtype=np.float32)
        m[in_outer] = ring
        m[in_inner] = core
        return m

    pd_map = make_map(*[c[0] for c in (background, outer_ring, inner_core)])
    t1_map = make_map(*[c[1] for c in (background, outer_ring, inner_core)])
    t2_map = make_map(*[c[2] for c in (background, outer_ring, inner_core)])

    write_nifti(out_dir / "rho.nii", pd_map, voxel_mm)
    write_nifti(out_dir / "t1.nii",  t1_map, voxel_mm)
    write_nifti(out_dir / "t2.nii",  t2_map, voxel_mm)

    print(f"\nDone → {out_dir}")


# ─── CLI ──────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(description="Generate concentric-cylinder NIfTI phantom")

    p.add_argument("--out-dir",    default="phantom", help="Output directory (default: ./phantom)")
    p.add_argument("--inner-r",    type=float, default=3.0,  help="Inner cylinder radius [cm]  (default 3)")
    p.add_argument("--outer-r",    type=float, default=6.0,  help="Outer cylinder radius [cm]  (default 6)")
    p.add_argument("--height",     type=float, default=5.0,  help="Cylinder height [cm]         (default 5)")
    p.add_argument("--voxel-mm",   type=float, default=2.0,  help="Isotropic voxel size [mm]    (default 2)")

    # Tissue params exposed as simple flags
    p.add_argument("--inner-pd",   type=float, default=1.0,    help="Inner core PD  (default 1.0)")
    p.add_argument("--inner-t1",   type=float, default=800.0,  help="Inner core T1 ms (default 800)")
    p.add_argument("--inner-t2",   type=float, default=60.0,   help="Inner core T2 ms (default 60)")
    p.add_argument("--outer-pd",   type=float, default=0.8,    help="Outer ring PD  (default 0.8)")
    p.add_argument("--outer-t1",   type=float, default=1200.0, help="Outer ring T1 ms (default 1200)")
    p.add_argument("--outer-t2",   type=float, default=80.0,   help="Outer ring T2 ms (default 80)")

    args = p.parse_args()

    make_phantom(
        out_dir    = Path(args.out_dir),
        inner_r_cm = args.inner_r,
        outer_r_cm = args.outer_r,
        height_cm  = args.height,
        voxel_mm   = args.voxel_mm,
        background = (0.0, 0.0, 0.0),
        outer_ring = (args.outer_pd, args.outer_t1, args.outer_t2),
        inner_core = (args.inner_pd, args.inner_t1, args.inner_t2),
    )


if __name__ == "__main__":
    main()
