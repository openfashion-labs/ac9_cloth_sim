# SPDX-License-Identifier: GPL-3.0-or-later
"""A temporary, viewport-owned input window. No data edits or undo entries here.

The existing modal operator owns the preview and the transaction. Only public
GPU/BLF/RNA APIs are used. Layout is collected when values change, never by the
draw callback; navigation therefore does not rebuild pattern geometry.
"""
import math
import shutil
import tempfile
import time
from pathlib import Path

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

from . import units as U
from .core.edit_text import Text, number
from .core.unit_expression import length
from .i18n import tx


def hit(rect, x, y):
    a, b, w, h = rect
    return a <= x < a+w and b <= y < b+h


def intersect(a,b):
    x,y=max(a[0],b[0]),max(a[1],b[1])
    right,top=min(a[0]+a[2],b[0]+b[2]),min(a[1]+a[3],b[1]+b[3])
    return x,y,max(0,right-x),max(0,top-y)


def canvas(area, region=None):
    region = region or next(r for r in area.regions if r.type == 'WINDOW')
    left, bottom, right, top = 0, 0, region.width, region.height
    for r in area.regions:
        if r.width <= 1 or r.height <= 1: continue
        if r.type == 'TOOLS': left = max(left, r.x+r.width-region.x)
        elif r.type == 'UI': right = min(right, r.x-region.x)
        elif r.type in ('HEADER', 'TOOL_HEADER'):
            if r.y > region.y+region.height/2: top = min(top, r.y-region.y)
            else: bottom = max(bottom, r.y+r.height-region.y)
        elif r.type in ('ASSET_SHELF', 'ASSET_SHELF_HEADER'):
            bottom = max(bottom, r.y+r.height-region.y)
    return left, bottom, max(left+1, right), max(bottom+1, top)


class Layout:
    """The small subset used by the three operators' existing draw_fields."""
    def __init__(self, items=None, parent=None):
        self.items = [] if items is None else items
        self.parent = parent
        self.enabled = True

    def column(self, **kwargs): return Layout(self.items, self)
    def row(self, **kwargs): return Layout(self.items, self)
    def box(self): return self.column()
    def separator(self, **kwargs): self.label(text='')

    def active(self):
        return self.enabled and (self.parent is None or self.parent.active())

    def label(self, text='', icon='NONE', **kwargs):
        self.items.append(('LABEL', self, str(text), icon))

    def prop(self, owner, name, text=None, index=-1, expand=False, **kwargs):
        p = owner.properties.bl_rna.properties[name]
        label = tx(p.name) if text is None else str(text)
        field = Field(owner, name, index, label, p, self)
        if p.type == 'ENUM':
            if label: self.label(text=label)
            choices=getattr(owner,'parameter_choices',{}).get(name)
            items=[p.enum_items[k] for k in choices] if choices else p.enum_items
            for item in items:
                self.items.append(('ENUM', self, field, (item.identifier, tx(item.name))))
        else:
            self.items.append(('FIELD', self, field, None))


class Field:
    def __init__(self, owner, name, index, label, prop, layout):
        self.owner, self.name, self.index = owner, name, index
        self.label, self.prop, self.layout = label, prop, layout
        self.key = (name, index)

    def get(self):
        value = getattr(self.owner, self.name)
        return value[self.index] if self.index >= 0 else value

    def set(self, value):
        if self.index >= 0:
            values = list(getattr(self.owner, self.name)); values[self.index] = value
            setattr(self.owner, self.name, values)
        else: setattr(self.owner, self.name, value)

    def display(self, context, editing=False):
        value = self.get()
        if self.prop.type == 'INT': return str(value)
        if self.prop.unit == 'LENGTH':
            if editing:
                factor, symbol = U.unit(context)
                return f'{value*1000/factor:.12g} {symbol}'
            return U.fmt(context, value*1000)
        return f'{value:.12g}'

    def parse(self, context, expression):
        if self.prop.type == 'INT':
            value = number(expression)
            if abs(value-round(value)) > 1e-8: raise ValueError('integer required')
            value = int(round(value))
        elif self.prop.unit == 'LENGTH':
            value = length(expression, U.unit(context)[0]/1000, context.scene.unit_settings.system)
        else: value = number(expression)
        if not math.isfinite(value) or not self.prop.hard_min <= value <= self.prop.hard_max:
            raise ValueError('value outside allowed range')
        return value

    def step(self, context):
        if self.prop.type=='INT':return 1
        return .0000254 if context.scene.unit_settings.system=='IMPERIAL' else .001


