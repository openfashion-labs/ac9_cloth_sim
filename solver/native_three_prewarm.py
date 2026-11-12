# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise Native3-only kernels on the worker's disposable four-point model."""
def warm(model, solver, pipe):
    import warp as wp
    from native_three_automatic import find_three
    from native_jacobi_diagnostic import TwoPerStencilBatch
    from native_experimental_coloring import ColoringPlan
    from native_experimental_packed import solve_elasticity_packed
    from native_experimental_small_tiles import solve_elasticity_width8
    from native_experimental_sparse_contacts import SparseContacts
    # This private fixture has one four-node bending stencil. Do not save its
    # plan in the real garment cache, or return any of its GPU resources.
    proper = ColoringPlan(model)
    rows = [tuple(map(int, row)) for row in model.tri_indices.numpy()]
    rows += [tuple(int(v) for v in row if v >= 0) for row in model.edge_indices.numpy()]
    rows += [tuple(map(int, row)) for row in model.spring_indices.numpy().reshape(-1, 2)[:model.spring_count]]
    colors, _ = find_three(sorted(set(tuple(sorted(set(row))) for row in rows if len(row) > 1)), proper.colors)
    if colors is None:
        raise ValueError('Native3 preload fixture could not be partitioned')
    TwoPerStencilBatch(model, None, colors).install()
    solver.ac9_elasticity_packed = solve_elasticity_packed
    solver.ac9_elasticity_narrow = solve_elasticity_width8
    solver.ac9_self_contact_queue = SparseContacts(solver)
    solver.ac9_self_contact_queue.enable_planar = True
    for width in (16, 8):
        solver.ac9_native_elastic_widths = (width,) * 3
        state, next_state = model.state(), model.state()
        contacts = pipe.contacts()
        state.clear_forces()
        pipe.collide(state, contacts)
        solver.step(state, next_state, model.control(), contacts, 1.0 / 600.0)
        wp.synchronize()
