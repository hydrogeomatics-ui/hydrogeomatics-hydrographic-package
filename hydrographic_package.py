#!/usr/bin/env python3
"""HYDROGEOMATICS HYDROGEOMATICS — standalone merged PyQt6 survey application.

Source inputs: 0701.py and sept102026.py, supplied by the user.
All survey workflows are retained in an isolated module; its widget imports
are served by native Qt implementations, without launching Tk or an external file.
The newer map/CAD/KTD interface shares the survey session and HYPACK writer.

Install: python -m pip install PyQt6 pyqtgraph numpy pyserial pyproj ezdxf matplotlib utide sonarlight pandas
Run:     python "hydrographic_package.py"
CLI:     python "hydrographic_package.py" --help
Dredge:  python "hydrographic_package.py" --dredge-volume
Added:   embedded DREDGE VOLUME with mouse-based channel design.
"""
from __future__ import annotations
import builtins
import sys
import types
import os
from pathlib import Path

def _module(name, source, widget_module=None):
    module=types.ModuleType(name)
    module.__file__=__file__
    module.__dict__['__builtins__']=dict(vars(builtins))
    if widget_module is not None:
        original=builtins.__import__
        def import_module(name, globals=None, locals=None, fromlist=(), level=0):
            if name=='tkinter':return widget_module
            if name=='tkinter.ttk':return widget_module.ttk
            return original(name,globals,locals,fromlist,level)
        module.__dict__['__builtins__']['__import__']=import_module
    sys.modules[name]=module
    exec(compile(source, name+'.py', 'exec'),module.__dict__)
    return module


# === QT_WIDGET_SOURCE: retained source, one string literal per source line ===
QT_WIDGET_SOURCE = (Path(__file__).resolve().parent / "modules" / "qt_widget_source.py").read_text(encoding="utf-8")

# === SURVEY_SOURCE: retained source, one string literal per source line ===
SURVEY_SOURCE = (Path(__file__).resolve().parent / "modules" / "survey_source.py").read_text(encoding="utf-8")

# === MAP_SOURCE: retained source, one string literal per source line ===
MAP_SOURCE = (Path(__file__).resolve().parent / "modules" / "map_source.py").read_text(encoding="utf-8")

# === ENGINE_SOURCE: retained source, one string literal per source line ===
ENGINE_SOURCE = (Path(__file__).resolve().parent / "modules" / "engine_source.py").read_text(encoding="utf-8")

INDUS_KTD_SOURCE = (Path(__file__).resolve().parent / "modules" / "indus_ktd_source.py").read_text(encoding="utf-8")
CHART_PLOT_SOURCE = (Path(__file__).resolve().parent / "modules" / "chart_plot_source.py").read_text(encoding="utf-8")

ENC_EDITOR_SOURCE = (Path(__file__).resolve().parent / "modules" / "enc_editor_source.py").read_text(encoding="utf-8")

def load_components():
    try:
        widgets=_module('HYDROGEOMATICS20_widgets',QT_WIDGET_SOURCE)
        qt=widgets.make_modules()
        core=_module('HYDROGEOMATICS20_survey',SURVEY_SOURCE,qt)
        modern=_module('HYDROGEOMATICS20_map',MAP_SOURCE)
        engine=_module('HYDROGEOMATICS20_engine',ENGINE_SOURCE)
        acq=engine.install(core,modern)
        return widgets,core,modern,engine,acq
    except ImportError as exc:
        raise SystemExit(f'Missing dependency: {exc}\nInstall: python -m pip install PyQt6 pyqtgraph numpy pyserial pyproj ezdxf matplotlib utide') from exc

