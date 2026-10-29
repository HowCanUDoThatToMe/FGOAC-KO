"""Network policy metadata and managed helper binding; installation is offline.

No target launcher is executed, no DNS lookup or connection occurs here.
Runtime plans and owned TCP relays live in FGO_KoNetwork.ps1.
"""
import hashlib
import ipaddress
from pathlib import Path
import re

HELPER = 'App/FGO_KoNetwork.ps1'
HELPERS = (HELPER, 'App/FGO_KoServerConfig.py', 'App/FGO_KoServerCleanup.ps1')
POLICY = 'destination-separated-v1'
NATIVE_HOOK_SHA256 = '01382853521d0a713d7dbd8ca1f53e8b54c3313a0497de62b86145ca704c484b'
LOCAL_NAMES = frozenset(('', 'auto', 'local', 'localhost', '127.0.0.1', '::1'))


def server_intent(host='auto', mode='auto'):
    """Explicit remote always wins, including a historical virtual address."""
    if not isinstance(host, str) or mode not in ('auto', 'local', 'remote'):
        raise ValueError('NETWORK_CONFIG_INVALID')
    host = host.strip()
    if any(c in host for c in '\r\n/\\@?#') or not host or host.lower() in ('auto', 'local'):
        if mode == 'remote':
            raise ValueError('NETWORK_REMOTE_DESTINATION_REQUIRED')
        if host.lower() not in ('', 'auto', 'local'):
            raise ValueError('NETWORK_CONFIG_INVALID')
    if mode == 'local':
        if host.lower() not in LOCAL_NAMES:
            raise ValueError('NETWORK_LOCAL_REMOTE_CONFLICT')
        return 'local'
    if host.lower() in LOCAL_NAMES and mode != 'remote':
        return 'local'
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
            raise ValueError('NETWORK_CONFIG_INVALID') from None
        if any(not label or len(label) > 63 or label.startswith('-') or label.endswith('-')
               for label in host.rstrip('.').split('.')):
            raise ValueError('NETWORK_CONFIG_INVALID')
    else:
        if address.is_unspecified or address.is_multicast or (address.version == 4 and int(address) >= 0xe0000000):
            raise ValueError('NETWORK_DESTINATION_INVALID')
    return 'remote'


def physical_prefix(address, prefix):
    """Diagnostic calculation only: never constrains cabinet identity."""
    return str(ipaddress.ip_network(f'{address}/{prefix}', strict=False))


def helper_bytes():
    return Path(__file__).with_name('FGO_KoNetwork.ps1').read_bytes()


def helper_record():
    raw = helper_bytes()
    return dict(path=HELPER, policy=POLICY, sha256=hashlib.sha256(raw).hexdigest(),
                install_network_required=False, validation='synthetic-and-loopback')


def native_hook_supported(raw):
    """Only the statically inspected hook may activate its local redirection."""
    return hashlib.sha256(raw).hexdigest() == NATIVE_HOOK_SHA256


def helper_files():
    """Install through the existing transaction/ownership inventory."""
    return {rel: Path(__file__).with_name(Path(rel).name).read_bytes() for rel in HELPERS}
