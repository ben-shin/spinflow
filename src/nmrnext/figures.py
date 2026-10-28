"""Optional publication figures; importing this module never imports Qt or Matplotlib."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import numpy as np

from .errors import NMRNextError
from .model import Dataset, finite


def _plotting():
    try:
        import matplotlib
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.patches import Rectangle
    except ImportError as exc:
        command = f'"{sys.executable}" -m pip install -e ".[plot]"'
        if os.name == 'nt':
            command = '& ' + command
        bootstrap = 'tools/bootstrap.ps1 -WithPlot' if os.name == 'nt' else 'bash tools/bootstrap.sh --plot'
        raise NMRNextError('PLOT_UNAVAILABLE',
                          f'Optional Matplotlib is unavailable in Python {sys.executable}. '
                          f'From the SpinFlow source checkout, run {command} in this environment; '
                          f'or run {bootstrap} to prepare the checkout environment and use its Python.') from exc
    return matplotlib, Figure, FigureCanvasAgg, Rectangle


def _validate_annotations(dataset, component, report):
    """Check report geometry against data, not just the report's claimed digest."""
    from .analysis import _region_bounds
    ids = {a.id for a in dataset.axes}
    def require(condition, message):
        if not condition:
            raise NMRNextError('INVALID_FIGURE_ANALYSIS', message)
    def exact_axes(mapping):
        require(isinstance(mapping, dict) and set(mapping) == ids, 'Annotation must identify every axis exactly once')
    def close(actual, expected):
        return np.isclose(finite(actual, 'annotation coordinate'), expected, rtol=1e-10, atol=1e-9)
    def coordinates(axis, point):
        hz = axis.start + point * axis.step
        result = {'Hz': hz}
        if axis.observation_mhz is not None and axis.reference_ppm is not None:
            result['ppm'] = axis.reference_ppm + (hz-axis.reference_hz)/axis.observation_mhz
        return result
    def pair(value):
        require(isinstance(value, (list, tuple)) and len(value) == 2, 'Annotation bounds must be pairs')
        return [finite(v, 'annotation bound') for v in value]
    if 'axes' in report:
        require(report['axes'] == [a.to_dict() for a in dataset.axes], 'Report axes contradict the figure dataset')
    parameters = report.get('parameters', {})
    require(isinstance(parameters, dict), 'Report parameters must be an object')
    channel = parameters.get('channel', component)
    require(not isinstance(channel, bool) and isinstance(channel, int), 'Analysis channel must be an integer')
    if channel != component:
        raise NMRNextError('ANALYSIS_COMPONENT', 'Analysis and figure components differ')
    peaks, regions = report.get('peaks', []), report.get('regions', [])
    require(isinstance(peaks, list) and isinstance(regions, list), 'Peaks and regions must be lists')
    for peak in peaks:
        require(isinstance(peak, dict) and isinstance(peak.get('id'), str), 'Peak must have a string ID')
        exact_axes(peak.get('points'))
        exact_axes(peak.get('coordinates'))
        index = []
        for axis in dataset.axes:
            point = finite(peak['points'][axis.id], 'peak point')
            require(point.is_integer() and 0 <= point < axis.size, 'Peak point must be an in-range integer sample index')
            index.append(int(point))
            expected = coordinates(axis, point)
            actual = peak['coordinates'][axis.id]
            require(isinstance(actual, dict) and set(actual) == set(expected), 'Peak coordinate units are missing or unknown')
            require(all(close(actual[u], v) for u, v in expected.items()), 'Peak coordinates contradict its sample index')
        require(close(peak.get('intensity'), dataset.data[(component, *index)]), 'Peak intensity contradicts the selected sample')
    if not regions:
        return
    require(all(isinstance(r, dict) for r in regions), 'Each region must be an object')
    declarations = [{k: r[k] for k in ('id', 'bounds', 'unit', 'flags', 'peak_id') if k in r} for r in regions]
    normalized = _region_bounds(dataset, declarations)
    for region, resolved in zip(regions, normalized):
        if 'point_bounds' not in region:
            # An unintegrated proposal has no point geometry to draw.
            require('coordinate_bounds' not in region, 'Coordinate bounds require resolved point bounds')
            continue
        exact_axes(region['point_bounds'])
        if 'coordinate_bounds' in region:
            exact_axes(region['coordinate_bounds'])
        for axis in dataset.axes:
            lo, hi = pair(region['point_bounds'][axis.id])
            require(0 <= lo < hi <= axis.size-1, 'Region point bounds must be ordered and within sample centers')
            require(all(close(p, q) for p, q in zip((lo, hi), resolved['point_bounds'][axis.id])),
                    'Region point bounds contradict declared bounds/unit')
            if 'coordinate_bounds' in region:
                actual = region['coordinate_bounds'][axis.id]
                endpoints = [coordinates(axis, p) for p in (lo, hi)]
                require(isinstance(actual, dict) and set(actual) == set(endpoints[0]), 'Region coordinate units are missing or unknown')
                for unit in actual:
                    require(all(close(p, endpoint[unit]) for p, endpoint in zip(pair(actual[unit]), endpoints)),
                            'Region coordinate bounds contradict its point bounds')


