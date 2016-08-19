#   Copyright (C) 2015 Canonical Ltd.
#
#   Author: Ryan Harper <ryan.harper@canonical.com>
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

import os
import sys

from .. import log
import curtin.config as config
import curtin.net as net
import curtin.util as util
from . import populate_one_subcmd


LOG = log.LOG

IFUPDOWN_IPV6_MTU_PRE_HOOK = """#!/bin/sh -x
# injected by curtin installer

[ "$IFACE" != "lo" ] || exit 0

# Trigger only if MTU configured
[ -n "$IF_MTU" ] || exit 0

CUR_DEV_MTU=$(/bin/cat /sys/class/net/$IFACE/mtu ||:)
CUR_IPV6_MTU=$(sysctl -n net.ipv6.conf.$IFACE.mtu ||:)
[ -n "$CUR_DEV_MTU" ] && echo $CUR_DEV_MTU > /run/network/$IFACE_dev.mtu
[ -n "$CUR_IPV6_MTU" ] && echo $CUR_IPV6_MTU > /run/network/$IFACE_ipv6.mtu
exit 0
"""

IFUPDOWN_IPV6_MTU_POST_HOOK = """#!/bin/sh -x
# injected by curtin installer

[ "$IFACE" != "lo" ] || exit 0

# Trigger only if MTU configured
[ -n "$IF_MTU" ] || exit 0

PRE_DEV_MTU=$(cat /run/network/$IFACE_dev.mtu)
CUR_DEV_MTU=$(/bin/cat /sys/class/net/$IFACE/mtu)
PRE_IPV6_MTU=$(cat /run/network/$IFACE_ipv6.mtu)
CUR_IPV6_MTU=$(sysctl -n net.ipv6.conf.$IFACE.mtu)

if [ "$ADDRFAM" = "inet6" ]; then
  # We need to check the underlying interface MTU and
  # raise it if the IPV6 mtu is larger
  if [ $CUR_DEV_MTU -lt $IF_MTU ]; then
      /bin/ip link set $IFACE mtu $IF_MTU
  fi
  /sbin/sysctl -q -e -w net.ipv6.conf.$IFACE.mtu=$IF_MTU

elif [ "$ADDRFAM" = "inet" ]; then
  # handle the clobber case where inet mtu changes v6 mtu
  # ifupdown will already have set dev mtu, so lower mtu
  # if needed.  If v6 mtu was larger, it get's clamped down
  # to the dev MTU value.
  if [ $PRE_IPV6_MTU -lt $CUR_IPV6_MTU ]; then
    /sbin/sysctl -q -e -w net.ipv6.conf.$IFACE.mtu=$PRE_IPV6_MTU
  fi
fi
exit 0
"""


def apply_net(target, network_state=None, network_config=None,
              postup_alias=None):
    if network_state is None and network_config is None:
        msg = "Must provide at least config or state"
        sys.stderr.write(msg + "\n")
        raise Exception(msg)

    if target is None:
        msg = "Must provide target"
        sys.stderr.write(msg + "\n")
        raise Exception(msg)

    if network_state:
        ns = net.network_state.from_state_file(network_state)
    elif network_config:
        ns = net.parse_net_config(network_config)

    net.render_network_state(target=target, network_state=ns,
                             postup_alias=postup_alias)

    _maybe_remove_legacy_eth0(target)
    LOG.info('Attempting to remove ipv6 privacy extensions')
    _disable_ipv6_privacy_extensions(target)
    _patch_ifupdown_ipv6_mtu_hook(target)


def detect_postup_alias(target):
    try:
        LOG.info('Checking target for version of ifupdown package')
        # check in-target version
        pkg_ver = util.get_package_version('ifupdown',
                                           target=target)
        if pkg_ver is None:
            raise Exception('Failed to get package version')

        LOG.debug("get_package_version:\n%s", pkg_ver)
        LOG.debug("ifupdown version is %s (major=%s minor=%s micro=%s)",
                  pkg_ver['semantic_version'], pkg_ver['major'],
                  pkg_ver['minor'], pkg_ver['micro'])
        # ifupdown versions < 0.8.6 need ifup alias to prevent 120 second
        # timeout, i.e. 0.7.47 in Trusty uses them.
        if pkg_ver['semantic_version'] < 806:
            return True
    except Exception:
        LOG.warn("Failed reading ifupdown pkg version (using defaults)")

    return False


def _patch_ifupdown_ipv6_mtu_hook(target,
                                  prehookfn="etc/network/if-pre-up.d/mtuipv6",
                                  posthookfn="etc/network/if-up.d/mtuipv6"):

    contents = {
        'prehook': IFUPDOWN_IPV6_MTU_PRE_HOOK,
        'posthook': IFUPDOWN_IPV6_MTU_POST_HOOK,
    }

    hookfn = {
        'prehook': prehookfn,
        'posthook': posthookfn,
    }

    for hook in ['prehook', 'posthook']:
        fn = hookfn[hook]
        cfg = os.path.sep.join([target, fn])
        LOG.info('Injecting fix for ipv6 mtu settings: %s', cfg)
        util.write_file(cfg, contents[hook], mode=0o755)


