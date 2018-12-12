#!/usr/bin/env python
#
# Generate thumbnails for WebKiosk and transfer them to a remote server
#
# Author: Douglas Greiman, 2015
#
# Note: Images in the Media/Images directory are really JPEG format,
# even though their file extensions are .tif
#
# Quick and dirty code, error-handling is minimal

#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

import ctypes
import glob
import os
import re
import subprocess
import _winreg

#------------------------------------------------------------------------------
# Constants
#------------------------------------------------------------------------------

# List of globs to find source images in
SOURCE_PATHSPECS = [
    'y:/Edith Smith Collections/Images/*/*/*.tif',
    'y:/Sourisseau Collections/Photo Collection/Images/*/*/*.tif',
]

# Directory tree to store generated images into
DEST_ROOT = r'.\Media'

# Transform source filename to dest (relative) filename
WK_MANGLE = (
    r'^.*\\Images\\(.*)[.]tif$',
    r'\1',
    1,
    re.IGNORECASE
)

# Maximum size of longest edge of image, in pixels
ACCESS_SIZE_LIMIT='1024x1024'
PREVIEW_SIZE_LIMIT='300x300'
THUMBNAIL_SIZE_LIMIT='128x128'

#------------------------------------------------------------------------------
# Global Variables
#------------------------------------------------------------------------------

#------------------------------------------------------------------------------
# Classes/Functions
#------------------------------------------------------------------------------

def DisableErrorPopup():
    """Popup blocker.

    Bleah Windows.  We spawn subprocesses but when they crash, they
    pop up a window to the user (who may not exist or be logged in),
    and halt all further processing, along with hogging file and
    database handles.  This disables the popup.

    From http://blogs.msdn.com/b/oldnewthing/archive/2004/07/27/198410.aspx

    DWORD dwMode = SetErrorMode(SEM_NOGPFAULTERRORBOX);
    SetErrorMode(dwMode | SEM_NOGPFAULTERRORBOX);
    """
    # Enums from MSDN
    SEM_FAILCRITICALERRORS     = 0x0001
    SEM_NOGPFAULTERRORBOX      = 0x0002
    SEM_NOALIGNMENTFAULTEXCEPT = 0x0004
    SEM_NOOPENFILEERRORBOX     = 0x8000
    dwMode = ctypes.windll.kernel32.SetErrorMode(
        SEM_FAILCRITICALERRORS|
        SEM_NOGPFAULTERRORBOX|
        SEM_NOALIGNMENTFAULTEXCEPT|
        SEM_NOOPENFILEERRORBOX
        )
#

def ImageMagickPath(tool, cache={}):
    """Find path to named ImageMagick executable."""
    if tool not in cache:
        with _winreg.OpenKey(_winreg.HKEY_LOCAL_MACHINE,
                             r'Software\\ImageMagick\\Current',
                             0,
                             (_winreg.KEY_READ + _winreg.KEY_WOW64_64KEY)) as key:
            value =  _winreg.QueryValueEx(key, "BinPath")[0]
            cache[tool] = os.path.join(value, tool + '.exe')
        #
    #
    return cache[tool]
#

def WKManglePath(source_fn):
    """Return relative filename expected by WebKiosk, given a filename from EmbARK"""
    wk_fn = re.sub(WK_MANGLE[0], WK_MANGLE[1], source_fn, WK_MANGLE[2], WK_MANGLE[3])
    if wk_fn == source_fn:
        return None
    else:
        return wk_fn
    #
#

def NeedsUpdate(fn1, fn2):
    """Returns True if file2 is older than file1 or this program, or file2
       doesn't exist"""
    if not os.path.isfile(fn2):
        return True
    #
    mtime2 = os.path.getmtime(fn2)
    if fn1:
        mtime1 = os.path.getmtime(fn1)
        if mtime2 < mtime1:
            return True
        #
    #
    mtime_program = os.path.getmtime(__file__)
    if mtime2 < mtime_program:
        return True
    #
    return False
#

