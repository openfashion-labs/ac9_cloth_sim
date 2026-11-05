"""Read current native inputs to form a whole-patch force/H shadow."""
import warp as wp
import numpy as np
from newton._src.solvers.vbd import particle_vbd_kernels as K


@wp.kernel
def add_elastic_and_inertia(vertices:wp.array[int],pos:wp.array[wp.vec3],prev:wp.array[wp.vec3],tri:wp.array2d[int],poses:wp.array[wp.mat22],materials:wp.array2d[float],areas:wp.array[float],edges:wp.array2d[int],angles:wp.array[float],lengths:wp.array[float],bend:wp.array2d[float],bf:wp.array2d[float],adj:K.MeshAdjacencyData,mass:wp.array[float],inertia:wp.array[wp.vec3],dt:float,force:wp.array[wp.vec3],hessian:wp.array[wp.mat33]):
    v=vertices[wp.tid()];f=wp.vec3(0.);h=wp.mat33(0.)
    for j in range(K.get_vertex_num_adjacent_faces(adj,v)):
        t,order=K.get_vertex_adjacent_face_id_order(adj,v,j)
        if materials[t,0]>0. or materials[t,1]>0.:
            if K.SIMSPIKE_MEMBRANE_BW==1:
                ft,ht=K.evaluate_bw_membrane_force_hessian(t,order,pos,prev,tri,poses[t],areas[t],materials[t,0],materials[t,1],materials[t,3],materials[t,2],dt)
            else:
                ft,ht=K.evaluate_neo_hookean_membrane_force_hessian(t,order,pos,prev,tri,poses[t],areas[t],materials[t,0],materials[t,1],materials[t,2],dt)
            f+=ft;h+=ht
    for j in range(K.get_vertex_num_adjacent_edges(adj,v)):
        e,order=K.get_vertex_adjacent_edge_id_order(adj,v,j)
        if bend[e,0]>0.:
            stiffness=bend[e,0]
            if K.SIMSPIKE_BUCKLE_RATIO>0.:
                stiffness=stiffness*K.simspike_buckle_factor(e,edges,pos,tri,poses,adj)
            fe,he=K.evaluate_dihedral_angle_based_bending_force_hessian(e,order,pos,prev,edges,angles,lengths,stiffness,bend[e,1],dt,bf)
            f+=fe;h+=he
    f+=mass[v]*(inertia[v]-pos[v])/(dt*dt);h+=mass[v]/(dt*dt)*wp.identity(3,dtype=float)
    force[v]=force[v]+f;hessian[v]=hessian[v]+h


def add_elastic(g,force,hessian,movable=None):
    m=g['m'];s=g['s'];state=g['state'];dt=g['dt'];ids=np.arange(m.particle_count,dtype=np.int32)if movable is None else np.asarray(movable,np.int32)
    wp.launch(add_elastic_and_inertia,dim=len(ids),inputs=[wp.array(ids,dtype=int),state.particle_q,s.particle_q_prev,m.tri_indices,m.tri_poses,m.tri_materials,m.tri_areas,m.edge_indices,m.edge_rest_angle,m.edge_rest_length,m.edge_bending_properties,s.ac9_bend_fric,s.particle_adjacency,m.particle_mass,s.inertia,dt,force,hessian])


def evaluate(g,movable=None,include_external=True):
    m=g['m'];s=g['s'];state=g['state'];p=g['p'];dt=g['dt'];captured=g['captured'];N=m.particle_count
    if m.tet_count:raise ValueError('Tet forces are not in the cloth shadow prototype')
    if getattr(s,'ac9_bound_constraints',None)is not None:raise ValueError('Dependent DOF mapping not implemented in this shadow')
    colors=wp.full(N,-1,dtype=int);force=wp.zeros(N,dtype=wp.vec3);hessian=wp.zeros(N,dtype=wp.mat33)
    crossing=s.ac9_layer_crossings
    wp.launch(g['LC'].accumulate,dim=crossing['active'].shape[0],inputs=[state.particle_q,m.tri_indices,colors,-1,p,crossing['active'],crossing['count'],s.particle_self_contact_margin,m.soft_contact_ke,force,hessian])
    for name in ['accumulate_particle_body_contact_force_and_hessian','accumulate_spring_force_and_hessian','accumulate_self_contact_force_and_hessian']:
        if name not in captured:continue
        call=dict(captured[name]);inputs=list(call['inputs']);inputs[1]=0;inputs[4]=colors;call['inputs']=inputs;call['outputs']=[force,hessian];wp.launch(**call)
    external_f=wp.clone(force)if include_external else None;external_h=wp.clone(hessian)if include_external else None
    add_elastic(g,force,hessian,movable)
    return {'force':force,'hessian':hessian,'external_force':external_f,'external_hessian':external_h}
