"""Regression checks for template filter isolation, without a running Revit."""
import copy
import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


class ElementId:
    def __init__(self, value):
        self.IntegerValue = int(value)

    def __eq__(self, other):
        return isinstance(other, ElementId) and self.IntegerValue == other.IntegerValue

    def __hash__(self):
        return hash(self.IntegerValue)


class NetList(list):
    @classmethod
    def __class_getitem__(cls, item):
        return cls


class Filter:
    def __init__(self, number, name, metadata=None):
        self.Id = ElementId(number)
        self.Name = name
        self.metadata = metadata or {}


class ParameterFilter(Filter):
    def __init__(self, number, name, categories):
        super().__init__(number, name)
        self.categories = [ElementId(c) for c in categories]

    def GetCategories(self):
        return self.categories


class SelectionFilter(Filter):
    def SetElementIds(self, values):
        self.members = set(i.IntegerValue for i in values)

    @classmethod
    def Create(cls, doc, name):
        new = cls(max(doc.elements) + 1, name)
        doc.elements[new.Id.IntegerValue] = new
        return new


class Override:
    def __init__(self):
        self.values = {}

    def __getattr__(self, name):
        if not name.startswith('Set'):
            raise AttributeError(name)

        def setter(value):
            self.values[name] = value
            return self
        return setter


class Document:
    def __init__(self, filters):
        self.elements = {f.Id.IntegerValue: f for f in filters}

    def GetElement(self, element_id):
        return self.elements.get(element_id.IntegerValue)


class Template:
    def __init__(self, order, metadata, protected):
        self.Id = ElementId(900)
        self.UniqueId = 'template-owner'
        self.order = list(order)
        self.metadata = metadata
        self.protected = set(protected)
        self.mutations = []
        self.settings = {i: {'override': {'line_color': i}, 'visible': i % 2 == 0,
                             'enabled': i % 3 == 0} for i in order}

    def GetOrderedFilters(self):
        return [ElementId(i) for i in self.order]

    def mutate(self, action, element_id):
        number = element_id.IntegerValue
        if number in self.protected:
            raise AssertionError('Unrelated filter mutated: {} {}'.format(action, number))
        self.mutations.append((action, number))
        return number

    def RemoveFilter(self, element_id):
        self.order.remove(self.mutate('remove', element_id))

    def AddFilter(self, element_id):
        number = self.mutate('add', element_id)
        if number in self.order:
            raise AssertionError('Existing filter added twice')
        self.order.append(number)
        self.settings[number] = {'override': None, 'visible': True, 'enabled': True}

    def SetFilterOverrides(self, element_id, value):
        self.settings[self.mutate('override', element_id)]['override'] = value

    def SetFilterVisibility(self, element_id, value):
        self.settings[self.mutate('visible', element_id)]['visible'] = value

    def SetIsFilterEnabled(self, element_id, value):
        self.settings[self.mutate('enabled', element_id)]['enabled'] = value


def load_adapter():
    path = next(pathlib.Path(__file__).parents[1].rglob('slab_revit.py'))
    db = types.SimpleNamespace(
        ElementId=ElementId, SelectionFilterElement=SelectionFilter,
        ParameterFilterElement=ParameterFilter, OverrideGraphicSettings=Override,
        BuiltInCategory=types.SimpleNamespace(OST_Floors=1),
        Color=lambda *rgb: rgb, FillPatternElement=object,
        FilteredElementCollector=lambda doc: types.SimpleNamespace(
            OfClass=lambda cls: [types.SimpleNamespace(
                Id=ElementId(500), GetFillPattern=lambda: types.SimpleNamespace(IsSolidFill=True))]))
    stubs = {
        'System': types.SimpleNamespace(Guid=lambda value: value, String=str),
        'System.Collections.Generic': types.SimpleNamespace(List=NetList),
        'pyrevit': types.SimpleNamespace(DB=db),
        'slab_logic': types.SimpleNamespace(),
    }
    spec = importlib.util.spec_from_file_location('slab_revit_scope_test', path)
    adapter = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(adapter)
    adapter.read_meta = lambda element: element.metadata
    adapter.write_meta = lambda element, value: setattr(element, 'metadata', value)
    return adapter


class FilterScopeTests(unittest.TestCase):
    def test_only_owned_floor_filters_change_and_refresh_is_stable(self):
        api = load_adapter()
        owned = {'template': 'template-owner'}
        filters = [
            ParameterFilter(11, 'Kerb', [2]),
            ParameterFilter(12, 'Floor SFL mixed', [1, 2]),
            ParameterFilter(13, 'Floor SFL old', [1]),
            SelectionFilter(14, 'Existing floor color', owned.copy()),
            SelectionFilter(15, 'Obsolete floor color', owned.copy()),
            ParameterFilter(16, 'Floor SFL wall', [2]),
            SelectionFilter(17, 'Another owner', {'template': 'other-template'}),
            ParameterFilter(18, 'User floor drainage', [1]),
        ]
        protected = [11, 12, 16, 17, 18]
        template = Template([f.Id.IntegerValue for f in filters], {
            'filters': {'100000': 14, '99000': 15, '98000': 16, '97000': 17}}, protected)
        doc = Document(filters)
        before = copy.deepcopy({i: template.settings[i] for i in protected})
        data = {'groups': {
            1: {100000: {'ids': [101, 102]}, 100100: {'ids': [103]}},
            2: {100000: {'ids': [102, 104]}}},
            'colors': {100000: (1, 2, 3), 100100: (4, 5, 6)}}

        result = api.update_filters(doc, template, data)

        self.assertEqual(result['100000'], 14)
        self.assertEqual(doc.elements[14].members, {101, 102, 104})
        self.assertEqual(doc.elements[result['100100']].members, {103})
        self.assertEqual([i for i in template.order if i in protected], protected)
        self.assertEqual({i: template.settings[i] for i in protected}, before)
        self.assertEqual({i for op, i in template.mutations if op == 'remove'}, {13, 15})
        self.assertEqual(template.order, [11, 12, 14, 16, 17, 18, result['100100']])
        # The real apply() persists this mapping between button clicks.
        template.metadata['filters'] = result
        template.mutations.clear()
        self.assertEqual(api.update_filters(doc, template, data), result)
        self.assertTrue(all(op == 'override' for op, _ in template.mutations))
        self.assertEqual({i: template.settings[i] for i in protected}, before)


if __name__ == '__main__':
    unittest.main()
