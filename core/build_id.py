"""Which build of the add-on wrote a run: its version and the commit it was built from.

A tester's run folder or .blend is often the first thing that reaches us. Without the build in it, a failure was
looked for in the wrong version (a run sent as one version came from code a release older). So every run says it:
job.json carries ``addon`` and the run log's first line names it.

The released ZIP has BUILD_INFO.txt (written by the release build: version, commit, build time). A checkout used
directly (a junction to the repository) has no BUILD_INFO.txt; its commit is read from the repository's HEAD. Read
once per session: nothing here touches the disk again."""
import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE = {}


def _manifest_version(root):
    try:
        text = open(os.path.join(root, 'blender_manifest.toml'), encoding='utf-8').read()
    except OSError:
        return None
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else None


def _build_info(root):
    """(version, commit) from BUILD_INFO.txt, or (None, None)."""
    try:
        text = open(os.path.join(root, 'BUILD_INFO.txt'), encoding='utf-8').read()
    except OSError:
        return None, None
    v = re.search(r'^AC9 Cloth Sim\s+(\S+)', text, re.M)
    c = re.search(r'^commit\s+([0-9a-f]{7,40})', text, re.M)
    return (v.group(1) if v else None), (c.group(1) if c else None)


def _git_head(root):
    """The commit a checkout is on, read from its files (no git process): None when it is not a checkout."""
    git = os.path.join(root, '.git')
    try:
        if os.path.isfile(git):                       # a worktree: ".git" names the real folder
            line = open(git, encoding='utf-8').read().strip()
            if not line.startswith('gitdir:'):
                return None
            git = os.path.normpath(os.path.join(root, line[len('gitdir:'):].strip()))
        head = open(os.path.join(git, 'HEAD'), encoding='utf-8').read().strip()
        if not head.startswith('ref:'):
            return head if re.fullmatch(r'[0-9a-f]{40}', head) else None
        ref = head[len('ref:'):].strip()
        common = git
        cd = os.path.join(git, 'commondir')
        if os.path.isfile(cd):
            common = os.path.normpath(os.path.join(git, open(cd, encoding='utf-8').read().strip()))
        for base in (git, common):
            p = os.path.join(base, *ref.split('/'))
            if os.path.isfile(p):
                return open(p, encoding='utf-8').read().strip()
        packed = os.path.join(common, 'packed-refs')
        if os.path.isfile(packed):
            for line in open(packed, encoding='utf-8'):
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref:
                    return parts[0]
    except OSError:
        return None
    return None


def info(root=None):
    """{'version', 'commit', 'source'}: source is 'release' (BUILD_INFO.txt), 'checkout' (a repository's HEAD) or
    'unknown'. commit is None when neither says it."""
    root = root or _ROOT
    if root in _CACHE:
        return dict(_CACHE[root])
    version, commit = _build_info(root)
    source = 'release' if commit else None
    if commit is None:
        commit = _git_head(root)
        source = 'checkout' if commit else 'unknown'
    out = {'version': version or _manifest_version(root), 'commit': commit, 'source': source}
    _CACHE[root] = out
    return dict(out)


def line(root=None):
    """One line for the run log: 'add-on 0.3.0 (c3118cd)', 'add-on 0.2.0 (checkout 6044a5d)'."""
    i = info(root)
    c = (i['commit'] or '')[:7]
    if i['source'] == 'release':
        return f"add-on {i['version']} ({c})"
    if i['source'] == 'checkout':
        return f"add-on {i['version']} (checkout {c})"
    return f"add-on {i['version']} (build unknown)"
