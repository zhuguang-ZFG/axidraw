#!/usr/bin/env python3
# coding=utf-8
"""
Plotter-oriented hatch generator for closed vector paths.

Compatible with the legacy ink_extensions runtime shipped with this repo.
"""

import math
import secrets
from lxml import etree

from axidrawinternal.plot_utils_import import from_dependency_import

inkex = from_dependency_import('ink_extensions.inkex')
simpletransform = from_dependency_import('ink_extensions.simpletransform')
simplestyle = from_dependency_import('ink_extensions.simplestyle')
cubicsuperpath = from_dependency_import('ink_extensions.cubicsuperpath')
exit_status = from_dependency_import('ink_extensions_utils.exit_status')

try:
    from ink_extensions.bezmisc import beziersplitatt  # noqa: F401
    from ink_extensions.cspsubdiv import cspsubdiv
except Exception:  # pragma: no cover
    cspsubdiv = None


def _rotate_point(point, angle_degrees, origin=(0.0, 0.0)):
    angle = math.radians(float(angle_degrees or 0.0))
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    ox, oy = origin
    px, py = point
    dx = float(px) - float(ox)
    dy = float(py) - float(oy)
    return (
        ox + dx * cos_a - dy * sin_a,
        oy + dx * sin_a + dy * cos_a,
    )


def _polygon_bounds(polygons):
    xs = []
    ys = []
    for polygon in polygons or []:
        for point in polygon or []:
            xs.append(float(point[0]))
            ys.append(float(point[1]))
    if not xs or not ys:
        return None
    return {
        "min_x": min(xs),
        "min_y": min(ys),
        "max_x": max(xs),
        "max_y": max(ys),
    }


def _normalize_polygon(polygon, epsilon=1e-9):
    points = [(float(x), float(y)) for x, y in (polygon or [])]
    if len(points) < 3:
        return []
    if abs(points[0][0] - points[-1][0]) <= epsilon and abs(points[0][1] - points[-1][1]) <= epsilon:
        points = points[:-1]
    if len(points) < 3:
        return []
    return points


def _scanline_intersections(polygon, y_value, epsilon=1e-9):
    intersections = []
    count = len(polygon)
    if count < 3:
        return intersections
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[(index + 1) % count]
        if abs(y1 - y2) <= epsilon:
            continue
        if y_value < min(y1, y2) or y_value >= max(y1, y2):
            continue
        ratio = (y_value - y1) / (y2 - y1)
        intersections.append(x1 + ratio * (x2 - x1))
    intersections.sort()
    return intersections


def _pair_scanline_spans(intersections, min_span=1e-6):
    spans = []
    for index in range(0, len(intersections) - 1, 2):
        start_x = float(intersections[index])
        end_x = float(intersections[index + 1])
        if abs(end_x - start_x) < float(min_span):
            continue
        spans.append((min(start_x, end_x), max(start_x, end_x)))
    return spans


def _generate_hatch_segments_for_angle(polygons, spacing, angle_degrees, *, cross_index=0):
    spacing = float(spacing or 0.0)
    if spacing <= 0.0:
        return []
    normalized = [_normalize_polygon(polygon) for polygon in (polygons or [])]
    normalized = [polygon for polygon in normalized if polygon]
    if not normalized:
        return []

    bounds = _polygon_bounds(normalized)
    if not bounds:
        return []
    center = (
        (bounds["min_x"] + bounds["max_x"]) / 2.0,
        (bounds["min_y"] + bounds["max_y"]) / 2.0,
    )
    rotated_polygons = [
        [_rotate_point(point, -float(angle_degrees or 0.0), origin=center) for point in polygon]
        for polygon in normalized
    ]
    rotated_bounds = _polygon_bounds(rotated_polygons)
    if not rotated_bounds:
        return []

    start_y = rotated_bounds["min_y"] + spacing / 2.0
    max_y = rotated_bounds["max_y"]
    segments = []
    line_index = 0
    y_value = start_y
    while y_value <= max_y + 1e-9:
        xs = []
        for polygon in rotated_polygons:
            xs.extend(_scanline_intersections(polygon, y_value))
        xs.sort()
        spans = _pair_scanline_spans(xs)
        reverse = ((line_index + int(cross_index or 0)) % 2) == 1
        if reverse:
            spans = list(reversed(spans))
        for start_x, end_x in spans:
            p1 = (end_x, y_value) if reverse else (start_x, y_value)
            p2 = (start_x, y_value) if reverse else (end_x, y_value)
            segments.append(
                (
                    _rotate_point(p1, float(angle_degrees or 0.0), origin=center),
                    _rotate_point(p2, float(angle_degrees or 0.0), origin=center),
                )
            )
        y_value += spacing
        line_index += 1
    return segments


