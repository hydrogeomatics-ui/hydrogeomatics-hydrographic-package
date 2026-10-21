"""Qt implementations of the widget vocabulary used by the retained survey tools.

No Tcl interpreter or Tk window is created. Keeping this small compatibility
boundary avoids rewriting the established survey calculations and callbacks.
"""
import math
import re
import sys
import types
import traceback
from PyQt6 import QtCore as QC, QtGui as QG, QtWidgets as QW
from PyQt6 import sip

APP = None
ROOT = None
ROOT_PARENT = None
WINDOWS = []
ERRORS = []

def call(fn, *args):
    try:
        return fn(*args)
    except Exception:
        detail = traceback.format_exc()
        ERRORS.append(detail)
        print(detail, file=sys.stderr)
        if APP is not None and not getattr(APP, '_testing', False):
            QW.QMessageBox.critical(None, 'HYDROGEOMATICS — operation failed', detail)

class Dispatcher(QC.QObject):
    requested = QC.pyqtSignal(object)
    def __init__(self):
        super().__init__()
        self.requested.connect(lambda fn: call(fn), QC.Qt.ConnectionType.QueuedConnection)

DISPATCH = None

def initialize(app):
    global APP, DISPATCH
    APP = app
    DISPATCH = Dispatcher()

class Variable:
    def __init__(self, master=None, value=None, **kw):
        self.value = self.convert(value)
        self.listeners = []
        self.busy = False
    def convert(self, value):
        return '' if value is None else value
    def get(self):
        return self.value
    def set(self, value):
        value = self.convert(value)
        if value == self.value:
            return
        self.value = value
        if self.busy:
            return
        def notify():
            self.busy = True
            try:
                for fn in list(self.listeners):
                    call(fn)
            finally:
                self.busy = False
        if APP and QC.QThread.currentThread() != APP.thread():
            DISPATCH.requested.emit(notify)
        else:
            notify()
    def trace_add(self, mode, fn):
        self.listeners.append(lambda: fn('', '', mode))
        return str(id(fn))

class StringVar(Variable):
    def convert(self, v): return '' if v is None else str(v)
class BooleanVar(Variable):
    def convert(self, v): return v not in (None, False, 0, '', '0', 'false')
class IntVar(Variable):
    def convert(self, v): return int(float(v or 0))
class DoubleVar(Variable):
    def convert(self, v): return float(v or 0)

def font(value):
    if isinstance(value, QG.QFont): return value
    if not isinstance(value, (tuple, list)): return QG.QFont('Segoe UI', 10)
    result = QG.QFont(str(value[0]), abs(int(value[1])) if len(value) > 1 else 10)
    result.setBold('bold' in value[2:])
    result.setItalic('italic' in value[2:])
    return result

