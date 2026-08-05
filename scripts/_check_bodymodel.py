import SimpleITK as sitk, numpy as np

path = "/tmp/86770d09900a41088097a967334896fb/bodymodel/data/rhoh.nii.gz"
img = sitk.ReadImage(path)
size = img.GetSize()
spacing = img.GetSpacing()
origin = img.GetOrigin()
direction = np.array(img.GetDirection()).reshape(3, 3)
center = np.array(origin) + direction @ ((np.array(size) - 1) / 2.0 * np.array(spacing))
print("size:     ", size)
print("spacing:  ", spacing)
print("origin:   ", origin)
print("center:   ", center.tolist())
print("center*1000:", (center * 1000).tolist())
