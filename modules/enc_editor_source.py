"""HYDROGEOMATICS ENC Editor — standalone chart authoring workbench.
Run: python "ENC Editor.py"
Dependencies: PyQt6 pyqtgraph numpy pyproj shapely matplotlib; GDAL for S-57 import.
Projects and GeoJSON are editable source data, not validated navigational ENCs.
"""
import csv, json, math, sys, uuid, copy
from pathlib import Path
import numpy as np
from PyQt6 import QtCore as C, QtGui as G, QtWidgets as W
import pyqtgraph as pg
from pyproj import Transformer
from shapely.geometry import shape, mapping
from shapely.validation import explain_validity

FALLBACK={
 'SOUNDG':('Sounding','Point',[]),'DEPCNT':('Depth contour','Line',['VALDCO']),
 'DEPARE':('Depth area','Area',['DRVAL1','DRVAL2']),'COALNE':('Coastline','Line',['OBJNAM']),
 'LNDARE':('Land area','Area',['OBJNAM']),'WRECKS':('Wreck','Point;Area',['VALSOU','WATLEV','OBJNAM']),
 'OBSTRN':('Obstruction','Point;Line;Area',['VALSOU','WATLEV']),
 'BOYLAT':('Lateral buoy','Point',['CATLAM','COLOUR','OBJNAM']),
 'LIGHTS':('Light','Point',['COLOUR','LITCHR','VALNMR','HEIGHT']),
 'M_COVR':('Coverage','Area',['CATCOV']),'M_QUAL':('Quality of data','Area',['CATZOC'])}

def catalogue():
    objects={k:dict(name=v[0],primitives=v[1],attributes=v[2]) for k,v in FALLBACK.items()};attributes={}
    try:
        from osgeo import gdal
        gdal.UseExceptions()
        for filename,target in [('s57objectclasses.csv',objects),('s57attributes.csv',attributes)]:
            path=gdal.FindFile('gdal',filename)
            with open(path,encoding='utf-8-sig') as handle:
                for row in csv.DictReader(handle):
                    if filename.startswith('s57object'):
                        target[row['Acronym']]=dict(name=row['ObjectClass'],primitives=row['Primitives'],attributes=list(dict.fromkeys(a for key in ('Attribute_A','Attribute_B','Attribute_C') for a in row[key].split(';') if a)))
                    else:target[row['Acronym']]=row
    except (ImportError,OSError,TypeError,RuntimeError):pass
    return objects,attributes

OBJECTS,ATTRIBUTES=catalogue()
TO_MAP=Transformer.from_crs(4326,3395,always_xy=True)
TO_LL=Transformer.from_crs(3395,4326,always_xy=True)
COLORS={'SOUNDG':'#172554','DEPCNT':'#0284c7','DEPARE':'#bae6fd','COALNE':'#334155','LNDARE':'#fef08a','M_COVR':'#be123c','M_QUAL':'#a855f7'}

# Marine-inspired display palettes. These are not the complete S-52 presentation library.
MARINE_PALETTES={
 'Day':dict(background='#d8dde1',land='#d6c79e',drying='#a9bf9b',shallow='#85b8d3',unsafe='#b1d5e8',medium='#d7e8ef',deep='#f5f7f8',ink='#18282e',contour='#648c9c',magenta='#b22e8d',red='#d52628',green='#18743e',yellow='#c79700'),
 'Dusk':dict(background='#30363e',land='#766955',drying='#58644d',shallow='#345566',unsafe='#466575',medium='#526977',deep='#667681',ink='#e0d4b7',contour='#91a9b4',magenta='#cc69ad',red='#e46660',green='#6aae80',yellow='#c5a85b'),
 'Night':dict(background='#080d12',land='#28251e',drying='#152820',shallow='#102937',unsafe='#173642',medium='#203e49',deep='#2c4650',ink='#a8a58f',contour='#5d7982',magenta='#95658b',red='#aa514a',green='#528a66',yellow='#9c864d')}


def create_enc_collection(bases, destination, progress=lambda text: None):
    """Package original bytes, including available updates and referenced support files."""
    import hashlib, os, tempfile, zipfile
    from osgeo import gdal
    gdal.UseExceptions()
    bases=list(dict.fromkeys(Path(p).resolve() for p in bases))
    if not bases:raise ValueError('Select at least one base cell.')
    destination=Path(destination).resolve();entries=[];cells=[]
    for index,base in enumerate(bases):
        if base.suffix.lower()!='.000':raise ValueError('Select .000 base cells; updates are collected automatically.')
        root=base.parent;prefix=f'cells/{index+1:04d}/';files={base}
        updates=sorted(p for p in root.iterdir() if p.is_file() and p.stem.lower()==base.stem.lower() and len(p.suffix)==4 and p.suffix[1:].isdigit() and p.suffix!='.000')
        numbers=[int(p.suffix[1:]) for p in updates]
        if numbers and numbers!=list(range(1,max(numbers)+1)):raise ValueError('Missing or duplicate update number for '+base.name+'; provide the complete update sequence.')
        files.update(updates)
        # Read each record version too, so support references removed by later updates remain included.
        for source in [base]+updates:
            progress('Inspecting '+source.name)
            ds=gdal.OpenEx(str(source),gdal.OF_VECTOR,open_options=['UPDATES=IGNORE','RETURN_PRIMITIVES=ON','RETURN_LINKAGES=ON','LNAM_REFS=ON'])
            if ds is None:raise ValueError('Cannot inspect '+str(source))
            for layer in ds:
                for record in layer:
                    for key in ('TXTDSC','NTXTDS','PICREP'):
                        field=record.GetFieldIndex(key)
                        if field<0 or not record.IsFieldSetAndNotNull(field):continue
                        refs=record.GetField(field);refs=refs if isinstance(refs,list) else [refs]
                        for ref in refs:
                            if not str(ref).strip():continue
                            support=(root/str(ref).replace('\\','/')).resolve()
                            if not support.is_relative_to(root):raise ValueError('Support reference outside cell folder: '+str(ref))
                            if not support.is_file():raise ValueError('Missing referenced support file: '+str(support))
                            files.add(support)
            ds=None
        cells.append(dict(base=prefix+base.name,updates=[prefix+p.name for p in updates]))
        for path in sorted(files):entries.append((path,prefix+path.relative_to(root).as_posix()))
    if any(path==destination for path,_ in entries):raise ValueError('Output cannot replace a source file.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    handle,temp=tempfile.mkstemp(prefix='.encpack-',suffix='.tmp',dir=destination.parent);os.close(handle)
    manifest=dict(format='HYDROGEOMATICS lossless ENC collection',version=1,cells=cells,files=[],scope='Original selected base cells, all available same-name numbered updates, and referenced TXTDSC/NTXTDS/PICREP files. No feature merging or deduplication.')
    try:
        with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED,allowZip64=True) as archive:
            for source,name in entries:
                progress('Preserving '+source.name);digest=hashlib.sha256();size=0
                before=source.stat()
                with source.open('rb') as inp,archive.open(name,'w',force_zip64=True) as out:
                    while True:
                        block=inp.read(1024*1024)
                        if not block:break
                        out.write(block);digest.update(block);size+=len(block)
                after=source.stat()
                if before.st_size!=after.st_size or before.st_mtime_ns!=after.st_mtime_ns:raise ValueError('Source changed while copying: '+str(source))
                manifest['files'].append(dict(path=name,size=size,sha256=digest.hexdigest()))
            archive.writestr('manifest.json',json.dumps(manifest,indent=2))
        verify_enc_collection(temp,progress=progress)
        os.replace(temp,destination)
        return manifest
    finally:
        if os.path.exists(temp):os.unlink(temp)


def verify_enc_collection(path, extract_to=None, progress=lambda text: None):
    """Verify every archived byte before accepting a collection; extract only safe paths."""
    import hashlib, zipfile
    from pathlib import PurePosixPath
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist();names=[i.filename for i in infos]
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive paths.')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size>16*1024*1024:raise ValueError('Missing or oversized manifest.')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('format')!='HYDROGEOMATICS lossless ENC collection' or manifest.get('version')!=1:raise ValueError('Unsupported collection.')
        records=manifest['files'];paths=[r['path'] for r in records]
        if len(paths)!=len(set(paths)) or set(names)!=set(paths)|{'manifest.json'}:raise ValueError('Manifest does not match archive contents.')
        for record in records:
            name=record['path'];pure=PurePosixPath(name)
            if pure.is_absolute() or '..' in pure.parts or '\\' in name or ':' in name or not name.startswith('cells/') or pure.as_posix()!=name:raise ValueError('Unsafe collection path.')
            info=archive.getinfo(name)
            if info.file_size!=record['size']:raise ValueError('Incorrect file size: '+name)
            progress('Verifying '+name);digest=hashlib.sha256()
            with archive.open(name) as inp:
                while True:
                    block=inp.read(1024*1024)
                    if not block:break
                    digest.update(block)
            if digest.hexdigest()!=record['sha256']:raise ValueError('Checksum mismatch: '+name)
        if not manifest.get('cells'):raise ValueError('Collection has no cells.')
        for cell in manifest['cells']:
            if cell['base'] not in paths or not cell['base'].lower().endswith('.000') or any(u not in paths for u in cell['updates']):raise ValueError('Invalid cell inventory.')
        if extract_to is not None:
            root=Path(extract_to).resolve()
            for record in records:
                target=(root/record['path']).resolve()
                if not target.is_relative_to(root):raise ValueError('Unsafe extraction path.')
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(record['path']) as inp,target.open('wb') as out:
                    import shutil
                    shutil.copyfileobj(inp,out,1024*1024)
        return manifest

def read_chart_features(path):
    from osgeo import gdal
    gdal.UseExceptions();incoming=[]
    ds=gdal.OpenEx(str(path),gdal.OF_VECTOR,open_options=['SPLIT_MULTIPOINT=OFF','UPDATES=APPLY'])
    if ds is None:raise ValueError('Cannot open '+str(path))
    for layer in ds:
        if layer.GetName() not in OBJECTS:continue
        for record in layer:
            geo=record.GetGeometryRef()
            if geo is None:continue
            item=feature(layer.GetName(),json.loads(geo.ExportToJson()),{key:record.GetField(key) for key in OBJECTS[layer.GetName()]['attributes'] if record.GetFieldIndex(key)>=0 and record.IsFieldSetAndNotNull(record.GetFieldIndex(key))})
            item['source_chart']=str(path);incoming.append(item)
    return incoming

def feature(obj,geometry,attributes=None):
    return dict(id=uuid.uuid4().hex,object=obj,geometry=geometry,attributes=attributes or {},visible=True)

def vertices(geometry):
    kind=geometry['type'];coords=geometry['coordinates']
    if kind=='Point':return [coords]
    if kind=='Polygon':return coords[0]
    if kind in ('LineString','MultiPoint'):return coords
    return []

