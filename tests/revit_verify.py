# -*- coding: utf-8 -*-
"""Run from a pyRevit command with a prepared doc/template/source. Always rolls back."""
from pyrevit import DB
import slab_revit as api

def verify(doc, template, source):
    before_source=[(e.Id.IntegerValue,e.Text) for e in DB.FilteredElementCollector(doc,source.Id).OfClass(DB.TextNote)]
    outer=DB.TransactionGroup(doc,'Jet validation - always rollback')
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
        for key,value in filter_ids.items():
            f=doc.GetElement(DB.ElementId(value))
            expected=set()
            for g in first_data['groups'].values():
                if int(key) in g:expected.update(g[int(key)]['ids'])
            assert expected==set(i.IntegerValue for i in f.GetElementIds()), 'Incorrect filter membership'
        second['verified_repeat']=True
        second['verified_filter_membership']=True
        second['verified_source_unchanged']=True
        second['dry_run']=True
        return second
    finally:
        outer.RollBack()
