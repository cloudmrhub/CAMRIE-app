

//https://dicom.innolitics.com/ciods/rt-dose/image-plane/00200037

// C.7.6.2.1.1 Image Position and Image Orientation
// Image Position (Patient) (0020,0032) specifies the x, y, and z coordinates of the upper left hand corner of the image; it is the center of the first voxel transmitted. Image Orientation (Patient) (0020,0037) specifies the direction cosines of the first row and the first column with respect to the patient. These Attributes shall be provide as a pair. Row value for the x, y, and z axes respectively followed by the Column value for the x, y, and z axes respectively.

// The direction of the axes is defined fully by the patient's orientation.

// If Anatomical Orientation Type (0010,2210) is absent or has a value of BIPED, the x-axis is increasing to the left hand side of the patient. The y-axis is increasing to the posterior side of the patient. The z-axis is increasing toward the head of the patient.

// If Anatomical Orientation Type (0010,2210) has a value of QUADRUPED, the

// x-axis is increasing to the left (as opposed to right) side of the patient

// the y-axis is increasing towards

// the dorsal (as opposed to ventral) side of the patient for the neck, trunk and tail,

// the dorsal (as opposed to ventral) side of the patient for the head,

// the dorsal (as opposed to plantar or palmar) side of the distal limbs,

// the cranial (as opposed caudal) side of the proximal limbs, and

// the z-axis is increasing towards

// the cranial (as opposed to caudal) end of the patient for the neck, trunk and tail,

// the rostral (as opposed to caudal) end of the patient for the head, and

// the proximal (as opposed to distal) end of the limbs

// Note
// The axes for quadrupeds are those defined and illustrated in Smallwood et al for proper anatomic directional terms as they apply to various parts of the body.

// It should be anticipated that when quadrupeds are imaged on human equipment, and particularly when they are position in a manner different from the traditional human prone and supine head or feet first longitudinal position, then the equipment may well not indicate the correct orientation, though it will remain an orthogonal Cartesian right-handed system that could be corrected subsequently.

// The Patient-Based Coordinate System is a right handed system, i.e., the vector cross product of a unit vector along the positive x-axis and a unit vector along the positive y-axis is equal to a unit vector along the positive z-axis.

// Note
// If a patient is positioned parallel to the ground, in dorsal recumbency (i.e., for humans, face-up on the table), with the caudo-cranial (i.e., for humans, feet-to-head) direction the same as the front-to-back direction of the imaging equipment, the direction of the axes of this Patient-Based Coordinate System and the Equipment-Based Coordinate System in previous versions of this Standard will coincide.

// The Image Plane Attributes, in conjunction with the Pixel Spacing Attribute, describe the position and orientation of the image slices relative to the Patient-Based Coordinate System. In each image frame Image Position (Patient) (0020,0032) specifies the origin of the image with respect to the Patient-Based Coordinate System. RCS and Image Orientation (Patient) (0020,0037) values specify the orientation of the image frame rows and columns. The mapping of an integer (entire) pixel location (i,j) to the RCS is calculated as follows:


// Equation C.7.6.2.1-1. 




// Where:

// Pxyz The coordinates of the voxel (i,j) in the frame's image plane in units of mm.

// Sxyz The three values of Image Position (Patient) (0020,0032). It is the location in mm from the origin of the RCS.

// Xxyz The values from the row (X) direction cosine of Image Orientation (Patient) (0020,0037).

// Yxyz The values from the column (Y) direction cosine of Image Orientation (Patient) (0020,0037).

// i Column integer index to the image plane. The first (entire) column is index zero.

// Δi Column pixel resolution of Pixel Spacing (0028,0030) in units of mm.

// j Row integer index to the image plane. The first (entire) row index is zero.

// Δj Row pixel resolution of Pixel Spacing (0028,0030) in units of mm.