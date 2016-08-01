#   Copyright (C) 2016 Canonical Ltd.
#
#   Author: Wesley Wiedenmeier <wesley.wiedenmeier@canonical.com>
#
#   Curtin is free software: you can redistribute it and/or modify it under
#   the terms of the GNU Affero General Public License as published by the
#   Free Software Foundation, either version 3 of the License, or (at your
#   option) any later version.
#
#   Curtin is distributed in the hope that it will be useful, but WITHOUT ANY
#   WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
#   FOR A PARTICULAR PURPOSE.  See the GNU Affero General Public License for
#   more details.
#
#   You should have received a copy of the GNU Affero General Public License
#   along with Curtin.  If not, see <http://www.gnu.org/licenses/>.

# This module provides some helper functions for manipulating lvm devices

from curtin import util
from curtin.log import LOG


def _filter_lvm_info(lvtool, match_field, query_field, match_key):
    """filter output of pv/vg/lvdisplay tools"""
    sep = '='
    (out, _) = util.subp([lvtool, '-C', '--separator', sep, '--noheadings',
                          '-o', ','.join([match_field, query_field])],
                         capture=True)
    return [qf for (mf, qf) in [l.strip().split(sep) for l in out.splitlines()]
            if mf == match_key]


def get_pvols_in_volgroup(vg_name):
    """get physical volumes used by volgroup"""
    return _filter_lvm_info('pvdisplay', 'vg_name', 'pv_name', vg_name)


def get_lvols_in_volgroup(vg_name):
    """get logical volumes in volgroup"""
    return _filter_lvm_info('lvdisplay', 'vg_name', 'lv_name', vg_name)


def split_lvm_name(full):
    """split full lvm name into tuple of (volgroup, lv_name)"""
    sep = '='
    # 'dmsetup splitname' is the authoratative source for lvm name parsing
    (out, _) = util.subp(['dmsetup', 'splitname', full, '-c', '--noheadings',
                          '--separator', sep, '-o', 'vg_name,lv_name'],
                         capture=True)
    return out.strip().split(sep)


def lvm_scan():
    """run full scan for volgroups, logical volumes and physical volumes"""
    # the lvm tools lvscan, vgscan and pvscan on ubuntu precise do not
    # support the flag --cache. the flag is present for the tools in ubuntu
    # trusty and later. since lvmetad is used in current releases of
    # ubuntu, the --cache flag is needed to ensure that the data cached by
    # lvmetad is updated.

    # if we are unable to determine the version of ubuntu we are running
    # on, we are much more likely to be correct in using the --cache flag,
    # as every supported release except for precise supports the --cache
    # flag, and most releases being installed now are either trusty or
    # xenial
    release_code_str = util.lsb_release().get('release')
    if release_code_str is None or release_code_str == 'UNAVAILABLE':
        LOG.warn('unable to find release number, assuming trusty or later')
        release_code_str = '14.04'
    release_code = float(release_code_str)

    for cmd in [['pvscan'], ['vgscan', '--mknodes'], ['lvscan', '--all']]:
        if release_code >= 14.04:
            cmd.append('--cache')
        util.subp(cmd, capture=True)