def _segments_to_path_d(segments):
    commands = []
    for start, end in segments or []:
        commands.append(
            "M {x1:.6f},{y1:.6f} L {x2:.6f},{y2:.6f}".format(
                x1=float(start[0]),
                y1=float(start[1]),
                x2=float(end[0]),
                y2=float(end[1]),
            )
        )
    return " ".join(commands).replace(".000000", "").replace(".500000", ".5")


class HatchEffect(inkex.Effect):
    def __init__(self):
        super().__init__()
        self.arg_parser.add_argument("--tab", action="store", type=str, dest="tab", default="splash")
        self.arg_parser.add_argument("--hatchSpacing", action="store", type=float, dest="hatchSpacing", default=2.4)
        self.arg_parser.add_argument("--units", action="store", type=str, dest="units", default="3")
        self.arg_parser.add_argument("--hatchAngle", action="store", type=float, dest="hatchAngle", default=45.0)
        self.arg_parser.add_argument("--crossHatch", action="store", type=inkex.boolean_option, dest="crossHatch", default=False)
        self.arg_parser.add_argument("--connect_bool", action="store", type=inkex.boolean_option, dest="connect_bool", default=True)
        self.arg_parser.add_argument("--hatchScope", action="store", type=float, dest="hatchScope", default=2.0)
        self.arg_parser.add_argument("--inset_bool", action="store", type=inkex.boolean_option, dest="inset_bool", default=True)
        self.arg_parser.add_argument("--inset_dist", action="store", type=float, dest="inset_dist", default=0.3)
        self.arg_parser.add_argument("--tolerance", action="store", type=float, dest="tolerance", default=3.0)

    def effect(self):
        self.svg = self.document.getroot()
        targets = list(self.selected.values()) if self.selected else list(self.svg.iter())
        generated_total = 0
        seen = set()
        for node in targets:
            node_id = node.get("id") or str(id(node))
            if node_id in seen:
                continue
            seen.add(node_id)
            generated_total += self._hatch_node(node)
        if generated_total == 0:
            inkex.errormsg("没有找到可生成填充线的闭合路径对象。")
            return
        inkex.errormsg(f"已生成 {generated_total} 组填充线。")

    def _hatch_node(self, node):
        polygons = self._node_to_polygons(node)
        if not polygons:
            return 0

        spacing = self._convert_to_user_units(float(self.options.hatchSpacing or 2.4))
        inset = self._convert_to_user_units(float(self.options.inset_dist or 0.0))
        if self.options.inset_bool and inset > 0:
            polygons = self._apply_simple_inset(polygons, inset)
            polygons = [polygon for polygon in polygons if len(polygon) >= 3]
            if not polygons:
                return 0

        spacing = self._auto_adjust_spacing(polygons, spacing)
        angle = float(self.options.hatchAngle or 45.0)
        path_data_list = self._generate_path_data(polygons, spacing, angle, bool(self.options.crossHatch))
        if not path_data_list:
            return 0

        parent = self.getParentNode(node)
        if parent is None:
            return 0

        group = etree.Element(inkex.addNS('g', 'svg'))
        group_id = self.uniqueId(f"paixi_hatch_{node.get('id') or secrets.token_hex(4)}")
        group.set('id', group_id)
        parent.insert(list(parent).index(node) + 1, group)

        style_string = simplestyle.formatStyle(self._generated_style(node))
        count = 0
        for index, path_data in enumerate(path_data_list, start=1):
            hatch_path = etree.SubElement(group, inkex.addNS('path', 'svg'))
            hatch_path.set('id', self.uniqueId(f"{group_id}_{index}"))
            hatch_path.set('d', path_data)
            hatch_path.set('style', style_string)
            count += 1
        return count

    def _node_to_polygons(self, node):
        tag = self._local_name(node.tag)
        transform = self._node_transform(node)
        if tag == 'path':
            return self._path_node_to_polygons(node, transform)
        if tag == 'rect':
            return self._basic_shape_to_polygons(self._rect_polygon(node), transform)
        if tag == 'polygon':
            return self._basic_shape_to_polygons(self._points_polygon(node, close_polygon=True), transform)
        if tag == 'circle':
            return self._basic_shape_to_polygons(self._ellipse_polygon(node, is_circle=True), transform)
        if tag == 'ellipse':
            return self._basic_shape_to_polygons(self._ellipse_polygon(node, is_circle=False), transform)
        return []

    @staticmethod
    def _local_name(tag):
        if tag.startswith('{'):
            return tag.split('}', 1)[1]
        return tag

    @staticmethod
    def _node_transform(node):
        transform_attr = (node.get('transform') or '').strip()
        if not transform_attr:
            return None
        try:
            return simpletransform.parseTransform(transform_attr)
        except Exception:
            return None

    def _path_node_to_polygons(self, node, transform):
        try:
            csp = cubicsuperpath.parsePath(node.get('d'))
        except Exception:
            return []
        if transform is not None:
            try:
                simpletransform.applyTransformToPath(transform, csp)
            except Exception:
                pass
        local_csp = self._clone_csp(csp)
        if cspsubdiv is not None:
            try:
                cspsubdiv(local_csp, 0.25)
            except Exception:
                pass

        polygons = []
        for subpath in local_csp:
            points = []
            for point in subpath or []:
                try:
                    points.append((float(point[1][0]), float(point[1][1])))
                except Exception:
                    continue
            if len(points) < 3:
                continue
            if abs(points[0][0] - points[-1][0]) > 1e-6 or abs(points[0][1] - points[-1][1]) > 1e-6:
                continue
            polygons.append(points)
        return polygons

    def _basic_shape_to_polygons(self, polygon, transform):
        if not polygon:
            return []
        if transform is not None:
            out = []
            for point in polygon:
                working = [float(point[0]), float(point[1])]
                simpletransform.applyTransformToPoint(transform, working)
                out.append((working[0], working[1]))
            polygon = out
        return [polygon]

    @staticmethod
    def _rect_polygon(node):
        try:
            x = float(node.get('x', '0'))
            y = float(node.get('y', '0'))
            width = float(node.get('width', '0'))
            height = float(node.get('height', '0'))
        except Exception:
            return []
        if width <= 0 or height <= 0:
            return []
        return [
            (x, y),
            (x + width, y),
            (x + width, y + height),
            (x, y + height),
            (x, y),
        ]

    @staticmethod
    def _points_polygon(node, close_polygon):
        raw = (node.get('points') or '').replace(',', ' ')
        parts = [part for part in raw.split() if part]
        if len(parts) < 6 or len(parts) % 2 != 0:
            return []
        polygon = []
        try:
            for index in range(0, len(parts), 2):
                polygon.append((float(parts[index]), float(parts[index + 1])))
        except Exception:
            return []
        if close_polygon and polygon[0] != polygon[-1]:
            polygon.append(polygon[0])
        return polygon

    @staticmethod
    def _ellipse_polygon(node, is_circle):
        try:
            cx = float(node.get('cx', '0'))
            cy = float(node.get('cy', '0'))
            if is_circle:
                rx = ry = float(node.get('r', '0'))
            else:
                rx = float(node.get('rx', '0'))
                ry = float(node.get('ry', '0'))
        except Exception:
            return []
        if rx <= 0 or ry <= 0:
            return []
        segments = 72
        polygon = []
        for index in range(segments):
            angle = 2.0 * math.pi * index / segments
            polygon.append((cx + rx * math.cos(angle), cy + ry * math.sin(angle)))
        polygon.append(polygon[0])
        return polygon

    @staticmethod
    def _clone_csp(csp):
        return [
            [[list(handle) for handle in point] for point in (subpath or [])]
            for subpath in (csp or [])
        ]

    def _generated_style(self, node):
        style = simplestyle.parseStyle(node.get('style', ''))
        stroke = style.get('stroke')
        fill = style.get('fill')
        if not stroke or stroke in ('none', 'transparent'):
            stroke = fill if fill and fill not in ('none', 'transparent') else '#000000'
        return {
            'fill': 'none',
            'stroke': stroke,
            'stroke-width': style.get('stroke-width', '1'),
            'stroke-linecap': style.get('stroke-linecap', 'round'),
            'stroke-linejoin': style.get('stroke-linejoin', 'round'),
        }

    def _unit_suffix(self):
        return {'2': 'px', '3': 'mm', '4': 'in'}.get(str(self.options.units), 'mm')

    def _convert_to_user_units(self, value):
        unit = self._unit_suffix()
        amount = max(0.0, float(value or 0.0))
        try:
            return float(self.unittouu(f"{amount}{unit}"))
        except Exception:
            if unit == 'mm':
                return amount * 96.0 / 25.4
            if unit == 'in':
                return amount * 96.0
            return amount

    def _auto_adjust_spacing(self, polygons, base_spacing):
        bounds = _polygon_bounds(polygons)
        if not bounds:
            return base_spacing
        width = max(0.0, bounds["max_x"] - bounds["min_x"])
        height = max(0.0, bounds["max_y"] - bounds["min_y"])
        area = width * height
        spacing = float(base_spacing)

        # Approx thresholds in px^2, assuming 96 px/in user units.
        if area >= 160000:
            spacing *= 2.0
        elif area >= 80000:
            spacing *= 1.6
        elif area >= 30000:
            spacing *= 1.3
        elif area >= 12000:
            spacing *= 1.15
        return spacing

    @staticmethod
    def _apply_simple_inset(polygons, inset):
        inset = float(inset or 0.0)
        if inset <= 0:
            return polygons
        result = []
        for polygon in polygons:
            bounds = _polygon_bounds([polygon])
            if not bounds:
                continue
            cx = (bounds["min_x"] + bounds["max_x"]) / 2.0
            cy = (bounds["min_y"] + bounds["max_y"]) / 2.0
            shrunken = []
            for x_value, y_value in polygon:
                dx = x_value - cx
                dy = y_value - cy
                length = math.hypot(dx, dy)
                if length <= inset + 1e-9:
                    continue
                scale = max(0.0, (length - inset) / length)
                shrunken.append((cx + dx * scale, cy + dy * scale))
            if len(shrunken) >= 3:
                if shrunken[0] != shrunken[-1]:
                    shrunken.append(shrunken[0])
                result.append(shrunken)
        return result

    @staticmethod
    def _generate_path_data(polygons, spacing, angle_degrees, crosshatch):
        path_data = []
        segments = _generate_hatch_segments_for_angle(polygons, spacing, angle_degrees, cross_index=0)
        first = _segments_to_path_d(segments)
        if first:
            path_data.append(first)
        if crosshatch:
            cross_segments = _generate_hatch_segments_for_angle(polygons, spacing, angle_degrees + 90.0, cross_index=1)
            second = _segments_to_path_d(cross_segments)
            if second:
                path_data.append(second)
        return path_data


if __name__ == '__main__':
    e = HatchEffect()
    exit_status.run(e.affect)