def _make_figure(dataset, *, component=0, analysis=None, title='SpinFlow spectrum', contour_level=None):
    from .analysis import dataset_digest
    if not isinstance(dataset, Dataset):
        raise NMRNextError('INVALID_DATASET', 'Figure export requires a Dataset')
    if len(dataset.axes) not in (1, 2) or any(a.domain != 'frequency' or a.role != 'spectral' or a.size < 2 for a in dataset.axes):
        raise NMRNextError('FIGURE_PROFILE', 'Figures require 1D or 2D frequency data with at least two points per axis')
    if isinstance(component, bool) or not isinstance(component, int) or not 0 <= component < dataset.data.shape[0]:
        raise NMRNextError('INVALID_COMPONENT', 'Choose an existing quadrature component')
    if dataset.data[component].size > 4_000_000:
        raise NMRNextError('FIGURE_SIZE', 'Figure export supports at most four million samples; crop a region first')
    digest = dataset_digest(dataset)
    if analysis is not None and not isinstance(analysis, dict):
        raise NMRNextError('INVALID_FIGURE_ANALYSIS', 'Analysis must be a report object or map of report objects')
    try:
        analysis_json = json.dumps(analysis or {}, sort_keys=True, allow_nan=False)
        analysis = json.loads(analysis_json)  # Validate/render one immutable report snapshot.
    except (TypeError, ValueError, OverflowError) as exc:
        raise NMRNextError('INVALID_FIGURE_ANALYSIS', 'Analysis must contain finite JSON values') from exc
    reports = [] if not analysis else ([analysis] if 'dataset_digest' in analysis else list(analysis.values()))
    for report in reports:
        if not isinstance(report, dict):
            raise NMRNextError('INVALID_FIGURE_ANALYSIS', 'Each analysis report must be an object')
        key = 'result_dataset_digest' if report.get('kind') == 'autophase' else 'dataset_digest'
        if report.get(key) != digest:
            raise NMRNextError('STALE_ANALYSIS', 'Analysis dataset digest does not match the figure source')
        _validate_annotations(dataset, component, report)
    mpl, Figure, Canvas, Rectangle = _plotting()
    figure = Figure(figsize=(8, 5.8), layout='constrained', facecolor='white')
    figure.get_layout_engine().set(rect=(0, .10, 1, .90))
    Canvas(figure)
    ax = figure.subplots()
    coords, units = {}, {}
    for axis in dataset.axes:
        units[axis.id] = 'ppm' if axis.observation_mhz is not None and axis.reference_ppm is not None else 'Hz'
        coords[axis.id] = dataset.coordinates(axis.id, units[axis.id])
    xaxis = dataset.axes[-1]
    x = coords[xaxis.id]
    values = dataset.data[component]
    ax.set_xlabel(f'{xaxis.nucleus or xaxis.label or xaxis.id} ({units[xaxis.id]})')
    ax.set_xlim(float(max(x)), float(min(x)))
    ax.set_title(str(title), loc='left', pad=16, fontsize=14, fontweight='bold')
    ax.tick_params(direction='out', labelsize=9)
    ax.spines[['top', 'right']].set_visible(False)
    levels = None
    if values.ndim == 1:
        ax.plot(x, values, color='#1769a3', linewidth=0.9)
        ax.axhline(0, color='#8895a3', linewidth=0.5)
        ax.set_ylabel('Signed intensity')
        ax.margins(y=0.15)
    else:
        yaxis = dataset.axes[0]
        y = coords[yaxis.id]
        ax.set_ylabel(f'{yaxis.nucleus or yaxis.label or yaxis.id} ({units[yaxis.id]})')
        ax.set_ylim(float(max(y)), float(min(y)))
        maximum = float(np.max(np.abs(values)))
        level = finite(contour_level, 'contour_level') if contour_level is not None else maximum * 0.05
        if level <= 0:
            if maximum != 0 or contour_level is not None:
                raise NMRNextError('INVALID_CONTOUR_LEVEL', 'Contour level must be positive')
            level = 1.0
        levels = np.geomspace(level, max(level * 1.01, maximum), 8)
        if values.min() <= -level:
            ax.contour(x, y, values, levels=-levels[::-1], colors='#2468aa', linewidths=0.65, linestyles='solid')
        if values.max() >= level:
            ax.contour(x, y, values, levels=levels, colors='#b33b42', linewidths=0.65)
        ax.text(0, 1.01, f'Blue: negative · Red: positive · first contour ±{level:.4g}',
                transform=ax.transAxes, fontsize=8, color='#4c5665')
    annotations = 0
    label_positions = []
    peak_count = sum(len(report.get('peaks', [])) for report in reports)
    for report in reports:
        for peak in sorted(report.get('peaks', []), key=lambda p: abs(p['intensity']), reverse=True):
            position = [float(np.interp(peak['points'][a.id], np.arange(a.size), coords[a.id])) for a in dataset.axes]
            px, py = (position[0], peak['intensity']) if values.ndim == 1 else (position[1], position[0])
            # Label spacing affects only annotations, never samples or peak tables.
            nx, ny = ax.transAxes.inverted().transform(ax.transData.transform((px, py)))
            crowded = any(abs(nx-x0) < .09 and abs(ny-y0) < .07 for x0, y0 in label_positions)
            if annotations < 20 and not crowded:
                ax.annotate(str(peak['id']), (px, py), xytext=(4, 7), textcoords='offset points', fontsize=7,
                            color='#333333', arrowprops={'arrowstyle': '-', 'color': '#777777', 'lw': 0.5})
                annotations += 1
                label_positions.append((nx, ny))
        for region in report.get('regions', []):
            if 'point_bounds' not in region:
                continue
            bounds = {a.id: sorted(float(np.interp(p, np.arange(a.size), coords[a.id]))
                                  for p in region['point_bounds'][a.id]) for a in dataset.axes}
            lo, hi = bounds[xaxis.id]
            if values.ndim == 1:
                ax.axvspan(lo, hi, facecolor='#d29b32', alpha=0.13, edgecolor='#9b721f')
            else:
                bottom, top = bounds[yaxis.id]
                ax.add_patch(Rectangle((lo, bottom), hi-lo, top-bottom, facecolor='none',
                                      edgecolor='#9b721f', linestyle='--', linewidth=0.9))
    result = {'schema_version': '1', 'dataset_digest': digest, 'component': component,
              'axis_units': units, 'analysis_digest': hashlib.sha256(analysis_json.encode()).hexdigest(),
              'contour_level': float(levels[0]) if levels is not None else None,
              'contour_levels': levels.tolist() if levels is not None else [],
              'peak_labels_shown': annotations, 'peak_label_limit': 20, 'peaks_in_report': peak_count}
    figure.text(0.01, .015, f'SpinFlow · component {component} · source {digest[:16]} · analysis {result["analysis_digest"][:16]}\n'
                f'Peak labels {annotations}/{peak_count}; complete peaks, methods and flags in analysis JSON.',
                fontsize=7, color='#5c6570')
    return figure, result


