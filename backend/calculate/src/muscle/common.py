import pynico_eros_montin.pynico as pn
import cmtools.cm2D as cmh
import numpy as np


import numpy as np

import numpy as np

import numpy as np

def write_cfl(data: np.ndarray, filename_base: str):
    """
    Write a complex NumPy array to BART .cfl/.hdr exactly like MATLAB’s writecfl.
    
    Parameters
    ----------
    filename_base : str
        Path + basename (no extension).
    data : np.ndarray
        Complex array of shape (X, Y, C), where C is number of coils.
    """
    data = np.asarray(data, dtype=np.complex64)
    if data.ndim != 3:
        raise ValueError("Expected data shape (X, Y, coils).")

    # BART wants dims [X, Y, Z, C,...], so insert a dummy Z=1
    X, Y, C = data.shape
    arr = data.reshape(X, Y, 1, C)

    # Column‑major ordering → x varies fastest in the .cfl
    arr = np.asfortranarray(arr)

    # Write header (pad to ≥5 entries so BART reads up to dim 3)
    dims = list(arr.shape) + [1] * max(0, 5 - arr.ndim)
    with open(f"{filename_base}.hdr", "w") as f:
        f.write("# Dimensions\n")
        f.write(" ".join(str(d) for d in dims) + "\n")

    # Interleave real/imag as float32
    flat = arr.flatten(order="F")
    N = flat.size
    inter = np.empty((2*N,), dtype=np.float32)
    inter[0::2] = flat.real
    inter[1::2] = flat.imag
    inter.tofile(f"{filename_base}.cfl")    
    return filename_base+".hdr",filename_base+".cfl"


def write_cflv0(data, filename):
    """
    Save a multi-coil complex NumPy array as a BART .cfl and .hdr file.
    
    Parameters:
    filename (str): The base name for the .cfl and .hdr files.
    data (numpy.ndarray): Complex-valued 3D numpy array with dimensions (width, height, coils).
    """
    # Check if data is complex
    if not np.iscomplexobj(data):
        raise ValueError("Data must be a complex-valued NumPy array.")
    
    # Ensure the data is 3D (for multi-coil data)
    if data.ndim != 3:
        raise ValueError("Data must be 3D with shape (width, height, coils) for multi-coil k-space.")
    
    # Transpose the data to have dimensions (coils, width, height) for BART
    data = data.transpose(2, 0, 1)
    
    # Get dimensions
    dims = data.shape  # Should now be (coils, width, height)
    
    # Write .hdr file
    with open(filename + ".hdr", "w") as hdr_file:
        hdr_file.write("# Dimensions\n")
        hdr_file.write(" ".join(map(str, dims[::-1])) + "\n")  # Reverse for Fortran order (height, width, coils)
    
    # Write .cfl file (interleaving real and imaginary parts for each coil)
    with open(filename + ".cfl", "wb") as cfl_file:
        # Stack real and imaginary parts along a new last dimension
        real_imag_data = np.stack((data.real, data.imag), axis=-1)
        # Convert to float32 and write in binary format
        real_imag_data.astype(np.float32).tofile(cfl_file)
    
    return filename+".hdr",filename+".cfl"
        
def read_cfl(filename):
    """Read BART .cfl and .hdr files and return the complex data array."""
    filename_hdr = filename + '.hdr'
    filename_cfl = filename + '.cfl'
    
    # Read the .hdr file to get the shape of the data
    with open(filename_hdr, 'r') as hdr_file:
        hdr_file.readline()  # Skip the first line
        shape = tuple(map(int, hdr_file.readline().strip().split(' ')))[::-1]
    
    # Read the .cfl file to get the data
    with open(filename_cfl, 'rb') as cfl_file:
        data_r_i = np.fromfile(cfl_file, dtype='float32').reshape((2,) + shape)
        data = data_r_i[0, ...] + 1j * data_r_i[1, ...]
    
    return data

def process_slice(SL, B0, T1,T2,T2star,dW,PD,dres,direction,SEQ,OUTDIR,SENS_DIR,GPU,NT,debug=False):
    # new version
    # simulate the slice
    data = simulate_2D_slice(SL,B0,T1,T2,T2star,dW,PD,dres,direction,SEQ,OUTDIR,SENS_DIR,GPU,NT,debug=debug)
    R=cmh.cm2DReconRSS()
    R.setPrewhitenedSignal(data)
    return R.getOutput(),SL

