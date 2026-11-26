# -*- coding: utf-8 -*-
"""Run from a pyRevit command with a prepared doc/template/source. Always rolls back."""
from pyrevit import DB
import slab_revit as api

def graphic_state(override):
    result={}
    for p in override.GetType().GetProperties():
        if not p.CanRead or p.GetIndexParameters().Length:continue
        value=p.GetValue(override,None)
        if isinstance(value,DB.Color):
            value=(value.Red,value.Green,value.Blue) if value.IsValid else None
        elif isinstance(value,DB.ElementId):value=value.IntegerValue
        else:value=str(value)
        result[p.Name]=value
    return result

def untouched_filters(doc,template):
    result=[]
    for fid in template.GetOrderedFilters():
        f=doc.GetElement(fid)
        owned=(isinstance(f,DB.SelectionFilterElement) and api.read_meta(f).get('template')==template.UniqueId)
        legacy=(isinstance(f,DB.ParameterFilterElement) and f.Name.startswith('Floor SFL ')
                and list(f.GetCategories())==[DB.ElementId(DB.BuiltInCategory.OST_Floors)])
        if not owned and not legacy:
            result.append((fid.IntegerValue,f.Name,template.GetFilterVisibility(fid),
                template.GetIsFilterEnabled(fid),graphic_state(template.GetFilterOverrides(fid))))
    return result

def verify(doc, template, source):
    before_source=[(e.Id.IntegerValue,e.Text) for e in DB.FilteredElementCollector(doc,source.Id).OfClass(DB.TextNote)]
    protected=untouched_filters(doc,template)
    before_parameters=[(p.Id.IntegerValue,p.AsValueString()) for p in template.Parameters]
    wall_id=DB.ElementId(DB.BuiltInCategory.OST_Walls)
    wall_graphics=graphic_state(template.GetCategoryOverrides(wall_id))
    outer=DB.TransactionGroup(doc,'V1 validation - always rollback')
    outer.Start()
    try:
        first_data=api.collect(doc,template,source)
        first=api.apply(doc,template,source,first_data)
        filter_ids=api.read_meta(template)['filters']
        second=api.apply(doc,template,source,api.collect(doc,template,source))
        assert filter_ids==api.read_meta(template)['filters'], 'Duplicate filters on repeated refresh'
        assert [v['legend_id'] for v in first['views']]==[v['legend_id'] for v in second['views']], 'Duplicate legends'
        assert [v['rows'] for v in first['views']]==[v['rows'] for v in second['views']], 'Changed rows on repeated refresh'
        assert before_source==[(e.Id.IntegerValue,e.Text) for e in DB.FilteredElementCollector(doc,source.Id).OfClass(DB.TextNote)], 'Reference changed'
        assert protected==untouched_filters(doc,template), 'Non-slab filter settings changed'
        assert before_parameters==[(p.Id.IntegerValue,p.AsValueString()) for p in template.Parameters], 'Template parameters changed'
        assert wall_graphics==graphic_state(template.GetCategoryOverrides(wall_id)), 'Wall category changed'
        for key,value in filter_ids.items():
            f=doc.GetElement(DB.ElementId(value))
            expected=set()
            for g in first_data['groups'].values():
                if int(key) in g:expected.update(g[int(key)]['ids'])
            assert expected==set(i.IntegerValue for i in f.GetElementIds()), 'Incorrect filter membership'
            assert all(doc.GetElement(i).Category.Id==DB.ElementId(DB.BuiltInCategory.OST_Floors)
                       for i in f.GetElementIds()), 'Non-floor element in slab filter'
        second['verified_repeat']=True
        second['verified_filter_membership']=True
        second['verified_source_unchanged']=True
        second['verified_other_filters_unchanged']=len(protected)
        second['verified_template_parameters_and_walls_unchanged']=True
        second['dry_run']=True
        return second
    finally:
        outer.RollBack()