def export_figure(dataset, path, *, component=0, analysis=None, title='SpinFlow spectrum', contour_level=None):
    """Write a new SVG, PDF or PNG with a calibrated signed plot and digest links.

    Save the corresponding analysis JSON alongside the figure for full methods,
    bounds and flags. The SVG/PDF retain vector curves, contours and annotations.
    """
    path = Path(path)
    fmt = path.suffix.lower().lstrip('.')
    if fmt not in {'svg', 'pdf', 'png'}:
        raise NMRNextError('FIGURE_FORMAT', 'Figure suffix must be .svg, .pdf or .png')
    if path.exists():
        raise NMRNextError('OUTPUT_EXISTS', f'Output already exists: {path}')
    mpl, *_ = _plotting()
    with mpl.rc_context({'font.family': 'DejaVu Sans', 'svg.fonttype': 'none', 'text.usetex': False}):
        figure, report = _make_figure(dataset, component=component, analysis=analysis,
                                      title=title, contour_level=contour_level)
        description = json.dumps(report, sort_keys=True, allow_nan=False)
        metadata = ({'Title': str(title), 'Subject': description, 'Creator': 'SpinFlow'} if fmt == 'pdf'
                    else {'Title': str(title), 'Description': description, 'Creator': 'SpinFlow'} if fmt == 'svg'
                    else {'Title': str(title), 'Description': description, 'Software': 'SpinFlow'})
        fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', suffix=f'.{fmt}', dir=path.parent)
        os.close(fd)
        published = False
        # Release the figure before publication: errors must not masquerade as a
        # failed export after the final path becomes visible.
        try:
            figure.savefig(temporary, format=fmt, dpi=300, bbox_inches='tight', metadata=metadata)
            figure.clear()
            try:
                if os.name == 'nt':
                    os.rename(temporary, path)
                else:
                    os.link(temporary, path)
                published = True
            except FileExistsError as exc:
                raise NMRNextError('OUTPUT_EXISTS', f'Output already exists: {path}') from exc
        finally:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError as exc:
                if published:
                    report['cleanup_warning'] = {'code': 'TEMPORARY_CLEANUP_FAILED',
                                                 'path': str(temporary), 'message': str(exc)}
                # Before publication, preserve the original render/link error.
    return {**report, 'path': str(path), 'format': fmt}
