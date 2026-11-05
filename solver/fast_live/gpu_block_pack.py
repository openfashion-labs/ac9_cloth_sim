# SPDX-License-Identifier: GPL-3.0-or-later
"""Pack the original FP64 projected elements without a CPU round trip."""
import warp as wp
wp.set_module_options({'enable_backward':False})

@wp.kernel
def pack_projected(element:wp.array3d[wp.float64],tri:wp.array2d[int],mass:wp.array[wp.float64],
                   rows:wp.array[int],cols:wp.array[int],values:wp.array[wp.mat33d],invalid:wp.array[int]):
    at=wp.tid();t=at//9;vr=(at%9)//3;vc=at%3
    row=tri[t,vr];col=tri[t,vc];rows[at]=row;cols[at]=col
    weight=wp.float64(0)
    if mass[row]>wp.float64(0)and mass[col]>wp.float64(0):weight=wp.float64(1)
    block=wp.mat33d(wp.float64(0))
    for i in range(3):
        for j in range(3):
            raw=element[t,vr*3+i,vc*3+j]
            value=raw*weight
            if not wp.isfinite(raw)or not wp.isfinite(value):wp.atomic_max(invalid,0,1)
            block[i,j]=value
    values[at]=block
