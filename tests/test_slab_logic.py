import importlib.util
import pathlib
import unittest

path = pathlib.Path(__file__).parents[1] / 'JetTools.extension/Jet Tools.tab/Slabs.panel/Slab Legend.pushbutton/slab_logic.py'
spec = importlib.util.spec_from_file_location('logic', path)
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)

def floor(i=1, top=100000, area=20, thickness=200):
    return {'id':i,'top_mm':top,'area_m2':area,'thk_mm':thickness,
            'top_faces':[{'planar':True,'normal':[0,0,1],'z':20}]}

class SlabTests(unittest.TestCase):
    def test_size_and_invalid_top(self):
        self.assertTrue(logic.eligible(floor()))
        self.assertFalse(logic.eligible(floor(area=19.9)))
        self.assertFalse(logic.eligible(floor(top=None)))

    def test_sloped_curved_and_stepped_top_excluded(self):
        r=floor()
        r['top_faces'][0]['normal']=[0,0.01,0.99995]
        self.assertFalse(logic.eligible(r))
        r['top_faces']=[{'planar':False}]
        self.assertFalse(logic.eligible(r))
        r=floor()
        r['top_faces'].append({'planar':True,'normal':[0,0,1],'z':20.1})
        self.assertFalse(logic.eligible(r))

    def test_noise_merged_distinct_5mm_separated_and_thickness_sorted(self):
        groups=logic.group_floors([floor(top=100000.00000001), floor(2,top=100000,thickness=400),floor(3,top=100005)])
        self.assertEqual(len(groups),2)
        self.assertEqual(logic.thickness_text(groups[100000]['thicknesses']),'200/400 mm')
        self.assertEqual(logic.elevation_text(100005),'EL. +100.005')

    def test_stable_colors_without_same_plan_collisions(self):
        colors=[(1,2,3),(4,5,6),(7,8,9)]
        groups=[{1:[],2:[]},{2:[],3:[]}]
        result=logic.assign_colors(groups,colors,{1:colors[0]})
        self.assertNotEqual(result[1],result[2])
        self.assertNotEqual(result[2],result[3])
        self.assertEqual(result,logic.assign_colors(groups,colors,{1:colors[0]},result))
        with self.assertRaises(ValueError):logic.assign_colors([{1:[],2:[],3:[],4:[]}],colors,{})

if __name__=='__main__':unittest.main()
