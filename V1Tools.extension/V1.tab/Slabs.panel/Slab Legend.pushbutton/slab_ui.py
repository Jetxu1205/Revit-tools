# -*- coding: utf-8 -*-
import io
import json
import os
import traceback
from pyrevit import DB, revit, forms
from Autodesk.Revit.Exceptions import OperationCanceledException
import slab_revit as api
try:
    text_type=unicode
except NameError:
    text_type=str

def choose(items, title):
    if len(items)==1:return items[0]
    return forms.SelectFromList.show(sorted(items,key=lambda v:v.Name),name_attr='Name',title=title,multiselect=False)

def active_plan(doc):
    v=doc.ActiveView
    if isinstance(v,DB.ViewPlan):return v
    if isinstance(v,DB.ViewSheet):
        plans=[doc.GetElement(doc.GetElement(i).ViewId) for i in v.GetAllViewports()]
        plans=[p for p in plans if isinstance(p,DB.ViewPlan)]
        return choose(plans,u'选择这张图纸的楼层平面') if plans else None
    return None

def find_source(doc,template):
    saved=api.read_meta(template).get('source')
    source=doc.GetElement(saved) if saved else None
    if source:return source
    candidates=[]
    for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewDrafting):
        if api.read_meta(v).get('plan'):continue
        if any(e.Text.strip()=='SLAB LEGEND' for e in DB.FilteredElementCollector(doc,v.Id).OfClass(DB.TextNote)):
            candidates.append(v)
    if not candidates:raise ValueError(u'请先在模型中准备原格式的 SLAB LEGEND 详图。')
    return choose(candidates,u'选择要沿用格式和颜色的原图例')

def write_report(report):
    folder=os.path.join(os.environ['LOCALAPPDATA'],'V1RevitTools')
    if not os.path.isdir(folder):os.makedirs(folder)
    with io.open(os.path.join(folder,'last_run.json'),'w',encoding='utf-8') as f:
        f.write(text_type(json.dumps(report,ensure_ascii=False,indent=2)))

def place(doc,uidoc):
    sheet=doc.ActiveView
    if not isinstance(sheet,DB.ViewSheet):
        forms.alert(u'请先打开要放置图例的 Sheet，再按住 Shift 点击本按钮。')
        return
    plan=active_plan(doc)
    if not plan:return
    candidates=[v for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewDrafting)
                if api.read_meta(v).get('plan')==plan.UniqueId]
    if not candidates:
        forms.alert(u'请先运行一次楼板上色与图例。')
        return
    legend=candidates[0]
    placed=[v for v in DB.FilteredElementCollector(doc).OfClass(DB.Viewport) if v.ViewId==legend.Id]
    if placed:
        forms.alert(u'该层图例已放在图纸上，更新时会保留放置位置。')
        return
    try:point=uidoc.Selection.PickPoint(u'点击图纸中图例的放置位置')
    except OperationCanceledException:return
    with revit.Transaction(u'放置楼板图例'):
        vp=DB.Viewport.Create(doc,sheet.Id,legend.Id,point)
        # Match the reference viewport type (for example, no view title).
        source_uid=api.read_meta(legend).get('source')
        source=doc.GetElement(source_uid) if source_uid else None
        if source:
            originals=[v for v in DB.FilteredElementCollector(doc).OfClass(DB.Viewport) if v.ViewId==source.Id]
            if not originals:
                originals=[v for v in DB.FilteredElementCollector(doc).OfClass(DB.Viewport)
                           if v.Id!=vp.Id and api.read_meta(doc.GetElement(v.ViewId)).get('source')==source_uid]
            if originals:vp.ChangeTypeId(originals[0].GetTypeId())

def run(shift=False):
    doc,uidoc=revit.doc,revit.uidoc
    if doc is None or doc.IsFamilyDocument:
        forms.alert(u'请先打开 Revit 项目。')
        return
    if shift:
        choice=forms.CommandSwitchWindow.show([u'放到当前 Sheet',u'只检查，不修改模型'],message=u'楼板工具')
        if choice==u'放到当前 Sheet':
            place(doc,uidoc)
            return
        if choice is None:return
        dry=True
    else:dry=False
    try:
        plan=active_plan(doc)
        template=doc.GetElement(plan.ViewTemplateId) if plan else None
        if template is None:
            templates=[v for v in DB.FilteredElementCollector(doc).OfClass(DB.View)
                       if v.IsTemplate and any(p.ViewTemplateId==v.Id for p in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan) if not p.IsTemplate)]
            template=choose(templates,u'选择需要统一上色的 View Template')
        if template is None:return
        controlled=set(i.IntegerValue for i in template.GetTemplateParameterIds())-set(i.IntegerValue for i in template.GetNonControlledTemplateParameterIds())
        if int(DB.BuiltInParameter.VIS_GRAPHICS_FILTERS) not in controlled:
            raise ValueError(u'这个模板没有控制 V/G Filters。请先在模板里勾选该项。')
        source=find_source(doc,template)
        if source is None:return
        data=api.collect(doc,template,source)
        result=api.apply(doc,template,source,data,dry_run=dry)
        write_report(result)
        if dry:
            forms.alert(u'检查通过：{0} 个平面、{1} 个标高过滤器。\n试运行已回滚，模型没有保留修改。'.format(len(result['views']),result['filters']))
        else:
            forms.alert(u'已更新 {0} 个平面的过滤器和图例。\n只纳入 ≥20㎡ 的水平楼板。\n\n图例在 Drafting Views 中，以 V1 SLAB 开头。\n打开 Sheet，按住 Shift 点击本按钮可放置图例。\n模型修改后，再点一次刷新。'.format(len(result['views'])),title=u'V1 楼板上色与图例')
    except Exception as exc:
        write_report({'error':traceback.format_exc()})
        forms.alert(u'未完成更新：\n'+text_type(exc),title=u'V1 楼板上色与图例')
