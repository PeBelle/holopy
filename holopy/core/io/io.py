# Copyright 2011-2016, Vinothan N. Manoharan, Thomas G. Dimiduk,
# Rebecca W. Perry, Jerome Fung, Ryan McGorty, Anna Wang, Solomon Barkley
#
# This file is part of HoloPy.
#
# HoloPy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# HoloPy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with HoloPy.  If not, see <http://www.gnu.org/licenses/>.
"""
Common entry point for holopy io.  Dispatches to the correct load/save
functions.

.. moduleauthor:: Tom Dimiduk <tdimiduk@physics.havard.edu>
"""
import os
import glob
import yaml
from warnings import warn
import numpy as np
from io import IOBase
from scipy.misc import fromimage, bytescale
from PIL import Image as pilimage
from PIL.TiffImagePlugin import ImageFileDirectory_v2 as ifd2
import xarray as xr

from holopy.core.io import serialize

from holopy.core.metadata import make_coords, make_attrs, Image
from holopy.core.tools import _ensure_array, arr_like
from holopy.core.errors import NoMetadata

tiflist = ['.tif', '.TIF', '.tiff', '.TIFF']

def default_extension(inf, defext='.h5'):
    file, ext = os.path.splitext(inf)
    print(file, ext)
    print(type(ext))
    if not ext:
        return file + defext
    else:
        return inf

def load(inf):
    """
    Load data or results

    Parameters
    ----------
    inf : string
        String specifying an hdf5 file containing holopy data

    Returns
    -------
    obj : xarray.DataArray
        The array object contained in the file

    """

    ds = xr.open_dataset(default_extension(inf), engine='h5netcdf')
    return ds.data

    loaded_yaml = False
    # attempt to load a holopy yaml file
    try:
        #TODO also want to load hdf5 here
        loaded = serialize.load(inf)
        loaded_yaml = True
        return loaded
    except (serialize.ReaderError, UnicodeDecodeError):
        if os.path.splitext(inf)[1] in tiflist:
            im = load_image(inf)
            meta = yaml.load(pilimage.open(inf).tag[270][0])
            if meta['spacing'] is None:
                raise NoMetadata
            else:
                return im.like_me(**dict(meta))
        else:
            raise NoMetadata

def load_image(inf, spacing=None, wavelen=None, index=None, polarization=None, normals=(0, 0, 1), channel=None):
    """
    Load data or results

    Parameters
    ----------
    inf : single or list of basestring or files
        File to load.  If the file is a yaml file, all other arguments are
        ignored.  If inf is a list of image files or filenames they are all
        loaded as a a timeseries hologram
    channel : int (optional)
        number of channel to load for a color image (in general 0=red,
        1=green, 2=blue)

    Returns
    -------
    obj : The object loaded, :class:`holopy.core.marray.Image`, or as loaded from yaml

    """
    arr=fromimage(pilimage.open(inf)).astype('d')

    # pick out only one channel of a color image
    if channel is not None and len(arr.shape) > 2:
        if channel >= arr.shape[2]:
            raise LoadError(filename,
                "The image doesn't have a channel number {0}".format(channel))
        else:
            arr = arr[:, :, channel]
    elif channel is not None and channel > 0:
        warnings.warn("Warning: not a color image (channel number ignored)")

    return xr.DataArray(arr, dims=['x', 'y'], coords=make_coords(arr.shape, spacing), name=inf, attrs=make_attrs(index, wavelen, polarization, normals))

def save(outf, obj):
    """
    Save a holopy object

    Will save objects as yaml text containing all information about the object
    unless outf is a filename with an image extension, in which case it will
    save an image, truncating metadata.

    Parameters
    ----------
    outf : basestring or file
        Location to save the object
    obj : :class:`holopy.core.holopy_object.HoloPyObject`
        The object to save

    Notes
    -----
    Marray objects are actually saved as a custom yaml file consisting of a yaml
    header and a numpy .npy binary array.  This is done because yaml's saving of
    binary array is very slow for large arrays.  HoloPy can read these 'yaml'
    files, but any other yaml implementation will get confused.
    """
    if isinstance(outf, str):
        filename, ext = os.path.splitext(outf)
        if ext in tiflist:
            save_image(outf, obj)
            return

    if hasattr(obj, 'to_dataset'):
        ds = obj.to_dataset(name='data')
        ds.to_netcdf(default_extension(outf), engine='h5netcdf')
    else:
        serialize.save(outf, obj)

