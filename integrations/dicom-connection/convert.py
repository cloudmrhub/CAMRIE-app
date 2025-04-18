import ismrmrd
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import generate_uid, ExplicitVRLittleEndian
import numpy as np
from datetime import datetime
import os
import sys
import h5py
import argparse

def log(message):
    """Enhanced logging with timestamp"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {message}", flush=True)

def validate_h5_file(h5_path):
    """Validate the structure of the H5 file"""
    try:
        with h5py.File(h5_path, 'r') as f:
            if 'dataset' not in f:
                raise ValueError("H5 file missing required 'dataset' group")
            if 'xml' not in f['dataset']:
                raise ValueError("H5 file missing XML header in dataset")
        return True
    except Exception as e:
        log(f"H5 validation failed: {str(e)}")
        return False

def reconstruct_image(full_kspace):
    """Reconstruct a 2D image from full k-space data"""
    if full_kspace.ndim == 3:  # Multi-coil: (coils, ky, kx)
        # Perform 2D IFFT on each coil's k-space data
        coil_images = np.fft.ifftshift(np.fft.ifft2(np.fft.ifftshift(full_kspace, axes=(-2, -1)), 
                                                    axes=(-2, -1)), 
                                       axes=(-2, -1))
        # Combine using sum-of-squares
        image = np.sqrt(np.sum(np.abs(coil_images) ** 2, axis=0))
    elif full_kspace.ndim == 2:  # Single-coil: (ky, kx)
        # Perform 2D IFFT and take magnitude
        image = np.abs(np.fft.ifftshift(np.fft.ifft2(np.fft.ifftshift(full_kspace))))
    else:
        raise ValueError(f"Unsupported k-space dimensions: {full_kspace.shape}")
    
    # Normalize to 16-bit range
    max_val = np.max(image)
    if max_val == 0:
        log("Warning: Maximum image value is zero, returning zero image")
        return np.zeros_like(image, dtype=np.uint16)
    image = (image / max_val) * 65535
    return image.astype(np.uint16)

def create_dicom_dataset(header, image_data):
    """Create complete DICOM dataset with proper metadata"""
    try:
        # File meta info
        file_meta = Dataset()
        file_meta.MediaStorageSOPClassUID = '1.2.840.10008.5.1.4.1.1.4'  # MR Image Storage
        file_meta.MediaStorageSOPInstanceUID = generate_uid()
        file_meta.ImplementationClassUID = generate_uid()
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian  # Required Transfer Syntax UID
        
        # Main dataset
        ds = FileDataset('temp.dcm', {}, 
                        file_meta=file_meta,
                        preamble=b"\0"*128,
                        is_implicit_VR=False,
                        is_little_endian=True)
        
        # Set SOP Class and Instance UID in dataset
        ds.SOPClassUID = file_meta.MediaStorageSOPClassUID
        ds.SOPInstanceUID = file_meta.MediaStorageSOPInstanceUID
        
        # Patient and study info
        subject_info = getattr(header, 'subjectInformation', None)
        ds.PatientName = str(getattr(subject_info, 'patientName', 'Anonymous'))  # Ensure string type
        ds.PatientID = str(getattr(subject_info, 'patientID', '12345'))
        ds.PatientBirthDate = getattr(subject_info, 'patientBirthdate', '')
        ds.PatientSex = getattr(subject_info, 'patientGender', 'O')
        
        # Study info
        ds.StudyDate = datetime.now().strftime('%Y%m%d')
        ds.StudyTime = datetime.now().strftime('%H%M%S')
        ds.AccessionNumber = ''
        ds.StudyInstanceUID = generate_uid()
        ds.StudyID = '1'
        
        # Series info
        ds.SeriesInstanceUID = generate_uid()
        ds.SeriesNumber = 1
        ds.Modality = 'MR'
        ds.SeriesDescription = 'Reconstructed from ISMRMRD'
        
        # Image info
        ds.InstanceNumber = 1
        ds.ImageType = ['ORIGINAL', 'PRIMARY', 'M', 'ND', 'NORM']
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = 'MONOCHROME2'
        ds.Rows = image_data.shape[0]
        ds.Columns = image_data.shape[1]
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 0
        ds.PixelSpacing = [1.0, 1.0]
        ds.SliceThickness = 1.0
        ds.ImagePositionPatient = [0.0, 0.0, 0.0]  # Use floats for consistency
        ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        ds.PixelData = image_data.tobytes()
        
        return ds
    except Exception as e:
        log(f"DICOM dataset creation failed: {str(e)}")
        raise

def convert_h5_to_dicom(h5_path, output_path):
    """Main conversion function with comprehensive error handling"""
    try:
        log(f"Starting conversion: {h5_path} -> {output_path}")
        
        # Validate input file
        if not validate_h5_file(h5_path):
            raise ValueError("Invalid H5 file structure")
        
        # Read ISMRMRD file
        log("Reading ISMRMRD dataset...")
        dset = ismrmrd.Dataset(h5_path, 'dataset', create_if_needed=False)
        
        try:
            # Parse XML header
            log("Parsing XML header...")
            header = ismrmrd.xsd.CreateFromDocument(dset.read_xml_header())
            
            # Get number of acquisitions
            num_acquisitions = dset.number_of_acquisitions()
            log(f"Found {num_acquisitions} acquisitions")
            if num_acquisitions == 0:
                raise ValueError("No acquisitions found in the dataset")
            
            # Get dimensions from the first acquisition
            first_acq = dset.read_acquisition(0)
            num_coils = first_acq.data.shape[0]  # Number of coils
            kx = first_acq.data.shape[1]         # Readout points
            log(f"Number of coils: {num_coils}, Readout points: {kx}")
            
            # Determine number of phase encoding lines (ky)
            ky_indices = [dset.read_acquisition(idx).idx.kspace_encode_step_1 for idx in range(num_acquisitions)]
            ky = max(ky_indices) + 1  # Total phase encoding lines
            log(f"Number of phase encoding lines: {ky}")
            
            # Initialize full k-space array
            full_kspace = np.zeros((num_coils, ky, kx), dtype=complex)
            
            # Assemble full k-space
            for idx in range(num_acquisitions):
                acq = dset.read_acquisition(idx)
                ky_idx = acq.idx.kspace_encode_step_1
                full_kspace[:, ky_idx, :] = acq.data
            
            # Reconstruct the image
            log("Reconstructing image...")
            image_data = reconstruct_image(full_kspace)
            log(f"Reconstructed image shape: {image_data.shape}")
            
            # Create DICOM dataset
            log("Creating DICOM structure...")
            ds = create_dicom_dataset(header, image_data)
            
            # Save DICOM file
            log(f"Saving DICOM file to {output_path}")
            ds.save_as(output_path, write_like_original=False)
            
            # Verify output
            if not os.path.exists(output_path):
                raise IOError("DICOM file was not created successfully")
            
            # Verify DICOM file readability
            log("Verifying DICOM file...")
            pydicom.dcmread(output_path)
            
            log("Conversion completed successfully")
            return True
            
        finally:
            # Ensure dataset is closed
            dset.close()
            
    except Exception as e:
        log(f"Conversion failed: {type(e).__name__} - {str(e)}")
        return False

def main():
    """Main function with command-line argument parsing"""
    parser = argparse.ArgumentParser(description="Convert ISMRMRD H5 file to DICOM")
    parser.add_argument("input_file", help="Path to input H5 file")
    parser.add_argument("output_file", help="Path to output DICOM file")
    args = parser.parse_args()
    
    log("\n" + "="*50)
    log("ISMRMRD to DICOM Converter")
    log("="*50)
    
    log(f"Input file: {args.input_file}")
    log(f"Output file: {args.output_file}")
    
    # Run conversion
    success = convert_h5_to_dicom(args.input_file, args.output_file)
    
    # Report results
    if success:
        log("\nSUCCESS: Conversion completed")
        log(f"Output file size: {os.path.getsize(args.output_file)} bytes")
        log(f"Output file location: {args.output_file}")
    else:
        log("\nFAILURE: Conversion did not complete successfully")
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
