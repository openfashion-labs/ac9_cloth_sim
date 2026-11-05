"""Conditional checkpoint contract for the opt-in terminal target experiment."""
import hashlib
import os
import numpy as np


def _bf_definition(bf, read):
    if bf is None:return None
    result={key:bf.get(key) for key in ('rat','inside','n','mm','freeze')}
    for key in ('rows','r_np','ei','thy'):
        if key in bf:
            a=np.ascontiguousarray(read(bf[key]));result[key]={'dtype':a.dtype.str,'shape':list(a.shape),'sha256':hashlib.sha256(a.tobytes()).hexdigest()}
    return result


def generator_hash():
    path=os.path.join(os.path.dirname(os.path.dirname(__file__)),'core','fold_reference.py')
    with open(path,'rb') as stream:return hashlib.sha256(stream.read()).hexdigest()


def build(g):
    helper=g['_terminal'];model=g['model']
    if generator_hash()!=g['TERMINAL_GENERATOR_SHA']:raise ValueError('Terminal generator changed during construction')
    base=g['TERMINAL_TARGET_BASE'].copy()
    metric={k:getattr(model,k).numpy() for k in ('tri_poses','tri_areas','particle_mass','edge_rest_length','edge_bending_properties')}
    with open(g['cfg']['pattern'],'rb') as stream:pattern=hashlib.sha256(stream.read()).hexdigest()
    manifest=helper.field_manifest(g['cfg']['experimental_fold_terminal'],g['d']['flat'],g['F'],model.edge_indices.numpy(),base,g['log']['experimental_fold_terminal'],metric,pattern,generator_hash())
    return {'manifest':manifest,'base_rest':base,'bf2_definition':_bf_definition(g.get('BF2'),lambda value:value.numpy() if hasattr(value,'numpy') else value)}


def validate(g, roots, read, frame):
    """Validate saved definitions and history relationships before any writes."""
    state=roots['globals'].get('TERMINAL_FIELD_STATE')
    if not g['cfg'].get('experimental_fold_terminal'):
        if state is not None:raise ValueError('Unexpected terminal checkpoint state')
        return
    fresh=g.get('TERMINAL_FIELD_STATE')
    if state is None or fresh is None:raise ValueError('Terminal checkpoint definition is missing')
    if generator_hash()!=g['TERMINAL_GENERATOR_SHA'] or generator_hash()!=fresh['manifest']['generator_sha256']:raise ValueError('Terminal generator changed')
    g['_terminal'].validate_field_manifest(state['manifest'],fresh['manifest'],read(state['base_rest']),fresh['base_rest'])
    if state.get('bf2_definition')!=fresh['bf2_definition'] or _bf_definition(roots['globals'].get('BF2'),read)!=fresh['bf2_definition']:raise ValueError('Terminal BF2 definition belongs to another generation')
    rest=read(roots['model']['edge_rest_angle']);props=read(roots['model']['edge_bending_properties']);base=fresh['base_rest']
    if rest.shape!=base.shape or rest.dtype!=base.dtype or not np.isfinite(rest).all():raise ValueError('Invalid terminal effective rest')
    bf=roots['globals'].get('BF2');original=g.get('BF2');expected=base.copy();dynamic_rows=np.empty(0,dtype=int)
    if (bf is None)!=(original is None):raise ValueError('Terminal BF2 configuration differs')
    if bf is not None:
        rows=np.asarray(bf['r_np']);reference=np.asarray(original['r_np'])
        if not np.array_equal(rows,reference) or not np.array_equal(read(bf['rows']),reference):raise ValueError('Terminal BF2 mapping differs')
        if bf['rat']!=original['rat'] or bf['inside']!=original['inside']:raise ValueError('Terminal BF2 law differs')
        on=read(bf['on'])
        if on.shape!=(1,) or on[0] not in (0,1):raise ValueError('Invalid terminal BF2 activation')
        started=bf['f_on'] is not None
        if not started and on[0]!=0:raise ValueError('Terminal BF2 generation is mixed')
        if not started and any(np.any(read(bf[key])!=0) for key in ('ra0','k0','st')):raise ValueError('Terminal inactive history belongs to another generation')
        if bf['inside'] and not started:
            history=read(roots['solver']['ac9_bend_fric'])
            if history.shape!=(len(base),3) or not np.isfinite(history).all() or np.any(history!=0):raise ValueError('Terminal inactive inside history differs')
        if started and on[0]==0 and not bf.get('freeze'):raise ValueError('Terminal active history lost its activation')
        if started:
            if not 1<=bf['f_on']<=frame or bf['closed'] is None or bf['closed']>bf['f_on']:raise ValueError('Terminal BF2 time differs')
            ra0=read(bf['ra0']);k0=read(bf['k0']);st=read(bf['st'])
            if not np.array_equal(ra0,base[rows]):raise ValueError('Terminal BF2 base belongs to another field')
            # At save time model properties are effective; immutable initial values
            # come from the construction snapshot recorded by the backend.
            initial_k=g['TERMINAL_INITIAL_PROPS'][rows,0]
            if not np.array_equal(k0,initial_k):raise ValueError('Terminal BF2 stiffness belongs to another generation')
            if not bf['inside']:
                dynamic_rows=rows
                rat=np.float32(bf['rat']);expected[rows]=(ra0+rat*st)/np.float32(1+rat)
                if not np.allclose(props[rows,0],k0*np.float32(1+rat),rtol=3e-6,atol=1e-10):raise ValueError('Terminal effective BF2 stiffness differs')
            else:
                history=read(roots['solver']['ac9_bend_fric'])
                expected_history=np.zeros((len(base),3),dtype=np.float32)
                expected_history[rows,0]=st;expected_history[rows,1]=read(bf['thy']);expected_history[rows,2]=np.float32(bf['rat'])
                if history.shape!=expected_history.shape or not np.isfinite(history).all() or not np.array_equal(history,expected_history):raise ValueError('Terminal inside friction history or law differs')
    fixed=np.ones(len(rest),bool);fixed[dynamic_rows]=False
    if not np.array_equal(rest[fixed],expected[fixed]):raise ValueError('Terminal fixed base rest differs')
    if len(dynamic_rows) and not np.allclose(rest[dynamic_rows],expected[dynamic_rows],rtol=3e-6,atol=2e-6):raise ValueError('Terminal effective rest and history generations differ')
    fixed_props=np.ones(props.shape,bool);fixed_props[dynamic_rows,0]=False
    if not np.array_equal(props[fixed_props],g['TERMINAL_INITIAL_PROPS'][fixed_props]):raise ValueError('Terminal fixed bending properties differ')