def validate_features(features):
    issues=[];ids=set()
    for f in features:
        fid=f['id'];obj=f['object'];geo=f['geometry'];attrs=f['attributes']
        def issue(message):issues.append((fid,message))
        if fid in ids:issue('Duplicate project feature ID.')
        ids.add(fid)
        if obj not in OBJECTS:issue('Unknown object class: '+obj)
        expected={'Point':'Point','MultiPoint':'Point','LineString':'Line','MultiLineString':'Line','Polygon':'Area','MultiPolygon':'Area'}.get(geo['type'])
        if expected and obj in OBJECTS and expected not in OBJECTS[obj]['primitives']:issue('Geometry type is not permitted for this object class.')
        try:
            g=shape(geo)
            if g.is_empty:issue('Empty geometry.')
            if not g.is_valid:issue(explain_validity(g))
            def walk(value):
                if isinstance(value,list) and value and isinstance(value[0],(int,float)):
                    if len(value)<2 or not all(math.isfinite(x) for x in value):issue('Non-finite coordinate.')
                    elif not (-180<=value[0]<=180 and -90<=value[1]<=90):issue('Coordinate outside WGS84 limits.')
                elif isinstance(value,list):
                    for item in value:walk(item)
            walk(geo['coordinates'])
            if obj=='SOUNDG' and not g.has_z:issue('Sounding needs depth in the third coordinate (metres).')
        except Exception as exc:issue('Invalid geometry: '+str(exc))
        for key,value in attrs.items():
            attr_type=ATTRIBUTES.get(key,{}).get('Attributetype')
            try:
                if attr_type in ('I','E') and (isinstance(value,bool) or float(value)!=int(value)):raise ValueError()
                if attr_type=='F' and not math.isfinite(float(value)):raise ValueError()
                if attr_type=='L':
                    values=value if isinstance(value,list) else str(value).split(',')
                    if any(isinstance(v,bool) or float(v)!=int(v) for v in values):raise ValueError()
            except (ValueError,TypeError,OverflowError):issue('Invalid catalogue attribute type: '+key)
        for key in attrs:
            if obj in OBJECTS and key not in OBJECTS[obj]['attributes'] and key not in ('DEPTH',):issue('Attribute is not listed for this class: '+key)
        if obj=='DEPARE':
            try:
                if float(attrs['DRVAL1'])>float(attrs['DRVAL2']):issue('DRVAL1 exceeds DRVAL2.')
            except (KeyError,ValueError,TypeError):issue('Depth area needs numeric DRVAL1 and DRVAL2.')
        if obj=='DEPCNT':
            try:float(attrs['VALDCO'])
            except (KeyError,ValueError,TypeError):issue('Depth contour needs numeric VALDCO.')
    # Group-1 area overlaps are useful authoring checks; they are not the full ENC topology rules.
    skin=[f for f in features if f['object'] in ('DEPARE','DRGARE','LNDARE','UNSARE') and f['geometry']['type'] in ('Polygon','MultiPolygon')]
    if skin:
        from shapely.strtree import STRtree
        geometries=[shape(f['geometry']) for f in skin];tree=STRtree(geometries)
        for i,g in enumerate(geometries):
            if not g.is_valid:continue
            for j in tree.query(g,predicate='intersects'):
                if j<=i or not geometries[j].is_valid:continue
                if g.intersection(geometries[j]).area>1e-12:issues.append((skin[i]['id'],'Coverage areas overlap: '+skin[j]['object']+' '+skin[j]['id'][:8]))
    return issues

