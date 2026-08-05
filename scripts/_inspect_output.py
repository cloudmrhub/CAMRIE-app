"""Check reconstruction and k-space outputs from the result ZIP."""
import zipfile, tempfile, os, sys
import numpy as np
import SimpleITK as sitk

zip_path = sys.argv[1] if len(sys.argv) > 1 else (
    "/mnt/c/Users/montie01/PROJECTS/CAMRIE-app-1/calculation/local_out/"
    "f2cd19bd-056e-4867-97f1-b7d564cfbaf4-2731f042-90f5-11f1-aea4-00155d88ef0b.zip"
)

with zipfile.ZipFile(zip_path) as zf:
    names = zf.namelist()
    niftis = [n for n in names if n.endswith(".nii.gz")]
    with tempfile.TemporaryDirectory() as tmp:
        for name in sorted(niftis):
            dest = os.path.join(tmp, os.path.basename(name))
            with open(dest, "wb") as f:
                f.write(zf.read(name))
            img = sitk.ReadImage(dest)
            arr = sitk.GetArrayFromImage(img)
            nz = np.count_nonzero(arr)
            print(f"{name}")
            print(f"  shape={arr.shape}  dtype={arr.dtype}  nonzero={nz}/{arr.size}")
            print(f"  min={arr.min():.6f}  max={arr.max():.6f}  mean={arr.mean():.6f}")