class PackLayout(QW.QLayout):
    """Ordered edge packing, including mixed top/bottom/left/right children."""
    def __init__(self, parent):
        super().__init__(parent)
        self.entries = []
        self.setContentsMargins(2, 2, 2, 2)
    def addItem(self, item): self.entries.append((item, {}))
    def addPacked(self, widget, options):
        self.addChildWidget(widget)
        item = QW.QWidgetItem(widget)
        before = options.get('before')
        index = next((i for i, (it, _) in enumerate(self.entries) if before and it.widget() is before.q), len(self.entries))
        self.entries.insert(index, (item, options))
        self.invalidate()
    def count(self): return len(self.entries)
    def itemAt(self, i): return self.entries[i][0] if 0 <= i < self.count() else None
    def takeAt(self, i): return self.entries.pop(i)[0] if 0 <= i < self.count() else None
    def sizeHint(self):
        w = h = 0
        for item, opt in self.entries:
            s = item.sizeHint()
            if opt.get('side', 'top') in ('left', 'right'):
                w += s.width() + 4; h = max(h, s.height() + 4)
            else:
                w = max(w, s.width() + 4); h += s.height() + 4
        return QC.QSize(max(80, w), max(30, h))
    def minimumSize(self): return QC.QSize(80, 30)
    def setGeometry(self, rect):
        super().setGeometry(rect)
        r = QC.QRect(rect).marginsRemoved(self.contentsMargins())
        def pad(v): return sum(v) if isinstance(v, (tuple, list)) else 2 * int(v or 0)
        for index, (item, opt) in enumerate(self.entries):
            side = opt.get('side', 'top'); horiz = side in ('left', 'right')
            s = item.sizeHint(); px = pad(opt.get('padx', 0)); py = pad(opt.get('pady', 0))
            future = self.entries[index + 1:]
            reserve = sum((it.sizeHint().width() + pad(o.get('padx', 0)) if horiz else it.sizeHint().height() + pad(o.get('pady', 0))) for it,o in future if (o.get('side','top') in ('left','right')) == horiz and not o.get('expand'))
            expands = 1 + sum(bool(o.get('expand')) for _,o in future if (o.get('side','top') in ('left','right')) == horiz)
            available = r.width() if horiz else r.height()
            desired = s.width() + px if horiz else s.height() + py
            extent = max(1, (available - reserve) // expands) if opt.get('expand') else min(max(1, desired), max(1, available))
            if horiz:
                box = QC.QRect(r.left() if side == 'left' else r.right()-extent+1, r.top(), extent, r.height())
                if side == 'left': r.setLeft(r.left()+extent)
                else: r.setRight(r.right()-extent)
            else:
                box = QC.QRect(r.left(), r.top() if side == 'top' else r.bottom()-extent+1, r.width(), extent)
                if side == 'top': r.setTop(r.top()+extent)
                else: r.setBottom(r.bottom()-extent)
            box.adjust(px//2, py//2, -(px-px//2), -(py-py//2))
            fill = opt.get('fill', '')
            if fill not in ('x','both'): box.setWidth(min(box.width(), max(1,s.width())))
            if fill not in ('y','both'): box.setHeight(min(box.height(), max(1,s.height())))
            item.setGeometry(box)

class EventFilter(QC.QObject):
    def __init__(self, owner):
        super().__init__(owner.q)
        self.owner = owner
    def eventFilter(self, obj, event):
        o = self.owner; typ = event.type(); names = []
        from PyQt6 import sip
        if o.dead or sip.isdeleted(o.q):return False
        relevant=(QC.QEvent.Type.Resize,QC.QEvent.Type.Enter,QC.QEvent.Type.Leave,QC.QEvent.Type.MouseMove,QC.QEvent.Type.MouseButtonPress,QC.QEvent.Type.MouseButtonRelease,QC.QEvent.Type.MouseButtonDblClick,QC.QEvent.Type.Wheel,QC.QEvent.Type.KeyPress,QC.QEvent.Type.Close)
        if typ not in relevant:return False
        pos = event.position().toPoint() if hasattr(event, 'position') else QC.QPoint()
        # Mapping a hidden top-level widget during Resize can realize its HWND
        # before it is embedded. Mouse events already provide global positions.
        glob = event.globalPosition().toPoint() if hasattr(event,'globalPosition') else QC.QPoint()
        ev = types.SimpleNamespace(widget=o, x=pos.x(), y=pos.y(), x_root=glob.x(), y_root=glob.y(), width=o.q.width(), height=o.q.height(), delta=0, num=0, state=0)
        if typ == QC.QEvent.Type.Resize: names = ['<Configure>']
        elif typ == QC.QEvent.Type.Enter: names = ['<Enter>']
        elif typ == QC.QEvent.Type.Leave: names = ['<Leave>']
        elif typ == QC.QEvent.Type.MouseMove:
            names = ['<Motion>']
            if event.buttons() & QC.Qt.MouseButton.LeftButton: names.append('<B1-Motion>')
        elif typ in (QC.QEvent.Type.MouseButtonPress, QC.QEvent.Type.MouseButtonRelease, QC.QEvent.Type.MouseButtonDblClick):
            n = {QC.Qt.MouseButton.LeftButton:1, QC.Qt.MouseButton.MiddleButton:2, QC.Qt.MouseButton.RightButton:3}.get(event.button(),1);ev.num=n
            if typ == QC.QEvent.Type.MouseButtonPress: names=['<ButtonPress>',f'<Button-{n}>',f'<ButtonPress-{n}>']
            elif typ == QC.QEvent.Type.MouseButtonRelease: names=[f'<ButtonRelease-{n}>']
            else: names=[f'<Double-{n}>',f'<Double-Button-{n}>']
        elif typ == QC.QEvent.Type.Wheel: names=['<MouseWheel>'];ev.delta=event.angleDelta().y()
        elif typ == QC.QEvent.Type.KeyPress:
            keys={QC.Qt.Key.Key_Return:'Return',QC.Qt.Key.Key_Enter:'Return',QC.Qt.Key.Key_Space:'space',QC.Qt.Key.Key_Escape:'Escape'}
            names=['<'+keys.get(event.key(),event.text())+'>']
        elif typ == QC.QEvent.Type.Close:
            if o.close_callback and not o.dead:
                event.ignore();call(o.close_callback);return True
        if hasattr(event,'modifiers'):
            mods=event.modifiers();ev.state=(1 if mods & QC.Qt.KeyboardModifier.ShiftModifier else 0) | (4 if mods & QC.Qt.KeyboardModifier.ControlModifier else 0)
        for name in names:
            for fn in list(o.bindings.get(name, [])):
                if call(fn, ev) == 'break': return True
        return False

class Widget:
    factory = QW.QWidget
    def __init__(self, master=None, _qt_parent=None, **kw):
        self.master = master; self.children=[];self.bindings={};self.timers={};self.options={};self.dead=False;self.close_callback=None;self.shortcuts={}
        self.q = self.factory(master.q if master else _qt_parent)
        self.q.destroyed.connect(self._native_destroyed)
        if master: master.children.append(self)
        self.filter = EventFilter(self);self.q.installEventFilter(self.filter)
        self.q.setMouseTracking(True)
        self.configure(**kw)
    def __getitem__(self,k): return self.options.get(k)
    def __setitem__(self,k,v): self.configure(**{k:v})
    def configure(self, **kw):
        self.options.update(kw)
        if 'text' in kw:
            if isinstance(self.q,QW.QGroupBox): self.q.setTitle(str(kw['text']))
            elif hasattr(self.q,'setText'): self.q.setText(str(kw['text']))
        if 'textvariable' in kw:
            v=kw['textvariable'];v.listeners.append(lambda: self._set_text(v.get()) if not self.dead else None);self._set_text(v.get())
        if 'font' in kw:self.q.setFont(font(kw['font']))
        if 'state' in kw:
            self.q.setEnabled(kw['state']!='disabled')
            if isinstance(self.q,QW.QLineEdit):self.q.setReadOnly(kw['state']=='readonly')
            if isinstance(self.q,QW.QPlainTextEdit):self.q.setReadOnly(kw['state']=='disabled');self.q.setEnabled(True)
        css=[]
        for k,p in [('bg','background-color'),('background','background-color'),('fg','color'),('foreground','color')]:
            if k in self.options:css.append(f'{p}:{self.options[k]}')
        if css:self.q.setStyleSheet(';'.join(css))
        if isinstance(self.q,QW.QLabel):
            self.q.setWordWrap(bool(self.options.get('wraplength')))
            self.q.setTextInteractionFlags(QC.Qt.TextInteractionFlag.TextSelectableByMouse)
        if 'width' in kw and not isinstance(self,Canvas):self.q.setMinimumWidth(min(1100,int(kw['width'])*7))
        if 'height' in kw:
            h=int(kw['height']);self.q.setMinimumHeight(h if isinstance(self,Canvas) else min(600,h*18))
        if 'menu' in kw:
            bar=QW.QMenuBar(self.q)
            for label,menu in kw['menu'].cascades:bar.addMenu(menu.q).setText(label)
            self._layout('pack').setMenuBar(bar)
        if isinstance(self.q,QW.QComboBox) and 'values' in kw:
            old=self.q.currentText();self.q.blockSignals(True);self.q.clear();self.q.addItems([str(v) for v in kw['values']]);self.q.setCurrentText(old);self.q.blockSignals(False)
    config=configure
    def _set_text(self,v):
        if self.dead:return
        self.q.blockSignals(True)
        if isinstance(self.q,QW.QComboBox):self.q.setCurrentText(str(v))
        elif hasattr(self.q,'setText'):self.q.setText(str(v))
        self.q.blockSignals(False)
    def _layout(self,kind):
        layout=self.q.layout()
        if layout is None:
            layout=QW.QGridLayout(self.q) if kind=='grid' else PackLayout(self.q)
            layout.setContentsMargins(5,5,5,5);layout.setSpacing(4)
        return layout
    def pack(self,**kw):
        if self.master:
            self.pack_forget();lay=self.master._layout('pack')
            if isinstance(lay,PackLayout):lay.addPacked(self.q,kw)
            else:lay.addWidget(self.q)
        self.q.show()
    def grid(self,**kw):
        lay=self.master._layout('grid')
        if isinstance(lay,QW.QGridLayout):lay.addWidget(self.q,int(kw.get('row',0)),int(kw.get('column',0)),int(kw.get('rowspan',1)),int(kw.get('columnspan',1)))
        else:lay.addPacked(self.q,{'fill':'both','expand':True})
        self.q.show()
    def pack_forget(self):
        if self.master and self.master.q.layout():self.master.q.layout().removeWidget(self.q)
        self.q.hide()
    grid_forget=pack_forget
    def columnconfigure(self,col,weight=0,**kw):
        lay=self._layout('grid')
        if isinstance(lay,QW.QGridLayout):lay.setColumnStretch(int(col),int(weight))
    def rowconfigure(self,row,weight=0,**kw):
        lay=self._layout('grid')
        if isinstance(lay,QW.QGridLayout):lay.setRowStretch(int(row),int(weight))
    def bind(self,event,fn,add=None):
        if not add:self.bindings[event]=[]
        self.bindings.setdefault(event,[]).append(fn)
    def bind_all(self,event,fn):
        if event.startswith('<Control-'):
            key='Ctrl+'+event[len('<Control-'):-1].upper()
            if key in self.shortcuts:self.shortcuts[key].setEnabled(False)
            sc=QG.QShortcut(QG.QKeySequence(key),self.q);sc.activated.connect(lambda:call(fn,None));self.shortcuts[key]=sc
        else:self.bind(event,fn)
    def unbind_all(self,event):self.bindings.pop(event,None)
    def _emit(self,event):
        for fn in self.bindings.get(event,[]):call(fn,types.SimpleNamespace(widget=self))
    def after(self,ms,fn=None,*args):
        if self.dead or sip.isdeleted(self.q):return None
        token=object();self.timers[token]=None
        def schedule():
            if self.dead or sip.isdeleted(self.q) or token not in self.timers:return
            timer=QC.QTimer(self.q);timer.setSingleShot(True);self.timers[token]=timer
            def run():
                self.timers.pop(token,None);timer.deleteLater()
                if not self.dead and fn:call(fn,*args)
            timer.timeout.connect(run);timer.start(max(0,int(ms)))
        if QC.QThread.currentThread()!=APP.thread():DISPATCH.requested.emit(schedule)
        else:schedule()
        return token
    def after_cancel(self,token):
        timer=self.timers.pop(token,None)
        if timer and not sip.isdeleted(timer):timer.stop();timer.deleteLater()
    def _native_destroyed(self,*args):
        # Qt can delete a child with its parent without calling this wrapper.
        self.dead=True
        self.timers.clear()
        if self in WINDOWS:WINDOWS.remove(self)
        if self.master and self in self.master.children:self.master.children.remove(self)
    def destroy(self):
        if self.dead:return
        # Mark first: destroy handlers may re-enter destroy or pump Qt events.
        self.dead=True
        for token in list(self.timers):self.after_cancel(token)
        if not sip.isdeleted(self.q):self.q.setUpdatesEnabled(False)
        for fn in list(self.bindings.get('<Destroy>',[])):call(fn,types.SimpleNamespace(widget=self))
        for ch in list(self.children):ch.destroy()
        if not sip.isdeleted(self.q):self.q.hide();self.q.deleteLater()
        if self in WINDOWS:WINDOWS.remove(self)
        if self.master and self in self.master.children:self.master.children.remove(self)
    def winfo_children(self):return [c for c in self.children if not c.dead]
    def winfo_exists(self):return not self.dead and not sip.isdeleted(self.q)
    def winfo_ismapped(self):return self.winfo_exists() and self.q.isVisible()
    def winfo_width(self):return self.q.width()
    def winfo_height(self):return self.q.height()
    def winfo_reqwidth(self):return self.q.sizeHint().width()
    def winfo_rootx(self):return self.q.mapToGlobal(QC.QPoint()).x()
    def winfo_rooty(self):return self.q.mapToGlobal(QC.QPoint()).y()
    def winfo_class(self):return type(self).__name__
    def title(self,text):self.q.setWindowTitle(str(text))
    def geometry(self,text):
        m=re.match(r'(\d+)x(\d+)',text)
        if m:self.q.resize(int(m[1]),int(m[2]))
        m=re.search(r'\+(-?\d+)\+(-?\d+)',text)
        if m:self.q.move(int(m[1]),int(m[2]))
    wm_geometry=geometry
    def minsize(self,w,h):self.q.setMinimumSize(w,h)
    def resizable(self,*args):pass
    def state(self,value=None):
        if value=='zoomed':
            if isinstance(self,Toplevel):self.q.showMaximized()
            else:self.q.resize(1400,900)
        return 'normal'
    def attributes(self,*args):
        if args and args[0]=='-topmost':self.q.setWindowFlag(QC.Qt.WindowType.WindowStaysOnTopHint,bool(args[1]));self.q.show()
    def wm_overrideredirect(self,v):self.q.setWindowFlag(QC.Qt.WindowType.FramelessWindowHint,bool(v));self.q.show()
    def protocol(self,name,fn):self.close_callback=fn
    def lift(self):self.q.show();self.q.raise_();self.q.activateWindow()
    def transient(self,parent):pass
    def grab_set(self):self.q.setWindowModality(QC.Qt.WindowModality.ApplicationModal)
    def grab_release(self):self.q.setWindowModality(QC.Qt.WindowModality.NonModal)
    def focus_force(self):self.q.setFocus()
    def wait_window(self,window=None):
        target=window or self
        loop=QC.QEventLoop();target.q.destroyed.connect(loop.quit);loop.exec()
    def update_idletasks(self):APP.processEvents()
    def option_add(self,name,value):
        if name=='*Font':self.q.setFont(font(value))
    def mainloop(self):return APP.exec()

class Tk(Widget):
    def __init__(self,**kw):
        global ROOT
        super().__init__(None,_qt_parent=ROOT_PARENT,**kw);ROOT=self
class Toplevel(Widget):
    def __init__(self,master=None,**kw):
        super().__init__(master or ROOT,**kw);self.q.setWindowFlag(QC.Qt.WindowType.Window,True);WINDOWS.append(self);self.q.show()
class Frame(Widget):pass
class LabelFrame(Widget):factory=QW.QGroupBox
class Label(Widget):factory=QW.QLabel
class Button(Widget):
    factory=QW.QPushButton
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);self.q.clicked.connect(lambda:call(self.options['command']) if self.options.get('command') else None)
class Entry(Widget):
    factory=QW.QLineEdit
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);self.q.textEdited.connect(lambda t:self.options['textvariable'].set(t) if self.options.get('textvariable') else None)
    def get(self):return self.q.text()
    def insert(self,index,text):self.q.setText(self.q.text()+str(text));self._changed()
    def delete(self,*args):self.q.clear();self._changed()
    def _changed(self):
        if self.options.get('textvariable'):self.options['textvariable'].set(self.q.text())
