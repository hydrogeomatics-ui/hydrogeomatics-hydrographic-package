"""Shared acquisition, projection and HYPACK text-format I/O for HYDROGEOMATICS."""
import base64
import copy
import hashlib
import json
import math
import os
import queue
import re
import threading
import time
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

CORE = None
NEW = None
_UNPARSED = object()

def serial_parameters(port, baud, width, parity, stop):
    port=str(port).strip()
    if not port:raise ValueError('Enter a serial port for the enabled device.')
    parity=str(parity).strip().upper()
    parity={'NONE':'N','EVEN':'E','ODD':'O','MARK':'M','SPACE':'S'}.get(parity,parity)
    try:baud=float(baud);width=float(width);stop=float(stop)
    except (ValueError,TypeError):raise ValueError('Baud rate, data width and stop bits must be numbers.')
    if not math.isfinite(baud) or baud<=0 or not baud.is_integer():raise ValueError('Baud rate must be a positive whole number.')
    if width not in (5,6,7,8):raise ValueError('Data width must be 5, 6, 7 or 8 bits.')
    if stop not in (1,1.5,2):raise ValueError('Stop bits must be 1, 1.5 or 2.')
    if parity not in ('N','E','O','M','S'):raise ValueError('Parity must be None, Even, Odd, Mark or Space.')
    if re.fullmatch(r'com\d+',port,re.I):port=port.upper()
    return dict(port=port,baudrate=int(baud),bytesize=int(width),parity=parity,stopbits=stop,timeout=.25)

def available_serial_ports():
    from serial.tools import list_ports
    return [p.device for p in list_ports.comports()]

def serial_connection_key(device):
    p=serial_parameters(device.port,device.baud_rate,device.data_width,device.parity,device.stop_bits)
    return '|'.join(str(p[key]).upper() for key in ('port','baudrate','bytesize','parity','stopbits'))

def configured_device_groups(devices):
    groups={};ports={}
    for device in devices:
        key=serial_connection_key(device);port=str(device.port).strip().casefold()
        if port in ports and ports[port]!=key:raise ValueError(f'Conflicting serial settings for {device.port}. Use the same parameters for a shared port.')
        ports[port]=key;groups.setdefault(key,[]).append(device)
    return list(groups.values())

def accepts_live_sentence(group,text):
    token=CORE.ascii_sentence_type(text);kind=CORE.normalized_sentence_type(token)
    candidates={token,kind,'NMEA' if text.startswith('$') else 'NON_NMEA'}
    for device in group:
        # No declared options is an unrestricted generic device. An explicitly
        # cleared selection on a configured device disables its live decoding.
        if not device.sentence_options and not device.selected_sentences:return True
        if candidates & {str(s).upper() for s in device.selected_sentences}:return True
    return False

def validate_hardware_fields(fields,settings):
    """Validate all active connections before the settings dialog mutates them."""
    shared=bool(fields['share_gnss_sbes_input'].get())
    connections={}
    for prefix,device in [('gps',settings.gps),('sensor',settings.sensor)]:
        source='gps' if shared and prefix=='sensor' else prefix
        get=lambda key:fields[f'{source}.{key}'].get()
        enabled=bool(fields[f'{prefix}.enabled'].get())
        # Blank ports are permitted only for disabled devices.
        p=serial_parameters(get('port') or ('DISABLED' if not enabled else ''),get('baud_rate'),get('data_width'),get('parity'),get('stop_bits'))
        rate=finite(get('recording_rate_hz'))
        if rate is None or rate<=0:raise ValueError(f'{device.title}: update rate must be positive and finite.')
        if enabled:
            key=p['port'].casefold();signature=(p['baudrate'],p['bytesize'],p['parity'],p['stopbits'])
            if key in connections and connections[key]!=signature:raise ValueError('Devices on the same port must use identical communication settings. Enable shared GNSS/SBES input if appropriate.')
            connections[key]=signature
    for device in settings.extra_devices:
        if not device.enabled:continue
        p=serial_parameters(device.port,device.baud_rate,device.data_width,device.parity,device.stop_bits)
        key=p['port'].casefold();signature=(p['baudrate'],p['bytesize'],p['parity'],p['stopbits'])
        if key in connections and connections[key]!=signature:raise ValueError(f'{device.title}: conflicting settings for {p["port"]}.')
        connections[key]=signature

def scan_serial_async(owner,parameters,finished,duration=5):
    """Read complete ASCII lines off the GUI thread; cancel when the dialog closes."""
    if getattr(owner,'_serial_probe_busy',False):raise ValueError('A device scan is already running in this settings window.')
    owner._serial_probe_busy=True
    def worker():
        found=[];samples={};error='';buffer=SerialLineBuffer()
        try:
            import serial
            with serial.Serial(**parameters) as port:
                deadline=time.monotonic()+duration
                while not owner.dead and time.monotonic()<deadline:
                    for raw,complete in buffer.feed(port.readline()):
                        if not complete:continue
                        line=raw.decode('ascii',errors='replace').strip()
                        for token in (CORE.ascii_sentence_type(line),CORE.normalized_sentence_type(CORE.ascii_sentence_type(line))):
                            token=str(token or '').strip().upper()
                            if token and token not in found:found.append(token);samples[token]=line[:120]
                    if len(found)>=80:break
        except Exception as exc:error=str(exc)
        def deliver():
            owner._serial_probe_busy=False
            finished(found,samples,error)
        if not owner.dead:owner.after(0,deliver)
    thread=threading.Thread(target=worker,daemon=True);thread.start()
    return thread

