#!/usr/bin/env python
#
# Generate thumbnails for WebKiosk
#
# Author: Douglas Greiman, 2015
#
# Quick and dirty code, error-handling is minimal

#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

import ctypes
import datetime
import glob
import json
import os
import pprint
import re
import socket
import subprocess
import winreg

#------------------------------------------------------------------------------
# Global Variables
#------------------------------------------------------------------------------

CONFIG_FN = './WebKioskImageUpdate.json'

# Regular expression used in WKManglePath()
WK_MANGLES = [
(
    r'^.*\\Images\\(.*)[.]tif$',
    r'\1',
    1,
    re.IGNORECASE
),
(
    r'^.*\\McKay\\neg scans\\(.*)[.](tif|jpg)$',
    r'\1',
    1,
    re.IGNORECASE
),
(
    r'^.*\\Del Carlo\\Scans\\(.*)[.](tif|jpg)',
    r'\1',
    1,
    re.IGNORECASE
),
(
    r'^.*\\Collections\\postcards\\(.*)[.](tif|jpg)',
    r'\1',
    1,
    re.IGNORECASE
),
(
    r'^.*\\Collections\\Layton\\(.*)[.](tif|jpg)',
    r'\1',
    1,
    re.IGNORECASE
),
]

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


def ReadConfig():
    """Read the config file and return a configuration object.

    Per-host options override global options.

    Config members:
        source_pathspecs: List of globs to find source images in
        dest_root: Directory tree to store generated images into

        access_size_limit: Maximum size of access image, in pixels
        preview_size_limit: Maximum size of preview image, in pixels
        thumbnail_size_limit: Maximum size of thumbnail image, in pixels

        watermark_basename_template: Base filename to store watermark image in
        watermark_create_args: ImageMagick command line for creating watermark image
        watermark_create_args: ImageMagick command line to applying watermark to another image

    Returns the configuration object
    """
    print("Reading configuration from %s" % CONFIG_FN)
    with open(CONFIG_FN, 'r') as config_file:
        whole_config = json.load(config_file)

    # join global and per-host configs
    current_config = whole_config['global_config']
    hostname = socket.gethostname()
    host_config = whole_config['host_config'][hostname]
    current_config.update(host_config)

    # Turn into an object with named members
    config = type('Config', (object,), current_config)
    print("Configuration:")
    pprint.pprint({
        k:v for k,v in vars(config).items()
        if k[0:1] != '_'})
    return config


def ImageMagickPath(tool, cache={}):
    """Find path to named ImageMagick executable."""
    if tool not in cache:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'Software\\ImageMagick\\Current',
                            0,
                            (winreg.KEY_READ + winreg.KEY_WOW64_64KEY)) as key:
            value =  winreg.QueryValueEx(key, 'BinPath')[0]
            cache[tool] = os.path.join(value, tool + '.exe')
    return cache[tool]


def WKManglePath(source_fn):
    """Return relative filename expected by WebKiosk, given a filename from EmbARK

    Find the right-most instance of a directory called "Images", and
    take the relative path under that.  Strip off file extension.

    Returns None if filename format is unrecognized

    """
    for mangle in WK_MANGLES:
        wk_fn = re.sub(
            mangle[0], mangle[1], source_fn,
            mangle[2], mangle[3])
        if wk_fn != source_fn:
            return wk_fn
    return None

assert (
    WKManglePath('Y:\\Edith Smith Collections\\Images\\4000\\4400\\ecs4404.tif') ==
    '4000\\4400\\ecs4404')

def NeedsUpdate(fn1, fn2):
    """Determine if an image should be (re)generated.

    Args:
        fn1: Filename of source image (file1)
        fn2: Filename of generated destination image (file2)

    fn1 may be None, in which case it is treated as infinitely old.

    Returns True if file2 doesn't exist or
            if file2 is older than file1 or this program
    """
    # File2 doesn't exist
    if not os.path.isfile(fn2):
        return True

    # File2 older than File1
    mtime2 = os.path.getmtime(fn2)
    if fn1:
        mtime1 = os.path.getmtime(fn1)
        if mtime2 < mtime1:
            return True

    # File2 older than this program
    mtime_program = os.path.getmtime(__file__)
    if mtime2 < mtime_program:
        return True
    return False


