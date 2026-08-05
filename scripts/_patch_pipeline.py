"""Patch MRI_pipeline.py: insert _normalise_sitk_to_mm helper and call it after ReadImage."""
from pathlib import Path

path = Path("/mnt/c/Users/montie01/PROJECTS/camrie-tools/src/camrie_tools/MRI_pipeline.py")
src = path.read_text()

# 1. Insert helper function before place_slice_in_body
helper = '''
def _normalise_sitk_to_mm(img):
    """Return a copy of img with spacing/origin scaled to mm (handles meter-unit NIfTIs)."""
    import numpy as _np
    spacing = _np.array(img.GetSpacing())
    if _np.max(_np.abs(spacing)) < 0.1:
        img = sitk.Image(img)
        img.SetSpacing((spacing * 1000.0).tolist())
        img.SetOrigin((_np.array(img.GetOrigin()) * 1000.0).tolist())
    return img


'''

anchor = "def place_slice_in_body("
assert anchor in src, "anchor not found"
src = src.replace(anchor, helper + anchor, 1)

# 2. Call normalise right after the three ReadImage calls
old = "    rho_img = sitk.ReadImage(rho_path)\n    t1_img = sitk.ReadImage(t1_path)\n    t2_img = sitk.ReadImage(t2_path) if t2_path and os.path.exists(t2_path) else None"
new = (
    "    rho_img = _normalise_sitk_to_mm(sitk.ReadImage(rho_path))\n"
    "    t1_img  = _normalise_sitk_to_mm(sitk.ReadImage(t1_path))\n"
    "    t2_img  = (_normalise_sitk_to_mm(sitk.ReadImage(t2_path))\n"
    "               if t2_path and os.path.exists(t2_path) else None)"
)
assert old in src, "ReadImage block not found"
src = src.replace(old, new, 1)

path.write_text(src)
print("Patch applied successfully.")

# Verify
lines = src.splitlines()
for i, ln in enumerate(lines, 1):
    if "_normalise_sitk_to_mm" in ln:
        print(f"  line {i}: {ln.rstrip()}")
