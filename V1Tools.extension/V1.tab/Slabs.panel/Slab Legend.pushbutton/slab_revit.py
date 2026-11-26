# -*- coding: utf-8 -*-
"""Revit adapter. All mutations are inside one rollback-capable transaction group."""
import json
import re
from System import Guid, String
from System.Collections.Generic import List
from pyrevit import DB
import slab_logic as logic

SCHEMA_GUID = Guid('b33fc4df-086e-4fba-986b-a13e5b7b770c')

def ids(values):
    return List[DB.ElementId]([v if isinstance(v, DB.ElementId) else DB.ElementId(int(v)) for v in values])

def schema():
    s = DB.ExtensibleStorage.Schema.Lookup(SCHEMA_GUID)
    if s:return s
    b = DB.ExtensibleStorage.SchemaBuilder(SCHEMA_GUID)
    b.SetSchemaName('SlabLegendV1')
    b.AddSimpleField('Data', String)
    return b.Finish()

def read_meta(element):
    entity = element.GetEntity(schema())
    return json.loads(entity.Get[String]('Data')) if entity.IsValid() else {}

def write_meta(element, value):
    entity = DB.ExtensibleStorage.Entity(schema())
    entity.Set[String]('Data', json.dumps(value))
    element.SetEntity(entity)

def owned_elements(doc, view):
    return [e for e in DB.FilteredElementCollector(doc, view.Id).WhereElementIsNotElementType()
            if e.OwnerViewId == view.Id and isinstance(e, (DB.TextNote, DB.CurveElement, DB.FilledRegion))]

def rgb(color):
    return (int(color.Red), int(color.Green), int(color.Blue))

def floor_record(floor):
    p = floor.get_Parameter(DB.BuiltInParameter.STRUCTURAL_ELEVATION_AT_TOP)
    a = floor.get_Parameter(DB.BuiltInParameter.HOST_AREA_COMPUTED)
    th = floor.get_Parameter(DB.BuiltInParameter.FLOOR_ATTR_THICKNESS_PARAM)
    row = {'id':floor.Id.IntegerValue,
           'top_mm':p.AsDouble()*304.8 if p and p.HasValue else None,
           'area_m2':a.AsDouble()*0.09290304 if a and a.HasValue else 0,
           'thk_mm':th.AsDouble()*304.8 if th and th.HasValue else None,
           'top_faces':[]}
    if row['area_m2'] + 1e-8 < logic.MIN_AREA_M2 or row['top_mm'] is None:return row
    try:
        for ref in DB.HostObjectUtils.GetTopFaces(floor):
            face = floor.GetGeometryObjectFromReference(ref)
            planar = isinstance(face, DB.PlanarFace)
            row['top_faces'].append({'planar':planar,
                'normal':[face.FaceNormal.X,face.FaceNormal.Y,face.FaceNormal.Z] if planar else None,
                'z':face.Origin.Z if planar else None})
    except Exception as exc:
        row['geometry_error'] = str(exc)
    return row

