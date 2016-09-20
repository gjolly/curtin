from curtin import __version__ as old_version
import os
import subprocess


def version_string():
    """ Extract a version string from curtin source or version file"""
    def _find_path(pathfile):
        """ Check for file existance and return dirpath
            Search PYTHONPATH, as curtin is typically
            launched with PYTHONPATH set, as with curtin pack
            executables.
        """
        if os.path.exists(pathfile):
            return os.getcwd()
        elif 'PYTHONPATH' in os.environ:
            for path in os.environ['PYTHONPATH'].split(":"):
                curpath = os.path.join(path, pathfile)
                if os.path.exists(curpath):
                    return os.path.dirname(curpath)

        return None

    dotversion = '.version'
    dotpath = _find_path(dotversion)
    if dotpath:
        return open(os.path.join(dotpath, dotversion), 'r').read().strip()

    bzrdir = _find_path('.bzr')
    revno = None
    if bzrdir:
        os.chdir(bzrdir)
        out = subprocess.check_output(['bzr', 'revno'])
        revno = "bzr%s" % out.decode('ascii').strip()

    version = old_version
    if revno:
        version += "~%s" % revno

    return version