def create_window(app, components=None, testing=False, project=None):
    widgets,core,modern,engine,acq=components or load_components()
    from PyQt6 import QtWidgets as W, QtCore as C, QtGui as G
    widgets.initialize(app)
    app._testing=testing
    app.setStyle('Fusion')
    if project:
        root=Path(project);root.mkdir(parents=True,exist_ok=True)
        core.DEFAULT_PROJECT_FOLDER=root
        core.DEFAULT_SETTINGS_PATH=root/'HYDROGEOMATICS_settings.json'
        def isolated_settings():
            s=core.AppSettings.load(core.DEFAULT_SETTINGS_PATH) if core.DEFAULT_SETTINGS_PATH.exists() else core.AppSettings.defaults()
            s.raw_folder=str(root/'RAW')
            if testing:
                s.gps.enabled=False;s.sensor.enabled=False
                for d in s.extra_devices:d.enabled=False
            return s
        core.load_app_settings=isolated_settings
    context={}
    modern.MERGED_PROJECT_FOLDER=core.project_root_from_raw_folder(core.load_app_settings().raw_folder)
    map_shapes_loader=modern.load_shapes

    class MergedWindow(modern.MainWindow):
        def __init__(self):
            self.context=context;self.acquisition=acq;self.engine=engine;self.core=core
            self._last_live=None;self._layer_versions={};self._snapshot_signature=None;self._follow_live=False
            super().__init__()
            self.device_timer.stop();self.port_scan_timer.stop()
            map_page=self.takeCentralWidget()
            self.pages=W.QTabWidget(self)
            self.setCentralWidget(self.pages)
            self.pages.addTab(map_page,'MAP window');self.pages.tabBar().hide()
            # The survey workspace must never become an independent HWND.
            widgets.ROOT_PARENT=self.pages
            try:context.update(core.run_gui())
            finally:widgets.ROOT_PARENT=None
            acq.attach(context)
            context['model'].geodetic=context['settings'].geodetic
            self.setWindowTitle('MAP window')
            self.crs_box.setEnabled(False)
            self.crs_box.setToolTip('Coordinate system is shared with Survey Settings → Geodetic.')
            self.project_folder=Path(core.project_root_from_raw_folder(context['settings'].raw_folder))
            self.refresh_project_label()
            context['root'].q.setProperty('unified_navigation',True)
            context['root'].q.hide()
            self.event_log.document().setMaximumBlockCount(1000)
            self.refresh_timer=C.QTimer(self);self.refresh_timer.timeout.connect(lambda:widgets.call(self.tick));self.refresh_timer.start(150)
            self.view.live_depth_scatter=modern.pg.ScatterPlotItem(pxMode=True,size=4,pen=None,brush=modern.pg.mkBrush('#0284c7'))
            self.view.addItem(self.view.live_depth_scatter)
            self._install_menus()
            self._install_navigation()
            self.health_badge=W.QLabel('Recording health: idle');self.status.addPermanentWidget(self.health_badge)
            self.health_timer=C.QTimer(self);self.health_timer.timeout.connect(self.update_health_badge);self.health_timer.start(1000)
            self._health_window=None
            acq.new_window=self
            if not testing:
                self.autoload_project_layers()
            self.tick()

        def show_dredge_volume(self):
            """Open the embedded local calculation module; no external files needed."""
            from http.server import ThreadingHTTPServer
            import threading
            try:
                if getattr(self, '_dredge_server', None) is None:
                    module = _module('HYDROGEOMATICS05_dredge', DREDGE_VOLUME_SOURCE)
                    server = ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
                    server.daemon_threads = True
                    self._dredge_module = module
                    self._dredge_server = server
                    thread = threading.Thread(target=server.serve_forever, name='DredgeVolumeHTTP', daemon=True)
                    thread.start()
                    self._dredge_thread = thread
                    def stop_server():
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=2)
                        self._dredge_server = None
                    app.aboutToQuit.connect(stop_server)
                url = C.QUrl('http://127.0.0.1:%s/' % self._dredge_server.server_address[1])
                if not getattr(app, '_testing', False):
                    if not G.QDesktopServices.openUrl(url):
                        raise RuntimeError('Cannot open the browser. Open this local address manually: ' + url.toString())
                self.statusBar().showMessage('Dredge Volume / Channel Design: ' + url.toString(), 15000)
                return url.toString()
            except Exception as exc:
                W.QMessageBox.warning(self, 'Dredge Volume', str(exc))
                return None

        def enc_editor_module(self):
            module=getattr(self,'_enc_editor_module',None)
            if module is None:
                module=_module('HYDROGEOMATICS27_enc_editor',ENC_EDITOR_SOURCE)
                self._enc_editor_module=module
            return module
        def show_enc_editor(self):
            try:
                existing=getattr(self,'_enc_editor',None)
                if existing is None:
                    existing=self.enc_editor_module().Editor()
                    self._enc_editor=existing
                    self.configure_enc_hub(existing)
                existing.nav_timer.start()
                existing.showMaximized();existing.raise_();existing.activateWindow()
                return existing
            except Exception as exc:
                W.QMessageBox.warning(self,'ENC Editor',str(exc))
        def configure_enc_hub(self,editor):
            bar=editor.addToolBar('Survey backgrounds')
            bar.addAction('Add ENC backgrounds…',self.add_enc_backgrounds)
            bar.addAction('Background manager',self.show_background_manager)
            bar.addAction('Display viewed charts on survey map',lambda:self.start_background_load([editor.enc_view_path] if editor.enc_view_path and editor.enc_view_path.lower().endswith('.encpack') else sorted({f['source_chart'] for f in editor.enc_view_features if f.get('source_chart')})))
            self._enc_display_timer=C.QTimer(self);self._enc_display_timer.setSingleShot(True)
            self._enc_display_timer.timeout.connect(self.refresh_enc_display)
            controls=[editor.palette_box,editor.sounding_size,*editor.depth_settings.values(),editor.show_soundings,editor.show_chart_labels,editor.show_chart_meta]
            for control in controls:
                signal=control.currentTextChanged if isinstance(control,W.QComboBox) else control.toggled if isinstance(control,W.QCheckBox) else control.valueChanged
                signal.connect(lambda *_:self._enc_display_timer.start(120))
            self.view.getViewBox().sigRangeChanged.connect(lambda *_:self._enc_display_timer.start(120))
        def ensure_enc_display(self):
            if any(getattr(shape,'_enc_features',None) for layer in self.layers for shape in layer.shapes):
                if getattr(self,'_enc_editor',None) is None:
                    editor=self.enc_editor_module().Editor();self._enc_editor=editor;self.configure_enc_hub(editor)
                self.refresh_enc_display()
        def refresh_enc_display(self):
            editor=getattr(self,'_enc_editor',None)
            if editor is None:return
            module=self.enc_editor_module()
            owner=self
            class Projection:
                def transform(self,x,y):
                    import numpy as np
                    scalar=np.isscalar(x);xs=[x] if scalar else x;ys=[y] if scalar else y
                    result=[core.project_lat_lon_to_selected_xy(float(lat),float(lon),context['settings'].geodetic) for lon,lat in zip(xs,ys)]
                    if any(a is None or b is None for a,b in result):raise ValueError('Cannot project ENC coordinates into survey CRS.')
                    xx,yy=zip(*result)
                    return (xx[0],yy[0]) if scalar else (np.asarray(xx),np.asarray(yy))
            for layer in self.layers:
                features=[f for shape in layer.shapes for f in getattr(shape,'_enc_features',[])]
                if not features:continue
                self.view.remove_layer_items(layer.group)
                proxy=types.SimpleNamespace(view=self.view,features=[],enc_view_features=features,items={},chart_transform=Projection(),chart_legend=W.QLabel())
                for key in ('palette_box','view_only','depth_settings','show_soundings','show_chart_labels','show_chart_meta','sounding_size'):setattr(proxy,key,getattr(editor,key))
                proxy.marine_style=types.MethodType(module.Editor.marine_style,proxy)
                proxy.draw_sounding_labels=types.MethodType(module.Editor.draw_sounding_labels,proxy)
                module.Editor.render(proxy)
                layer.group=[item for items in proxy.items.values() for item in items]+proxy._sounding_labels
                for item in layer.group:item._enc_z=item.zValue()
            self.apply_background_order()
        def add_enc_backgrounds(self):
            names,_=W.QFileDialog.getOpenFileNames(self,'Add ENC backgrounds — base cells or portable collections',str(self.project_folder),'ENC charts (*.000 *.enc *.s57 *.encpack);;All files (*)')
            if names:self.start_background_load(names)
        def show_chart_plot(self):
            existing=getattr(self,'_chart_plot',None)
            if existing is None:
                try:
                    module=_module('HYDROGEOMATICS26_chart_plot',CHART_PLOT_SOURCE)
                    existing=module.ChartPlotWindow(self,modern)
                    self._chart_plot=existing
                except ImportError as exc:
                    W.QMessageBox.warning(self,'Plotting dependencies',f'{exc}\nInstall: python -m pip install matplotlib pyproj numpy')
                    return
            existing.show();existing.raise_();existing.activateWindow()
            return existing
        def show_indus_ktd_editor(self):
            existing=getattr(self,'_indus_editor',None)
            if existing is not None and existing.root.winfo_exists():existing.root.lift();return existing
            try:
                module=getattr(self,'_indus_module',None)
                if module is None:
                    module=_module('HYDROGEOMATICS20_indus_ktd',INDUS_KTD_SOURCE,widgets.make_modules())
                    self._indus_module=module
                root=module.QtToplevel(context['root'])
                editor=module.App(root);self._indus_editor=editor
                root.title('KTD Editor — Indus / UTM 42N — draft datum review')
                integration=module.ttk.Frame(root,padding=6);integration.pack(side='bottom',fill='x')
                module.ttk.Button(integration,text='KTD table editor',command=lambda:self._tool('show_ktd_editor')).pack(side='left',padx=4)
                module.ttk.Button(integration,text='KTD matrix editor',command=lambda:modern.MatrixKtdEditor(self).show()).pack(side='left',padx=4)
                def load_grid():
                    name,_=W.QFileDialog.getOpenFileName(root.q,'Load KTD on navigation map',str(self.project_folder),'KTD (*.ktd *.KTD)')
                    if name:self.start_background_load([name])
                module.ttk.Button(integration,text='Load KTD on navigation map',command=load_grid).pack(side='left',padx=4)
                def close():
                    if editor.busy:
                        editor.status.set('A KTD export is running. Wait for it to finish before closing.');return
                    editor.canvas.close();root.destroy();self._indus_editor=None
                root.protocol('WM_DELETE_WINDOW',close)
                root.lift();return editor
            except Exception as exc:
                if 'root' in locals() and root.winfo_exists():root.destroy()
                self._indus_editor=None
                W.QMessageBox.warning(self,'KTD editor',f'Could not open KTD editor: {exc}\nRequired packages: numpy scipy shapely pyproj matplotlib rasterio requests')
                return None
        def load_startup_geodetic_settings(self):return {}
        def load_active_ktd_reference(self):pass
        def save_active_ktd_reference(self):
            if 'settings' in self.context:
                g=self.context['settings'].geodetic;g.ktd_file_path=self.ktd_file_path
                if self.use_rtk_tide_method and g.rtk_tide_method=='Not using RTK tide':g.rtk_tide_method='(K-N) from KTD file'
                self.context['save_default_settings']()
        def _tool(self,name):
            self.context.update(self.context['_refresh_state']())
            self.context['model'].geodetic=self.context['settings'].geodetic
            self._send_layers_to_survey()
            return widgets.call(self.context[name])
        def show_projects_panel(self):return self._tool('show_project_dialog')
        def show_settings_panel(self):
            if self.context['logging_active'].get():
                W.QMessageBox.information(self,'Recording','Stop recording before changing device or geodetic settings.');return
            return self._tool('show_settings_dialog')
        def show_hardware_settings(self):
            dialog=self.show_settings_panel()
            if dialog is not None:dialog._settings_notebook.select(1)
            return dialog
        def show_all_tools_panel(self):
            dialog=W.QDialog(self);dialog.setWindowTitle('All survey tools');dialog.resize(600,650)
            dialog.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose)
            layout=W.QVBoxLayout(dialog);scroll=W.QScrollArea();scroll.setWidgetResizable(True)
            content=W.QWidget();buttons=W.QVBoxLayout(content)
            for action in self._complete_tools_menu.actions():
                if action.isSeparator():continue
                button=W.QPushButton(action.text());button.clicked.connect(lambda checked=False,a=action:a.trigger());buttons.addWidget(button)
            buttons.addStretch();scroll.setWidget(content);layout.addWidget(scroll);dialog.show()
            self._all_tools_window=dialog
            return dialog
        def show_feature_availability(self):
            self.show_text_window('Feature availability',
                'Implemented workflows\nHardware settings and serial scanning; GNSS/SBES ASCII recording; RAW review and export; runlines and borders; KTD tools; DXF and KML/KMZ; MBES packet archive and processed CSV tools.\n\n'
                'External dependencies\nENC/S-57 needs GDAL. DWG conversion needs ODA File Converter. Device communication requires the correct driver, port and protocol.\n\n'
                'Unavailable in this build\nGeoTIFF raster rendering; proprietary MBES beam decoding; native BAG/S-102/HS2/HS2X generation. MBES processor profiles and handoff packages describe external processing requirements; selecting a profile does not install a decoder.\n\n'
                'Device output rate\nThe saved rate field documents the requested device rate. Configure the instrument itself to change its output frequency. RAW acquisition retains all received messages.\n\n'
                'Validation\nPhysical instruments and import into HYPACK still require field/bench validation.')
        def show_sbes_panel(self):
            existing=getattr(self,'_sbes_window',None)
            if existing is not None:
                existing.show();existing.raise_();existing.activateWindow();return existing
            dialog=W.QDialog(self);dialog.setWindowTitle('SBES — Single Beam Survey');dialog.resize(780,460)
            dialog.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose)
            layout=W.QVBoxLayout(dialog)
            title=W.QLabel('Single Beam Survey');title.setStyleSheet('font-size:20px;font-weight:600');layout.addWidget(title)
            status=W.QLabel();status.setWordWrap(True);layout.addWidget(status)
            readings=W.QLabel();readings.setWordWrap(True);readings.setStyleSheet('padding:10px;background:#e6f1f5;color:#123142;font-size:14px');layout.addWidget(readings)
            record_row=W.QHBoxLayout();start=W.QPushButton('Start RAW recording');stop=W.QPushButton('Stop and save')
            start.setMinimumHeight(38);stop.setMinimumHeight(38)
            start.setStyleSheet('QPushButton:enabled {background:#087e8b;color:white;font-weight:600;padding:8px;border-radius:4px;}')
            record_row.addWidget(start);record_row.addWidget(stop);layout.addLayout(record_row)
            scroll=W.QScrollArea();scroll.setWidgetResizable(True);content=W.QWidget();grid=W.QGridLayout(content);layout.addWidget(scroll);scroll.setWidget(content)
            def tool(name):return lambda:self._tool(name)
            groups=[
                ('Live instruments', [('I/O Monitor',tool('show_io_monitor')),('Data Display',tool('show_data_display'))]),
                ('Soundings and recording', [('Live echogram',tool('show_live_echogram_display')),('Recording health',self.show_recording_health)])]
            for index,(heading,actions) in enumerate(groups):
                box=W.QGroupBox(heading);buttons=W.QVBoxLayout(box)
                for label,fn in actions:
                    button=W.QPushButton(label);button.clicked.connect(lambda checked=False,f=fn:widgets.call(f));buttons.addWidget(button)
                buttons.addStretch();grid.addWidget(box,index//2,index%2)
            def refresh():
                h=acq.health();latest=context['latest_sensor_data'];project=core.project_root_from_raw_folder(context['settings'].raw_folder)
                devices=context['configured_devices']()
                connections=' | '.join(f'{name}: {state}' for name,state in h['devices'].items()) or 'Monitoring stopped'
                status.setText(f'Project: {project}\n{connections}')
                def value(key,suffix=''):
                    v=latest.get(key)
                    try:return f'{float(v):,.3f}{suffix}' if v is not None else 'Waiting for data'
                    except (TypeError,ValueError):return 'Waiting for data'
                age=h['last_data_age_s'];age_text='No messages received' if age is None else f'Last message {age:.1f} s ago'
                readings.setText(f'Raw depth: {value("raw_depth"," m")}     Easting: {value("easting")}     Northing: {value("northing")}\n'
                    f'{"RECORDING" if h["recording"] else "NOT RECORDING"} · {h["metrics"]["recorded"]:,} messages recorded · {age_text}')
                if h['error']:readings.setText(readings.text()+'\nRecording error: '+h['error'])
                start.setEnabled(not h['recording'] and bool(devices));start.setToolTip('Enable a GNSS or SBES device in hardware settings.' if not devices else 'Record all incoming source messages to HYPACK-style RAW.')
                stop.setEnabled(h['recording'] or any(t.is_alive() for t in context['io_monitor_threads']))
            def record(fn):widgets.call(fn);refresh()
            start.clicked.connect(lambda:record(self.start_logging));stop.clicked.connect(lambda:record(self.stop_logging_and_save))
            timer=C.QTimer(dialog);timer.timeout.connect(refresh);timer.start(500)
            dialog._status_label=status;dialog._readings_label=readings;dialog._refresh=refresh
            self._sbes_window=dialog;dialog.destroyed.connect(lambda:setattr(self,'_sbes_window',None))
            refresh();dialog.show();return dialog
        def show_mbes_panel(self):return self._tool('show_mbes_dialog')
        def show_geocalc_panel(self):return core.show_geodetic_tools_dialog(context['root'])
        def show_sb_edit_panel(self):return self._tool('show_sb_edit')
        def show_ss510_panel(self):return self._tool('show_ss510_control_panel')
        def show_line_editor_panel(self):return self._open_line_editor()
        def show_border_panel(self):return self._tool('show_border_dialog')
        def show_enc_panel(self):return self.show_enc_editor()
        def show_kmz_panel(self):return self._tool('show_kmz_dialog')
        def show_clip_by_border_panel(self):return self._tool('clip_lines_by_border_dialog')
        def show_background_panel(self):return self.show_background_manager()
        def show_data_display(self):return self._tool('show_data_display')
        def show_io_monitor(self):return self._tool('show_io_monitor')
        def show_echogram_panel(self):return self._tool('show_live_echogram_display')
        def start_logging(self):return self._tool('start_logging')
        def stop_logging_and_save(self):return self._tool('stop_logging_and_save')
        def show_theme_panel(self):return self._tool('show_theme_dialog')
        def show_toolbar_panel(self):return self._tool('show_toolbar_settings')
        def launch_classic_0701(self):
            if hasattr(self,'pages'):self.pages.setCurrentIndex(1)
        def show_help_panel(self):
            self.show_text_window('HYDROGEOMATICS — Help','One application, one survey session.\n\nMap & CAD provides the newer plotting, measurement, DXF vertex editing and matrix KTD tools. Survey planning & processing contains the complete original survey workflow rendered with native Qt widgets.\n\nConfigure hardware and coordinates in Settings. Start/Stop Logging records HYPACK ASCII .RAW files incrementally, with UTC time tags, MSG source observations and typed POS/EC1/GYR/RAW records. MBES native packet logging remains in Multi-Beam Sys.\n\nUse SB EDIT for corrections and exports. Raw depths are preserved. GGA ellipsoidal height requires both altitude and geoid separation.\n\nOptional: ezdxf for DXF, GDAL for ENC/S-57, ODA File Converter for DWG. HYPACK import and live hardware commissioning must be checked on your installation.')
        def show_about_panel(self):self.show_text_window('About HYDROGEOMATICS','HYDROGEOMATICS — HYDROGEOMATICS\nMerged from the supplied 0701.py and sept102026.py.\nPyQt6 interface; original survey workflows retained; shared acquisition and HYPACK ASCII recording.')
        def _install_menus(self):
            self.menuBar().clear();self._workflow_actions={}
            def tool(name):return lambda:self._tool(name)
            def add(menu,label,fn):
                action=menu.addAction(label);action.triggered.connect(lambda checked=False:widgets.call(fn));self._workflow_actions[label]=action;return action
            menus={name:self.menuBar().addMenu(name) for name in ('Project','Settings','Survey','Planning','Backgrounds','Processing','View','Help')}
            entries={
                'Project':[('Projects',self.show_projects_panel),('Load survey data',tool('load_files')),('Open RAW folder',self.open_raw_folder),('Review recording session / verify files',self.review_session),('Exit',self.close)],
                'Settings':[('Hardware Settings',self.show_hardware_settings),('Project / geodetic / corrections',self.show_settings_panel),('Display theme',self.show_theme_panel)],
                'Survey':[('Single beam (SBES)',self.show_sbes_panel),('Multi-beam (MBES)',self.show_mbes_panel),('SS510 instrument controls',self.show_ss510_panel)],
                'Planning':[('Line Editor',self.show_line_editor_panel),('Generate runlines',tool('generate_runlines_dialog')),('Load runlines',tool('load_runlines')),('Save runlines',tool('save_runlines')),('Border Editor',self.show_border_panel),('Four-corner border',lambda:modern.show_four_corner_border(self)),('Clip lines by border',self.show_clip_by_border_panel)],
                'Backgrounds':[('Background manager',self.show_background_manager),('Add ENC backgrounds…',self.add_enc_backgrounds),('ENC Editor / chart viewer',self.show_enc_editor),('KML / KMZ tools',self.show_kmz_panel),('DWG conversion (ODA)',self.convert_dwg_files)],
                'Processing':[('Dredge Volume / Channel Design',self.show_dredge_volume),('Plot area / IHO graduation',self.show_chart_plot),('SB EDIT',self.show_sb_edit_panel),('KTD editor',self.show_indus_ktd_editor),('GEOCALC',self.show_geocalc_panel),('Export HYPACK RAW',tool('log_hypack_raw'))],
                'View':[('Follow vessel',lambda:self._follow_current_view(True)),('Stop following vessel',lambda:self._follow_current_view(False)),
                        ('Zoom window',lambda:self.set_mode('Zoom Window')),('Fit navigation extents',self.fit_extents)],
                'Help':[('User guide',self.show_help_panel),('Feature availability',self.show_feature_availability),('Disclaimer and terms',self.show_disclaimer_panel),('About',self.show_about_panel)]}
            for name,items in entries.items():
                for label,fn in items:add(menus[name],label,fn)
            self._complete_tools_menu=menus['Settings'] # Internal compatibility; no duplicate tools menu.
            # Remove the older workspace's duplicate global menus and button rows.
            for bar in context['root'].q.findChildren(W.QMenuBar):bar.hide()
            self._legacy_global_rows=[]
            for child in list(context['root'].children):
                texts={c.options.get('text','') for c in child.children}
                if 'SBES' in texts or any(str(t).startswith('HYDROGEOMATICS') for t in texts):
                    child.pack_forget();self._legacy_global_rows.append(child)
            # Keep the map sidebar for layers/status; commands have one home.
            map_page=self.pages.widget(0);layout=map_page.layout()
            row=layout.itemAt(1).layout()
            if row is not None:
                while row.count():
                    item=row.takeAt(0)
                    if item.widget():item.widget().hide();item.widget().deleteLater()
                layout.removeItem(row)
            for button in map_page.findChildren(W.QPushButton):button.hide()
            splitter=map_page.findChild(W.QSplitter)
            if splitter:
                self.navigation_splitter=splitter;sidebar=splitter.widget(0);self.navigation_sidebar=sidebar
                sidebar.setMinimumWidth(140);sidebar.setMaximumWidth(16777215)
                splitter.setHandleWidth(10);splitter.setCollapsible(0,True);splitter.setCollapsible(1,False)
                splitter.setStyleSheet('QSplitter::handle {background:#cbd5e1;} QSplitter::handle:hover {background:#087e8b;}')
                for widget in (self.layer_list,self.event_log,self.crs_box):
                    widget.setMinimumWidth(0);widget.setSizePolicy(W.QSizePolicy.Policy.Ignored,W.QSizePolicy.Policy.Expanding if widget is not self.crs_box else W.QSizePolicy.Policy.Fixed)
                side_layout=sidebar.layout()
                for label in sidebar.findChildren(W.QLabel):
                    if label.text() in ('Map Layers','Survey Logger Events'):side_layout.removeWidget(label);label.hide()
                side_layout.removeWidget(self.layer_list);side_layout.removeWidget(self.event_log)
                self.event_log.setMaximumHeight(16777215)
                vertical=W.QSplitter(C.Qt.Orientation.Vertical,sidebar);vertical.setHandleWidth(8)
                for title,widget in [('Loaded CAD / KTD files',self.layer_list),('Survey event logger',self.event_log)]:
                    panel=W.QWidget();panel_layout=W.QVBoxLayout(panel);panel_layout.setContentsMargins(0,0,0,0)
                    panel_layout.addWidget(W.QLabel(title));panel_layout.addWidget(widget);vertical.addWidget(panel)
                vertical.setSizes([450,150]);side_layout.insertWidget(1,vertical,1);self.sidebar_splitter=vertical
                splitter.setSizes([260,1100]);self._sidebar_width=260
                self.sidebar_action=menus['View'].addAction('Show files and event logger');self.sidebar_action.setCheckable(True);self.sidebar_action.setChecked(True)
                self.sidebar_action.toggled.connect(self._toggle_navigation_sidebar)
                splitter.splitterMoved.connect(lambda *_:self._sidebar_resized())
            # Contextual map commands: compact groups, no duplicate sidebar buttons.
            self.main_toolbar.clear();self.main_toolbar.setWindowTitle('Map navigation');self.main_toolbar.setMovable(False)
            def command(label,fn):
                action=G.QAction(label,self);action.triggered.connect(lambda checked=False:widgets.call(fn));return action
            self.main_toolbar.addAction(self.sidebar_action)
            self.main_toolbar.addAction(command('ENC Editor',self.show_enc_editor))
            self.main_toolbar.addAction(command('Dredge Volume',self.show_dredge_volume))
            self.main_toolbar.addAction(command('Plot area',self.show_chart_plot))
            self.main_toolbar.addAction(command('Pan',lambda:self.set_mode('Pan')))
            self.main_toolbar.addAction(command('Measure',lambda:self.set_mode('Measure')))
            self.zoom_toolbar=W.QToolBar('Zoom controls',self);self.zoom_toolbar.setMovable(False)
            self.addToolBarBreak(C.Qt.ToolBarArea.TopToolBarArea);self.addToolBar(C.Qt.ToolBarArea.TopToolBarArea,self.zoom_toolbar)
            for label,fn in [('＋ Zoom in',lambda:self.zoom_view(1.35)),('− Zoom out',lambda:self.zoom_view(1/1.35)),('Zoom window',lambda:self.set_mode('Zoom Window')),('Fit all',self.fit_extents),('Fit survey lines',self.fit_survey_lines),('Previous view',self.zoom_previous)]:
                button=W.QPushButton(label);button.setMinimumHeight(44);button.setMinimumWidth(115)
                button.setStyleSheet('font-size:15px;font-weight:600;padding:8px 14px;')
                button.clicked.connect(lambda checked=False,f=fn:widgets.call(f));self.zoom_toolbar.addWidget(button)
            for title,items in [
                                ('CAD edit',[('Edit vertices',lambda:self.set_mode('Edit DXF')),('DXF edit window',self.show_dxf_edit_window),('Save edited DXF',self.save_edited_linework)]),
                                ('Move view',[('West ←',lambda:self.pan_view(-.2,0)),('East →',lambda:self.pan_view(.2,0)),('North ↑',lambda:self.pan_view(0,.2)),('South ↓',lambda:self.pan_view(0,-.2))])]:
                button=W.QToolButton();button.setText(title);button.setPopupMode(W.QToolButton.ToolButtonPopupMode.InstantPopup);menu=W.QMenu(button)
                for label,fn in items:menu.addAction(command(label,fn))
                button.setMenu(menu);self.main_toolbar.addWidget(button)

            for sequence,fn in [('Ctrl+S',self.start_logging),('Ctrl+E',self.stop_logging_and_save),('Ctrl+I',lambda:self._tool('select_next_line')),('Ctrl+D',lambda:self._tool('select_previous_line')),('Ctrl+W',lambda:self._tool('swap_selected_line_direction'))]:
                action=G.QAction(self);action.setShortcut(G.QKeySequence(sequence));action.triggered.connect(fn);self.addAction(action)
            self.view.setFocusPolicy(C.Qt.FocusPolicy.StrongFocus)
            self._pan_shortcuts=[]
            for key,dx,dy in [('Left',-.2,0),('Right',.2,0),('Up',0,.2),('Down',0,-.2)]:
                shortcut=G.QShortcut(G.QKeySequence(key),self.view)
                shortcut.setContext(C.Qt.ShortcutContext.WidgetWithChildrenShortcut)
                shortcut.setAutoRepeat(True)
                shortcut.activated.connect(lambda x=dx,y=dy:self.pan_view(x,y))
                self._pan_shortcuts.append(shortcut)
            self.view.setToolTip('Click the map, then use arrow keys to move the view. Up = north, Down = south.')
            for sc in context['root'].shortcuts.values():sc.setEnabled(False)
        def pan_view(self,dx_fraction,dy_fraction):
            if getattr(self,'_follow_live',False):self.unfollow_vessel()
            modern.MainWindow.pan_view(self,dx_fraction,dy_fraction)
        def _toggle_navigation_sidebar(self,visible):
            sizes=self.navigation_splitter.sizes()
            if not visible and sizes[0]>0:self._sidebar_width=sizes[0]
            self.navigation_sidebar.setVisible(visible)
            if visible:self.navigation_splitter.setSizes([self._sidebar_width,max(200,self.navigation_splitter.width()-self._sidebar_width)])
        def _sidebar_resized(self):
            width=self.navigation_splitter.sizes()[0]
            if width>0:self._sidebar_width=width
            self.sidebar_action.blockSignals(True);self.sidebar_action.setChecked(width>0);self.sidebar_action.blockSignals(False)
        def open_raw_folder(self):
            path=Path(context['settings'].raw_folder);path.mkdir(parents=True,exist_ok=True)
            G.QDesktopServices.openUrl(C.QUrl.fromLocalFile(str(path.resolve())))
        def update_sensor_alerts(self):
            if not hasattr(self,'_sensor_outages'):self._sensor_outages=set();self._sensor_alarm=None
            new_loss=False
            while not acq.sensor_events.empty():
                name,state=acq.sensor_events.get_nowait()
                if state=='lost':self._sensor_outages.add(name);new_loss=True
                else:self._sensor_outages.discard(name)
                self.event_log.appendPlainText(f"Sensor {name}: {state}")
            if not self._sensor_outages:
                if self._sensor_alarm:self._sensor_alarm.hide()
                return
            if self._sensor_alarm is None:
                self._sensor_alarm=W.QMessageBox(self)
                self._sensor_alarm.setWindowTitle('Sensor data loss — 15 seconds')
                self._sensor_alarm.setIcon(W.QMessageBox.Icon.Warning)
                self._sensor_alarm.setStandardButtons(W.QMessageBox.StandardButton.Ok)
                self._sensor_alarm.setModal(False)
            self._sensor_alarm.setText('No incoming data for at least 15 seconds:\n'+ '\n'.join(sorted(self._sensor_outages)))
            self._sensor_alarm.setInformativeText('Reconnecting automatically. Recording remains active for available sensors. Check sensor power and cables if data does not return.')
            if new_loss:
                self._sensor_alarm.show();self._sensor_alarm.raise_()
                try:
                    import winsound
                    winsound.PlaySound('SystemExclamation',winsound.SND_ALIAS | winsound.SND_ASYNC)
                except Exception:W.QApplication.beep()
        def update_health_badge(self):
            self.update_sensor_alerts()
            h=acq.health();issue=h['error'] or h['checkpoint_error']
            age=h['last_data_age_s'];free=h['free_bytes']
            if issue:text='Recording error — open health';color='#b91c1c'
            elif h['recording'] and (age is None or age>5):text='Waiting for device data';color='#b45309'
            elif h['recording'] and h['last_fix_age_s'] is not None and h['last_fix_age_s']>5:text='GNSS position is stale';color='#b45309'
            elif h['recording'] and free is not None and free<200*1024*1024:text='Low disk space';color='#b45309'
            elif h['recording']:text=f"{h['metrics']['recorded']:,} recorded · queue {h['queue']:,}";color='#15803d'
            else:text='Recording health: idle';color='#475569'
            self.health_badge.setText(text);self.health_badge.setStyleSheet('color:'+color)
            self.health_badge.setToolTip('Survey → Single beam (SBES) → Recording health')
        def show_recording_health(self):
            if self._health_window is not None:
                self._health_window.show();self._health_window.raise_();return
            dialog=W.QDialog(self);dialog.setWindowTitle('Recording health');dialog.resize(760,560)
            dialog.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose)
            layout=W.QVBoxLayout(dialog);text=W.QPlainTextEdit();text.setReadOnly(True);layout.addWidget(text)
            button=W.QPushButton('Open RAW folder');button.clicked.connect(self.open_raw_folder);layout.addWidget(button)
            def refresh():
                h=acq.health();fmt=lambda a:'not received' if a is None else f'{a:.1f} seconds ago'
                lines=[f"Recording: {'ON' if h['recording'] else 'OFF'}",f"Last data: {fmt(h['last_data_age_s'])}",f"Last valid position: {fmt(h['last_fix_age_s'])}",f"Pending records: {h['queue']:,}",f"Free disk space: {h['free_bytes']/1024**3:.2f} GiB" if h['free_bytes'] is not None else 'Free disk space: unavailable','',
                       'Counters since recording start (or monitor start):']
                lines.extend(f'  {k.replace("_"," ")}: {v:,}' for k,v in h['metrics'].items())
                lines+=['','Device connections:']+[f'  {k}: {v}' for k,v in h['devices'].items()]
                lines+=['','RAW files:']+h['files']+['','Session metadata:',h['session'] or '(none)']
                if h['error']:lines+=['','Recording error: '+h['error']]
                if h['checkpoint_error']:lines+=['','Checkpoint error: '+h['checkpoint_error']]
                lines+=['','Unparsed includes unsupported messages and invalid/incomplete input; these are still retained as MSG records while recording.']
                text.setPlainText('\n'.join(lines))
            timer=C.QTimer(dialog);timer.timeout.connect(refresh);timer.start(1000);refresh()
            self._health_window=dialog;dialog.destroyed.connect(lambda:setattr(self,'_health_window',None));dialog.show()
        def review_session(self):
            name,_=W.QFileDialog.getOpenFileName(self,'Review recording session',str(Path(context['settings'].raw_folder)),'HYDROGEOMATICS session (*.session.json)')
            if not name:return
            try:report=engine.inspect_session(name)
            except (OSError,ValueError,TypeError) as exc:W.QMessageBox.warning(self,'Session review',str(exc));return
            dialog=W.QDialog(self);dialog.setWindowTitle('Recording session review');dialog.resize(820,580)
            dialog.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose)
            layout=W.QVBoxLayout(dialog);text=W.QPlainTextEdit();text.setReadOnly(True);text.setPlainText(report);layout.addWidget(text)
            verify=W.QPushButton('Verify recorded file checksums');layout.addWidget(verify)
            alive=[True];dialog.destroyed.connect(lambda:alive.__setitem__(0,False))
            def run():
                import threading
                verify.setEnabled(False);text.appendPlainText('\nVerifying files…')
                def worker():
                    try:result=engine.inspect_session(name,verify=True)
                    except Exception as exc:result='Verification failed: '+str(exc)
                    def finish():
                        if alive[0]:text.setPlainText(result);verify.setEnabled(True)
                    widgets.DISPATCH.requested.emit(finish)
                threading.Thread(target=worker,daemon=True).start()
            verify.clicked.connect(run);dialog.show()
        @staticmethod
        def _background_key(path):return str(Path(path).resolve()).casefold() if path else ''
        def _background_preferences(self):return self.background_state_path().with_name('disabled_backgrounds.json')
        def _save_disabled_backgrounds(self):
            engine.atomic_json(self._background_preferences(),{'disabled':sorted(getattr(self,'_disabled_backgrounds',set()))})
        def _load_background_shapes(self,path):
            path=Path(path);suffix=path.suffix.lower()
            if suffix=='.encpack':
                import tempfile
                with tempfile.TemporaryDirectory(prefix='HYDROGEOMATICS-background-') as folder:
                    manifest=self.enc_editor_module().verify_enc_collection(path,folder)
                    shapes=[]
                    for cell in manifest['cells']:
                        shapes.extend(self._load_background_shapes(Path(folder)/cell['base']))
                    return shapes
            if suffix in ('.kml','.kmz','.000','.enc','.s57'):
                raw=core.load_kmz_shapes(path) if suffix in ('.kml','.kmz') else core.load_enc_s57_shapes(path)
                raw=context['project_background_shapes'](raw)
                shapes=[modern.Shape('line' if s.kind=='polygon' else s.kind,list(s.points)+([s.points[0]] if s.kind=='polygon' and s.points else []),s.text,s.color) for s in raw]
                if suffix in ('.000','.enc','.s57') and shapes:
                    shapes[0]._enc_features=self.enc_editor_module().read_chart_features(path)
                return shapes
            if suffix in ('.raw','.log','.nmea','.1','.2','.3','.4','.5'):
                points=context['load_project_background_points'](path)
                return [modern.Shape('point',[(p.x,p.y)],'' if p.raw_depth is None else f'{p.raw_depth:.2f}', '#0369a1') for p in points]
            if suffix=='.brd':
                points=core.load_hypack_brd_points(path)
                return [modern.Shape('line',points+([points[0]] if points else []),path.stem)] if points else []
            if suffix in ('.tif','.tiff'):raise ValueError('GeoTIFF raster rendering is not implemented in this build.')
            return map_shapes_loader(path)
        def autoload_project_layers(self):
            import json
            try:
                payload=json.loads(self._background_preferences().read_text(encoding='utf-8'))
                self._disabled_backgrounds=set(payload.get('disabled',[]))
            except FileNotFoundError:self._disabled_backgrounds=set()
            except (OSError,ValueError,TypeError) as exc:
                self._disabled_backgrounds=set();self.log_event('Could not read background visibility: '+str(exc))
            # Use the same readers for restoring and adding files.
            previous=modern.load_shapes;modern.load_shapes=self._load_background_shapes
            try:modern.MainWindow.autoload_project_layers(self)
            finally:modern.load_shapes=previous
            for layer in list(self.layers):
                if self._background_key(layer.path) in self._disabled_backgrounds:
                    self.layers.remove(layer);self.view.remove_layer_items(layer.group)
            legacy=[row for row in self.background_catalog() if row['scope']=='survey' and self._background_key(row.get('path')) in self._disabled_backgrounds]
            if legacy:self.remove_backgrounds(legacy)
            self._rebuild_background_list()
            self.ensure_enc_display()
        def _rebuild_background_list(self):
            self.layer_list.blockSignals(True)
            try:
                self.layer_list.clear()
                for layer in self.layers:
                    if layer.hidden:continue
                    item=W.QListWidgetItem(layer.name);item.setFlags(item.flags()|C.Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(C.Qt.CheckState.Checked if layer.visible else C.Qt.CheckState.Unchecked);self.layer_list.addItem(item)
            finally:self.layer_list.blockSignals(False)
        def add_background_files(self,paths,prepared=None):
            errors=[];added=0
            for value in paths:
                path=Path(value);key=self._background_key(path)
                if any(key in (self._background_key(layer.path),self._background_key(getattr(layer,'_source_path',None))) for layer in self.layers):continue
                try:
                    shapes=prepared[str(path)] if prepared is not None else self._load_background_shapes(path)
                    if not shapes:raise ValueError('No drawable positions or geometry were found.')
                    layer=modern.Layer(path.name,path,shapes);layer._source_path=path
                    self.add_layer(layer)
                    disabled=getattr(self,'_disabled_backgrounds',set());disabled.discard(key);disabled.discard(self._background_key(layer.path));self._disabled_backgrounds=disabled
                    added+=1
                except Exception as exc:errors.append(f'{path.name}: {exc}')
            self._save_disabled_backgrounds();self._refresh_background_views()
            self.ensure_enc_display()
            if added:
                self.fit_extents()
                if self.pages.currentIndex()==1:context['fit_view']()
            return added,errors
        def start_background_load(self,paths):
            # Workers produce plain geometry only. Qt objects are owned by this GUI thread.
            import threading,queue
            if getattr(self,'_background_job',None) is not None:
                self._background_job['progress'].show();return False
            paths=[Path(p) for p in paths]
            results=queue.Queue();cancel=threading.Event()
            progress=W.QProgressDialog('Reading background files…','Cancel',0,0,self)
            progress.setWindowTitle('Loading backgrounds');progress.setMinimumDuration(0)
            progress.setAutoClose(False);progress.setAutoReset(False)
            job={'progress':progress,'cancel':cancel};self._background_job=job
            timer=C.QTimer(self);job['timer']=timer
            def worker():
                for path in paths:
                    if cancel.is_set():break
                    results.put(('reading',path,None))
                    try:
                        # Core format readers use shared survey state; run those on the GUI thread.
                        shapes=map_shapes_loader(path) if path.suffix.lower() in {'.dxf','.hpdxf','.dwg','.xyz','.csv','.txt','.ktd','.ktdp','.hpktd'} else None
                        if not cancel.is_set():results.put(('ready',path,shapes))
                    except Exception as exc:results.put(('error',path,str(exc)))
                results.put(('done',None,None))
            counts=[0];errors=[]
            def finish():
                timer.stop();timer.deleteLater();progress.close();progress.deleteLater()
                self._background_job=None
                dialog=getattr(self,'_background_window',None)
                if dialog is not None:
                    dialog._refresh()
                    dialog._status.setText(('Loading cancelled. ' if cancel.is_set() else '')+f'Loaded {counts[0]} file(s).'+ ('\n'+'\n'.join(errors) if errors else ''))
            def poll():
                if cancel.is_set():finish();return
                try:kind,path,value=results.get_nowait()
                except queue.Empty:return
                if kind=='done':finish();return
                if kind=='reading':progress.setLabelText(f'Reading {path.name}…');return
                if kind=='error':errors.append(f'{path.name}: {value}');return
                progress.setLabelText(f'Drawing {path.name}…')
                try:
                    added,failed=self.add_background_files([path],None if value is None else {str(path):value})
                    counts[0]+=added;errors.extend(failed)
                except Exception as exc:errors.append(f'{path.name}: {exc}')
                dialog=getattr(self,'_background_window',None)
                if dialog is not None:dialog._refresh()
            progress.canceled.connect(cancel.set)
            timer.timeout.connect(poll);timer.start(50);progress.show()
            threading.Thread(target=worker,name='background-reader',daemon=True).start()
            return True
        def background_catalog(self):
            context.update(context['_refresh_state']());rows=[]
            for layer in self.layers:
                rows.append({'scope':'map','layer':layer,'name':layer.name,'path':str(layer.path or ''),'kind':Path(layer.path).suffix.upper().lstrip('.') if layer.path else 'Geometry','visible':layer.visible})
            settings=context['settings']
            for key,label,path in [('dxf_background_shapes','Survey CAD',settings.dxf_background_path),('enc_background_shapes','Survey ENC',settings.enc_background_path),('kmz_background_shapes','Survey KML/KMZ',settings.kmz_background_path),('ktd_extent_shapes','Survey KTD outlines','')]:
                shapes=[s for s in context[key] if not getattr(s,'_map_copy',False)]
                by_source={}
                for shape in shapes:by_source.setdefault(str(shape.attrs.get('SOURCE') or path or ''),[]).append(shape)
                for source,objects in by_source.items():
                    rows.append({'scope':'survey','key':key,'objects':objects,'name':Path(source).name if source else label,'path':source,'kind':label,'visible':True})
            if context['project_background_points']:
                by_source={};files=context['project_background_files']
                for point in context['project_background_points']:
                    source=str(point.source or '')
                    matches=[f for f in files if str(f).casefold() in source.casefold() or Path(f).name.casefold() in source.casefold()]
                    matched=matches[0] if len(matches)==1 else ''
                    by_source.setdefault(matched,[]).append(point)
                for source,objects in by_source.items():
                    rows.append({'scope':'survey','key':'project_background_points','objects':objects,'name':Path(source).name if source else 'Other survey background points','path':source,'kind':'Soundings / KTD','visible':True})
            return rows
        def _refresh_background_views(self):
            context.update(context['_refresh_state']());self._send_layers_to_survey()
            context['rebuild_background_cache'](context['dxf_background_shapes'],context['enc_background_shapes'],context['kmz_background_shapes'],context['ktd_extent_shapes'])
            self._snapshot_signature=None
            if self.pages.currentIndex()==1:context['draw_view']()
            self.apply_background_order()
            self.tick();self._rebuild_background_list()
            self.save_background_state()
        def remove_backgrounds(self,rows):
            context.update(context['_refresh_state']())
            disabled=getattr(self,'_disabled_backgrounds',set())
            for row in rows:
                if row['scope']=='map':
                    layer=row['layer']
                    if layer not in self.layers:continue
                    for path in (layer.path,getattr(layer,'_source_path',None)):
                        if path:disabled.add(self._background_key(path))
                    self.layers.remove(layer);self.view.remove_layer_items(layer.group)
                else:
                    key=row['key'];removed={id(obj) for obj in row.get('objects',context[key])}
                    context[key][:]=[obj for obj in context[key] if id(obj) not in removed]
                    setting={'dxf_background_shapes':'dxf_background_path','enc_background_shapes':'enc_background_path','kmz_background_shapes':'kmz_background_path'}.get(key)
                    if setting and not [s for s in context[key] if not getattr(s,'_map_copy',False)]:setattr(context['settings'],setting,'')
                    if key=='project_background_points':
                        if not context[key]:context['project_background_files'].clear()
                        elif row.get('path'):context['project_background_files'][:]=[f for f in context['project_background_files'] if self._background_key(f)!=self._background_key(row['path'])]
                    state=context.get('background_file_state',{})
                    if row.get('path'):
                        disabled.add(self._background_key(row['path']));state.pop(self._background_key(row['path']),None)
            self._disabled_backgrounds=disabled;self._save_disabled_backgrounds()
            context['save_default_settings']();self._refresh_background_views()
        def unload_selected(self):
            layer=self.layer_for_list_row(self.layer_list.currentRow())
            if layer is not None:self.remove_backgrounds([r for r in self.background_catalog() if r.get('layer') is layer])
        def clear_layers(self):
            job=getattr(self,'_background_job',None)
            if job is not None:job['cancel'].set()
            self.remove_backgrounds(self.background_catalog())
        def fit_backgrounds(self):
            self._refresh_background_views();context['fit_view']();self.view.autoRange()
        def layer_visibility_changed(self,item):
            modern.MainWindow.layer_visibility_changed(self,item)
            self.apply_background_order()
            if context and hasattr(self,'refresh_timer'):self._refresh_background_views()
        def add_layer(self, layer):
            names = list(dict.fromkeys(getattr(s, 'cad_layer', '') for s in layer.shapes if hasattr(s, 'cad_layer')))
            priority = {}
            for s in layer.shapes:
                if hasattr(s, 'cad_layer'):
                    rank = 0 if s.kind == 'cad_fill' else (2 if s.kind == 'text' else 1)
                    priority[s.cad_layer] = max(priority.get(s.cad_layer, 0), rank)
            defaults = sorted(names, key=lambda n: priority.get(n, 1))
            saved = getattr(layer, 'cad_order', [])
            layer.cad_order = [n for n in saved if n in names] + [n for n in defaults if n not in saved]
            layer.cad_visibility = getattr(layer, 'cad_visibility', {})
            for s in layer.shapes:
                if hasattr(s, 'cad_layer'):
                    layer.cad_visibility.setdefault(s.cad_layer, s.cad_visible)
            modern.MainWindow.add_layer(self, layer)
            self.apply_background_order()

        def apply_background_order(self):
            # Keep backgrounds below live soundings, runlines and vessel graphics.
            layers = getattr(self, 'layers', [])
            for index, layer in enumerate(layers):
                order = getattr(layer, 'cad_order', [])
                ranks = {name:i for i,name in enumerate(order)}
                for item in list(layer.group or []):
                    name = getattr(item, '_cad_layer', None)
                    local = (ranks.get(name, 0) + getattr(item, '_cad_kind', 0)/10) / (len(order)+1)
                    if hasattr(item,'_enc_z'):local=(item._enc_z+101)/200
                    item.setZValue(-10000 + (index + local) * 9999 / (len(layers)+1))
                    item.setVisible(layer.visible and (name is None or getattr(layer, 'cad_visibility', {}).get(name, True)))

        def move_background_display(self, layer, cad_name, action):
            seq = self.layers if cad_name is None else layer.cad_order
            target = layer if cad_name is None else cad_name
            index = seq.index(target)
            destination = {'front':len(seq)-1, 'back':0, 'forward':min(len(seq)-1,index+1), 'backward':max(0,index-1)}[action]
            seq.insert(destination, seq.pop(index))
            self.apply_background_order()
            self._refresh_background_views()

        def show_background_manager(self):
            existing=getattr(self,'_background_window',None)
            if existing is not None:existing.show();existing.raise_();return existing
            dialog=W.QDialog(self);dialog.setWindowTitle('Background manager — HYDROGEOMATICS');dialog.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose)
            available=dialog.screen().availableGeometry();dialog.resize(min(1120,int(available.width()*.9)),min(720,int(available.height()*.9)))
            layout=W.QVBoxLayout(dialog)
            info=W.QLabel('Top = FRONT; bottom = BACK. Expand a DXF to select its internal layers. Check boxes show/hide layers. Select a drawing or DXF layer, then change its order. Changes are saved in this project. Live survey graphics stay above backgrounds.')
            info.setWordWrap(True);layout.addWidget(info)
            tree=W.QTreeWidget();tree.setColumnCount(4);tree.setHeaderLabels(['Background / DXF layer (front first)','Type','Colour','File / source']);tree.setRootIsDecorated(True);tree.setSelectionMode(W.QAbstractItemView.SelectionMode.SingleSelection);layout.addWidget(tree)
            status=W.QLabel();status.setWordWrap(True);layout.addWidget(status)
            def refresh(selected=None):
                tree.blockSignals(True)
                try:
                    tree.clear()
                    for row in reversed(self.background_catalog()):
                        item=W.QTreeWidgetItem([row['name'],row['kind'],'',row['path']]);item.setData(0,C.Qt.ItemDataRole.UserRole,row);tree.addTopLevelItem(item)
                        if row['scope']=='map':
                            layer=row['layer'];item.setCheckState(0,C.Qt.CheckState.Checked if layer.visible else C.Qt.CheckState.Unchecked)
                            colors={getattr(s,'cad_layer',''):s.color for s in layer.shapes}
                            for name in reversed(getattr(layer,'cad_order',[])):
                                child=W.QTreeWidgetItem([name,'DXF layer',colors.get(name,''),''])
                                child.setData(0,C.Qt.ItemDataRole.UserRole,{'scope':'cad','layer':layer,'cad_name':name})
                                child.setCheckState(0,C.Qt.CheckState.Checked if layer.cad_visibility.get(name,True) else C.Qt.CheckState.Unchecked)
                                child.setForeground(2,modern.QColor(colors.get(name,'#808080')))
                                item.addChild(child)
                                if selected is not None and selected[0] is layer and selected[1]==name:tree.setCurrentItem(child)
                            item.setExpanded(True)
                            if selected is not None and selected[0] is layer and selected[1] is None:tree.setCurrentItem(item)
                    for col in range(3):tree.resizeColumnToContents(col)
                    status.setText('Blue water fills and yellow island fills are read from the DXF. Drawing order controls overlapping backgrounds; DXF layer order applies within its drawing.')
                finally:tree.blockSignals(False)
            def changed(item,column):
                if column != 0:return
                row=item.data(0,C.Qt.ItemDataRole.UserRole)
                if row['scope'] not in ('map','cad'):return
                layer=row['layer'];visible=item.checkState(0)==C.Qt.CheckState.Checked
                if row['scope']=='cad':layer.cad_visibility[row['cad_name']]=visible
                else:layer.visible=visible
                self.apply_background_order();self._refresh_background_views()
            tree.itemChanged.connect(changed)
            def move(action):
                item=tree.currentItem()
                if item is None:status.setText('Select a drawing or DXF layer first.');return
                row=item.data(0,C.Qt.ItemDataRole.UserRole)
                if row['scope'] not in ('map','cad'):status.setText('This survey overlay is managed by the survey workspace.');return
                layer=row['layer'];name=row.get('cad_name')
                self.move_background_display(layer,name,action);refresh((layer,name))
            order_buttons=W.QHBoxLayout();layout.addLayout(order_buttons)
            for label,action in [('Bring to front','front'),('Move forward','forward'),('Move backward','backward'),('Send to back','back')]:
                button=W.QPushButton(label);button.clicked.connect(lambda checked=False,a=action:widgets.call(move,a));order_buttons.addWidget(button)
            def load():
                names,_=W.QFileDialog.getOpenFileNames(dialog,'Add backgrounds',str(self.project_folder),'Background files (*.dxf *.hpdxf *.dwg *.ktd *.ktdp *.hpktd *.xyz *.csv *.txt *.raw *.RAW *.log *.nmea *.1 *.2 *.3 *.4 *.5 *.kml *.kmz *.000 *.enc *.s57 *.encpack *.brd);;All files (*)')
                if names:self.start_background_load(names)
            def remove(all_rows=False):
                rows=self.background_catalog() if all_rows else [i.data(0,C.Qt.ItemDataRole.UserRole) for i in tree.selectedItems()]
                if any(r['scope']=='cad' for r in rows):status.setText('To hide a DXF layer, clear its check box. Select the parent drawing to unload the file.');return
                if all_rows:self.clear_layers()
                elif rows:self.remove_backgrounds(rows)
                refresh()
            buttons=W.QHBoxLayout();layout.addLayout(buttons)
            for label,fn in [('Add files…',load),('Add ENCs…',self.add_enc_backgrounds),('ENC Editor',self.show_enc_editor),('Unload selected',lambda:remove()),('Unload all',lambda:remove(True)),('Fit extents',self.fit_backgrounds),('Refresh',refresh),('Close',dialog.close)]:
                button=W.QPushButton(label);button.clicked.connect(lambda checked=False,f=fn:widgets.call(f));buttons.addWidget(button)
            dialog._tree=tree;dialog._refresh=refresh;dialog._status=status;self._background_window=dialog
            dialog.destroyed.connect(lambda:setattr(self,'_background_window',None));refresh();dialog.show();return dialog

        def _send_layers_to_survey(self):
            if not hasattr(self,'layers'):return
            imported=[]
            for layer in self.layers:
                if not layer.visible or getattr(layer,'_survey_copy',False):continue
                for s in layer.shapes:
                    if s.kind == "cad_fill" or not getattr(layer,"cad_visibility",{}).get(getattr(s,"cad_layer",""),True):continue
                    imported.append(core.MapShape(kind='point' if s.kind=='ktd' else s.kind,points=list(s.points),text=s.label,color=s.color))
            # Update only map-owned shapes; retain chart attributes from the survey engine.
            current=context['dxf_background_shapes']
            current[:]=[s for s in current if not getattr(s,'_map_copy',False)]
            for s in imported:s._map_copy=True
            current.extend(imported)
            context['rebuild_background_cache'](current,context['enc_background_shapes'],context['kmz_background_shapes'])
        def tick(self):
            for row in getattr(self,'_legacy_global_rows',[]):
                if not row.dead and not row.q.isHidden():row.pack_forget()
            for bar in context['root'].q.findChildren(W.QMenuBar):
                if not bar.isHidden():bar.hide()
            context.update(context['_refresh_state']())
            acq.drain()
            settings=context['settings'];g=settings.geodetic
            context['model'].geodetic=g
            self.project_folder=Path(core.project_root_from_raw_folder(settings.raw_folder));self.ktd_file_path=g.ktd_file_path;self.use_rtk_tide_method=g.rtk_tide_method!='Not using RTK tide'
            self.sound_velocity=context['model'].sound_speed
            self.current_xy=acq.last_xy
            self.live_track_points=acq.live_track;self.live_soundings=acq.live_depth
            self.logging_active=context['logging_active'].get()
            self.logging_badge.setText('LOGGING: ON | Events 1 Hz | RAW full rate' if self.logging_active else 'LOGGING: OFF')
            self.logging_badge.setStyleSheet('font-weight:700;color:'+('#15803d' if self.logging_active else '#991b1b'))
            self.gnss_status_label.setText(' | '.join(f'{k}: {v}' for k,v in acq.states.items()) or 'Devices: idle')
            self.sbes_status_label.setText(f'{len(context["model"].points):,} survey records')
            self.crs_box.setCurrentText(f'{g.projection_name}; CM {engine.projection_parameters(g)[0]:g}°')
            self.refresh_project_label()
            self.status.showMessage(context['status_var'].get())
            sig=(len(acq.live_track),acq.live_track[-1] if acq.live_track else None,len(acq.live_depth),acq.live_depth[-1] if acq.live_depth else None)
            if sig!=self._last_live:
                self._last_live=sig;self.refresh_live_survey_layer()
                if self._follow_live:self._center_live_position()
            # The survey view retains ENC symbology; the overview mirrors its geometry.
            groups=[context['dxf_background_shapes'],context['enc_background_shapes'],context['kmz_background_shapes'],context['ktd_extent_shapes']]
            background_points=context['project_background_points']
            lines=context['runlines'].lines
            signature=(tuple(context['active_border_points']()),len(context['model'].points),tuple((id(group),len(group),id(group[-1]) if group else 0) for group in groups),tuple((l.name,l.start_x,l.start_y,l.end_x,l.end_y,l.enabled) for l in lines),(len(background_points),id(background_points[-1]) if background_points else 0))
            if signature!=self._snapshot_signature:
                self._snapshot_signature=signature
                if hasattr(self,'_survey_overlay'):self.view.remove_layer_items(self._survey_overlay)
                shapes=[]
                for group in groups:
                    for s in group:
                        if getattr(s,'_map_copy',False):continue
                        pts=list(s.points)
                        if s.kind=='polygon' and pts:pts.append(pts[0])
                        shapes.append(modern.Shape('line' if s.kind=='polygon' else s.kind,pts,s.text,s.color))
                for l in lines:shapes.append(modern.Shape('line',[(l.start_x,l.start_y),(l.end_x,l.end_y)],l.name,'#dc2626' if l.enabled else '#94a3b8'))
                border=context['active_border_points']()
                if border:shapes.append(modern.Shape('line',border+[border[0]],'Survey border','#7c3aed'))
                for p in context['model'].points:
                    if p.x is not None and p.y is not None:shapes.append(modern.Shape('point',[(p.x,p.y)],'', '#0369a1'))
                for p in background_points:
                    if p.x is not None and p.y is not None:shapes.append(modern.Shape('point',[(p.x,p.y)],'' if p.raw_depth is None else f'{p.raw_depth:.2f}','#0369a1'))
                self._survey_overlay=self.view.add_shapes_layer(shapes)
            line_geometry=tuple((l.start_x,l.start_y,l.end_x,l.end_y) for l in lines)
            previous=getattr(self,'_visible_runline_geometry',None)
            self._visible_runline_geometry=line_geometry
            if line_geometry and line_geometry!=previous:self.fit_survey_lines()
            self._update_navigation()
        def _install_navigation(self):
            self.navigation_toolbar=W.QToolBar('Survey navigation',self);self.navigation_toolbar.setMovable(False)
            self.addToolBar(C.Qt.ToolBarArea.BottomToolBarArea,self.navigation_toolbar)
            self.navigation_toolbar.addWidget(W.QLabel(' Navigation line: '))
            self.navigation_line=W.QComboBox();self.navigation_line.setMinimumWidth(180)
            self.navigation_toolbar.addWidget(self.navigation_line)
            self.navigation_line.activated.connect(lambda index:self._choose_navigation_line(self.navigation_line.itemData(index)))
            for label,name in [('Previous','select_previous_line'),('Next','select_next_line'),('Reverse','swap_selected_line_direction')]:
                action=self.navigation_toolbar.addAction(label)
                action.triggered.connect(lambda checked=False,n=name:(widgets.call(context[n]),self.tick()))
            follow=self.navigation_toolbar.addAction('Follow boat');follow.setCheckable(True)
            follow.toggled.connect(self._follow_current_view);self._follow_action=follow
            self.guidance=W.QLabel('Waiting for GNSS position');self.guidance.setWordWrap(True)
            self.guidance.setStyleSheet('font-size:14px;padding:8px;background:#e6f1f5;color:#123142')
            dock=W.QDockWidget('Boat guidance',self);dock.setFeatures(W.QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
            dock.setWidget(self.guidance);self.addDockWidget(C.Qt.DockWidgetArea.BottomDockWidgetArea,dock)
            pg=modern.pg
            self.boat_marker=pg.ScatterPlotItem(pxMode=True,size=16,pen=pg.mkPen('#ffffff',width=2),brush=pg.mkBrush('#087e8b'))
            self.active_line_item=pg.PlotDataItem([],[],pen=pg.mkPen('#f59e0b',width=4),symbol='o',symbolSize=7)
            self.guidance_link=pg.PlotDataItem([],[],pen=pg.mkPen('#087e8b',width=1,style=C.Qt.PenStyle.DashLine))
            self.boat_heading=pg.PlotDataItem([],[],pen=pg.mkPen('#087e8b',width=3))
            for item in (self.active_line_item,self.guidance_link,self.boat_heading,self.boat_marker):
                self.view.addItem(item);item.setZValue(100)
            self._navigation_lines=None
            # Planning picks are consumed before pan/measure/CAD mouse handlers.
        def _choose_navigation_line(self,index):
            if index is None or index<0:
                context['selected_line_index'].set(-1);context['use_selected_line'].set(False)
            else:context['select_line_index'](index)
            self._update_navigation()
        def _open_line_editor(self):
            existing=getattr(self,'_line_editor',None)
            if existing is not None:existing.show();existing.raise_();return existing
            import math
            dialog=W.QDialog(self);dialog.setWindowTitle('Line Editor — navigation runlines');dialog.resize(900,600)
            dialog.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose)
            layout=W.QVBoxLayout(dialog)
            info=W.QLabel('Draw a new line with two map clicks, or enter coordinates. Changes appear immediately on the navigation map. Save LNW to keep a file. DXF coordinates must use the project grid; loaded DXF converts displayed straight segments.')
            info.setWordWrap(True);layout.addWidget(info)
            table=W.QTableWidget(0,5);table.setHorizontalHeaderLabels(['Line','Start E','Start N','End E','End N'])
            table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows);table.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection)
            table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers);layout.addWidget(table)
            form=W.QHBoxLayout();layout.addLayout(form);fields=[]
            for label in ['Name','Start E','Start N','End E','End N']:
                form.addWidget(W.QLabel(label));entry=W.QLineEdit();form.addWidget(entry);fields.append(entry)
            message=W.QLabel();message.setWordWrap(True);layout.addWidget(message)
            state=context['view_state'];self._line_editor=dialog
            selection_overlay=modern.pg.PlotDataItem([],[],connect='finite',pen=modern.pg.mkPen('#c026d3',width=5))
            selection_overlay.setZValue(110);self.view.addItem(selection_overlay)
            def highlight_selection():
                xs=[];ys=[];lines=context['runlines'].lines
                for index in table.selectionModel().selectedRows():
                    if index.row()>=len(lines):continue
                    line=lines[index.row()];xs.extend([line.start_x,line.end_x,float('nan')]);ys.extend([line.start_y,line.end_y,float('nan')])
                selection_overlay.setData(xs,ys)
            table.itemSelectionChanged.connect(highlight_selection)
            def refresh():
                lines=context['runlines'].lines
                dialog._line_signature=tuple((id(l),l.name,l.start_x,l.start_y,l.end_x,l.end_y) for l in lines)
                table.blockSignals(True);table.clearSelection();table.setRowCount(len(lines))
                for row,line in enumerate(lines):
                    for col,value in enumerate((line.name,line.start_x,line.start_y,line.end_x,line.end_y)):
                        table.setItem(row,col,W.QTableWidgetItem(str(value)))
                table.blockSignals(False);highlight_selection();self._snapshot_signature=None;self.tick()
            def blank():
                table.clearSelection();table.setCurrentCell(-1,-1)
                used={l.name for l in context['runlines'].lines};index=1
                while f'L{index}' in used:index+=1
                fields[0].setText(f'L{index}')
                for entry in fields[1:]:entry.clear()
            def selected():
                row=table.currentRow();lines=context['runlines'].lines
                if row<0 or row>=len(lines):return
                line=lines[row]
                for entry,value in zip(fields,(line.name,line.start_x,line.start_y,line.end_x,line.end_y)):entry.setText(str(value))
            table.itemSelectionChanged.connect(selected)
            def commit(update=False):
                try:
                    coords=[float(f.text()) for f in fields[1:]]
                    if not all(math.isfinite(v) for v in coords):raise ValueError('Coordinates must be finite numbers.')
                    if math.hypot(coords[2]-coords[0],coords[3]-coords[1])<.005:raise ValueError('Start and end must be different positions.')
                    name=fields[0].text().strip()
                    if not name or '\n' in name or '\r' in name:raise ValueError('Enter a valid line name.')
                    lines=context['runlines'].lines;row=table.currentRow() if update else len(lines)
                    if update and not 0<=row<len(lines):raise ValueError('Select a line to update.')
                    if any(l.name==name for i,l in enumerate(lines) if not(update and i==row)):raise ValueError('Use a unique line name.')
                    line=core.RunLine(name,*coords)
                    if update:line.enabled=lines[row].enabled;lines[row]=line
                    else:lines.append(line)
                    context['refresh_line_selector_options']();context['select_line_index'](row);refresh();table.selectRow(row)
                    message.setText(f'{name} is active on the navigation map. Save LNW to keep a file.');return True
                except ValueError as exc:message.setText(str(exc));return False
            def cancel_pick():
                state['line_pick_callback']=None;state['line_pick_target']=None;state['line_pick_cancel']=None
                dialog.show();dialog.raise_()
            def pick(target=0,two=False):
                if two and target==0:blank()
                state['border_pick_callback']=None
                def receive(x,y):
                    offset=1+target*2;fields[offset].setText(f'{x:.3f}');fields[offset+1].setText(f'{y:.3f}')
                    if two and target==0:pick(1,True);return
                    cancel_pick()
                    if two:commit()
                    else:message.setText('Coordinate picked. Add line or Update selected to apply it.')
                state['line_pick_callback']=receive;state['line_pick_cancel']=cancel_pick;state['line_pick_target']=f'editor:{target}'
                dialog.hide();self.raise_();self.view.setFocus()
                context['status_var'].set('Click line '+('start' if target==0 else 'end')+' on the map. Right-click cancels.')
            def remove():
                rows=sorted({i.row() for i in table.selectionModel().selectedRows()},reverse=True)
                if not rows:message.setText('Select lines in the table (Ctrl/Shift for multiple), or use Select lines on map.');return
                lines=context['runlines'].lines;active=context['selected_navigation_line']()
                dialog._removed_lines=[(row,lines[row]) for row in sorted(rows)]
                for row in rows:lines.pop(row)
                context['selected_line_index'].set(next((i for i,l in enumerate(lines) if l is active),-1))
                context['refresh_line_selector_options']();refresh();blank();message.setText(f'Removed {len(rows)} selected line(s). Undo removal restores them.')
            def undo_remove():
                saved=getattr(dialog,'_removed_lines',[])
                for row,line in saved:context['runlines'].lines.insert(min(row,len(context['runlines'].lines)),line)
                dialog._removed_lines=[];context['refresh_line_selector_options']();refresh()
            def select_on_map():
                def choose(x,y):
                    lines=context['runlines'].lines
                    if not lines:return
                    row,line=min(enumerate(lines),key=lambda pair:((pair[1].navigation(x,y).nearest_x-x)**2+(pair[1].navigation(x,y).nearest_y-y)**2))
                    sol=line.navigation(x,y);ranges=self.view.viewRange()
                    tolerance=12*max((ranges[0][1]-ranges[0][0])/max(1,self.view.width()),(ranges[1][1]-ranges[1][0])/max(1,self.view.height()))
                    if math.hypot(sol.nearest_x-x,sol.nearest_y-y)>tolerance:return
                    index=table.model().index(row,0)
                    table.selectionModel().select(index,C.QItemSelectionModel.SelectionFlag.Toggle|C.QItemSelectionModel.SelectionFlag.Rows)
                    count=len(table.selectionModel().selectedRows());context['status_var'].set(f'{count} line(s) selected. Click to toggle; right-click or Escape to return to the editor.')
                state['border_pick_callback']=None;state['line_pick_callback']=choose;state['line_pick_cancel']=cancel_pick
                dialog.hide();self.raise_();self.view.setFocus()
                context['status_var'].set('Click survey lines to select them. Right-click or Escape returns to Remove selected.')
            def save():
                if not context['runlines'].lines:message.setText('Create or import lines first.');return
                name,_=W.QFileDialog.getSaveFileName(dialog,'Save navigation lines',str(self.project_folder/'survey_lines.lnw'),'HYPACK lines (*.lnw)')
                if not name:return
                path=Path(name)
                if path.suffix.lower()!='.lnw':path=path.with_suffix('.lnw')
                try:count=core.write_hypack_lnw(path,context['runlines'].lines);message.setText(f'Saved {count} lines to {path}')
                except Exception as exc:message.setText(f'Could not save: {exc}')
            def import_shapes(shapes):
                lines,skipped=self._runlines_from_shapes(shapes)
                context['runlines'].lines.extend(lines);context['refresh_line_selector_options']();refresh()
                message.setText(f'Added {len(lines)} straight segments; skipped {skipped} non-line or invalid shapes. Save LNW to create the file.')
            def loaded_dxf():
                layers=[l for l in self.layers if l.path and Path(l.path).suffix.lower() in ('.dxf','.hpdxf','.dwg')]
                if not layers:message.setText('No DXF layers loaded. Use Import DXF file.');return
                chooser=W.QDialog(dialog);chooser.setWindowTitle('Choose loaded DXF layers');box=W.QVBoxLayout(chooser)
                listing=W.QListWidget();listing.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection);box.addWidget(listing)
                for layer in layers:listing.addItem(layer.name)
                buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel);box.addWidget(buttons)
                buttons.accepted.connect(chooser.accept);buttons.rejected.connect(chooser.reject)
                if chooser.exec()==W.QDialog.DialogCode.Accepted:
                    import_shapes([shape for item in listing.selectedItems() for shape in layers[listing.row(item)].shapes])
            def file_dxf():
                name,_=W.QFileDialog.getOpenFileName(dialog,'Convert DXF linework to runlines',str(self.project_folder),'DXF (*.dxf)')
                if not name:return
                import threading,queue
                results=queue.Queue();message.setText('Reading DXF…');file_button.setEnabled(False)
                def worker():
                    try:results.put((self._read_dxf_runline_shapes(Path(name)),None))
                    except Exception as exc:results.put((None,str(exc)))
                def poll():
                    try:data,error=results.get_nowait()
                    except queue.Empty:return
                    timer.stop();timer.deleteLater();file_button.setEnabled(True)
                    if error:message.setText(error)
                    else:
                        shapes,unsupported=data;import_shapes(shapes)
                        message.setText(message.text()+f' DXF entities skipped: {unsupported} (curves, blocks, text or other unsupported entities).')
                timer=C.QTimer(dialog);timer.timeout.connect(poll);timer.start(50)
                threading.Thread(target=worker,daemon=True,name='dxf-runlines').start()
            for actions in [[('New fields',blank),('Draw new line (2 clicks)',lambda:pick(0,True)),('Pick start',lambda:pick(0)),('Pick end',lambda:pick(1)),('Add line',lambda:commit()),('Update selected',lambda:commit(True)),('Remove selected',remove)],
                            [('Select lines on map',select_on_map),('Undo removal',undo_remove),('Fit lines',self.fit_survey_lines)],
                            [('From loaded DXF',loaded_dxf),('Import DXF file',file_dxf),('Save LNW',save),('Parallel lines',lambda:self._tool('generate_runlines_dialog')),('Close',dialog.close)]]:
                row=W.QHBoxLayout();layout.addLayout(row)
                for label,callback in actions:
                    button=W.QPushButton(label);button.clicked.connect(lambda checked=False,f=callback:widgets.call(f));row.addWidget(button)
                    if label=='Import DXF file':file_button=button
            def cleanup(*args):
                self.view.removeItem(selection_overlay)
                state['line_pick_callback']=None;state['line_pick_cancel']=None;state['line_pick_target']=None;self._line_editor=None
            dialog.destroyed.connect(cleanup)
            dialog._fields=fields;dialog._table=table;dialog._message=message;dialog._import_shapes=import_shapes
            def sync_lines():
                signature=tuple((id(l),l.name,l.start_x,l.start_y,l.end_x,l.end_y) for l in context['runlines'].lines)
                if signature!=getattr(dialog,'_line_signature',None):refresh()
            sync_timer=C.QTimer(dialog);sync_timer.timeout.connect(sync_lines);sync_timer.start(200)
            refresh();blank();dialog.show();return dialog
        def _runlines_from_shapes(self,shapes):
            import math,re
            used={l.name for l in context['runlines'].lines};result=[];skipped=0
            for shape in shapes:
                if shape.kind!='line' or len(shape.points)<2:skipped+=1;continue
                prefix=re.sub(r'[^A-Za-z0-9_-]+','_',getattr(shape,'label','') or 'DXF')[:40] or 'DXF'
                for a,b in zip(shape.points,shape.points[1:]):
                    coords=tuple(map(float,(*a[:2],*b[:2])))
                    if not all(math.isfinite(v) for v in coords) or math.hypot(coords[2]-coords[0],coords[3]-coords[1])<.005:skipped+=1;continue
                    index=len(result)+1;name=f'{prefix}_{index}'
                    while name in used:index+=1;name=f'{prefix}_{index}'
                    used.add(name);result.append(core.RunLine(name,*coords))
            return result,skipped
        def _read_dxf_runline_shapes(self,path):
            import ezdxf
            shapes=[];skipped=0
            for entity in ezdxf.readfile(str(path)).modelspace():
                kind=entity.dxftype();points=[]
                if kind=='LINE':points=[tuple(entity.dxf.start)[:2],tuple(entity.dxf.end)[:2]]
                elif kind=='LWPOLYLINE':
                    vertices=list(entity.get_points('xyb'))
                    if any(v[2] for v in vertices):skipped+=1;continue
                    points=[(v[0],v[1]) for v in vertices]
                    if entity.closed and points:points.append(points[0])
                elif kind=='POLYLINE':
                    if not (entity.is_2d_polyline or entity.is_3d_polyline) or any(v.dxf.get('bulge',0) for v in entity.vertices):skipped+=1;continue
                    points=[tuple(v.dxf.location)[:2] for v in entity.vertices]
                    if entity.is_closed and points:points.append(points[0])
                else:skipped+=1;continue
                shapes.append(modern.Shape('line',points,str(entity.dxf.layer)))
            return shapes,skipped
        def _consume_planning_click(self,event,view):
            state=context['view_state']
            callback=state.get('border_pick_callback') or state.get('line_pick_callback')
            if callback is None:return False
            if event.button()==C.Qt.MouseButton.RightButton:
                cancel=state.get('line_pick_cancel')
                if cancel:cancel()
                else:state['line_pick_callback']=None;state['border_pick_callback']=None
                return True
            if event.button()!=C.Qt.MouseButton.LeftButton:return False
            scene=view.mapToScene(event.position().toPoint())
            if not view.plotItem.vb.sceneBoundingRect().contains(scene):return False
            widgets.call(callback,*view.event_to_map_xy(event));self._snapshot_signature=None;self.tick()
            return True
        def _navigation_map_click(self,event):
            if event.button()!=C.Qt.MouseButton.LeftButton:return
            state=context['view_state']
            callback=state.get('border_pick_callback') or state.get('line_pick_callback')
            if callback is None or not self.view.sceneBoundingRect().contains(event.scenePos()):return
            point=self.view.plotItem.vb.mapSceneToView(event.scenePos())
            widgets.call(callback,float(point.x()),float(point.y()));self._snapshot_signature=None;self.tick();event.accept()
        def _update_navigation(self):
            if not hasattr(self,'navigation_line'):return
            import math
            lines=context['runlines'].lines
            signature=tuple((l.name,l.enabled) for l in lines)
            if signature!=self._navigation_lines:
                self._navigation_lines=signature;self.navigation_line.blockSignals(True);self.navigation_line.clear()
                self.navigation_line.addItem('Select a survey line',-1)
                for index,line in enumerate(lines):
                    if line.enabled:self.navigation_line.addItem(line.name,index)
                self.navigation_line.blockSignals(False)
            age=acq.health()['last_fix_age_s'];xy=acq.last_xy
            if xy is not None and age is not None and age<=5 and context['auto_select_nearest_line'].get():context['select_nearest_line_to_position'](*xy)
            selected=context['selected_line_index'].get()
            self.navigation_line.setCurrentIndex(max(0,self.navigation_line.findData(selected)))
            line=context['selected_navigation_line']()
            self.active_line_item.setData([line.start_x,line.end_x] if line else [],[line.start_y,line.end_y] if line else [])
            age=acq.health()['last_fix_age_s'];xy=acq.last_xy
            if xy is None:
                self.boat_marker.setData([],[]);self.boat_heading.setData([],[]);self.guidance_link.setData([],[])
                self.guidance.setText('Waiting for GNSS position • Select a survey line for guidance');return
            fresh=age is not None and age<=5
            x,y=xy;self.boat_marker.setData([x],[y],brush=modern.pg.mkBrush('#087e8b' if fresh else '#9ca3af'))
            latest=context['latest_sensor_data'];heading=latest.get('heading_degrees');heading_kind='Heading'
            if heading is None:heading=latest.get('course_degrees');heading_kind='COG'
            self.boat_heading.setData([],[])
            if fresh and heading is not None and math.isfinite(float(heading)):
                extent=self.view.viewRange();length=max(1,(extent[1][1]-extent[1][0])*.04);angle=math.radians(float(heading))
                self.boat_heading.setData([x,x+length*math.sin(angle)],[y,y+length*math.cos(angle)])
            if not fresh:
                self.guidance_link.setData([],[]);self.guidance.setText('GNSS position stale — guidance paused • Last position E %.2f  N %.2f'%(x,y));return
            text=f'E {x:,.2f}  N {y:,.2f}'
            if heading is not None:text+=f'  |  {heading_kind} {float(heading):.1f}°'
            if latest.get('speed_knots') is not None:text+=f"  |  Speed {latest['speed_knots']:.1f} kn"
            if latest.get('raw_depth') is not None:text+=f"  |  Depth {latest['raw_depth']:.2f} m"
            self.guidance_link.setData([],[])
            if line is not None and line.length>0:
                solution=line.navigation(x,y)
                self.guidance_link.setData([x,solution.nearest_x],[y,solution.nearest_y])
                text+=f'\n{line.name}: bearing {line.bearing_degrees:.1f}°  |  XTE {solution.absolute_cross_track:.2f} m {solution.side}  |  Along {solution.along_track:.1f} m  |  To end {solution.distance_to_end:.1f} m'
            elif line is not None:text+='\nSelected line has zero length — select a valid line.'
            else:text+='\nSelect a survey line for cross-track guidance.'
            self.guidance.setText(text)
        def fit_survey_lines(self):
            lines=context.get('runlines')
            if lines is None or not lines.lines:return
            import math
            points=[(x,y) for line in lines.lines for x,y in ((line.start_x,line.start_y),(line.end_x,line.end_y)) if math.isfinite(x) and math.isfinite(y)]
            if not points:return
            self.unfollow_vessel()
            xs,ys=zip(*points);xmin,xmax=min(xs),max(xs);ymin,ymax=min(ys),max(ys)
            margin=max(1,max(xmax-xmin,ymax-ymin)*.08)
            self.view.setRange(xRange=(xmin-margin,xmax+margin),yRange=(ymin-margin,ymax+margin),padding=0)
        def fit_extents(self):
            if hasattr(self,'_survey_overlay'):self.tick()
            self.view.autoRange()
        def refresh_live_survey_layer(self):
            import numpy as np
            if self.live_track_points:
                a=np.asarray(self.live_track_points);self.view.live_track_item.setData(a[:,0],a[:,1])
            else:self.view.live_track_item.setData([],[])
            if hasattr(self.view,'live_depth_scatter'):
                if self.live_soundings:
                    a=np.asarray(self.live_soundings);self.view.live_depth_scatter.setData(a[:,0],a[:,1])
                else:self.view.live_depth_scatter.setData([],[])
        def set_coordinate_status(self,x,y):
            self.coord_label.setText(f'E: {x:,.3f}  N: {y:,.3f}')
        def _center_live_position(self):
            if self.current_xy is None:return
            x,y=self.current_xy;ranges=self.view.viewRange()
            dx=max(1,(ranges[0][1]-ranges[0][0])/2);dy=max(1,(ranges[1][1]-ranges[1][0])/2)
            self.view.setXRange(x-dx,x+dx,padding=0);self.view.setYRange(y-dy,y+dy,padding=0)
        def _follow_current_view(self,enabled):
            if enabled:self.follow_vessel()
            else:self.unfollow_vessel()
        def follow_vessel(self):
            self._follow_live=True
            if hasattr(self,'_follow_action'):
                self._follow_action.blockSignals(True);self._follow_action.setChecked(True);self._follow_action.blockSignals(False)
            self._center_live_position()
            self.log_event('Continuous vessel following enabled.' if self.current_xy is not None else 'Vessel following enabled; waiting for a GNSS position.')
        def unfollow_vessel(self):
            self._follow_live=False
            if hasattr(self,'_follow_action'):
                self._follow_action.blockSignals(True);self._follow_action.setChecked(False);self._follow_action.blockSignals(False)
            self.log_event('Vessel following disabled.')
        def closeEvent(self,event):
            enc_window=getattr(self,'_enc_editor',None)
            if enc_window is not None and not enc_window.close():
                event.ignore();return
            editor=getattr(self,'_indus_editor',None)
            if editor is not None and editor.busy:
                event.ignore();editor.root.lift();editor.status.set('Wait for the KTD export to finish before closing the application.');return
            try:acq.stop()
            except Exception as exc:
                event.ignore();W.QMessageBox.warning(self,'Recording has not stopped',str(exc));return
            job=getattr(self,'_background_job',None)
            if job is not None:job['cancel'].set();job['timer'].stop()
            self.refresh_timer.stop();self.health_timer.stop()
            for logger in list(core._active_mbes_loggers):logger.stop()
            context['save_default_settings']()
            for window in list(widgets.WINDOWS):
                if not window.dead:window.destroy()
            context['root'].destroy()
            event.accept()

    window=MergedWindow()
    # Native Qt callbacks retain references to all windows via their parents.
    window._components=(widgets,core,modern,engine,acq)
    return hydro_attach_tides(window)

