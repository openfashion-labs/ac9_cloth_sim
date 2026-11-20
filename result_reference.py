"""Read result-file capabilities without repairing Blender data; explicit operations own repair."""
from dataclasses import dataclass
import os

import bpy


@dataclass(frozen=True)
class JobReference:
    path: str
    source: str
    has_metadata: bool
    has_index: bool
    is_directory: bool

    @property
    def has_reference_files(self):
        # This describes files, not a promise that a particular solver can resume them.
        return self.has_metadata and self.has_index


def inspect(path, source='property'):
    path = str(path or '')
    metadata = bool(path) and os.path.isfile(os.path.join(path, JOB))
    index = bool(path) and os.path.isfile(os.path.join(path, INDEX))
    directory = metadata or index or (bool(path) and os.path.isdir(path))
    return JobReference(path, source, metadata, index, directory)


def _absolute(g, path):
    return bpy.path.abspath(str(path), library=g.library) if str(path).startswith('//') else str(path)


def resolve(g, display=False):
    """Preserve the two existing read contracts: display accepts a stored folder, result needs both files.

    Both accept a cache-derived index-only job. Missing metadata is retained in the descriptor rather than
    converted into either 'fully resumable' or 'no visible result'. No object, Text, or file is written.
    """
    if g is None:
        return None
    stored = g.get('ac9_job')
    if stored:
        ref = inspect(_absolute(g, stored))
        if (ref.is_directory if display else ref.has_reference_files):
            return ref
    for modifier in g.modifiers:
        if modifier.type == 'MESH_CACHE' and modifier.filepath:
            path = bpy.path.abspath(modifier.filepath, library=g.library)
            ref = inspect(os.path.dirname(path), 'cache')
            if ref.has_index:
                return ref
    return None


# ---- the result folder's contract -------------------------------------------------------------------------------
# A result folder is written by a run (Dress / Phase Sim / Live, dress._prepare) or made without one (write_result:
# from the garment of a .blend opened without its folder, From Mesh, a pattern edit carried to the 3D). Whichever
# wrote it, a reader finds the same things through open_result(); the file names live in this module only (the run's
# own writer and its stages, and the solver, are the exceptions: tests/test_result_contract.py counts them).
JOB = 'job.json'
INDEX = 'scene.idx.npz'
PATTERN = 'pattern.npz'
AVATAR = 'avatar.npz'
SPEC = 'ac9_spec.json'
FINAL = 'final.npy'
FRAMES = 'frames.npy'

# what every result folder holds, by whoever wrote it: (file, keys). A run's folder holds more (avatar.npz, the
# stages' config, Xclo, ...), which a reader asks for as optional (open_result: avatar() None, stitches() None).
REQUIRED = ((JOB, ('names', 'stages')), (INDEX, ('offs', 'F', 'fp', 'flat', 'Xinit')), (PATTERN, ('names',)))
KINDS = {'from the .blend': 'taken from the garment in the .blend (its job folder was not found)',
         'from mesh': 'from mesh', 'carried': 'pattern edit carried over (no simulation)'}


class Arrays(dict):
    """A loaded npz as a dict (read once, the file closed), with .files as an NpzFile has."""
    @property
    def files(self):
        return list(self)


class ResultUnreadable(ValueError):
    """A result folder a button needs cannot be read: said in one line (a ValueError, so every button's refusal path
    takes it), with the way out."""


def _unreadable(job, what):
    return ResultUnreadable(f"the garment's result folder cannot be read ({what}): Back to Placed with nothing "
                            "selected, then Phase Sim")


def last_frame(job):
    """The result's last shape (meters, full garment), or None: final.npy once the frames were dropped (another
    garment simulated since), else the last frame written (a job stopped on an anomaly has no frames for its later
    stages)."""
    import json
    import numpy as np
    p = os.path.join(job, FINAL)
    if os.path.exists(p):
        return np.load(p).astype(np.float64)
    try:
        n = len(json.load(open(os.path.join(job, JOB), encoding='utf-8'))['stages'])
    except (OSError, ValueError, KeyError, TypeError):
        n = 20
    for k in range(n, 0, -1):
        f = os.path.join(job, f"s{k}", FRAMES)
        if os.path.exists(f):
            return np.load(f)[-1].astype(np.float64)
    return None