class Window:
    def __init__(self, context, op):
        self.window, self.area = context.window, context.area
        self.region = op._parameter_region
        self.op, self.items, self.rows = op, [], []
        self.key = None
        self.edit = self.edit_key = None
        self.text_offset = 0
        self.error = ''
        self.changed = False
        self.scroll = 0
        self.drag = self.selecting = None
        self.position = None
        self.bounds = (0, 0, 0, 0)
        self.closed = False
        self.shader = None
        self.draw_ms = []
        self.draw_error = ''
        self.handler = None
        self.font = None
        self.font_dir = None
        try:
            folder=Path(bpy.utils.system_resource('DATAFILES',path='fonts'))
            custom=getattr(context.preferences.view,'font_path_ui','')
            source=Path(bpy.path.abspath(custom)) if custom else folder/'Inter.woff2'
            if not source.is_file():source=folder/'Inter.woff2'
            self.font_dir=tempfile.TemporaryDirectory(prefix='ac9_edit_font_')
            self.font_path=Path(self.font_dir.name)/source.name
            shutil.copyfile(source,self.font_path)
            self.font=blf.load(str(self.font_path))
            if self.font<0:raise RuntimeError('font unavailable')
            self.refresh(context, None)
            self.handler = bpy.types.SpaceView3D.draw_handler_add(self.draw, (), 'WINDOW', 'POST_PIXEL')
        except Exception:
            self.close()
            raise

    def close(self):
        if self.closed: return
        self.closed = True
        if self.handler is not None:
            try:bpy.types.SpaceView3D.draw_handler_remove(self.handler, 'WINDOW')
            except (ReferenceError,RuntimeError,ValueError):pass
        if self.font is not None:
            try:blf.unload(str(self.font_path))
            except RuntimeError:pass
        if self.font_dir is not None:
            root=Path(self.font_dir.name).resolve()
            if root.parent==Path(tempfile.gettempdir()).resolve() and root.name.startswith('ac9_edit_font_'):
                try:self.font_dir.cleanup()
                except OSError:pass
        self.op = None; self.items = []; self.rows = []; self.edit = None

    def refresh(self, context, key):
        if key is not None and key == self.key: return
        layout = Layout()
        self.op.draw_fields(context, layout)
        self.items, self.key = layout.items, key
        self.blocked = any(i[0]=='LABEL' and i[3]=='ERROR' for i in self.items)

    def field(self, key):
        return next((i[2] for i in self.items if i[0] == 'FIELD' and i[2].key == key), None)

    def begin(self, context, field):
        self.edit_key = field.key
        self.edit = Text(field.display(context, editing=True))
        self.text_offset = 0; self.error = ''

    def commit(self, context):
        if self.edit is None: return True
        field = self.field(self.edit_key)
        if field is None: self.edit = self.edit_key = None; return True
        try:
            field.set(field.parse(context, self.edit.text))
        except (ValueError, TypeError, SyntaxError, ZeroDivisionError, OverflowError):
            self.error = tx('Invalid value'); return False
        self.edit = self.edit_key = None
        self.error = ''; self.changed = True
        return True

    def _wrap(self, text, width):
        out, line = [], ''
        for char in text:
            if char == '\n' or (line and blf.dimensions(self.font, line+char)[0] > width):
                out.append(line); line = ''
            if char != '\n': line += char
        if line or not out: out.append(line)
        return out

    def geometry(self, context):
        self.scale = max(.5, context.preferences.system.ui_scale)
        s = self.scale
        self.font_size=context.preferences.ui_styles[0].widget.points*s
        blf.size(self.font,self.font_size)
        l, b, r, t = canvas(self.area,self.region)
        self.width = max(80, min(300*s, r-l-12*s))
        self.line = 22*s; pad = 8*s
        rows, height = [], 0
        enum_counts={}
        for kind,layout,value,extra in self.items:
            if kind=='ENUM':enum_counts[value.key]=enum_counts.get(value.key,0)+1
        enum_seen={}
        for kind, layout, value, extra in self.items:
            label = value if kind == 'LABEL' else extra[1] if kind == 'ENUM' else value.label
            columns=min(2,enum_counts.get(value.key,1)) if kind=='ENUM' else 1
            cell_width=(self.width-pad*2)/columns
            wrap = self._wrap(label,cell_width-12*s if kind=='ENUM' else self.width-pad*2-(22*s if kind != 'LABEL' else 0))
            h = max(self.line, (len(wrap)*17+7)*s)
            if kind == 'FIELD' and value.prop.type in ('FLOAT', 'INT'): h = self.line
            if kind == 'LABEL' and not value: h = 6*s
            index=enum_seen.get(value.key,0) if kind=='ENUM' else 0
            rows.append(dict(kind=kind,field=value,extra=extra,lines=wrap,enabled=layout.active(),h=h,columns=columns,column=index%columns))
            if kind=='ENUM':enum_seen[value.key]=index+1
            if kind!='ENUM' or index%columns==0:height+=h
        for i,row in enumerate(rows):
            if row['kind']=='ENUM' and row['column']==0:
                group=[v for v in rows[i:i+row['columns']] if v['kind']=='ENUM' and v['field'].key==row['field'].key]
                h=max(v['h'] for v in group)
                for v in group:v['h']=h
        height=sum(v['h'] for v in rows if v['kind']!='ENUM' or v['column']==0)
        if self.error:
            lines = self._wrap(self.error, self.width-2*pad)
            h = (len(lines)*17+8)*s
            rows.append(dict(kind='ERROR', field=None, extra=None, lines=lines, enabled=True, h=h)); height += h
        head, foot = 43*s, 36*s
        self.height = max(1, min(height+head+foot+pad, max(160*s,t-b-130*s),t-b-12*s))
        if self.position is None:
            # The title can be dragged. Initial placement stays inside this viewport.
            self.position = (r-self.width-42*s, t-115*s)
        x, top = self.position
        x = min(max(x, l+4*s), max(l+4*s, r-self.width-4*s))
        top = min(max(top, b+self.height+4*s), t-4*s)
        self.position = x, top
        y = top-self.height
        self.bounds = (x, y, self.width, self.height)
        self.body = (x+pad, y+foot, self.width-2*pad, max(0, self.height-head-foot))
        self.scroll = max(0, min(self.scroll, max(0, height-self.body[3])))
        cy = top-head+self.scroll
        for row in rows:
            if row['kind']!='ENUM' or row['column']==0:cy-=row['h']
            cell_width=(self.width-2*pad)/row.get('columns',1)
            row['rect']=(x+pad+row.get('column',0)*cell_width,cy,cell_width,row['h'])
        self.rows = rows
        self.title = (x, top-head, self.width, head)
        self.cancel = (x+self.width/2+3*s, y+8*s, self.width/2-13*s, 20*s)
        self.apply = (x+10*s, y+8*s, self.width/2-13*s, 20*s)

    def _colors(self, context):
        ui = context.preferences.themes[0].user_interface
        def rgba(value, fallback):
            p = list(value) if value is not None else list(fallback)
            if max(p) > 1: p = [v/255 for v in p]
            return tuple(p[:3])+((p[3] if len(p)>3 else 1),)
        bg=rgba(ui.wcol_menu_back.inner,(.1,.1,.1,1))[:3]+(1,)
        return dict(bg=bg,
                    field=rgba(ui.wcol_num.inner, (.23,.23,.23,1)),
                    selected=rgba(ui.wcol_regular.inner_sel,(.18,.35,.6,1)),
                    field_sel=rgba(ui.wcol_num.inner_sel,(.13,.13,.13,1)),
                    field_text=rgba(ui.wcol_num.text,(.9,.9,.9,1)),
                    field_text_sel=rgba(ui.wcol_num.text_sel,(1,1,1,1)),
                    button=rgba(ui.wcol_regular.inner,(.3,.3,.3,1)),
                    button_text=rgba(ui.wcol_regular.text,(.9,.9,.9,1)),
                    button_text_sel=rgba(ui.wcol_regular.text_sel,(1,1,1,1)),
                    radio_text=rgba(ui.wcol_radio.text,(.9,.9,.9,1)),
                    radio_text_sel=rgba(ui.wcol_radio.text_sel,(1,1,1,1)),
                    radio=rgba(ui.wcol_radio.inner,(.3,.3,.3,1)),
                    radio_sel=rgba(ui.wcol_radio.inner_sel,(.18,.35,.6,1)),
                    toggle=rgba(ui.wcol_option.inner,(.3,.3,.3,1)),
                    toggle_sel=rgba(ui.wcol_option.inner_sel,(.18,.35,.6,1)),
                    outline=rgba(ui.wcol_num.outline,(.24,.24,.24,1)),
                    text=rgba(ui.wcol_regular.text, (.9,.9,.9,1)))

    def box(self,rect,color,clip=None,radius=0):
        x,y,w,h=rect
        if clip:
            a,b,c,d=clip;x1,y1=max(x,a),max(y,b)
            w,h=min(x+w,a+c)-x1,min(y+h,b+d)-y1;x,y=x1,y1
        if w<=0 or h<=0:return
        radius=min(radius,w/2,h/2)
        if radius:
            vertices=[]
            for cx,cy,start in ((x+w-radius,y+radius,-90),(x+w-radius,y+h-radius,0),(x+radius,y+h-radius,90),(x+radius,y+radius,180)):
                for k in range(5):
                    angle=math.radians(start+k*22.5)
                    vertices.append((cx+radius*math.cos(angle),cy+radius*math.sin(angle),0))
            center=(x+w/2,y+h/2,0)
            triangles=[p for k,v in enumerate(vertices) for p in (center,v,vertices[(k+1)%len(vertices)])]
        else:triangles=[(x,y,0),(x+w,y,0),(x+w,y+h,0),(x,y,0),(x+w,y+h,0),(x,y+h,0)]
        self.shader.bind();self.shader.uniform_float('color',color)
        batch_for_shader(self.shader,'TRIS',{'pos':triangles}).draw(self.shader)

    def text(self, x, y, value, color, clip):
        a,b,w,h = clip
        blf.enable(self.font, blf.CLIPPING); blf.clipping(self.font,a,b,a+w,b+h)
        blf.color(self.font,*color); blf.position(self.font,x,y,0); blf.draw(self.font,str(value))
        blf.disable(self.font, blf.CLIPPING)

    def value_rect(self, row):
        x,y,w,h=row['rect']
        return x,y+self.scale,w,h-2*self.scale

    def draw(self):
        if self.closed: return
        context = bpy.context
        if context.window != self.window or context.area != self.area or context.region != self.region: return
        start = time.perf_counter()
        blend,depth=gpu.state.blend_get(),gpu.state.depth_test_get()
        try:
            self.geometry(context)
            if self.shader is None: self.shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            c=self._colors(context); s=self.scale; x,y,w,h=self.bounds
            gpu.state.blend_set('ALPHA'); gpu.state.depth_test_set('NONE')
            self.box(self.bounds,c['bg'],radius=5*s)
            self.box((x+8*s,y+h-32*s,w-16*s,s),c['outline'])
            self.text(x+8*s,y+h-21*s,tx(self.op.bl_label),c['text'],self.title)
            self.text(x+w-22*s,y+h-21*s,'×',c['text'],self.title)
            for row in self.rows:
                rx,ry,rw,rh=row['rect']
                if ry+rh<self.body[1] or ry>self.body[1]+self.body[3]: continue
                kind, f = row['kind'], row['field']
                color=c['text'] if row['enabled'] else tuple(v*.55 for v in c['text'][:3])+(1,)
                if kind in ('LABEL','ERROR'):
                    color=(1,.4,.3,1) if kind=='ERROR' or row['extra']=='ERROR' else color
                    for i,line in enumerate(row['lines']): self.text(rx,ry+rh-(16+i*17)*s,line,color,self.body)
                elif kind=='ENUM':
                    selected=f.get()==row['extra'][0]
                    color=c['radio_text_sel'] if selected else c['radio_text']
                    if not row['enabled']:color=tuple((a+b)/2 for a,b in zip(color,c['radio_sel'] if selected else c['radio']))
                    rect=(rx+s,ry+s,rw-2*s,rh-2*s)
                    self.box(rect,c['radio_sel'] if selected else c['radio'],self.body,radius=3*s)
                    for i,line in enumerate(row['lines']):
                        tw=blf.dimensions(self.font,line)[0]
                        self.text(rx+(rw-tw)/2,ry+rh-(16+i*17)*s,line,color,self.body)
                elif f.prop.type=='BOOLEAN':
                    selected=bool(f.get());check=(rx,ry+(rh-13*s)/2,13*s,13*s)
                    self.box(check,c['toggle_sel'] if selected else c['toggle'],self.body,radius=2*s)
                    if selected:self.text(check[0]+s,check[1]+s,'✓',color,self.body)
                    for i,line in enumerate(row['lines']):self.text(rx+20*s,ry+rh-(16+i*17)*s,line,color,self.body)
                else:
                    vr=self.value_rect(row);editing=self.edit is not None and self.edit_key==f.key
                    self.box(vr,c['field_sel'] if editing else c['field'],self.body,radius=3*s)
                    color=c['field_text_sel'] if editing else c['field_text']
                    if not row['enabled']:color=tuple((a+b)/2 for a,b in zip(color,c['field']))
                    if not editing:self.text(rx+6*s,ry+rh-16*s,f.label,color,intersect((rx+4*s,ry,rw*.5-8*s,rh),self.body))
                    a,b,vw,vh=vr; clip=intersect((a+4*s,b,vw-8*s,vh),self.body)
                    if editing:
                        caret=blf.dimensions(self.font,self.edit.text[:self.edit.cursor])[0]
                        self.text_offset=max(0,min(self.text_offset,caret))
                        self.text_offset=max(self.text_offset,caret-(vw-12*s))
                        lo,hi=self.edit.selection
                        px=blf.dimensions(self.font,self.edit.text[:lo])[0];pz=blf.dimensions(self.font,self.edit.text[:hi])[0]
                        self.box((a+4*s+px-self.text_offset,b+2*s,pz-px,vh-4*s),c['selected'],clip)
                        self.text(a+4*s-self.text_offset,b+5*s,self.edit.text,color,clip)
                        self.box((a+4*s+caret-self.text_offset,b+3*s,s,vh-6*s),color,clip)
                    else:
                        value=f.display(context);tw=blf.dimensions(self.font,value)[0]
                        self.text(max(a+vw*.5,a+vw-7*s-tw),b+5*s,value,color,intersect((a+vw*.5,b,vw*.5-4*s,vh),self.body))
            for rect,label in ((self.apply,tx('Apply')),(self.cancel,tx('Cancel'))):
                active=rect==self.apply and not self.blocked
                self.box(rect,c['selected'] if active else c['button'],radius=3*s)
                color=c['button_text_sel'] if active else c['button_text']
                if rect==self.apply and self.blocked:color=tuple(v*.5 for v in color[:3])+(1,)
                tw=blf.dimensions(self.font,label)[0];self.text(rect[0]+(rect[2]-tw)/2,rect[1]+5*s,label,color,rect)
        except (ReferenceError,RuntimeError) as exc:
            if not self.closed:self.draw_error=str(exc)
        finally:
            gpu.state.blend_set(blend);gpu.state.depth_test_set(depth)
            self.draw_ms.append((time.perf_counter()-start)*1000)
            self.draw_ms=self.draw_ms[-240:]

    def _caret(self, row, x):
        field=self.value_rect(row); target=x-field[0]-4*self.scale+self.text_offset
        return min(range(len(self.edit.text)+1),key=lambda i:abs(blf.dimensions(self.font,self.edit.text[:i])[0]-target))

    def event(self, context, event, canvas_event=True):
        """None: pass to viewport, EDIT: consumed, APPLY/CANCEL: ask the owner."""
        if context.window != self.window or context.area != self.area: return None
        self.geometry(context)
        x,y=event.mouse_x-self.region.x,event.mouse_y-self.region.y
        inside=hit(self.bounds,x,y); ctrl=event.ctrl or event.oskey
        if not inside and not canvas_event and not self.drag and not self.selecting:
            if event.type=='LEFTMOUSE' and event.value=='PRESS':self.commit(context)
            return None
        if event.type in ('MIDDLEMOUSE','WHEELUPMOUSE','WHEELDOWNMOUSE'):
            if event.type!='MIDDLEMOUSE' and hit(self.body,x,y):
                self.scroll+=self.line*(1 if event.type=='WHEELDOWNMOUSE' else -1);return 'EDIT'
            self.commit(context);return None
        row=next((r for r in self.rows if hit(r['rect'],x,y) and hit(self.body,x,y)),None)
        if event.type=='MOUSEMOVE':
            if self.drag:
                kind,data=self.drag
                if kind=='WINDOW':self.position=(event.mouse_x-data[0],event.mouse_y-data[1]);return 'EDIT'
                field,start_x,start_value=data
                dx=event.mouse_x-start_x
                if abs(dx)>3*self.scale:
                    value=start_value+dx/self.scale*field.step(context)*(.1 if event.shift else 1)
                    if field.prop.type=='INT':value=int(round(value))
                    field.set(max(field.prop.hard_min,min(field.prop.hard_max,value)));self.changed=True
                return 'EDIT'
            if self.selecting is not None and self.edit is not None:
                r=next((r for r in self.rows if r['kind']=='FIELD' and r['field'].key==self.edit_key),None)
                if r:self.edit.cursor=self._caret(r,x)
                return 'EDIT'
            return 'EDIT' if inside else None
        if event.type=='LEFTMOUSE' and event.value=='RELEASE':
            if self.drag:
                kind,data=self.drag;self.drag=None
                if kind=='VALUE' and abs(event.mouse_x-data[1])<=3*self.scale:self.begin(context,data[0])
                return 'EDIT'
            self.selecting=None
            return 'EDIT' if inside else None
        if event.type=='LEFTMOUSE' and event.value in ('PRESS','DOUBLE_CLICK'):
            if hit(self.title,x,y):
                if not self.commit(context):return 'EDIT'
                if x>self.bounds[0]+self.width-30*self.scale:return 'CANCEL'
                self.drag=('WINDOW',(event.mouse_x-self.position[0],event.mouse_y-self.position[1]));return 'EDIT'
            if hit(self.cancel,x,y):return 'CANCEL'
            if hit(self.apply,x,y):return 'APPLY' if self.commit(context) else 'EDIT'
            if row and row['enabled'] and row['kind'] in ('FIELD','ENUM'):
                field=row['field']
                if self.edit is not None and field.key==self.edit_key:
                    if event.value=='DOUBLE_CLICK':self.begin(context,field);return 'EDIT'
                    self.edit.cursor=self._caret(row,x)
                    if not event.shift:self.edit.anchor=self.edit.cursor
                    self.selecting=True;return 'EDIT'
                if not self.commit(context):return 'EDIT'
                if row['kind']=='ENUM':field.set(row['extra'][0]);self.changed=True
                elif field.prop.type=='BOOLEAN':field.set(not field.get());self.changed=True
                else:self.drag=('VALUE',(field,event.mouse_x,field.get()))
                return 'EDIT'
            self.commit(context)
            return 'EDIT' if inside else None
        if event.value=='PRESS':
            if self.edit is not None:
                if event.type=='ESC':self.edit=self.edit_key=None;self.error=''
                elif event.type in ('RET','NUMPAD_ENTER'):self.commit(context)
                elif event.type=='TAB':
                    old=self.edit_key
                    if self.commit(context):self._tab(context,old,event.shift)
                elif ctrl and event.type=='A':self.edit.anchor=0;self.edit.cursor=len(self.edit.text)
                elif ctrl and event.type in ('C','X'):
                    if self.edit.selected:context.window_manager.clipboard=self.edit.selected
                    if event.type=='X':self.edit.replace('')
                elif ctrl and event.type=='V':
                    try:self.edit.replace(context.window_manager.clipboard)
                    except ValueError:self.error=tx('Invalid value')
                elif ctrl and event.type=='Z':self.edit.history(redo=event.shift)
                elif event.type in ('LEFT_ARROW','RIGHT_ARROW'):self.edit.move(-1 if event.type=='LEFT_ARROW' else 1,event.shift,ctrl)
                elif event.type in ('HOME','END'):self.edit.edge(event.type=='END',event.shift)
                elif event.type in ('BACK_SPACE','DEL'):self.edit.remove(event.type=='BACK_SPACE',ctrl)
                elif event.unicode and not ctrl:
                    try:self.edit.replace(event.unicode)
                    except ValueError:self.error=tx('Invalid value')
                return 'EDIT'
            if event.type=='TAB':self._tab(context,None,event.shift);return 'EDIT'
            if event.type=='ESC':return 'CANCEL'
            if event.type in ('RET','NUMPAD_ENTER'):return 'APPLY'
        return 'EDIT' if inside and event.type in ('LEFTMOUSE','RIGHTMOUSE') else None

    def _tab(self, context, old, reverse):
        fields=[i[2] for i in self.items if i[0]=='FIELD' and i[2].prop.type in ('FLOAT','INT') and i[1].active()]
        if not fields:return
        ids=[f.key for f in fields]
        n=ids.index(old) if old in ids else (0 if reverse else -1)
        field=fields[(n+(-1 if reverse else 1))%len(fields)]
        self.begin(context,field)
        row=next((r for r in self.rows if r['kind']=='FIELD' and r['field'].key==field.key),None)
        if row:
            a,b,w,h=row['rect'];x,y,bw,bh=self.body
            if b<y:self.scroll+=y-b
            elif b+h>y+bh:self.scroll-=b+h-y-bh