def GetWatermark(config, size_limit):
    """Return filename of watermark image, generating file if needed"""
    watermark_basename = config.watermark_basename_template % size_limit
    watermark_fn = os.path.join(config.dest_root, watermark_basename)
    if NeedsUpdate(None, watermark_fn):
        print('Updating watermark %s' % watermark_fn)
        args = [
            ImageMagickPath('magick')
        ] + config.watermark_create_args[size_limit] + [
            watermark_fn
        ]

        try:
            print("    Running %s" % args)
            os.makedirs(os.path.dirname(watermark_fn), exist_ok=True)
            output = subprocess.check_output(args, stderr=subprocess.STDOUT)
            if output.strip():
                print(output)
        except (IOError, WindowsError, subprocess.CalledProcessError) as e:
            print('  Error generating watermark: %s' % (e,))
            raise # This is a fatal error
    return watermark_fn


def UpdateDestImage(config, stats, source_fn, dest_fn, size_limit, watermark):
    """Generate a single access, preview, or thumbnail image

    Args:
        config: Config object
        stats: Cumulative statistics (updated by this function)
        source_fn: Filename of archival source image
        dest_fn: Filename of generated image
        size_limit: Maximum size of generated images (e.g. '1024x1024')
        watermark: Apply watermark to generated image if True, else don't
    """
    print('Considering generated file %s' % dest_fn)
    if not NeedsUpdate(source_fn, dest_fn):
        stats.skipped += 1
        print('  Skipping, up-to-date')
        return

    stats.updated += 1
    print('  Updating from %s' % (source_fn,))

    args = [
        ImageMagickPath('magick'),
        #'-bench', '2',
        source_fn + '[0]', # select first page of multi-page tiff
        '-thumbnail', size_limit,
        ]
    if watermark:
        watermark_fn = GetWatermark(config, size_limit)
        args.extend([watermark_fn])
        args.extend(config.watermark_apply_args)
    args.extend(['JPEG:' + dest_fn])

    try:
        print("    Running %s" % args)
        os.makedirs(os.path.dirname(dest_fn), exist_ok=True)
        output = subprocess.check_output(args, stderr=subprocess.STDOUT)
        if output.strip():
            print(output)
    except (IOError, WindowsError,subprocess.CalledProcessError) as e:
        print('  Error generating image: %s' % (e,))
        return


def UpdateFromSourceImage(config, stats, source_fn):
    """Generate access, preview, and thumbnail images from single source image"""
    print('Considering source image %s' % (source_fn,))
    # Webkiosk wants the images in different directories than EmbARK
    wk_rel_path = WKManglePath(source_fn)
    if not wk_rel_path:
        stats.unrecognized += 1
        print('  Skipping, path not recognized')
        return

    UpdateDestImage(config, stats, source_fn,
                    os.path.join(config.dest_root, 'Images', wk_rel_path + '.jpg'),
                    config.access_size_limit, watermark=True)
    UpdateDestImage(config, stats, source_fn,
                    os.path.join(config.dest_root, 'Previews', wk_rel_path + '.jpg'),
                    config.preview_size_limit, watermark=True)
    UpdateDestImage(config, stats, source_fn,
                    os.path.join(config.dest_root, 'Thumbnails', wk_rel_path + '.jpg'),
                    config.thumbnail_size_limit, watermark=False)


def UpdateAll(config):
    """Find all EmbARK images, and generate all Web Kiosk images from them"""

    # Collect cumulative statistics
    stats = type('Stats', (object,), { 'found':0, 'unrecognized':0, 'skipped':0, 'updated':0})

    # Walk the source trees
    for pathspec in config.source_pathspecs:
        for source_fn in glob.iglob(pathspec):
            source_fn = os.path.abspath(source_fn)
            stats.found += 1
            UpdateFromSourceImage(config, stats, source_fn)

    # Print summary
    summary = """
Summary:

Found %d source images
  (%d unrecognized)
Updated %d destination images
Skipped %d already up-to-date

Done""" % (
    stats.found, stats.unrecognized, stats.updated, stats.skipped)
    print(summary)

def main():
    print("Starting WebKioskImageUpdate.py at %s" % (datetime.datetime.now().isoformat(),))
    config = ReadConfig()
    # Ensure watermarks exist
    GetWatermark(config, config.access_size_limit)
    GetWatermark(config, config.preview_size_limit)
    UpdateAll(config)

#------------------------------------------------------------------------------
# Main
#------------------------------------------------------------------------------

if __name__ == '__main__':
    DisableErrorPopup()
    main()