class ResultFolder:
    """One result folder, read lazily. Each part either is there, is made from what is there (pattern), is answered
    as not known (stitches / avatar / dressed_spec: None), or raises ResultUnreadable -- never an OSError / KeyError
    / TypeError out of a button."""

    def __init__(self, job):
        self.path = str(job)
        self._info = self._index = None

    def exists(self):
        return bool(self.path) and os.path.isfile(os.path.join(self.path, JOB)) and \
            os.path.isfile(os.path.join(self.path, INDEX))

    @property
    def info(self):
        if self._info is None:
            import json
            try:
                self._info = json.load(open(os.path.join(self.path, JOB), encoding='utf-8'))
            except (OSError, ValueError, TypeError) as exc:
                raise _unreadable(self.path, f"{JOB}: {exc}") from None
            if not isinstance(self._info, dict):
                raise _unreadable(self.path, JOB)
        return self._info

    @property
    def names(self):
        n = self.info.get('names')
        if not isinstance(n, list):
            raise _unreadable(self.path, f"{JOB} has no piece names")
        return [str(x) for x in n]

    @property
    def index(self):
        if self._index is None:
            import numpy as np
            try:
                z = np.load(os.path.join(self.path, INDEX))
                with z:
                    self._index = Arrays({k: z[k] for k in z.files})
            except (OSError, ValueError) as exc:
                raise _unreadable(self.path, f"{INDEX}: {exc}") from None
            missing = [k for k in REQUIRED[1][1] if k not in self._index]
            if missing:
                raise _unreadable(self.path, f"{INDEX} has no {', '.join(missing)}")
        return self._index

    def piece_ranges(self):
        """{piece: (first vertex, end)} in the garment's vertex order."""
        o = [int(x) for x in self.index['offs']] + [len(self.index['flat'])]
        names = self.names
        if len(names) != len(o) - 1:
            raise _unreadable(self.path, f"{len(names)} piece names for {len(o) - 1} pieces")
        return {n: (o[k], o[k + 1]) for k, n in enumerate(names)}

    def pattern(self):
        return job_pattern(self.path)

    def stitches(self):
        return stitches(self.index)

    def seams(self):
        """The spec seam id of each stitch (E_seam), or None."""
        return self.index.get('E_seam')

    def avatar(self):
        import numpy as np
        p = os.path.join(self.path, AVATAR)
        try:
            return np.load(p) if os.path.isfile(p) else None
        except (OSError, ValueError):
            return None

    def dressed_spec_text(self):
        """The pattern the garment was dressed with (ac9_spec.json, as text), or None (a job from before it was
        written)."""
        p = os.path.join(self.path, SPEC)
        try:
            return open(p, encoding='utf-8').read() if os.path.isfile(p) else None
        except OSError:
            return None

    def has_dressed_spec(self):
        return os.path.isfile(os.path.join(self.path, SPEC))

    def write_dressed_spec(self, text):
        open(os.path.join(self.path, SPEC), 'w', encoding='utf-8').write(text)

    def last_shape(self, required=False):
        """The last shape (meters), or None; required: ResultUnreadable instead of None."""
        X = last_frame(self.path)
        if X is None and required:
            raise _unreadable(self.path, "no frame of the result")
        return X


def open_result(job):
    return ResultFolder(job)


