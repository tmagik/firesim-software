import os
import subprocess as sp
import logging
import time
import random
import string
import sys
import collections
import shutil
import psutil
import errno
from contextlib import contextmanager

wlutil_dir = os.path.normpath(os.path.dirname(__file__))
root_dir = os.getcwd()
image_dir = os.path.join(root_dir, "images")
linux_dir = os.path.join(root_dir, "riscv-linux")
log_dir = os.path.join(root_dir, "logs")
res_dir = os.path.join(root_dir, "runOutput")
mnt = os.path.join(root_dir, "disk-mount")
commandScript = os.path.join(wlutil_dir, "_command.sh")
jlevel = "-j" + str(os.cpu_count())
runName = ""

# Useful for defining lists of files (e.g. 'files' part of config)
FileSpec = collections.namedtuple('FileSpec', [ 'src', 'dst' ])

# Create a unique run name. You can call this multiple times to reset internal
# paths (e.g. for starting a logically different run). The run name controls
# where logging and workload outputs go. You must call initLogging again to
# reset logging after changing setRunName.
def setRunName(configPath, operation):
    global runName
    
    timeline = time.strftime("%Y-%m-%d--%H-%M-%S", time.gmtime())
    randname = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(16))

    runName = os.path.splitext(os.path.basename(configPath))[0] + \
            "-" + operation + \
            "-" + timeline + \
            "-" +  randname

def getRunName():
    return runName

# logging setup: You can call this multiple times to reset logging (e.g. if you
# change the RunName)
fileHandler = None
consoleHandler = None
def initLogging(verbose):
    global fileHandler
    global consoleHandler

    rootLogger = logging.getLogger()
    rootLogger.setLevel(logging.NOTSET) # capture everything
    
    # Create a unique log name
    logPath = os.path.join(log_dir, getRunName() + ".log")
    
    # formatting for log to file
    if fileHandler is not None:
        rootLogger.removeHandler(fileHandler)

    fileHandler = logging.FileHandler(str(logPath))
    logFormatter = logging.Formatter("%(asctime)s [%(funcName)-12.12s] [%(levelname)-5.5s]  %(message)s")
    fileHandler.setFormatter(logFormatter)
    fileHandler.setLevel(logging.NOTSET) # log everything to file
    rootLogger.addHandler(fileHandler)

    # log to stdout, without special formatting
    if consoleHandler is not None:
        rootLogger.removeHandler(consoleHandler)

    consoleHandler = logging.StreamHandler(stream=sys.stdout)
    if verbose:
        consoleHandler.setLevel(logging.NOTSET) # show everything
    else:
        consoleHandler.setLevel(logging.INFO) # show only INFO and greater in console

    rootLogger.addHandler(consoleHandler)

# Run subcommands and handle logging etc.
# The arguments are identical to those for subprocess.call()
# level - The logging level to use
# check - Throw an error on non-zero return status?
# def run(*args, level=logging.DEBUG, check=True, **kwargs):
#     log = logging.getLogger()
#
#     try:
#         out = sp.check_output(*args, universal_newlines=True, stderr=sp.STDOUT, **kwargs)
#         log.log(level, out)
#     except sp.CalledProcessError as e:
#         log.log(level, e.output)
#         if check:
#             raise
def run(*args, level=logging.DEBUG, check=True, **kwargs):
    log = logging.getLogger()

    if isinstance(args[0], str):
        prettyCmd = args[0]
    else:
        prettyCmd = ' '.join(args[0])

    if 'cwd' in kwargs:
        log.log(level, 'Running: "' + prettyCmd + '" in ' + kwargs['cwd'])
    else:
        log.log(level, 'Running: "' + prettyCmd + '" in ' + os.getcwd())

    p = sp.Popen(*args, universal_newlines=True, stderr=sp.STDOUT, stdout=sp.PIPE, **kwargs)
    for line in iter(p.stdout.readline, ''):
        log.log(level, line.strip())
    p.wait()

    if check == True and p.returncode != 0:
            raise sp.CalledProcessError(p.returncode, prettyCmd)

