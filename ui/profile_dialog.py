# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import pyqtSignal, Qt, QSettings, QRectF, QPointF, QSize
from qgis.PyQt.QtGui import QPainter, QColor, QPen, QPolygonF, QImage, QFont
from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                                 QLabel, QComboBox, QFileDialog, QTabWidget,
                                 QWidget, QFormLayout, QDoubleSpinBox, QSpinBox,
                                 QGroupBox, QTextEdit, QListWidget, QListWidgetItem,
                                 QCheckBox, QRadioButton, QButtonGroup, QToolButton, QMenu, QAction,
                                 QSizePolicy)
from qgis.PyQt.QtSvg import QSvgGenerator
from qgis.core import QgsProject


# Anchors for the Factor-of-Safety colour scale (RdYlGn):
# t=0 -> red (low FoS, unsafe), t=0.5 -> yellow, t=1 -> green (high FoS, safe).
_FOS_COLOR_ANCHORS = [
    (0.0, (215, 48, 39)),
    (0.5, (255, 255, 191)),
    (1.0, (26, 152, 80)),
]


def fos_to_rgb(t):
    """Map a normalised value t in [0, 1] to an (r, g, b) tuple on the FoS scale."""
    try:
        t = float(t)
    except Exception:
        return (136, 136, 136)
    if t != t:  # NaN
        return (136, 136, 136)
    t = max(0.0, min(1.0, t))
    anchors = _FOS_COLOR_ANCHORS
    for i in range(len(anchors) - 1):
        t0, c0 = anchors[i]
        t1, c1 = anchors[i + 1]
        if t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(int(round(c0[k] + (c1[k] - c0[k]) * f)) for k in range(3))
    return anchors[-1][1]


def fos_to_hex(t):
    """Map a normalised value t in [0, 1] to a '#rrggbb' colour on the FoS scale."""
    r, g, b = fos_to_rgb(t)
    return '#%02x%02x%02x' % (r, g, b)