def write_result(d, kind, names, offs, F, fp, flat, X, spec_text=None, *, pattern=None, stitches=None, seams=None,
                 index_extra=None, info=None):
    """The one writer of a result folder made without a run (KINDS): scene.idx.npz (with E_st / E_seam when the
    stitches are known: None leaves them out, an empty array writes "no seams"), pattern.npz (given, else made from
    the mesh), the shape as stage 1's one frame, ac9_spec.json (the pattern it shows) and job.json. Returns d; the
    folder then meets REQUIRED (checked: a writer that does not is a bug, not a user's file)."""
    import json
    import numpy as np
    os.makedirs(os.path.join(d, 's1'), exist_ok=True)
    extra = dict(index_extra or {})
    if stitches is not None:
        extra['E_st'] = np.asarray(stitches, np.int64).reshape(-1, 2)
        if seams is not None:
            extra['E_seam'] = np.asarray(seams)
    np.savez(os.path.join(d, INDEX), offs=np.asarray(offs), F=F, fp=fp, flat=flat, Xinit=X, **extra)
    pat = pattern if pattern is not None else pattern_from_index({'offs': offs, 'flat': flat, 'F': F}, names)
    np.savez(os.path.join(d, PATTERN), **pat)
    np.save(os.path.join(d, 's1', FRAMES), (np.asarray(X)[None] * 0.001).astype(np.float32))
    if spec_text is not None:
        open(os.path.join(d, SPEC), 'w', encoding='utf-8').write(spec_text)
    head = {'kind': KINDS.get(kind, kind), 'names': [str(n) for n in names], 'stages': [kind],
            'verts': int(len(flat)), 'tris': int(len(F))}
    head.update(info or {})
    json.dump(head, open(os.path.join(d, JOB), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    bad = check(d)
    if bad:
        raise RuntimeError(f"AC9 Cloth Sim: a result folder written without {bad}")
    return d


def check(job):
    """What of REQUIRED a folder lacks (a list of 'file:key'), [] when it meets it. Reads the npz keys only."""
    import json
    import numpy as np
    out = []
    for f, keys in REQUIRED:
        p = os.path.join(job, f)
        if not os.path.isfile(p):
            out.append(f)
            continue
        try:
            have = set(json.load(open(p, encoding='utf-8'))) if f.endswith('.json') else set(np.load(p).files)
        except (OSError, ValueError):
            out.append(f)
            continue
        out += [f"{f}:{k}" for k in keys if k not in have]
    if last_frame(job) is None:
        out.append(FRAMES)
    return out


def pattern_from_index(idx, names):
    """The pattern.npz arrays a result folder's own mesh gives (scene.idx.npz offs / flat, job.json names): each
    piece's flat vertices in the garment's order, which is what the carry reads from it (names, V{k}; the mesh is not
    remade). 'nb' (outline vertices first) and 'h' (edge length) are written only when the mesh shows them: a piece
    whose outline is not at the front of its vertices gets no nb, and shape_changed counts it as changed."""
    import numpy as np
    offs = [int(o) for o in np.asarray(idx['offs'])] + [len(idx['flat'])]
    flat = np.asarray(idx['flat'], float)
    F = np.asarray(idx['F'], np.int64)
    out = Arrays({'names': np.array([str(n) for n in names])})      # .files, as a loaded pattern.npz has
    nb, lens = [], []
    for k in range(len(names)):
        a, b = offs[k], offs[k + 1]
        out[f"V{k}"] = flat[a:b]
        Fk = F[(F >= a).all(1) & (F < b).all(1)] - a
        e = np.sort(np.vstack([Fk[:, [0, 1]], Fk[:, [1, 2]], Fk[:, [2, 0]]]), 1)
        u, c = np.unique(e, axis=0, return_counts=True)
        if len(u):
            lens.append(np.linalg.norm(out[f"V{k}"][u[:, 0]] - out[f"V{k}"][u[:, 1]], axis=1))
        on = np.unique(u[c == 1].ravel())             # vertices on an outline (the outer one or a hole's)
        nb.append(len(on) if len(on) and (on == np.arange(len(on))).all() else -1)
    if all(n > 0 for n in nb):
        out['nb'] = np.array(nb)
    if lens:
        out['h'] = float(np.median(np.concatenate(lens)))
    return out


def job_pattern(job):
    """A result folder's pattern.npz, loaded. A folder made without one (from the .blend alone by an earlier version)
    gets it now, from its own mesh (pattern_from_index), so every reader finds the same file. Raises ValueError, said
    in one line, when it is not there and cannot be made."""
    import json
    import numpy as np
    p = os.path.join(job, PATTERN)
    if not os.path.isfile(p):
        try:
            names = json.load(open(os.path.join(job, JOB), encoding='utf-8'))['names']
            np.savez(p, **pattern_from_index(np.load(os.path.join(job, INDEX)), names))
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise _unreadable(job, f"{PATTERN} could not be made: {exc}") from None
    try:
        return np.load(p, allow_pickle=True)
    except (OSError, ValueError) as exc:
        raise _unreadable(job, f"{PATTERN}: {exc}") from None


def stitches(idx):
    """The stitches (scene.idx E_st) of a result folder, or None when the folder does not know them (made from the
    .blend alone or from a mesh by an earlier version): not the same as a garment with no seams (an empty array)."""
    files = idx.files if hasattr(idx, 'files') else idx
    return idx['E_st'] if 'E_st' in files else None


def repair(g):
    """Persist a resolved legacy cache reference only inside an explicit operation/load boundary. Idempotent."""
    ref = resolve(g)
    if ref is None or ref.source != 'cache' or str(g.get('ac9_job') or '') == ref.path:
        return False
    if not getattr(g, 'is_editable', g.library is None):
        return False
    g['ac9_job'] = ref.path
    return True