class Spinbox(Entry):
    """Editable numeric field with a native spin control and text-variable binding."""
    factory=QW.QDoubleSpinBox
    def __init__(self,master=None,**kw):
        Widget.__init__(self,master,**{k:v for k,v in kw.items() if k!='textvariable'})
        self.q.setRange(float(kw.get('from_',-1e12)),float(kw.get('to',1e12)));step=float(kw.get('increment',1));self.q.setDecimals(6 if step<1 else 0);self.q.setSingleStep(step)
        self.options.update(kw)
        v=kw.get('textvariable')
        if v:
            self._set_text(v.get());v.listeners.append(lambda:self._set_text(v.get()) if not self.dead else None)
        self.q.valueChanged.connect(self._changed)
    def _set_text(self,v):
        try:
            self.q.blockSignals(True);self.q.setValue(float(v or 0))
        except (ValueError,TypeError):pass
        finally:self.q.blockSignals(False)
    def _changed(self,*args):
        if self.options.get('textvariable'):self.options['textvariable'].set(self.q.cleanText())
        if self.options.get('command'):call(self.options['command'])
    def get(self):return self.q.cleanText()
class Combobox(Widget):
    factory=QW.QComboBox
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);self.q.setEditable(kw.get('state')!='readonly')
        if kw.get('textvariable'):self._set_text(kw['textvariable'].get())
        self.q.currentTextChanged.connect(self._changed)
        self.q.activated.connect(lambda:self._emit('<<ComboboxSelected>>'))
    def _changed(self,text):
        if self.options.get('textvariable'):self.options['textvariable'].set(text)
    def get(self):return self.q.currentText()
    def set(self,value):self.q.setCurrentText(str(value))
    def current(self,index=None):
        if index is not None:self.q.setCurrentIndex(index)
        return self.q.currentIndex()
