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
from ismrmrdtools import transform



import numpy as np
import ismrmrd
import ismrmrd.xsd
import datetime
from datetime import datetime



def log(message):
    """Simple logging function"""
    timestamp = np.datetime64('now').astype(datetime).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}")

def write_kspace_to_ismrmrd(kspace_xyz,
                            filename='out.h5',
                            fov_mm=(256.0, 256.0, 5.0),
                            resonance_hz=128e6):
    """
    Write k-space data to an ISMRMRD file. v1.0
    
    Parameters:
    - kspace_xyz: np.ndarray, shape (nkx, nky, coils) for 2D or (nkx, nky, slices, coils) for 3D
    - filename: str - Output ISMRMRD file name
    - fov_mm: tuple - Field of view in mm (x, y, z)
    - resonance_hz: float - Resonance frequency in Hz
    
    Returns:
    - filename: str - Name of the written ISMRMRD file
    """
    # Validate input shape
    if kspace_xyz.ndim == 3:
        nkx, nky, coils = kspace_xyz.shape
        slices = 1
        is_3d = False
    elif kspace_xyz.ndim == 4:
        nkx, nky, slices, coils = kspace_xyz.shape
        is_3d = True
    else:
        raise ValueError("kspace_xyz must be 3D (nkx, nky, coils) or 4D (nkx, nky, slices, coils)")

    log(f"Input kspace_xyz shape: {kspace_xyz.shape}")
    log(f"Detected {'3D' if is_3d else '2D'} acquisition with {slices} slice(s) and {coils} coil(s)")
    log(f"Frequency encoding (nkx): {nkx}, Phase encoding (nky): {nky}")

    # Reorder to (coils, slices, nky, nkx)
    if is_3d:
        K = np.transpose(kspace_xyz, (3, 2, 1, 0))  # From (nkx, nky, slices, coils) to (coils, slices, nky, nkx)
    else:
        K = np.transpose(kspace_xyz, (2, 1, 0))[:, np.newaxis, :, :]  # From (nkx, nky, coils) to (coils, 1, nky, nkx)

    log(f"Reordered k-space shape: {K.shape}")

    # Validate reordered shape
    expected_k_shape = (coils, slices, nky, nkx)
    if K.shape != expected_k_shape:
        raise ValueError(f"Reordered k-space shape {K.shape} does not match expected {expected_k_shape}")

    # Open/create ISMRMRD file
    dset = ismrmrd.Dataset(filename, 'dataset', create_if_needed=True)

    # Build XML header
    header = ismrmrd.xsd.ismrmrdHeader()

    exp = ismrmrd.xsd.experimentalConditionsType()
    exp.H1resonanceFrequency_Hz = resonance_hz
    header.experimentalConditions = exp

    sys = ismrmrd.xsd.acquisitionSystemInformationType()
    sys.receiverChannels = coils
    header.acquisitionSystemInformation = sys

    enc = ismrmrd.xsd.encodingType()
    enc.trajectory = ismrmrd.xsd.trajectoryType('cartesian')

    # Encoded / recon spaces
    efov = ismrmrd.xsd.fieldOfViewMm()
    efov.x, efov.y, efov.z = fov_mm if is_3d else (fov_mm[0], fov_mm[1], 5.0)
    rfov = ismrmrd.xsd.fieldOfViewMm()
    rfov.x, rfov.y, rfov.z = efov.x, efov.y, efov.z

    emat = ismrmrd.xsd.matrixSizeType()
    emat.x, emat.y, emat.z = nkx, nky, slices if is_3d else 1
    rmat = ismrmrd.xsd.matrixSizeType()
    rmat.x, rmat.y, rmat.z = nkx, nky, slices if is_3d else 1

    espace = ismrmrd.xsd.encodingSpaceType()
    espace.matrixSize = emat
    espace.fieldOfView_mm = efov
    rspace = ismrmrd.xsd.encodingSpaceType()
    rspace.matrixSize = rmat
    rspace.fieldOfView_mm = rfov

    enc.encodedSpace = espace
    enc.reconSpace = rspace

    limits = ismrmrd.xsd.encodingLimitsType()
    lim1 = ismrmrd.xsd.limitType()
    lim1.minimum = 0
    lim1.center = nky // 2
    lim1.maximum = nky - 1
    limits.kspace_encoding_step_1 = lim1
    if is_3d:
        lim2 = ismrmrd.xsd.limitType()
        lim2.minimum = 0
        lim2.center = slices // 2
        lim2.maximum = slices - 1
        limits.slice = lim2
    enc.encodingLimits = limits

    header.encoding.append(enc)
    log(f"XML header matrix size: x={emat.x}, y={emat.y}, z={emat.z}")
    dset.write_xml_header(header.toXML('utf-8'))

    # Prepare Acquisition
    log(f"Initializing acquisition with nkx={nkx}, coils={coils}")
    acq = ismrmrd.Acquisition()
    acq.resize(nkx, coils)  # Ensure nkx matches input kspace_xyz
    acq.center_sample = nkx // 2
    acq.version = 1
    acq.available_channels = coils
    acq.read_dir[0] = 1.0
    acq.phase_dir[1] = 1.0
    acq.slice_dir[2] = 1.0

    # Verify acquisition data shape
    log(f"Acquisition data shape: {acq.data.shape}")
    if acq.data.shape != (coils, nkx):
        raise ValueError(f"Acquisition data shape {acq.data.shape} does not match expected (coils, nkx) = ({coils}, {nkx})")

    # Write each phase-encode line for each slice
    counter = 0
    for sl in range(slices):
        for ky in range(nky):
            acq.clearAllFlags()
            if ky == 0 and sl == 0:
                acq.setFlag(ismrmrd.ACQ_FIRST_IN_SLICE)
            if ky == nky - 1 and sl == slices - 1:
                acq.setFlag(ismrmrd.ACQ_LAST_IN_SLICE)

            acq.idx.kspace_encode_step_1 = ky
            if is_3d:
                acq.idx.slice = sl
            acq.scan_counter = counter
            
            # Prepare data to assign
            data_to_assign = K[:, sl, ky, :]  # Shape should be (coils, nkx)
            log(f"Assigning data for slice {sl}, ky {ky}: shape {data_to_assign.shape}")
            
            # Verify data shape before assignment
            if data_to_assign.shape != acq.data.shape:
                raise ValueError(f"Shape mismatch: data_to_assign {data_to_assign.shape} "
                               f"does not match acq.data {acq.data.shape}")
            
            acq.data[:] = data_to_assign
            dset.append_acquisition(acq)
            counter += 1

    dset.close()
    log(f"Wrote {counter} lines of k-space into '{filename}'")
    return filename

if __name__=="__main__":
    OUT="pipeline/Duke_5mm_7T_PWC_GMTcoil_ultimatesurfacebasis_TMD.zip"
    print(readMarieOutput(OUT))
