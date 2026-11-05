# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit worker adapter. Private resources are constructed after checkpoint restore."""
from pathlib import Path


class FastLiveError(RuntimeError):
    """The current partial frame must not be shown or checkpointed."""


class Context:
    def __init__(self, frame, **values):
        self.frame = frame
        self.values = values

    def __getitem__(self, name):
        return self.frame() if name == 'f' else self.values[name]

    def close(self):
        import atexit
        owner = self.values.get('cpu_geometry')
        if owner is not None:
            owner.close()
        atexit.unregister(self.close)


def _argument_key(value, depth=0):
    # Include all arrays and scalars supplied by Native's owning objects, including
    # nested adjacency/color structs. Contents update in place; allocations do not.
    if hasattr(value, 'ptr') and hasattr(value, 'shape'):
        return ('array', value.ptr, tuple(value.shape), str(value.dtype))
    if value is None or isinstance(value, (int, float, bool, str)):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_argument_key(v, depth+1) for v in value)
    if depth < 2 and hasattr(value, '__dict__'):
        return tuple((k, _argument_key(v, depth+1)) for k, v in sorted(vars(value).items())
                     if not callable(v) and k not in ('model', 'device'))
    return id(value)


def install_native_graph(model, solver):
    import warp as wp
    raw = solver._solve_particle_iteration
    graphs, warmed = {}, set()
    stats = {'calls': 0, 'captures': 0}
    model_names = '''particle_color_groups particle_colors particle_mass particle_flags
    particle_radius tri_indices tri_poses tri_materials tri_areas edge_indices
    edge_rest_angle edge_rest_length edge_bending_properties tet_indices tet_poses
    tet_materials spring_count spring_indices spring_rest_length spring_stiffness
    spring_damping shape_body shape_margin body_com'''.split()
    solver_names = '''particle_q_prev inertia particle_adjacency particle_forces
    particle_hessians particle_displacements pos_prev_collision_detection truncation_ts friction_epsilon ac9_bend_fric
    ac9_fric_anchor ac9_fric_hit ac9_fric_n body_particle_contact_penalty_k
    body_particle_contact_material_ke body_particle_contact_material_kd
    body_particle_contact_material_mu use_particle_tile_solve'''.split()
    contact_names = '''soft_contact_indices soft_contact_count soft_contact_max
    soft_contact_shape soft_contact_body_pos soft_contact_body_vel soft_contact_normal
    soft_contact_barycentric'''.split()

    def iteration(state_in, state_out, contacts, dt, index):
        stats['calls'] += 1
        detects = False   # whether this iteration runs the self-contact detection (Python branch, fixed in a capture)
        if solver.particle_enable_self_contact:
            F = type(solver).CollisionFrequencyType
            detects = ((solver._sc_mode_this_step == F.PRE_POST_INIT and index == 0) or
                       (solver._sc_mode_this_step == F.ITERATIONS and (index + 1) % solver._sc_freq_this_step == 0))
        extra = getattr(solver, 'ac9_graph_extra', None)   # Python branches inside the iteration (the freeing kernels)
        # ac9_step_cap_active: whether the Accurate step cap replaces Native's (a Python branch inside the iteration)
        key = (detects, extra() if extra else None, getattr(solver, 'ac9_step_cap_active', None), float(dt), tuple(_argument_key(getattr(model, n)) for n in model_names),
               tuple(_argument_key(getattr(solver, n)) for n in solver_names),
               tuple(_argument_key(getattr(state_in, n)) for n in ('particle_q','body_q','body_qd')),
               _argument_key(state_out.particle_q),
               tuple(_argument_key(getattr(contacts, n)) for n in contact_names))
        if key not in warmed:
            warmed.add(key)
            return raw(state_in, state_out, contacts, dt, index)
        if key not in graphs:
            with wp.ScopedCapture(device=solver.device) as capture:
                raw(state_in, state_out, contacts, dt, index)
            graphs[key] = capture.graph
            stats['captures'] += 1
        wp.capture_launch(graphs[key])

    solver._solve_particle_iteration = iteration
    return stats


def install(*, model, solver, cfg, state, pipe, contacts, dt, frame, output, n_stitch=None, grab=None, bf2=None, attach=None):
    import atexit
    import warp as wp
    from .cpu_hinge_geometry import CpuHingeGeometry
    from .correction import install as install_correction
    if not wp.get_device(solver.device).is_cuda:
        raise ValueError('AC9 Fast Live requires a CUDA device')
    import numpy as np
    contract = cfg['fast_live_contract']
    for field, key in [('shape_material_kd','body_kd'),('shape_material_mu','body_mu')]:
        actual = getattr(model, field).numpy()
        if not np.allclose(actual, contract[key], rtol=2e-6, atol=1e-9):
            raise ValueError('AC9 Fast Live effective model differs: '+key)
    for key in ('soft_contact_kd','soft_contact_mu'):
        if not np.isclose(getattr(model,key),contract[key],rtol=2e-6,atol=1e-9):
            raise ValueError('AC9 Fast Live effective model differs: '+key)
    # n_stitch: the pattern stitches are the first springs (newton_vbd N_ST); None = every spring
    # grab: newton_vbd GRB (spring mode) or None; its 'kp' is solved inside the correction
    context = Context(frame, model=model, solver=solver, cfg=cfg, s0=state,
                      n_stitch=model.spring_count if n_stitch is None else n_stitch, grab=grab, bf2=bf2, attach=attach,
                      pipe=pipe, contacts=contacts, dt=dt, cpu_geometry=CpuHingeGeometry())
    output = Path(output)
    output.mkdir(exist_ok=True)
    import os as _os
    context.graph_stats = {} if _os.environ.get('AC9_TEST_NO_GRAPH') else install_native_graph(model, solver)   # test switch
    try:
        context.flush = install_correction(context, output)
    except BaseException:
        context.close()
        raise
    # Covers an unexpected top-level worker exception before its normal finalizer.
    atexit.register(context.close)
    step = solver._solve_particle_iteration

    def checked(*args, **kwargs):
        try:
            return step(*args, **kwargs)
        except Exception as error:
            try:
                context.close()
            finally:
                raise FastLiveError(str(error)) from error

    solver._solve_particle_iteration = checked
    phase = context.begin_substep

    def begin_substep(state):
        try:
            return phase(state)
        except Exception as error:
            try:
                context.close()
            finally:
                raise FastLiveError(str(error)) from error

    context.begin_substep = begin_substep
    return context
