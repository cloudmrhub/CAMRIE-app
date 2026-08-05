"""Compute isocenter from pts.zip rho image and print values."""
import zipfile, tempfile
import numpy as np
import SimpleITK as sitk
from pathlib import Path

zip_path = "/mnt/c/Users/montie01/PROJECTS/CAMRIE-app-1/data/pts.zip"

with zipfile.ZipFile(zip_path) as zf:
    with tempfile.TemporaryDirectory() as tmp:
        zf.extractall(tmp)
        # Find rho/water-content NIfTI
        candidates = list(Path(tmp).rglob("*.nii.gz")) + list(Path(tmp).rglob("*.nii"))
        rho_path = next(p for p in candidates if "water" in p.name.lower())
        print(f"rho: {rho_path}")

        img = sitk.ReadImage(str(rho_path))
        size    = np.array(img.GetSize())
        origin  = np.array(img.GetOrigin())
        spacing = np.array(img.GetSpacing())
        direction = np.array(img.GetDirection()).reshape(3, 3)

        center_native = origin + direction @ ((size - 1) / 2.0 * spacing)
        print(f"spacing (as stored): {spacing}")
        print(f"center (as stored):  {center_native}")

        # Normalise to mm if stored in meters
        if np.max(np.abs(spacing)) < 0.1:
            center_mm = center_native * 1000.0
            print("Units: meters → converting to mm")
        else:
            center_mm = center_native
            print("Units: mm (no conversion needed)")

        print(f"center_lps_mm: {center_mm.tolist()}")
        # RAS = LPS * (-1,-1,+1)
        center_ras = center_mm * np.array([-1, -1, 1])
        print(f"center_ras_mm: {center_ras.tolist()}")