class ProfileCanvas(QWidget):
    """Qt-based profile renderer compatible with QGIS 4 (no Matplotlib)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self._series = []
        self._x_label = 'Distance [m]'
        self._y_label = 'Elevation [m]'
        self._empty_message = 'No valid data'
        self._colorbar = None

    def set_plot_data(self, series, x_label='Distance [m]', y_label='Elevation [m]', empty_message='No valid data', colorbar=None):
        self._series = series or []
        self._x_label = x_label
        self._y_label = y_label
        self._empty_message = empty_message
        self._colorbar = colorbar
        self.update()

    def export_image(self, fmt, path, include_table=False, table_rows=None):
        width = max(self.width(), 800)
        height = max(self.height(), 420)
        table_h = 150 if include_table else 0
        total_h = height + table_h

        if str(fmt).lower() == 'svg':
            svg = QSvgGenerator()
            svg.setFileName(path)
            svg.setSize(QSize(width, total_h))
            svg.setViewBox(QRectF(0, 0, width, total_h).toRect())
            svg.setTitle('The Raise Of Slopes - Profile Export')
            painter = QPainter(svg)
            painter.fillRect(QRectF(0, 0, width, total_h), Qt.white)
            self._paint_content(painter, QRectF(0, 0, width, total_h), include_table=include_table, table_rows=table_rows or [])
            painter.end()
            return

        image = QImage(width, total_h, QImage.Format_ARGB32)
        image.fill(Qt.white)
        painter = QPainter(image)
        self._paint_content(painter, QRectF(0, 0, width, total_h), include_table=include_table, table_rows=table_rows or [])
        painter.end()
        image.save(path, 'PNG')

    def paintEvent(self, event):
        _ = event
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        self._paint_content(painter, QRectF(self.rect()), include_table=False, table_rows=[])
        painter.end()

    def _paint_content(self, painter, rect, include_table=False, table_rows=None):
        table_rows = table_rows or []
        left = 62.0
        right = 18.0
        top = 16.0
        bottom = 44.0
        legend_pad = 8.0
        table_h = 130.0 if include_table else 0.0

        plot_rect = QRectF(rect.left() + left, rect.top() + top, rect.width() - left - right, rect.height() - top - bottom - table_h)
        if plot_rect.width() < 10 or plot_rect.height() < 10:
            return

        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QPen(QColor('#999999'), 1.0))
        painter.drawRect(plot_rect)

        all_points = []
        for s in self._series:
            pts = s.get('points', [])
            for p in pts:
                all_points.append(p)

        if not all_points:
            painter.setPen(QPen(QColor('#555555'), 1.0))
            painter.drawText(plot_rect, Qt.AlignCenter, self._empty_message)
            return

        x_vals = [p[0] for p in all_points]
        y_vals = [p[1] for p in all_points]
        x_min = min(x_vals)
        x_max = max(x_vals)
        y_min = min(y_vals)
        y_max = max(y_vals)

        if x_max <= x_min:
            x_max = x_min + 1.0
        if y_max <= y_min:
            y_max = y_min + 1.0

        x_pad = 0.02 * (x_max - x_min)
        y_pad = 0.08 * (y_max - y_min)
        x_min -= x_pad
        x_max += x_pad
        y_min -= y_pad
        y_max += y_pad

        def map_pt(x, y):
            xp = plot_rect.left() + (x - x_min) / (x_max - x_min) * plot_rect.width()
            yp = plot_rect.bottom() - (y - y_min) / (y_max - y_min) * plot_rect.height()
            return QPointF(xp, yp)

        grid_pen = QPen(QColor('#d7d7d7'), 1.0)
        grid_pen.setStyle(Qt.DashLine)
        painter.setPen(grid_pen)
        n_grid = 5
        for i in range(1, n_grid):
            gx = plot_rect.left() + (i / float(n_grid)) * plot_rect.width()
            gy = plot_rect.top() + (i / float(n_grid)) * plot_rect.height()
            painter.drawLine(QPointF(gx, plot_rect.top()), QPointF(gx, plot_rect.bottom()))
            painter.drawLine(QPointF(plot_rect.left(), gy), QPointF(plot_rect.right(), gy))

        for s in self._series:
            points = s.get('points', [])
            if len(points) < 2:
                continue
            poly = QPolygonF([map_pt(px, py) for px, py in points])
            pen = QPen(QColor(s.get('color', '#000000')), float(s.get('width', 2.0)))
            pen.setStyle(s.get('style', Qt.SolidLine))
            painter.setPen(pen)
            painter.drawPolyline(poly)

        painter.setPen(QPen(QColor('#222222'), 1.0))
        painter.setFont(QFont('Sans Serif', 9))
        painter.drawText(QRectF(plot_rect.left(), plot_rect.bottom() + 8, plot_rect.width(), 20), Qt.AlignCenter, self._x_label)

        painter.save()
        painter.translate(rect.left() + 16, plot_rect.center().y())
        painter.rotate(-90)
        painter.drawText(QRectF(-plot_rect.height() / 2, -16, plot_rect.height(), 20), Qt.AlignCenter, self._y_label)
        painter.restore()

        tick_font = QFont('Sans Serif', 8)
        painter.setFont(tick_font)
        painter.drawText(QRectF(plot_rect.left() - 30, plot_rect.bottom() - 8, 60, 14), Qt.AlignCenter, f"{x_min:.1f}")
        painter.drawText(QRectF(plot_rect.right() - 30, plot_rect.bottom() - 8, 60, 14), Qt.AlignCenter, f"{x_max:.1f}")
        painter.drawText(QRectF(plot_rect.left() - 56, plot_rect.top() - 6, 52, 14), Qt.AlignRight, f"{y_max:.1f}")
        painter.drawText(QRectF(plot_rect.left() - 56, plot_rect.bottom() - 8, 52, 14), Qt.AlignRight, f"{y_min:.1f}")

        legend_items = [s for s in self._series if s.get('label')]
        if legend_items:
            item_h = 16.0
            legend_w = min(280.0, plot_rect.width() * 0.55)
            legend_h = min(plot_rect.height() * 0.55, item_h * len(legend_items) + 10.0)
            legend_rect = QRectF(plot_rect.right() - legend_w - legend_pad, plot_rect.top() + legend_pad, legend_w, legend_h)
            painter.fillRect(legend_rect, QColor(255, 255, 255, 235))
            painter.setPen(QPen(QColor('#bbbbbb'), 1.0))
            painter.drawRect(legend_rect)

            y = legend_rect.top() + 8.0
            for item in legend_items:
                if y + item_h > legend_rect.bottom() - 2:
                    break
                painter.setPen(QPen(QColor(item.get('color', '#000000')), float(item.get('width', 2.0))))
                painter.drawLine(QPointF(legend_rect.left() + 8.0, y + 6.0), QPointF(legend_rect.left() + 26.0, y + 6.0))
                painter.setPen(QPen(QColor('#222222'), 1.0))
                painter.drawText(QRectF(legend_rect.left() + 30.0, y - 2.0, legend_rect.width() - 34.0, item_h), Qt.AlignLeft | Qt.AlignVCenter, str(item.get('label', '')))
                y += item_h

        # Factor-of-Safety colour scale (shown when several surfaces are plotted)
        if self._colorbar:
            try:
                self._draw_colorbar(painter, plot_rect, self._colorbar)
            except Exception:
                pass

        if include_table:
            table_rect = QRectF(plot_rect.left(), plot_rect.bottom() + 20.0, plot_rect.width(), table_h - 24.0)
            self._draw_table(painter, table_rect, table_rows)

    def _draw_colorbar(self, painter, plot_rect, colorbar):
        """Draw a horizontal Factor-of-Safety colour scale in the top-left of the plot."""
        fs_min = float(colorbar.get('fs_min', 0.0))
        fs_max = float(colorbar.get('fs_max', 1.0))

        bar_w = min(170.0, plot_rect.width() * 0.42)
        bar_h = 10.0
        bx = plot_rect.left() + 12.0
        by = plot_rect.top() + 24.0

        # White backdrop so the scale stays readable over the grid/surfaces
        painter.fillRect(QRectF(bx - 6.0, by - 18.0, bar_w + 12.0, bar_h + 38.0), QColor(255, 255, 255, 235))

        steps = 64
        seg_w = bar_w / steps
        for i in range(steps):
            t = i / float(steps - 1)
            r, g, b = fos_to_rgb(t)
            painter.fillRect(QRectF(bx + i * seg_w, by, seg_w + 1.0, bar_h), QColor(r, g, b))
        painter.setPen(QPen(QColor('#666666'), 1.0))
        painter.drawRect(QRectF(bx, by, bar_w, bar_h))

        painter.setPen(QPen(QColor('#222222'), 1.0))
        painter.setFont(QFont('Sans Serif', 8))
        painter.drawText(QRectF(bx, by - 15.0, bar_w, 12.0), Qt.AlignHCenter, 'Factor of Safety')
        painter.drawText(QRectF(bx, by + bar_h + 1.0, bar_w, 12.0), Qt.AlignLeft, f"{fs_min:.2f}")
        painter.drawText(QRectF(bx, by + bar_h + 1.0, bar_w, 12.0), Qt.AlignRight, f"{fs_max:.2f}")

    def _draw_table(self, painter, rect, rows):
        headers = ['Layer', 'γ (kN/m³)', 'c (kPa)', 'φ (°)', 'n']
        painter.setPen(QPen(QColor('#9a9a9a'), 1.0))
        painter.drawRect(rect)

        if not rows:
            painter.setPen(QPen(QColor('#444444'), 1.0))
            painter.drawText(rect, Qt.AlignCenter, 'No stratigraphy or water table defined.')
            return

        n_cols = len(headers)
        n_rows = len(rows) + 1
        col_w = rect.width() / float(n_cols)
        row_h = rect.height() / float(n_rows)

        painter.fillRect(QRectF(rect.left(), rect.top(), rect.width(), row_h), QColor('#f2f2f2'))
        for i in range(1, n_cols):
            x = rect.left() + i * col_w
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        for i in range(1, n_rows):
            y = rect.top() + i * row_h
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        painter.setPen(QPen(QColor('#222222'), 1.0))
        painter.setFont(QFont('Sans Serif', 8))
        for i, h in enumerate(headers):
            cell = QRectF(rect.left() + i * col_w + 4.0, rect.top(), col_w - 8.0, row_h)
            painter.drawText(cell, Qt.AlignLeft | Qt.AlignVCenter, h)

        for r, row in enumerate(rows, start=1):
            for c, val in enumerate(row):
                cell = QRectF(rect.left() + c * col_w + 4.0, rect.top() + r * row_h, col_w - 8.0, row_h)
                painter.drawText(cell, Qt.AlignLeft | Qt.AlignVCenter, str(val))


class ProfileDialog(QDialog):
    """Dialog principale per configurare e visualizzare il profilo.

    Segnali:
      - startSelectionRequested: l'utente chiede di selezionare i due punti sulla mappa
      - computeProfileRequested: richiesto il calcolo del profilo (passa layer raster e punti)
      - exportRequested: richiesto export CSV
    """
    startSelectionRequested = pyqtSignal()
    computeProfileRequested = pyqtSignal(object, object, object)  # raster_layer, p1, p2
    exportRequested = pyqtSignal(str)
    # Nuovi segnali per export avanzati e salvataggio progetto
    exportResultsRequested = pyqtSignal(str)                # path
    exportImageRequested = pyqtSignal(str, str)             # format, path
    exportDxfRequested = pyqtSignal(str)                    # path
    saveProjectRequested = pyqtSignal(str)                  # path (.rslope JSON)
    loadProjectRequested = pyqtSignal(str)                  # path (.rslope JSON)
    gridStabilityAnalysisRequested = pyqtSignal(dict)  # parameters for grid analysis
    simplexStabilityAnalysisRequested = pyqtSignal(dict)  # parameters for simplex analysis
    clearSurfacesRequested = pyqtSignal()  # richiesta pulizia superfici dal grafico

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Terrain Profile - The Raise Of Slopes")
        self._raster_layer = None
        self._p1 = None
        self._p2 = None
        self._last_profile_distances = []
        self._last_profile_elevations = []
        self._last_surfaces_list = None
        self._plugin = None  # Riferimento al plugin per accedere ai metodi di calcolo
        self._build_ui()

    def _settings_key(self):
        return "TheRaiseOfSlopes/lastPath"

    def _get_last_dir(self):
        try:
            v = QSettings().value(self._settings_key(), "")
            return str(v) if v is not None else ""
        except Exception:
            return ""

    def _set_last_dir_from_path(self, path):
        try:
            import os
            if path:
                QSettings().setValue(self._settings_key(), os.path.dirname(path))
        except Exception:
            pass
    
    def set_plugin(self, plugin):
        """Imposta il riferimento al plugin principale."""
        self._plugin = plugin

    def _build_ui(self):
        """Costruisce i widget dell'interfaccia con schede per profilo e analisi stabilità."""
        layout = QVBoxLayout(self)
        
        # Crea il widget a schede
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)
        
        # Prima scheda: Profilo altimetrico
        self._create_profile_tab()
        
        # Seconda scheda: Parametri geotecnici (condivisi)
        self._create_soil_parameters_tab()
        
        # Terza scheda: Stratigrafia e Falda
        self._create_stratigraphy_tab()
        
        # Quarta scheda: Analisi di stabilità con Griglia
        self._create_grid_stability_tab()
        
        # Quinta scheda: Analisi di stabilità con Simplex
        self._create_simplex_stability_tab()

        # Widget calcolo FOS sempre visibile (sotto le tab)
        fos_group = QGroupBox("Factor of Safety Calculation")
        fos_layout = QHBoxLayout(fos_group)
        self.btnRunAnalysis = QToolButton()
        self.btnRunAnalysis.setText("Run Simplex Analysis")
        self.btnRunAnalysis.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btnRunAnalysis.setPopupMode(QToolButton.MenuButtonPopup)
        self.btnRunAnalysis.clicked.connect(self._emit_simplex_stability_analysis)
        run_menu = QMenu(self)
        act_grid = QAction("Run Grid Analysis", self)
        act_grid.triggered.connect(self._emit_grid_stability_analysis)
        run_menu.addAction(act_grid)
        self.btnRunAnalysis.setMenu(run_menu)
        self.btnRunAnalysis.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        fos_layout.addWidget(self.btnRunAnalysis)
        layout.addWidget(fos_group)

        # Status bar comune
        self.lblStatus = QLabel("")
        layout.addWidget(self.lblStatus)
        
        self._reload_rasters()

    def _create_profile_tab(self):
        """Crea la scheda per il calcolo del profilo altimetrico."""
        profile_widget = QWidget()
        layout = QVBoxLayout(profile_widget)

        # Selezione DEM
        hl = QHBoxLayout()
        hl.addWidget(QLabel("DEM:"))
        self.cboRaster = QComboBox()
        hl.addWidget(self.cboRaster)
        self.btnReload = QPushButton("Refresh")
        self.btnReload.clicked.connect(self._reload_rasters)
        hl.addWidget(self.btnReload)
        layout.addLayout(hl)

        # Selezione punti
        self.btnSelect = QPushButton("Select 2 points")
        self.btnSelect.clicked.connect(self.startSelectionRequested.emit)
        layout.addWidget(self.btnSelect)

        self.lblPoints = QLabel("P1: -  P2: -")
        layout.addWidget(self.lblPoints)

        # Calcolo profilo: rimosso il pulsante, calcolo automatico al secondo punto

        # Grafico Qt nativo (compatibile con QGIS 4)
        self.profile_canvas = ProfileCanvas(self)
        layout.addWidget(self.profile_canvas)

        # Azioni profilo
        hl_actions = QHBoxLayout()
        self.btnClearSurfaces = QPushButton("Clear surfaces")
        self.btnClearSurfaces.clicked.connect(self.clearSurfacesRequested.emit)
        hl_actions.addWidget(self.btnClearSurfaces)

        # Export button as dropdown to save horizontal space
        self.btnExport = QToolButton()
        self.btnExport.setText("Export")
        export_menu = QMenu(self)
        act_csv = QAction("Export CSV", self)
        act_csv.triggered.connect(self._do_export)
        export_menu.addAction(act_csv)
        act_txt = QAction("Export results (TXT)", self)
        act_txt.triggered.connect(self._do_export_results)
        export_menu.addAction(act_txt)
        act_img = QAction("Export image (PNG/SVG)", self)
        act_img.triggered.connect(self._do_export_image)
        export_menu.addAction(act_img)
        act_dxf = QAction("Export DXF", self)
        act_dxf.triggered.connect(self._do_export_dxf)
        export_menu.addAction(act_dxf)

        # Opzione per includere la legenda stratigrafica nell'export immagine
        act_include_legend = QAction("Include stratigraphy legend", self)
        act_include_legend.setCheckable(True)
        # Carica valore salvato nelle impostazioni (di default True)
        try:
            v = QSettings().value("TheRaiseOfSlopes/includeLegend", True)
            act_include_legend.setChecked(bool(v) if not isinstance(v, str) else v.lower() in ('1', 'true', 'yes'))
        except Exception:
            act_include_legend.setChecked(True)
        act_include_legend.toggled.connect(self._on_include_legend_toggled)
        export_menu.addSeparator()
        export_menu.addAction(act_include_legend)
        self.act_include_legend = act_include_legend

        self.btnExport.setMenu(export_menu)
        self.btnExport.setPopupMode(QToolButton.InstantPopup)
        hl_actions.addWidget(self.btnExport)

        # Project menu (save / load) also grouped to save space
        self.btnProject = QToolButton()
        self.btnProject.setText("Project")
        proj_menu = QMenu(self)
        act_save = QAction("Save project (.rslope)", self)
        act_save.triggered.connect(self._do_save_project)
        proj_menu.addAction(act_save)
        act_load = QAction("Open project (.rslope)", self)
        act_load.triggered.connect(self._do_load_project)
        proj_menu.addAction(act_load)
        self.btnProject.setMenu(proj_menu)
        self.btnProject.setPopupMode(QToolButton.InstantPopup)
        hl_actions.addWidget(self.btnProject)

        layout.addLayout(hl_actions)

        # Visibilità superfici
        vis_group = QGroupBox("Surface visibility")
        vis_layout = QVBoxLayout(vis_group)
        self.surface_visibility_list = QListWidget()
        self.surface_visibility_list.itemChanged.connect(self._on_surface_visibility_changed)
        vis_layout.addWidget(self.surface_visibility_list)
        layout.addWidget(vis_group)
        
        self.tab_widget.addTab(profile_widget, "Elevation Profile")

    def _create_soil_parameters_tab(self):
        """Crea la scheda per i parametri geotecnici (condivisi tra griglia e simplex)."""
        soil_widget = QWidget()
        layout = QVBoxLayout(soil_widget)
        
        # Gruppo parametri del terreno
        soil_group = QGroupBox("Soil Geotechnical Parameters")
        soil_layout = QFormLayout(soil_group)
        
        # Peso specifico
        self.gamma_spinbox = QDoubleSpinBox()
        self.gamma_spinbox.setRange(10.0, 30.0)
        self.gamma_spinbox.setValue(20.0)
        self.gamma_spinbox.setSuffix(" kN/m³")
        self.gamma_spinbox.setDecimals(1)
        soil_layout.addRow("Unit weight (γ):", self.gamma_spinbox)
        
        # Cohesion
        self.cohesion_spinbox = QDoubleSpinBox()
        self.cohesion_spinbox.setRange(0.0, 100.0)
        self.cohesion_spinbox.setValue(10.0)
        self.cohesion_spinbox.setSuffix(" kPa")
        self.cohesion_spinbox.setDecimals(1)
        soil_layout.addRow("Cohesion (c):", self.cohesion_spinbox)
        
        # Porosity
        self.porosity_spinbox = QDoubleSpinBox()
        self.porosity_spinbox.setRange(0.0, 1.0)
        self.porosity_spinbox.setValue(0.3)
        self.porosity_spinbox.setDecimals(2)
        self.porosity_spinbox.setSingleStep(0.05)
        soil_layout.addRow("Porosity (n):", self.porosity_spinbox)
        
        # Friction angle
        self.friction_angle_spinbox = QDoubleSpinBox()
        self.friction_angle_spinbox.setRange(0.0, 45.0)
        self.friction_angle_spinbox.setValue(25.0)
        self.friction_angle_spinbox.setSuffix(" °")
        self.friction_angle_spinbox.setDecimals(1)
        soil_layout.addRow("Friction angle (φ):", self.friction_angle_spinbox)
        
        layout.addWidget(soil_group)
        
        # Gruppo parametri dell'analisi (comuni)
        analysis_group = QGroupBox("Analysis Parameters")
        analysis_layout = QFormLayout(analysis_group)
        
        # Numero di conci
        self.slices_spinbox = QSpinBox()
        self.slices_spinbox.setRange(10, 100)
        self.slices_spinbox.setValue(50)
        analysis_layout.addRow("Number of slices:", self.slices_spinbox)
        
        # Profondità della superficie di scivolamento
        self.depth_factor_spinbox = QDoubleSpinBox()
        self.depth_factor_spinbox.setRange(0.1, 2.0)
        self.depth_factor_spinbox.setValue(0.5)
        self.depth_factor_spinbox.setDecimals(2)
        analysis_layout.addRow("Depth factor:", self.depth_factor_spinbox)
        
        layout.addWidget(analysis_group)
        
        # Gruppo parametri secondo strato
        layer2_group = QGroupBox("Parametri Geotecnici Secondo Strato (opzionale)")
        layer2_layout = QFormLayout(layer2_group)
        
        self.gamma_2_spinbox = QDoubleSpinBox()
        self.gamma_2_spinbox.setRange(10.0, 30.0)
        self.gamma_2_spinbox.setValue(22.0)
        self.gamma_2_spinbox.setSuffix(" kN/m³")
        self.gamma_2_spinbox.setDecimals(1)
        layer2_layout.addRow("Unit weight (γ₂):", self.gamma_2_spinbox)
        
        self.cohesion_2_spinbox = QDoubleSpinBox()
        self.cohesion_2_spinbox.setRange(0.0, 1000.0)
        self.cohesion_2_spinbox.setValue(50.0)
        self.cohesion_2_spinbox.setSuffix(" kPa")
        self.cohesion_2_spinbox.setDecimals(1)
        layer2_layout.addRow("Cohesion (c₂):", self.cohesion_2_spinbox)
        
        # Porosità secondo strato
        self.porosity_2_spinbox = QDoubleSpinBox()
        self.porosity_2_spinbox.setRange(0.0, 1.0)
        self.porosity_2_spinbox.setValue(0.25)
        self.porosity_2_spinbox.setDecimals(2)
        self.porosity_2_spinbox.setSingleStep(0.05)
        layer2_layout.addRow("Porosity (n₂):", self.porosity_2_spinbox)
        
        self.friction_angle_2_spinbox = QDoubleSpinBox()
        self.friction_angle_2_spinbox.setRange(0.0, 45.0)
        self.friction_angle_2_spinbox.setValue(30.0)
        self.friction_angle_2_spinbox.setSuffix(" °")
        self.friction_angle_2_spinbox.setDecimals(1)
        layer2_layout.addRow("Friction angle (φ₂):", self.friction_angle_2_spinbox)
        
        layout.addWidget(layer2_group)
        
        # Aggiungi uno stretch per spingere tutto in alto
        layout.addStretch()
        
        self.tab_widget.addTab(soil_widget, "Soil Parameters")

    def _create_stratigraphy_tab(self):
        """Crea la scheda per la configurazione di stratigrafia e falda."""
        strat_widget = QWidget()
        layout = QVBoxLayout(strat_widget)
        
        # Gruppo secondo strato
        layer2_group = QGroupBox("Second Layer")
        layer2_layout = QVBoxLayout(layer2_group)
        
        # Checkbox to enable second layer
        self.enable_layer2_checkbox = QCheckBox("Enable second layer")
        self.enable_layer2_checkbox.stateChanged.connect(self._on_layer2_enabled_changed)
        layer2_layout.addWidget(self.enable_layer2_checkbox)
        
        # Widget container per parametri secondo strato
        self.layer2_params_widget = QWidget()
        layer2_params_layout = QFormLayout(self.layer2_params_widget)
        
        # Modalità definizione interfaccia
        interface_group = QGroupBox("Layer Interface Definition")
        interface_layout = QVBoxLayout(interface_group)
        
        self.layer2_definition_group = QButtonGroup()
        
        self.layer2_const_depth_radio = QRadioButton("Constant depth from ground surface")
        self.layer2_definition_group.addButton(self.layer2_const_depth_radio, 0)
        interface_layout.addWidget(self.layer2_const_depth_radio)
        
        self.layer2_const_depth_spinbox = QDoubleSpinBox()
        self.layer2_const_depth_spinbox.setRange(0.1, 100.0)
        self.layer2_const_depth_spinbox.setValue(5.0)
        self.layer2_const_depth_spinbox.setSuffix(" m")
        self.layer2_const_depth_spinbox.setDecimals(2)
        self.layer2_const_depth_spinbox.valueChanged.connect(self._refresh_profile_display)
        interface_layout.addWidget(self.layer2_const_depth_spinbox)
        
        self.layer2_raster_depth_radio = QRadioButton("Depth from raster")
        self.layer2_definition_group.addButton(self.layer2_raster_depth_radio, 1)
        interface_layout.addWidget(self.layer2_raster_depth_radio)
        
        self.layer2_raster_combo = QComboBox()
        self.layer2_raster_combo.currentIndexChanged.connect(self._refresh_profile_display)
        interface_layout.addWidget(self.layer2_raster_combo)
        
        self.layer2_elevation_radio = QRadioButton("Absolute elevation")
        self.layer2_definition_group.addButton(self.layer2_elevation_radio, 2)
        interface_layout.addWidget(self.layer2_elevation_radio)
        
        self.layer2_elevation_spinbox = QDoubleSpinBox()
        self.layer2_elevation_spinbox.setRange(-1000.0, 10000.0)
        self.layer2_elevation_spinbox.setValue(0.0)
        self.layer2_elevation_spinbox.setSuffix(" m")
        self.layer2_elevation_spinbox.setDecimals(2)
        self.layer2_elevation_spinbox.valueChanged.connect(self._refresh_profile_display)
        interface_layout.addWidget(self.layer2_elevation_spinbox)
        
        self.layer2_const_depth_radio.setChecked(True)
        self.layer2_definition_group.buttonClicked.connect(self._on_layer2_definition_changed)
        
        layer2_params_layout.addRow(interface_group)
        
        layer2_layout.addWidget(self.layer2_params_widget)
        self.layer2_params_widget.setEnabled(False)
        
        layout.addWidget(layer2_group)
        
        # Gruppo falda
        water_group = QGroupBox("Water Table")
        water_layout = QVBoxLayout(water_group)
        
        self.enable_water_checkbox = QCheckBox("Enable water table")
        self.enable_water_checkbox.stateChanged.connect(self._on_water_enabled_changed)
        water_layout.addWidget(self.enable_water_checkbox)
        
        self.water_params_widget = QWidget()
        water_params_layout = QFormLayout(self.water_params_widget)
        
        # Water definition modes
        water_def_group = QGroupBox("Water Table Definition")
        water_def_layout = QVBoxLayout(water_def_group)
        
        self.water_definition_group = QButtonGroup()
        
        self.water_const_depth_radio = QRadioButton("Constant depth from ground surface")
        self.water_definition_group.addButton(self.water_const_depth_radio, 0)
        water_def_layout.addWidget(self.water_const_depth_radio)
        
        self.water_const_depth_spinbox = QDoubleSpinBox()
        self.water_const_depth_spinbox.setRange(0.0, 100.0)
        self.water_const_depth_spinbox.setValue(2.0)
        self.water_const_depth_spinbox.setSuffix(" m")
        self.water_const_depth_spinbox.setDecimals(2)
        self.water_const_depth_spinbox.valueChanged.connect(self._refresh_profile_display)
        water_def_layout.addWidget(self.water_const_depth_spinbox)
        
        self.water_raster_depth_radio = QRadioButton("Depth from raster")
        self.water_definition_group.addButton(self.water_raster_depth_radio, 1)
        water_def_layout.addWidget(self.water_raster_depth_radio)
        
        self.water_raster_combo = QComboBox()
        self.water_raster_combo.currentIndexChanged.connect(self._refresh_profile_display)
        water_def_layout.addWidget(self.water_raster_combo)
        
        self.water_elevation_radio = QRadioButton("Absolute elevation")
        self.water_definition_group.addButton(self.water_elevation_radio, 2)
        water_def_layout.addWidget(self.water_elevation_radio)
        
        self.water_elevation_spinbox = QDoubleSpinBox()
        self.water_elevation_spinbox.setRange(-1000.0, 10000.0)
        self.water_elevation_spinbox.setValue(0.0)
        self.water_elevation_spinbox.setSuffix(" m")
        self.water_elevation_spinbox.setDecimals(2)
        self.water_elevation_spinbox.valueChanged.connect(self._refresh_profile_display)
        water_def_layout.addWidget(self.water_elevation_spinbox)
        
        self.water_const_depth_radio.setChecked(True)
        self.water_definition_group.buttonClicked.connect(self._on_water_definition_changed)
        
        water_params_layout.addRow(water_def_group)
        
        water_layout.addWidget(self.water_params_widget)
        self.water_params_widget.setEnabled(False)
        
        layout.addWidget(water_group)
        
        # Aggiungi uno stretch per spingere tutto in alto
        layout.addStretch()
        
        self.tab_widget.addTab(strat_widget, "Stratigraphy & Water Table")
    
    def _on_layer2_enabled_changed(self, state):
        """Gestisce l'abilitazione/disabilitazione dei parametri del secondo strato."""
        self.layer2_params_widget.setEnabled(state == Qt.Checked)
        if state == Qt.Checked:
            self._reload_layer2_rasters()
        # Aggiorna il grafico
        self._refresh_profile_display()
    
    def _on_layer2_definition_changed(self):
        """Aggiorna l'interfaccia in base alla modalità selezionata."""
        selected_id = self.layer2_definition_group.checkedId()
        self.layer2_const_depth_spinbox.setEnabled(selected_id == 0)
        self.layer2_raster_combo.setEnabled(selected_id == 1)
        self.layer2_elevation_spinbox.setEnabled(selected_id == 2)
        # Aggiorna il grafico
        self._refresh_profile_display()
    
    def _on_water_enabled_changed(self, state):
        """Gestisce l'abilitazione/disabilitazione dei parametri della falda."""
        self.water_params_widget.setEnabled(state == Qt.Checked)
        if state == Qt.Checked:
            self._reload_water_rasters()
        # Aggiorna il grafico
        self._refresh_profile_display()
    
    def _on_water_definition_changed(self):
        """Aggiorna l'interfaccia in base alla modalità selezionata per la falda."""
        selected_id = self.water_definition_group.checkedId()
        self.water_const_depth_spinbox.setEnabled(selected_id == 0)
        self.water_raster_combo.setEnabled(selected_id == 1)
        self.water_elevation_spinbox.setEnabled(selected_id == 2)
        # Aggiorna il grafico
        self._refresh_profile_display()
    
    def _reload_layer2_rasters(self):
        """Popola la combo per il raster del secondo strato."""
        self.layer2_raster_combo.clear()
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                if lyr.type() == lyr.RasterLayer:
                    self.layer2_raster_combo.addItem(lyr.name(), lyr)
            except Exception:
                continue
    
    def _reload_water_rasters(self):
        """Popola la combo per il raster della falda."""
        self.water_raster_combo.clear()
        for lyr in QgsProject.instance().mapLayers().values():
            try:
                if lyr.type() == lyr.RasterLayer:
                    self.water_raster_combo.addItem(lyr.name(), lyr)
            except Exception:
                continue
    
    def _refresh_profile_display(self):
        """Aggiorna la visualizzazione del profilo con le superfici e stratigrafia correnti."""
        if not self._last_profile_distances or not self._last_profile_elevations:
            return
        
        # Ridisegna usando i dati salvati e le superfici correnti
        self.updateProfile(
            self._last_profile_distances,
            self._last_profile_elevations,
            slip_surfaces_list=self._last_surfaces_list
        )

    def _create_grid_stability_tab(self):
        """Crea la scheda per l'analisi di stabilità con griglia di cerchi (Bishop/GLE)."""
        stability_widget = QWidget()
        layout = QVBoxLayout(stability_widget)
        
        # Gruppo parametri griglia
        grid_group = QGroupBox("Grid Search Parameters")
        grid_layout = QFormLayout(grid_group)

        # Calculation method
        self.grid_method_combo = QComboBox()
        self.grid_method_combo.addItems(["Bishop", "Morgenstern-Price", "Spencer"])
        grid_layout.addRow("Calculation method:", self.grid_method_combo)
        
        # Number of entry points
        self.num_in_pts_spinbox = QSpinBox()
        self.num_in_pts_spinbox.setRange(5, 50)
        self.num_in_pts_spinbox.setValue(16)
        grid_layout.addRow("Number of entry points:", self.num_in_pts_spinbox)
        
        # Number of exit points
        self.num_out_pts_spinbox = QSpinBox()
        self.num_out_pts_spinbox.setRange(5, 50)
        self.num_out_pts_spinbox.setValue(16)
        grid_layout.addRow("Number of exit points:", self.num_out_pts_spinbox)
        
        # Min eta increment
        self.min_eta_inc_spinbox = QDoubleSpinBox()
        self.min_eta_inc_spinbox.setRange(1.0, 20.0)
        self.min_eta_inc_spinbox.setValue(5.0)
        self.min_eta_inc_spinbox.setSuffix(" °")
        self.min_eta_inc_spinbox.setDecimals(1)
        grid_layout.addRow("Min η increment:", self.min_eta_inc_spinbox)
        
        # In interval (fraction of profile)
        self.in_interval_min_spinbox = QDoubleSpinBox()
        self.in_interval_min_spinbox.setRange(0.0, 1.0)
        self.in_interval_min_spinbox.setValue(0.6)
        self.in_interval_min_spinbox.setDecimals(2)
        grid_layout.addRow("In - min (fraction):", self.in_interval_min_spinbox)
        
        self.in_interval_max_spinbox = QDoubleSpinBox()
        self.in_interval_max_spinbox.setRange(0.0, 1.0)
        self.in_interval_max_spinbox.setValue(1.0)
        self.in_interval_max_spinbox.setDecimals(2)
        grid_layout.addRow("In - max (fraction):", self.in_interval_max_spinbox)
        
        # Out interval (fraction of profile)
        self.out_interval_min_spinbox = QDoubleSpinBox()
        self.out_interval_min_spinbox.setRange(0.0, 1.0)
        self.out_interval_min_spinbox.setValue(0.0)
        self.out_interval_min_spinbox.setDecimals(2)
        grid_layout.addRow("Out - min (fraction):", self.out_interval_min_spinbox)
        
        self.out_interval_max_spinbox = QDoubleSpinBox()
        self.out_interval_max_spinbox.setRange(0.0, 1.0)
        self.out_interval_max_spinbox.setValue(0.4)
        self.out_interval_max_spinbox.setDecimals(2)
        grid_layout.addRow("Out - max (fraction):", self.out_interval_max_spinbox)

        # Number of slip surfaces to display (coloured by Factor of Safety)
        self.grid_num_surfaces_spinbox = QSpinBox()
        self.grid_num_surfaces_spinbox.setRange(1, 50)
        self.grid_num_surfaces_spinbox.setValue(1)
        self.grid_num_surfaces_spinbox.setToolTip(
            "Number of computed slip surfaces to display, ordered by Factor of Safety.\n"
            "Surfaces are coloured on a FoS scale (red = critical, green = safer).")
        grid_layout.addRow("Surfaces to display:", self.grid_num_surfaces_spinbox)

        layout.addWidget(grid_group)

        # Area risultati
        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout(results_group)
        
        self.grid_results_text = QTextEdit()
        self.grid_results_text.setMaximumHeight(150)
        self.grid_results_text.setPlainText("No analysis executed")
        results_layout.addWidget(self.grid_results_text)
        
        layout.addWidget(results_group)
        
        self.tab_widget.addTab(stability_widget, "Grid Analysis")

    def _create_simplex_stability_tab(self):
        """Crea la scheda per l'analisi di stabilità con ottimizzazione simplex."""
        stability_widget = QWidget()
        layout = QVBoxLayout(stability_widget)
        
        # Gruppo bounds ottimizzazione simplex
        bounds_group = QGroupBox("Simplex Optimization Bounds")
        bounds_layout = QFormLayout(bounds_group)

        # Metodo di calcolo stabilità
        self.simplex_method_combo = QComboBox()
        self.simplex_method_combo.addItems(["Bishop", "Morgenstern-Price", "Spencer"])
        bounds_layout.addRow("Calculation method:", self.simplex_method_combo)
        
        # x_in bounds (frazione del profilo)
        self.x_in_min_spinbox = QDoubleSpinBox()
        self.x_in_min_spinbox.setRange(0.0, 1.0)
        self.x_in_min_spinbox.setValue(0.5)
        self.x_in_min_spinbox.setDecimals(2)
        bounds_layout.addRow("x_in - min (fraction):", self.x_in_min_spinbox)
        
        self.x_in_max_spinbox = QDoubleSpinBox()
        self.x_in_max_spinbox.setRange(0.0, 1.0)
        self.x_in_max_spinbox.setValue(1.0)
        self.x_in_max_spinbox.setDecimals(2)
        bounds_layout.addRow("x_in - max (fraction):", self.x_in_max_spinbox)
        
        # x_out bounds (frazione del profilo)
        self.x_out_min_spinbox = QDoubleSpinBox()
        self.x_out_min_spinbox.setRange(0.0, 1.0)
        self.x_out_min_spinbox.setValue(0.0)
        self.x_out_min_spinbox.setDecimals(2)
        bounds_layout.addRow("x_out - min (fraction):", self.x_out_min_spinbox)
        
        self.x_out_max_spinbox = QDoubleSpinBox()
        self.x_out_max_spinbox.setRange(0.0, 1.0)
        self.x_out_max_spinbox.setValue(0.5)
        self.x_out_max_spinbox.setDecimals(2)
        bounds_layout.addRow("x_out - max (fraction):", self.x_out_max_spinbox)
        
        # eta bounds (gradi)
        self.eta_min_spinbox = QDoubleSpinBox()
        self.eta_min_spinbox.setRange(0.0, 90.0)
        self.eta_min_spinbox.setValue(0.0)
        self.eta_min_spinbox.setSuffix(" °")
        self.eta_min_spinbox.setDecimals(1)
        bounds_layout.addRow("η - min:", self.eta_min_spinbox)
        
        self.eta_max_spinbox = QDoubleSpinBox()
        self.eta_max_spinbox.setRange(0.0, 90.0)
        self.eta_max_spinbox.setValue(90.0)
        self.eta_max_spinbox.setSuffix(" °")
        self.eta_max_spinbox.setDecimals(1)
        bounds_layout.addRow("η - max:", self.eta_max_spinbox)
        
        layout.addWidget(bounds_group)
        
        # Gruppo parametri ottimizzazione
        optimization_group = QGroupBox("Optimization Parameters")
        optimization_layout = QFormLayout(optimization_group)
        
        # Numero massimo iterazioni
        self.max_iterations_spinbox = QSpinBox()
        self.max_iterations_spinbox.setRange(50, 1000)
        self.max_iterations_spinbox.setValue(300)
        optimization_layout.addRow("Max iterations:", self.max_iterations_spinbox)

        # Number of slip surfaces to display (coloured by Factor of Safety)
        self.simplex_num_surfaces_spinbox = QSpinBox()
        self.simplex_num_surfaces_spinbox.setRange(1, 50)
        self.simplex_num_surfaces_spinbox.setValue(1)
        self.simplex_num_surfaces_spinbox.setToolTip(
            "Number of optimised slip surfaces to display, ordered by Factor of Safety.\n"
            "Surfaces are coloured on a FoS scale (red = critical, green = safer).")
        optimization_layout.addRow("Surfaces to display:", self.simplex_num_surfaces_spinbox)

        layout.addWidget(optimization_group)

        # Area risultati
        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout(results_group)
        
        self.simplex_results_text = QTextEdit()
        self.simplex_results_text.setMaximumHeight(150)
        self.simplex_results_text.setPlainText("No analysis executed")
        results_layout.addWidget(self.simplex_results_text)
        
        layout.addWidget(results_group)
        
        self.tab_widget.addTab(stability_widget, "Simplex Analysis")

    def _reload_rasters(self):
        """Popola la combo con i raster presenti nel progetto (solo layer di tipo Raster)."""
        self.cboRaster.clear()
        for lyr in QgsProject.instance().mapLayers().values():
            # Uso attributi robusti per raster
            try:
                if lyr.type() == lyr.RasterLayer:
                    self.cboRaster.addItem(lyr.name(), lyr)
            except Exception:
                continue

    def setSelectedPoints(self, p1, p2):
        self._p1, self._p2 = p1, p2
        self.lblPoints.setText(f"P1: ({p1.x():.2f},{p1.y():.2f})  P2: ({p2.x():.2f},{p2.y():.2f})")

    def _emit_compute(self):
        # Obsoleto: il profilo viene calcolato automaticamente al secondo punto
        raster_layer = self.cboRaster.currentData()
        self.computeProfileRequested.emit(raster_layer, self._p1, self._p2)

    def _emit_grid_stability_analysis(self):
        """Raccoglie i parametri e emette il segnale per l'analisi di stabilità con griglia."""
        x_max = self.profile_distances[-1] if hasattr(self, 'profile_distances') and self.profile_distances else 100.0
        
        params = {
            'analysis_type': 'grid',
            'stability_method': self.grid_method_combo.currentText(),
            'gamma': self.gamma_spinbox.value(),
            'cohesion': self.cohesion_spinbox.value(),
            'porosity': self.porosity_spinbox.value(),
            'friction_angle': self.friction_angle_spinbox.value(),
            'num_slices': self.slices_spinbox.value(),
            'depth_factor': self.depth_factor_spinbox.value(),
            'num_in_pts': self.num_in_pts_spinbox.value(),
            'num_out_pts': self.num_out_pts_spinbox.value(),
            'min_eta_inc': self.min_eta_inc_spinbox.value(),
            'in_interval_min': self.in_interval_min_spinbox.value(),
            'in_interval_max': self.in_interval_max_spinbox.value(),
            'out_interval_min': self.out_interval_min_spinbox.value(),
            'out_interval_max': self.out_interval_max_spinbox.value(),
            'num_surfaces': self.grid_num_surfaces_spinbox.value(),
        }

        # Aggiungi parametri stratigrafia
        params.update(self._get_stratigraphy_params())

        self.gridStabilityAnalysisRequested.emit(params)

    def _emit_simplex_stability_analysis(self):
        """Raccoglie i parametri e emette il segnale per l'analisi di stabilità con simplex."""
        params = {
            'analysis_type': 'simplex',
            'stability_method': self.simplex_method_combo.currentText(),
            'gamma': self.gamma_spinbox.value(),
            'cohesion': self.cohesion_spinbox.value(),
            'porosity': self.porosity_spinbox.value(),
            'friction_angle': self.friction_angle_spinbox.value(),
            'num_slices': self.slices_spinbox.value(),
            'depth_factor': self.depth_factor_spinbox.value(),
            'x_in_min': self.x_in_min_spinbox.value(),
            'x_in_max': self.x_in_max_spinbox.value(),
            'x_out_min': self.x_out_min_spinbox.value(),
            'x_out_max': self.x_out_max_spinbox.value(),
            'eta_min': self.eta_min_spinbox.value(),
            'eta_max': self.eta_max_spinbox.value(),
            'max_iterations': self.max_iterations_spinbox.value(),
            'num_surfaces': self.simplex_num_surfaces_spinbox.value(),
        }

        # Aggiungi parametri stratigrafia
        params.update(self._get_stratigraphy_params())

        self.simplexStabilityAnalysisRequested.emit(params)

    def updateProfile(self, distances, elevations, slip_surface_points=None, slip_surfaces_list=None):
        """Aggiorna il grafico del profilo.

        Filtra i valori None (nodat / fuori raster). Se nessun dato valido mostra un messaggio.
        
        Args:
            distances: lista delle distanze
            elevations: lista delle quote
            slip_surface_points: tupla (x, y) - superficie singola (deprecato, usa slip_surfaces_list)
            slip_surfaces_list: lista di dizionari con 'x', 'y', 'color', 'label' per più superfici
        """
        # Salva ultimo profilo
        self._last_profile_distances = list(distances) if distances else []
        self._last_profile_elevations = list(elevations) if elevations else []

        # Se ho nuove superfici, aggiorno l'elenco di visibilità se cambia
        if slip_surfaces_list is not None:
            need_rebuild = False
            if self._last_surfaces_list is None:
                need_rebuild = True
            else:
                old_labels = [s.get('label', '') for s in self._last_surfaces_list]
                new_labels = [s.get('label', '') for s in slip_surfaces_list]
                if len(old_labels) != len(new_labels) or old_labels != new_labels:
                    need_rebuild = True
            self._last_surfaces_list = slip_surfaces_list
            if need_rebuild:
                self._populate_surface_visibility_list(self._last_surfaces_list)
        elif slip_surface_points is None:
            # Nessuna superficie: svuoto lista visibilità
            self._last_surfaces_list = None
            self.surface_visibility_list.clear()

        # Filtra valori None mantenendo allineamento
        dist_f = []
        elev_f = []
        for d, z in zip(distances, elevations):
            if z is not None:
                dist_f.append(d)
                elev_f.append(z)

        series = []
        if dist_f:
            series.append({
                'label': 'Ground profile',
                'color': '#000000',
                'width': 2.0,
                'style': Qt.SolidLine,
                'points': list(zip(dist_f, elev_f)),
            })

        # Disegna interfaccia secondo strato se abilitato
        if hasattr(self, 'enable_layer2_checkbox') and self.enable_layer2_checkbox.isChecked() and dist_f:
            layer2_y = self._calculate_layer2_interface(dist_f)
            if layer2_y is not None:
                points = [(x, y) for x, y in zip(dist_f, layer2_y) if y is not None]
                if points:
                    series.append({
                        'label': 'Layer 2 interface',
                        'color': '#1f77b4',
                        'width': 1.5,
                        'style': Qt.DashLine,
                        'points': points,
                    })

        # Disegna falda se abilitata
        if hasattr(self, 'enable_water_checkbox') and self.enable_water_checkbox.isChecked() and dist_f:
            water_y = self._calculate_water_table(dist_f)
            if water_y is not None:
                points = [(x, y) for x, y in zip(dist_f, water_y) if y is not None]
                if points:
                    series.append({
                        'label': 'Water table',
                        'color': '#17becf',
                        'width': 1.5,
                        'style': Qt.DashDotLine,
                        'points': points,
                    })

        # Plotta multiple superfici di scivolamento (priorità)
        visible_surfaces = []
        colorbar = None
        if slip_surfaces_list is not None and len(slip_surfaces_list) > 0:
            # Filtra solo le superfici visibili
            visible_indices = [i for i in range(self.surface_visibility_list.count())
                               if self.surface_visibility_list.item(i).checkState() == 2]
            visible_surfaces = [slip_surfaces_list[i] for i in visible_indices if i < len(slip_surfaces_list)]

            # With several surfaces, convey the Factor-of-Safety scale through the
            # colour bar and keep the legend uncluttered: only the critical
            # surface (drawn thicker) keeps a legend entry. The colour bar spans
            # the whole computed set (the colours were assigned over that set),
            # not just the currently visible subset.
            import math
            fs_vals = []
            for s in slip_surfaces_list:
                try:
                    fv = float(s.get('fs'))
                    if math.isfinite(fv):
                        fs_vals.append(fv)
                except Exception:
                    continue
            many = len(visible_surfaces) > 1
            if len(fs_vals) >= 2:
                fmn, fmx = min(fs_vals), max(fs_vals)
                if fmx - fmn > 1e-9:
                    colorbar = {'fs_min': fmn, 'fs_max': fmx}

            for surface in visible_surfaces:
                points = [(x, y) for x, y in zip(surface.get('x', []), surface.get('y', [])) if x is not None and y is not None]
                if not points:
                    continue
                width = float(surface.get('width', 2.0))
                label = surface.get('label', 'Critical slip surface')
                # Suppress per-surface legend entries for the non-critical curves
                # when many are shown (the colour bar already explains the scale).
                if many and width < 3.0:
                    label = ''
                series.append({
                    'label': label,
                    'color': surface.get('color', '#d62728'),
                    'width': width,
                    'style': Qt.SolidLine,
                    'points': points,
                })

        # Fallback: singola superficie (retrocompatibilità)
        elif slip_surface_points is not None:
            slip_x, slip_y = slip_surface_points
            points = [(x, y) for x, y in zip(slip_x, slip_y) if x is not None and y is not None]
            if points:
                series.append({
                    'label': 'Critical slip surface',
                    'color': '#d62728',
                    'width': 2.0,
                    'style': Qt.SolidLine,
                    'points': points,
                })

        self.profile_canvas.set_plot_data(series, colorbar=colorbar)

    def _export_stratigraphy_rows(self):
        rows = []
        try:
            rows.append([
                'Layer 1',
                f"{self.gamma_spinbox.value():.1f}",
                f"{self.cohesion_spinbox.value():.1f}",
                f"{self.friction_angle_spinbox.value():.1f}",
                f"{self.porosity_spinbox.value():.2f}",
            ])
        except Exception:
            pass

        try:
            if getattr(self, 'enable_layer2_checkbox', None) and self.enable_layer2_checkbox.isChecked():
                rows.append([
                    'Layer 2',
                    f"{self.gamma_2_spinbox.value():.1f}",
                    f"{self.cohesion_2_spinbox.value():.1f}",
                    f"{self.friction_angle_2_spinbox.value():.1f}",
                    f"{self.porosity_2_spinbox.value():.2f}",
                ])
        except Exception:
            pass
        return rows

    def export_profile_image(self, fmt, path, include_legend=False):
        self.profile_canvas.export_image(fmt=fmt, path=path, include_table=bool(include_legend), table_rows=self._export_stratigraphy_rows())

    def _populate_surface_visibility_list(self, surfaces_list):
        self.surface_visibility_list.blockSignals(True)
        self.surface_visibility_list.clear()
        for s in surfaces_list:
            label = s.get('label', 'Surface')
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | 2)  # ItemIsUserCheckable
            item.setCheckState(2)  # Checked
            self.surface_visibility_list.addItem(item)
        self.surface_visibility_list.blockSignals(False)

    def _on_surface_visibility_changed(self, item):
        """Aggiorna la visualizzazione quando cambia la visibilità di una superficie."""
        # Usa _refresh_profile_display per ridisegnare tutto correttamente
        self._refresh_profile_display()

    def _do_export(self):
        import os
        start_dir = self._get_last_dir() or ""
        default_path = os.path.join(start_dir, "profilo.csv") if start_dir else "profilo.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Save profile", default_path, "CSV (*.csv)")
        if path:
            self._set_last_dir_from_path(path)
            self.exportRequested.emit(path)

    def _do_export_results(self):
        import os
        start_dir = self._get_last_dir() or ""
        default_path = os.path.join(start_dir, "results.txt") if start_dir else "results.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save results", default_path, "Text files (*.txt);;All files (*.*)")
        if path:
            self._set_last_dir_from_path(path)
            self.exportResultsRequested.emit(path)

    def _do_export_image(self):
        import os
        start_dir = self._get_last_dir() or ""
        default_path = os.path.join(start_dir, "profilo.png") if start_dir else "profilo.png"
        path, _ = QFileDialog.getSaveFileName(self, "Export profile image", default_path, "PNG (*.png);;SVG (*.svg)")
        if path:
            self._set_last_dir_from_path(path)
            fmt = 'png' if path.lower().endswith('.png') else 'svg'
            self.exportImageRequested.emit(fmt, path)

    def _do_export_dxf(self):
        import os
        start_dir = self._get_last_dir() or ""
        default_path = os.path.join(start_dir, "profilo.dxf") if start_dir else "profilo.dxf"
        path, _ = QFileDialog.getSaveFileName(self, "Export DXF", default_path, "DXF (*.dxf)")
        if path:
            self._set_last_dir_from_path(path)
            self.exportDxfRequested.emit(path)

    def _do_save_project(self):
        import os
        start_dir = self._get_last_dir() or ""
        default_path = os.path.join(start_dir, "project.rslope") if start_dir else "project.rslope"
        path, _ = QFileDialog.getSaveFileName(self, "Save project", default_path, "RSlope project (*.rslope)")
        if path:
            # Assicura estensione .rslope
            if not path.lower().endswith('.rslope'):
                path = path + '.rslope'
            self._set_last_dir_from_path(path)
            self.saveProjectRequested.emit(path)

    def _do_load_project(self):
        start_dir = self._get_last_dir() or ""
        path, _ = QFileDialog.getOpenFileName(self, "Open project", start_dir, "RSlope project (*.rslope)")
        if path:
            self._set_last_dir_from_path(path)
            self.loadProjectRequested.emit(path)

    def setStatus(self, msg):
        self.lblStatus.setText(msg)

    def _on_include_legend_toggled(self, state):
        """Salva la preferenza di includere la legenda nell'export immagine."""
        try:
            QSettings().setValue("TheRaiseOfSlopes/includeLegend", bool(state))
        except Exception:
            pass

    def include_legend_in_export(self):
        """Restituisce True se l'utente ha abilitato l'inclusione della legenda nell'export."""
        return getattr(self, 'act_include_legend', None) is not None and self.act_include_legend.isChecked()

    def updateStabilityResults(self, results_text, analysis_type='grid'):
        """Aggiorna l'area dei risultati dell'analisi di stabilità.
        
        Args:
            results_text: testo dei risultati
            analysis_type: 'grid' o 'simplex' per selezionare quale area aggiornare
        """
        if analysis_type == 'grid' and hasattr(self, 'grid_results_text'):
            self.grid_results_text.setPlainText(results_text)
        elif analysis_type == 'simplex' and hasattr(self, 'simplex_results_text'):
            self.simplex_results_text.setPlainText(results_text)
            
    def setProfileDistances(self, distances):
        """Salva le distanze del profilo per uso nei calcoli dei parametri."""
        self.profile_distances = distances
    
    def _calculate_layer2_interface(self, x_distances):
        """Calcola le quote dell'interfaccia del secondo strato per la visualizzazione.
        
        Args:
            x_distances: lista di distanze lungo il profilo
            
        Returns:
            lista di quote o None se il calcolo fallisce
        """
        if not self._plugin:
            return None
        
        # Ottieni i parametri di stratigrafia
        params = self._get_stratigraphy_params()
        if not params.get('enable_layer2', False):
            return None
        
        # Usa il metodo del plugin per calcolare il profilo
        return self._plugin.compute_layer2_profile_for_display(params)
    
    def _calculate_water_table(self, x_distances):
        """Calcola le quote della falda per la visualizzazione.
        
        Args:
            x_distances: lista di distanze lungo il profilo
            
        Returns:
            lista di quote o None se il calcolo fallisce
        """
        if not self._plugin:
            return None
        
        # Ottieni i parametri di stratigrafia
        params = self._get_stratigraphy_params()
        if not params.get('enable_water', False):
            return None
        
        # Usa il metodo del plugin per calcolare il profilo
        return self._plugin.compute_water_profile_for_display(params)
    
    def _get_stratigraphy_params(self):
        """Raccoglie i parametri di stratigrafia e falda."""
        params = {}
        
        # Secondo strato
        params['enable_layer2'] = self.enable_layer2_checkbox.isChecked()
        if params['enable_layer2']:
            layer2_def_id = self.layer2_definition_group.checkedId()
            params['layer2_definition_mode'] = layer2_def_id  # 0=const_depth, 1=raster, 2=elevation
            
            if layer2_def_id == 0:  # Profondità costante
                params['layer2_const_depth'] = self.layer2_const_depth_spinbox.value()
            elif layer2_def_id == 1:  # Raster
                params['layer2_raster_layer'] = self.layer2_raster_combo.currentData()
            elif layer2_def_id == 2:  # Quota assoluta
                params['layer2_elevation'] = self.layer2_elevation_spinbox.value()
            
            params['gamma_2'] = self.gamma_2_spinbox.value()
            params['cohesion_2'] = self.cohesion_2_spinbox.value()
            params['porosity_2'] = self.porosity_2_spinbox.value()
            params['friction_angle_2'] = self.friction_angle_2_spinbox.value()
        
        # Falda
        params['enable_water'] = self.enable_water_checkbox.isChecked()
        if params['enable_water']:
            water_def_id = self.water_definition_group.checkedId()
            params['water_definition_mode'] = water_def_id  # 0=const_depth, 1=raster, 2=elevation
            
            if water_def_id == 0:  # Profondità costante
                params['water_const_depth'] = self.water_const_depth_spinbox.value()
            elif water_def_id == 1:  # Raster
                params['water_raster_layer'] = self.water_raster_combo.currentData()
            elif water_def_id == 2:  # Quota assoluta
                params['water_elevation'] = self.water_elevation_spinbox.value()
        
        return params
