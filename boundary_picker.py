# SPDX-License-Identifier: GPL-3.0-or-later
"""A small vertex-choice popup whose hovered row highlights its real line before dragging."""
import json
import bpy,blf,gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from . import store,pattern_view as PV,view_visibility as VV
from .core import draft as D,boundary_connection as V
from .i18n import tx

_active=None


def nearby(pc,ref):
    pos=V.points(pc)
    if ref not in pos:return []
    import numpy as np
    return list(V.members(pc,ref)) if any(r[0]=='corner' for r in V.members(pc,ref)) else []


def popup_rect(context,q,count):
    """Stay inside the visible canvas, outside overlapping toolbar/sidebar regions."""
    region=context.region;left=0;right=region.width;bottom=0;top=region.height-80
    for other in context.area.regions:
        if other.type not in ('TOOLS','UI') or other.width<=1:continue
        if other.type=='TOOLS' and not context.space_data.show_region_toolbar:continue
        if other.type=='UI' and not context.space_data.show_region_ui:continue
        x0=other.x-region.x;x1=x0+other.width
        if x0<=left<x1:left=x1
        elif x0<right<=x1:right=x0
    left=max(0,left);right=max(left+1,right)
    w=min(300,max(1,right-left-24));visible=min(count,8,max(1,(top-bottom-64)//28))
    h=64+28*visible
    x=left+12 if q is not None and q.x>(left+right)*.5 else right-w-12
    return (max(left,min(x,right-w)),max(h,top),w,h),visible


def cancel_active():
    if _active is not None:_active.finish()


@persistent
def _before_load(_):cancel_active()


def register_handlers():
    if _before_load not in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.append(_before_load)


def unregister_handlers():
    cancel_active()
    if _before_load in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(_before_load)


def _valid(op):
    c=bpy.context
    try:return c.area==op.area and op.owner.matches(c) and VV.allows_pattern(c)
    except (ReferenceError,AttributeError):return False


def _line(op):
    if not _valid(op):return
    from . import draft_tool as T
    pc=D.piece(op.spec,op.piece);r=op.refs[op.index]
    if r[0]=='internal':polys=[D.internal(pc,r[1])['pts']]
    else:
        j=D.edge_index(pc,r[1]);polys=[pc['edges'][k]['pts'] for k in (j-1,j)]
    M=PV.layout_matrix(bpy.context);segs=[]
    for p in polys:
        W=T.to_world(M,p);segs += [tuple(q) for a,b in zip(W[:-1],W[1:]) for q in (a,b)]
    sh=gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR');sh.bind()
    gpu.state.blend_set('ALPHA');gpu.state.depth_test_set('NONE')
    sh.uniform_float('viewportSize',(bpy.context.region.width,bpy.context.region.height))
    sh.uniform_float('lineWidth',4.0);sh.uniform_float('color',(1.,.55,.05,1.))
    if segs:batch_for_shader(sh,'LINES',{'pos':segs}).draw(sh)
    T._marks(bpy.context,[tuple(T.to_world(M,[V.points(pc)[r]])[0])],(1,1,1,1),8,hollow=True)
    gpu.state.blend_set('NONE')


def _rows(op):
    if not _valid(op):return
    sh=gpu.shader.from_builtin('UNIFORM_COLOR');sh.bind();gpu.state.blend_set('ALPHA')
    x,y,w,h=op.rect
    def box(y0,y1,color):
        sh.bind();sh.uniform_float('color',color)
        batch_for_shader(sh,'TRIS',{'pos':[(x,y0),(x+w,y0),(x+w,y1),(x,y0),(x+w,y1),(x,y1)]}).draw(sh)
    box(y-h,y,(.035,.035,.035,.98))
    def text(label,yy):
        blf.size(0,13);blf.color(0,1,1,1,1);blf.position(0,x+10,yy,0);blf.draw(0,label)
    text(tx('Choose a vertex to separate'),y-22)
    for row,i in enumerate(range(op.first,min(len(op.refs),op.first+op.visible))):
        top=y-32-row*28
        if i==op.index:box(top-27,top,(.18,.32,.48,1.))
        text(tx('Vertex {0}',i+1),top-19)
    text(tx('Click: choose   Wheel: switch   Esc: cancel'),y-h+8)
    gpu.state.blend_set('NONE')


class AC9_OT_SimSeparateVertex(bpy.types.Operator):
    """Choose one overlapping authored vertex, then move it independently"""
    bl_idname='ac9_cloth_sim.separate_vertex'
    bl_label='Separate Vertex and Move'
    bl_options={'INTERNAL','BLOCKING'}
    piece:bpy.props.StringProperty(options={'HIDDEN','SKIP_SAVE'})
    vertex:bpy.props.StringProperty(options={'HIDDEN','SKIP_SAVE'})

    @classmethod
    def poll(cls,context):
        from . import toolbar as TB
        return context.area is not None and context.area.type=='VIEW_3D' and TB.tool_policy(context)['domain']=='element' and store.get(context)[0] is not None

    def invoke(self,context,event):
        global _active
        cancel_active()
        self.spec,_=store.get(context);self.owner=store.edit_owner(context)
        try:self.refs=nearby(D.piece(self.spec,self.piece),tuple(json.loads(self.vertex)))
        except (ValueError,KeyError,TypeError):return {'CANCELLED'}
        if len(self.refs)<2:return {'CANCELLED'}
        self.area=context.area;self.index=0;self.first=0;self.pressed=None
        from . import draft_tool as T
        ref=tuple(json.loads(self.vertex));pc=D.piece(self.spec,self.piece)
        q=T.location_3d_to_region_2d(context.region,context.region_data,T.Vector(T.to_world(PV.layout_matrix(context),[V.points(pc)[ref]])[0]))
        self.rect,self.visible=popup_rect(context,q,len(self.refs))
        self.handles=[bpy.types.SpaceView3D.draw_handler_add(_line,(self,),'WINDOW','POST_VIEW'),
                      bpy.types.SpaceView3D.draw_handler_add(_rows,(self,),'WINDOW','POST_PIXEL')]
        _active=self;context.window_manager.modal_handler_add(self);self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def finish(self):
        global _active
        for h in getattr(self,'handles',[]):
            try:bpy.types.SpaceView3D.draw_handler_remove(h,'WINDOW')
            except (ReferenceError,ValueError):pass
        self.handles=[]
        if _active is self:_active=None
        try:self.area.tag_redraw()
        except ReferenceError:pass

    def modal(self,context,event):
        if not self.owner.matches(context) or event.type in ('ESC','RIGHTMOUSE'):
            self.finish();return {'CANCELLED'}
        if event.type in ('WHEELUPMOUSE','WHEELDOWNMOUSE','TAB') and self.pressed is None:
            self.index=(self.index+(-1 if event.type=='WHEELUPMOUSE' or event.shift else 1))%len(self.refs)
            self.first=max(0,min(self.first,self.index))
            self.first=max(self.first,self.index-self.visible+1)
        x,y,w,h=self.rect
        row=int((y-32-event.mouse_region_y)//28)
        inside=x<=event.mouse_region_x<=x+w and 0<=row<self.visible and self.first+row<len(self.refs)
        if inside and event.type=='MOUSEMOVE' and self.pressed is None:self.index=self.first+row
        if event.type=='LEFTMOUSE' and event.value=='PRESS':
            if not inside:self.finish();return {'CANCELLED'}
            self.index=self.first+row;self.pressed=self.index
        if event.type=='LEFTMOUSE' and event.value=='RELEASE' and self.pressed is not None:
            if not inside or self.first+row!=self.pressed:self.finish();return {'CANCELLED'}
            if not self.owner.matches(context,content=True):self.finish();return {'CANCELLED'}
            self.index=self.pressed;r=self.refs[self.index];self.finish()
            props=dict(target_piece=self.piece)
            if r[0]=='internal':props.update(move_only='INTERNAL',target_line=r[1],target_node=r[2])
            else:props.update(move_only='OUTLINE',target_corner=D.edge_index(D.piece(self.spec,self.piece),r[1]))
            bpy.ops.ac9_cloth_sim.pattern_drag('INVOKE_DEFAULT',True,**props)
            return {'FINISHED'}
        self.area.tag_redraw();return {'RUNNING_MODAL'}

    def cancel(self,context):self.finish()