def GetWatermark():
    """Return filename of watermark image, generating file if needed"""
    watermark_fn = os.path.join(DEST_ROOT, 'watermark.tif')
    if NeedsUpdate(None, watermark_fn):
        print "Updating watermark %s" % watermark_fn
        args = [
            ImageMagickPath('convert'),
            #'-bench', '2',
            #'-antialias',
            '-size', '4096x4096',
            '-background', 'none',
            '-stroke', 'black',
            '-virtual-pixel', 'tile',
            '-gravity', 'center',
            '-fill', 'grey75',
            '-font', 'Arial',
            'label: Sourisseau Academy for State and Local History ',
            '-define', 'distort:viewport=1024x1024-512-512',
            '-distort', 'Arc', '360 90 1300',
            watermark_fn,
        ]

        try:
            if not os.path.isdir(os.path.dirname(watermark_fn)):
                os.makedirs(os.path.dirname(watermark_fn))
            #print args
            output = subprocess.check_output(args, stderr=subprocess.STDOUT)
            if output.strip():
                print output
            #
        except (IOError, WindowsError, subprocess.CalledProcessError), e:
            print "Error generating watermark %s: %s" % (watermark_fn, str(e))
            return
        #
    #
    return watermark_fn
#

def UpdateDestImage(source_fn, stats, wk_rel_path, wk_ext, dest_dir, size_limit, watermark=False):
    """Generate a single access, preview, or thumbnail image"""
    dest_fn = os.path.join(DEST_ROOT, dest_dir, wk_rel_path + wk_ext)
    #print dest_access_fn, dest_preview_fn, dest_thumbnail_fn

    if not NeedsUpdate(source_fn, dest_fn):
        stats.skipped += 1
        print "Skipping %s, up-to-date" % dest_fn
        return
    #

    stats.updated += 1
    print "Updating %s from %s" % (dest_fn, source_fn)

    args = [
        ImageMagickPath('convert'),
        #'-bench', '2',
        source_fn + '[0]', # select first page of multi-page tiff
        '-thumbnail', size_limit,
        ]
    if watermark:
        watermark_fn = GetWatermark()
        args.extend([
            watermark_fn,
            '-gravity', 'center',
            '-compose', 'dissolve', '-define', 'compose:args=30,100',
            '-composite',
        ])
    #
    args.extend(['JPEG:' + dest_fn])

    try:
        if not os.path.isdir(os.path.dirname(dest_fn)):
            os.makedirs(os.path.dirname(dest_fn))
        #
        #print args
        output = subprocess.check_output(args, stderr=subprocess.STDOUT)
        if output.strip():
            print output
        #
    except (IOError, WindowsError,subprocess.CalledProcessError), e:
        print "Error generating thumbnail image %s: %s" % (dest_fn, str(e))
        return
    #
#

def UpdateFromSourceImage(source_fn, stats):
    """Generate access, preview, and thumbnail images from single source image"""
    wk_rel_path = WKManglePath(source_fn)
    if not wk_rel_path:
        stats.unrecognized += 1
        print "Skipping %s, path not recognized" % source_fn
        return
    #
    #print source_fn, wk_rel_path
    UpdateDestImage(source_fn, stats, wk_rel_path, '.tif', "Images", ACCESS_SIZE_LIMIT, watermark=True)
    UpdateDestImage(source_fn, stats, wk_rel_path, '.jpg', "Previews", PREVIEW_SIZE_LIMIT)
    UpdateDestImage(source_fn, stats, wk_rel_path, '.jpg', "Thumbnails", THUMBNAIL_SIZE_LIMIT)
#

def UpdateAll(pathspecs):
    """Find all EmbARK images, and generate all Web Kiosk images from them"""
    stats = type('Stats', (object,), { 'found':0, 'unrecognized':0, 'skipped':0, 'updated':0})
    for pathspec in pathspecs:
        for source_fn in glob.iglob(pathspec):
            source_fn = os.path.abspath(source_fn)
            stats.found += 1
            UpdateFromSourceImage(source_fn, stats)
        #
    #
    print "Found %d source images (%d unrecognized)" % (stats.found, stats.unrecognized)
    print "Updated %d destination images, skipped %d already up-to-date" % (stats.updated, stats.skipped)
#

def main():
    GetWatermark()
    UpdateAll(SOURCE_PATHSPECS)
#

#------------------------------------------------------------------------------
# Main
#------------------------------------------------------------------------------

if __name__ == '__main__':
    DisableErrorPopup()
    main()
#