# Convert a linux configuration file to use an initramfs that points to the correct cpio
# This will modify linuxCfg in place
def convertInitramfsConfig(cfgPath, cpioPath):
    log = logging.getLogger()
    with open(cfgPath, 'at') as f:
        f.write("CONFIG_BLK_DEV_INITRD=y\n")
        f.write('CONFIG_INITRAMFS_SOURCE="' + cpioPath + '"\n')
 
def genRunScript(command):
    with open(commandScript, 'w') as s:
        s.write("#!/bin/bash\n")
        s.write(command + "\n")
        s.write("poweroff\n")

    return commandScript

# This is like os.waitpid, but it works for non-child processes
def waitpid(pid):
    done = False
    while not done:
        try:
            os.kill(pid, 0)
        except OSError as err:
            if err.errno == errno.ESRCH:
                done = True
                break
        time.sleep(0.25)

@contextmanager
def mountImg(imgPath, mntPath):
    run(['guestmount', '--pid-file', 'guestmount.pid', '-a', imgPath, '-m', '/dev/sda', mntPath])
    try:
        with open('./guestmount.pid', 'r') as pidFile:
            mntPid = int(pidFile.readline())
        yield mntPath
    finally:
        run(['guestunmount', mntPath])
        os.remove('./guestmount.pid')

    # There is a race-condition in guestmount where a background task keeps
    # modifying the image for a period after unmount. This is the documented
    # best-practice (see man guestmount).
    waitpid(mntPid)

def toCpio(config, src, dst):
    log = logging.getLogger()

    with mountImg(src, mnt):
        # Fedora needs a special init in order to boot from initramfs
        run("find -print0 | cpio --owner root:root --null -ov --format=newc > " + dst, shell=True, cwd=mnt)

    # Ideally, the distro's themselves would provide initramfs-based versions.
    # However, having two codepaths for disk images and cpio archives
    # complicates a bunch of stuff in the rest of marshal. Instead, we maintain
    # overlays here that convert a disk-based image to a cpio-based image.
    if config['distro'] == 'fedora':
        sp.call("cat " + os.path.join(wlutil_dir, "fedora-initramfs-append.cpio") + " >> " + dst, shell=True)
    elif config['distro'] == 'br':
        sp.call("cat " + os.path.join(wlutil_dir, "br-initramfs-append.cpio") + " >> " + dst, shell=True)

# Apply the overlay directory "overlay" to the filesystem image "img"
# Note that all paths must be absolute
def applyOverlay(img, overlay):
    log = logging.getLogger()
    copyImgFiles(img, [FileSpec(src=os.path.join(overlay, "*"), dst='/')], 'in')
    
# Copies a list of type FileSpec ('files') to/from the destination image (img)
#   img - path to image file to use
#   files - list of FileSpecs to use
#   direction - "in" or "out" for copying files into or out of the image (respectively)
def copyImgFiles(img, files, direction):
    log = logging.getLogger()

    if not os.path.exists(mnt):
        run(['mkdir', mnt])

    with mountImg(img, mnt):
        for f in files:
            # Overlays may not be owned by root, but the filesystem must be.
            # The FUSE mount automatically handles the chown from root to user
            # Note: shell=True because f.src is allowed to contain globs
            # Note: os.path.join can't handle overlay-style concats (e.g. join('foo/bar', '/baz') == '/baz')
            if direction == 'in':
                run('cp -a ' + f.src + " " + os.path.normpath(mnt + f.dst), shell=True)
            elif direction == 'out':
                uid = os.getuid()
                run('cp -a ' + os.path.normpath(mnt + f.src) + " " + f.dst, shell=True)
            else:
                raise ValueError("direction option must be either 'in' or 'out'")