class Checkbutton(Widget):
    factory=QW.QCheckBox
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);v=kw.get('variable')
        if v:
            self.q.setChecked(bool(v.get()));v.listeners.append(lambda:self._sync(v) if not self.dead else None)
        self.q.clicked.connect(self._clicked)
    def _sync(self,v):self.q.blockSignals(True);self.q.setChecked(bool(v.get()));self.q.blockSignals(False)
    def _clicked(self,checked):
        if self.options.get('variable'):self.options['variable'].set(checked)
        if self.options.get('command'):call(self.options['command'])
class Radiobutton(Checkbutton):
    factory=QW.QRadioButton
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);self.q.setAutoExclusive(False)
        if kw.get('variable'):self._sync(kw['variable'])
    def _sync(self,v):self.q.blockSignals(True);self.q.setChecked(v.get()==self.options.get('value'));self.q.blockSignals(False)
    def _clicked(self,checked):
        if self.options.get('variable'):self.options['variable'].set(self.options.get('value'))
        if self.options.get('command'):call(self.options['command'])
class Scale(Widget):
    factory=QW.QSlider
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);self.q.setOrientation(QC.Qt.Orientation.Horizontal);self.q.setRange(0,1000)
        self.low=float(kw.get('from_',0));self.high=float(kw.get('to',100));v=kw.get('variable')
        if v:self.set(v.get());v.listeners.append(lambda:self.set(v.get()) if not self.dead else None)
        self.q.valueChanged.connect(self._changed)
    def get(self):return self.low+(self.high-self.low)*self.q.value()/1000
    def set(self,v):self.q.blockSignals(True);self.q.setValue(round(1000*(float(v)-self.low)/(self.high-self.low or 1)));self.q.blockSignals(False)
    def _changed(self,*args):
        if self.options.get('variable'):self.options['variable'].set(self.get())
        if self.options.get('command'):call(self.options['command'],self.get())