def source_layout(doc, source):
    """Identify the existing three-column table; never guess a new text or line style."""
    elements = owned_elements(doc, source)
    texts = [e for e in elements if isinstance(e, DB.TextNote)]
    rows = sorted([e for e in texts if re.match(r'^EL\.\s*[+-]?\d', e.Text.strip())], key=lambda e:-e.Coord.Y)
    if len(rows) < 2:raise ValueError('Reference legend needs at least two existing elevation rows.')
    row_h = rows[0].Coord.Y - rows[1].Coord.Y
    if any(abs((a.Coord.Y-b.Coord.Y)-row_h)>1e-6 for a,b in zip(rows,rows[1:])):
        raise ValueError('Reference legend row spacing is not uniform.')
    lines = [e for e in elements if isinstance(e, DB.CurveElement)]
    regions = [e for e in elements if isinstance(e, DB.FilledRegion)]
    horizontal = [e for e in lines if abs(e.GeometryCurve.GetEndPoint(0).Y-e.GeometryCurve.GetEndPoint(1).Y)<1e-7]
    ys = sorted(set(round(e.GeometryCurve.GetEndPoint(0).Y,9) for e in horizontal), reverse=True)
    bottom = min(ys)
    first_top = min(y for y in ys if y > rows[0].Coord.Y)
    dynamic = []
    palette, anchors, color_types = [], {}, {}
    for t in rows:
        thks = [x for x in texts if abs(x.Coord.Y-t.Coord.Y)<1e-6 and x.Coord.X>t.Coord.X]
        swatches = []
        for region in regions:
            pts = [c.GetEndPoint(0) for loop in region.GetBoundaries() for c in loop]
            if min(p.Y for p in pts)<t.Coord.Y<max(p.Y for p in pts) and max(p.X for p in pts)<t.Coord.X:
                swatches.append(region)
        if len(thks)!=1 or len(swatches)!=1:raise ValueError('Cannot identify reference row thickness/color.')
        swatch = swatches[0]
        col = rgb(doc.GetElement(swatch.GetTypeId()).ForegroundPatternColor)
        if col not in palette:palette.append(col)
        key = logic.elevation_key(float(re.search(r'[+-]?\d+(?:\.\d+)?',t.Text).group())*1000)
        anchors[key] = col
        color_types[col] = swatch.GetTypeId()
        dynamic.extend([t.Id, thks[0].Id, swatch.Id])
    for line in horizontal:
        if line.GeometryCurve.GetEndPoint(0).Y < first_top-1e-6:dynamic.append(line.Id)
    # The source has a full-table background. Recreate its boundary at the new height.
    backgrounds = []
    for region in regions:
        pts=[c.GetEndPoint(0) for loop in region.GetBoundaries() for c in loop]
        if max(p.Y for p in pts)-min(p.Y for p in pts)>row_h*2:backgrounds.append(region)
    dynamic.extend(e.Id for e in backgrounds)
    vertical = [e for e in lines if abs(e.GeometryCurve.GetEndPoint(0).X-e.GeometryCurve.GetEndPoint(1).X)<1e-7
                and min(e.GeometryCurve.GetEndPoint(0).Y,e.GeometryCurve.GetEndPoint(1).Y)<first_top-1e-6]
    dynamic.extend(e.Id for e in vertical)
    bottom_line = next(e for e in horizontal if abs(e.GeometryCurve.GetEndPoint(0).Y-bottom)<1e-6)
    first_thk = next(e for e in texts if abs(e.Coord.Y-rows[0].Coord.Y)<1e-6 and e.Coord.X>rows[0].Coord.X)
    return {'all':elements,'static':[e for e in elements if e.Id not in dynamic], 'rows':rows,
            'first_thk':first_thk,'row_h':row_h,'first_top':first_top,'bottom':bottom,
            'bottom_line':bottom_line,'vertical':vertical,'backgrounds':backgrounds,
            'palette':palette,'anchors':anchors,'color_types':color_types,
            'swatch':doc.GetElement(dynamic[2])}

def collect(doc, template, source):
    views = [v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan)
             if not v.IsTemplate and v.ViewTemplateId==template.Id]
    layout = source_layout(doc, source)
    cache, groups, excluded = {}, {}, {}
    for v in views:
        if v.IsTemporaryHideIsolateActive():raise ValueError('Exit temporary hide/isolate before updating: '+v.Name)
        rows=[]
        for f in DB.FilteredElementCollector(doc,v.Id).OfCategory(DB.BuiltInCategory.OST_Floors).OfClass(DB.Floor).WhereElementIsNotElementType():
            key=f.Id.IntegerValue
            if key not in cache:cache[key]=floor_record(f)
            rows.append(cache[key])
        groups[v.Id.IntegerValue]=logic.group_floors(rows)
        excluded[v.Id.IntegerValue]=sum(1 for r in rows if not logic.eligible(r))
    previous=read_meta(template).get('colors',{})
    mapping=logic.assign_colors(list(groups.values()),layout['palette'],layout['anchors'],previous)
    return {'views':views,'groups':groups,'excluded':excluded,'layout':layout,'colors':mapping}

def copy_one(doc, source, target, element, offset=0):
    result=DB.ElementTransformUtils.CopyElements(source,ids([element.Id]),target,
             DB.Transform.CreateTranslation(DB.XYZ(0,offset,0)), DB.CopyPasteOptions())
    matches=[doc.GetElement(i) for i in result if doc.GetElement(i).GetType()==element.GetType()]
    if len(matches)!=1:raise ValueError('Unexpected copied legend elements.')
    new=matches[0]
    target.SetElementOverrides(new.Id,source.GetElementOverrides(element.Id))
    return new

def replace_text(note, text):
    formatted=note.GetFormattedText()
    formatted.SetPlainText(text)
    note.SetFormattedText(formatted)

