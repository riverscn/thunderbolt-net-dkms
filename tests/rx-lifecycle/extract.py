#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build a test tree from the actual driver, without a second driver copy."""
import argparse
import hashlib
import json
import pathlib
import re
import shutil

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
FUNCTIONS = (
    'tbnet_frame_size', 'tbnet_free_buffers', 'tbnet_rx_prepare',
    'tbnet_rx_activate', 'tbnet_rx_quiesce', 'tbnet_available_buffers', 'tbnet_alloc_rx_buffers',
    'tbnet_check_frame', 'tbnet_poll', 'tbnet_start_poll',
)


def fragments(source):
    result = {}
    for name in FUNCTIONS:
        # Match definitions, not calls. These functions contain no braces in
        # string literals; reject extraction drift via the recorded hashes.
        pattern = rf'^static [^;{{}}]*\b{re.escape(name)}\([^;{{}}]*\)\n\{{'
        matches = list(re.finditer(pattern, source, re.MULTILINE))
        if len(matches) != 1:
            raise ValueError(f'Expected one definition of {name}')
        match = matches[0]
        end, depth = match.end(), 1
        while depth and end < len(source):
            depth += (source[end] == '{') - (source[end] == '}')
            end += 1
        if depth:
            raise ValueError(f'Unclosed definition of {name}')
        result[name] = source[match.start():end]
    return result


def prepare(destination):
    source = (ROOT / 'src/main.c').read_text()
    bodies = fragments(source)
    hashes = {name: hashlib.sha256(body.encode()).hexdigest()
              for name, body in bodies.items()}
    expected = json.loads((HERE / 'tested-functions.json').read_text())
    if hashes != expected:
        raise ValueError('RX core changed: review/retest and update tested-functions.json')
    destination.mkdir(parents=True, exist_ok=True)
    prefix = source[:source.index('/* Network property directory UUID:')]
    prefix = prefix.replace('#include "trace.h"', '')
    (destination / 'core.inc').write_text(
        prefix + '\nstatic bool tbnet_rx_page_pool, tbnet_rx_segment;\n'
        'static unsigned int tbnet_rx_segment_mtu = 1500;\n' +
        '\n\n'.join(bodies.values()) + '\n')
    for name in ('harness.c', 'sanity.c', 'Makefile'):
        shutil.copy2(HERE / name, destination / name)
    for name in ('rx_fixup.c', 'rx_fixup.h', 'compat.h'):
        shutil.copy2(ROOT / 'src' / name, destination / name)
    (destination / 'shared-functions.json').write_text(json.dumps(hashes, indent=2) + '\n')
    return hashes


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=pathlib.Path, default=ROOT / 'build/rx-page-pool/test')
    args = parser.parse_args()
    print(f'Extracted {len(prepare(args.output))} verified driver functions')