class Text(Widget):
    factory=QW.QPlainTextEdit
    def insert(self,index,text,*tags):
        cursor=self.q.textCursor();cursor.movePosition(QG.QTextCursor.MoveOperation.End);cursor.insertText(str(text));self.q.setTextCursor(cursor)
    def get(self,*args):return self.q.toPlainText()
    def delete(self,start='1.0',end='end'):
        if start in ('1.0',0) and end=='end':self.q.clear();return
        text=self.q.toPlainText();lines=text.splitlines(True)
        try:
            stop=int(str(end).split('.')[0])-1;self.q.setPlainText(''.join(lines[stop:]))
        except ValueError:self.q.clear()
    def see(self,*args):self.q.verticalScrollBar().setValue(self.q.verticalScrollBar().maximum())
    def tag_config(self,*args,**kw):pass
    tag_configure=tag_config
    def yview(self,*args):_scroll(self.q.verticalScrollBar(),args)
    def xview(self,*args):_scroll(self.q.horizontalScrollBar(),args)
class Listbox(Widget):
    factory=QW.QListWidget
    def insert(self,index,*values):
        for v in values:self.q.insertItem(self.q.count() if index=='end' else int(index),str(v))
    def delete(self,first,last=None):
        first=int(first);last=self.q.count()-1 if last=='end' else int(last if last is not None else first)
        for i in range(last,first-1,-1):self.q.takeItem(i)
    def get(self,index):return self.q.item(int(index)).text()
    def curselection(self):return tuple(self.q.row(i) for i in self.q.selectedItems())
    def selection_set(self,index,*args):self.q.setCurrentRow(int(index))
    def activate(self,index):self.q.setCurrentRow(int(index))
    def size(self):return self.q.count()
    def yview(self,*args):_scroll(self.q.verticalScrollBar(),args)
class Notebook(Widget):
    factory=QW.QTabWidget
    def add(self,child,**kw):self.q.addTab(child.q,kw.get('text',''))
    def select(self,child=None):
        if child is not None:self.q.setCurrentIndex(child if isinstance(child,int) else self.q.indexOf(child.q))
        return self.q.currentIndex()
class PanedWindow(Widget):
    factory=QW.QSplitter
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);self.q.setOrientation(QC.Qt.Orientation.Vertical if kw.get('orient')=='vertical' else QC.Qt.Orientation.Horizontal)
    def add(self,child,**kw):self.q.addWidget(child.q);self.q.setStretchFactor(self.q.count()-1,int(kw.get('weight',1)))