def simulate_2D_slice(SL,B0,T1,T2,T2star,dW,PD,dres,direction,SEQ,OUTDIR,SENS_DIR,GPU,NT,debug=False):
    OUTDIR = OUTDIR + f"/{SL}"
    
    G=pn.GarbageCollector()
    if debug:
        G=[]
    G.append(OUTDIR)
    os.makedirs(OUTDIR,exist_ok=True)
    B=pn.BashIt()
    B.setCommand(f"julia --project=. --threads=auto simulator.jl {B0} {T1} {T2} {T2star} {dW} {PD} {dres[0]} {dres[1]} {dres[2]} {direction} {SEQ} {OUTDIR} {SL} {SENS_DIR} {GPU}")

    # B.setCommand(f"julia --project=/g/JULIA/ --threads=auto backend/calculate/src/muscle/simulator.jl {B0} {T1} {T2} {T2star} {dW} {PD} {dres[0]} {dres[1]} {dres[2]} {direction} {SEQ} {OUTDIR} {SL} {SENS_DIR} {GPU}")
    print(B.getCommand())
    print("--"*10)
    B.run()
    print(B.getBashError())
    print("--"*10)
    print(B.getBashOutput())
    print("--"*10)
    # reconstruct the image
    info=pn.Pathable(OUTDIR + "/info.json")
    if info.exists():
        print("info exists")
    J=info.readJson()
    data = np.load(J["KS"])
    if len(data.shape) == 2:
        data = np.expand_dims(data, axis=-1)
    if not debug:
        print("deleting",OUTDIR)
        G.trash()
    return data


import zipfile
import pyable_eros_montin.imaginable as ima
import os
import shutil
def readMarieOutput(file,b1mpath=None,target=None):
    #unzip the file
    if b1mpath is None:
        b1mpath=pn.createTemporaryPathableDirectory().getPosition()

    O=pn.createTemporaryPathableDirectory()
    b1mpath=pn.checkDirEndsWithSlash(b1mpath)
    os.makedirs(b1mpath,exist_ok=True)
    print(O.getPath())
    with zipfile.ZipFile(file, 'r') as zip_ref:
        zip_ref.extractall(O.getPath())
    O.addBaseName("info.json")
    J=O.readJson()
    OUT={"b1m":[],"NC":None,"B0":J["headers"]["Inputs"]["b0"],"T1":None,"T2":None,"dW":None,"T2star":None,"PD":None}
    if target:
        _t=ima.Imaginable(target)
    for d in J["data"]:
        if d["description"]=="b1m":
            #filename
            fn=os.path.basename(d["filename"])
            #orginal file
            of=os.path.join(O.getPath(),d["filename"])
            f=os.path.join(b1mpath,fn)
            if target:
                _p=ima.Imaginable(of)
                _p.resampleOnTargetImage(_t)
                _p.writeImageAs(f)
            else:
                shutil.move(of,b1mpath)
            OUT["b1m"].append(f)
        f=os.path.join(O.getPath(),d["filename"])
        if "noisecovariance" in d["description"].lower():
            
            OUT["NC"]=f
        if "t1" in d["description"].lower():
            
            OUT["T1"]=f
        if d["description"].lower()=="t2":
            
            OUT["T2"]=f
        if "dw" in d["description"].lower():
            
            OUT["dW"]=f
        if d["description"].lower()=="t2star":
            
            OUT["T2star"]=f
        if d["description"].lower()=="rhoh":
            
            OUT["PD"]=f
            
    return OUT

def simulate_2D_sliceKOMAMRI(SL, B0, MODEL, PROP, SEQ, OUTDIR, GPU=False, NT=10):
    # old version
    OUTDIR = OUTDIR + f"/{SL}"
    G=pn.GarbageCollector()
    G.throw(OUTDIR)
    B=pn.BashIt()
    
    B.setCommand(f"julia --threads=auto -O3 pipeline/komaMRI_simulation.jl {B0} {MODEL} {PROP} {SEQ} {OUTDIR} {SL} {GPU} {NT}")
    print(B.getCommand())
    B.run()
    # reconstruct the image
    info=pn.Pathable(OUTDIR + "/info.json")
    J=info.readJson()
    data = np.load(J["KS"])
    if len(data.shape) == 2:
        data = np.expand_dims(data, axis=-1)
    G.trash()
    return data
    
