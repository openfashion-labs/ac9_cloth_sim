# SPDX-License-Identifier: GPL-3.0-or-later
"""Newton's collision narrow phase makes its Warp kernels with factory functions every time a CollisionPipeline is
made (newton._src.geometry.narrow_phase: create_narrow_phase_*). The factories build the kernel's Python source tree
each call; the compiled module is found in Warp's cache, but building the kernel objects took about 0.13 s of every
fresh model (a test T-shirt, dev PC), on every pattern edit in a waiting solver.

install(module) wraps the module's factories so a call with the same arguments returns the kernel made the first time
in this process. A kernel holds no model data (the arrays come with each launch), so one kernel serves every pipeline.
Arguments that cannot be hashed go to the factory as before. Env AC9_KERNEL_MEMO=0 leaves the factories alone."""
import functools
import os

FACTORIES = ('create_narrow_phase_primitive_kernel', 'create_narrow_phase_kernel_gjk_mpr',
             'create_narrow_phase_kernels_gjk_mpr_split', 'create_narrow_phase_process_mesh_triangle_contacts_kernel',
             'create_narrow_phase_process_mesh_plane_contacts_kernel',
             'create_narrow_phase_process_mesh_mesh_contacts_kernel')


def _memo(factory):
    made = {}

    @functools.wraps(factory)
    def call(*args, **kwargs):
        try:
            key = (args, tuple(sorted(kwargs.items())))
            hash(key)
        except TypeError:
            return factory(*args, **kwargs)
        if key not in made:
            made[key] = factory(*args, **kwargs)
        return made[key]
    call._ac9_memo = made
    return call


def install(module, names=FACTORIES):
    """Wrap the factories of `module` that it calls by its own global names. Returns the names wrapped."""
    if os.environ.get('AC9_KERNEL_MEMO', '1') == '0':
        return []
    done = []
    for name in names:
        factory = getattr(module, name, None)
        if factory is None or hasattr(factory, '_ac9_memo'):
            continue
        setattr(module, name, _memo(factory))
        done.append(name)
    return done
