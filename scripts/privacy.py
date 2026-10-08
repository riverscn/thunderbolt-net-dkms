#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Small, deterministic release hygiene guard; not a complete secret scanner."""
import ipaddress
import re

PATTERNS = {
    'private key': r'-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----',
    'GitHub token': r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b',
    'AWS access key': r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
    'personal filesystem path': r'/(?:Users|home)/[A-Za-z0-9_.-]+',
    'SSH authorized key': r'\bssh-(?:ed25519|rsa) AAAA[A-Za-z0-9+/=]{25,}',
    'link-local IPv6 address': r'\bfe80:(?::|[0-9a-fA-F])[^\s<>"\']{5,}',
}
PRIVATE_NETS = tuple(ipaddress.ip_network(x) for x in (
    '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))

def findings(text):
    results = []
    for name, pattern in PATTERNS.items():
        if re.search(pattern, text):
            results.append(name)
    for match in re.finditer(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', text):
        try:
            addr = ipaddress.ip_address(match.group())
        except ValueError:
            continue
        # The literal network definitions above are rules, not machine addresses.
        suffix = text[match.end():match.end()+3]
        if suffix.startswith('/'):
            continue
        if any(addr in network for network in PRIVATE_NETS):
            results.append('private IPv4 address')
    return sorted(set(results))