def check_install():
    """Check runtime imports without loading projects or opening instruments."""
    import importlib
    import importlib.metadata
    import platform
    print('HYDROGEOMATICS installation check')
    print('Python:', platform.python_version(), '|', sys.executable)
    failed = False
    for package, module in [('PyQt6', 'PyQt6.QtWidgets'),
                            ('numpy', 'numpy'), ('pyqtgraph', 'pyqtgraph'),
                            ('pyserial', 'serial'), ('pyproj', 'pyproj'),
                            ('ezdxf', 'ezdxf'), ('matplotlib', 'matplotlib'), ('UTide', 'utide'), ('sonarlight', 'sonarlight'), ('pandas', 'pandas')]:
        try:
            importlib.import_module(module)
            print('OK:', package, importlib.metadata.version(package))
        except Exception as exc:
            failed = True
            print('FAILED:', package, str(exc))
    print('This checks imports only; instruments and HYPACK compatibility are not tested.')
    if failed:
        print('Install dependencies using this Python executable: python -m pip install PyQt6 pyqtgraph numpy pyserial pyproj ezdxf matplotlib utide sonarlight pandas.')
    return 1 if failed else 0

def install_qt_diagnostics():
    """Keep Qt platform warnings for diagnosis, without hiding console output."""
    import logging
    import logging.handlers
    import os
    import platform
    from PyQt6 import QtCore
    global _qt_message_handler
    folder=Path(os.environ.get('LOCALAPPDATA') or Path.home())/'HYDROGEOMATICS'/'logs'
    try:
        folder.mkdir(parents=True,exist_ok=True)
        handler=logging.handlers.RotatingFileHandler(folder/'qt-runtime.log',maxBytes=1000000,backupCount=3,encoding='utf-8')
    except OSError as exc:
        print('Qt diagnostic log unavailable:',exc,file=sys.stderr)
        return
    logger=logging.getLogger('HYDROGEOMATICS.qt');logger.setLevel(logging.INFO);logger.propagate=False
    for old in list(logger.handlers):logger.removeHandler(old);old.close()
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));logger.addHandler(handler)
    logger.info('START Python=%s Qt=%s platform=%s script=%s',platform.python_version(),QtCore.qVersion(),platform.platform(),Path(__file__).resolve())
    def message(kind,context,text):
        logger.warning('%s: %s',kind.name,text)
        if sys.stderr is not None:print(text,file=sys.stderr)
    _qt_message_handler=message
    QtCore.qInstallMessageHandler(message)
    print('Qt diagnostic log:',folder/'qt-runtime.log')

