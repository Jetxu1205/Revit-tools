# -*- coding: utf-8 -*-
"""Unit-independent grouping and stable palette allocation."""
import math

MIN_AREA_M2 = 20.0

def elevation_key(mm):
    # Ignore floating point noise, but do not merge genuinely different levels.
    return int(math.floor(mm + 0.5))

def eligible(row):
    if row.get('top_mm') is None:
        return False
    if row.get('area_m2', 0) + 1e-8 < MIN_AREA_M2:
        return False
    faces = row.get('top_faces') or []
    if not faces or row.get('geometry_error'):
        return False
    if any(not f.get('planar') or abs(f['normal'][2] - 1.0) > 1e-8 for f in faces):
        return False
    heights = [f['z'] for f in faces]
    return (max(heights) - min(heights)) * 304.8 < 0.1

def group_floors(rows):
    groups = {}
    for row in rows:
        if not eligible(row):
            continue
        thk = row.get('thk_mm')
        if thk is None or thk <= 0:
            raise ValueError('Eligible floor has no valid thickness: {0}'.format(row['id']))
        key = elevation_key(row['top_mm'])
        # Output uses mm; keep submillimetre thicknesses instead of truncating them.
        g = groups.setdefault(key, {'ids': [], 'thicknesses': set()})
        g['ids'].append(row['id'])
        g['thicknesses'].add(round(thk, 3))
    return groups

def thickness_text(values):
    return '/'.join(('{0:.3f}'.format(v)).rstrip('0').rstrip('.') for v in sorted(values)) + ' mm'

def elevation_text(key):
    return 'EL. {0:+.3f}'.format(key / 1000.0)

def assign_colors(view_groups, palette, anchors, previous=None):
    """Keep screenshot anchors and past assignments; avoid repeated colors per plan."""
    neighbors = {}
    for groups in view_groups:
        keys = set(groups)
        for key in keys:
            neighbors.setdefault(key, set()).update(keys - {key})
    mapping = {}
    for source in (previous or {}, anchors):
        for key, value in source.items():
            key = int(key)
            if key in neighbors and tuple(value) in palette:
                mapping[key] = tuple(value)
    pending = set(neighbors) - set(mapping)
    def solve():
        if not pending:
            return True
        key = max(pending, key=lambda k:(len(set(mapping[n] for n in neighbors[k] if n in mapping)), len(neighbors[k]), -k))
        unavailable = set(mapping[n] for n in neighbors[key] if n in mapping)
        pending.remove(key)
        for col in palette:
            if col in unavailable:
                continue
            mapping[key] = col
            if solve():return True
        mapping.pop(key, None)
        pending.add(key)
        return False
    for key in mapping:
        if any(mapping.get(n) == mapping[key] for n in neighbors[key]):
            raise ValueError('Existing colors conflict in a plan; choose a larger palette.')
    if not solve():
        raise ValueError('The reference palette cannot distinguish all elevations in these plans.')
    return mapping