class Treeview(Widget):
    factory=QW.QTreeWidget
    def __init__(self,master=None,**kw):
        self.nodes={};self.columns=list(kw.get('columns',[]));self.tags={};self.counter=0
        super().__init__(master,**kw);self.q.setColumnCount(max(1,len(self.columns)));self.q.setHeaderLabels([str(c) for c in self.columns]);self.q.setRootIsDecorated(False)
        self.q.setSelectionMode(QW.QAbstractItemView.SelectionMode.ExtendedSelection if kw.get('selectmode')!='browse' else QW.QAbstractItemView.SelectionMode.SingleSelection)
        self.q.itemSelectionChanged.connect(lambda:self._emit('<<TreeviewSelect>>'))
        self.q.viewport().installEventFilter(self.filter)
    def heading(self,col,**kw):
        i=self.columns.index(col) if col in self.columns else int(str(col).lstrip('#') or 0)
        if i<self.q.columnCount():self.q.headerItem().setText(i,kw.get('text',str(col)))
    def column(self,col,**kw):
        if col in self.columns and 'width' in kw:self.q.setColumnWidth(self.columns.index(col),int(kw['width']))
    def insert(self,parent,index,iid=None,**kw):
        self.counter+=1;key=str(iid) if iid is not None else f'I{self.counter}'
        vals=kw.get('values',());item=QW.QTreeWidgetItem([str(v) for v in vals]);item.setData(0,QC.Qt.ItemDataRole.UserRole,key)
        if parent and str(parent) in self.nodes:self.nodes[str(parent)].addChild(item)
        else:self.q.addTopLevelItem(item)
        self.nodes[key]=item
        for tag in kw.get('tags',()):
            opts=self.tags.get(tag,{})
            for i in range(self.q.columnCount()):
                if opts.get('foreground'):item.setForeground(i,QG.QBrush(QG.QColor(opts['foreground'])))
                if opts.get('background'):item.setBackground(i,QG.QBrush(QG.QColor(opts['background'])))
        return key
    def get_children(self,parent=''):return tuple(self.nodes)
    def delete(self,*items):
        for key in items:
            if isinstance(key,(tuple,list)):self.delete(*key);continue
            item=self.nodes.pop(str(key),None)
            if item:
                parent=item.parent()
                if parent:parent.removeChild(item)
                else:self.q.takeTopLevelItem(self.q.indexOfTopLevelItem(item))
    def selection(self):return tuple(i.data(0,QC.Qt.ItemDataRole.UserRole) for i in self.q.selectedItems())
    def selection_set(self,items):
        self.q.clearSelection()
        for key in items if isinstance(items,(list,tuple)) else [items]:
            if str(key) in self.nodes:self.nodes[str(key)].setSelected(True)
    def focus(self,key=None):
        if key is not None and str(key) in self.nodes:self.q.setCurrentItem(self.nodes[str(key)])
        item=self.q.currentItem();return item.data(0,QC.Qt.ItemDataRole.UserRole) if item else ''
    def item(self,key,option=None,**kw):
        item=self.nodes[str(key)]
        if 'values' in kw:
            for i,v in enumerate(kw['values']):item.setText(i,str(v))
        values=tuple(item.text(i) for i in range(self.q.columnCount()))
        return values if option=='values' else {'values':values}
    def see(self,key):
        if str(key) in self.nodes:self.q.scrollToItem(self.nodes[str(key)])
    def identify_region(self,x,y):return 'cell' if self.q.itemAt(x,y) else ''
    def tag_configure(self,name,**kw):self.tags[name]=kw
    def yview(self,*args):_scroll(self.q.verticalScrollBar(),args)
    def xview(self,*args):_scroll(self.q.horizontalScrollBar(),args)

def _scroll(bar,args):
    if not args:return
    if args[0]=='moveto':bar.setValue(round(float(args[1])*bar.maximum()))
    elif args[0]=='scroll':bar.setValue(bar.value()+int(args[1])*bar.singleStep())
class Scrollbar(Widget):
    factory=QW.QScrollBar
    def __init__(self,master=None,**kw):
        super().__init__(master,**kw);self.q.setOrientation(QC.Qt.Orientation.Horizontal if kw.get('orient')=='horizontal' else QC.Qt.Orientation.Vertical);self.q.setRange(0,1000)
        command=kw.get('command');owner=getattr(command,'__self__',None)
        if owner and hasattr(owner.q,'verticalScrollBar'):
            bar=owner.q.horizontalScrollBar() if kw.get('orient')=='horizontal' else owner.q.verticalScrollBar()
            self.q.setRange(bar.minimum(),bar.maximum());bar.rangeChanged.connect(self.q.setRange);bar.valueChanged.connect(self.q.setValue);self.q.valueChanged.connect(bar.setValue)
        elif command:self.q.valueChanged.connect(lambda v:call(command,'moveto',v/1000))
    def set(self,*args):pass