def render_legend(doc, source, target, layout, groups, colors):
    old=read_meta(target).get('elements',[])
    for value in old:
        e=doc.GetElement(DB.ElementId(value))
        if e and e.OwnerViewId==target.Id:doc.Delete(e.Id)
    created=[]
    new_bottom=layout['first_top']-len(groups)*layout['row_h']
    def adjust(p):
        return DB.XYZ(p.X,new_bottom if abs(p.Y-layout['bottom'])<1e-6 else p.Y,p.Z)
    for bg in layout['backgrounds']:
        loops=List[DB.CurveLoop]()
        for boundary in bg.GetBoundaries():
            loop=DB.CurveLoop()
            for c in boundary:
                if not isinstance(c,DB.Line):raise ValueError('Reference table background is not rectangular.')
                loop.Append(DB.Line.CreateBound(adjust(c.GetEndPoint(0)),adjust(c.GetEndPoint(1))))
            loops.Add(loop)
        new=DB.FilledRegion.Create(doc,bg.GetTypeId(),target.Id,loops)
        target.SetElementOverrides(new.Id,source.GetElementOverrides(bg.Id))
        created.append(new.Id.IntegerValue)
    for e in layout['static']:
        created.append(copy_one(doc,source,target,e).Id.IntegerValue)
    for line in layout['vertical']:
        c=line.GeometryCurve
        new=doc.Create.NewDetailCurve(target,DB.Line.CreateBound(adjust(c.GetEndPoint(0)),adjust(c.GetEndPoint(1))))
        new.LineStyle=line.LineStyle
        target.SetElementOverrides(new.Id,source.GetElementOverrides(line.Id))
        created.append(new.Id.IntegerValue)
    for index,key in enumerate(sorted(groups)):
        offset=-index*layout['row_h']
        top=copy_one(doc,source,target,layout['rows'][0],offset)
        thk=copy_one(doc,source,target,layout['first_thk'],offset)
        replace_text(top,logic.elevation_text(key))
        replace_text(thk,logic.thickness_text(groups[key]['thicknesses']))
        swatch=copy_one(doc,source,target,layout['swatch'],offset)
        swatch.ChangeTypeId(layout['color_types'][colors[key]])
        bottom_y=layout['first_top']-(index+1)*layout['row_h']
        line=copy_one(doc,source,target,layout['bottom_line'],bottom_y-layout['bottom'])
        created.extend(e.Id.IntegerValue for e in (top,thk,swatch,line))
    return created

def update_filters(doc, template, data):
    oldmeta=read_meta(template)
    union={}
    for groups in data['groups'].values():
        for key,g in groups.items():union.setdefault(key,set()).update(g['ids'])
    managed=[]
    for key in sorted(union):
        previous=oldmeta.get('filters',{}).get(str(key))
        f=doc.GetElement(DB.ElementId(previous)) if previous else None
        if f is None:
            f=DB.SelectionFilterElement.Create(doc,'V1 SFL {0} {1:+.3f}'.format(template.Id.IntegerValue,key/1000.0))
        elif not isinstance(f,DB.SelectionFilterElement) or read_meta(f).get('template')!=template.UniqueId:
            raise ValueError('Managed filter identity changed; update stopped.')
        f.Name='V1 SFL {0} {1:+.3f}'.format(template.Id.IntegerValue,key/1000.0)
        f.SetElementIds(ids(sorted(union[key])))
        write_meta(f,{'template':template.UniqueId,'top_mm':key})
        managed.append((key,f))
    # Touch only owned slab filters and explicitly recognized floor-only legacy filters.
    # Never remove/re-add unrelated filters or write their overrides, visibility or order.
    oldorder=list(template.GetOrderedFilters())
    oldmanaged=set(oldmeta.get('filters',{}).values())
    current=set(f.Id.IntegerValue for key,f in managed)
    for fid in oldorder:
        f=doc.GetElement(fid)
        legacy=False
        if isinstance(f,DB.ParameterFilterElement) and f.Name.startswith('Floor SFL '):
            cats=list(f.GetCategories())
            legacy=len(cats)==1 and cats[0]==DB.ElementId(DB.BuiltInCategory.OST_Floors)
        obsolete=(fid.IntegerValue in oldmanaged and fid.IntegerValue not in current
                  and isinstance(f,DB.SelectionFilterElement)
                  and read_meta(f).get('template')==template.UniqueId)
        if legacy or obsolete:
            template.RemoveFilter(fid)
    solid=next(p.Id for p in DB.FilteredElementCollector(doc).OfClass(DB.FillPatternElement) if p.GetFillPattern().IsSolidFill)
    for key,f in managed:
        o=DB.OverrideGraphicSettings()
        c=DB.Color(*data['colors'][key])
        o.SetSurfaceForegroundPatternId(solid).SetSurfaceForegroundPatternColor(c).SetSurfaceForegroundPatternVisible(True)
        o.SetCutForegroundPatternId(solid).SetCutForegroundPatternColor(c).SetCutForegroundPatternVisible(True)
        if f.Id not in oldorder:template.AddFilter(f.Id)
        template.SetFilterOverrides(f.Id,o)
    return {str(k):f.Id.IntegerValue for k,f in managed}

