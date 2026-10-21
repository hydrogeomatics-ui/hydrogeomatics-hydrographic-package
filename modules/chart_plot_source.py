"""Chart plot workspace. Graduation reference: IHO INT 2, edition 5 (2025), B-212.
Supports WGS84 ellipsoidal Mercator, non-antimeridian areas below 85 degrees.
"""
import math
import numpy as np
from pathlib import Path
from PyQt6 import QtWidgets as W, QtCore as C, QtGui as G
from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.ticker import FixedLocator
from pyproj import CRS, Transformer

REFERENCE = 'https://iho.int/uploads/user/pubs/standards/s-4/INT2_Ed5_2025.pdf'
INTERVALS = (.5, 1, 2, 5, 10, 30, 60, 300)
SHEETS = {name:(w,h) for name,w,h in [('A4',297,210),('A3',420,297),('A2',594,420),('A1',841,594),('A0',1189,841)]}
# INT 2 (2025): degree, intermediate, minute, minor, dicing; units arc minutes.
GRADUATION_STYLES = {
 'E': (1,.5,.1,.1,0), 'F': (60,5,.5,.1,1), 'G': (60,5,1,.2,1),
 'H': (60,5,1,.5,1), 'J': (60,10,5,1,5), 'K': (60,30,10,2,10),
 'L': (60,0,30,5,30), 'M': (300,0,60,10,60)}
SCALE_STYLES = {'O':(100,20),'P':(500,50),'Q':(1000,100),'R':(1000,200)}
def graduation_style(scale):
    for limit,style in [(30000,'E'),(100000,'F'),(200000,'G'),(500000,'H'),(1500000,'J'),(2250000,'K'),(4750000,'L')]:
        if scale<limit:return style
    return 'M'

MERCATOR = '+proj=merc +lat_ts=0 +datum=WGS84 +units=m +no_defs'

def coordinate_label(value, longitude=True):
    total = round(abs(value) * 6000)
    degrees, hundredths = divmod(total, 6000)
    hemisphere = ('E' if value >= 0 else 'W') if longitude else ('N' if value >= 0 else 'S')
    return f'{degrees}°{hundredths / 100:05.2f}′{hemisphere}'

def tick_values(low, high, minutes):
    step = minutes / 60
    first = math.ceil(low / step - 1e-9)
    last = math.floor(high / step + 1e-9)
    if last - first > 4000:
        raise ValueError('Graduation is too dense for this area. Choose a larger interval.')
    return np.arange(first, last + 1, dtype=float) * step