def _disable_ipv6_privacy_extensions(target,
                                     path="etc/sysctl.d/10-ipv6-privacy.conf"):

    """Ubuntu server image sets a preference to use IPv6 privacy extensions
       by default; this races with the cloud-image desire to disable them.
       Resolve this by allowing the cloud-image setting to win. """

    if path.startswith("/"):
        raise ValueError("path parameter must not start with '/': %s", path)

    cfg = os.path.normpath(os.path.sep.join([target, path]))
    if not os.path.exists(cfg):
        LOG.warn('Failed to find legacy conf file %s', cfg)
        return

    bmsg = "Disabling IPv6 privacy extensions config may not apply."
    try:
        contents = util.load_file(cfg)
        known_contents = ["net.ipv6.conf.all.use_tempaddr = 2",
                          "net.ipv6.conf.default.use_tempaddr = 2"]
        lines = [f.strip() for f in contents.splitlines()
                 if not f.startswith("#")]
        if lines == known_contents:
            LOG.info('deleting file: %s', cfg)
            util.del_file(cfg)
            msg = "removed %s with known contents" % cfg
            curtin_contents = '\n'.join(
                ["# IPv6 Privacy Extensions (RFC 4941)",
                 "# Disabled by curtin",
                 "# net.ipv6.conf.all.use_tempaddr = 2",
                 "# net.ipv6.conf.default.use_tempaddr = 2"])
            util.write_file(cfg, curtin_contents)
        else:
            LOG.info('skipping, content didnt match')
            LOG.info(lines)
            LOG.info(known_contents)
            msg = (bmsg + " '%s' exists with user configured content." % cfg)
    except:
        msg = bmsg + " %s exists, but could not be read." % cfg
        LOG.exception(msg)
        return


def _maybe_remove_legacy_eth0(target,
                              path="etc/network/interfaces.d/eth0.cfg"):
    """Ubuntu cloud images previously included a 'eth0.cfg' that had
       hard coded content.  That file would interfere with the rendered
       configuration if it was present.

       if the file does not exist do nothing.
       If the file exists:
         - with known content, remove it and warn
         - with unknown content, leave it and warn
    """

    cfg = os.path.normpath(os.path.sep.join([target, path]))
    if not os.path.exists(cfg):
        LOG.warn('Failed to find legacy conf file %s', cfg)
        return

    bmsg = "Dynamic networking config may not apply."
    try:
        contents = util.load_file(cfg)
        known_contents = ["auto eth0", "iface eth0 inet dhcp"]
        lines = [f.strip() for f in contents.splitlines()
                 if not f.startswith("#")]
        if lines == known_contents:
            util.del_file(cfg)
            msg = "removed %s with known contents" % cfg
        else:
            msg = (bmsg + " '%s' exists with user configured content." % cfg)
    except:
        msg = bmsg + " %s exists, but could not be read." % cfg
        LOG.exception(msg)
        return

    LOG.warn(msg)


def apply_net_main(args):
    #  curtin apply_net [--net-state=/config/netstate.yml] [--target=/]
    #                   [--net-config=/config/maas_net.yml]
    state = util.load_command_environment()

    log.basicConfig(stream=args.log_file, verbosity=1)

    if args.target is not None:
        state['target'] = args.target

    if args.net_state is not None:
        state['network_state'] = args.net_state

    if args.net_config is not None:
        state['network_config'] = args.net_config

    if state['target'] is None:
        sys.stderr.write("Unable to find target.  "
                         "Use --target or set TARGET_MOUNT_POINT\n")
        sys.exit(2)

    if not state['network_config'] and not state['network_state']:
        sys.stderr.write("Must provide at least config or state\n")
        sys.exit(2)

    postup_alias = False
    if args.postup_alias is not None:
        postup_alias = config.value_as_boolean(args.postup_alias)
    else:
        postup_alias = detect_postup_alias(target=state['target'])
    LOG.info('Applying network configuration')
    try:
        apply_net(target=state['target'],
                  network_state=state['network_state'],
                  network_config=state['network_config'],
                  postup_alias=postup_alias)
    except Exception:
        LOG.exception('failed to apply network config')

    LOG.info('Applied network configuration successfully')
    sys.exit(0)


CMD_ARGUMENTS = (
    ((('-s', '--net-state'),
     {'help': ('file to read containing network state. '
               'defaults to env["OUTPUT_NETWORK_STATE"]'),
      'metavar': 'NETSTATE', 'action': 'store',
      'default': os.environ.get('OUTPUT_NETWORK_STATE')}),
     (('-t', '--target'),
      {'help': ('target filesystem root to configure networking to. '
                'default is env["TARGET_MOUNT_POINT"]'),
       'metavar': 'TARGET', 'action': 'store',
       'default': os.environ.get('TARGET_MOUNT_POINT')}),
     (('-a', '--postup-alias'),
      {'help': ('target filesystem check for postup alias config. '
                'default is not set'),
       'metavar': 'POST', 'action': 'store',
       'default': None}),
     (('-c', '--net-config'),
      {'help': ('file to read containing curtin network config.'
                'defaults to env["OUTPUT_NETWORK_CONFIG"]'),
       'metavar': 'NETCONFIG', 'action': 'store',
       'default': os.environ.get('OUTPUT_NETWORK_CONFIG')})))


def POPULATE_SUBCMD(parser):
    populate_one_subcmd(parser, CMD_ARGUMENTS, apply_net_main)

# vi: ts=4 expandtab syntax=python