def export_s57_draft(features, metadata, destination):
    """Write new base-cell geometry/topology and verify by GDAL read-back.
    This is not an update writer, exchange-set packager, or S-58 validator.
    """
    from osgeo import ogr,gdal
    from shapely.geometry.polygon import orient
    import tempfile, datetime, hashlib, re
    ogr.UseExceptions();gdal.UseExceptions()
    issues=validate_features(features)
    if issues:raise ValueError('Resolve authoring errors before S-57 export:\n'+'\n'.join(message for _,message in issues[:15]))
    if not features:raise ValueError('The project has no features.')
    destination=Path(destination)
    if not re.fullmatch(r'[A-Z0-9]{2}[1-6][A-Z0-9]{5}',destination.stem.upper()):raise ValueError('Use an 8-character cell name: two producer characters, usage digit 1–6, then five characters, e.g. ZZ5TEST1.000 for a draft test.')
    agency=int(metadata.get('agency') or 0);scale=int(metadata['scale']);edition=int(metadata.get('edition',1));datum=int(metadata.get('sounding_datum_code',0))
    if not 0<=agency<=65535 or scale<1 or edition<1:raise ValueError('Check producing-agency code, compilation scale and edition.')
    if datum<=0 or int(metadata.get('vertical_datum_code',0))<=0:raise ValueError('Set both S-57 sounding and vertical datum codes in Cell settings before export. Do not guess the survey datum.')
    nodes={};edges={};isolated=[];records=[]
    def node(p):
        key=tuple(round(float(v),7) for v in p[:2])
        if key not in nodes:nodes[key]=len(nodes)+1
        return nodes[key]
    def chain(coords,usage):
        refs=[]
        for a,b in zip(coords,coords[1:]):
            na,nb=node(a),node(b)
            if na==nb:continue
            key=(min(na,nb),max(na,nb));direction=1 if na<=nb else 2
            if key not in edges:edges[key]=len(edges)+1
            refs.append((130,edges[key],direction,usage,2))
        if not refs:raise ValueError('A line/ring collapses at S-57 coordinate precision.')
        return refs
    for i,f in enumerate(features,1):
        g=f['geometry'];kind=g['type'];coords=g['coordinates'];refs=[]
        if kind in ('Point','MultiPoint'):
            if kind=='MultiPoint' and f['object']!='SOUNDG':raise ValueError('Split non-sounding multipoints before draft export.')
            points=[coords] if kind=='Point' else coords
            if f['object']=='SOUNDG':
                if any(len(p)!=3 for p in points):raise ValueError('Every SOUNDG point must have depth.')
                geo=dict(type='MultiPoint',coordinates=points)
            else:geo=dict(type='Point',coordinates=coords[:2])
            isolated.append(geo);refs=[(110,len(isolated),255,255,255)];prim=1
        elif kind in ('LineString','MultiLineString'):
            for line in ([coords] if kind=='LineString' else coords):refs+=chain(line,1)
            prim=2
        elif kind in ('Polygon','MultiPolygon'):
            polys=[shape(g)] if kind=='Polygon' else list(shape(g).geoms)
            for poly in polys:
                poly=orient(poly,sign=-1);refs+=chain(list(poly.exterior.coords),1)
                for ring in poly.interiors:refs+=chain(list(ring.coords),2)
            prim=3
        else:raise ValueError('Unsupported geometry: '+kind)
        records.append((i,f,prim,refs))
    today=datetime.datetime.now().strftime('%Y%m%d')
    options=[f'S57_AGEN={agency}',f'S57_CSCL={scale}',f'S57_EDTN={edition}','S57_UPDN=0',f'S57_INTU={destination.stem[2]}',f'S57_ISDT={today}',f'S57_UADT={today}','S57_HDAT=2',f'S57_SDAT={datum}',f'S57_VDAT={int(metadata.get("vertical_datum_code",datum))}','S57_COMF=10000000','S57_SOMF=100','S57_AALL=1','S57_NALL=2','S57_COMT=DRAFT - independent ENC validation required',f'S57_NOIN={len(isolated)}',f'S57_NOCN={len(nodes)}',f'S57_NOED={len(edges)}',f'S57_NOMR={sum(f["object"].startswith("M_") for f in features)}',f'S57_NOGR={sum(not f["object"].startswith("M_") for f in features)}']
    with tempfile.TemporaryDirectory(prefix='enc_export_',dir=destination.parent) as temp:
        path=Path(temp)/destination.name
        ds=ogr.GetDriverByName('S57').CreateDataSource(str(path),options=options)
        if ds is None:raise RuntimeError('GDAL could not create S-57 output.')
        def write(layer_name,fields,geo=None):
            layer=ds.GetLayerByName(layer_name)
            if layer is None:raise ValueError('S-57 writer does not support '+layer_name)
            out=ogr.Feature(layer.GetLayerDefn())
            for key,value in fields.items():
                index=out.GetFieldIndex(key)
                if index<0:raise ValueError(f'Unsupported field {layer_name}.{key}')
                fieldtype=out.GetDefnRef().GetFieldDefn(index).GetType()
                if isinstance(value,list):
                    if fieldtype==ogr.OFTIntegerList:out.SetFieldIntegerList(index,[int(v) for v in value])
                    elif fieldtype==ogr.OFTStringList:out.SetFieldStringList(index,[str(v) for v in value])
                    else:out.SetField(key,','.join(str(v) for v in value))
                else:out.SetField(key,value)
            if geo:out.SetGeometry(ogr.CreateGeometryFromJson(json.dumps(geo)))
            if layer.CreateFeature(out)!=0:raise RuntimeError('Could not write '+layer_name)
        try:
            for i,geo in enumerate(isolated,1):write('IsolatedNode',dict(RCNM=110,RCID=i,RVER=1,RUIN=1),geo)
            for p,i in nodes.items():write('ConnectedNode',dict(RCNM=120,RCID=i,RVER=1,RUIN=1),dict(type='Point',coordinates=p))
            for (start,end),i in edges.items():
                fields=dict(RCNM=130,RCID=i,RVER=1,RUIN=1)
                for j,nodeid in enumerate((start,end)):
                    fields.update({f'NAME_RCNM_{j}':120,f'NAME_RCID_{j}':nodeid,f'ORNT_{j}':255,f'USAG_{j}':255,f'TOPI_{j}':j+1,f'MASK_{j}':255})
                write('Edge',fields)
            for i,f,prim,refs in records:
                fields=dict(RCID=i,PRIM=prim,GRUP=1 if f['object'] in ('DEPARE','DRGARE','LNDARE','UNSARE') else 2,AGEN=agency,FIDN=i,FIDS=1)
                for j,key in enumerate(('NAME_RCNM','NAME_RCID','ORNT','USAG','MASK')):fields[key]=[r[j] for r in refs]
                fields.update(f['attributes']);write(f['object'],fields)
        finally:ds=None
        check=gdal.OpenEx(str(path),gdal.OF_VECTOR,open_options=['SPLIT_MULTIPOINT=OFF','RETURN_PRIMITIVES=OFF'])
        if check is None:raise RuntimeError('Written S-57 file could not be reopened.')
        received={}
        for layer in check:
            if layer.GetName()=='DSID':continue
            for f in layer:received[f.GetField('FIDN')]=(layer.GetName(),f.Clone())
        if len(received)!=len(features):raise RuntimeError('S-57 read-back feature count does not match.')
        for i,f,_,_ in records:
            obj,actual=received[i];geom=actual.GetGeometryRef()
            if obj!=f['object'] or geom is None:raise RuntimeError('S-57 object/geometry verification failed.')
            before=shape(f['geometry']);after=shape(json.loads(geom.ExportToJson()))
            if before.hausdorff_distance(after)>2e-7:raise RuntimeError('S-57 geometry read-back differs from source.')
            for key,wanted in f['attributes'].items():
                got=actual.GetField(key)
                attr_type=ATTRIBUTES.get(key,{}).get('Attributetype')
                if attr_type in ('I','E','F'):
                    same=got is not None and abs(float(got)-float(wanted))<1e-8
                elif attr_type=='L':
                    def values(v):return [str(a).strip() for a in (v if isinstance(v,list) else str(v).split(','))]
                    same=values(got)==values(wanted)
                else:same=str(got)==str(wanted)
                if not same:raise RuntimeError(f'S-57 attribute read-back differs: {obj}.{key}. No output cell was replaced.')
            if obj=='SOUNDG':
                def depth_values(geo):return [geo.GetZ()] if ogr.GT_Flatten(geo.GetGeometryType())==ogr.wkbPoint else [v for j in range(geo.GetGeometryCount()) for v in depth_values(geo.GetGeometryRef(j))]
                wanted=[p[2] for p in (f['geometry']['coordinates'] if f['geometry']['type']=='MultiPoint' else [f['geometry']['coordinates']])]
                got=depth_values(geom)
                if len(wanted)!=len(got) or any(abs(a-b)>.011 for a,b in zip(sorted(wanted),sorted(got))):raise RuntimeError('S-57 sounding depth verification failed.')
        received.clear();actual=None;geom=None;check=None
        report=dict(status='DRAFT — not independently ENC validated',features=len(features),connected_nodes=len(nodes),edges=len(edges),isolated_nodes=len(isolated),checks=['basic authoring checks','S-57 read-back feature count','geometry comparison','sounding depth comparison','attribute comparison'],not_checked=['full S-58 validation','S-52 portrayal','complete ENC product specification','exchange-set catalogue','update compatibility'],sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        path.replace(destination)
        destination.with_suffix('.validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

# Live navigation uses non-blocking reads in the Qt timer; no acquisition thread touches UI.
def parse_nmea(sentence):
    sentence=sentence.strip()
    if not sentence.startswith('$') or '*' not in sentence:raise ValueError('NMEA checksum required')
    body,check=sentence[1:].rsplit('*',1);checksum=0
    for c in body:checksum^=ord(c)
    if len(check)!=2 or checksum!=int(check,16):raise ValueError('Invalid NMEA checksum')
    fields=body.split(',');kind=fields[0][-3:]
    def coordinate(value,hemisphere,latitude):
        if hemisphere not in ('N','S') if latitude else hemisphere not in ('E','W'):raise ValueError('Invalid hemisphere')
        number=float(value);degrees=int(number//100);minutes=number-degrees*100
        if not math.isfinite(number) or not 0<=minutes<60:raise ValueError('Invalid coordinate')
        result=degrees+minutes/60
        if hemisphere in ('S','W'):result=-result
        if abs(result)>(90 if latitude else 180):raise ValueError('Coordinate out of range')
        return result
    def number(value):
        if not value:return None
        value=float(value)
        if not math.isfinite(value):raise ValueError('Invalid number')
        return value
    if kind=='RMC':
        if fields[2]!='A':return dict(valid=False,message='RMC: no valid fix')
        result=dict(valid=True,lat=coordinate(fields[3],fields[4],True),lon=coordinate(fields[5],fields[6],False),speed=number(fields[7]),course=number(fields[8]),utc=fields[1],message='RMC fix')
        if len(fields)>12 and fields[12]=='N':result.update(valid=False,message='RMC: invalid mode')
        return result
    if kind=='GGA':
        quality=int(fields[6] or 0)
        if quality<=0:return dict(valid=False,message='GGA: no valid fix')
        return dict(valid=True,lat=coordinate(fields[2],fields[3],True),lon=coordinate(fields[4],fields[5],False),quality=quality,satellites=fields[7],hdop=fields[8],utc=fields[1],message='GGA fix')
    if kind=='GLL':
        if len(fields)<7 or fields[6]!='A' or (len(fields)>7 and fields[7]=='N'):return dict(valid=False,message='GLL: no valid fix')
        return dict(valid=True,lat=coordinate(fields[1],fields[2],True),lon=coordinate(fields[3],fields[4],False),utc=fields[5],message='GLL fix')
    if kind=='HDT':return dict(heading=number(fields[1]))
    if kind=='VTG':
        if len(fields)>9 and fields[9]=='N':return {}
        return dict(course=number(fields[1]),speed=number(fields[5]))
    return {}

def read_hydrographic_files(paths,config,cancel=lambda:False,progress=lambda message:None):
    """Read source data into WGS84 features; no UI calls in this function."""
    from shapely.ops import transform as transform_geometry
    transform=Transformer.from_crs(config['crs'],4326,always_xy=True)
    incoming=[];warnings=set();soundings=[]
    def append(kind,coordinates,label='',source=''):
        if cancel():raise InterruptedError('Import cancelled; no data added.')
        obj=config[{'Point':'point_class','LineString':'line_class','Polygon':'area_class'}[kind]]
        if kind=='Point':
            p=coordinates;lon,lat=transform.transform(p[0],p[1],errcheck=True)
            if not -85<lat<85:raise ValueError('Coordinate outside the Mercator viewer range. Check input CRS.')
            if obj=='SOUNDG':
                depth=float(label) if config['text_depth'] and label else float(p[2])*config['z_sign'] if config['use_z'] and len(p)>2 else None
                if depth is None:raise ValueError('SOUNDG requires a depth. Enable Z-as-depth or numeric-text depths, or choose another point class.')
                if not math.isfinite(depth):raise ValueError('Non-finite depth.')
                soundings.append([lon,lat,depth]);return
            geometry=dict(type='Point',coordinates=[lon,lat])
        else:
            geometry=json.loads(json.dumps(mapping(transform_geometry(transform.transform,shape(dict(type=kind,coordinates=coordinates))))))
        attrs={}
        if label and 'OBJNAM' in OBJECTS.get(obj,{}).get('attributes',[]):attrs['OBJNAM']=label
        if obj=='DEPCNT' and config['use_z']:
            z=[p[2] for p in coordinates if len(p)>2]
            if len(z)==len(coordinates) and max(z)-min(z)<1e-6:attrs['VALDCO']=float(z[0])*config['z_sign']
        f=feature(obj,geometry,attrs);f['source']=source;incoming.append(f)
    for filename in paths:
        path=Path(filename);progress('Reading '+path.name);source=str(path)
        if path.suffix.lower() in ('.csv','.xyz','.txt'):
            import re
            with path.open(encoding='utf-8-sig') as handle:
                first=True
                for number,line in enumerate(handle,1):
                    if cancel():raise InterruptedError('Import cancelled; no data added.')
                    if not line.strip() or line.lstrip().startswith('#'):continue
                    try:values=[float(v) for v in re.split(r'[,;\s]+',line.strip())[:3]]
                    except ValueError:
                        if first and all(v.lower() in ('x','y','z','depth','easting','northing','longitude','latitude','lon','lat') for v in re.split(r'[,;\s]+',line.strip())[:3]):first=False;continue
                        raise ValueError(f'{path.name}, line {number}: invalid numeric coordinates.')
                    first=False
                    if len(values)<3 or not all(math.isfinite(v) for v in values):raise ValueError(f'{path.name}, line {number}: expected finite X,Y,depth.')
                    lon,lat=transform.transform(*values[:2],errcheck=True)
                    if not -85<lat<85:raise ValueError('Coordinates outside viewer range; check input CRS.')
                    soundings.append([lon,lat,values[2]*config['z_sign']])
                    if number%10000==0:progress(f'{path.name}: {number:,} rows')
        elif path.suffix.lower() in ('.dxf','.dwg'):
            import ezdxf,tempfile,subprocess,os,shutil,time
            from ezdxf.path import make_path
            def read_dxf(dxf):
                doc=ezdxf.readfile(dxf)
                for index,entity in enumerate(doc.modelspace()):
                    if cancel():raise InterruptedError('Import cancelled; no data added.')
                    kind=entity.dxftype();layer=entity.dxf.layer
                    if config.get('layer') and layer!=config['layer']:continue
                    try:
                        if kind=='INSERT':entities=list(entity.virtual_entities())
                        else:entities=[entity]
                        for ent in entities:
                            k=ent.dxftype()
                            if k=='POINT':append('Point',list(ent.dxf.location),source=source)
                            elif k in ('TEXT','MTEXT'):
                                text=ent.plain_text() if k=='MTEXT' else ent.dxf.text
                                if config['text_depth']:
                                    try:float(text)
                                    except ValueError:continue
                                    append('Point',list(ent.dxf.insert),text,source)
                            elif k in ('LINE','LWPOLYLINE','POLYLINE','ARC','CIRCLE','SPLINE','ELLIPSE'):
                                if k=='LINE':pts=[list(ent.dxf.start),list(ent.dxf.end)];closed=False
                                else:
                                    curve=make_path(ent);pts=[list(p) for p in curve.flattening(config['tolerance'])];closed=curve.is_closed
                                if len(pts)>=2:append('Polygon' if closed and config['closed_areas'] else 'LineString',[pts] if closed and config['closed_areas'] else pts,source=source)
                            else:warnings.add(f'{path.name}: unsupported CAD entity {k} on {layer}')
                    except (InterruptedError,ValueError):raise
                    except Exception as exc:raise ValueError(f'{path.name}: {kind} on {layer}: {exc}')
                    if index%5000==0:progress(f'{path.name}: {index:,} CAD entities')
            if path.suffix.lower()=='.dxf':read_dxf(path)
            else:
                converter=config['converter']
                if not Path(converter).is_file():raise ValueError('Locate ODAFileConverter.exe to import DWG.')
                with tempfile.TemporaryDirectory(prefix='enc_dwg_') as temp:
                    source_dir=Path(temp)/'in';dest=Path(temp)/'out';source_dir.mkdir();dest.mkdir();shutil.copy2(path,source_dir/path.name)
                    environment={k:v for k,v in os.environ.items() if not k.startswith(('QT_','QML'))}
                    process=subprocess.Popen([converter,str(source_dir),str(dest),'ACAD2018','DXF','0','1','*.dwg'],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    started=time.monotonic()
                    while process.poll() is None:
                        if cancel() or time.monotonic()-started>180:process.kill();process.wait();raise InterruptedError('DWG conversion cancelled or timed out.')
                        time.sleep(.1)
                    files=list(dest.glob('*.dxf'))
                    if process.returncode or not files:raise ValueError('ODA could not convert this DWG.')
                    read_dxf(files[0])
        else:
            from osgeo import gdal
            gdal.UseExceptions();ds=gdal.OpenEx(str(path),gdal.OF_VECTOR)
            if ds is None:raise ValueError('Unsupported vector data source: '+str(path))
            for layer in ds:
                if config.get('layer') and layer.GetName()!=config['layer']:continue
                spatial=layer.GetSpatialRef();layer_transform=Transformer.from_crs(spatial.ExportToWkt(),4326,always_xy=True) if spatial else transform
                for item in layer:
                    if cancel():raise InterruptedError('Import cancelled; no data added.')
                    geo=item.GetGeometryRef()
                    if geo is None:continue
                    geometry=mapping(transform_geometry(layer_transform.transform,shape(json.loads(geo.ExportToJson()))));kind=geometry['type'];obj=config['point_class'] if 'Point' in kind else config['line_class'] if 'Line' in kind else config['area_class']
                    props={name:item.GetField(name) for name in OBJECTS.get(obj,{}).get('attributes',[]) if item.GetFieldIndex(name)>=0 and item.IsFieldSetAndNotNull(item.GetFieldIndex(name))}
                    f=feature(obj,json.loads(json.dumps(geometry)),props);f['source']=source;incoming.append(f)
            ds=None
    # Batch sounding geometries keep large surveys from creating one graphics item per observation.
    for start in range(0,len(soundings),50000):incoming.append(feature('SOUNDG',dict(type='MultiPoint',coordinates=soundings[start:start+50000])))
    return incoming,sorted(warnings)

class HydroImportWorker(C.QThread):
    loaded=C.pyqtSignal(object,object)
    failed=C.pyqtSignal(str)
    progress=C.pyqtSignal(str)
    def __init__(self,paths,config,parent=None):super().__init__(parent);self.paths=paths;self.config=config
    def run(self):
        try:
            features,warnings=read_hydrographic_files(self.paths,self.config,self.isInterruptionRequested,self.progress.emit)
            self.loaded.emit(features,warnings)
        except Exception as exc:self.failed.emit(str(exc))

class Editor(W.QMainWindow):
    def __init__(self):
        super().__init__()
        from matplotlib import font_manager
        G.QFontDatabase.addApplicationFont(font_manager.findfont('DejaVu Sans'))
        W.QApplication.instance().setFont(G.QFont('DejaVu Sans',9))
        self.setFont(G.QFont('DejaVu Sans',9))
        self.setWindowTitle('HYDROGEOMATICS ENC Editor');self.resize(1500,950)
        self.features=[];self.project_path=None;self.dirty=False;self.undo_stack=[];self.redo_stack=[];self.drawing=[];self.mode='Select';self.items={}
        self.metadata=dict(name='Untitled ENC project',cell_name='',scale=25000,horizontal_datum='WGS84',sounding_datum='',agency='',edition=1,source_notes='',sounding_datum_code=0,vertical_datum_code=0)
        self.default_metadata=copy.deepcopy(self.metadata)
        self.view=pg.PlotWidget();self.view.setBackground('w');self.view.setAspectLocked(True);self.view.showGrid(x=True,y=True,alpha=.15);self.view.setLabel('bottom','Easting (Mercator display metres)');self.view.setLabel('left','Northing (Mercator display metres)');self.setCentralWidget(self.view)
        self.view.scene().sigMouseClicked.connect(self.map_click);self.view.scene().sigMouseMoved.connect(self.mouse_move)
        self.table=W.QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['Visible','Object','Name / depth']);self.table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows);self.table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.load_selected);self.table.itemChanged.connect(self.visibility_changed)
        dock=W.QDockWidget('Chart features',self);dock.setWidget(self.table);self.addDockWidget(C.Qt.DockWidgetArea.LeftDockWidgetArea,dock)
        panel=W.QWidget();form=W.QVBoxLayout(panel)
        self.class_box=W.QComboBox();self.class_box.setEditable(True)
        for key,value in sorted(OBJECTS.items()):self.class_box.addItem(key+' — '+value['name'],key)
        self.class_box.setCurrentIndex(self.class_box.findData('SOUNDG'));form.addWidget(W.QLabel('Object class for new/selected feature'));form.addWidget(self.class_box)
        self.geometry_mode=W.QComboBox();self.geometry_mode.addItems(['Point','LineString','Polygon']);form.addWidget(self.geometry_mode)
        self.coordinates=W.QTableWidget(0,3);self.coordinates.setHorizontalHeaderLabels(['Longitude','Latitude','Depth (m)']);form.addWidget(W.QLabel('Vertices (WGS84); depth is positive down'));form.addWidget(self.coordinates)
        row=W.QHBoxLayout();form.addLayout(row)
        for label,fn in [('Add vertex',self.add_vertex),('Remove vertex',self.remove_vertex)]:b=W.QPushButton(label);b.clicked.connect(fn);row.addWidget(b)
        self.attr=W.QTableWidget(0,2);self.attr.setHorizontalHeaderLabels(['Attribute','Value']);form.addWidget(self.attr)
        self.attr_choice=W.QComboBox();form.addWidget(self.attr_choice);self.class_box.currentIndexChanged.connect(self.update_attributes);self.update_attributes()
        for label,fn in [('Add catalogue attribute',self.add_attribute),('Apply feature edits',self.apply_edits),('Delete selected feature',self.delete_selected)]:
            b=W.QPushButton(label);b.clicked.connect(lambda _,f=fn:self.safe(f));form.addWidget(b)
        scroll=W.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(panel);dock=W.QDockWidget('Feature editor',self);dock.setWidget(scroll);self.addDockWidget(C.Qt.DockWidgetArea.RightDockWidgetArea,dock)
        self.issue_list=W.QListWidget();self.issue_list.itemClicked.connect(self.issue_clicked);dock=W.QDockWidget('Validation — authoring checks, not full S-58 certification',self);dock.setWidget(self.issue_list);self.addDockWidget(C.Qt.DockWidgetArea.BottomDockWidgetArea,dock)
        toolbar=self.addToolBar('ENC authoring')
        actions=[('New',self.new_project),('Open',self.open_project),('Save',self.save_project),('Import data',self.import_dialog),('Export GeoJSON',self.export_geojson),('Export draft S-57',self.export_s57),('Coverage boundary',self.coverage_boundary),('Cell settings',self.cell_settings),('Draw',self.start_draw),('Finish drawing',self.finish_draw),('Cancel drawing',self.cancel_draw),('Fit',self.fit),('Zoom +',lambda:self.view.getViewBox().scaleBy((.8,.8))),('Zoom −',lambda:self.view.getViewBox().scaleBy((1.25,1.25))),('Undo',self.undo),('Redo',self.redo),('Contours',self.contours),('Validate',self.validate)]
        for label,fn in actions:
            action=toolbar.addAction(label);action.triggered.connect(lambda _,f=fn:self.safe(f))
        self.snap=W.QCheckBox('Snap vertices');self.snap.setChecked(True);toolbar.addWidget(self.snap)
        self.statusBar().showMessage('Draw: choose class and geometry, click map, then Finish drawing. Select: click a feature or its list row.')
        self.view.setRange(xRange=(7400000,7550000),yRange=(2750000,2900000),padding=0)
        self.authoring_toolbar=toolbar
        self.setup_navigation()
        self.preview_item=pg.PlotDataItem(pen=pg.mkPen('#ef4444',width=2),symbol='o',symbolSize=5);self.view.addItem(self.preview_item)
        self.setup_map_navigation()

    def setup_navigation(self):
        import time
        self.enc_view_features=[];self.enc_view_path=None;self.nav_connection=None;self.nav_buffer=b'';self.nav_fix={};self.nav_last_fix=0;self.nav_valid=False;self.nav_track=[];self.nav_errors=0;self.nav_motion={}
        panel=W.QWidget();form=W.QFormLayout(panel)
        self.nav_transport=W.QComboBox();self.nav_transport.addItems(['Serial','UDP','TCP']);form.addRow('GNSS input',self.nav_transport)
        self.nav_address=W.QLineEdit('COM3');form.addRow('COM port / host',self.nav_address)
        self.nav_port=W.QSpinBox();self.nav_port.setRange(1,65535);self.nav_port.setValue(10110);form.addRow('Network port',self.nav_port)
        self.nav_baud=W.QComboBox();self.nav_baud.addItems(['4800','9600','38400','57600','115200']);self.nav_baud.setCurrentText('38400');form.addRow('Serial baud',self.nav_baud)
        def transport_changed():
            mode=self.nav_transport.currentText();self.nav_address.setText('COM3' if mode=='Serial' else '127.0.0.1');self.nav_port.setEnabled(mode!='Serial');self.nav_baud.setEnabled(mode=='Serial')
        self.nav_transport.currentIndexChanged.connect(transport_changed);transport_changed()
        self.nav_connect=W.QPushButton('Connect GNSS');self.nav_connect.clicked.connect(lambda:self.safe(self.connect_gnss));form.addRow(self.nav_connect)
        disconnect=W.QPushButton('Disconnect');disconnect.clicked.connect(self.disconnect_gnss);form.addRow(disconnect)
        self.nav_follow=W.QCheckBox('Follow vessel');self.nav_follow.setChecked(True);form.addRow(self.nav_follow)
        self.nav_show_track=W.QCheckBox('Show track');self.nav_show_track.setChecked(True);self.nav_show_track.toggled.connect(lambda value:self.nav_track_item.setVisible(value));form.addRow(self.nav_show_track)
        self.nav_readout=W.QLabel('Disconnected');self.nav_readout.setWordWrap(True);form.addRow(self.nav_readout)
        note=W.QLabel('ENC display is an authoring/navigation aid with simplified symbols; it is not an approved ECDIS. Stale or invalid fixes are marked explicitly.');note.setWordWrap(True);form.addRow(note)
        dock=W.QDockWidget('ENC viewer / GNSS navigation',self);dock.setWidget(panel);self.addDockWidget(C.Qt.DockWidgetArea.LeftDockWidgetArea,dock)
        self.nav_boat=pg.ScatterPlotItem(symbol='o',size=14,pen=pg.mkPen('white',width=2),brush='#dc2626');self.nav_boat.setZValue(1000);self.view.addItem(self.nav_boat)
        self.nav_heading=pg.PlotDataItem(pen=pg.mkPen('#dc2626',width=2));self.nav_heading.setZValue(999);self.view.addItem(self.nav_heading)
        self.nav_track_item=pg.PlotDataItem(pen=pg.mkPen('#f97316',width=1.5),connect='finite');self.nav_track_item.setZValue(998);self.view.addItem(self.nav_track_item)
        self.nav_timer=C.QTimer(self);self.nav_timer.setInterval(100);self.nav_timer.timeout.connect(self.poll_gnss);self.nav_timer.start()
        tools=self.addToolBar('ENC viewer')
        self.setup_chart_display(tools)
        for caption,fn in [('Create ENC collection…',self.create_collection),('Open ENC collection…',self.open_collection),('View ENC…',self.view_enc),('Import hydrographic data…',self.import_hydrographic),('Validate / export chart…',self.validated_chart_export),('Fit charts',self.fit),('Clear viewed ENC',self.clear_viewed_enc),('Centre vessel',self.center_vessel),('Clear GNSS track',self.clear_nav_track)]:
            action=tools.addAction(caption);action.triggered.connect(lambda _,f=fn:self.safe(f))

    def setup_map_navigation(self):
        self.addToolBarBreak()
        toolbar=self.addToolBar('Map navigation');toolbar.setObjectName('MapNavigation')
        toolbar.setMovable(False)
        menu=self.menuBar().addMenu('Navigation')
        self.navigation_group=G.QActionGroup(self);self.navigation_group.setExclusive(True)
        self.zoom_window_action=G.QAction('Zoom window',self)
        self.pan_action=G.QAction('Pan',self)
        for action,shortcut,tip,callback in (
            (self.zoom_window_action,'Ctrl+Shift+Z','Drag with the left mouse button to zoom into a rectangle.',self.zoom_window),
            (self.pan_action,'Ctrl+Shift+P','Drag with the left mouse button to move the chart. Mouse wheel zooms.',self.pan_chart)):
            action.setCheckable(True);action.setShortcut(shortcut);action.setToolTip(tip+' ('+shortcut+')')
            self.navigation_group.addAction(action);toolbar.addAction(action);menu.addAction(action)
            action.triggered.connect(callback)
        for caption,callback in [('Zoom +',lambda:self.navigation_zoom(.8)),('Zoom −',lambda:self.navigation_zoom(1.25)),('Fit charts',self.fit)]:
            action=toolbar.addAction(caption);action.triggered.connect(callback);menu.addAction(action)
        self.navigation_hint=W.QLabel('  Pan: drag chart • Mouse wheel: zoom')
        toolbar.addWidget(self.navigation_hint)
        self.pan_chart()

    def navigation_zoom(self,factor):
        self.nav_follow.setChecked(False)
        self.view.getViewBox().scaleBy((factor,factor))

    def setup_chart_display(self,tools):
        self.palette_box=W.QComboBox();self.palette_box.addItems(MARINE_PALETTES);tools.addWidget(self.palette_box)
        self.view_only=W.QCheckBox('Chart viewer mode');tools.addWidget(self.view_only)
        self.show_soundings=W.QCheckBox('Soundings');self.show_soundings.setChecked(True);tools.addWidget(self.show_soundings)
        self.show_chart_labels=W.QCheckBox('Names');self.show_chart_labels.setChecked(True);tools.addWidget(self.show_chart_labels)
        self.show_chart_meta=W.QCheckBox('Quality / coverage boundaries');tools.addWidget(self.show_chart_meta)
        depths=self.addToolBar('Depth shading')
        self.depth_settings={}
        for name,value in [('Shallow',2),('Safety',5),('Deep',10)]:
            depths.addWidget(W.QLabel(name+' (m) '));spin=W.QDoubleSpinBox();spin.setRange(0,12000);spin.setValue(value);spin.setDecimals(1);depths.addWidget(spin);self.depth_settings[name]=spin;spin.valueChanged.connect(lambda *_:self.render())
        self.sounding_size=W.QDoubleSpinBox();self.sounding_size.setRange(6,18);self.sounding_size.setValue(10);self.sounding_size.setSuffix(' pt soundings');depths.addWidget(self.sounding_size)
        self._sounding_labels=[];self._sounding_batches=[];self.sounding_timer=C.QTimer(self);self.sounding_timer.setSingleShot(True);self.sounding_timer.timeout.connect(self.draw_sounding_labels)
        self.view.getViewBox().sigRangeChanged.connect(lambda *_:self.sounding_timer.start(120));self.sounding_size.valueChanged.connect(lambda *_:self.sounding_timer.start(120))
        self.chart_legend=W.QLabel('Land • drying • shallow blue • deep water');depths.addWidget(self.chart_legend)
        self.palette_box.currentIndexChanged.connect(lambda *_:self.render())
        for checkbox in (self.show_soundings,self.show_chart_labels,self.show_chart_meta):checkbox.toggled.connect(lambda *_:self.render())
        self.view_only.toggled.connect(self.set_chart_viewer_mode)

    def set_chart_viewer_mode(self,enabled):
        self.cancel_draw()
        for dock in self.findChildren(W.QDockWidget):
            if dock.windowTitle()!='ENC viewer / GNSS navigation':dock.setVisible(not enabled)
        self.authoring_toolbar.setVisible(not enabled)
        self.view.showGrid(x=not enabled,y=not enabled,alpha=.15)
        self.render()

    def marine_style(self,f):
        palette=MARINE_PALETTES[self.palette_box.currentText()];obj=f['object'];attrs=f['attributes']
        style=dict(stroke=palette['ink'],fill=None,z=0,width=1,symbol='o',size=6)
        if obj=='LNDARE':style.update(fill=palette['land'],z=-80)
        elif obj in ('DEPARE','DRGARE'):
            try:depth=float(attrs['DRVAL1'])
            except (ValueError,TypeError,KeyError):depth=None
            shallow=self.depth_settings['Shallow'].value();safety=max(shallow,getattr(self,'effective_safety_contour',self.depth_settings['Safety'].value()));deep=max(safety,self.depth_settings['Deep'].value())
            if depth is None:colour=palette['background']
            elif depth<0:colour=palette['drying']
            elif depth<shallow:colour=palette['shallow']
            elif depth<safety:colour=palette['unsafe']
            elif depth<deep:colour=palette['medium']
            else:colour=palette['deep']
            style.update(fill=colour,stroke=palette['contour'],z=-100)
        elif obj in ('LAKARE','RIVERS','CANALS'):style.update(fill=palette['unsafe'],stroke=palette['contour'],z=-70)
        elif obj=='DEPCNT':
            try:safety_line=abs(float(attrs['VALDCO'])-self.effective_safety_contour)<.001
            except (ValueError,TypeError,KeyError,AttributeError):safety_line=False
            style.update(stroke=palette['ink'] if safety_line else palette['contour'],width=2.5 if safety_line else .8,z=5)
        elif obj=='COALNE':style.update(width=1.4,z=8)
        elif obj in ('RESARE','CTNARE','ACHARE','CBLARE','PIPARE') or obj.startswith('M_'):style.update(stroke=palette['magenta'],z=10)
        elif obj.startswith(('BOY','BCN')):
            colour=attrs.get('COLOUR',[])
            if isinstance(colour,str):colour=colour.split(',')
            if not isinstance(colour,list):colour=[colour]
            colour=[str(v) for v in colour]
            chosen=palette['red'] if '3' in colour else palette['green'] if '4' in colour else palette['yellow'] if '6' in colour else palette['ink']
            style.update(stroke=chosen,symbol='d' if obj.startswith('BOY') else 't',size=11,z=20)
        elif obj=='LIGHTS':style.update(stroke=palette['magenta'],symbol='star',size=13,z=21)
        elif obj in ('WRECKS','OBSTRN','UWTROC'):style.update(symbol='x',size=11,z=20)
        elif obj=='SOUNDG':style.update(size=1,z=15)
        return style

    def collection_progress(self):
        dialog=W.QProgressDialog('Preparing collection…','Cancel',0,0,self)
        dialog.setWindowModality(C.Qt.WindowModality.ApplicationModal);dialog.setMinimumDuration(0)
        def update(text):
            dialog.setLabelText(text);W.QApplication.processEvents()
            if dialog.wasCanceled():raise ValueError('Collection operation cancelled; existing files unchanged.')
        return dialog,update

    def create_collection(self):
        names,_=W.QFileDialog.getOpenFileNames(self,'Select original ENC base cells — updates and referenced support files included','','S-57 base cells (*.000)')
        if not names:return
        target,_=W.QFileDialog.getSaveFileName(self,'Save portable collection (original cells preserved)','Charts.encpack','HYDROGEOMATICS ENC collection (*.encpack)')
        if not target:return
        if not target.lower().endswith('.encpack'):target+='.encpack'
        progress,update=self.collection_progress()
        try:manifest=create_enc_collection(names,target,update)
        finally:progress.reset()
        W.QMessageBox.information(self,'Collection saved',f"Preserved {len(manifest['cells'])} cells and {len(manifest['files'])} original files. All checksums verified.\n\nOpen ENC collection displays these charts together. This portable archive is not a new .000 cell.")

    def open_collection(self):
        import tempfile
        name,_=W.QFileDialog.getOpenFileName(self,'Open portable ENC collection','','HYDROGEOMATICS ENC collection (*.encpack)')
        if not name:return
        progress,update=self.collection_progress();folder=tempfile.TemporaryDirectory(prefix='HYDROGEOMATICS-enc-')
        try:
            manifest=verify_enc_collection(name,folder.name,update)
            progress.reset()
            loaded=self.load_enc_charts([str(Path(folder.name)/cell['base']) for cell in manifest['cells']])
            if not loaded:folder.cleanup();return
        except Exception:
            folder.cleanup();raise
        finally:progress.reset()
        previous=getattr(self,'_collection_folder',None)
        self._collection_folder=folder
        if previous:previous.cleanup()
        self.enc_view_path=name
        self.statusBar().showMessage(f"Collection opened: {len(manifest['cells'])} cells; {len(manifest['files'])} files verified. Original data preserved; viewer uses supported chart symbols.")

    def view_enc(self):
        names,_=W.QFileDialog.getOpenFileNames(self,'Open existing charts (.000 base cells; adjacent updates are applied)','','S-57 charts (*.000 *.enc *.s57);;All files (*)')
        if names:self.load_enc_charts(names)

    def load_enc_charts(self,names):
        from osgeo import gdal
        gdal.UseExceptions();incoming=[]
        progress=W.QProgressDialog('Reading ENC charts…','Cancel',0,len(names),self);progress.setWindowModality(C.Qt.WindowModality.ApplicationModal);progress.setMinimumDuration(0)
        try:
            for index,name in enumerate(names):
                progress.setValue(index);progress.setLabelText('Reading '+Path(name).name);W.QApplication.processEvents()
                if progress.wasCanceled():return
                ds=gdal.OpenEx(str(name),gdal.OF_VECTOR,open_options=['SPLIT_MULTIPOINT=OFF','UPDATES=APPLY'])
                if ds is None:raise ValueError('Could not open '+str(name))
                for layer in ds:
                    if layer.GetName() not in OBJECTS:continue
                    for f in layer:
                        geo=f.GetGeometryRef()
                        if geo is None:continue
                        item=feature(layer.GetName(),json.loads(geo.ExportToJson()),{key:f.GetField(key) for key in OBJECTS[layer.GetName()]['attributes'] if f.GetFieldIndex(key)>=0 and f.IsFieldSetAndNotNull(f.GetFieldIndex(key))})
                        item['source_chart']=str(name);incoming.append(item)
                ds=None
        finally:progress.close()
        self.enc_view_features=incoming;self.enc_view_path='; '.join(str(n) for n in names);self.view_only.setChecked(True);self.render();self.fit()
        self.statusBar().showMessage(f'Viewing {len(names)} chart(s), {len(incoming):,} features. Marine colour display; source files unchanged.')
        return True

    def render(self):
        if not hasattr(self,'palette_box'):return
        palette=MARINE_PALETTES[self.palette_box.currentText()];self.view.setBackground(palette['background'])
        for axis in ('left','bottom'):
            self.view.getAxis(axis).setTextPen(palette['ink']);self.view.getAxis(axis).setPen(palette['contour'])
        for items in self.items.values():
            for item in items:self.view.removeItem(item)
        self.items={};self._sounding_batches=[]
        displayed=self.enc_view_features+([] if self.view_only.isChecked() else self.features)
        contours=[]
        for f in displayed:
            if f['object']=='DEPCNT':
                try:contours.append(float(f['attributes']['VALDCO']))
                except (KeyError,ValueError,TypeError):pass
        requested=self.depth_settings['Safety'].value();self.effective_safety_contour=next((v for v in sorted(set(contours)) if v>=requested),requested)
        self.chart_legend.setText(f'Land / drying / blue depth zones • Safety contour: {self.effective_safety_contour:g} m'+(' (not present)' if self.effective_safety_contour not in contours else ''))
        for f in displayed:
            if not f.get('visible',True):continue
            if f['object']=='SOUNDG' and not self.show_soundings.isChecked():continue
            if f['object'].startswith('M_') and not self.show_chart_meta.isChecked() and f in self.enc_view_features:continue
            items=[];style=self.marine_style(f);color=style['stroke']
            if f['object']=='SOUNDG' and f['geometry']['type'] in ('Point','MultiPoint'):
                pts=vertices(f['geometry']);a=np.asarray(pts)
                if len(a) and a.shape[1]>=3:
                    x,y=getattr(self,"chart_transform",TO_MAP).transform(a[:,0],a[:,1]);self._sounding_batches.append((np.asarray(x),np.asarray(y),a[:,2]))
                    item=pg.ScatterPlotItem(x,y,size=2,pen=None,brush=color);self.view.addItem(item);item.setZValue(15);items.append(item)
                    self.items[f['id']]=items;continue
            def add(item,z):item.setZValue(z);self.view.addItem(item);items.append(item)
            def draw(geo):
                kind=geo['type'];coords=geo['coordinates']
                if kind.startswith('Multi'):
                    subtype={'MultiPoint':'Point','MultiLineString':'LineString','MultiPolygon':'Polygon'}.get(kind)
                    if subtype:
                        for c in coords:draw(dict(type=subtype,coordinates=c))
                    return
                if kind not in ('Point','LineString','Polygon'):return
                rings=coords if kind=='Polygon' else [coords] if kind=='LineString' else [[coords]]
                if not rings or not rings[0]:return
                if kind=='Polygon' and style['fill']:
                    path=G.QPainterPath();path.setFillRule(C.Qt.FillRule.OddEvenFill)
                    for ring in coords:
                        a=np.asarray(ring);xx,yy=getattr(self,"chart_transform",TO_MAP).transform(a[:,0],a[:,1]);path.moveTo(xx[0],yy[0])
                        for x,y in zip(xx[1:],yy[1:]):path.lineTo(x,y)
                        path.closeSubpath()
                    fill=W.QGraphicsPathItem(path);fill.setPen(G.QPen(C.Qt.PenStyle.NoPen));fill.setBrush(G.QColor(style['fill']));add(fill,style['z'])
                for ring in rings:
                    a=np.asarray(ring);x,y=getattr(self,"chart_transform",TO_MAP).transform(a[:,0],a[:,1])
                    item=pg.PlotDataItem(x,y,pen=None if kind=='Point' else pg.mkPen(color,width=style['width']),symbol=style['symbol'] if kind=='Point' else None,symbolSize=style['size'],symbolBrush=color,symbolPen=color)
                    item._chart_pen=None if kind=='Point' else pg.mkPen(color,width=style['width']);add(item,max(1,style['z']))
                pts=vertices(geo);label=f['attributes'].get('OBJNAM','') if self.show_chart_labels.isChecked() else ''
                if f['object']=='SOUNDG' and pts and len(pts[0])>2:label=f'{pts[0][2]:g}'
                if label and pts:
                    text=pg.TextItem(str(label),color=palette['ink'],anchor=(0,1));text.setFont(G.QFont('DejaVu Sans',9));text.setPos(*getattr(self,"chart_transform",TO_MAP).transform(*pts[0][:2]));add(text,30)
            draw(f['geometry']);self.items[f['id']]=items
        self.draw_sounding_labels()

    def clear_viewed_enc(self):self.enc_view_features=[];self.enc_view_path=None;self.render()
    def clear_nav_track(self):self.nav_track=[];self.nav_track_item.setData([],[])
    def connect_gnss(self):
        import socket,time,errno
        self.disconnect_gnss();mode=self.nav_transport.currentText();address=self.nav_address.text().strip()
        connection=None
        try:
            if mode=='Serial':
                import serial
                connection=serial.Serial(address,int(self.nav_baud.currentText()),timeout=0)
            else:
                connection=socket.socket(socket.AF_INET,socket.SOCK_DGRAM if mode=='UDP' else socket.SOCK_STREAM);connection.setblocking(False)
                if mode=='UDP':connection.bind((address,self.nav_port.value()))
                else:
                    result=connection.connect_ex((address,self.nav_port.value()))
                    if result not in (0,errno.EINPROGRESS,errno.EWOULDBLOCK,10035):raise OSError(result,'TCP connection failed')
            self.nav_connection=connection;self.nav_mode=mode;self.nav_started=time.monotonic();self.nav_tcp_ready=mode!='TCP';self.nav_connect.setEnabled(False)
            for widget in (self.nav_transport,self.nav_address,self.nav_port,self.nav_baud):widget.setEnabled(False)
            self.nav_readout.setText('Connected; waiting for a valid NMEA position.' if mode!='TCP' else 'Connecting TCP…')
        except Exception:
            if connection is not None:connection.close()
            raise
    def disconnect_gnss(self):
        if self.nav_connection is not None:self.nav_connection.close()
        self.nav_connection=None;self.nav_buffer=b'';self.nav_valid=False;self.nav_motion={};self.nav_boat.setData([],[]);self.nav_heading.setData([],[])
        self.nav_readout.setText('Disconnected');self.nav_connect.setEnabled(True)
        for widget in (self.nav_transport,self.nav_address,self.nav_port,self.nav_baud):widget.setEnabled(True)
    def consume_nmea(self,line,now=None):
        import time
        now=time.monotonic() if now is None else now
        try:data=parse_nmea(line)
        except (ValueError,IndexError,TypeError):self.nav_errors+=1;return
        for key in ('speed','course','heading'):
            if data.get(key) is not None:self.nav_motion[key]=(data[key],now)
        if 'valid' in data:
            if not data['valid']:
                self.nav_valid=False;self.nav_fix.update(message=data['message']);return
            if abs(data['lat'])>=85:self.nav_valid=False;self.nav_fix.update(message='Fix outside Mercator display range');return
            gap=not self.nav_valid or now-self.nav_last_fix>5
            self.nav_valid=True;self.nav_last_fix=now;self.nav_fix.update(data)
            x,y=TO_MAP.transform(data['lon'],data['lat'])
            if self.nav_track and gap:self.nav_track.append((float('nan'),float('nan')))
            if not self.nav_track or self.nav_track[-1]!=(x,y):self.nav_track.append((x,y));self.nav_track=self.nav_track[-10000:]
            if self.nav_follow.isChecked():self.center_vessel()
    def center_vessel(self):
        import time
        if not self.nav_valid or time.monotonic()-self.nav_last_fix>5:return
        x,y=TO_MAP.transform(self.nav_fix['lon'],self.nav_fix['lat']);vb=self.view.getViewBox();ranges=vb.viewRange();dx=ranges[0][1]-ranges[0][0];dy=ranges[1][1]-ranges[1][0]
        vb.setRange(xRange=(x-dx/2,x+dx/2),yRange=(y-dy/2,y+dy/2),padding=0)
    def update_nav_display(self,now):
        fresh=self.nav_valid and now-self.nav_last_fix<=5
        if 'lon' not in self.nav_fix:
            self.nav_readout.setText(f'Waiting for valid fix. Rejected sentences: {self.nav_errors}');return
        x,y=TO_MAP.transform(self.nav_fix['lon'],self.nav_fix['lat']);self.nav_boat.setData([x],[y],brush='#dc2626' if fresh else '#9ca3af',symbol='o' if fresh else 'x')
        def value(key):
            data=self.nav_motion.get(key);return data[0] if data and now-data[1]<=5 else None
        course=value('heading');course=value('course') if course is None else course
        if fresh and course is not None:
            length=(self.view.getViewBox().viewRange()[0][1]-self.view.getViewBox().viewRange()[0][0])*.035;angle=math.radians(course)
            self.nav_heading.setData([x,x+length*math.sin(angle)],[y,y+length*math.cos(angle)])
        else:self.nav_heading.setData([],[])
        if self.nav_track:
            xy=np.asarray(self.nav_track);self.nav_track_item.setData(xy[:,0],xy[:,1])
        state='LIVE' if fresh else 'STALE / NO FIX';speed=value('speed')
        self.nav_readout.setText(f'{state}\nLat {self.nav_fix["lat"]:.7f}  Lon {self.nav_fix["lon"]:.7f}\nSpeed {speed if speed is not None and fresh else "—"} kn | Heading/COG {course if course is not None and fresh else "—"}°\nUTC {self.nav_fix.get("utc","—")} | {self.nav_fix.get("message","")}\nRejected sentences: {self.nav_errors}')
    def poll_gnss(self):
        import time,socket,select
        if self.nav_connection is None:return
        now=time.monotonic()
        try:
            if self.nav_mode=='TCP' and not self.nav_tcp_ready:
                _,ready,errors=select.select([], [self.nav_connection],[self.nav_connection],0)
                if errors or (ready and self.nav_connection.getsockopt(socket.SOL_SOCKET,socket.SO_ERROR)):raise OSError('TCP connection failed')
                if not ready:
                    if now-self.nav_started>10:raise OSError('TCP connection timed out')
                    return
                self.nav_tcp_ready=True
            for _ in range(32):
                try:chunk=self.nav_connection.read(4096) if self.nav_mode=='Serial' else self.nav_connection.recv(4096)
                except BlockingIOError:break
                if not chunk:
                    if self.nav_mode=='TCP':raise OSError('TCP connection closed')
                    break
                self.nav_buffer+=chunk
                if len(self.nav_buffer)>65536:self.nav_buffer=b'';self.nav_errors+=1
                while b'\n' in self.nav_buffer:
                    raw,self.nav_buffer=self.nav_buffer.split(b'\n',1);self.consume_nmea(raw.decode('ascii',errors='replace').strip(),now)
            self.update_nav_display(now)
        except Exception as exc:self.disconnect_gnss();self.nav_readout.setText('GNSS disconnected: '+str(exc))

    def zoom_window(self):
        self.cancel_draw();self.nav_follow.setChecked(False);self._zoom_window=True
        self.view.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        self.view.viewport().setCursor(C.Qt.CursorShape.CrossCursor)
        if hasattr(self,'zoom_window_action'):
            self.zoom_window_action.setChecked(True);self.navigation_hint.setText('  Zoom window: drag a rectangle • Escape: Pan')
        self.statusBar().showMessage('Drag a rectangle to zoom. Select Pan or press Escape to return to panning.')
    def pan_chart(self):
        self.cancel_draw();self.nav_follow.setChecked(False);self._zoom_window=False
        self.view.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self.view.viewport().setCursor(C.Qt.CursorShape.OpenHandCursor)
        if hasattr(self,'pan_action'):
            self.pan_action.setChecked(True);self.navigation_hint.setText('  Pan: drag chart • Mouse wheel: zoom')
    def draw_sounding_labels(self):
        for item in getattr(self,'_sounding_labels',[]):self.view.removeItem(item)
        self._sounding_labels=[]
        if not self.show_soundings.isChecked():return
        vb=self.view.getViewBox();ranges=vb.viewRange();candidates=[]
        for x,y,depths in getattr(self,'_sounding_batches',[]):
            mask=(x>=ranges[0][0])&(x<=ranges[0][1])&(y>=ranges[1][0])&(y<=ranges[1][1])
            ids=np.flatnonzero(mask)
            candidates.extend((float(depths[i]),float(x[i]),float(y[i])) for i in ids)
        color=MARINE_PALETTES[self.palette_box.currentText()]['ink'];size=self.sounding_size.value();bins={};shown=0
        font=G.QFont('DejaVu Serif');font.setPointSizeF(size);font.setItalic(True);metrics=G.QFontMetricsF(font)
        for depth,x,y in sorted(candidates):
            if shown>=2000:break
            value=f'{depth:g}';whole,separator,frac=value.partition('.')
            html=f'<span style="font-family:DejaVu Serif;font-size:{size}pt;font-style:italic;color:{color}">{whole}'+(f'<sub style="font-size:{size*.7}pt">{frac}</sub>' if separator else '')+'</span>'
            pos=vb.mapViewToScene(C.QPointF(x,y));width=metrics.horizontalAdvance(whole+frac)+8;height=metrics.height()*1.6+8;box=C.QRectF(pos.x()-width/2,pos.y()-height,width,height)
            keys=[(a,b) for a in range(math.floor(box.left()/80),math.floor(box.right()/80)+1) for b in range(math.floor(box.top()/80),math.floor(box.bottom()/80)+1)]
            if any(box.intersects(other) for key in keys for other in bins.get(key,[])):continue
            for key in keys:bins.setdefault(key,[]).append(box)
            item=pg.TextItem(html=html,anchor=(.5,1));item.setPos(x,y);item.setZValue(31);self.view.addItem(item,ignoreBounds=True);self._sounding_labels.append(item);shown+=1

    def import_hydrographic(self):
        if getattr(self,'_import_worker',None) and self._import_worker.isRunning():raise ValueError('An import is already running.')
        paths,_=W.QFileDialog.getOpenFileNames(self,'Import hydrographic sources','','Hydrographic data (*.dxf *.dwg *.csv *.xyz *.txt *.gpkg *.shp *.geojson);;All files (*)')
        if not paths:return
        dialog=W.QDialog(self);dialog.setWindowTitle('Source CRS and S-57 object mapping');form=W.QFormLayout(dialog)
        crs=W.QLineEdit('EPSG:32642');form.addRow('Input CRS (CAD / text; GIS uses embedded CRS)',crs)
        choices={}
        for key,label,primitive,default in [('point_class','Points','Point','SOUNDG'),('line_class','Lines','Line','DEPCNT'),('area_class','Closed areas','Area','DEPARE')]:
            box=W.QComboBox();box.addItems(sorted(k for k,v in OBJECTS.items() if primitive in v['primitives']));box.setCurrentText(default);form.addRow(label+' → object class',box);choices[key]=box
        z=W.QCheckBox('Use CAD Z/elevation as sounding or contour depth');form.addRow(z)
        sign=W.QComboBox();sign.addItems(['Positive Z is depth','Negative Z is depth']);form.addRow('Depth sign',sign)
        text=W.QCheckBox('Import numeric CAD TEXT/MTEXT as depths');form.addRow(text)
        areas=W.QCheckBox('Import closed CAD curves as polygons');areas.setChecked(True);form.addRow(areas)
        layer=W.QLineEdit();layer.setPlaceholderText('Blank = all layers');form.addRow('Source layer filter',layer)
        tolerance=W.QDoubleSpinBox();tolerance.setRange(.000001,1000);tolerance.setDecimals(6);tolerance.setValue(.1);form.addRow('Curve approximation tolerance (source units)',tolerance)
        converter=W.QLineEdit(r'C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe');form.addRow('ODA converter for DWG',converter)
        browse=W.QPushButton('Locate ODA converter…');form.addRow(browse)
        def locate():
            name,_=W.QFileDialog.getOpenFileName(dialog,'ODAFileConverter.exe','','Executable (*.exe)')
            if name:converter.setText(name)
        browse.clicked.connect(locate)
        note=W.QLabel('Class mapping is explicit: import each CAD layer with the appropriate class. Missing required attributes remain validation issues. CSV/XYZ columns are X,Y,depth. No datum reduction is applied.');note.setWordWrap(True);form.addRow(note)
        buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel);form.addRow(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        if dialog.exec()!=W.QDialog.DialogCode.Accepted:return
        config={key:box.currentText() for key,box in choices.items()};config.update(crs=crs.text(),use_z=z.isChecked(),text_depth=text.isChecked(),z_sign=-1 if sign.currentIndex() else 1,closed_areas=areas.isChecked(),layer=layer.text().strip(),converter=converter.text(),tolerance=tolerance.value())
        progress=W.QProgressDialog('Importing hydrographic data…','Cancel',0,0,self);progress.setWindowModality(C.Qt.WindowModality.ApplicationModal);progress.show()
        worker=HydroImportWorker(paths,config,self);self._import_worker=worker;progress.canceled.connect(worker.requestInterruption);worker.progress.connect(progress.setLabelText)
        def received(features,warnings):
            cancelled=worker.isInterruptionRequested();progress.reset()
            if cancelled:return
            self.checkpoint();self.features.extend(features);self.view_only.setChecked(False);self.refresh();self.fit()
            self.issue_list.clear()
            for message in warnings:self.issue_list.addItem(message)
            self.statusBar().showMessage(f'Imported {len(features):,} feature groups; {len(warnings)} distinct import warnings. Run Validate before export.')
        def failed(message):progress.reset();self.statusBar().showMessage(message);W.QMessageBox.warning(self,'Import result',message)
        worker.loaded.connect(received);worker.failed.connect(failed);worker.finished.connect(worker.deleteLater);worker.finished.connect(lambda:setattr(self,'_import_worker',None));worker.start()

    def validated_chart_export(self):
        features=self.enc_view_features if self.view_only.isChecked() else self.features
        if not features:raise ValueError('Load or draw a chart first.')
        issues=validate_features(features);self.issue_list.clear()
        if issues:
            for fid,message in issues:self.issue_list.addItem(fid[:8]+': '+message)
            raise ValueError(f'{len(issues)} authoring validation issues must be resolved before export. These checks do not replace full S-58 validation.')
        name,selected=W.QFileDialog.getSaveFileName(self,'Export checked chart / current view','chart.pdf','PDF chart (*.pdf);;PNG chart (*.png);;SVG chart (*.svg);;Draft S-57 (*.000)')
        if not name:return
        path=Path(name)
        if not path.suffix:path=path.with_suffix('.'+{'PDF':'pdf','PNG':'png','SVG':'svg','Draft':'000'}[selected.split()[0]])
        if path.suffix.lower()=='.000':export_s57_draft(features,self.metadata,path);return
        self.draw_sounding_labels()
        if path.suffix.lower()=='.pdf':
            writer=G.QPdfWriter(str(path));writer.setPageSize(G.QPageSize(C.QSizeF(1189,841),G.QPageSize.Unit.Millimeter));writer.setResolution(150)
            painter=G.QPainter(writer)
            try:self.view.scene().render(painter,C.QRectF(0,0,writer.width(),writer.height()),self.view.mapToScene(self.view.viewport().rect()).boundingRect())
            finally:painter.end()
        elif path.suffix.lower()=='.svg':
            from PyQt6.QtSvg import QSvgGenerator
            generator=QSvgGenerator();generator.setFileName(str(path));generator.setSize(self.view.viewport().size());generator.setViewBox(C.QRect(C.QPoint(0,0),self.view.viewport().size()))
            painter=G.QPainter(generator)
            try:self.view.scene().render(painter,C.QRectF(generator.viewBox()),self.view.mapToScene(self.view.viewport().rect()).boundingRect())
            finally:painter.end()
        elif path.suffix.lower()=='.png':
            import pyqtgraph.exporters
            exporter=pg.exporters.ImageExporter(self.view.plotItem);exporter.parameters()['width']=3000;exporter.export(str(path))
        else:raise ValueError('Choose PDF, PNG, SVG or .000.')
        report=dict(status='Authoring checks passed; not full S-58 validation',feature_groups=len(features),checks=['geometry validity','catalogue attribute checks','area overlap checks'],scope='Current visible chart view; sounding labels may be thinned for readability',format=path.suffix)
        path.with_suffix(path.suffix+'.validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8');self.statusBar().showMessage('Chart and validation report saved: '+str(path))

    def safe(self,fn):
        try:return fn()
        except Exception as exc:W.QMessageBox.warning(self,'ENC Editor',str(exc))
    def changed(self):self.dirty=True;self.setWindowTitle('HYDROGEOMATICS ENC Editor — '+self.metadata['name']+' *')
    def checkpoint(self):self.undo_stack.append(copy.deepcopy((self.features,self.metadata)));self.undo_stack=self.undo_stack[-30:];self.redo_stack.clear();self.changed()
    def undo(self):
        if self.undo_stack:self.redo_stack.append(copy.deepcopy((self.features,self.metadata)));self.features,self.metadata=self.undo_stack.pop();self.changed();self.refresh()
    def redo(self):
        if self.redo_stack:self.undo_stack.append(copy.deepcopy((self.features,self.metadata)));self.features,self.metadata=self.redo_stack.pop();self.changed();self.refresh()
    def selected(self):
        row=self.table.currentRow();return self.features[row] if 0<=row<len(self.features) else None
    def update_attributes(self):
        self.attr_choice.clear();obj=self.class_box.currentData();self.attr_choice.addItems(OBJECTS.get(obj,{}).get('attributes',[]))
    def add_attribute(self):
        key=self.attr_choice.currentText()
        if key and not any(self.attr.item(i,0) and self.attr.item(i,0).text()==key for i in range(self.attr.rowCount())):
            r=self.attr.rowCount();self.attr.insertRow(r);self.attr.setItem(r,0,W.QTableWidgetItem(key));self.attr.setItem(r,1,W.QTableWidgetItem(''))
    def add_vertex(self):self.coordinates.insertRow(self.coordinates.rowCount())
    def remove_vertex(self):
        if self.coordinates.currentRow()>=0:self.coordinates.removeRow(self.coordinates.currentRow())
    def load_selected(self):
        f=self.selected()
        if not f:return
        self.class_box.setCurrentIndex(self.class_box.findData(f['object']));self.geometry_mode.setCurrentText(f['geometry']['type'])
        rows=vertices(f['geometry']);rows=rows[:500] if f['geometry']['type'].startswith('Multi') else rows;self.coordinates.setRowCount(len(rows))
        for i,p in enumerate(rows):
            for j in range(3):self.coordinates.setItem(i,j,W.QTableWidgetItem(str(p[j]) if j<len(p) else ''))
        self.attr.setRowCount(len(f['attributes']))
        for i,(k,v) in enumerate(f['attributes'].items()):
            self.attr.setItem(i,0,W.QTableWidgetItem(k));self.attr.setItem(i,1,W.QTableWidgetItem(json.dumps(v) if isinstance(v,(list,dict)) else str(v)))
        for fid,items in self.items.items():
            for item in items:
                if isinstance(item,pg.PlotDataItem):item.setPen(pg.mkPen('#ef4444',width=2) if fid==f['id'] else getattr(item,'_chart_pen',None))
    def visibility_changed(self,item):
        if item.column()==0 and item.row()<len(self.features):self.checkpoint();self.features[item.row()]['visible']=item.checkState()==C.Qt.CheckState.Checked;self.render()
    def apply_edits(self):
        f=self.selected()
        if not f:raise ValueError('Select a feature first.')
        if f['geometry']['type'] not in ('Point','LineString','Polygon') or (f['geometry']['type']=='Polygon' and len(f['geometry']['coordinates'])>1):raise ValueError('Multipart geometries and polygons with holes are preserved on import; vertex-table editing is limited to simple geometry.')
        pts=[]
        for r in range(self.coordinates.rowCount()):
            values=[self.coordinates.item(r,c).text().strip() if self.coordinates.item(r,c) else '' for c in range(3)]
            pts.append([float(values[0]),float(values[1])]+([float(values[2])] if values[2] else []))
        kind=self.geometry_mode.currentText();geometry=self.make_geometry(kind,pts)
        attrs={}
        for i in range(self.attr.rowCount()):
            key=self.attr.item(i,0).text().strip();value=self.attr.item(i,1).text().strip() if self.attr.item(i,1) else ''
            if key and value:
                try:attrs[key]=json.loads(value)
                except ValueError:attrs[key]=value
        obj=self.class_box.currentData()
        if not obj:raise ValueError('Select a catalogue object class.')
        self.checkpoint();f.update(object=obj,geometry=geometry,attributes=attrs);self.refresh()
    @staticmethod
    def make_geometry(kind,pts):
        minimum={'Point':1,'LineString':2,'Polygon':3}[kind]
        if len(pts)<minimum:raise ValueError(f'{kind} needs at least {minimum} vertices.')
        if kind=='Point' and len(pts)!=1:raise ValueError('Point needs exactly one vertex.')
        if any(not -180<=p[0]<=180 or not -85<p[1]<85 or not all(math.isfinite(v) for v in p) for p in pts):raise ValueError('Use finite WGS84 coordinates within the Mercator display range (85° latitude).')
        if kind=='Polygon' and pts[0]!=pts[-1]:pts=pts+[pts[0][:]]
        geo=dict(type=kind,coordinates=pts[0] if kind=='Point' else [pts] if kind=='Polygon' else pts)
        if not shape(geo).is_valid:raise ValueError(explain_validity(shape(geo)))
        return geo
    def delete_selected(self):
        f=self.selected()
        if f:self.checkpoint();self.features.remove(f);self.refresh()
    def start_draw(self):self.pan_chart();self.mode='Draw';self.drawing=[];self.statusBar().showMessage('Click vertices on the map. Finish drawing or press Enter; Escape cancels.')
    def cancel_draw(self):self.mode='Select';self.drawing=[];self.preview_item.setData([],[])
    def map_click(self,event):
        if getattr(self,'_zoom_window',False):return
        if event.button()!=C.Qt.MouseButton.LeftButton:return
        vb=self.view.getViewBox()
        if not vb.sceneBoundingRect().contains(event.scenePos()):return
        p=vb.mapSceneToView(event.scenePos());lon,lat=TO_LL.transform(p.x(),p.y())
        if self.mode=='Draw':
            if self.snap.isChecked():
                tolerance=abs(vb.viewRange()[0][1]-vb.viewRange()[0][0])/max(self.view.width(),1)*10;best=tolerance
                for f in ([] if self.view_only.isChecked() else self.features)+self.enc_view_features:
                    if not f.get('visible',True):continue
                    for vertex in vertices(f['geometry']):
                        x,y=TO_MAP.transform(*vertex[:2]);distance=math.hypot(x-p.x(),y-p.y())
                        if distance<best:best=distance;lon,lat=vertex[:2]
            self.drawing.append([lon,lat]);xy=np.asarray([TO_MAP.transform(*p) for p in self.drawing]);self.preview_item.setData(xy[:,0],xy[:,1])
            if self.geometry_mode.currentText()=='Point':self.safe(self.finish_draw)
        else:
            target=np.array([p.x(),p.y()]);best=(float('inf'),None)
            for f in ([] if self.view_only.isChecked() else self.features)+self.enc_view_features:
                if not f.get('visible',True):continue
                from shapely.ops import transform
                from shapely.geometry import Point
                distance=transform(TO_MAP.transform,shape(f['geometry'])).distance(Point(*target))
                if distance<best[0]:best=(distance,f)
            tolerance=abs(vb.viewRange()[0][1]-vb.viewRange()[0][0])/max(self.view.width(),1)*12
            if best[1] and best[0]<=tolerance:
                if best[1] in self.features:self.table.selectRow(self.features.index(best[1]))
                else:
                    dialog=W.QDialog(self);dialog.setWindowTitle('ENC object — read-only: '+best[1]['object']);dialog.resize(600,450)
                    layout=W.QVBoxLayout(dialog);text=W.QPlainTextEdit(json.dumps(best[1],indent=2));text.setReadOnly(True);layout.addWidget(text);dialog.exec()
    def finish_draw(self):
        if not self.drawing:return
        obj=self.class_box.currentData()
        if not obj:raise ValueError('Choose an object class.')
        pts=copy.deepcopy(self.drawing)
        if obj=='SOUNDG':
            depth,ok=W.QInputDialog.getDouble(self,'Sounding','Depth in metres (positive down)',0,-100,12000,2)
            if not ok:return
            pts=[p+[depth] for p in pts]
        geo=self.make_geometry(self.geometry_mode.currentText(),pts);self.checkpoint();self.features.append(feature(obj,geo));self.cancel_draw();self.refresh();self.table.selectRow(len(self.features)-1)
    def mouse_move(self,pos):
        if self.view.getViewBox().sceneBoundingRect().contains(pos):
            p=self.view.getViewBox().mapSceneToView(pos);lon,lat=TO_LL.transform(p.x(),p.y());self.statusBar().showMessage(f'Longitude {lon:.7f}  Latitude {lat:.7f} | {self.mode} | {len(self.features)} features')
    def keyPressEvent(self,event):
        if event.key()==C.Qt.Key.Key_Escape:self.cancel_draw();self.pan_chart()
        elif event.key() in (C.Qt.Key.Key_Return,C.Qt.Key.Key_Enter) and self.mode=='Draw':self.safe(self.finish_draw)
        else:super().keyPressEvent(event)
    def refresh(self):
        self.table.blockSignals(True);self.table.setRowCount(len(self.features))
        for i,f in enumerate(self.features):
            check=W.QTableWidgetItem('');check.setFlags(check.flags()|C.Qt.ItemFlag.ItemIsUserCheckable);check.setCheckState(C.Qt.CheckState.Checked if f.get('visible',True) else C.Qt.CheckState.Unchecked);self.table.setItem(i,0,check)
            self.table.setItem(i,1,W.QTableWidgetItem(f['object']));pts=vertices(f['geometry']);name=f['attributes'].get('OBJNAM','')
            if f['object']=='SOUNDG' and pts and len(pts[0])>2:name=f'{pts[0][2]:g} m'
            self.table.setItem(i,2,W.QTableWidgetItem(str(name)))
        self.table.blockSignals(False);self.render()
    def fit(self):
        displayed=self.enc_view_features+([] if self.view_only.isChecked() else self.features)
        bounds=[shape(f['geometry']).bounds for f in displayed if f.get('visible',True) and not shape(f['geometry']).is_empty]
        if not bounds:return
        bounds=np.asarray(bounds);west,south=bounds[:,:2].min(axis=0);east,north=bounds[:,2:].max(axis=0)
        x0,y0=TO_MAP.transform(west,south);x1,y1=TO_MAP.transform(east,north)
        if all(math.isfinite(v) for v in (x0,y0,x1,y1)):
            self.view.setRange(xRange=(x0-1,x1+1),yRange=(y0-1,y1+1),padding=.05)

    def validate(self):
        self.issue_list.clear();issues=validate_features(self.features)
        for fid,message in issues:
            item=W.QListWidgetItem(next(f['object'] for f in self.features if f['id']==fid)+': '+message);item.setData(C.Qt.ItemDataRole.UserRole,fid);self.issue_list.addItem(item)
        if not issues:self.issue_list.addItem('Basic authoring checks passed. Full IHO S-58 / production validation has not been performed.')
        return issues
    def issue_clicked(self,item):
        fid=item.data(C.Qt.ItemDataRole.UserRole)
        for i,f in enumerate(self.features):
            if f['id']==fid:self.table.selectRow(i);break
    def confirm_save(self):
        if not self.dirty:return True
        answer=W.QMessageBox.question(self,'Unsaved project','Save changes before continuing?',W.QMessageBox.StandardButton.Save|W.QMessageBox.StandardButton.Discard|W.QMessageBox.StandardButton.Cancel)
        if answer==W.QMessageBox.StandardButton.Cancel:return False
        if answer==W.QMessageBox.StandardButton.Save:return bool(self.save_project())
        return True
    def new_project(self):
        if self.confirm_save():self.features=[];self.project_path=None;self.metadata=copy.deepcopy(self.default_metadata);self.undo_stack.clear();self.redo_stack.clear();self.dirty=False;self.cancel_draw();self.refresh()
    def save_project(self):
        path=self.project_path
        if not path:
            name,_=W.QFileDialog.getSaveFileName(self,'Save ENC authoring project','ENC_Project.henc','HYDROGEOMATICS ENC project (*.henc)')
            if not name:return False
            path=Path(name)
        payload=dict(format='HYDROGEOMATICS ENC authoring project',version=1,metadata=self.metadata,features=self.features)
        temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(payload,indent=2,allow_nan=False),encoding='utf-8');temp.replace(path);self.project_path=path;self.dirty=False;self.setWindowTitle('HYDROGEOMATICS ENC Editor — '+self.metadata['name']);return True
    def open_project(self):
        if not self.confirm_save():return
        name,_=W.QFileDialog.getOpenFileName(self,'Open project','','HYDROGEOMATICS ENC project (*.henc)')
        if not name:return
        data=json.loads(Path(name).read_text(encoding='utf-8'))
        if data.get('format')!='HYDROGEOMATICS ENC authoring project' or data.get('version')!=1:raise ValueError('Unsupported project format.')
        for f in data['features']:shape(f['geometry'])
        self.features=data['features'];self.metadata=dict(self.default_metadata,**data['metadata']);self.project_path=Path(name);self.dirty=False;self.undo_stack.clear();self.redo_stack.clear();self.cancel_draw();self.refresh();self.fit()
    def import_dialog(self):
        name,_=W.QFileDialog.getOpenFileName(self,'Import chart or survey data','','Supported (*.000 *.geojson *.json *.csv *.xyz *.txt);;All files (*)')
        if not name:return
        path=Path(name);incoming=[]
        if path.suffix.lower() in ('.csv','.xyz','.txt'):
            crs,ok=W.QInputDialog.getText(self,'Survey coordinates','Input CRS (e.g. EPSG:32642 or EPSG:4326)',text='EPSG:4326')
            if not ok:return
            incoming=self.read_soundings(path,crs)
        elif path.suffix.lower() in ('.json','.geojson'):
            data=json.loads(path.read_text(encoding='utf-8-sig'))
            for f in data['features']:
                props=dict(f.get('properties') or {});obj=props.pop('OBJL_NAME',props.pop('object_class',''))
                if obj not in OBJECTS:raise ValueError('GeoJSON features require an OBJL_NAME property with an S-57 acronym.')
                shape(f['geometry']);incoming.append(feature(obj,f['geometry'],props))
        else:
            from osgeo import gdal
            ds=gdal.OpenEx(str(path),gdal.OF_VECTOR,open_options=['SPLIT_MULTIPOINT=OFF','LNAM_REFS=ON'])
            if ds is None:raise ValueError('Cannot open S-57 dataset.')
            for layer in ds:
                if layer.GetName() not in OBJECTS:continue
                for f in layer:
                    geo=f.GetGeometryRef()
                    if geo is None:continue
                    allowed=OBJECTS[layer.GetName()]['attributes'];attrs={key:f.GetField(key) for key in allowed if f.GetFieldIndex(key)>=0 and f.IsFieldSetAndNotNull(f.GetFieldIndex(key))}
                    incoming.append(feature(layer.GetName(),json.loads(geo.ExportToJson()),attrs))
        self.checkpoint();self.features.extend(incoming);self.refresh();self.fit();self.statusBar().showMessage(f'Imported {len(incoming)} features. Original source files are unchanged.')
    @staticmethod
    def read_soundings(path,crs):
        import re
        transform=Transformer.from_crs(crs,4326,always_xy=True);result=[]
        for number,line in enumerate(Path(path).read_text(encoding='utf-8-sig').splitlines(),1):
            if not line.strip() or line.lstrip().startswith('#'):continue
            fields=re.split(r'[,;\s]+',line.strip())
            try:x,y,z=map(float,fields[:3])
            except (ValueError,TypeError):
                if number==1 and any(c.isalpha() for c in line):continue
                raise ValueError(f'Invalid X,Y,depth on line {number}.')
            lon,lat=transform.transform(x,y,errcheck=True);geo=Editor.make_geometry('Point',[[lon,lat,z]]);result.append(feature('SOUNDG',geo))
        if not result:raise ValueError('No soundings found.')
        return result
    def coverage_boundary(self):
        if not self.features:raise ValueError('Load or draw features first.')
        bounds=np.asarray([shape(f['geometry']).bounds for f in self.features if f['object']!='M_COVR'])
        if len(bounds)==0:raise ValueError('No source features to cover.')
        west,south=bounds[:,:2].min(axis=0);east,north=bounds[:,2:].max(axis=0)
        pad=max(max(east-west,north-south)*.01,.00001)
        from shapely.geometry import box
        geometry=json.loads(json.dumps(mapping(box(west-pad,south-pad,east+pad,north+pad))))
        self.checkpoint();self.features.append(feature('M_COVR',geometry,{'CATCOV':1}));self.refresh();self.fit()
        self.statusBar().showMessage('Draft rectangular coverage added. Edit it to the actual chart coverage.')
    def export_s57(self):
        self.validate()
        name,_=W.QFileDialog.getSaveFileName(self,'Export DRAFT S-57 base cell',self.metadata.get('cell_name') or 'ZZ5TEST1.000','S-57 base cell (*.000)')
        if not name:return
        target=Path(name).with_suffix('.000')
        report=export_s57_draft(self.features,self.metadata,target)
        self.statusBar().showMessage(f"Draft S-57 saved: {target} — {report['features']} verified features. Independent ENC validation required.")

    def export_geojson(self):
        name,_=W.QFileDialog.getSaveFileName(self,'Export editable features (not an ENC exchange set)','ENC_features.geojson','GeoJSON (*.geojson)')
        if name:
            data=dict(type='FeatureCollection',features=[dict(type='Feature',id=f['id'],geometry=f['geometry'],properties=dict(f['attributes'],OBJL_NAME=f['object'])) for f in self.features])
            Path(name).write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')
    def cell_settings(self):
        dialog=W.QDialog(self);dialog.setWindowTitle('ENC cell/project information');layout=W.QFormLayout(dialog);fields={}
        for key,value in self.metadata.items():fields[key]=W.QLineEdit(str(value));layout.addRow(key.replace('_',' ').title(),fields[key])
        buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel);layout.addRow(buttons);buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject)
        if dialog.exec()==W.QDialog.DialogCode.Accepted:
            values={k:v.text() for k,v in fields.items()};values['scale']=int(values['scale']);values['edition']=int(values['edition'])
            if min(values['scale'],values['edition'])<=0:raise ValueError('Scale and edition must be positive.')
            if values['horizontal_datum'].upper()!='WGS84':raise ValueError('Stored project coordinates use WGS84.')
            self.checkpoint();self.metadata=values;self.changed()
    def contours(self):
        points=[p for f in self.features if f['object']=='SOUNDG' and f.get('visible',True) for p in vertices(f['geometry']) if len(p)>2]
        if len(points)<3:raise ValueError('At least three non-collinear point soundings are needed.')
        interval,ok=W.QInputDialog.getDouble(self,'Generate draft contours','Contour interval (metres)',1,.01,1000,2)
        if not ok:return
        from matplotlib.figure import Figure
        from matplotlib.tri import Triangulation
        a=np.asarray(points);x,y=TO_MAP.transform(a[:,0],a[:,1]);tri=Triangulation(x,y)
        levels=np.arange(math.ceil(a[:,2].min()/interval)*interval,a[:,2].max(),interval)
        if len(levels)==0 or len(levels)>500:raise ValueError('Choose an interval producing between 1 and 500 contours.')
        fig=Figure();ax=fig.add_subplot();cs=ax.tricontour(tri,a[:,2],levels=levels);incoming=[]
        for level,segments in zip(cs.levels,cs.allsegs):
            for segment in segments:
                if len(segment)<2:continue
                lon,lat=TO_LL.transform(segment[:,0],segment[:,1]);incoming.append(feature('DEPCNT',dict(type='LineString',coordinates=np.column_stack((lon,lat)).tolist()),{'VALDCO':float(level),'INFORM':'Draft interpolated contour; review against source survey.'}))
        self.checkpoint();self.features.extend(incoming);self.refresh();self.statusBar().showMessage(f'Created {len(incoming)} draft contours within the sounding convex hull. Review gaps and survey coverage before use.')
    def closeEvent(self,event):
        if getattr(self,'_import_worker',None) and self._import_worker.isRunning():
            self._import_worker.requestInterruption();event.ignore();self.statusBar().showMessage('Cancelling import; close again after it stops.');return
        try:
            if self.confirm_save():self.disconnect_gnss();self.nav_timer.stop();event.accept()
            else:event.ignore()
        except Exception as exc:event.ignore();W.QMessageBox.warning(self,'Save failed',str(exc))

def main():
    app=W.QApplication(sys.argv);app.setApplicationName('HYDROGEOMATICS ENC Editor');window=Editor();window.showMaximized();return app.exec()
if __name__=='__main__':sys.exit(main())