class ChartPlotWindow(W.QDialog):
    def __init__(self, owner, modern):
        super().__init__(owner)
        self.modern = modern
        from matplotlib import font_manager
        ui_font=font_manager.findfont('DejaVu Sans')
        G.QFontDatabase.addApplicationFont(ui_font)
        self.setFont(G.QFont('DejaVu Sans',9))
        self.setWindowTitle('26SEPT26P — Plot area and IHO border graduation')
        self.resize(1450, 950)
        self.setWindowFlags(self.windowFlags() | C.Qt.WindowType.WindowMaximizeButtonHint | C.Qt.WindowType.WindowMinimizeButtonHint)
        self._first_open=True
        self._map_pick_active=False
        self.metadata = {}
        self.metadata_positions = {}
        self._metadata_artists = {}
        self._drag_metadata = None
        self.metadata_font = 'DejaVu Serif'
        self.metadata_size = 8.0
        self.metadata_width = 65.0
        self._map_styles={}
        self._map_fill_lengths={}
        self.data_warnings={}
        self.datasets = []
        self.figure = Figure(figsize=(11.69, 8.27), dpi=100)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout = W.QHBoxLayout(self)
        side = W.QWidget(); side.setMaximumWidth(440)
        controls = W.QVBoxLayout(side)
        scroll = W.QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(side); scroll.setMinimumWidth(445); scroll.setMaximumWidth(470)
        layout.addWidget(scroll)
        self.preview_image = W.QLabel(); self.preview_image.setAlignment(C.Qt.AlignmentFlag.AlignCenter); self.preview_image.setMinimumSize(400,400)
        self.preview_image.setSizePolicy(W.QSizePolicy.Policy.Ignored,W.QSizePolicy.Policy.Ignored)
        self.preview_image.installEventFilter(self)
        preview_panel=W.QWidget(); preview_layout=W.QVBoxLayout(preview_panel)
        self.preview_zoom=W.QComboBox();self.preview_zoom.addItems(['Fit sheet','50%','100%','200%','25%','400%','800%'])
        for i,ratio in enumerate([None,.5,1,2,.25,4,8]):self.preview_zoom.setItemData(i,ratio)
        self._preview_pan=None
        preview_tools=W.QHBoxLayout();preview_tools.addWidget(self.preview_zoom)
        preview_save=W.QPushButton('Preview and save…');preview_save.clicked.connect(lambda:self.safe(self.save_plot));preview_tools.addWidget(preview_save);preview_layout.addLayout(preview_tools)
        self.preview_scroll=W.QScrollArea();self.preview_scroll.setWidgetResizable(True);self.preview_scroll.setWidget(self.preview_image)
        preview_layout.addWidget(self.preview_scroll,1);layout.addWidget(preview_panel,1)
        metadata_button=W.QPushButton('Title block / footnotes…');preview_tools.addWidget(metadata_button)
        metadata_button.clicked.connect(self.edit_metadata)
        self.move_information=W.QCheckBox('Drag information');self.move_information.setChecked(True);preview_tools.addWidget(self.move_information)
        zoom_tools=W.QHBoxLayout();preview_layout.addLayout(zoom_tools)
        for caption,action in [('− Zoom out',lambda:self.zoom_preview(1/1.25)),('+ Zoom in',lambda:self.zoom_preview(1.25)),('Fit sheet',lambda:self.preview_zoom.setCurrentIndex(0)),('100%',lambda:self.preview_zoom.setCurrentIndex(2))]:
            button=W.QPushButton(caption);button.clicked.connect(lambda checked=False,fn=action:fn());zoom_tools.addWidget(button)
        zoom_hint=W.QLabel('Mouse wheel: zoom • Middle-button drag: pan');zoom_tools.addWidget(zoom_hint)
        series_button=W.QPushButton('A0 cascade sheet series…');controls.addWidget(series_button);series_button.clicked.connect(lambda:self.safe(self.open_sheet_series))
        self.preview_zoom.currentIndexChanged.connect(lambda *_:self.refresh_preview_image())
        self._preview_pixmap = None
        form = W.QFormLayout(); controls.addLayout(form)
        self.title = W.QLineEdit('Survey plot'); form.addRow('Plot title', self.title)
        self.crs = W.QLineEdit('EPSG:4326'); form.addRow('Input data CRS', self.crs)
        self.crs.setToolTip('Applied separately when each file is added. EPSG:4326 = longitude, latitude; use the correct projected EPSG code for easting/northing.')
        hint = W.QLabel('Set the data CRS before adding files.\nCSV / XYZ / TXT: X, Y, optional depth in the first columns. DXF and KTD are also supported.')
        hint.setWordWrap(True); controls.addWidget(hint)
        self.files = W.QListWidget(); self.files.setMinimumHeight(80); self.files.setMaximumHeight(130); self.files.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection); controls.addWidget(self.files)
        for label, fn in [('Add data files…', self.choose_files), ('Remove selected files', self.remove_files), ('Fit area to checked files', self.fit_files)]:
            button = W.QPushButton(label); controls.addWidget(button); button.clicked.connect(lambda checked=False, f=fn:self.safe(f))
        area = W.QGroupBox('Plot area — WGS84 decimal degrees'); bounds = W.QFormLayout(area); controls.addWidget(area)
        self.bounds = {}
        for name, val in [('West', 66.9), ('East', 67.1), ('South', 24.7), ('North', 24.9)]:
            spin = W.QDoubleSpinBox(); spin.setDecimals(7); spin.setRange(-180 if name in ('West','East') else -84.999, 180 if name in ('West','East') else 84.999); spin.setValue(val)
            bounds.addRow(name, spin); self.bounds[name] = spin
        pick_map=W.QPushButton('Pick plot limits on MAP (two corners)…');bounds.addRow(pick_map)
        pick_map.clicked.connect(lambda:self.safe(self.start_map_limits_pick))
        self.graduation = W.QComboBox(); self.graduation.addItem('IHO INT 2 — automatic', None)
        for value in (.01, .02, .05, .1, .2) + INTERVALS:
            self.graduation.addItem(f'{value:g} minutes' + (' (small plan)' if value < .5 else ''), value)
        bounds.addRow('Numbered interval', self.graduation)
        self.labels = W.QCheckBox('Show sounding / point labels'); controls.addWidget(self.labels)
        self.page = W.QComboBox(); self.page.addItems([f'{name} {orientation}' for name in SHEETS for orientation in ('landscape','portrait')]); bounds.addRow('Export sheet', self.page)
        self.options={}
        self.add_int2_controls(controls)
        fonts = W.QGroupBox('Cartographic lettering'); fontform = W.QFormLayout(fonts); controls.addWidget(fonts)
        self.font_style = W.QComboBox(); self.font_style.addItems(['Cartographic (italic soundings)', 'Regular', 'Italic', 'Bold'])
        fontform.addRow('Style', self.font_style)
        from matplotlib import font_manager
        self.font_family = W.QComboBox(); self.font_family.addItems(sorted({f.name for f in font_manager.fontManager.ttflist}))
        self.font_family.setCurrentText('DejaVu Sans'); fontform.addRow('Font family', self.font_family)
        self.font_size = W.QDoubleSpinBox(); self.font_size.setRange(3,36); self.font_size.setValue(7); self.font_size.setSuffix(' pt'); self.font_size.setDecimals(1)
        fontform.addRow('Label size', self.font_size)
        self.auto_font = W.QCheckBox('Fit lettering to plot extents'); self.auto_font.setChecked(True); fontform.addRow(self.auto_font)
        self.converter = W.QLineEdit(str(modern.find_oda_file_converter() or '')); self.converter.setPlaceholderText('ODAFileConverter.exe (for DWG)')
        fontform.addRow('DWG converter', self.converter)
        browse = W.QPushButton('Locate ODA converter…'); browse.clicked.connect(self.choose_converter); fontform.addRow(browse)
        for label, fn in [('Create / update plot area', self.render_plot), ('Preview before saving…', self.preview_before_save), ('Save plot (PDF / PNG / SVG / DXF / DWG)…', self.save_plot)]:
            button = W.QPushButton(label); controls.addWidget(button); button.clicked.connect(lambda checked=False, f=fn:self.safe(f))
        self.status = W.QLabel(); self.status.setWordWrap(True); controls.addWidget(self.status)
        note = W.QLabel(f'<a href="{REFERENCE}">IHO INT 2 graduation reference</a><br>Mercator / WGS84. Selectable INT 2 patterns, borders, grids and scales. Open the reference for production review.')
        note.setWordWrap(True); note.setOpenExternalLinks(True); controls.addWidget(note)
        self.preview_timer = C.QTimer(self); self.preview_timer.setSingleShot(True); self.preview_timer.timeout.connect(self.update_preview)
        for control in (self.title, self.font_family, self.font_style, self.font_size, self.auto_font, self.graduation, self.page, self.labels, *self.bounds.values(), *self.options.values()):
            signal = control.textChanged if isinstance(control,W.QLineEdit) else control.currentIndexChanged if isinstance(control,W.QComboBox) else control.valueChanged if isinstance(control,(W.QDoubleSpinBox,W.QSpinBox)) else control.toggled
            signal.connect(lambda *_:self.preview_timer.start(350))
        self.options['margin'].valueChanged.connect(self.update_placement_limits)
        self.options['exceptional'].toggled.connect(self.update_placement_limits)
        self.files.itemChanged.connect(lambda *_:self.preview_timer.start(350))
        self.render_plot()

    METADATA_FIELDS = [
        ('region','Region / coast'), ('chart_title','Chart title'),
        ('surveyed_by','Surveyed by'), ('vessel','Survey vessel / organization'),
        ('dates','Survey dates'), ('heights','Heights reference'),
        ('depth_units','Depth units'), ('precision','Sounding precision note'),
        ('vertical_datum','Vertical datum / benchmark notes'),
        ('projection','Projection / zone / central meridian'), ('datum','Horizontal datum'),
        ('scale','Scale statement'), ('report','Survey report reference'),
        ('approval','Approval / name / signature text'), ('role','Approving officer / role'),
        ('publication','Publication / publisher / date / authority'),
        ('copyright','Copyright'), ('footnote','Additional footnote'), ('privacy','Privacy marking')]

    def edit_metadata(self):
        dialog=W.QDialog(self);dialog.setWindowTitle('Title block and footnotes — editable chart information')
        dialog.setWindowFlags(dialog.windowFlags()|C.Qt.WindowType.WindowMaximizeButtonHint)
        layout=W.QVBoxLayout(dialog)
        hint=W.QLabel('Enter your own chart information. Blank fields are omitted. Each item can be dragged independently in either preview. Positions are retained when the plot is updated.')
        hint.setWordWrap(True);layout.addWidget(hint)
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);body=W.QWidget();form=W.QFormLayout(body);scroll.setWidget(body);layout.addWidget(scroll,1)
        family=W.QComboBox();family.addItems([self.font_family.itemText(i) for i in range(self.font_family.count())]);family.setCurrentText(self.metadata_font);form.addRow('Cartographic font',family)
        size=W.QDoubleSpinBox();size.setRange(3,48);size.setValue(self.metadata_size);size.setSuffix(' pt');form.addRow('Body font size (fitted to available space)',size)
        width=W.QDoubleSpinBox();width.setRange(10,95);width.setValue(self.metadata_width);width.setSuffix(' %');form.addRow('Title block width (% of map)',width)
        fields={}
        for key,label in self.METADATA_FIELDS:
            edit=W.QPlainTextEdit(self.metadata.get(key,''));edit.setMinimumHeight(64);edit.setMaximumHeight(95);form.addRow(label,edit);fields[key]=edit
        reset=W.QCheckBox('Reset all information to the default layout');layout.addWidget(reset)
        buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Apply|W.QDialogButtonBox.StandardButton.Close);layout.addWidget(buttons)
        def apply():
            self.metadata={key:edit.toPlainText().strip() for key,edit in fields.items()}
            self.metadata_font=family.currentText();self.metadata_size=size.value();self.metadata_width=width.value()
            if reset.isChecked():self.metadata_positions.clear();reset.setChecked(False)
            self.safe(self.render_plot)
        buttons.button(W.QDialogButtonBox.StandardButton.Apply).clicked.connect(apply)
        buttons.rejected.connect(dialog.reject)
        dialog.showMaximized();dialog.exec()

    def draw_metadata(self,renderer):
        import textwrap
        from matplotlib.font_manager import FontProperties
        self._metadata_artists={}
        area=self.ax.get_position();fw,fh=self.figure.bbox.width,self.figure.bbox.height
        main=[(k,self.metadata.get(k,'')) for k,_ in self.METADATA_FIELDS if k not in ('publication','copyright','footnote','privacy') and self.metadata.get(k,'')]
        foot=[(k,self.metadata.get(k,'')) for k in ('publication','copyright','footnote') if self.metadata.get(k,'')]
        if self.metadata.get('chart_title'):self.figure.texts[0].set_visible(False)
        if foot:self.figure.texts[1].set_visible(False)
        def layout_rows(rows,width,height):
            size=self.metadata_size
            for attempt in range(60):
                result=[];total=0
                for key,value in rows:
                    factor=2 if key=='chart_title' else 1
                    prop=FontProperties(family=self.metadata_font,size=size*factor)
                    lines=[]
                    for paragraph in value.splitlines():
                        line=''
                        for word in paragraph.split():
                            candidate=(line+' '+word).strip()
                            if line and renderer.get_text_width_height_descent(candidate,prop,False)[0]>width:
                                lines.append(line);line=word
                            else:line=candidate
                        lines.append(line)
                    text='\n'.join(lines)
                    measured=max((renderer.get_text_width_height_descent(line,prop,False)[0] for line in lines),default=0)
                    actual=size*factor*min(1,width/max(measured,1))
                    rowheight=(len(lines)*1.3+.6)*actual*self.figure.dpi/72
                    result.append((key,text,actual,rowheight));total+=rowheight
                if total<=height or size<=1:break
                size*=.92
            return result
        def add(rows,width,height,x,y):
            for key,value,size,rowheight in layout_rows(rows,width,height):
                pos=self.metadata_positions.get(key,(x,y))
                artist=self.figure.text(*pos,value,ha='center',va='top',fontfamily=self.metadata_font,fontsize=size,
                    fontweight='bold' if key=='chart_title' else 'normal',
                    fontstyle='italic' if key in ('heights','vertical_datum','datum','report','approval','role') else 'normal',
                    linespacing=1.3,zorder=100,parse_math=False,
                    bbox=dict(facecolor='white',edgecolor='none',pad=2))
                artist.set_gid('chart_metadata_'+key);self._metadata_artists[key]=artist
                y-=rowheight/fh
        add(main,area.width*fw*self.metadata_width/100,area.height*fh*.85,area.x0+area.width*.5,area.y1-area.height*.06)
        # Publication lines occupy the paper margin beneath the border.
        below=[t.get_window_extent(renderer).y0 for t in self._border_texts+self._int2_texts if t.get_visible() and t.get_window_extent(renderer).y0<self.ax.bbox.y0]
        foot_top=min([area.y0*fh-12*self.figure.dpi/25.4]+below)-3*self.figure.dpi/25.4
        foot_top=max(fh*.025,foot_top)
        add(foot,fw*.86,max(8,foot_top-fh*.012),.5,foot_top/fh)
        if self.metadata.get('privacy'):
            add([('privacy',self.metadata['privacy'])],fw*.8,fh*.04,.5,.985)

    def metadata_mouse(self,label,event):
        if not hasattr(self,'move_information') or not self.move_information.isChecked():return False
        kind=event.type()
        if kind not in (C.QEvent.Type.MouseButtonPress,C.QEvent.Type.MouseMove,C.QEvent.Type.MouseButtonRelease):return False
        pix=label.pixmap()
        if pix is None or pix.isNull():return False
        rect=label.contentsRect();pw=pix.width()/pix.devicePixelRatio();ph=pix.height()/pix.devicePixelRatio()
        pos=event.position();x=(pos.x()-rect.x()-(rect.width()-pw)/2)/pw;y=1-(pos.y()-rect.y()-(rect.height()-ph)/2)/ph
        point=np.array([x*self.figure.bbox.width,y*self.figure.bbox.height])
        if kind==C.QEvent.Type.MouseButtonPress:
            if event.button()!=C.Qt.MouseButton.LeftButton:return False
            for key,artist in reversed(list(self._metadata_artists.items())):
                if artist.get_window_extent(self.canvas.get_renderer()).expanded(1.1,1.2).contains(*point):
                    self._drag_metadata=(key,np.array(artist.get_position())-np.array([x,y]));label.setCursor(C.Qt.CursorShape.ClosedHandCursor);return True
            return False
        if self._drag_metadata is None:return False
        key,offset=self._drag_metadata;artist=self._metadata_artists.get(key)
        if artist is not None:
            box=artist.get_window_extent(self.canvas.get_renderer());w=box.width/self.figure.bbox.width;h=box.height/self.figure.bbox.height
            target=np.array([x,y])+offset;target[0]=np.clip(target[0],w/2+.005,1-w/2-.005);target[1]=np.clip(target[1],h+.005,.995)
            self.metadata_positions[key]=tuple(target);artist.set_position(target)
            self.canvas.draw();image=G.QImage(self.canvas.buffer_rgba(),*self.canvas.get_width_height(),G.QImage.Format.Format_RGBA8888).copy();self._preview_pixmap=G.QPixmap.fromImage(image);self.refresh_preview_image()
            if label is not self.preview_image:label.setPixmap(self._preview_pixmap.scaled(label.contentsRect().size(),C.Qt.AspectRatioMode.KeepAspectRatio,C.Qt.TransformationMode.SmoothTransformation))
        if kind==C.QEvent.Type.MouseButtonRelease:
            self._drag_metadata=None;label.unsetCursor();self.safe(self.render_plot)
            if label is not self.preview_image:label.setPixmap(self._preview_pixmap.scaled(label.contentsRect().size(),C.Qt.AspectRatioMode.KeepAspectRatio,C.Qt.TransformationMode.SmoothTransformation))
        return True

    def start_map_limits_pick(self):
        import re
        owner=self.parentWidget();view=getattr(owner,'view',None)
        if view is None or not hasattr(view,'plotItem'):
            raise ValueError('Open plotting from the main MAP window to pick plot limits.')
        source=owner.crs_box.currentText().strip()
        match=re.search(r'EPSG:\s*(\d+)',source,re.I)
        if hasattr(owner,'engine') and 'settings' in getattr(owner,'context',{}):
            g=owner.context['settings'].geodetic
            if not g.use_projection:self._map_limits_crs=CRS.from_epsg(4326)
            else:
                if not g.use_tm_projection:raise ValueError('MAP selection requires the supported survey TM/UTM projection.')
                central,origin,scale,east,north=owner.engine.projection_parameters(g)
                self._map_limits_crs=CRS.from_proj4(f'+proj=tmerc +lat_0={origin} +lon_0={central} +k={scale} +x_0={east} +y_0={north} +a={g.semi_major_axis} +rf={g.flattening} +units=m +no_defs')
        else:self._map_limits_crs=CRS.from_user_input('EPSG:'+match.group(1) if match else source)
        self._pick_map_view=view;self._map_first_corner=None
        self._map_old_cursor=view.viewport().cursor();self._map_old_tracking=view.viewport().hasMouseTracking()
        self._map_outline=self.modern.pg.PlotDataItem([],[],pen=self.modern.pg.mkPen('#dc2626',width=2))
        self._map_outline.setZValue(100000);view.addItem(self._map_outline)
        self._map_pick_hint=W.QLabel('PLOT LIMITS: click two opposite corners • Escape / right-click cancels');owner.statusBar().addPermanentWidget(self._map_pick_hint)
        self.preview_timer.stop()
        self._map_pick_active=True;W.QApplication.instance().installEventFilter(self)
        view.viewport().setMouseTracking(True);view.viewport().setCursor(C.Qt.CursorShape.CrossCursor)
        self.hide();owner.showMaximized();owner.raise_();owner.activateWindow();view.setFocus()
        owner.statusBar().showMessage('Plot limits: click the first corner, then the opposite corner. Right-click or Escape cancels.')

    def map_limits_point(self,position):
        view=self._pick_map_view;scene=view.mapToScene(position.toPoint())
        if not view.plotItem.vb.sceneBoundingRect().contains(scene):return None
        point=view.plotItem.vb.mapSceneToView(scene)
        return float(point.x()),float(point.y())

    def update_map_limits_outline(self,point):
        x,y=self._map_first_corner;xx,yy=point
        self._map_outline.setData([x,xx,xx,x,x],[y,y,yy,yy,y])

    def map_limits_click(self,position):
        point=self.map_limits_point(position)
        if point is None:return
        if self._map_first_corner is None:
            self._map_first_corner=point
            self.parentWidget().statusBar().showMessage('Plot limits: click the opposite corner. Right-click or Escape cancels.')
            return
        x,y=self._map_first_corner;xx,yy=point
        if abs(xx-x)<1e-10 or abs(yy-y)<1e-10:
            self.parentWidget().statusBar().showMessage('Choose an opposite corner with non-zero width and height.');return
        transform=Transformer.from_crs(self._map_limits_crs,'EPSG:4326',always_xy=True)
        west,south,east,north=transform.transform_bounds(min(x,xx),min(y,yy),max(x,xx),max(y,yy),densify_pts=41,errcheck=True)
        if not all(math.isfinite(v) for v in (west,south,east,north)) or not -180<=west<east<=180 or not -85<south<north<85:
            raise ValueError('Selected area lies outside the supported Mercator limits or crosses the antimeridian. Choose another area.')
        self.import_map_selection((min(x,xx),min(y,yy),max(x,xx),max(y,yy)))
        for key,value in [('West',west),('East',east),('South',south),('North',north)]:
            widget=self.bounds[key];widget.blockSignals(True);widget.setValue(value);widget.blockSignals(False)
        self.options['placement'].blockSignals(True);self.options['placement'].setCurrentIndex(0);self.options['placement'].blockSignals(False)
        self.finish_map_limits_pick(True)

    def draw_map_feature(self,ax,kind,points,label,x,y):
        if not kind.startswith('map_'):return False
        color=self._map_styles.get(id(points),'#111827')
        if kind=='map_fill':
            from matplotlib.path import Path as MPath
            from matplotlib.patches import PathPatch
            codes=[]
            for length in self._map_fill_lengths.get(id(points),[len(points)]):codes.extend([MPath.MOVETO]+[MPath.LINETO]*(length-2)+[MPath.CLOSEPOLY])
            ax.add_patch(PathPatch(MPath(np.column_stack((x,y)),codes),facecolor=color,edgecolor='none',zorder=1))
        elif kind=='map_line':ax.plot(x,y,color=color,lw=.7,zorder=2)
        elif kind=='map_text':
            text=ax.text(x[0],y[0],label,color=color,ha='left',va='bottom',clip_on=True,zorder=4)
            self._int2_texts.append(text)
        else:
            ax.scatter(x,y,s=4,color=color,zorder=3)
            if self.labels.isChecked() and label:
                self.chart_text(ax,x[0],y[0],label,'sounding',ha='center',va='bottom',color=color,zorder=4)
        return True

    def import_map_selection(self,limits):
        """Take an independent, clipped snapshot of visible MAP geometry."""
        from shapely.geometry import box, LineString, Polygon
        owner=self.parentWidget();view=self._pick_map_view
        region=box(*limits);west,south,east,north=limits
        transformer=Transformer.from_crs(self._map_limits_crs,'EPSG:4326',always_xy=True)
        snapshots=[];seen_items=set();has_labels=False
        def convert(kind,points,label='',color='#111827',rings=None):
            points=np.asarray(points,dtype=float)
            if points.ndim!=2 or points.shape[1]<2 or len(points)==0:return []
            points=points[:,:2];result=[]
            def append(pts,mode):
                lon,lat=transformer.transform(pts[:,0],pts[:,1],errcheck=True)
                data=np.column_stack((lon,lat))
                if not np.isfinite(data).all():raise ValueError('Visible MAP data could not be transformed. Check survey projection settings.')
                data=data.copy();self._map_styles[id(data)]=color
                result.append((mode,data,str(label or '')))
            if kind=='cad_fill':
                from shapely.geometry.polygon import orient
                polygon=Polygon((rings or [points])[0],(rings or [points])[1:])
                if not polygon.is_valid:polygon=polygon.buffer(0)
                clipped=polygon.intersection(region)
                polygons=[clipped] if clipped.geom_type=='Polygon' else list(getattr(clipped,'geoms',[]))
                for poly in polygons:
                    if poly.is_empty or poly.geom_type!='Polygon':continue
                    poly=orient(poly,sign=1)
                    arrays=[np.asarray(ring.coords) for ring in [poly.exterior]+list(poly.interiors)]
                    append(np.concatenate(arrays),'map_fill');self._map_fill_lengths[id(result[-1][1])]=[len(a) for a in arrays]
                return result
            if kind in ('point','text','ktd') or len(points)==1:
                for pt in points:
                    if np.isfinite(pt).all() and west<=pt[0]<=east and south<=pt[1]<=north:append(pt.reshape(1,2),'map_text' if kind=='text' else 'map_point')
            else:
                # Split non-finite line separators; clip crossing segments even when both vertices are outside.
                runs=[];start=0
                for i in range(len(points)+1):
                    if i==len(points) or not np.isfinite(points[i]).all():
                        if i-start>=2:runs.append(points[start:i])
                        start=i+1
                def collect(geometry):
                    if geometry.is_empty:return
                    if geometry.geom_type=='LineString':append(np.asarray(geometry.coords),'map_line')
                    elif hasattr(geometry,'geoms'):
                        for child in geometry.geoms:collect(child)
                for run in runs:collect(LineString(run).intersection(region))
            return result
        for layer in getattr(owner,'layers',[]):
            group=list(getattr(layer,'group',None) or []);seen_items.update(id(item) for item in group)
            if not getattr(layer,'visible',True) or (group and not any(item.isVisible() for item in group)):continue
            converted=[]
            for shape in layer.shapes:
                if not getattr(shape,'cad_visible',True):continue
                if not getattr(layer,'cad_visibility',{}).get(getattr(shape,'cad_layer',''),True):continue
                label=getattr(shape,'label','')
                if not label and getattr(shape,'value',None) is not None:label=f'{shape.value:g}'
                if shape.kind=='ktd' and self.modern.is_ktd_nodata(getattr(shape,'value',None)):continue
                color=getattr(shape,'color','#111827') or '#111827'
                if getattr(shape,'cad_auto_color',False):color='#111827' if view.backgroundBrush().color().lightness()>128 else '#ffffff'
                converted.extend(convert(shape.kind,shape.points,label,color,getattr(shape,'rings',None)))
            snapshots.append(('MAP — '+layer.name,converted))
        # Soundings retain their actual depth values, independently of screen label thinning.
        live_item=getattr(view,'live_depth_scatter',None)
        if live_item is not None:
            seen_items.add(id(live_item));converted=[]
            if live_item.isVisible():
                for x,y,depth in getattr(owner,'live_soundings',[]):converted.extend(convert('point',[(x,y)],f'{depth:g}'))
            if converted:snapshots.append(('MAP — Live soundings',converted))
        # Include other visible map graphics (track, runlines and navigation overlays).
        for index,item in enumerate(view.plotItem.items):
            if id(item) in seen_items or item is self._map_outline or not item.isVisible():continue
            if isinstance(item,self.modern.pg.TextItem):
                position=item.pos();converted=convert('text',[(position.x(),position.y())],item.toPlainText(),item.color.name())
                if converted:snapshots.append((f'MAP — Annotation {index+1}',converted))
            elif isinstance(item,(self.modern.pg.PlotDataItem,self.modern.pg.ScatterPlotItem)):
                x,y=item.getData()
                if x is None or y is None or len(x)==0:continue
                pts=np.column_stack((x,y))
                kind='point' if isinstance(item,self.modern.pg.ScatterPlotItem) or item.opts.get('pen') is None else 'line'
                style=item.opts.get('pen') if kind=='line' else item.opts.get('brush')
                color=style.color().name() if hasattr(style,'color') else '#111827'
                converted=convert(kind,pts,color=color)
                if converted:snapshots.append((f'MAP — {item.name() or "Visible graphics "+str(index+1)}',converted))
        self.files.blockSignals(True)
        try:
            for i in range(self.files.count()-1,-1,-1):
                if self.files.item(i).data(C.Qt.ItemDataRole.UserRole)=='map_snapshot':self.files.takeItem(i);self.datasets.pop(i)
            for name,shapes in snapshots:
                self.datasets.append((name,self._map_limits_crs.to_string(),shapes))
                item=W.QListWidgetItem(name);item.setFlags(item.flags()|C.Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(C.Qt.CheckState.Checked);item.setData(C.Qt.ItemDataRole.UserRole,'map_snapshot')
                item.setToolTip('Snapshot of visible MAP data clipped to the selected rectangle. Pick limits again to refresh.');self.files.addItem(item)
                has_labels=has_labels or any(label for _,_,label in shapes)
        finally:self.files.blockSignals(False)
        if has_labels:self.labels.setChecked(True)
        self._map_snapshot_count=len(snapshots)

    def finish_map_limits_pick(self,accepted):
        if not self._map_pick_active:return
        self._map_pick_active=False;W.QApplication.instance().removeEventFilter(self)
        self.parentWidget().statusBar().removeWidget(self._map_pick_hint);self._map_pick_hint.deleteLater()
        view=self._pick_map_view;view.removeItem(self._map_outline)
        view.viewport().setCursor(self._map_old_cursor);view.viewport().setMouseTracking(self._map_old_tracking)
        self.parentWidget().statusBar().showMessage('Plot limits transferred to plotting.' if accepted else 'Plot limit selection cancelled.',5000)
        self.showMaximized();self.raise_();self.activateWindow()
        if accepted:self.render_plot()

    def make_sheet_series(self,scale=25000,overlap=10,orientation='A0 landscape',crs_text='EPSG:32642',skip_empty=True,font_size=8):
        from shapely.geometry import Point,LineString,box
        from shapely.strtree import STRtree
        crs=CRS.from_user_input(crs_text);self.validate_origin_crs(crs)
        transform=Transformer.from_crs(4326,crs,always_xy=True)
        geometry=[]
        for _,_,shapes in self.checked():
            for kind,points,label in shapes:
                x,y=transform.transform(points[:,0],points[:,1],errcheck=True);xy=np.column_stack((x,y))
                if not np.isfinite(xy).all():raise ValueError('A selected dataset cannot be transformed to the series CRS.')
                if len(xy)==1 or kind in ('point','map_point','map_text'):geometry.extend(Point(p) for p in xy)
                else:geometry.append(LineString(xy))
        if not geometry:raise ValueError('Load and check the full survey/corridor data first. A MAP selection contains only the selected area.')
        bounds=np.asarray([g.bounds for g in geometry]);xmin,ymin=bounds[:,:2].min(axis=0);xmax,ymax=bounds[:,2:].max(axis=0)
        sw,sh=(1189,841) if orientation.endswith('landscape') else (841,1189)
        margin=self.opt('margin');lim=(1100,750) if sw>sh else (750,1100)
        width=min(lim[0],sw-2*(margin+14));height=min(lim[1],sh-2*(margin+14)-22)
        if min(width,height)<=5:raise ValueError('Reduce the sheet margins.')
        dx,dy=width*scale/1000,height*scale/1000;stepx=dx*(1-overlap/100);stepy=dy*(1-overlap/100)
        # A small edge allowance keeps edge soundings clear of the neatline.
        pad=min(dx,dy)*.02;xmin-=pad;xmax+=pad;ymin-=pad;ymax+=pad
        nx=max(1,math.ceil(max(0,xmax-xmin-dx)/stepx)+1);ny=max(1,math.ceil(max(0,ymax-ymin-dy)/stepy)+1)
        if nx*ny>10000:raise ValueError(f'This requires {nx*ny:,} grid blocks. Increase the scale denominator or reduce the selected data extent.')
        tree=STRtree(geometry);sheets=[]
        for row in range(ny):
            for col in range(nx):
                x=xmin+col*stepx;y=ymax-dy-row*stepy
                if skip_empty and len(tree.query(box(x,y,x+dx,y+dy),predicate='intersects'))==0:continue
                sheets.append(dict(number=len(sheets)+1,row=row+1,column=col+1,x=x,y=y,width=width,height=height,dx=dx,dy=dy))
        return dict(sheets=sheets,scale=scale,overlap=overlap,orientation=orientation,crs=crs.to_string(),font_size=font_size,title=self.title.text(),geometry=geometry,dataset_ids=[id(data) for data in self.checked()])

    def preview_series_sheet(self,series,index):
        if series['dataset_ids']!=[id(data) for data in self.checked()]:raise ValueError('Checked data changed. Generate the sheet blocks again before preview/export.')
        sheet=series['sheets'][index];self.page.setCurrentText(series['orientation']);self.update_placement_limits()
        values={'origin_crs':series['crs'],'origin_x':sheet['x'],'origin_y':sheet['y'],'plot_width':sheet['width'],'plot_height':sheet['height'],'plot_scale':series['scale'],'rotation':0}
        for key,value in values.items():
            widget=self.options[key];widget.blockSignals(True)
            if isinstance(widget,W.QLineEdit):widget.setText(value)
            else:widget.setValue(value)
            widget.blockSignals(False)
        self.options['placement'].setCurrentIndex(1);self.options['origin_anchor'].setCurrentIndex(0)
        self.options['avoid_overlap'].setChecked(True);self.labels.setChecked(True);self.font_size.setValue(series['font_size']);self.auto_font.setChecked(True)
        self.title.setText(f'{series["title"]} — Sheet {sheet["number"]:03d} / {len(series["sheets"]):03d}')
        self._series_limits={key:self.opt(key) for key in ('placement','origin_crs','origin_x','origin_y','plot_width','plot_height','plot_scale','rotation','origin_anchor')}
        self._series_stamp=f'SHEET {sheet["number"]:03d} / {len(series["sheets"]):03d}'
        self.render_plot()

    def open_sheet_series(self):
        existing=getattr(self,'_series_dialog',None)
        if existing is not None:existing.showMaximized();existing.raise_();return
        dialog=W.QDialog(self);dialog.setWindowTitle('A0 cascade sheet series — Attock to Keti Bandar');dialog.setWindowFlags(dialog.windowFlags()|C.Qt.WindowType.WindowMaximizeButtonHint)
        layout=W.QVBoxLayout(dialog);hint=W.QLabel('Coverage follows ALL checked survey data, north to south. Load the complete Attock–Keti Bandar survey first; a clipped MAP selection cannot provide the missing reach. Scale is projected grid scale. Sheet overlap preserves continuity; sounding collision prevention stays on.')
        hint.setWordWrap(True);layout.addWidget(hint);form=W.QFormLayout();layout.addLayout(form)
        orientation=W.QComboBox();orientation.addItems(['A0 landscape','A0 portrait']);form.addRow('Paper',orientation)
        crs=W.QLineEdit('EPSG:32642');form.addRow('Series projected CRS (metres)',crs)
        scale=W.QDoubleSpinBox();scale.setRange(100,5000000);scale.setDecimals(0);scale.setValue(25000);form.addRow('Scale 1 :',scale)
        overlap=W.QDoubleSpinBox();overlap.setRange(0,40);overlap.setValue(10);overlap.setSuffix(' %');form.addRow('Adjacent-sheet overlap',overlap)
        font=W.QDoubleSpinBox();font.setRange(5,20);font.setValue(8);font.setSuffix(' pt');form.addRow('Sounding / label size',font)
        skip=W.QCheckBox('Only sheets intersecting checked data (river corridor)');skip.setChecked(True);form.addRow(skip)
        controls=W.QHBoxLayout();layout.addLayout(controls);generate=W.QPushButton('Generate sheet blocks');preview=W.QPushButton('Preview selected sheet');export=W.QPushButton('Export checked sheets…');controls.addWidget(generate);controls.addWidget(preview);controls.addWidget(export)
        format_box=W.QComboBox();format_box.addItems(['PDF','DXF','DWG','PNG','SVG']);controls.addWidget(format_box)
        split=W.QSplitter();layout.addWidget(split,1);table=W.QTableWidget(0,4);table.setHorizontalHeaderLabels(['Sheet / export','Row / column','Origin E','Origin N']);table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows);table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers);split.addWidget(table)
        overview=Figure(figsize=(8,6));canvas=FigureCanvasQTAgg(overview);split.addWidget(canvas);split.setStretchFactor(1,1)
        status=W.QLabel('Generate an index, review the blocks, then preview or export.');status.setWordWrap(True);layout.addWidget(status);state={};preview.setEnabled(False);export.setEnabled(False)
        def generate_blocks():
            series=self.make_sheet_series(scale.value(),overlap.value(),orientation.currentText(),crs.text(),skip.isChecked(),font.value());state['series']=series
            table.setRowCount(len(series['sheets']));overview.clear();ax=overview.add_subplot(111)
            from matplotlib.patches import Rectangle
            for geometry in series['geometry']:
                xy=np.asarray(geometry.coords);ax.plot(xy[:,0],xy[:,1],color='#126a91',lw=.5,marker='.' if len(xy)==1 else None,markersize=1)
            for i,sheet in enumerate(series['sheets']):
                item=W.QTableWidgetItem(f'{i+1:03d}');item.setFlags(item.flags()|C.Qt.ItemFlag.ItemIsUserCheckable);item.setCheckState(C.Qt.CheckState.Checked);table.setItem(i,0,item)
                for j,value in enumerate([f'{sheet["row"]} / {sheet["column"]}',f'{sheet["x"]:.2f}',f'{sheet["y"]:.2f}'],1):table.setItem(i,j,W.QTableWidgetItem(value))
                ax.add_patch(Rectangle((sheet['x'],sheet['y']),sheet['dx'],sheet['dy'],fill=False,edgecolor='#dc2626',lw=.6));ax.text(sheet['x']+sheet['dx']/2,sheet['y']+sheet['dy']/2,str(i+1),ha='center',fontsize=6)
            ax.set_aspect('equal');ax.autoscale_view();ax.set_title(f'A0 sheet index • 1:{series["scale"]:g} • {series["overlap"]:g}% overlap');ax.set_xlabel('Easting (m)');ax.set_ylabel('Northing (m)');overview.tight_layout();canvas.draw()
            preview.setEnabled(bool(series['sheets']));export.setEnabled(bool(series['sheets']));table.selectRow(0);status.setText(f'{len(series["sheets"]):,} A0 sheets. Preview labels before export; decrease the scale denominator for more ground detail.')
        def preview_one():
            row=table.currentRow()
            if row>=0:self.preview_series_sheet(state['series'],row);dialog.showMinimized();self.showMaximized();self.raise_()
        def export_all():
            import csv,datetime
            series=state['series'];indices=[i for i in range(table.rowCount()) if table.item(i,0).checkState()==C.Qt.CheckState.Checked]
            if not indices:raise ValueError('Check at least one sheet to export.')
            folder=W.QFileDialog.getExistingDirectory(dialog,'Select parent folder for new sheet series')
            if not folder:return
            root=Path(folder)/('A0_series_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'));root.mkdir()
            progress=W.QProgressDialog('Exporting A0 sheets…','Cancel',0,len(indices),dialog);progress.setWindowModality(C.Qt.WindowModality.ApplicationModal);progress.setMinimumDuration(0)
            rows=[]
            try:
                overview.savefig(root/'sheet_index.pdf')
                for done,index in enumerate(indices):
                    progress.setValue(done);W.QApplication.processEvents()
                    if progress.wasCanceled():break
                    self.preview_series_sheet(series,index);sheet=series['sheets'][index];filename=f'sheet_{sheet["number"]:03d}.'+format_box.currentText().lower();self.export_file(root/filename)
                    rows.append([filename,sheet['number'],sheet['row'],sheet['column'],sheet['x'],sheet['y'],sheet['dx'],sheet['dy'],series['crs'],series['scale'],series['overlap'],series['font_size']])
            finally:
                progress.close()
                with (root/'sheet_manifest.csv').open('w',newline='',encoding='utf-8-sig') as handle:
                    writer=csv.writer(handle);writer.writerow(['file','sheet','row','column','origin_e','origin_n','ground_width_m','ground_height_m','crs','scale_denominator','overlap_percent','font_pt']);writer.writerows(rows)
                status.setText(f'Exported {len(rows)} of {len(indices)} sheets to {root}')
        def invalidate(*_):preview.setEnabled(False);export.setEnabled(False);status.setText('Settings changed. Generate sheet blocks again.')
        for widget in (orientation,crs,scale,overlap,font,skip):
            signal=widget.currentIndexChanged if isinstance(widget,W.QComboBox) else widget.textChanged if isinstance(widget,W.QLineEdit) else widget.toggled if isinstance(widget,W.QCheckBox) else widget.valueChanged;signal.connect(invalidate)
        generate.clicked.connect(lambda:self.safe(generate_blocks));preview.clicked.connect(lambda:self.safe(preview_one));export.clicked.connect(lambda:self.safe(export_all))
        self._series_dialog=dialog;dialog.showMaximized()

    def update_extent_label(self,*_):
        if not hasattr(self,'extent_label'):return
        w=self.opt('plot_width');h=self.opt('plot_height');scale=self.opt('plot_scale')
        for key,printed in [('ground_width',w),('ground_height',h)]:
            widget=self.options[key];widget.blockSignals(True)
            source=self.options['plot_width' if key=='ground_width' else 'plot_height']
            widget.setRange(source.minimum()*scale/1000,source.maximum()*scale/1000);widget.setValue(printed*scale/1000);widget.blockSignals(False)
        self.extent_label.setText(f'Plot extent: {w*scale/1000:,.3f} × {h*scale/1000:,.3f} projected metres.\nWidth/height limits follow the selected sheet and margins.')

    def set_ground_dimension(self,key,value):
        self.options[key].setValue(value*1000/self.opt('plot_scale'))

    def apply_placement(self):
        self.options['placement'].setCurrentIndex(1);self.render_plot()

    def update_placement_limits(self,*_):
        sw,sh=self.sheet_mm();margin=self.opt('margin') if 'margin' in self.options else 20
        long,short=(1110,760) if 'exceptional' in self.options and self.opt('exceptional') else (1100,750)
        limits=(long,short) if sw>=sh else (short,long)
        self.options['plot_width'].setMaximum(max(5,min(limits[0],sw-2*(margin+14))))
        self.options['plot_height'].setMaximum(max(5,min(limits[1],sh-2*(margin+14)-22)))
        self.update_extent_label()

    def start_origin_pick(self):
        self._picking_origin=True;self.preview_image.setCursor(C.Qt.CursorShape.CrossCursor)
        self.status.setText('Click inside the map preview to set the plot origin. X/Y use the Origin / CAD CRS. Press Escape to cancel.')

    def show_origin_overview(self):
        self.options['placement'].setCurrentIndex(0)
        if self.checked():self.safe(self.fit_files)
        else:self.safe(self.render_plot)
        self.start_origin_pick()

    def keyPressEvent(self,event):
        if event.key()==C.Qt.Key.Key_Escape and self._picking_origin:
            self._picking_origin=False;self.preview_image.unsetCursor();self.status.setText('Origin selection cancelled.');return
        super().keyPressEvent(event)

    def pick_origin_at(self,position):
        pix=self.preview_image.pixmap()
        if pix is None or pix.isNull():raise ValueError('Create a preview first.')
        pw,ph=pix.width()/pix.devicePixelRatio(),pix.height()/pix.devicePixelRatio()
        rect=self.preview_image.contentsRect();left=rect.x()+(rect.width()-pw)/2;top=rect.y()+(rect.height()-ph)/2
        px=(position.x()-left)/pw;py=(position.y()-top)/ph
        if not 0<=px<=1 or not 0<=py<=1:raise ValueError('Click inside the displayed plot.')
        display=(px*self.figure.bbox.width,(1-py)*self.figure.bbox.height)
        if not self.ax.bbox.contains(*display):raise ValueError('Click inside the main map neatline, not the paper margin.')
        x,y=self.ax.transData.inverted().transform(display)
        x,y=self._pick_to_world(float(x),float(y))
        crs=CRS.from_user_input(self.opt('origin_crs'))
        self.validate_origin_crs(crs)
        x,y=Transformer.from_crs(self._pick_crs,crs,always_xy=True).transform(x,y,errcheck=True)
        for key,value in [('origin_x',x),('origin_y',y)]:
            self.options[key].blockSignals(True);self.options[key].setValue(value);self.options[key].blockSignals(False)
        self._picking_origin=False;self.preview_image.unsetCursor()
        self.options['placement'].setCurrentIndex(1);self.render_plot()

    @staticmethod
    def validate_origin_crs(crs):
        if not crs.is_projected or any(abs(axis.unit_conversion_factor-1)>1e-8 for axis in crs.axis_info[:2]):
            raise ValueError('Origin / CAD CRS must be projected and use metres, for example EPSG:32642 or EPSG:3395.')

    def prevent_sounding_overlap(self,renderer):
        if not self.opt('avoid_overlap'):return ''
        from matplotlib.transforms import Bbox
        gap=self.opt('label_gap')/25.4*self.figure.dpi
        bins={};cell=max(24,self.font_size.value()*self.figure.dpi/72*3)
        def keys(box):
            return [(i,j) for i in range(math.floor(box.x0/cell),math.floor(box.x1/cell)+1) for j in range(math.floor(box.y0/cell),math.floor(box.y1/cell)+1)]
        def insert(box):
            for key in keys(box):bins.setdefault(key,[]).append(box)
        def padded(box):return Bbox.from_extents(box.x0-gap/2,box.y0-gap/2,box.x1+gap/2,box.y1+gap/2)
        for text in self._int2_texts+self._border_texts:
            if text.get_visible() and text.get_text():insert(padded(text.get_window_extent(renderer)))
        for text in self._metadata_artists.values():
            if text.get_visible() and text.get_text():insert(padded(text.get_window_extent(renderer)))
        for ax,_ in self._map_axes:
            legend=ax.get_legend()
            if legend is not None:insert(padded(legend.get_window_extent(renderer)))
        def priority(text):
            try:value=float(text.get_text().replace(',','.'));return value if math.isfinite(value) else float('inf')
            except ValueError:return float('inf')
        kept=hidden=0
        for text in sorted(self._sounding_texts,key=priority):
            text.set_visible(True)
            if hasattr(text,'_sounding_anchor'):text.set_position(text._sounding_anchor)
            box=padded(text.get_window_extent(renderer));axbox=text.axes.bbox
            inside=box.x0>=axbox.x0 and box.x1<=axbox.x1 and box.y0>=axbox.y0 and box.y1<=axbox.y1
            blocked=not inside or any(box.overlaps(other) for key in keys(box) for other in bins.get(key,()))
            # Do not draw parent-map labels through an overlaid inset.
            for other,g in self._map_axes:
                if other is not text.axes and other.get_zorder()>text.axes.get_zorder() and box.overlaps(other.bbox):blocked=True
            if blocked:text.set_visible(False);hidden+=1
            else:insert(box);kept+=1
        self._overlap_stats=(kept,hidden)
        return f' Soundings: {kept:,} labels shown, {hidden:,} suppressed to avoid overlap; shallower depths take priority. Source points unchanged.'

    def render_placed_plot(self):
        from matplotlib.patches import Rectangle
        from shapely.geometry import LineString,box
        self.preview_timer.stop();self.update_placement_limits()
        crs=CRS.from_user_input(self.opt('origin_crs'));self.validate_origin_crs(crs)
        ox,oy=self.opt('origin_x'),self.opt('origin_y');angle=math.radians(self.opt('rotation'));cos,sin=math.cos(angle),math.sin(angle)
        width,height,scale=self.opt('plot_width'),self.opt('plot_height'),self.opt('plot_scale')
        dx,dy=width*scale/1000,height*scale/1000
        cx,cy=(dx/2,dy/2) if self.opt('origin_anchor').startswith('Centre') else (0,0)
        def world(x,y):return (ox+cos*(x-cx)-sin*(y-cy),oy+sin*(x-cx)+cos*(y-cy))
        def local(x,y):
            x=np.asarray(x)-ox;y=np.asarray(y)-oy
            return (cos*x+sin*y+cx,-sin*x+cos*y+cy)
        to_world=Transformer.from_crs('EPSG:4326',crs,always_xy=True)
        to_ll=Transformer.from_crs(crs,'EPSG:4326',always_xy=True)
        self._pick_to_world=world;self._pick_crs=crs;self.figure._cad_to_world=world;self.figure._cad_crs=crs.to_string()
        self._warnings=[self.data_warnings[path] for path,_,_ in self.checked() if self.data_warnings.get(path)];self._sounding_texts=[];self._border_texts=[];self._int2_texts=[];self._map_axes=[]
        sw,sh=self.sheet_mm();self.figure.set_size_inches(sw/25.4,sh/25.4);self.figure.set_dpi(min(144,4400/(max(sw,sh)/25.4)));self.figure.clear()
        ax=self.figure.add_axes([(sw-width)/2/sw,(sh-height)/2/sh,width/sw,height/sh]);self.ax=ax
        ax.set_xlim(0,dx);ax.set_ylim(0,dy);ax.set_aspect('equal');ax.set_xticks([]);ax.set_yticks([]);ax.set_facecolor('#f6fbfd')
        unit=scale/1000;outer=self.dual_border_width(12.5);bounds_local=box(0,0,dx,dy)
        ax.add_patch(Rectangle((-outer*unit,-outer*unit),dx+2*outer*unit,dy+2*outer*unit,fill=False,lw=self.opt('outer_lw')*72/25.4,clip_on=False))
        corners=[world(x,y) for x,y in [(0,0),(dx,0),(dx,dy),(0,dy)]]
        self._placed_corners=corners;self._placed_scale=scale
        perimeter=[world(x,y) for x,y in zip(np.r_[np.linspace(0,dx,80),np.full(80,dx),np.linspace(dx,0,80),np.zeros(80)],np.r_[np.zeros(80),np.linspace(0,dy,80),np.full(80,dy),np.linspace(dy,0,80)])]
        lon,lat=to_ll.transform(*zip(*perimeter),errcheck=True)
        if not np.isfinite(lon).all() or not np.isfinite(lat).all() or max(lon)-min(lon)>180:raise ValueError('Plot footprint is outside the supported CRS area or crosses the antimeridian. Adjust the origin or dimensions.')
        west,east,south,north=min(lon),max(lon),min(lat),max(lat)
        self._map_axes=[(ax,dict(x0=0,x1=dx,y0=0,y1=dy,width=width,height=height,ux=unit,uy=unit,inset=False))]
        count=0
        for index,(path,_,shapes) in enumerate(self.checked()):
            colour=('#126a91','#c24b31','#338443','#8652a3','#956116')[index%5];px=[];py=[];used=False
            for kind,points,label in shapes:
                x,y=to_world.transform(points[:,0],points[:,1],errcheck=True);x,y=local(x,y)
                if self.draw_map_feature(ax,kind,points,label,x,y):count+=len(points);continue
                if kind=='point' or len(points)==1:
                    inside=(x>=0)&(x<=dx)&(y>=0)&(y<=dy);px.extend(x[inside]);py.extend(y[inside]);count+=int(inside.sum())
                    if self.labels.isChecked() and label and inside[0] and len(self._sounding_texts)<2000:self.chart_text(ax,x[0],y[0],label,'sounding',ha='center',va='bottom')
                else:
                    ax.plot(x,y,color=colour,lw=.6,label=Path(path).name if not used else None);used=True
            if px:ax.scatter(px,py,s=2,color=colour,label=Path(path).name if not used else None)
        if self.opt('legend') and ax.get_legend_handles_labels()[0]:ax.legend(loc='upper left',fontsize=6)
        interval=self.graduation.currentData()
        if interval is None:
            required=max((east-west)*60*30/width,(north-south)*60*30/height)
            interval=next((v for v in (.01,.02,.05,.1,.2)+INTERVALS if v>=required),300)
        # Intersections are calculated on the rotated sheet boundary, not relabelled unrotated axes.
        for longitude,low,high in [(True,west,east),(False,south,north)]:
            for value in tick_values(low,high,interval):
                lons=np.full(160,value) if longitude else np.linspace(west,east,160)
                lats=np.linspace(south,north,160) if longitude else np.full(160,value)
                gx,gy=local(*to_world.transform(lons,lats));line=LineString(np.column_stack((gx,gy))).intersection(bounds_local)
                parts=[line] if line.geom_type=='LineString' else list(getattr(line,'geoms',()))
                for part in parts:
                    if part.geom_type!='LineString' or part.is_empty:continue
                    arr=np.asarray(part.coords)
                    if self.opt('graticule')!='Off':ax.plot(arr[:,0],arr[:,1],color='.6',lw=.3,zorder=0)
                    for x,y in (arr[0],arr[-1]):
                        distances=[abs(x),abs(x-dx),abs(y),abs(y-dy)];side=int(np.argmin(distances))
                        enabled={'All four':{0,1,2,3},'Bottom and left':{0,2},'Top and right':{1,3},'None (small plans only)':set()}[self.opt('sides')]
                        if distances[side]>unit*.01 or side not in enabled:continue
                        vx,vy=[(-1,0),(1,0),(0,-1),(0,1)][side]
                        ax.plot([x,x+vx*3*unit],[y,y+vy*3*unit],color='black',lw=.4,clip_on=False)
                        self.chart_text(ax,x+vx*6*unit,y+vy*6*unit,self.border_label(value,interval,longitude),'border',ha='center',va='center',rotation=90 if side<2 else 0)
        if self.opt('corner_coords'):
            for (px,py),(x,y) in zip(corners,[(0,0),(dx,0),(dx,dy),(0,dy)]):
                lon,lat=to_ll.transform(px,py)
                self.chart_text(ax,x,y+(-outer-2 if y==0 else outer+2)*unit,coordinate_label(lat,False)+' / '+coordinate_label(lon),ha='left' if x==0 else 'right',va='top' if y==0 else 'bottom')
        self._map_axes.append((ax,dict(x0=0,x1=dx,y0=0,y1=dy,ux=unit,uy=unit,width=width,height=height,scale=scale)))
        self.draw_utm_graduation(ax,dict(x0=0,x1=dx,y0=0,y1=dy,ux=unit,uy=unit,width=width,height=height,scale=scale),crs,world,local)
        # North direction is transformed into the selected sheet axes.
        wx,wy=world(dx/2,dy/2);lon,lat=to_ll.transform(wx,wy);nx,ny=to_world.transform(lon,lat+.001);nx,ny=local(nx,ny)
        vector=np.array([nx-dx/2,ny-dy/2]);vector=vector/max(np.linalg.norm(vector),1e-12)*12*unit
        anchor=np.array([dx-18*unit,dy-22*unit]);end=anchor+vector
        ax.plot([anchor[0],end[0]],[anchor[1],end[1]],color='black',lw=.9)
        back=-vector/np.linalg.norm(vector)*2*unit;side=np.array([-back[1],back[0]])*.4
        ax.plot([end[0]+back[0]+side[0],end[0],end[0]+back[0]-side[0]],[end[1]+back[1]+side[1],end[1],end[1]+back[1]-side[1]],color='black',lw=.8)
        self.chart_text(ax,end[0],end[1]+unit,'N',ha='center',va='bottom')
        ax.plot([cx],[cy],marker='+',color='#b12d80',ms=7,clip_on=False)
        self.figure.text(.5,1-12/sh,self.title.text(),ha='center',va='top',fontsize=15)
        self.figure.text(.5,10/sh,f'{crs.to_string()} • projected scale 1:{scale:,.0f} • {width:g} × {height:g} mm • rotation {self.opt("rotation"):g}°\nOrigin {ox:.3f}, {oy:.3f} m • ground extent {dx:,.3f} × {dy:,.3f} m',ha='center',va='bottom',fontsize=7)
        self.canvas.draw();note=self.apply_lettering();self.canvas.draw()
        image=G.QImage(self.canvas.buffer_rgba(),*self.canvas.get_width_height(),G.QImage.Format.Format_RGBA8888).copy();self._preview_pixmap=G.QPixmap.fromImage(image);self.refresh_preview_image()
        extra=' Rotated sheets use boundary-intersection coordinate ticks; INT 2 preset borders, inset plans and scale-bar options apply in geographic-bounds mode.'
        self.status.setText(f'{count:,} points in placed plot.'+note+extra+'\n'+'\n'.join(self._warnings))
        return scale

    def choose_converter(self):
        name,_ = W.QFileDialog.getOpenFileName(self,'Locate ODA File Converter','','Executable (*.exe);;All files (*)')
        if name:self.converter.setText(name)

    def update_preview(self):
        try:self.render_plot()
        except Exception as exc:self.status.setText('Preview needs adjustment: '+str(exc))

    def eventFilter(self, obj, event):
        if obj is self.preview_image and hasattr(self,'preview_scroll'):
            if event.type()==C.QEvent.Type.Wheel:
                delta=event.angleDelta().y()
                if delta:self.zoom_preview(1.25**(delta/120),event.position())
                return True
            if event.type()==C.QEvent.Type.MouseButtonPress and event.button()==C.Qt.MouseButton.MiddleButton:
                self._preview_pan=event.globalPosition();self.preview_image.setCursor(C.Qt.CursorShape.ClosedHandCursor);return True
            if event.type()==C.QEvent.Type.MouseMove and getattr(self,'_preview_pan',None) is not None:
                delta=event.globalPosition()-self._preview_pan;self._preview_pan=event.globalPosition()
                for bar,change in [(self.preview_scroll.horizontalScrollBar(),delta.x()),(self.preview_scroll.verticalScrollBar(),delta.y())]:bar.setValue(bar.value()-round(change))
                return True
            if event.type()==C.QEvent.Type.MouseButtonRelease and event.button()==C.Qt.MouseButton.MiddleButton:
                self._preview_pan=None;self.preview_image.unsetCursor();return True
        if self._map_pick_active:
            if event.type()==C.QEvent.Type.KeyPress and event.key()==C.Qt.Key.Key_Escape:
                self.finish_map_limits_pick(False);return True
            if obj is self._pick_map_view.viewport():
                if event.type()==C.QEvent.Type.MouseButtonPress:
                    if event.button()==C.Qt.MouseButton.RightButton:self.finish_map_limits_pick(False)
                    elif event.button()==C.Qt.MouseButton.LeftButton:self.safe(lambda:self.map_limits_click(event.position()))
                    return True
                if event.type()==C.QEvent.Type.MouseButtonRelease:return True
                if event.type()==C.QEvent.Type.MouseMove and self._map_first_corner is not None:
                    point=self.map_limits_point(event.position())
                    if point is not None:self.update_map_limits_outline(point)
                    return True
        if obj is self.preview_image and event.type()==C.QEvent.Type.MouseButtonPress and self._picking_origin:
            self.safe(lambda:self.pick_origin_at(event.position()));return True
        if obj is self.preview_image and event.type()==C.QEvent.Type.Resize and self._preview_pixmap is not None:
            self.refresh_preview_image()
        if obj is self.preview_image and self.metadata_mouse(obj,event):return True
        return super().eventFilter(obj,event)

    def showEvent(self,event):
        super().showEvent(event)
        if self._first_open:
            self._first_open=False;C.QTimer.singleShot(0,self.showMaximized)

    def zoom_preview(self,factor,position=None):
        pix=self.preview_image.pixmap()
        if self._preview_pixmap is None or pix is None or pix.isNull():return
        scroll=self.preview_scroll;viewport=scroll.viewport()
        if position is None:position=C.QPointF(self.preview_image.mapFrom(viewport,viewport.rect().center()))
        pw,ph=pix.width()/pix.devicePixelRatio(),pix.height()/pix.devicePixelRatio()
        rect=self.preview_image.contentsRect();left=rect.x()+(rect.width()-pw)/2;top=rect.y()+(rect.height()-ph)/2
        fraction=((position.x()-left)/pw,(position.y()-top)/ph)
        anchor=self.preview_image.mapTo(viewport,position.toPoint())
        ratio=max(.1,min(8,pw/self._preview_pixmap.width()*factor))
        index=next((i for i in range(1,self.preview_zoom.count()) if abs(float(self.preview_zoom.itemData(i))-ratio)<.0001),-1)
        self.preview_zoom.blockSignals(True)
        if index<0:
            index=self.preview_zoom.findText('Custom',C.Qt.MatchFlag.MatchStartsWith)
            if index<0:self.preview_zoom.addItem('Custom',ratio);index=self.preview_zoom.count()-1
            self.preview_zoom.setItemText(index,f'Custom {ratio*100:.0f}%');self.preview_zoom.setItemData(index,ratio)
        self.preview_zoom.setCurrentIndex(index);self.preview_zoom.blockSignals(False)
        self.refresh_preview_image()
        def restore_anchor():
            shown=self.preview_image.pixmap();w=shown.width()/shown.devicePixelRatio();h=shown.height()/shown.devicePixelRatio()
            for bar,frac,extent,labelsize,offset in [(scroll.horizontalScrollBar(),fraction[0],w,self.preview_image.width(),anchor.x()),(scroll.verticalScrollBar(),fraction[1],h,self.preview_image.height(),anchor.y())]:
                bar.setValue(round(frac*extent+max(0,(labelsize-extent)/2)-offset))
        C.QTimer.singleShot(0,restore_anchor)

    def refresh_preview_image(self):
        if self._preview_pixmap is None:return
        fit=self.preview_zoom.currentIndex()==0
        self.preview_scroll.setWidgetResizable(fit)
        if fit:
            size=self.preview_scroll.viewport().size()
            pix=self._preview_pixmap.scaled(size,C.Qt.AspectRatioMode.KeepAspectRatio,C.Qt.TransformationMode.SmoothTransformation)
        else:
            ratio=float(self.preview_zoom.currentData() or 1)
            pix=self._preview_pixmap.scaled(self._preview_pixmap.size()*ratio,C.Qt.AspectRatioMode.KeepAspectRatio,C.Qt.TransformationMode.SmoothTransformation)
            self.preview_image.setMinimumSize(pix.size());self.preview_image.resize(pix.size())
        if fit:self.preview_image.setMinimumSize(100,100)
        self.preview_image.setPixmap(pix)

    def safe(self, fn):
        try: return fn()
        except Exception as exc: W.QMessageBox.warning(self, 'Plotting', str(exc))

    def choose_files(self):
        names, _ = W.QFileDialog.getOpenFileNames(self, 'Select plot data', str(Path.home()/'Desktop'), 'Plot data (*.csv *.xyz *.txt *.dxf *.ktd *.KTD *.hpktd *.ktdp);;All files (*)')
        errors = []
        for name in names:
            try: self.add_file(name)
            except Exception as exc: errors.append(f'{Path(name).name}: {exc}')
        if errors: W.QMessageBox.warning(self, 'Files not loaded', '\n'.join(errors))
        if names and self.datasets:
            if any(shapes for _,_,shapes in self.checked()):self.fit_files()
            else:self.render_plot()

    def add_file(self, name):
        source = CRS.from_user_input(self.crs.text().strip())
        transformer = Transformer.from_crs(source, 'EPSG:4326', always_xy=True)
        path = Path(name)
        if path.suffix.lower() in ('.csv', '.xyz', '.txt'):
            # Do not drop invalid columns and silently shift coordinates.
            import csv, re
            shapes = []
            with path.open(encoding='utf-8-sig') as handle:
                started = False
                for line_no, raw in enumerate(handle, 1):
                    raw = raw.strip()
                    if not raw or raw.startswith(('#',';')): continue
                    parts = next(csv.reader([raw])) if ',' in raw else re.split(r'[;\s]+', raw)
                    try:
                        x, y = float(parts[0]), float(parts[1])
                    except (ValueError, IndexError):
                        if not started and not shapes and any(c.isalpha() for c in raw):
                            started = True; continue
                        raise ValueError(f'Invalid X/Y at line {line_no}. Expected X,Y[,depth].')
                    started = True
                    label = ''
                    if len(parts) > 2 and parts[2].strip():
                        try:
                            depth = float(parts[2])
                            if not math.isfinite(depth): raise ValueError()
                            label = f'{depth:g}'
                        except ValueError: raise ValueError(f'Invalid depth at line {line_no}.')
                    shapes.append(self.modern.Shape('point', [(x,y)], label))
        else: shapes = self.modern.load_shapes(path)
        converted = [];omitted=0
        for shape in shapes:
            if not shape.points: continue
            xy = np.asarray(shape.points, dtype=float)
            lon, lat = transformer.transform(xy[:,0], xy[:,1], errcheck=False)
            points = np.column_stack((lon,lat))
            valid=np.isfinite(points).all(axis=1)&(abs(points[:,0])<=180)&(abs(points[:,1])<85)
            omitted+=int((~valid).sum())
            if shape.kind=='point' or len(points)==1:
                for point in points[valid]:converted.append(('point',point.reshape(1,2),shape.label))
            else:
                # Split at invalid vertices rather than joining across missing geometry.
                start=0
                for i in range(len(points)+1):
                    if i==len(points) or not valid[i]:
                        if i-start>=2:converted.append((shape.kind,points[start:i].copy(),shape.label))
                        start=i+1
        warning=(f'{path.name}: {omitted:,} coordinates cannot be plotted in the current CRS/Mercator range; file remains selected. Check its input CRS.' if omitted else '')
        if not converted and not warning:warning=f'{path.name}: no plottable coordinates; file remains selected.'
        self.data_warnings[str(path)]=warning
        self.datasets.append((str(path), source.to_string(), converted))
        item = W.QListWidgetItem(path.name); item.setToolTip(str(path)+'\nCRS: '+source.to_string()+('\n'+warning if warning else ''))
        if warning:item.setText(path.name+' [CRS / extent warning]')
        item.setFlags(item.flags() | C.Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(C.Qt.CheckState.Checked); self.files.addItem(item)

    def checked(self):
        return [data for i,data in enumerate(self.datasets) if self.files.item(i).checkState() == C.Qt.CheckState.Checked]

    def remove_files(self):
        for row in sorted((self.files.row(item) for item in self.files.selectedItems()), reverse=True):
            self.files.takeItem(row); self.datasets.pop(row)
        self.render_plot()

    def fit_files(self):
        points = [s[1] for _,_,shapes in self.checked() for s in shapes]
        if not points: raise ValueError('Check at least one data file first.')
        points = np.concatenate(points)
        low = points.min(axis=0); high = points.max(axis=0)
        if high[0]-low[0] > 180: raise ValueError('Areas crossing the antimeridian are not supported. Select a smaller area.')
        pad = np.maximum((high-low)*.05, .0002)
        for key,value in zip(('West','East','South','North'),(max(-180,low[0]-pad[0]),min(180,high[0]+pad[0]),max(-84.999,low[1]-pad[1]),min(84.999,high[1]+pad[1]))): self.bounds[key].setValue(float(value))
        self.render_plot()

    def add_int2_controls(self,layout):
        def group(title):
            box=W.QGroupBox(title);form=W.QFormLayout(box);layout.addWidget(box);return form
        def combo(form,key,label,items):
            w=W.QComboBox();w.addItems(items);form.addRow(label,w);self.options[key]=w;return w
        def spin(form,key,label,value,low,high,decimals=2):
            w=W.QDoubleSpinBox();w.setDecimals(decimals);w.setRange(low,high);w.setValue(value);form.addRow(label,w);self.options[key]=w;return w
        def check(form,key,label,value=False):
            w=W.QCheckBox(label);w.setChecked(value);form.addRow(w);self.options[key]=w;return w
        def line(form,key,label,text):
            w=W.QLineEdit(text);form.addRow(label,w);self.options[key]=w;return w
        f=group('Place plot sheet on screen')
        combo(f,'placement','Plot area method',['Geographic bounds / INT 2','Origin, dimensions and rotation'])
        line(f,'origin_crs','Origin / CAD CRS','EPSG:3395')
        spin(f,'origin_x','Origin X / easting (m)',0,-100000000,100000000,3)
        spin(f,'origin_y','Origin Y / northing (m)',0,-100000000,100000000,3)
        combo(f,'origin_anchor','Origin represents',['Lower-left corner','Centre of plot'])
        spin(f,'plot_width','Plot width on paper (mm)',150,5,1110,2)
        spin(f,'plot_height','Plot height on paper (mm)',120,5,1110,2)
        spin(f,'plot_scale','Plot scale 1 :',25000,1,50000000,0)
        spin(f,'rotation','Rotation CCW from east (°)',0,-180,180,2)
        spin(f,'ground_width','Extent width (m)',3750,.001,100000000,3)
        spin(f,'ground_height','Extent height (m)',3000,.001,100000000,3)
        self.options['ground_width'].valueChanged.connect(lambda value:self.set_ground_dimension('plot_width',value))
        self.options['ground_height'].valueChanged.connect(lambda value:self.set_ground_dimension('plot_height',value))
        apply_sheet=W.QPushButton('Apply origin / dimensions / rotation');apply_sheet.clicked.connect(lambda:self.safe(self.apply_placement));f.addRow(apply_sheet)
        self.extent_label=W.QLabel();self.extent_label.setWordWrap(True);f.addRow(self.extent_label)
        pick=W.QPushButton('Pick origin on preview');pick.clicked.connect(self.start_origin_pick);f.addRow(pick)
        overview=W.QPushButton('Show all data / choose a new origin');overview.clicked.connect(self.show_origin_overview);f.addRow(overview)
        self._picking_origin=False
        for key in ('plot_width','plot_height','plot_scale'):
            self.options[key].valueChanged.connect(self.update_extent_label)
        self.page.currentIndexChanged.connect(self.update_placement_limits)
        self.update_placement_limits()
        f=group('Sounding overlap control')
        check(f,'avoid_overlap','Prevent overlapping sounding labels',True)
        spin(f,'label_gap','Minimum label clearance (mm)',.6,0,10,2)
        f=group('Sheet, neatline and chart scale')
        combo(f,'kind','Sheet use',['Chart','Plan','Very small plan'])
        combo(f,'neatline','Neatline size',['Fit sheet','Custom millimetres'])
        spin(f,'neat_w','Neatline width mm',980,20,1189);spin(f,'neat_h','Neatline height mm',650,20,841)
        spin(f,'margin','Paper margin mm',20,5,100)
        check(f,'dimensions','Print neatline dimensions',True)
        check(f,'exceptional','Allow exceptional 1110 × 760 mm neatline')
        combo(f,'snap','Align bounds to subdivisions',['Keep entered bounds','Expand to 0.01 minute','Expand to 0.1 minute','Expand to 1 minute'])
        combo(f,'scale_mode','Scale',['Fit entered extents','Fixed scale at middle latitude'])
        spin(f,'scale','Scale denominator',25000,100,50000000,0)
        f=group('INT 2 A–D / E–M — borders and graduation')
        check(f,'dual_graduation','Dual graduations: UTM + latitude/longitude',True)
        combo(f,'utm_zone','UTM zone',[str(z) for z in range(1,61)]).setCurrentText('42')
        combo(f,'utm_hemisphere','UTM hemisphere',['North','South'])
        combo(f,'utm_display','UTM grid display',['Full grid','Border ticks only','Off'])
        spin(f,'utm_grid_lw','UTM grid line width (mm)',.18,.1,.8,2)
        combo(f,'utm_purpose','UTM grid role',['Secondary / reference grid','Primary / construction grid'])
        combo(f,'utm_spacing','UTM interval selection',['Automatic by scale (1000 m multiples)','Manual interval'])
        spin(f,'utm_interval','Manual UTM interval (m)',1000,1,1000000,0)
        combo(f,'utm_sides','UTM graduated sides',['Same as geographic','All four','Bottom and left','Top and right','None'])
        combo(f,'utm_colour','Secondary grid colour',['Magenta','Black (no primary grid)'])
        combo(f,'utm_numbering','UTM numbering',['Full easting / northing','Kilometres (omit 3 digits)'])
        combo(f,'dual_labels','Dual label density',['Avoid overlap automatically','Every numbered tick (manual)'])
        spin(f,'utm_tick_mm','UTM tick length (mm)',4,1,10,1)
        spin(f,'utm_tick_lw','UTM tick line width (mm)',.18,.1,.8,2)
        spin(f,'geo_font_pt','Latitude / longitude labels (pt)',10,8,24,1)
        spin(f,'utm_font_pt','UTM labels (pt)',10,8,24,1)
        spin(f,'utm_offset_mm','UTM label row from neatline (mm)',12.5,8,30,1)
        spin(f,'dual_gap_mm','Label clearance (mm)',1.5,.3,5,1)
        check(f,'coordinate_bold','Bold coordinate labels',True)
        check(f,'coordinate_halo','White halo behind coordinate labels',True)
        check(f,'utm_group_digits','Group UTM digits for readability',True)
        check(f,'utm_small_scale','Show grid below 1:100,000 (survey override)',True)
        standards_note=W.QLabel('IHO S-4 B-215 / INT 2: secondary UTM ticks are longer, normally magenta, at 1000 m or multiples. Chart grids are omitted below 1:100,000; the survey override is an explicit exception. Label size and row clearance are adjustable.')
        standards_note.setWordWrap(True);f.addRow(standards_note)
        def grid_role_defaults(*_):
            primary=self.opt('utm_purpose').startswith('Primary')
            self.options['utm_tick_mm'].setValue(2 if primary else 4)
            self.options['utm_colour'].setCurrentIndex(1 if primary else 0)
        self.options['utm_purpose'].currentIndexChanged.connect(grid_role_defaults)
        self.options['utm_interval'].setEnabled(False)
        self.options['utm_spacing'].currentIndexChanged.connect(lambda *_:self.options['utm_interval'].setEnabled(self.opt('utm_spacing').startswith('Manual')))
        owner=self.parent()
        if 'settings' in getattr(owner,'context',{}):
            geo=owner.context['settings'].geodetic
            zone=str(getattr(geo,'utm_zone','42'))
            if zone in [str(z) for z in range(1,61)]:self.options['utm_zone'].setCurrentText(zone)
            self.options['utm_hemisphere'].setCurrentText('South' if float(getattr(geo,'false_northing',0))==10000000 else 'North')
        combo(f,'pattern','Graduation pattern',['Automatic by scale']+[f'{k} — INT 2' for k in GRADUATION_STYLES]+['Custom intervals'])
        combo(f,'border','Border dimensions',['A — plain border','B — diced border','C / D — adjacent / inset','Custom']).setCurrentIndex(1)
        combo(f,'dicing','Dicing',['Automatic by pattern','On','Off'])
        combo(f,'sides','Graduated sides',['All four','Bottom and left','Top and right','None (small plans only)'])
        spin(f,'border_mm','Custom outer width mm',12.5,4,30)
        spin(f,'band_mm','Dicing band width mm',.8,.2,2)
        spin(f,'outer_lw','Outer line width mm',1,.1,2)
        spin(f,'neat_lw','Neatline width mm',.15,.05,1)
        combo(f,'corners','Border corners',['Mitred','Square'])
        spin(f,'long_tick','Long tick mm',3,.2,10);spin(f,'mid_tick','Intermediate tick mm',1.5,.2,10);spin(f,'short_tick','Minor tick mm',.8,.1,5)
        spin(f,'degree_int','Custom degree interval ′',60,.01,600)
        spin(f,'inter_int','Custom intermediate ′',5,0,300)
        spin(f,'minute_int','Custom minute interval ′',1,.01,300)
        spin(f,'minor_int','Custom minor interval ′',.1,.01,60)
        spin(f,'dice_int','Custom dicing interval ′',1,.01,300)
        combo(f,'minor_mode','Minor subdivisions',['Throughout border','Near numbered ticks only','Hundredths near centre','None'])
        combo(f,'lat_mode','Latitude spacing',['Same as longitude','Independent (high latitudes)'])
        spin(f,'lat_interval','Latitude numbering ′',1,.01,300)
        combo(f,'longitude_multiplier','High-latitude longitude ticks',['1 × latitude','2 × latitude','5 × latitude','10 × latitude'])
        f=group('B-212 / B-214 — coordinate lettering')
        combo(f,'numbering','Degree values',['Full at every label','INT 2 periodic / fold halves'])
        combo(f,'precision','Minute decimals',['Automatic','0','1','2'])
        combo(f,'decimal','Decimal separator',['Point','Comma'])
        check(f,'leading','Leading zero below 10 minutes',True)
        combo(f,'hemisphere','Hemisphere labels',['Each coordinate','Central E/W and N/S','Full Greenwich / Equator wording','None'])
        combo(f,'fold','Chart fold',['None','Vertical centre','Horizontal centre'])
        check(f,'corner_coords','Corner coordinates',True)
        combo(f,'corner_positions','Corner positions',['Southwest and northeast','All four corners'])
        combo(f,'corner_precision','Corner precision',['Automatic by scale','0.001 minute','0.01 minute','0.1 minute'])
        check(f,'legend','File legend',True)
        f=group('B-213 / N — graticule and subsidiary graduation')
        combo(f,'graticule','Graticule',['Automatic spacing (100–230 mm)','Off','Selected interval','Intersections only'])
        spin(f,'graticule_int','Selected interval ′',5,.01,600)
        check(f,'subsidiary','N — supplementary graduation')
        combo(f,'subsidiary_pos','Internal lines',['Automatic (≤450 mm apart)','Central meridian and parallel','Selected meridian and parallel'])
        spin(f,'sub_lon','Internal longitude °',67,-180,180,6);spin(f,'sub_lat','Internal latitude °',24.8,-84.9,84.9,6)
        f=group('S / V — rectangular grid')
        combo(f,'grid','Grid display',['Off','Border ticks','Full grid','Crosses'])
        combo(f,'grid_kind','Grid purpose',['Secondary / reference (magenta)','Primary / construction (black)'])
        line(f,'grid_crs','Projected grid CRS','EPSG:32642')
        spin(f,'grid_step','Grid interval metres',1000,1,1000000,0)
        combo(f,'grid_label','Grid numbering',['Full easting / northing','Abbreviated kilometres'])
        spin(f,'false_e','Label easting offset m',0,-10000000,10000000,0);spin(f,'false_n','Label northing offset m',0,-10000000,10000000,0)
        line(f,'grid_id','Square / zone annotation','')
        check(f,'grid_note','Show grid reference note',True)
        check(f,'grid_diagram','Square identification diagram (V)')
        check(f,'grid2','Second reference grid')
        line(f,'grid2_crs','Second grid CRS','EPSG:32643')
        spin(f,'grid2_step','Second grid interval m',1000,1,1000000,0)
        f=group('O–R / B-221 — linear scales')
        combo(f,'scale_bar','Linear scale',['Automatic by chart scale','Off','Border scale','Inset / short scale'])
        combo(f,'scale_style','Metre scale pattern',['Automatic','O — 100 / 20 m','P — 500 / 50 m','Q — 1000 / 100 m','R — 1000 / 200 m','Custom'])
        combo(f,'scale_units','Units',['Metres','Nautical miles','Cables'])
        spin(f,'scale_length','Target printed length mm',250,20,450)
        spin(f,'scale_major','Custom major interval m',1000,1,1000000,0);spin(f,'scale_minor','Custom subdivision m',100,1,100000,0)
        combo(f,'scale_pos','Position',['Bottom border','Top border','Left border','Right border'])
        check(f,'scale_opposite','Repeat in opposite border',True)
        combo(f,'scale_latitudes','Latitude reference',['Middle latitude','South and north latitude zones'])
        check(f,'scale_dice','Dice nautical-mile / cable scales')
        check(f,'extra_scale','Additional nautical-mile scale')
        f=group('C / D — inset and adjacent plans')
        self._plans=[]
        button=W.QPushButton('Add / edit plan areas…');button.clicked.connect(self.edit_plans);f.addRow(button)
        self.plan_count=W.QLabel('No additional plans');f.addRow(self.plan_count)
        f=group('Print and export quality')
        combo(f,'dpi','PNG resolution',['150 dpi','300 dpi','600 dpi'])
        check(f,'show_scale','Show numeric chart scale',True)
        button=W.QPushButton('INT 2 options and coverage…');button.clicked.connect(self.show_int2_help);f.addRow(button)

    def opt(self,key):
        w=self.options[key]
        if isinstance(w,W.QComboBox):return w.currentText()
        if isinstance(w,W.QCheckBox):return w.isChecked()
        if isinstance(w,W.QLineEdit):return w.text().strip()
        return w.value()

    def show_int2_help(self):
        text=('INT 2, edition 5 (2025) — selectable controls\n\n'
              'A/B: plain/diced borders, physical line and tick dimensions. C/D: adjacent/inset plans with compact borders. '
              'E–M: scale-dependent degree, intermediate, minute, minor and dicing intervals. N: internal supplementary graduation. '
              'O–R: metric scale patterns. S/V: projected rectangular grids and reference annotation. T/U: tick hierarchy and mitred corners.\n\n'
              'Numbering includes full or periodic degrees, fold-half degree repetition, decimal minutes, leading zeros, '
              'hemisphere notation and corner positions. Graticule spacing and numbering are checked in physical millimetres. '
              'Scale bars use WGS84 Mercator scale at the middle latitude. Sheet sizes A4–A0 have exact ISO dimensions.\n\n'
              'Plot projection: WGS84 Mercator only, latitudes between −85° and +85°, no antimeridian crossing. '
              'Other projection-specific curved neatlines and polar chart design are not implemented. '
              'Grid reference square identifiers are entered by the user. Inset CAD geometry is placed at its sheet location, '
              'while the main map retains EPSG:3395 coordinates. National variations require review against INT 2 / S-4. '
              'Selecting an option does not establish full chart-production compliance.\n\n'+REFERENCE)
        dialog=W.QDialog(self);dialog.setWindowTitle('INT 2 control coverage');dialog.resize(760,560)
        layout=W.QVBoxLayout(dialog);view=W.QTextBrowser();view.setPlainText(text);layout.addWidget(view)
        button=W.QPushButton('Close');button.clicked.connect(dialog.accept);layout.addWidget(button);dialog.exec()

    def edit_plans(self):
        dialog=W.QDialog(self);dialog.setWindowTitle('Inset / adjacent plans — positions in sheet millimetres');dialog.resize(1200,550)
        layout=W.QVBoxLayout(dialog)
        layout.addWidget(W.QLabel('Each plan uses checked data files. X/Y locate its neatline from the bottom-left of the sheet. Allow at least 8.1 mm around each plan border.'))
        table=W.QTableWidget(0,9);table.setHorizontalHeaderLabels(['Name','West °','East °','South °','North °','X mm','Y mm','Width mm','Height mm']);layout.addWidget(table)
        def row(values):
            i=table.rowCount();table.insertRow(i)
            for j,value in enumerate(values):table.setItem(i,j,W.QTableWidgetItem(str(value)))
        for plan in self._plans:row(plan)
        buttons=W.QHBoxLayout();layout.addLayout(buttons)
        add=W.QPushButton('Add plan');buttons.addWidget(add)
        add.clicked.connect(lambda:row(['Plan '+str(table.rowCount()+1),*[self.bounds[k].value() for k in ('West','East','South','North')],40,40,100,80]))
        remove=W.QPushButton('Remove selected');buttons.addWidget(remove);remove.clicked.connect(lambda:table.removeRow(table.currentRow()))
        box=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel);layout.addWidget(box);box.rejected.connect(dialog.reject)
        def accept():
            try:
                plans=[]
                for i in range(table.rowCount()):
                    values=[table.item(i,j).text() if table.item(i,j) else '' for j in range(9)]
                    plan=[values[0]]+[float(v) for v in values[1:]]
                    if not -180<=plan[1]<plan[2]<=180 or plan[2]-plan[1]>180 or not -85<plan[3]<plan[4]<85:raise ValueError('Invalid bounds in '+plan[0])
                    if min(plan[5:])<=0:raise ValueError('Plan positions and sizes must be positive.')
                    plans.append(plan)
                if len(plans)>8:raise ValueError('At most eight additional plans per sheet.')
                self._plans=plans;self.plan_count.setText(f'{len(plans)} additional plan(s)');dialog.accept();self.update_preview()
            except ValueError as exc:W.QMessageBox.warning(dialog,'Plan area',str(exc))
        box.accepted.connect(accept);dialog.exec()

    def sheet_mm(self):
        name,orientation=self.page.currentText().split();w,h=SHEETS[name]
        return (w,h) if orientation=='landscape' else (h,w)

    def render_plot(self):
        if self.opt('placement')=='Origin, dimensions and rotation':return self.render_placed_plot()
        self.figure._cad_to_world=None;self.figure._cad_crs='EPSG:3395'
        self._pick_to_world=lambda x,y:(x,y)
        self._pick_crs='EPSG:3395'
        from pyproj import Proj
        self.preview_timer.stop()
        west,east,south,north=(self.bounds[k].value() for k in ('West','East','South','North'))
        if not -180<=west<east<=180 or east-west>180 or not -85<south<north<85:raise ValueError('Enter ordered W/E/S/N bounds within ±85° latitude and 180° width; antimeridian crossing is unsupported.')
        self._warnings=[self.data_warnings[path] for path,_,_ in self.checked() if self.data_warnings.get(path)];self._sounding_texts=[];self._border_texts=[];self._int2_texts=[];self._map_axes=[]
        sw,sh=self.sheet_mm();self.figure.set_size_inches(sw/25.4,sh/25.4)
        # Fixed physical sheet, bounded preview raster memory; vector export stays full precision.
        self.figure.set_dpi(min(144,4400/(max(sw,sh)/25.4)))
        self.figure.clear()
        self.projection=Transformer.from_crs('EPSG:4326',MERCATOR,always_xy=True)
        x0,y0=self.projection.transform(west,south);x1,y1=self.projection.transform(east,north)
        snap=self.opt('snap')
        if snap!='Keep entered bounds':
            step=float(snap.split()[2])/60
            west=math.floor(west/step)*step;east=math.ceil(east/step)*step;south=math.floor(south/step)*step;north=math.ceil(north/step)*step
            x0,y0=self.projection.transform(west,south);x1,y1=self.projection.transform(east,north)
        margin=max(self.opt('margin')+14,self.dual_border_width(12.5)+6)
        available=(sw-2*margin,sh-2*margin-22)
        if min(available)<20:raise ValueError('Margins leave no usable plot area.')
        max_long,max_short=(1110,760) if self.opt('exceptional') else (1100,750)
        maximum=(max_long,max_short) if sw>=sh else (max_short,max_long)
        available=(min(available[0],maximum[0]),min(available[1],maximum[1]))
        width,height=available if self.opt('neatline')=='Fit sheet' else (self.opt('neat_w'),self.opt('neat_h'))
        if width>available[0] or height>available[1]:raise ValueError('Custom neatline plus margins does not fit the selected sheet.')
        if self.opt('scale_mode').startswith('Fixed'):
            cx,cy=(x0+x1)/2,(y0+y1)/2;mid=(south+north)/2
            k=Proj(MERCATOR).get_factors((west+east)/2,mid).parallel_scale
            dx=width/1000*self.opt('scale')*k;dy=height/1000*self.opt('scale')*k
            x0,x1=cx-dx/2,cx+dx/2;y0,y1=cy-dy/2,cy+dy/2
            inverse=Transformer.from_crs(MERCATOR,'EPSG:4326',always_xy=True)
            west,south=inverse.transform(x0,y0);east,north=inverse.transform(x1,y1)
            if not -180<=west<east<=180 or not -85<south<north<85:raise ValueError('Fixed scale expands outside the supported world extent.')
        else:
            factor=min(width/(x1-x0),height/(y1-y0));width=(x1-x0)*factor;height=(y1-y0)*factor
        left=(sw-width)/2;bottom=(sh-height)/2-2
        self.ax=self.figure.add_axes([left/sw,bottom/sh,width/sw,height/sh])
        self.figure.text(.5,1-12/sh,self.title.text(),ha='center',va='top',fontsize=15)
        self.figure.text(.5,10/sh,'',ha='center',va='bottom',fontsize=7)
        count,scale,pattern=self.draw_chart(self.ax,(west,east,south,north),width,height,False)
        for plan in self._plans:
            name,pw,pe,ps,pn,px,py,ww,hh=plan
            if px<9 or py<9 or px+ww+9>sw or py+hh+9>sh:raise ValueError(f'{name}: plan and compact border do not fit the sheet.')
            inside=(px-9>=left and py-9>=bottom and px+ww+9<=left+width and py+hh+9<=bottom+height)
            outside=(px+ww+9<=left-14 or px-9>=left+width+14 or py+hh+9<=bottom-14 or py-9>=bottom+height+14)
            if not inside and not outside:raise ValueError(f'{name}: the plan crosses the main border. Move it fully inside the neatline or fully outside the outer frame.')
            ax=self.figure.add_axes([px/sw,py/sh,ww/sw,hh/sh],zorder=10)
            # Keep Mercator isotropic and centre the neatline inside the selected plan box.
            ax.set_aspect('equal',adjustable='box')
            self.draw_chart(ax,(pw,pe,ps,pn),ww,hh,True)
            self.chart_text(ax,ax.get_xlim()[0]+4*(ax.get_xlim()[1]-ax.get_xlim()[0])/ww,ax.get_ylim()[1]-4*(ax.get_ylim()[1]-ax.get_ylim()[0])/hh,name,ha='left',va='top',bbox=dict(facecolor='white',edgecolor='none',pad=1))
        footer=f'WGS84 / Mercator • {self.page.currentText()} • INT 2 pattern {pattern}'
        if self.opt('show_scale'):footer+=f' • 1:{scale:,.0f} at {(south+north)/2:.5f}°'
        self.figure.texts[1].set_text(footer)
        if self.opt('fold')!='None':
            from matplotlib.lines import Line2D
            vertical=self.opt('fold').startswith('Vertical')
            for endpoint in (0,1):
                xs=[.5,.5] if vertical else [endpoint,endpoint+(.007 if endpoint==0 else -.007)]
                ys=[endpoint,endpoint+(.01 if endpoint==0 else -.01)] if vertical else [.5,.5]
                self.figure.add_artist(Line2D(xs,ys,transform=self.figure.transFigure,color='.5',lw=.4))
        self.canvas.draw();font_note=self.apply_lettering();self.canvas.draw()
        image=G.QImage(self.canvas.buffer_rgba(),*self.canvas.get_width_height(),G.QImage.Format.Format_RGBA8888).copy()
        self._preview_pixmap=G.QPixmap.fromImage(image);self.refresh_preview_image()
        self.status.setText(f'{count:,} vertices • {sw:g} × {sh:g} mm • 1:{scale:,.0f} • pattern {pattern}.'+font_note+('\n'+'\n'.join(dict.fromkeys(self._warnings)) if self._warnings else ''))
        return scale

    def chart_text(self,ax,x,y,text,kind='detail',**kw):
        item=ax.text(x,y,text,fontsize=self.font_size.value(),clip_on=False,**kw)
        if kind=='sounding':self._sounding_texts.append(item)
        elif kind in ('border','utm-border','geographic-grid'):self._border_texts.append(item)
        else:self._int2_texts.append(item)
        if kind in ('utm-border','geographic-grid') or kind=='border' and self.opt('dual_graduation'):
            item._graduation_font=self.opt('utm_font_pt' if kind=='utm-border' else 'geo_font_pt')
            item.set_zorder(20)
        item.set_gid(kind)
        if kind=='sounding':item._sounding_anchor=(x,y)
        return item

    def draw_chart(self,ax,bounds,width,height,inset):
        from pyproj import Proj
        west,east,south,north=bounds;x0,y0=self.projection.transform(west,south);x1,y1=self.projection.transform(east,north)
        ax.set_xlim(x0,x1);ax.set_ylim(y0,y1);ax.set_facecolor('#f6fbfd');ax.set_xticks([]);ax.set_yticks([])
        ax.set_aspect('equal',adjustable='box');self.canvas.draw()
        width=ax.bbox.width/self.figure.dpi*25.4;height=ax.bbox.height/self.figure.dpi*25.4
        scale=(x1-x0)/(width/1000)/Proj(MERCATOR).get_factors((west+east)/2,(south+north)/2).parallel_scale
        pattern=graduation_style(scale) if self.opt('pattern').startswith('Automatic') else self.opt('pattern')[0]
        if pattern=='C':profile=tuple(self.opt(k) for k in ('degree_int','inter_int','minute_int','minor_int','dice_int'));pattern='Custom'
        else:
            profile=GRADUATION_STYLES[pattern]
            if pattern=='F' and scale<50000:profile=(60,2,.5,.1,1)
        degree,inter,minute,minor,dicing=profile
        if self.opt('dicing')=='Off' or self.opt('border').startswith('A'):dicing=0
        elif self.opt('dicing')=='On' and not dicing:dicing=minute
        # E remains plain in Automatic; F–M use B when border preset is B.
        outer=(8.1 if dicing else 7.5) if inset or self.opt('border').startswith('C') else self.opt('border_mm') if self.opt('border')=='Custom' else 12.5
        outer=self.dual_border_width(outer)
        inner=(5.3 if dicing else 6.5) if inset or self.opt('border').startswith('C') else outer-self.opt('outer_lw')
        if inner<3:raise ValueError('Outer border is too narrow for its line width.')
        units_x=(x1-x0)/width;units_y=(y1-y0)/height
        geometry=dict(bounds=bounds,x0=x0,x1=x1,y0=y0,y1=y1,width=width,height=height,ux=units_x,uy=units_y,outer=outer,inner=inner,scale=scale,profile=profile,inset=inset)
        self._map_axes.append((ax,geometry))
        for spine in ax.spines.values():spine.set_linewidth(self.opt('neat_lw')*72/25.4)
        from matplotlib.patches import Rectangle
        if inset:
            for rx,ry,rw,rh in [(x0-inner*units_x,y0-inner*units_y,x1-x0+2*inner*units_x,inner*units_y),
                               (x0-inner*units_x,y1,x1-x0+2*inner*units_x,inner*units_y),
                               (x0-inner*units_x,y0,inner*units_x,y1-y0),
                               (x1,y0,inner*units_x,y1-y0)]:
                ax.add_patch(Rectangle((rx,ry),rw,rh,facecolor='white',edgecolor='none',clip_on=False,zorder=-1))
        ax.add_patch(Rectangle((x0-inner*units_x,y0-inner*units_y),(x1-x0)+2*inner*units_x,(y1-y0)+2*inner*units_y,fill=False,lw=self.opt('outer_lw')*72/25.4,clip_on=False,zorder=3,joinstyle='miter' if self.opt('corners')=='Mitred' else 'bevel'))
        if self.opt('corners')=='Mitred':
            for x,dx in ((x0,-1),(x1,1)):
                for y,dy in ((y0,-1),(y1,1)):ax.plot([x,x+dx*inner*units_x],[y,y+dy*inner*units_y],color='black',lw=.3,clip_on=False)
        count=0
        for index,(path,crs,shapes) in enumerate(self.checked()):
            color=('#126a91','#c24b31','#338443','#8652a3','#956116')[index%5];px=[];py=[];legend_used=False
            for kind,points,label in shapes:
                x,y=self.projection.transform(points[:,0],points[:,1]);count+=len(points)
                if self.draw_map_feature(ax,kind,points,label,x,y):continue
                if kind=='point' or len(points)==1:
                    inside=(np.asarray(x)>=x0)&(np.asarray(x)<=x1)&(np.asarray(y)>=y0)&(np.asarray(y)<=y1)
                    px.extend(np.asarray(x)[inside]);py.extend(np.asarray(y)[inside])
                    if self.labels.isChecked() and label and inside[0] and len(self._sounding_texts)<2000:self.chart_text(ax,x[0],y[0],label,'sounding',ha='center',va='bottom')
                else:
                    ax.plot(x,y,color=color,lw=.6,label=Path(path).name if not legend_used else None);legend_used=True
            if px:ax.scatter(px,py,s=2,color=color,label=Path(path).name if not legend_used else None)
        if self.opt('legend') and not inset and ax.get_legend_handles_labels()[0]:ax.legend(loc='upper left',fontsize=6,framealpha=.9)
        options=(.01,.02,.05,.1,.2)+INTERVALS if max(east-west,north-south)*60<2 else INTERVALS
        required=max((east-west)*60*25/width,(north-south)*60*25/height)
        numbering=self.graduation.currentData() or next((v for v in options if v>=required),options[-1])
        lat_numbering=self.opt('lat_interval') if self.opt('lat_mode').startswith('Independent') else numbering
        if min(width*numbering/((east-west)*60),height*lat_numbering/((north-south)*60))<20:self._warnings.append('Numbered ticks are less than 20 mm apart; increase numbering interval.')
        if pattern=='M' and scale<4750000:self._warnings.append('Pattern M is a very-small-scale example; review this override.')
        self.draw_graduation(ax,geometry,numbering,lat_numbering,dicing)
        self.draw_graticule(ax,geometry,numbering,lat_numbering)
        self.draw_utm_graduation(ax,geometry,CRS.from_user_input(MERCATOR))
        self.draw_rectangular_grid(ax,geometry)
        self.draw_linear_scale(ax,geometry)
        if self.opt('corner_coords'):
            corners=[(x0,y0,west,south,'left','top'),(x1,y1,east,north,'right','bottom')]
            if self.opt('corner_positions')=='All four corners':corners += [(x1,y0,east,south,'right','top'),(x0,y1,west,north,'left','bottom')]
            for x,y,lon,lat,ha,va in corners:
                precision=self.opt('corner_precision')
                step=(.001 if scale<=10000 else .1 if scale>=500000 else .01) if precision.startswith('Automatic') else float(precision.split()[0])
                lon=(math.floor(lon*60/step) if x==x0 else math.ceil(lon*60/step))*step/60
                lat=(math.floor(lat*60/step) if y==y0 else math.ceil(lat*60/step))*step/60
                decimals=int(round(-math.log10(step)))
                def corner_label(value,longitude):
                    total=round(abs(value)*60,decimals);deg=int(total//60);minute=total-deg*60
                    value_text=f'{deg}°{minute:0{3+decimals}.{decimals}f}′'+(('E' if value>=0 else 'W') if longitude else ('N' if value>=0 else 'S'))
                    return value_text.replace('.',',') if self.opt('decimal')=='Comma' else value_text
                text=corner_label(lat,False)+' / '+corner_label(lon,True)
                self.chart_text(ax,x,y+(-1 if va=='top' else 1)*(outer+(8 if self.opt('grid2') and self.opt('grid')!='Off' else 2))*units_y,text,ha=ha,va=va)
        if self.opt('dimensions'):
            text=f'({width:.1f} × {height:.1f} mm)'
            if self.opt('decimal')=='Comma':text=text.replace('.',',')
            self.chart_text(ax,x1,y0-(outer+(13 if self.opt('corner_positions')=='All four corners' or self.opt('grid2') and self.opt('grid')!='Off' else 7))*units_y,text,ha='right',va='top')
        return count,scale,pattern
    def border_label(self,value,interval,longitude,full=True):
        precision=self.opt('precision')
        decimals=(2 if interval<.1 else 1 if interval<1 else 0) if precision=='Automatic' else int(precision)
        total=round(abs(value)*60,decimals);degrees=int(total//60);minutes=total-degrees*60
        if minutes>=60:degrees+=1;minutes=0
        width=(2 if self.opt('leading') else 1)+(decimals+1 if decimals else 0)
        minute=f'{minutes:0{width}.{decimals}f}' if self.opt('leading') else f'{minutes:.{decimals}f}'
        if self.opt('decimal')=='Comma':minute=minute.replace('.',',')
        result=(str(degrees)+'°' if full else '')+minute+'′'
        if self.opt('hemisphere')=='Each coordinate':result+=('E' if value>=0 else 'W') if longitude else ('N' if value>=0 else 'S')
        return result

    def draw_graduation(self,ax,g,numbering,lat_numbering,dicing):
        from matplotlib.patches import Rectangle
        west,east,south,north=g['bounds'];x0,x1,y0,y1=(g[k] for k in ('x0','x1','y0','y1'));ux,uy=g['ux'],g['uy']
        degree,inter,minute,minor,_=g['profile'];sides=self.opt('sides')
        enabled={'bottom','left'} if sides.startswith('Bottom') else {'top','right'} if sides.startswith('Top') else set() if sides.startswith('None') else {'bottom','top','left','right'}
        if not enabled and self.opt('kind')!='Very small plan':self._warnings.append('Ungraded borders are an exception for very small plans, not a chart default.')
        if len(enabled)==2 and self.opt('kind')=='Chart':self._warnings.append('Two-sided graduation is intended for plans.')
        offset=2 if dicing else 0
        for longitude,lo,hi,num in [(True,west,east,numbering),(False,south,north,lat_numbering)]:
            values=tick_values(lo,hi,num)
            full_indices=set(range(len(values))) if self.opt('numbering').startswith('Full') else {i for i,v in enumerate(values) if abs(v-round(v))<1e-8}
            if len(values):
                # A full degree reference in both halves also covers degree-near-corner cases.
                full_indices.add(0);full_indices.add(len(values)//2)
            label_positions=[]
            for i,value in enumerate(values):
                pos=self.projection.transform(value,south)[0] if longitude else self.projection.transform(west,value)[1]
                label_positions.append(pos)
                for side in (('bottom','top') if longitude else ('left','right')):
                    if side not in enabled:continue
                    direction=-1 if side in ('bottom','left') else 1
                    coordinate=(y0 if side=='bottom' else y1) if longitude else (x0 if side=='left' else x1)
                    text=self.border_label(value,num,longitude,i in full_indices)
                    if longitude:self.chart_text(ax,pos,coordinate+direction*(offset+4.4)*uy,text,'border',ha='center',va='center')
                    else:self.chart_text(ax,coordinate+direction*(offset+4.4)*ux,pos,text,'border',ha='center',va='center',rotation=90)
            multiplier=int(self.opt('longitude_multiplier').split()[0]) if longitude else 1
            if multiplier!=1 and max(abs(south),abs(north))<=70:self._warnings.append('Different longitude tick intervals are intended for latitudes beyond 70°. Dicing remains equal.')
            levels=[(degree*multiplier,g['inner']-offset),(inter*multiplier,self.opt('long_tick')),(minute*multiplier,self.opt('mid_tick'))]
            minor_mode=self.opt('minor_mode')
            if minor_mode!='None':levels.append((minor*multiplier,self.opt('short_tick')))
            ticks={}
            for interval,length in levels:
                if not interval:continue
                if (hi-lo)*60/interval>4000:
                    self._warnings.append('A graduation level exceeds 4,000 ticks and was omitted; choose a coarser scale pattern.');continue
                for value in tick_values(lo,hi,interval):
                    if interval==minor*multiplier and minor_mode=='Near numbered ticks only':
                        nearest=round(value*60/num)*num/60
                        direction=1 if value>=0 else -1
                        if not 0<=direction*(value-nearest)*60<=minute:continue
                    key=round(float(value),10);ticks[key]=max(ticks.get(key,0),length)
            if minor_mode=='Hundredths near centre':
                centre=round((lo+hi)*30*10)/10/60
                for value in tick_values(max(lo,centre),min(hi,centre+.1/60),.01):ticks[round(float(value),10)]=self.opt('short_tick')
            for side in (('bottom','top') if longitude else ('left','right')):
                if side not in enabled:continue
                direction=-1 if side in ('bottom','left') else 1
                coordinate=(y0 if side=='bottom' else y1) if longitude else (x0 if side=='left' else x1)
                for value,length in ticks.items():
                    pos=self.projection.transform(value,south)[0] if longitude else self.projection.transform(west,value)[1]
                    if longitude:xs=[pos,pos];ys=[coordinate+direction*offset*uy,coordinate+direction*(offset+length)*uy]
                    else:xs=[coordinate+direction*offset*ux,coordinate+direction*(offset+length)*ux];ys=[pos,pos]
                    ax.plot(xs,ys,color='black',lw=.35,clip_on=False,zorder=3)
                if dicing:
                    values_dice=np.r_[lo,tick_values(lo,hi,dicing),hi];values_dice=np.unique(values_dice)
                    for a,b in zip(values_dice,values_dice[1:]):
                        p0=self.projection.transform(a,south)[0] if longitude else self.projection.transform(west,a)[1]
                        p1=self.projection.transform(b,south)[0] if longitude else self.projection.transform(west,b)[1]
                        fill='black' if int(math.floor(((a+b)/2)*60/dicing))%2==0 else 'white'
                        band=self.opt('band_mm')
                        if longitude:r=Rectangle((p0,coordinate+min(0,direction*band*uy)),p1-p0,band*uy)
                        else:r=Rectangle((coordinate+min(0,direction*band*ux),p0),band*ux,p1-p0)
                        r.set_facecolor(fill);r.set_edgecolor('black');r.set_linewidth(.3);r.set_clip_on(False);ax.add_patch(r)
        hemi=self.opt('hemisphere')
        if hemi.startswith('Central') or hemi.startswith('Full'):
            lon=(west+east)/2;lat=(south+north)/2
            ew='East' if lon>=0 else 'West';ns='North' if lat>=0 else 'South'
            label='Longitude '+ew+' from Greenwich' if hemi.startswith('Full') else ew[0]
            label_lat='Latitude '+ns+' from Equator' if hemi.startswith('Full') else ns[0]
            self.chart_text(ax,(x0+x1)/2,y0-(g['inner']-1.5)*uy,label,ha='center',va='center')
            self.chart_text(ax,x0-(g['inner']-1.5)*ux,(y0+y1)/2,label_lat,ha='center',va='center',rotation=90)

    def draw_graticule(self,ax,g,numbering,lat_numbering):
        west,east,south,north=g['bounds'];x0,x1,y0,y1=(g[k] for k in ('x0','x1','y0','y1'))
        mode=self.opt('graticule')
        if mode!='Off':
            if mode.startswith('Automatic'):
                required=max((east-west)*60*100/g['width'],(north-south)*60*100/g['height'])
                interval=next((v for v in (.01,.02,.05,.1,.2)+INTERVALS if v>=required),300)
            else:interval=self.opt('graticule_int')
            lon=tick_values(west,east,interval);lat=tick_values(south,north,interval)
            if not len(lon):lon=np.array([(west+east)/2])
            if not len(lat):lat=np.array([(south+north)/2])
            xx=self.projection.transform(lon,np.full(len(lon),south))[0];yy=self.projection.transform(np.full(len(lat),west),lat)[1]
            spacing_x=g['width']*interval/((east-west)*60);spacing_y=g['height']*interval/((north-south)*60)
            if len(xx)>1 and not 100<=spacing_x<=230 or len(yy)>1 and not 100<=spacing_y<=230:self._warnings.append('Graticule spacing is outside 100–230 mm; review interval or sheet extents.')
            if mode=='Intersections only':
                if len(xx)*len(yy)>5000:raise ValueError('Too many graticule intersections.')
                for x in xx:
                    for y in yy:ax.plot([x-1*g['ux'],x+1*g['ux']],[y,y],color='.6',lw=.3);ax.plot([x,x],[y-g['uy'],y+g['uy']],color='.6',lw=.3)
            else:
                for x in xx:ax.axvline(x,color='.6',lw=.3,zorder=0)
                for y in yy:ax.axhline(y,color='.6',lw=.3,zorder=0)
            # A graticule line not already numbered receives an explicit coordinate.
            for longitude,values,positions,number in ((True,lon,xx,numbering),(False,lat,yy,lat_numbering)):
                for value,pos in zip(values,positions):
                    if abs(value*60/number-round(value*60/number))<1e-7:continue
                    label=self.border_label(value,interval,longitude,True)
                    if longitude:self.chart_text(ax,pos,y1+7.8*g['uy'],label,'border',ha='center',va='center')
                    else:self.chart_text(ax,x1+7.8*g['ux'],pos,label,'border',ha='center',va='center',rotation=90)
        if self.opt('subsidiary'):
            mode=self.opt('subsidiary_pos')
            if mode.startswith('Automatic'):
                inv=Transformer.from_crs(MERCATOR,'EPSG:4326',always_xy=True)
                xs=np.linspace(x0,x1,max(2,math.ceil(g['width']/450))+1)[1:-1]
                ys=np.linspace(y0,y1,max(2,math.ceil(g['height']/450))+1)[1:-1]
                longitudes=[inv.transform(x,y0)[0] for x in xs];latitudes=[inv.transform(x0,y)[1] for y in ys]
            elif mode.startswith('Central'):longitudes=[(west+east)/2];latitudes=[(south+north)/2]
            else:longitudes=[self.opt('sub_lon')];latitudes=[self.opt('sub_lat')]
            minute=g['profile'][2]
            for lon in longitudes:
                if not west<lon<east:self._warnings.append('Internal meridian is outside the plot.');continue
                x=self.projection.transform(lon,south)[0];ax.axvline(x,color='.25',lw=.4)
                for value in tick_values(south,north,minute):
                    py=self.projection.transform(west,value)[1];ax.plot([x,x+g['ux']],[py,py],color='black',lw=.4)
                for value in tick_values(south,north,lat_numbering):self.chart_text(ax,x+2*g['ux'],self.projection.transform(west,value)[1],self.border_label(value,lat_numbering,False),'geographic-grid',ha='left',va='center')
            for lat in latitudes:
                if not south<lat<north:self._warnings.append('Internal parallel is outside the plot.');continue
                y=self.projection.transform(west,lat)[1];ax.axhline(y,color='.25',lw=.4)
                for value in tick_values(west,east,minute):
                    px=self.projection.transform(value,south)[0];ax.plot([px,px],[y,y+g['uy']],color='black',lw=.4)
                for value in tick_values(west,east,numbering):self.chart_text(ax,self.projection.transform(value,south)[0],y+2*g['uy'],self.border_label(value,numbering,True),'geographic-grid',ha='center',va='bottom')
        elif max(g['width'],g['height'])>450:self._warnings.append('This sheet exceeds 450 mm; enable supplementary internal graduation.')

    def dual_border_width(self,outer):
        if not self.opt('dual_graduation'):return outer
        offset=max(self.opt('utm_offset_mm'),6.4+(self.opt('geo_font_pt')+self.opt('utm_font_pt'))*.352778/2+self.opt('dual_gap_mm'))
        return max(outer,offset+self.opt('utm_font_pt')*.352778/2+2)

    def fit_dual_labels(self,renderer):
        fixed=[text for text in self._border_texts if hasattr(text,'_graduation_font')]
        from matplotlib import patheffects
        for text in fixed:
            text.set_fontsize(text._graduation_font);text.set_visible(True)
            text.set_fontweight('bold' if self.opt('coordinate_bold') else 'normal')
            text.set_fontstyle('normal')
            text.set_path_effects([patheffects.withStroke(linewidth=2,foreground='white')] if self.opt('coordinate_halo') else [])
        occupied={};hidden=0;overlaps=0
        for text in fixed:
            box=text.get_window_extent(renderer)
            gap=self.opt('dual_gap_mm')/25.4*self.figure.dpi
            from matplotlib.transforms import Bbox
            padded=Bbox.from_extents(box.x0-gap/2,box.y0-gap/2,box.x1+gap/2,box.y1+gap/2)
            key=text.axes
            blocked=any(padded.overlaps(other) for other in occupied.get(key,()))
            if blocked:overlaps+=1
            if blocked and self.opt('dual_labels').startswith('Avoid'):
                text.set_visible(False);hidden+=1
            else:occupied.setdefault(key,[]).append(padded)
        if overlaps and not self.opt('dual_labels').startswith('Avoid'):self._warnings.append('Selected manual graduation labels overlap; increase interval, row offset or plot size.')
        if hidden:self._warnings.append(f'{hidden} crowded graduation labels omitted; ticks remain visible. Increase the numbered interval or plot size to show more labels.')

    def draw_utm_graduation(self,ax,g,sheet_crs,world=None,local=None):
        """Intersect UTM coordinate lines with the actual (possibly rotated) sheet."""
        if not self.opt('dual_graduation') or self.opt('utm_display')=='Off':return
        from shapely.geometry import LineString,box
        zone=int(self.opt('utm_zone'))
        utm=CRS.from_epsg((32700 if self.opt('utm_hemisphere')=='South' else 32600)+zone)
        primary=self.opt('utm_purpose').startswith('Primary')
        if primary and not sheet_crs.equals(utm):raise ValueError('Primary UTM grid must match the plot projection. Use Secondary for a Mercator plot, or select the matching UTM Origin / CAD CRS.')
        forward=Transformer.from_crs(sheet_crs,utm,always_xy=True)
        inverse=Transformer.from_crs(utm,sheet_crs,always_xy=True)
        x0,x1,y0,y1=(g[k] for k in ('x0','x1','y0','y1'))
        xx=np.r_[np.linspace(x0,x1,100),np.full(100,x1),np.linspace(x1,x0,100),np.full(100,x0)]
        yy=np.r_[np.full(100,y0),np.linspace(y0,y1,100),np.full(100,y1),np.linspace(y1,y0,100)]
        wx,wy=world(xx,yy) if world else (xx,yy)
        ee,nn=forward.transform(wx,wy,errcheck=True)
        if not np.isfinite(ee).all() or not np.isfinite(nn).all():raise ValueError('Selected UTM zone cannot cover this plot.')
        scale=float(g.get('scale',max((x1-x0)/g['width'],(y1-y0)/g['height'])*1000))
        if scale>100000 and self.opt('utm_small_scale'):self._warnings.append('Survey override: UTM grid shown below 1:100,000.')
        if scale>100000 and not self.opt('utm_small_scale'):
            self._warnings.append('UTM grid omitted below 1:100,000 per IHO S-4 B-215. Enable the survey override to show it.')
            return
        if self.opt('utm_spacing').startswith('Automatic'):
            ground_per_mm=max((max(ee)-min(ee))/g['width'],(max(nn)-min(nn))/g['height'])
            if primary:
                target=ground_per_mm*100
                quantum=10**math.floor(math.log10(max(target,1)))/2
                step=max(1,round(target/quantum)*quantum)
            else:step=max(1000,math.ceil(ground_per_mm*25/1000)*1000)
        else:
            step=float(self.opt('utm_interval'))
            if not primary and step%1000:self._warnings.append('Manual UTM interval is not a 1000 m multiple; this is a survey-plan override of B-215.2.')
        boundary=box(x0,y0,x1,y1)
        emin,emax,nmin,nmax=min(ee),max(ee),min(nn),max(nn)
        eastings=np.arange(math.ceil(emin/step)*step,emax+step*.000001,step)
        northings=np.arange(math.ceil(nmin/step)*step,nmax+step*.000001,step)
        if len(eastings)+len(northings)>1000:raise ValueError('UTM graduations are too dense; increase the metre interval.')
        selection=self.opt('sides') if self.opt('utm_sides')=='Same as geographic' else self.opt('utm_sides')
        sides={'All four':{0,1,2,3},'Bottom and left':{0,2},'Top and right':{1,3},'None':set(),'None (small plans only)':set()}[selection]
        colour='black' if primary or self.opt('utm_colour')!='Magenta' else '#8f005d'
        length=self.opt('utm_tick_mm');linewidth=self.opt('utm_tick_lw')*72/25.4
        offset=max(self.opt('utm_offset_mm'),6.4+(self.opt('geo_font_pt')+self.opt('utm_font_pt'))*.352778/2+self.opt('dual_gap_mm'))
        self._warnings.append(f'UTM {zone}{"S" if self.opt("utm_hemisphere")=="South" else "N"}: {step:g} m ticks, {self.opt("utm_font_pt"):g} pt labels.')
        primary_labels={side:[] for side in sides}
        for east,values in ((True,eastings),(False,northings)):
            for value in values:
                e=np.full(200,value) if east else np.linspace(emin-step,emax+step,200)
                n=np.linspace(nmin-step,nmax+step,200) if east else np.full(200,value)
                px,py=inverse.transform(e,n,errcheck=True)
                if local:px,py=local(px,py)
                line=LineString(np.column_stack((px,py))).intersection(boundary)
                parts=[line] if line.geom_type=='LineString' else list(getattr(line,'geoms',()))
                for part in parts:
                    if part.geom_type!='LineString' or part.is_empty:continue
                    coords=np.asarray(part.coords)
                    if self.opt('utm_display')=='Full grid':
                        artists=ax.plot(coords[:,0],coords[:,1],color=colour,lw=self.opt('utm_grid_lw')*72/25.4,zorder=1,clip_on=True)
                        for artist in artists:artist.set_gid('utm-grid-line')
                    for px,py in (coords[0],coords[-1]):
                        distances=[abs(px-x0)/g['ux'],abs(px-x1)/g['ux'],abs(py-y0)/g['uy'],abs(py-y1)/g['uy']]
                        side=int(np.argmin(distances))
                        if side not in sides or distances[side]>.01:continue
                        dx,dy=[(-1,0),(1,0),(0,-1),(0,1)][side]
                        ax.plot([px,px+dx*length*g['ux']],[py,py+dy*length*g['uy']],color=colour,lw=linewidth,clip_on=False)
                        along=(px-x0)/g['ux'] if side>=2 else (py-y0)/g['uy']
                        span=g['width'] if side>=2 else g['height']
                        half=self.opt('utm_font_pt')*.352778*len(f'E {value:.0f}')*.3
                        if min(along,span-along)<half+1:continue
                        label=f'{value/1000:g}' if self.opt('utm_numbering').startswith('Kilometres') else (f'{value:,.0f}'.replace(',', ' ') if self.opt('utm_group_digits') else f'{value:.0f}')
                        text=self.chart_text(ax,px+dx*offset*g['ux'],py+dy*offset*g['uy'],('E ' if east else 'N ')+label,
                            'utm-border',ha='center',va='center',rotation=90 if side<2 else 0,color=colour)
                        text._graduation_font=self.opt('utm_font_pt')
                        if primary:primary_labels[side].append((along,text))
        if primary:
            for labels in primary_labels.values():
                labels.sort(key=lambda item:item[0])
                keep={id(text) for _,text in labels[:2]+labels[-2:]}
                for _,text in labels:
                    if id(text) not in keep:
                        text.remove();self._border_texts.remove(text)
        note=f'UTM {zone}{"S" if self.opt("utm_hemisphere")=="South" else "N"} / WGS84; {step:g} m; {"construction grid" if primary else "reference grid"}'
        if self.opt('utm_numbering').startswith('Kilometres'):note+='; km labels: last 3 digits omitted'
        self.chart_text(ax,x0+2*g['ux'],y0+3*g['uy'],note,ha='left',va='bottom',color=colour)

    def draw_rectangular_grid(self,ax,g,second=False):
        if self.opt('grid')=='Off':return
        from shapely.geometry import LineString,box
        grid_crs=CRS.from_user_input(self.opt('grid2_crs' if second else 'grid_crs'))
        if not grid_crs.is_projected or any(abs(a.unit_conversion_factor-1)>1e-8 for a in grid_crs.axis_info[:2]):raise ValueError('Rectangular grid CRS must use projected metre coordinates.')
        dual_crs=CRS.from_epsg((32700 if self.opt('utm_hemisphere')=='South' else 32600)+int(self.opt('utm_zone')))
        dual_border=self.opt('dual_graduation') and self.opt('utm_display')!='Off' and grid_crs==dual_crs and (g['scale']<=100000 or self.opt('utm_small_scale')) and self.opt('utm_sides')!='None'
        to_grid=Transformer.from_crs(MERCATOR,grid_crs,always_xy=True);to_map=Transformer.from_crs(grid_crs,MERCATOR,always_xy=True)
        x0,x1,y0,y1=(g[k] for k in ('x0','x1','y0','y1'));boundary=box(x0,y0,x1,y1)
        xx=np.r_[np.linspace(x0,x1,80),np.full(80,x1),np.linspace(x1,x0,80),np.full(80,x0)]
        yy=np.r_[np.full(80,y0),np.linspace(y0,y1,80),np.full(80,y1),np.linspace(y1,y0,80)]
        ee,nn=to_grid.transform(xx,yy,errcheck=True);step=self.opt('grid2_step' if second else 'grid_step')
        emin,emax,nmin,nmax=min(ee),max(ee),min(nn),max(nn)
        eastings=np.arange(math.floor(emin/step)*step,emax+step,step);northings=np.arange(math.floor(nmin/step)*step,nmax+step,step)
        if len(eastings)+len(northings)>1000:raise ValueError('Grid is too dense; increase the grid interval.')
        color='black' if second or self.opt('grid_kind').startswith('Primary') else '#b12d80';mode=self.opt('grid')
        if g['scale']>100000:self._warnings.append('Rectangular grids are not normally shown below 1:100,000.')
        if self.opt('grid_kind').startswith('Primary') and grid_crs!=CRS.from_user_input(MERCATOR):self._warnings.append('Primary construction grid must match the Mercator plot projection; choose EPSG:3395 or Secondary.')
        if mode=='Crosses':
            if len(eastings)*len(northings)>10000:raise ValueError('Too many grid intersections.')
            for e in eastings:
                x,y=to_map.transform(np.full(len(northings),e),northings)
                for px,py in zip(x,y):
                    if x0<px<x1 and y0<py<y1:ax.plot([px-g['ux'],px+g['ux']],[py,py],color=color,lw=.3);ax.plot([px,px],[py-g['uy'],py+g['uy']],color=color,lw=.3)
        for east,values in ((True,eastings),(False,northings)):
            for value in values:
                e=np.full(100,value) if east else np.linspace(emin-step,emax+step,100)
                n=np.linspace(nmin-step,nmax+step,100) if east else np.full(100,value)
                x,y=to_map.transform(e,n);line=LineString(np.column_stack((x,y))).intersection(boundary)
                parts=[line] if line.geom_type=='LineString' else list(getattr(line,'geoms',()))
                for part in parts:
                    if part.geom_type!='LineString' or part.is_empty:continue
                    coords=np.asarray(part.coords)
                    if mode=='Full grid' and not (dual_border and self.opt('utm_display')=='Full grid'):ax.plot(coords[:,0],coords[:,1],color=color,lw=.3)
                    if dual_border:continue
                    for px,py in (coords[0],coords[-1]):
                        distances=[abs(px-x0)/g['ux'],abs(px-x1)/g['ux'],abs(py-y0)/g['uy'],abs(py-y1)/g['uy']];side=int(np.argmin(distances))
                        if distances[side]>.01:continue
                        dx,dy=[(-1,0),(1,0),(0,-1),(0,1)][side]
                        ax.plot([px,px+dx*4*g['ux']],[py,py+dy*4*g['uy']],color=color,lw=.5,clip_on=False)
                        along=(px-x0)/g['ux'] if side>=2 else (py-y0)/g['uy']
                        span=g['width'] if side>=2 else g['height']
                        if min(along,span-along)<(23 if side>=2 else 12):continue
                        value_label=value+(self.opt('false_e') if east else self.opt('false_n'))
                        label=f'{value_label:.0f}' if self.opt('grid_label').startswith('Full') else f'{value_label/1000:g}'
                        self.chart_text(ax,px+dx*(g['outer']+3 if second else 8.6)*g['ux'],py+dy*(g['outer']+3 if second else 8.6)*g['uy'],('E ' if east else 'N ')+label,'utm-border',ha='center',va='center',rotation=90 if side<2 else 0,color=color)
        if self.opt('grid_note') and not dual_border:
            note=f'Grid: {grid_crs.to_string()} • {step:g} m • Easting then Northing • reference use'
            if self.opt('grid_label').startswith('Abbreviated'):note+=' • kilometre labels (3 final digits omitted)'
            if self.opt('grid_id') and not second:note+=' • '+self.opt('grid_id')
            self.chart_text(ax,x0+2*g['ux'],y0+(13 if second else 9)*g['uy'],note,ha='left',va='bottom',color=color)

        if self.opt('grid_diagram') and not second:
            from matplotlib.patches import Rectangle
            x=x1-45*g['ux'];y=y1-35*g['uy']
            ax.add_patch(Rectangle((x,y),40*g['ux'],24*g['uy'],facecolor='white',edgecolor=color,lw=.5))
            self.chart_text(ax,x+20*g['ux'],y+18*g['uy'],self.opt('grid_id') or grid_crs.to_string(),ha='center',va='center',color=color)
            self.chart_text(ax,x+20*g['ux'],y+8*g['uy'],f'{step:g} m grid squares',ha='center',va='center',color=color)
        if self.opt('grid2') and not second:self.draw_rectangular_grid(ax,g,True)

    def draw_linear_scale(self,ax,g,repeat=False,zone=None):
        from pyproj import Proj
        if self.opt('scale_latitudes').startswith('South') and zone is None:
            self.draw_linear_scale(ax,g,False,g['bounds'][2]);self.draw_linear_scale(ax,g,True,g['bounds'][3]);return
        mode=self.opt('scale_bar');scale=g['scale']
        if mode=='Off' or mode.startswith('Automatic') and scale>80000 and not g['inset']:return
        short=mode=='Inset / short scale' or g['inset'] or self.opt('kind')!='Chart'
        if scale>80000 and not short:self._warnings.append('INT 2 normally omits border scales below 1:80,000; this is a manual override.')
        style=self.opt('scale_style')[0]
        if style=='A':style='O' if scale<=12500 else 'P' if scale<=30000 else 'Q' if scale<=50000 else 'R'
        major,minor=(self.opt('scale_major'),self.opt('scale_minor')) if style=='C' else SCALE_STYLES[style]
        if minor>major:raise ValueError('Scale subdivision must not exceed the major interval.')
        unit=self.opt('scale_units');unit_m=1 if unit=='Metres' else 1852 if unit=='Nautical miles' else 185.2
        if unit!='Metres':
            target_m=scale*self.opt('scale_length')/1000/5
            values=np.asarray([.1,.2,.5,1,2,5,10,20,50,100])*unit_m
            major=float(values[np.argmin(abs(values-target_m))]);minor=major/5
        if zone is not None:
            reference=sum(g['bounds'][2:])/2;lon=sum(g['bounds'][:2])/2
            scale=scale*Proj(MERCATOR).get_factors(lon,reference).parallel_scale/Proj(MERCATOR).get_factors(lon,zone).parallel_scale
        target=min(self.opt('scale_length'),90 if short else 450)
        position=self.opt('scale_pos')
        if repeat:position={'Bottom border':'Top border','Top border':'Bottom border','Left border':'Right border','Right border':'Left border'}[position]
        vertical=position.startswith(('Left','Right'))
        available=(g['height'] if vertical else g['width'])-15
        if not short and available<200:
            self._warnings.append('Sheet has less than 200 mm for a border scale; choose a larger sheet or an inset/short scale.');return
        target=min(target,available)
        if not short:target=max(200,target)
        major_mm=major/scale*1000
        if major_mm<8:
            self._warnings.append('Selected linear-scale intervals would crowd labels; choose Automatic or a larger interval.');return
        segments=max(1,int(target/major_mm)-1)
        length=(segments+1)*major_mm
        if length>available:self._warnings.append('Selected scale interval does not fit; choose a finer scale style.');return
        if not short and length<200:
            segments=math.ceil(200/major_mm)-1;length=(segments+1)*major_mm
            if length>available or length>450:self._warnings.append('Scale pattern cannot produce a 200–450 mm scale on this sheet.');return
        x0,x1,y0,y1=(g[k] for k in ('x0','x1','y0','y1'))
        midlat=(sum(g['bounds'][2:])/2 if zone is None else zone);midlon=sum(g['bounds'][:2])/2
        k=Proj(MERCATOR).get_factors(midlon,midlat).parallel_scale
        distance=(segments+1)*major*k
        anchorx=(x0+x1-distance)/2 if not vertical else x0-9.5*g['ux'] if position.startswith('Left') else x1+9.5*g['ux']
        anchory=(y0+y1-distance)/2 if vertical else y0-9.5*g['uy'] if position.startswith('Bottom') else y1+9.5*g['uy']
        if short:
            anchorx=x0+8*g['ux'];anchory=y0+18*g['uy']
        def point(distance_m,offset_mm=0):return (anchorx+offset_mm*g['ux'],anchory+distance_m*k) if vertical else (anchorx+distance_m*k,anchory+offset_mm*g['uy'])
        end=(segments+1)*major
        ax.plot(*zip(point(0),point(end)),color='black',lw=.4,clip_on=False)
        if major/minor>500:raise ValueError('Linear-scale subdivisions are too dense; increase subdivision size.')
        ticks=np.unique(np.r_[np.arange(0,major+minor*.1,minor),np.arange(major,end+major*.1,major)])
        for value in ticks:
            is_major=abs(value/major-round(value/major))<1e-7
            ax.plot(*zip(point(value),point(value,1.8 if is_major else 1)),color='black',lw=.4,clip_on=False)
            if is_major:self.chart_text(ax,*point(value,2.2),f'{abs(value-major)/unit_m:g}',ha='center',va='bottom',rotation=90 if vertical else 0)
        self.chart_text(ax,*point(end/2,5),unit.upper()+(f' at {midlat:.3f}°' if zone is not None else ''),ha='center',va='bottom',rotation=90 if vertical else 0)
        if self.opt('scale_dice'):
            if unit=='Metres':self._warnings.append('Metre scale dicing was suppressed: INT 2 allows dicing for nautical-mile/cable units.')
            else:
                from matplotlib.patches import Polygon
                for i in range(segments+1):
                    poly=Polygon([point(i*major),point((i+1)*major),point((i+1)*major,.8),point(i*major,.8)],facecolor='black' if i%2==0 else 'white',edgecolor='black',lw=.3,clip_on=False);ax.add_patch(poly)
        if self.opt('scale_opposite') and not repeat and not short and zone is None:self.draw_linear_scale(ax,g,True,zone)
        if self.opt('extra_scale') and unit=='Metres' and not repeat:
            nm=1852;max_nm=max(.1,math.floor(target/1000*scale/nm*10)/10)
            p0=(x0+8*g['ux'],y0+24*g['uy']);p1=(p0[0]+max_nm*nm*k,p0[1])
            if p1[0]<x1:
                ax.plot([p0[0],p1[0]],[p0[1],p1[1]],color='black',lw=.6)
                for x in (p0[0],p1[0]):ax.plot([x,x],[p0[1]-g['uy'],p0[1]+g['uy']],color='black',lw=.4)
                self.chart_text(ax,p0[0],p0[1]+2*g['uy'],'0',ha='center',va='bottom')
                self.chart_text(ax,p1[0],p1[1]+2*g['uy'],f'{max_nm:g} NM',ha='center',va='bottom')

    def apply_lettering(self):
        from matplotlib.text import Text
        family = self.font_family.currentText(); style = self.font_style.currentText()
        base = self.font_size.value(); renderer = self.canvas.get_renderer()
        for text in self.figure.findobj(Text):
            text.set_fontfamily(family); text.set_fontsize(base)
            text.set_fontstyle('italic' if style=='Italic' else 'normal')
            text.set_fontweight('bold' if style=='Bold' else 'normal')
            text.set_parse_math(False)
        if style.startswith('Cartographic'):
            for text in self._sounding_texts:text.set_fontstyle('italic')
        self.figure.texts[0].set_fontsize(base*1.8)
        self.figure.texts[0].set_fontweight('bold')
        self.figure.texts[1].set_fontsize(base)
        for text in self._int2_texts:text.set_fontsize(min(base,7));text.set_fontstyle('normal')
        self.draw_metadata(renderer)
        if any(self.opt(key)!=value for key,value in getattr(self,'_series_limits',{}).items()):self._series_stamp=''
        if getattr(self,'_series_stamp',''):self.figure.text(.98,.985,self._series_stamp,ha='right',va='top',fontsize=self.font_size.value(),parse_math=False)
        if len(self._sounding_texts)>=2000:self._warnings.append('Sounding label preview/export is capped at 2,000; reduce selected area/data for more labels.')
        self.fit_dual_labels(renderer)
        if not self.auto_font.isChecked():return f' Font: {base:g} pt (manual).'+self.prevent_sounding_overlap(renderer)
        minimum = min(3.0,base)
        # Size border labels from their actual glyph widths and distances.
        ticks = [text for text in self._border_texts if not hasattr(text,'_graduation_font')]
        width = self.ax.bbox.width; height = self.ax.bbox.height
        ratios = [1.0]
        for axis,g in self._map_axes:
            groups={'left':[],'right':[],'top':[],'bottom':[]}
            for text in ticks:
                if text.axes is not axis:continue
                centre=text.get_transform().transform(text.get_position())
                side='left' if centre[0]<axis.bbox.x0 else 'right' if centre[0]>axis.bbox.x1 else 'bottom' if centre[1]<axis.bbox.y0 else 'top'
                groups[side].append(text.get_window_extent(renderer))
            for side,boxes in groups.items():
                horizontal=side in ('top','bottom');boxes.sort(key=lambda b:b.x0 if horizontal else b.y0)
                for a,b in zip(boxes,boxes[1:]):
                    distance=abs((b.x0+b.x1-a.x0-a.x1)/2) if horizontal else abs((b.y0+b.y1-a.y0-a.y1)/2)
                    if distance<1:continue
                    need=(a.width+b.width)/2+4 if horizontal else (a.height+b.height)/2+4
                    ratios.append(distance/max(need,1))
        border_size=max(minimum,base*min(ratios))
        for text in ticks:text.set_fontsize(border_size)
        # Shrink the title/footer to the physical sheet width.
        for text in self.figure.texts[:2]:
            box=text.get_window_extent(renderer)
            if box.width > self.figure.bbox.width*.86:
                text.set_fontsize(max(minimum,text.get_fontsize()*self.figure.bbox.width*.86/box.width))
        if self.opt('avoid_overlap'):
            return f' Fonts fitted: border {border_size:.1f}, soundings {base:.1f} pt.'+self.prevent_sounding_overlap(renderer)
        # Check sounding collisions in display coordinates, then fit them inside the neatline.
        labels=self._sounding_texts
        def position_and_collisions():
            bins={}; collision=False
            for text in labels:
                box=text.get_window_extent(renderer)
                dx=max(text.axes.bbox.x0+2-box.x0,0)-max(box.x1-text.axes.bbox.x1+2,0)
                dy=max(text.axes.bbox.y0+2-box.y0,0)-max(box.y1-text.axes.bbox.y1+2,0)
                if dx or dy:
                    pos=text.axes.transData.transform(text.get_position())
                    text.set_position(text.axes.transData.inverted().transform(pos+(dx,dy)))
                    box=text.get_window_extent(renderer)
                padded=box.expanded(1.08,1.12)
                keys=[(x,y) for x in range(int(padded.x0//40),int(padded.x1//40)+1) for y in range(int(padded.y0//40),int(padded.y1//40)+1)]
                if any(padded.overlaps(other) for key in keys for other in bins.get(key,())):collision=True
                for key in keys:bins.setdefault(key,[]).append(padded)
            return collision
        sounding_size=base
        while position_and_collisions() and sounding_size>minimum:
            sounding_size=max(minimum,sounding_size*.85)
            for text in labels:text.set_fontsize(sounding_size)
        unresolved=position_and_collisions()
        overlap_note=self.prevent_sounding_overlap(renderer)
        if self.opt('avoid_overlap'):unresolved=False
        return f' Fonts fitted: border {border_size:.1f}, soundings {sounding_size:.1f} pt.'+(' Dense labels still overlap at minimum size; use a smaller area or larger sheet.' if unresolved else '')+overlap_note

    def preview_before_save(self):
        self.render_plot()
        dialog=W.QDialog(self); dialog.setWindowTitle('Export preview — review fonts and plot extents'); dialog.resize(1250,900);dialog.setWindowFlags(dialog.windowFlags()|C.Qt.WindowType.WindowMaximizeButtonHint)
        layout=W.QVBoxLayout(dialog)
        owner=self
        class PreviewImage(W.QLabel):
            def resizeEvent(self,event):
                super().resizeEvent(event)
                self.setPixmap(owner._preview_pixmap.scaled(self.contentsRect().size(),C.Qt.AspectRatioMode.KeepAspectRatio,C.Qt.TransformationMode.SmoothTransformation))
            def mousePressEvent(self,event):
                if not owner.metadata_mouse(self,event):super().mousePressEvent(event)
            def mouseMoveEvent(self,event):
                if not owner.metadata_mouse(self,event):super().mouseMoveEvent(event)
            def mouseReleaseEvent(self,event):
                if not owner.metadata_mouse(self,event):super().mouseReleaseEvent(event)
        image=PreviewImage(); image.setAlignment(C.Qt.AlignmentFlag.AlignCenter)
        image.setSizePolicy(W.QSizePolicy.Policy.Ignored,W.QSizePolicy.Policy.Ignored)
        image.setMinimumSize(100,100);layout.addWidget(image,1)
        info=W.QLabel('DXF / DWG: '+getattr(self.figure,'_cad_crs','EPSG:3395')+', model-space metres. Lettering is preserved as vector outlines.\nDWG requires ODA File Converter. '+self.status.text()); info.setWordWrap(True);layout.addWidget(info)
        buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Save | W.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(W.QDialogButtonBox.StandardButton.Cancel).setText('Back to editing')
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        dialog.showMaximized()
        if dialog.exec()==W.QDialog.DialogCode.Accepted:self.choose_export()

    def save_plot(self):
        self.preview_before_save()

    def choose_export(self):
        name,selected=W.QFileDialog.getSaveFileName(self,'Save reviewed plot',str(Path.home()/'Desktop'/'survey_plot.pdf'),'PDF (*.pdf);;PNG (*.png);;SVG (*.svg);;DXF (*.dxf);;DWG (*.dwg)')
        if not name:return
        path=Path(name)
        if not path.suffix:path=path.with_suffix('.'+selected.split()[0].lower())
        self.export_file(path)
        self.status.setText('Saved: '+str(path))

    def export_file(self,path):
        path=Path(path); suffix=path.suffix.lower()
        if suffix in ('.pdf','.png','.svg'):
            self.figure.savefig(path,dpi=int(self.opt('dpi').split()[0]),facecolor='white');return
        if suffix not in ('.dxf','.dwg'):raise ValueError('Use PDF, PNG, SVG, DXF or DWG.')
        if suffix=='.dxf':
            export_figure_dxf(self.figure,self.ax,path);return
        import tempfile, subprocess, shutil, os
        converter=self.converter.text().strip() or str(self.modern.find_oda_file_converter() or '')
        if not converter or not Path(converter).is_file():
            raise ValueError('DWG export requires ODA File Converter. Locate ODAFileConverter.exe in the lettering panel, or save as DXF.')
        with tempfile.TemporaryDirectory(prefix='HYDROGEOMATICS_plot_dwg_') as tmp:
            source=Path(tmp)/'in'; target=Path(tmp)/'out'; source.mkdir();target.mkdir()
            export_figure_dxf(self.figure,self.ax,source/'plot.dxf')
            environment=os.environ.copy()
            # ODA ships its own Qt runtime; do not inherit Python Qt plugin paths.
            for key in list(environment):
                if key.startswith(('QT_', 'QML')):environment.pop(key)
            process=subprocess.Popen([converter,str(source),str(target),'ACAD2018','DWG','0','1','*.dxf'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            progress=W.QProgressDialog('Converting DXF to DWG…','Cancel',0,0,self);progress.setWindowModality(C.Qt.WindowModality.ApplicationModal);progress.setMinimumDuration(0)
            import time
            started=time.monotonic()
            # Poll without blocking Qt; drain both pipes through a worker to avoid deadlocks.
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(process.communicate)
                while not future.done():
                    W.QApplication.processEvents()
                    if progress.wasCanceled() or time.monotonic()-started>180:
                        process.kill();future.result();progress.close()
                        raise RuntimeError('DWG conversion cancelled or timed out. No destination file was written.')
                    time.sleep(.04)
                stdout,stderr=future.result()
            progress.close()
            converted=target/'plot.dwg'
            if process.returncode or not converted.exists() or converted.stat().st_size<6:
                detail=(stderr or stdout).decode(errors='replace')[-1000:]
                raise RuntimeError('ODA could not create DWG. '+detail)
            if not converted.read_bytes()[:6].startswith(b'AC10'):raise RuntimeError('Converter output is not a DWG file.')
            # Only replace the destination once conversion has succeeded.
            staging=path.with_name(path.name+'.partial')
            try:shutil.copy2(converted,staging);os.replace(staging,path)
            finally:
                if staging.exists():staging.unlink()


def export_figure_dxf(figure,ax,filename):
    """Capture the reviewed plot as CAD vectors, in EPSG:3395 metres.

    Text outlines preserve chosen fonts without requiring fonts in the CAD viewer.
    Clipping uses the same rectangular neatline as the on-screen plot.
    """
    import ezdxf
    from matplotlib.backend_bases import RendererBase
    from matplotlib.backends.backend_agg import RendererAgg
    from matplotlib.path import Path as MPath
    from shapely.geometry import Polygon, LineString, box as clip_box
    from ezdxf.colors import rgb2int
    from ezdxf import units
    figure.canvas.draw()
    document=ezdxf.new('R2018');document.units=units.M
    document.header['$PROJECTNAME']=getattr(figure,'_cad_crs','EPSG:3395')+'; model units metres; lettering as outlines'
    for name in ('PLOT_GEOMETRY','CARTOGRAPHIC_TEXT'):document.layers.new(name)
    space=document.modelspace()
    factor=(ax.get_xlim()[1]-ax.get_xlim()[0])/ax.bbox.width
    ox=ax.get_xlim()[0]-ax.bbox.x0*factor;oy=ax.get_ylim()[0]-ax.bbox.y0*factor
    class CADRenderer(RendererBase):
        def __init__(self):
            super().__init__();self.dpi=figure.dpi;self.text_mode=False
            self.metrics=RendererAgg(int(figure.bbox.width),int(figure.bbox.height),self.dpi)
        def flipy(self):return False
        def get_canvas_width_height(self):return figure.bbox.width,figure.bbox.height
        def points_to_pixels(self,points):return points*self.dpi/72
        def get_text_width_height_descent(self,s,prop,ismath):return self.metrics.get_text_width_height_descent(s,prop,ismath)
        def draw_text(self,gc,x,y,s,prop,angle,ismath=False,mtext=None):
            self.text_mode=True
            try:super().draw_text(gc,x,y,s,prop,angle,ismath,mtext)
            finally:self.text_mode=False
        def draw_image(self,*args,**kwargs):raise RuntimeError('Raster images cannot be exported as CAD vectors.')
        def draw_path(self,gc,path,transform,rgbFace=None):
            transformed=transform.transform_path(path)
            # Flatten curves before clipping, at subpixel tolerance.
            runs=[];current=[]
            for vertices,code in transformed.iter_segments(curves=False,simplify=False):
                if code==MPath.MOVETO:
                    if len(current)>1:runs.append(current)
                    current=[tuple(vertices[-2:])]
                elif code==MPath.CLOSEPOLY:
                    if current:current.append(current[0])
                elif code==MPath.LINETO:current.append(tuple(vertices[-2:]))
            if len(current)>1:runs.append(current)
            rect=gc.get_clip_rectangle();clip=clip_box(*rect.extents) if rect is not None else None
            def attributes(color):
                rgb=tuple(round(255*max(0,min(1,float(c)))) for c in color[:3])
                alpha=float(color[3]) if len(color)>3 else gc.get_alpha()
                if alpha<1:rgb=tuple(round(c*alpha+255*(1-alpha)) for c in rgb)
                return {'layer':'CARTOGRAPHIC_TEXT' if self.text_mode else 'PLOT_GEOMETRY','true_color':rgb2int(rgb)}
            def coords(ring):
                world=getattr(figure,'_cad_to_world',None)
                points=[(ox+x*factor,oy+y*factor) for x,y in ring]
                return [world(x,y) for x,y in points] if world else points
            if rgbFace is not None:
                filled=None
                for run in runs:
                    if len(run)<3:continue
                    polygon=Polygon(run)
                    if not polygon.is_valid:polygon=polygon.buffer(0)
                    filled=polygon if filled is None else filled.symmetric_difference(polygon)
                if filled is not None:
                    if clip is not None:filled=filled.intersection(clip)
                    polys=[filled] if filled.geom_type=='Polygon' else list(getattr(filled,'geoms',()))
                    for poly in polys:
                        if poly.geom_type!='Polygon' or poly.is_empty:continue
                        hatch=space.add_hatch(dxfattribs=attributes(rgbFace))
                        hatch.paths.add_polyline_path(coords(poly.exterior.coords),is_closed=True,flags=1)
                        for ring in poly.interiors:hatch.paths.add_polyline_path(coords(ring.coords),is_closed=True,flags=0)
            if gc.get_linewidth()>0:
                for run in runs:
                    line=LineString(run)
                    if clip is not None:line=line.intersection(clip)
                    lines=[line] if line.geom_type=='LineString' else list(getattr(line,'geoms',()))
                    for segment in lines:
                        if segment.geom_type=='LineString' and not segment.is_empty:
                            attrs=attributes(gc.get_rgb());attrs['lineweight']=min(211,max(0,round(gc.get_linewidth()*25.4/72*100)))
                            space.add_lwpolyline(coords(segment.coords),dxfattribs=attrs)
    figure.draw(CADRenderer())
    world=getattr(figure,'_cad_to_world',None)
    centre=(sum(ax.get_xlim())/2,sum(ax.get_ylim())/2)
    if world:centre=world(*centre)
    document.set_modelspace_vport(height=(ax.get_ylim()[1]-ax.get_ylim()[0])*1.45,center=centre)
    import os,tempfile
    filename=Path(filename)
    with tempfile.NamedTemporaryFile(suffix='.dxf',dir=filename.parent,delete=False) as handle:staging=Path(handle.name)
    try:document.saveas(staging);os.replace(staging,filename)
    finally:
        if staging.exists():staging.unlink()