class Canvas(Widget):
    factory=QW.QGraphicsView
    def __init__(self,master=None,**kw):
        self.items={};self.counter=0
        super().__init__(master,**kw);self.scene=QW.QGraphicsScene(self.q);self.q.setScene(self.scene);self.q.setAlignment(QC.Qt.AlignmentFlag.AlignLeft|QC.Qt.AlignmentFlag.AlignTop)
        self.q.setRenderHint(QG.QPainter.RenderHint.Antialiasing);self.q.setMouseTracking(True);self.q.viewport().installEventFilter(self.filter);self.q.viewport().setMouseTracking(True)
        self.q.setHorizontalScrollBarPolicy(QC.Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.q.setVerticalScrollBarPolicy(QC.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bind('<Configure>',lambda e:self.scene.setSceneRect(0,0,e.width,e.height))
    def configure(self,**kw):
        super().configure(**kw)
        if hasattr(self,'scene') and 'scrollregion' in kw and kw['scrollregion']:
            x,y,x2,y2=kw['scrollregion'];self.scene.setSceneRect(x,y,x2-x,y2-y)
    def _xy(self,args):
        result=[]
        def add(v):
            if isinstance(v,(tuple,list)):
                for x in v:add(x)
            else:result.append(float(v))
        add(args);return result
    def _pen(self,kw):
        color=kw.get('outline',kw.get('fill','#111827'));p=QG.QPen(QG.QColor(color or 'transparent'));p.setWidthF(float(kw.get('width',1)))
        if kw.get('dash'):p.setStyle(QC.Qt.PenStyle.DashLine)
        return p
    def _add(self,item,kw):
        self.counter+=1;self.scene.addItem(item);self.items[self.counter]=(item,kw);return self.counter
    def create_line(self,*args,**kw):
        pts=self._xy(args);path=QG.QPainterPath()
        if len(pts)>=2:path.moveTo(pts[0],pts[1])
        for i in range(2,len(pts)-1,2):path.lineTo(pts[i],pts[i+1])
        item=QW.QGraphicsPathItem(path);item.setPen(self._pen(kw));return self._add(item,kw)
    def create_rectangle(self,*args,**kw):
        x,y,x2,y2=self._xy(args);item=QW.QGraphicsRectItem(QC.QRectF(QC.QPointF(x,y),QC.QPointF(x2,y2)).normalized());item.setPen(self._pen({**kw,'fill':kw.get('outline','black')}));item.setBrush(QG.QBrush(QG.QColor(kw.get('fill') or 'transparent')));return self._add(item,kw)
    def create_oval(self,*args,**kw):
        x,y,x2,y2=self._xy(args);item=QW.QGraphicsEllipseItem(QC.QRectF(QC.QPointF(x,y),QC.QPointF(x2,y2)).normalized());item.setPen(self._pen({**kw,'fill':kw.get('outline','black')}));item.setBrush(QG.QBrush(QG.QColor(kw.get('fill') or 'transparent')));return self._add(item,kw)
    def create_polygon(self,*args,**kw):
        p=self._xy(args);poly=QG.QPolygonF([QC.QPointF(p[i],p[i+1]) for i in range(0,len(p)-1,2)]);item=QW.QGraphicsPolygonItem(poly);item.setPen(self._pen({**kw,'fill':kw.get('outline','black')}));item.setBrush(QG.QBrush(QG.QColor(kw.get('fill') or 'transparent')));return self._add(item,kw)
    def create_text(self,*args,**kw):
        x,y=self._xy(args);item=QW.QGraphicsSimpleTextItem(str(kw.get('text','')));item.setFont(font(kw.get('font',('Segoe UI',10))));item.setBrush(QG.QBrush(QG.QColor(kw.get('fill','black'))));r=item.boundingRect();a=kw.get('anchor','center');x-=0 if 'w' in a else r.width() if 'e' in a else r.width()/2;y-=0 if 'n' in a else r.height() if 's' in a else r.height()/2;item.setPos(x,y)
        if kw.get('angle'):item.setTransformOriginPoint(r.center());item.setRotation(-float(kw['angle']))
        return self._add(item,kw)
    def create_window(self,*args,**kw):
        x,y=self._xy(args);child=kw['window'];child.q.setParent(None);proxy=self.scene.addWidget(child.q);proxy.setPos(x,y);self.counter+=1;self.items[self.counter]=(proxy,kw);return self.counter
    def delete(self,*ids):
        for key in ids:
            keys=list(self.items) if key=='all' else [key] if key in self.items else [i for i,(_,k) in self.items.items() if key in k.get('tags',())]
            for i in keys:
                item,_=self.items.pop(i);self.scene.removeItem(item)
    def coords(self,key,*args):
        item,_=self.items[key]
        if args and hasattr(item,'setRect'):
            x,y,x2,y2=self._xy(args);item.setRect(QC.QRectF(QC.QPointF(x,y),QC.QPointF(x2,y2)).normalized())
    def itemconfigure(self,key,**kw):
        if key not in self.items:return
        item,_=self.items[key]
        if 'width' in kw and isinstance(item,QW.QGraphicsProxyWidget):item.resize(float(kw['width']),item.size().height())
        if 'text' in kw and hasattr(item,'setText'):item.setText(str(kw['text']))
    def bbox(self,*args):
        r=self.scene.itemsBoundingRect();return (r.left(),r.top(),r.right(),r.bottom())
    def yview(self,*args):_scroll(self.q.verticalScrollBar(),args)
    def yview_scroll(self,n,units):_scroll(self.q.verticalScrollBar(),('scroll',n,units))
    def xview(self,*args):_scroll(self.q.horizontalScrollBar(),args)

class Menu:
    def __init__(self,master=None,**kw):
        self.q=QW.QMenu(master.q if master else None);self.cascades=[];self.actions={}
    def add_command(self,label,command=None,**kw):
        a=self.q.addAction(label);self.actions[label]=a
        if command:a.triggered.connect(lambda checked=False:call(command))
        if kw.get('state')=='disabled':a.setEnabled(False)
    def add_cascade(self,label,menu,**kw):self.cascades.append((label,menu));self.q.addMenu(menu.q).setText(label)
    def add_separator(self):self.q.addSeparator()
    def add_checkbutton(self,label,variable=None,command=None,**kw):
        a=self.q.addAction(label);a.setCheckable(True);a.setChecked(bool(variable.get()) if variable else False);self.actions[label]=a
        def changed(v):
            if variable:variable.set(v)
            if command:call(command)
        a.triggered.connect(changed)
    def entryconfig(self,label,**kw):
        if label in self.actions:self.actions[label].setEnabled(kw.get('state')!='disabled')
    def tk_popup(self,x,y):self.q.popup(QC.QPoint(int(x),int(y)))
    def grab_release(self):pass
class Style:
    def __init__(self,*args):pass
    def theme_names(self):return ('Fusion',)
    def theme_use(self,*args):APP.setStyle('Fusion')
    def configure(self,name,**kw):
        if name=='.' and APP:
            pal=APP.palette()
            for key,role in [('background',QG.QPalette.ColorRole.Window),('foreground',QG.QPalette.ColorRole.WindowText)]:
                if key in kw:pal.setColor(role,QG.QColor(kw[key]))
            APP.setPalette(pal)
    def map(self,*args,**kw):pass

def _parent(kw):
    p=kw.get('parent');return p.q if p else None
def _message(kind,title,message,**kw):
    if APP and getattr(APP,'_testing',False):
        if kind in ('critical','warning'):print(f'{kind}: {title}: {message}',file=sys.stderr)
        return False
    return getattr(QW.QMessageBox,kind)(_parent(kw),str(title),str(message))
def _filter(filetypes):return ';;'.join(str(label)+' ('+' '.join(pats if isinstance(pats,(tuple,list)) else [str(pats).replace(';',' ')])+')' for label,pats in filetypes)
def _file(mode,**kw):
    if APP and getattr(APP,'_testing',False):return () if mode=='many' else ''
    parent=_parent(kw);title=kw.get('title','Select file');start=str(kw.get('initialdir',''))
    if kw.get('initialfile'):start=start.rstrip('/\\')+'/'+str(kw['initialfile'])
    filt=_filter(kw.get('filetypes',[('All files','*')]))
    if mode=='dir':return QW.QFileDialog.getExistingDirectory(parent,title,start)
    if mode=='many':return tuple(QW.QFileDialog.getOpenFileNames(parent,title,start,filt)[0])
    if mode=='save':
        name=QW.QFileDialog.getSaveFileName(parent,title,start,filt)[0]
        if name and kw.get('defaultextension') and not __import__('pathlib').Path(name).suffix:name+=kw['defaultextension']
        return name
    return QW.QFileDialog.getOpenFileName(parent,title,start,filt)[0]
def _ask(kind,title,prompt,**kw):
    if APP and getattr(APP,'_testing',False):return None
    p=_parent(kw);v=kw.get('initialvalue')
    if kind=='str':value,ok=QW.QInputDialog.getText(p,title,prompt,text=str(v or ''))
    elif kind=='int':value,ok=QW.QInputDialog.getInt(p,title,prompt,int(v or 0),int(kw.get('minvalue',-2147483647)),int(kw.get('maxvalue',2147483647)))
    else:value,ok=QW.QInputDialog.getDouble(p,title,prompt,float(v or 0),float(kw.get('minvalue',-1e12)),float(kw.get('maxvalue',1e12)),6)
    return value if ok else None

def make_modules():
    tk=types.ModuleType('HYDROGEOMATICS_qt_widgets');ttk=types.ModuleType('HYDROGEOMATICS_qt_widgets.ttk')
    for name in ('Tk','Toplevel','StringVar','BooleanVar','IntVar','DoubleVar','Text','Canvas','Menu','Listbox','Scale','Widget'):
        setattr(tk,name,globals()[name])
    for name in ('Frame','LabelFrame','Label','Button','Entry','Spinbox','Combobox','Checkbutton','Radiobutton','Scale','Notebook','PanedWindow','Treeview','Scrollbar','Style'):
        setattr(ttk,name,globals()[name])
    tk.TclError=RuntimeError
    for k in ('END','BOTH','LEFT','RIGHT','TOP','BOTTOM','X','Y','HORIZONTAL','VERTICAL','NORMAL','DISABLED'):setattr(tk,k,k.lower())
    tk.ttk=ttk;tk.messagebox=types.SimpleNamespace(showerror=lambda t,m,**k:_message('critical',t,m,**k),showwarning=lambda t,m,**k:_message('warning',t,m,**k),showinfo=lambda t,m,**k:_message('information',t,m,**k),askyesno=lambda t,m,**k:_message('question',t,m,**k)==QW.QMessageBox.StandardButton.Yes)
    tk.filedialog=types.SimpleNamespace(askopenfilename=lambda **k:_file('open',**k),askopenfilenames=lambda **k:_file('many',**k),asksaveasfilename=lambda **k:_file('save',**k),askdirectory=lambda **k:_file('dir',**k))
    tk.simpledialog=types.SimpleNamespace(askstring=lambda t,p,**k:_ask('str',t,p,**k),askfloat=lambda t,p,**k:_ask('float',t,p,**k),askinteger=lambda t,p,**k:_ask('int',t,p,**k))
    return tk