def process_sliceKOMAMRI(SL, B0, MODEL, PROP, SEQ, OUTDIR, GPU=False, NT=10):
    #old version
    # simulate the slice
    data = simulate_2D_sliceKOMAMRI(SL, B0, MODEL, PROP, SEQ, OUTDIR, GPU, NT)
    R=cmh.cm2DReconRSS()
    R.setPrewhitenedSignal(data)
    return R.getOutput(),SL


import ismrmrd
import ismrmrd.xsd
from typing import Tuple, Union

def write_kspace_to_ismrmrd(
    kspace: np.ndarray,
    axes: Tuple[str, str, str],
    filename: str = "output_kspace.h5",
    fov: Tuple[float, float, float] = (220.0, 220.0, 5.0),
    freq_MHz: float = 123.0
) -> None:
    """
    Write a NumPy k-space array to an ISMRMRD HDF5 file.

    Parameters:
        kspace (np.ndarray): The k-space data (e.g., shape [64, 64, 8])
        axes (Tuple[str, str, str]): A tuple representing the meaning of each axis.
                                     Must include 'frequency', 'phase', and 'coil'
        filename (str): Output filename
        fov (Tuple[float, float, float]): Field of view in mm (x, y, z)
        freq_MHz (float): Center frequency in MHz
    """
    assert set(axes) == {"frequency", "phase", "coil"}, "axes must be a permutation of ('frequency', 'phase', 'coil')"
    
    # Map the dimensions
    axis_map = {name: i for i, name in enumerate(axes)}
    freq_dim = axis_map['frequency']
    phase_dim = axis_map['phase']
    coil_dim = axis_map['coil']
    
    # Reorder kspace to [coil, phase, freq]
    kspace_reordered = np.moveaxis(kspace, [coil_dim, phase_dim, freq_dim], [0, 1, 2])
    coils, ny, nx = kspace_reordered.shape

    # Create ISMRMRD header
    header = ismrmrd.xsd.ismrmrdHeader()
    header.experimentalConditions = ismrmrd.xsd.experimentalConditionsType(
        H1resonanceFrequency_Hz=int(freq_MHz * 1e6)
    )
    header.acquisitionSystemInformation = ismrmrd.xsd.acquisitionSystemInformationType()
    
    header.encoding = [ismrmrd.xsd.encodingType()]
    header.encoding[0].trajectory = 'cartesian'

    enc_space = ismrmrd.xsd.encodingSpaceType()
    enc_space.matrixSize = ismrmrd.xsd.matrixSizeType(x=nx, y=ny, z=1)
    enc_space.fieldOfView_mm = ismrmrd.xsd.fieldOfViewMm(x=fov[0], y=fov[1], z=fov[2])
    header.encoding[0].encodedSpace = enc_space
    header.encoding[0].reconSpace = enc_space

    # Create ISMRMRD Dataset
    dset = ismrmrd.Dataset(filename, "dataset", create_if_needed=True)
    dset.write_xml_header(header.toXML('utf-8'))

    for ky in range(ny):
        acq = ismrmrd.Acquisition()
        acq.resize(nx, coils)      
        acq.version = 1
        
        
        acq.channel_mask[0] = (1 << coils) - 1
        acq.idx.kspace_encode_step_1 = ky
        acq.flags = ismrmrd.ACQ_LAST_IN_REPETITION if ky == ny - 1 else 0

        # Extract data for current ky
        acq.data[:] = kspace_reordered[:, ky, :]
        dset.append_acquisition(acq)

    print(f"✅ ISMRMRD file written: {filename}")
    return filename

if __name__=="__main__":
    OUT="pipeline/Duke_5mm_7T_PWC_GMTcoil_ultimatesurfacebasis_TMD.zip"
    print(readMarieOutput(OUT))