def atomic_json(path,payload):
    """Replace metadata only after a complete, durable temporary write."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as handle:
            json.dump(payload,handle,indent=2,allow_nan=False);handle.write('\n');handle.flush();os.fsync(handle.fileno())
        os.replace(name,path)
    finally:
        Path(name).unlink(missing_ok=True)

def file_sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()

def inspect_session(path,verify=False):
    """Read-only session review; optional full-file checksum verification."""
    path=Path(path);payload=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload,dict) or payload.get('application')!='HYDROGEOMATICS':
        raise ValueError('Select a HYDROGEOMATICS session JSON file.')
    lines=[f'Status: {payload.get("status","unknown")}',f'Started (UTC): {payload.get("started_utc","unknown")}',f'Last checkpoint (UTC): {payload.get("updated_utc","unknown")}']
    if payload.get('status') in ('recording','stopping'):
        lines.append('Not finalized: the session may still be running or may have been interrupted. RAW files are left untouched.')
    lines.append('Measurements: '+json.dumps(payload.get('metrics',{}),sort_keys=True))
    entries=payload.get('files',[])
    if not isinstance(entries,list) or not all(isinstance(e,dict) for e in entries):raise ValueError('Invalid session file list.')
    for entry in entries:
        name=entry.get('name','')
        if not isinstance(name,str) or not name or Path(name).name!=name or '/' in name or '\\' in name:
            lines.append('Rejected invalid file reference in session metadata.');continue
        target=path.parent/name
        if not target.resolve().is_relative_to(path.parent.resolve()):
            lines.append(f'{name}: file reference escapes the session folder');continue
        if not target.is_file():lines.append(f'{name}: MISSING');continue
        size=target.stat().st_size;lines.append(f'{name}: {size:,} bytes')
        expected=entry.get('sha256')
        if verify and expected:
            lines.append('  SHA-256: '+('MATCH' if file_sha256(target)==expected else 'MISMATCH — file differs from the finalized recording'))
        elif verify:lines.append('  No finalized checksum is available; integrity cannot be confirmed.')
    if payload.get('last_error'):lines.append('Last error: '+payload['last_error'])
    return '\n'.join(lines)

class SerialLineBuffer:
    """Keep timeout fragments until CR/LF arrives; bound unterminated input."""
    def __init__(self,limit=65536):self.pending=bytearray();self.limit=limit;self.fragmented=False
    def feed(self,data):
        self.pending.extend(data);records=[]
        while self.pending:
            marks=[n for n in (self.pending.find(b'\r'),self.pending.find(b'\n')) if n>=0]
            if marks and min(marks)<=self.limit:
                index=min(marks);line=bytes(self.pending[:index]);del self.pending[:index+1]
                if line:records.append((line,not self.fragmented))
                self.fragmented=False
            elif len(self.pending)>self.limit:
                records.append((bytes(self.pending[:self.limit]),False));del self.pending[:self.limit];self.fragmented=True
            else:break
        return records
    def finish(self):
        tail=bytes(self.pending);self.pending.clear();self.fragmented=False
        return [(tail,False)] if tail else []

def checksum_valid(text):
    if not text.startswith(('$','!')) or '*' not in text:
        return True
    body, check = text[1:].rsplit('*',1)
    total = 0
    for char in body: total ^= ord(char)
    try: return len(check.strip()) == 2 and total == int(check,16)
    except ValueError: return False

def finite(value):
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None

def seconds_of_day(value):
    s=str(value).strip()
    if ':' in s:
        parts=s.split(':');result=int(parts[-3])*3600+int(parts[-2])*60+float(parts[-1])
    elif len(s.split('.')[0])==6:
        result=int(s[:2])*3600+int(s[2:4])*60+float(s[4:])
    else: result=float(s)
    if not math.isfinite(result) or not 0<=result<86400:raise ValueError('Invalid time of day: '+s)
    return result

def safe_observation(text):
    try:return parse_observation(text)
    except (ValueError,TypeError,IndexError,OverflowError):return None

def parse_observation(text):
    """Parse without discarding original observations from the recording."""
    text=text.strip()
    if not checksum_valid(text):return None
    point=CORE.parse_nmea_sentence_original(text)
    parts=text.split('*',1)[0].split(',');kind=parts[0][-3:].upper()
    if kind=='GGA':
        if len(parts)<10 or parts[6] in ('','0'):return None
        if point:
            h=finite(parts[9]);n=finite(parts[11]) if len(parts)>11 else None
            point.altitude_m=h+n if h is not None and n is not None else None
    elif kind=='RMC' and (len(parts)<3 or parts[2]!='A'):return None
    elif kind in ('GLL','GNS'):
        if kind=='GLL' and len(parts)>=7 and parts[6]=='A':
            point=CORE.SurveyPoint(time=parts[5],latitude=CORE.parse_nmea_lat_lon(parts[1],parts[2]),longitude=CORE.parse_nmea_lat_lon(parts[3],parts[4]),quality='A')
        elif kind=='GNS' and len(parts)>=11 and parts[6].strip('N'):
            h=finite(parts[9]);n=finite(parts[10]);point=CORE.SurveyPoint(time=parts[1],latitude=CORE.parse_nmea_lat_lon(parts[2],parts[3]),longitude=CORE.parse_nmea_lat_lon(parts[4],parts[5]),quality=parts[6],altitude_m=h+n if h is not None and n is not None else None)
        else:return None
    elif 'BESTPOS' in parts[0].upper() and ';' in text:
        fields=text.split(';',1)[1].split('*',1)[0].split(',')
        if len(fields)<6 or fields[0]!='SOL_COMPUTED':return None
        lat,lon,h,n=(finite(fields[i]) for i in (2,3,4,5))
        point=CORE.SurveyPoint(latitude=lat,longitude=lon,altitude_m=h+n if h is not None and n is not None else None,quality=fields[1])
    if point is None:
        depth=finite(text)
        if depth is not None:point=CORE.SurveyPoint(raw_depth=depth)
    if point:
        if point.latitude is not None and not -90<=point.latitude<=90:return None
        if point.longitude is not None and not -180<=point.longitude<=180:return None
        for name in ('latitude','longitude','raw_depth','altitude_m','heading_degrees','speed_knots','course_degrees'):
            v=getattr(point,name,None)
            if v is not None and finite(v) is None:setattr(point,name,None)
    return point

def projection_parameters(g):
    zone=CORE.selected_utm_zone(g)
    central=zone*6-183 if zone is not None else float(g.central_meridian)
    return central,float(g.latitude_origin),float(g.scale_factor),float(g.false_easting),float(g.false_northing)

def validate_raw_geodetic(g):
    # Fail explicitly instead of writing a false PRO record for a different CRS.
    if not g.use_projection or not g.use_tm_projection or g.use_lcc_projection:
        raise ValueError('HYPACK RAW recording requires an explicitly configured Transverse Mercator/UTM grid. Other transformations remain available in GEOCALC.')
    if str(g.utm_zone).strip().lower() in ('auto','automatic'):
        raise ValueError('Select a fixed UTM zone before RAW recording so every position matches the file header.')
    unit=str(g.distance_unit).lower()
    if unit not in ('meter','meters','metre','metres','m'):
        raise ValueError('Use metre horizontal units for HYPACK RAW recording in this build.')
    for name in ('delta_x','delta_y','delta_z','delta_rx','delta_ry','delta_rz','delta_scale'):
        if float(getattr(g,name,0))!=0:raise ValueError('Live RAW uses WGS84 input: apply datum transformations in GEOCALC, or clear the datum shift before recording.')
    if g.local_grid_adjustment:raise ValueError('Local grid adjustments must be applied and verified before RAW recording.')
    if str(g.datum_name).replace('-','').replace(' ','').upper() not in ('WGS84','WGS1984'):
        raise ValueError('Live GNSS recording currently requires WGS84 datum; datum conversion remains available in GEOCALC.')

def project_xy(lat,lon,g):
    if not g.use_projection:return lon,lat
    if not g.use_tm_projection:raise ValueError('Choose a supported TM/UTM projection for live positioning.')
    from pyproj import CRS, Transformer
    central,origin,scale,east,north=projection_parameters(g)
    key=(central,origin,scale,east,north,float(g.semi_major_axis),float(g.flattening))
    if key not in _TRANSFORMS:
        crs=CRS.from_proj4(f'+proj=tmerc +lat_0={origin} +lon_0={central} +k={scale} +x_0={east} +y_0={north} +a={g.semi_major_axis} +rf={g.flattening} +units=m +no_defs')
        _TRANSFORMS[key]=Transformer.from_crs('EPSG:4326',crs,always_xy=True)
    return _TRANSFORMS[key].transform(lon,lat,errcheck=True)
_TRANSFORMS={}

class HypackWriter:
    """Incremental HYPACK ASCII records; raw source messages use MSG, not RAW.

    POS/EC1/GYR/RAW retain acquisition values. Reductions are post-processing.
    Time tags are UTC seconds since midnight; a new file is opened each UTC day.
    """
    def __init__(self,path,geodetic,devices,when=None,draft=0.0):
        self.path=Path(path);self.geodetic=copy.deepcopy(geodetic);validate_raw_geodetic(self.geodetic)
        self.devices=list(dict.fromkeys(str(d) for d in devices));self.ids={d:i for i,d in enumerate(self.devices)}
        self.when=when or datetime.now(timezone.utc);self.count=0;self.handle=None;self.draft=float(draft)
        header=self.header()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.handle=self.path.open('x',encoding='ascii',errors='backslashreplace',newline='\n')
        self.handle.write(header);self.handle.flush()
    def header(self):
        g=self.geodetic;c,o,k,x,y=projection_parameters(g)
        lines=['FTP NEW 2','VER HYDROGEOMATICS', 'INF "" "" "" "" 1.000000 0.000000 0.000000',f'ELL WGS-84 {float(g.semi_major_axis):.6f} {float(g.flattening):.9f}',f'PRO TME {c:.9f} {k:.9f} {o:.9f} 0.000000 0.000000 {x:.4f} {y:.4f}','DTM 0 0 0 0 0 0 0','HVU 1.0000000000 1.0000000000',f'TND {self.when:%H:%M:%S} {self.when:%m/%d/%Y} 0']
        for name,i in self.ids.items():
            safe=name.replace('"',"'").replace('\n',' ').replace('\r',' ')
            # A shared stream can carry grid/latlon, depth, heading and speed.
            lines += [f'DEV {i} 124 "{safe}"',f'OFF {i} 0 0 0 0 0 0 0']
        lines.append('EOH')
        return '\n'.join(lines)+'\n'
    def record(self,name,when,text,point=_UNPARSED):
        if name not in self.ids:raise ValueError('Device is not declared in RAW header: '+name)
        i=self.ids[name];t=when.hour*3600+when.minute*60+when.second+when.microsecond/1e6
        clean=str(text).replace('\r','').replace('\n','')
        self.handle.write(f'MSG {i} {t:.6f} {clean}\n')
        p=safe_observation(clean) if point is _UNPARSED else point
        if p is not None:self.write_point(i,t,p)
        self.count+=1;self.handle.flush()
    def write_point(self,i,t,p):
        if p.latitude is not None and p.longitude is not None:
            x,y=project_xy(p.latitude,p.longitude,self.geodetic)
            self.handle.write(f'POS {i} {t:.6f} {x:.4f} {y:.4f}\n')
            if p.altitude_m is not None:
                utc=datetime.fromtimestamp(self.when.timestamp(),timezone.utc).strftime('%H%M')
                try:
                    st=seconds_of_day(p.time);utc=f'{int(st//3600):02d}{int(st%3600//60):02d}'
                except (ValueError,TypeError):pass
                self.handle.write(f'RAW {i} {t:.6f} 4 {p.latitude*100:.10f} {p.longitude*100:.10f} {p.altitude_m:.4f} {utc}\n')
        elif p.easting is not None and p.northing is not None:
            self.handle.write(f'POS {i} {t:.6f} {p.easting:.4f} {p.northing:.4f}\n')
        if p.raw_depth is not None:self.handle.write(f'EC1 {i} {t:.6f} {p.raw_depth:.4f}\n')
        if p.heading_degrees is not None:self.handle.write(f'GYR {i} {t:.6f} {p.heading_degrees:.4f}\n')
    def close(self):
        if self.handle and not self.handle.closed:
            try:self.handle.flush();os.fsync(self.handle.fileno())
            finally:self.handle.close()

def unique_raw_path(folder,prefix='HYDROGEOMATICS'):
    p=Path(folder)/f'{prefix}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S_%f}.RAW'
    return p

def export_points(model,path,g=None):
    g=g or getattr(model,'geodetic',None) or CORE.GeodeticSettings()
    path=Path(path);temp=unique_raw_path(path.parent,'.export');writer=HypackWriter(temp,g,['GNSS','SBES']);count=0
    try:
        for index,p in enumerate(model.points):
            try:t=seconds_of_day(p.time)
            except (ValueError,TypeError):t=float(index%86400)
            # Set the water level before its sounding, including transitions to zero.
            writer.handle.write(f'TID 0 {t:.6f} {float(p.tide):.4f}\n')
            # Each typed record retains its measurement's source ID.
            if p.latitude is not None or p.easting is not None or p.heading_degrees is not None:
                pos=copy.copy(p);pos.raw_depth=None;writer.write_point(0,t,pos)
            if p.raw_depth is not None:
                writer.handle.write(f'EC1 1 {t:.6f} {p.raw_depth:.4f}\n');count+=1
        writer.close();os.replace(temp,path)
    except Exception:
        writer.close();temp.unlink(missing_ok=True);raise
    return count

def read_raw(path):
    """Read standard typed records and both supplied applications' older logs."""
    path=Path(path);g=CORE.GeodeticSettings()
    standard_depth=False;standard_pos=False
    with path.open('r',encoding='utf-8',errors='replace') as scan:
        for line in scan:
            standard_depth |= line.lstrip().startswith(('EC1 ','EC2 '))
            standard_pos |= line.lstrip().startswith('POS ')
    points=[];last=None;last_index=None;tide=0.0;drafts={}
    def lines():
        with path.open('r',encoding='utf-8',errors='replace') as handle:yield from handle
    for n,line in enumerate(lines(),1):
        line=line.rstrip('\r\n')
        parts=line.split();tag=parts[0] if parts else ''
        try:
            if tag=='PRO' and len(parts)>=9 and parts[1]=='TME':
                g.utm_zone='';g.central_meridian=float(parts[2]);g.scale_factor=float(parts[3]);g.latitude_origin=float(parts[4]);g.false_easting=float(parts[7]);g.false_northing=float(parts[8])
            elif tag=='OFF' and len(parts)>=5:drafts[parts[1]]=float(parts[4])
            elif tag=='TID' and len(parts)>=4:
                value=finite(parts[3])
                if value is not None:tide=value
            elif tag=='POS' and len(parts)>=5:
                last=CORE.SurveyPoint(time=CORE.hypack_time_to_display(parts[2]),easting=float(parts[3]),northing=float(parts[4]),tide=tide,source=f'{path.name}:{n}')
                if finite(last.easting) is None or finite(last.northing) is None:last=None;last_index=None;continue
                last_index=len(points);points.append(last)
            elif tag=='RAW' and len(parts)>=8 and '$' not in line:
                # Standard RAW is numeric navigation, never a depth record.
                lat=float(parts[4])/100;lon=float(parts[5])/100;h=float(parts[6]);x,y=project_xy(lat,lon,g)
                if last is not None and last.time==CORE.hypack_time_to_display(parts[2]):last.latitude=lat;last.longitude=lon;last.altitude_m=h
                elif not standard_pos:
                    last=CORE.SurveyPoint(time=CORE.hypack_time_to_display(parts[2]),latitude=lat,longitude=lon,altitude_m=h,easting=x,northing=y,tide=tide,source=f'{path.name}:{n}');last_index=len(points);points.append(last)
            elif tag in ('EC1','EC2','DEP') and len(parts)>=4:
                depth=finite(parts[3])
                if depth is None:continue
                p=copy.copy(last) if last else CORE.SurveyPoint();p.raw_depth=depth;p.time=CORE.hypack_time_to_display(parts[2]);p.tide=tide;p.draft=drafts.get(parts[1],0);p.source=f'{path.name}:{n}'
                if last_index is not None and last is not None and last.raw_depth is None:
                    points[last_index]=p;last_index=None
                else:points.append(p)
            elif tag in ('MSG','RAW') and len(parts)>=4 and ('$' in line or tag=='MSG'):
                text=line.split(None,3)[3];p=safe_observation(text)
                if p is None:continue
                if p.raw_depth is not None and standard_depth:continue
                if p.latitude is not None and standard_pos:continue
                p.time=CORE.hypack_time_to_display(parts[2]);p.tide=tide;p.source=f'{path.name}:{n}'
                if p.latitude is not None and p.longitude is not None:p.easting,p.northing=project_xy(p.latitude,p.longitude,g);last=p;last_index=len(points)
                elif p.raw_depth is not None and last:p.easting=last.easting;p.northing=last.northing;p.altitude_m=last.altitude_m
                points.append(p)
            elif line.startswith(('GNSS,','SBES,')) or line.startswith(('$','#')):
                fields=line.split(',',2);text=fields[2] if line.startswith(('GNSS,','SBES,')) and len(fields)==3 else line;p=safe_observation(text)
                if p is None:continue
                if p.latitude is not None and p.longitude is not None:p.easting,p.northing=project_xy(p.latitude,p.longitude,g);last=p;last_index=len(points)
                elif p.raw_depth is not None and last:p.easting=last.easting;p.northing=last.northing;p.altitude_m=last.altitude_m
                p.source=f'{path.name}:{n}';points.append(p)
        except (ValueError,IndexError,OverflowError):continue
    return points