DREDGE_VOLUME_SOURCE = (Path(__file__).resolve().parent / "modules" / "dredge_volume_source.py").read_text(encoding="utf-8")

def main():
    args=sys.argv[1:]
    if args and args[0]=='--hydro-tides':
        sys.argv=[sys.argv[0]]+args[1:]
        return hydro_tide_module().desktop_main()
    if args == ['--dredge-volume']:
        module = _module('HYDROGEOMATICS05_dredge_cli', DREDGE_VOLUME_SOURCE)
        previous_argv = sys.argv
        try:
            sys.argv = [sys.argv[0]]
            return module.main()
        finally:
            sys.argv = previous_argv
    if args==['--check-install']:
        return check_install()
    if len(args)==2 and args[0]=='--self-test':
        import json,tempfile,traceback
        report={'ok':False}
        try:
            os.environ['QT_QPA_PLATFORM']='offscreen'
            from PyQt6 import QtWidgets as W
            app=W.QApplication.instance() or W.QApplication([])
            failures=[]
            W.QMessageBox.warning=lambda parent,title,message,*a,**k:failures.append(str(message))
            components=load_components()
            with tempfile.TemporaryDirectory(prefix='HYDROGEOMATICS_exe_check_') as project:
                window=create_window(app,components,testing=True,project=project)
                window.autoload_project_layers()
                editor=window.show_indus_ktd_editor()
                if editor is None:raise RuntimeError('; '.join(failures) or 'KTD editor did not open')
                import numpy as np
                target=Path(project)/'test.KTD';window._indus_module.write_ktd(target,(0,0,100,100),np.array([[1,2],[3,999.99]]))
                assert window._indus_module.read_ktd(target)['values'][1,1]==999.99
                editor.root.destroy();window._indus_editor=None
                window.close();app.processEvents()
                if components[0].ERRORS or failures:raise RuntimeError(str(components[0].ERRORS+failures))
                report={'ok':True,'checks':['Qt startup','embedded backgrounds','Indus KTD editor','KTD read/write','clean shutdown']}
        except Exception:report['error']=traceback.format_exc()
        Path(args[1]).write_text(json.dumps(report,indent=2),encoding='utf-8')
        return 0 if report['ok'] else 1
    components=load_components()
    if args and args!=['--gui']:
        core=components[1]
        parsed=core.build_arg_parser().parse_args(args)
        return core.run_cli(parsed)
    install_qt_diagnostics()
    from PyQt6.QtWidgets import QApplication
    app=QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName('HYDROGEOMATICS HYDROGEOMATICS')
    window=create_window(app,components)
    window.showMaximized()
    return app.exec()