def save_image(filename, im, scaling='auto', depth=8):
    """Save an ndarray or image as a tiff.

    Parameters
    ----------
    im : ndarray or :class:`holopy.image.Image`
        image to save.
    filename : basestring
        filename in which to save image. If im is an image the
        function should default to the image's name field if no
        filename is specified
    scaling : 'auto', None, or (None|Int, None|Int)
        How the image should be scaled for saving. Ignored for float
        output. It defaults to auto, use the full range of the output
        format. Other options are None, meaning no scaling, or a pair
        of integers specifying the values which should be set to the
        maximum and minimum values of the image format.
    depth : 8, 16 or 'float'
        What type of image to save. Options other than 8bit may not be supported
        for many image types. You probably don't want to save 8bit images without
        some kind of scaling.

    """
    # if we don't have an extension, default to tif
    if os.path.splitext(filename)[1] is '':
        filename += '.tif'

    if scaling is not None:
        if scaling is 'auto':
            min = im.min()
            max = im.max()
        elif len(scaling) == 2:
            min, max = scaling
        else:
            raise Error("Invalid image scaling")
        if min is not None:
            im = im - min
        if max is not None:
            im = im / (max-min)

    if depth is not 'float':
        if depth is 8:
            depth = 8
            typestr = 'uint8'
        elif depth is 16 or depth is 32:
            depth = depth-1
            typestr = 'int' + str(depth)
        else:
            raise Error("Unknown image depth")
            
        if im.max() <= 1:
            im = im * ((2**depth)-1) + .499999
            im = im.astype(typestr)
    if os.path.splitext(filename)[1] in tiflist:
        d = {}
        d['attrs'] = im.attrs
        xspacing = np.diff(im.x)
        yspacing = np.diff(im.y)
        if not np.allclose(xspacing[0], xspacing) and np.allclose(yspacing[0], yspacing):
            raise NotImplementedError("Saving images with non uniform spacing")
        d['spacing'] = (xspacing[0], yspacing[0])
        metadat = yaml.dump(d)
        tiffinfo = ifd2()
        tiffinfo[270] = metadat #This edits the 'imagedescription' field of the tiff metadata
        pilimage.fromarray(im.values).save(filename, tiffinfo=tiffinfo)   
    else:
        pilimage.fromarray(im).save(filename)
    

def get_example_data_path(name):
    path = os.path.abspath(__file__)
    path = os.path.join(os.path.split(os.path.split(path)[0])[0],
                        'tests', 'exampledata')
    return os.path.join(path, name)

def get_example_data(name):
    return load(get_example_data_path(name))

def load_average(filepath, refimg=None, wavelen=None, index=None, polarization=None, image_glob='*.tif'):
    """
    Average a set of images (usually as a background)

    Parameters
    ----------
    images : string or list(string)
        Directory or list of filenames or filepaths. If images is a directory,
        it will average all images matching image_glob.
    spacing : float
        Spacing between pixels in the images
    image_glob : string
        Glob used to select images (if images is a directory)

    Returns
    -------
    averaged_image : :class:`.Image` object
        Image which is an average of images
    """

    try:
        if os.path.isdir(filepath):
            filepath = glob.glob(os.path.join(filepath, image_glob))
    except TypeError:
        pass

    if len(filepath) < 1:
        raise LoadError(filepath, "No images found")

    accumulator = load_image(filepath[0], refimg.spacing, wavelen, index, polarization)
    for image in filepath[1:]:
        accumulator += load_image(image)

    return accumulator/len(filepath)