def apply(doc, template, source, data, dry_run=False):
    group=DB.TransactionGroup(doc,'V1 - Floor colors and slab legends')
    group.Start()
    report={'dry_run':dry_run,'views':[], 'filters':0, 'replaced_on_sheets':[]}
    try:
        t=DB.Transaction(doc,'Update slab colors and legends')
        t.Start()
        try:
            filters=update_filters(doc,template,data)
            report['filters']=len(filters)
            allviews=list(DB.FilteredElementCollector(doc).OfClass(DB.ViewDrafting))
            for plan in data['views']:
                groups=data['groups'][plan.Id.IntegerValue]
                matches=[v for v in allviews if read_meta(v).get('plan')==plan.UniqueId]
                if len(matches)>1:raise ValueError('More than one managed legend for '+plan.Name)
                if matches:target=matches[0]
                else:
                    # Duplicate without detailing. Rendering copies the user's original elements.
                    target=doc.GetElement(source.Duplicate(DB.ViewDuplicateOption.Duplicate))
                target.Name='V1 SLAB - '+plan.Name
                target.Scale=source.Scale
                created=render_legend(doc,source,target,data['layout'],groups,data['colors'])
                write_meta(target,{'plan':plan.UniqueId,'source':source.UniqueId,'elements':created})
                # Replace only the original legend viewport on the SAME sheet as this plan.
                # Keep the source drafting view untouched as the formatting reference.
                viewports=list(DB.FilteredElementCollector(doc).OfClass(DB.Viewport))
                plan_sheets=set(v.SheetId.IntegerValue for v in viewports if v.ViewId==plan.Id)
                for viewport in viewports:
                    if viewport.ViewId==source.Id and viewport.SheetId.IntegerValue in plan_sheets:
                        old_corner=viewport.GetBoxOutline().MaximumPoint
                        viewport.ViewId=target.Id
                        doc.Regenerate()
                        delta=old_corner-viewport.GetBoxOutline().MaximumPoint
                        viewport.SetBoxCenter(viewport.GetBoxCenter()+delta)
                        report['replaced_on_sheets'].append(doc.GetElement(viewport.SheetId).SheetNumber)
                report['views'].append({'plan':plan.Name,'legend_id':target.Id.IntegerValue,'rows':len(groups),
                     'excluded':data['excluded'][plan.Id.IntegerValue]})
            write_meta(template,{'filters':filters,'colors':{str(k):list(v) for k,v in data['colors'].items()},'source':source.UniqueId})
            doc.Regenerate()
            for plan in data['views']:
                entry=next(r for r in report['views'] if r['plan']==plan.Name)
                legend=doc.GetElement(DB.ElementId(entry['legend_id']))
                actual=sorted(e.Text.strip() for e in DB.FilteredElementCollector(doc,legend.Id).OfClass(DB.TextNote)
                              if e.Text.strip().startswith('EL.'))
                expected=sorted(logic.elevation_text(k) for k in data['groups'][plan.Id.IntegerValue])
                if actual!=expected:raise ValueError('Legend validation failed: '+plan.Name)
            if t.Commit()!=DB.TransactionStatus.Committed:raise ValueError('Revit did not commit the update.')
        except Exception:
            if t.GetStatus()==DB.TransactionStatus.Started:t.RollBack()
            raise
        if dry_run:group.RollBack()
        else:group.Assimilate()
        return report
    except Exception:
        if group.GetStatus()==DB.TransactionStatus.Started:group.RollBack()
        raise