# Embedded HYDROGEOMATICS tide, astronomy and harmonic-analysis module.
import base64 as _hydro_b64, zlib as _hydro_zlib
HYDRO_TIDE_PAYLOAD = (Path(__file__).resolve().parent / "modules" / "hydro_tide_payload.py").read_text(encoding="utf-8")

def hydro_tide_module():
    source=HYDRO_TIDE_PAYLOAD
    return _module('hydrogeomatics_tides',source)

def hydro_open_tides(window):
    from PyQt6 import QtCore
    process=QtCore.QProcess(window)
    process.setProgram(sys.executable)
    process.setArguments(['--hydro-tides'] if getattr(sys,'frozen',False) else [str(Path(__file__).resolve()),'--hydro-tides'])
    process.errorOccurred.connect(lambda error: window.statusBar().showMessage('Could not start Tides & Astronomy: '+process.errorString()))
    if not hasattr(window,'_hydro_tide_processes'):window._hydro_tide_processes=[]
    window._hydro_tide_processes.append(process)
    process.start()
    window.statusBar().showMessage('Opening HYDROGEOMATICS Tides & Astronomy…')

def hydro_attach_tides(window):
    from PyQt6.QtGui import QAction
    menu=window.menuBar().addMenu('HYDROGEOMATICS')
    action=QAction('Tides & Astronomy',window);action.triggered.connect(lambda:hydro_open_tides(window));menu.addAction(action)
    window._hydro_tide_action=action
    window.setWindowTitle('HYDROGEOMATICS')
    return window

if __name__=='__main__':
    raise SystemExit(main())