class SensorWatchdog:
    """Monotonic per-source timeout; reconnecting does not reset the deadline."""
    def __init__(self, name, events, clock=time.monotonic):
        self.name=name;self.events=events;self.clock=clock
        self.last=clock();self.retry=self.last;self.lost=False
    def received(self):
        self.last=self.clock()
        if self.lost:self.events.put((self.name,'restored'));self.lost=False
    def check(self):
        now=self.clock()
        if now-self.last<15:return False
        if not self.lost:self.events.put((self.name,'lost'));self.lost=True
        if now-self.retry>=15:self.retry=now;return True
        return False
    def stop(self):self.events.put((self.name,'stopped'))

class Acquisition:
    def __init__(self):
        self.ctx=None;self.writer=None;self.paths=[];self.queue=queue.Queue();self.latest_accuracy={'x':None,'y':None,'z':None};self.states={};self.last_position_time=None;self.last_xy=None;self.live_track=[];self.live_depth=[];self.new_window=None
        self.session_path=None;self.session=None;self.last_checkpoint=0.0;self.last_received=None
        self.metrics={'received':0,'recorded':0,'invalid_checksum':0,'incomplete':0,'unparsed':0}
        self.last_error='';self.metadata_error=''
        self.sensor_events=queue.Queue();self.incoming_handle=None;self.event_handle=None;self.event_second=None;self.event_point=None;self.event_count=0
    def attach(self,ctx):self.ctx=ctx
    def checkpoint(self,force=False):
        if not self.session or not self.session_path:return
        if not force and time.monotonic()-self.last_checkpoint<5:return
        self.last_checkpoint=time.monotonic()
        try:
            for handle in (self.incoming_handle,self.event_handle):
                if handle:handle.flush();os.fsync(handle.fileno())
            if self.writer and self.writer.handle and not self.writer.handle.closed:
                self.writer.handle.flush();os.fsync(self.writer.handle.fileno())
            self.session.update(updated_utc=datetime.now(timezone.utc).isoformat(),metrics=dict(self.metrics),pending_records=self.queue.qsize(),last_error=self.last_error)
            existing={f['name']:f for f in self.session.get('files',[])}
            self.session['files']=[dict(existing.get(p.name,{}),name=p.name,bytes=p.stat().st_size if p.exists() else 0) for p in self.paths]
            atomic_json(self.session_path,self.session);self.metadata_error=''
        except OSError as exc:
            self.metadata_error=str(exc)
            # A metadata failure must not discard an otherwise writable RAW stream.
            if self.ctx:self.ctx['status_var'].set('Session checkpoint failed: '+str(exc))
    def finish_session(self,status):
        if not self.session or self.session.get('status') not in ('recording','stopping','error') or self.session.get('ended_utc'):return
        self.session['status']=status;self.session['ended_utc']=datetime.now(timezone.utc).isoformat()
        files=[]
        for path in self.paths:
            entry={'name':path.name}
            try:entry.update(bytes=path.stat().st_size,sha256=file_sha256(path))
            except OSError as exc:entry['error']=str(exc)
            files.append(entry)
        self.session['files']=files;self.checkpoint(force=True)
    def health(self):
        now=datetime.now(timezone.utc)
        folder=Path(self.ctx['settings'].raw_folder) if self.ctx else Path.cwd()
        while not folder.exists() and folder!=folder.parent:folder=folder.parent
        try:free=shutil.disk_usage(folder).free
        except OSError:free=None
        return {'recording':self.writer is not None,'files':[str(p) for p in self.paths],
                'session':str(self.session_path or ''),'metrics':dict(self.metrics),'queue':self.queue.qsize(),
                'last_data_age_s':max(0,(now-self.last_received).total_seconds()) if self.last_received else None,
                'last_fix_age_s':max(0,(now-self.last_position_time).total_seconds()) if self.last_position_time else None,
                'free_bytes':free,'devices':dict(self.states),'error':self.last_error,'checkpoint_error':self.metadata_error}
    def names(self):
        return [self.group_name(group) for group in configured_device_groups(self.ctx['configured_devices']())]
    def group_name(self,group):return ' / '.join(dict.fromkeys(d.title for d in group))+' @ '+str(group[0].port).strip().upper()
    def start(self,*args):
        c=self.ctx
        if c['logging_active'].get():return 'break'
        if any(t.is_alive() for t in c['io_monitor_threads']) and c['io_monitor_stop_event'].is_set():raise RuntimeError('Previous device readers are still stopping; wait before restarting.')
        c['read_settings']();g=c['settings'].geodetic;validate_raw_geodetic(g)
        names=self.names()
        if not names:raise ValueError('Enable and configure at least one device in Settings before logging.')
        # Process monitor-only data before opening a new recording.
        while not self.queue.empty():self.drain()
        self.writer=HypackWriter(unique_raw_path(c['settings'].raw_folder),g,names);self.paths=[self.writer.path]
        self.metrics=dict.fromkeys(self.metrics,0);self.last_received=None;self.last_error='';self.metadata_error=''
        self.event_second=None;self.event_point=None;self.event_count=0
        try:
            for suffix,attribute in (('.incoming.jsonl','incoming_handle'),('.events.jsonl','event_handle')):
                path=self.writer.path.with_suffix(suffix)
                setattr(self,attribute,path.open('x',encoding='utf8'));self.paths.append(path)
        except Exception:
            self.writer.close();self.writer=None;self.close_sidecars();raise
        self.session_path=self.writer.path.with_suffix('.session.json')
        self.session={'version':1,'application':'HYDROGEOMATICS','status':'recording','started_utc':self.writer.when.isoformat(),
                      'survey_event_interval_s':1.0,'raw_policy':'all incoming data; no decimation','settings':copy.deepcopy(c['settings'].to_dict()),'corrections':{'tide':c['model'].tide,'draft':c['model'].draft,'sound_speed':c['model'].sound_speed},'files':[]}
        self.checkpoint(force=True)
        c['live_raw_records'].clear();c['live_echogram_points'].clear();self.live_track.clear();self.live_depth.clear();self.last_position_time=None;self.last_xy=None
        c['logging_active'].set(True);c['logging_started_at'].set(datetime.now(timezone.utc).strftime('%H:%M:%S UTC'));c['status_var'].set('Recording HYPACK RAW: '+str(self.writer.path))
        c['show_io_monitor'](auto_start=True);c['show_data_display']();c['show_live_echogram_display'](auto=True)
        if self.new_window:self.new_window.show_accuracy_monitor()
        return 'break'
    def stop(self,*args):
        c=self.ctx;c['io_monitor_stop_event'].set()
        for t in list(c['io_monitor_threads']):t.join(timeout=1.5)
        while not self.queue.empty():self.drain()
        if any(t.is_alive() for t in c['io_monitor_threads']):
            if self.session:self.session['status']='stopping'
            self.checkpoint(force=True)
            raise RuntimeError('Device reader is still stopping. The RAW file remains open; retry Stop once it exits.')
        self.flush_event()
        self.close_sidecars()
        if self.writer:
            try:self.writer.close()
            except OSError as exc:self.last_error=str(exc)
            finally:self.writer=None
        c['logging_active'].set(False);c['io_monitor_threads'][:]=[t for t in c['io_monitor_threads'] if t.is_alive()]
        self.finish_session('error' if self.last_error else 'complete')
        c['autosave_echogram']('stop_logging');c['close_auto_echogram_display']()
        c['status_var'].set(('Recording error: '+self.last_error) if self.last_error else 'Saved HYPACK RAW: '+'; '.join(str(p) for p in self.paths))
        return 'break'
    def close_sidecars(self):
        for attribute in ('incoming_handle','event_handle'):
            handle=getattr(self,attribute)
            if handle:
                try:handle.flush();os.fsync(handle.fileno())
                finally:handle.close();setattr(self,attribute,None)
    def advance_event(self,when):
        second=int(when.timestamp())
        if self.event_second is None:self.event_second=second
        if second>self.event_second:
            self.flush_event();self.event_second=second
    def flush_event(self):
        if self.event_second is None or self.event_handle is None:return
        self.event_count+=1
        p=self.event_point
        record={'event':self.event_count,'utc':datetime.fromtimestamp(self.event_second,timezone.utc).isoformat(),
                'has_observation':p is not None,'observation':vars(p) if p is not None else None}
        self.event_handle.write(json.dumps(record,default=str,allow_nan=False)+'\n');self.event_handle.flush()
        if p is not None:self.ctx['model'].add(copy.copy(p))
        self.event_point=None;self.event_second=None
    def collect_event(self,p,when):
        self.advance_event(when)
        if self.event_point is None:self.event_point=copy.copy(p)
        else:
            # Only observations from this second contribute to this event.
            for key,value in vars(p).items():
                if key in ('tide','draft','sound_speed') and p.raw_depth is None:continue
                if value is not None and value!='':setattr(self.event_point,key,value)
    def monitor_group(self,group,key,display):
        c=self.ctx;event=c['io_monitor_stop_event'];name=self.group_name(group);d=group[0]
        watches=[SensorWatchdog(str(device.title)+' @ '+str(d.port),self.sensor_events) for device in group]
        def mark(raw):
            text=raw.decode('ascii',errors='replace').strip()
            token=CORE.ascii_sentence_type(text);kind=CORE.normalized_sentence_type(token)
            for device,watch in zip(group,watches):
                options={str(v).upper() for v in device.sentence_options}
                if len(group)==1 or not options or {token,kind,'NMEA' if text.startswith('$') else 'NON_NMEA'} & options:watch.received()
        try:
            import serial
            while not event.is_set():
                try:
                    for watch in watches:watch.check()
                    self.states[name]='Connecting'
                    with serial.Serial(**serial_parameters(d.port,d.baud_rate,d.data_width,d.parity,d.stop_bits)) as port:
                        self.states[name]='Connected'
                        buffer=SerialLineBuffer()
                        def enqueue(records):
                            for raw,complete in records:
                                mark(raw)
                                stamp=datetime.now(timezone.utc);text=raw.decode('ascii',errors='backslashreplace')
                                self.queue.put((group,name,stamp,text,key,display,complete))
                        try:
                            while not event.is_set():
                                raw=port.readline()
                                if raw:
                                    stamp=datetime.now(timezone.utc)
                                    self.queue.put(('bytes',name,stamp,raw))
                                    if len(group)==1:watches[0].received()
                                    enqueue(buffer.feed(raw))
                                retry=[watch.check() for watch in watches]
                                if any(retry):
                                    self.states[name]='No sensor data for 15 seconds; reconnecting'
                                    break
                        finally:enqueue(buffer.finish())
                except Exception as exc:
                    self.states[name]='Reconnecting: '+str(exc)
                    for watch in watches:watch.check()
                    event.wait(1.0)
        finally:
            for watch in watches:watch.stop()
            self.states[name]='Stopped';c['io_monitor_thread_keys'].discard(key)
    def update_tide(self,p,g,stamp,name):
        """Update the current solution without changing historical soundings."""
        c=self.ctx;latest=c['latest_sensor_data'];k=CORE.ktd_cell_value_at(g.ktd_file_path,p.easting,p.northing) if g.ktd_file_path else None
        tide=p.altitude_m-k if k is not None else CORE.compute_rtk_tide_from_ellipsoidal_height(p.altitude_m,g,p.easting,p.northing)
        if tide is None:return
        latest.update(ktd_cell_k=k,rtk_reduced_height=p.altitude_m-k if k is not None else None,rtk_tide=tide,chart_datum_level=g.chart_datum_level,rtk_tide_method=g.rtk_tide_method)
        c['tide_var'].set(f'{tide:.4f}');c['model'].tide=tide
        c['status_var'].set(f'RTK tide {tide:.3f} m | {name} | {stamp} UTC')
    def drain(self):
        try:return self._drain()
        except Exception as exc:
            self.last_error=str(exc)
            if self.ctx:
                self.ctx['logging_active'].set(False);self.ctx['io_monitor_stop_event'].set()
                self.ctx['status_var'].set('Recording failed: '+str(exc))
            if self.writer:
                try:self.writer.close()
                except Exception:pass
                self.writer=None
            try:self.close_sidecars()
            except Exception:pass
            if self.session:self.session['status']='error'
            self.checkpoint(force=True)
            raise
    def _drain(self):
        if self.ctx is None:return
        c=self.ctx;updated=False;previous_events=self.event_count
        for _ in range(10000):
            try:item=self.queue.get_nowait()
            except queue.Empty:break
            if item[0]=='bytes':
                _,name,when,payload=item
                if self.incoming_handle:
                    self.incoming_handle.write(json.dumps({'utc':when.isoformat(),'source':name,'data_base64':base64.b64encode(payload).decode('ascii')})+'\n')
                    self.incoming_handle.flush()
                continue
            group,name,when,text,key,display=item[:6];complete=item[6] if len(item)>6 else True
            self.metrics['received']+=1;self.last_received=when
            if not complete:self.metrics['incomplete']+=1
            if not checksum_valid(text):self.metrics['invalid_checksum']+=1
            if self.writer and when.date()!=self.writer.when.date():
                old=self.writer;old.close();self.writer=HypackWriter(unique_raw_path(old.path.parent),old.geodetic,old.devices,when);self.paths.append(self.writer.path)
            p=safe_observation(text) if complete else None
            if p is None:self.metrics['unparsed']+=1
            if self.writer:
                try:self.writer.record(name,when,text,p);self.metrics['recorded']+=1
                except Exception as exc:
                    self.last_error=str(exc);c['logging_active'].set(False);c['io_monitor_stop_event'].set()
                    try:self.writer.close()
                    except OSError:pass
                    self.writer=None
                    if self.session:self.session['status']='error'
                    self.checkpoint(force=True);raise
                # Recording never depends on sentence-display preferences.
                with c['live_raw_lock']:c['live_raw_records'].append((name,when.strftime('%H:%M:%S.%f'),text))
            display(key,text)
            if self.new_window:
                self.new_window._extract_accuracy_from_sentence(text)
            if not accepts_live_sentence(group,text):continue
            if p is None:continue
            stamp=when.strftime('%H:%M:%S.%f');p.time=stamp;p.source=name
            latest=c['latest_sensor_data'];latest['source']=name;latest['time']=stamp
            g=self.writer.geodetic if self.writer else c['settings'].geodetic
            if p.latitude is not None and p.longitude is not None:
                p.easting,p.northing=project_xy(p.latitude,p.longitude,g);self.last_xy=(p.easting,p.northing);self.last_position_time=when
                for k in ('latitude','longitude','easting','northing','altitude_m','quality'):latest[k]=getattr(p,k)
                self.live_track.append(self.last_xy);self.live_track=self.live_track[-20000:]
                if p.altitude_m is not None and g.rtk_tide_method!='Not using RTK tide':self.update_tide(p,g,stamp,name)
                c['enable_realtime_navigation_follow']()
            if p.heading_degrees is not None:latest['heading_degrees']=p.heading_degrees
            if p.course_degrees is not None:latest['course_degrees']=p.course_degrees
            if p.speed_knots is not None:latest['speed_knots']=p.speed_knots
            if p.raw_depth is not None:
                latest['raw_depth']=p.raw_depth;c['add_live_echogram_depth'](p.raw_depth,stamp,name)
                if self.last_xy and self.last_position_time and 0<=(when-self.last_position_time).total_seconds()<=5:
                    p.easting,p.northing=self.last_xy;p.altitude_m=latest.get('altitude_m')
                p.tide=c['model'].tide;p.draft=c['model'].draft;p.sound_speed=c['model'].sound_speed
                if g.rtk_tide_method!='Not using RTK tide':CORE.apply_ktd_rtk_postprocess_to_point(p,g)
                if p.easting is not None:self.live_depth.append((p.easting,p.northing,p.corrected_depth));self.live_depth=self.live_depth[-20000:]
            if c['logging_active'].get():self.collect_event(p,when)
            updated=True
        if self.writer and self.queue.empty():self.advance_event(datetime.now(timezone.utc))
        if updated:c['refresh_data_display']()
        if self.event_count!=previous_events:c['refresh_table']()
        if self.writer:self.checkpoint()
    def export_records(self,path,records):
        temp=unique_raw_path(Path(path).parent,'.export');names=list(dict.fromkeys(r[0] for r in records));writer=HypackWriter(temp,self.ctx['settings'].geodetic,names)
        try:
            for name,stamp,text in records:
                s=seconds_of_day(stamp);when=datetime.now(timezone.utc).replace(hour=int(s//3600),minute=int(s%3600//60),second=int(s%60),microsecond=int(s%1*1e6));writer.record(name,when,text)
            writer.close();os.replace(temp,path)
        except Exception:writer.close();temp.unlink(missing_ok=True);raise
        return len(records)

def monitored_udp_worker(logger,source_name,port):
    import socket
    acq=CORE._acquisition;watch=SensorWatchdog(f'{source_name} @ UDP {port}',acq.sensor_events)
    try:
        while not logger.stop_event.is_set():
            try:
                watch.check()
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
                    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);sock.settimeout(.5)
                    sock.bind(('0.0.0.0',int(port)))
                    while not logger.stop_event.is_set():
                        try:payload,addr=sock.recvfrom(65535)
                        except socket.timeout:
                            if watch.check():break
                            continue
                        if payload:watch.received()
                        # File failures are not transport failures: stop and report them.
                        try:logger._write_packet(source_name,payload,f'{addr[0]}:{addr[1]}')
                        except Exception as exc:
                            logger.errors.put(f'{source_name}: recording failed: {exc}');logger.stop_event.set();return
            except OSError as exc:
                logger.errors.put(f'{source_name} UDP {port}: {exc}')
                watch.check();logger.stop_event.wait(1)
    finally:watch.stop()


def install(core,new):
    global CORE,NEW
    CORE=core;NEW=new
    core.serial_parameters=serial_parameters
    core.available_serial_ports=available_serial_ports
    core.validate_hardware_fields=validate_hardware_fields
    core.scan_serial_async=scan_serial_async
    core.serial_connection_key_canonical=serial_connection_key
    core.configured_device_groups=configured_device_groups
    core.parse_nmea_sentence_original=core.parse_nmea_sentence
    core.parse_nmea_sentence=parse_observation
    core.load_hypack_raw_points=read_raw
    core.SurveyModel.export_hypack_raw=export_points
    core.project_lat_lon_to_selected_xy=lambda lat,lon,g:(None,None) if lat is None or lon is None else project_xy(lat,lon,g)
    # The older processing engine can also consume the newer protected assets.
    import tempfile,hashlib
    cache=tempfile.TemporaryDirectory(prefix='HYDROGEOMATICS20_assets_')
    core._asset_cache=cache
    def decoded_path(path):
        path=Path(path)
        if path.suffix.lower() not in ('.ktdp','.hpktd','.hpdxf'):return path
        payload=path.read_bytes();key=hashlib.sha256(payload).hexdigest()
        dxf=path.suffix.lower()=='.hpdxf';target=Path(cache.name)/(key+('.dxf' if dxf else '.ktd'))
        if not target.exists():target.write_bytes(new.unprotect_dxf_bytes(payload) if dxf else new.unprotect_ktd_bytes(payload))
        return target
    for name in ('load_ktd_nodes','load_ktd_matrix','load_ktd_grid_meta','load_dxf_shapes'):
        original=getattr(core,name)
        def reader(path,*args,_reader=original,**kw):return _reader(decoded_path(path),*args,**kw)
        setattr(core,name,reader)
    core._acquisition=Acquisition()
    core.show_river_bank_border=lambda callback:new.show_river_bank_border(core._acquisition.new_window,callback)
    core._active_mbes_loggers=[]
    init_logger=core.MBESRawLogger.__init__
    stop_logger=core.MBESRawLogger.stop
    def logger_init(self,*args,**kw):
        init_logger(self,*args,**kw);core._active_mbes_loggers.append(self)
    def logger_stop(self):
        stop_logger(self)
        if self in core._active_mbes_loggers:core._active_mbes_loggers.remove(self)
    core.MBESRawLogger._udp_worker=monitored_udp_worker
    core.MBESRawLogger.__init__=logger_init
    core.MBESRawLogger.stop=logger_stop
    return core._acquisition
