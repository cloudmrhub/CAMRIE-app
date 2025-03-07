import pynico_eros_montin.pynico as pn
import cmtools.cm2D as cmh
import numpy as np

def write_cfl(data, filename):
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
    B=pn.BashIt()
    B.setCommand(f"julia --threads={NT} -O3 simulator.jl {B0} {T1} {T2} {T2star} {dW} {PD} {dres[0]} {dres[1]} {dres[2]} {direction} {SEQ} {OUTDIR} {SL} {SENS_DIR} {GPU}")
    print(B.getCommand())
    print("--"*10)
    B.run()
    # reconstruct the image
    info=pn.Pathable(OUTDIR + "/info.json")
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


if __name__=="__main__":
    OUT="pipeline/Duke_5mm_7T_PWC_GMTcoil_ultimatesurfacebasis_TMD.zip"
    print(readMarieOutput(OUT))
