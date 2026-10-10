#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Pinned upstream provenance, reproducible local delta, and review-only updates."""
import argparse
import difflib
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = ('main.c', 'trace.c', 'trace.h')


def delta(root=ROOT):
    return ''.join(''.join(difflib.unified_diff(
        (root / 'upstream' / name).read_text().splitlines(True),
        (root / 'src' / name).read_text().splitlines(True),
        fromfile='a/' + name, tofile='b/' + name)) for name in FILES)


def verify(root=ROOT):
    metadata = json.loads((root / 'upstream/provenance.json').read_text())
    if not re.fullmatch(r'[0-9a-f]{40}', metadata['commit']):
        raise ValueError('baseline must pin a full commit')
    for name in FILES:
        data = (root / 'upstream' / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != metadata['files'][name]:
            raise ValueError('upstream hash mismatch: ' + name)
        expected = (f"https://raw.githubusercontent.com/{metadata['repository']}/"
                    f"{metadata['commit']}/drivers/net/thunderbolt/{name}")
        if metadata['urls'][name] != expected:
            raise ValueError('upstream URL is not pinned: ' + name)
    for item in metadata.get('imports', []):
        sha = item['commit']
        validate_commit(sha)
        if item['path'] != f'upstream/commits/{sha}.patch':
            raise ValueError('invalid original patch path')
        original = (root / item['path']).read_bytes()
        validate_mail(sha, original)
        if hashlib.sha256(original).hexdigest() != item['sha256']:
            raise ValueError('original patch checksum mismatch: ' + sha)
    patch = root / 'upstream/local.patch'
    if patch.read_text() != delta(root):
        raise ValueError('local delta is stale: review changes, then run upstream.py refresh')
    with tempfile.TemporaryDirectory(prefix='tbnet-replay-') as tmp:
        target = pathlib.Path(tmp)
        for name in FILES:
            shutil.copy2(root / 'upstream' / name, target / name)
        subprocess.run(['git', 'apply', str(patch.resolve())], cwd=target,
                       check=True, timeout=15)
        for name in FILES:
            if (target / name).read_bytes() != (root / 'src' / name).read_bytes():
                raise ValueError('delta replay mismatch: ' + name)
    return metadata


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'thunderbolt-net-upstream-check'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def stable_release(data):
    version = data['latest_stable']['version']
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', version):
        raise ValueError('invalid stable version')
    return version


def resolve_tag(tag):
    if not re.fullmatch(r'v\d+\.\d+(?:\.\d+)?', tag):
        raise ValueError('source baseline updates require an explicit stable tag')
    api = 'https://api.github.com/repos/gregkh/linux/git/'
    obj = json.loads(fetch(api + 'ref/tags/' + tag))['object']
    for _ in range(4):
        if not re.fullmatch(r'[0-9a-f]{40}', obj['sha']):
            raise ValueError('invalid upstream object')
        if obj['type'] == 'commit':
            return obj['sha']
        if obj['type'] != 'tag':
            break
        obj = json.loads(fetch(api + 'tags/' + obj['sha']))['object']
    raise ValueError('tag does not resolve to a commit')


def validate_commit(sha):
    if not re.fullmatch(r'[0-9a-f]{40}', sha):
        raise ValueError('original commits require full lowercase SHA-1 IDs')


def validate_mail(sha, data):
    validate_commit(sha)
    if not data.startswith(f'From {sha} Mon Sep 17 00:00:00 2001\n'.encode()):
        raise ValueError('original patch does not identify requested commit: ' + sha)
    if re.search(rb'\nFrom [0-9a-f]{40} Mon Sep 17', data):
        raise ValueError('one original commit is required per patch')


def git(directory, *args, **kwargs):
    return subprocess.run(['git', '-c', 'user.name=Thunderbolt net import',
                          '-c', 'user.email=import@example.org', *args],
                         cwd=directory, check=True, timeout=30, **kwargs)


def import_mail(directory, sha, data):
    """Preserve the original mail; only remap diff paths for git am."""
    validate_mail(sha, data)
    lines = data.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.startswith((b'diff --git ', b'--- a/', b'+++ b/')):
            lines[i] = line.replace(b'a/drivers/net/thunderbolt/', b'a/upstream/').replace(
                b'b/drivers/net/thunderbolt/', b'b/upstream/')
    git(directory, 'am', *['--include=upstream/' + n for n in FILES],
        input=b''.join(lines), capture_output=True)
    message = git(directory, 'show', '-s', '--format=%B',
                  capture_output=True).stdout.decode().rstrip()
    message += (f'\n\nUpstream-commit: {sha}\n'
                'Upstream-repository: https://github.com/gregkh/linux\n'
                'Import-scope: drivers/net/thunderbolt/{main.c,trace.c,trace.h} -> upstream/\n'
                'Import-note: Other paths remain in the host kernel; local compatibility is separate.\n')
    git(directory, 'commit', '--amend', '-q', '-F', '-', input=message.encode())


def prepare(tag, output, root=ROOT, commits=()):
    metadata = verify(root)
    if output.exists():
        raise ValueError('output must not exist; source checkout is never overwritten')
    seen = {item['commit'] for item in metadata.get('imports', [])}
    for sha in commits:
        validate_commit(sha)
        if sha in seen:
            raise ValueError('duplicate or already archived commit: ' + sha)
        seen.add(sha)
    commit = resolve_tag(tag)
    urls = {name: f'https://raw.githubusercontent.com/gregkh/linux/{commit}/drivers/net/thunderbolt/{name}'
            for name in FILES}
    # Endpoint files are verification only, never the input to an import.
    expected = {name: fetch(url) for name, url in urls.items()}
    mails = [(sha, fetch(f'https://github.com/gregkh/linux/commit/{sha}.patch'))
             for sha in commits]
    for sha, data in mails:
        validate_mail(sha, data)
    output.mkdir(parents=True)
    shutil.copytree(root / 'src', output / 'src', ignore=shutil.ignore_patterns(
        '*.o', '*.ko', '*.mod*', '.*.cmd', 'Module.symvers', 'modules.order'))
    shutil.copytree(root / 'upstream', output / 'upstream')
    git(output, 'init', '-q')
    git(output, 'add', 'src', 'upstream')
    git(output, 'commit', '-qm', 'Seed current reviewed upstream and local source')
    import_base = git(output, 'rev-parse', 'HEAD', capture_output=True).stdout.decode().strip()
    for sha, data in mails:
        # Failure leaves git am state in the candidate for explicit review.
        import_mail(output, sha, data)
    import_head = git(output, 'rev-parse', 'HEAD', capture_output=True).stdout.decode().strip()
    for name in FILES:
        if (output / 'upstream' / name).read_bytes() != expected[name]:
            raise ValueError('commit series does not reproduce target baseline: ' + name)
    imports = list(metadata.get('imports', []))
    for sha, data in mails:
        path = f'upstream/commits/{sha}.patch'
        (output / path).parent.mkdir(exist_ok=True)
        (output / path).write_bytes(data)
        imports.append({'commit': sha, 'path': path,
                        'sha256': hashlib.sha256(data).hexdigest()})
    conflicts = []
    for name in FILES:
        result = subprocess.run(['git', 'merge-file', '-p', str(root / 'src' / name),
                                 str(root / 'upstream' / name),
                                 str(output / 'upstream' / name)],
                                capture_output=True, timeout=15)
        if result.returncode < 0 or result.returncode > 127:
            raise RuntimeError('merge-file failed: ' + result.stderr.decode())
        (output / 'src' / name).write_bytes(result.stdout)
        if result.returncode:
            conflicts.append(name)
    metadata.update(baseline='Linux ' + tag, repository='gregkh/linux', tag=tag,
                    commit=commit, urls=urls, imports=imports,
                    files={n: hashlib.sha256(d).hexdigest() for n, d in expected.items()})
    (output / 'upstream/provenance.json').write_text(json.dumps(metadata, indent=2) + '\n')
    (output / 'review.json').write_text(json.dumps({'conflicts': conflicts,
        'import_base': import_base, 'import_head': import_head,
        'required': ['transfer original import commits, not a source snapshot',
                     'review upstream and controller dependencies', 'resolve local changes',
                     'update kernel lock and compatibility gate only after tests',
                     'refresh lifecycle fingerprints and local delta after review',
                     'run all regression and DKMS lifecycle checks']}, indent=2) + '\n')
    print('Candidate only; checkout unchanged. Conflicts:', ', '.join(conflicts) or 'none')
    return bool(conflicts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('verify')
    sub.add_parser('refresh')
    status = sub.add_parser('status')
    status.add_argument('--output', type=pathlib.Path)
    update = sub.add_parser('prepare')
    update.add_argument('--tag', required=True)
    update.add_argument('--commit', action='append', default=[], dest='commits',
                        help='original upstream SHA, repeat in application order')
    update.add_argument('--output', type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.command == 'refresh':
        (ROOT / 'upstream/local.patch').write_text(delta())
    elif args.command == 'verify':
        verify()
        print('Pinned baseline and local delta replay verified')
    elif args.command == 'prepare':
        raise SystemExit(prepare(args.tag, args.output.resolve(), commits=args.commits))
    else:
        metadata = verify()
        releases = json.loads(fetch('https://www.kernel.org/releases.json'))
        latest = stable_release(releases)
        result = {'baseline': metadata['tag'], 'latest_stable': latest,
                  'update_available': metadata['tag'] != 'v' + latest,
                  'mainline': next(r['version'] for r in releases['releases']
                                   if r['moniker'] == 'mainline')}
        text = json.dumps(result, indent=2) + '\n'
        print(text, end='')
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text)


if __name__ == '__main__':
    main()
