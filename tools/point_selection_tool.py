# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QCursor
from qgis.PyQt.QtWidgets import QApplication
from qgis.gui import QgsMapTool
from qgis.core import QgsPointXY


class TwoPointSelectionTool(QgsMapTool):
    """Map tool semplice per catturare esattamente due clic e restituire i punti.

    Emette segnali:
    - firstPointSelected(p1) quando viene selezionato il primo punto
    - pointsSelected(p1, p2) quando il secondo punto viene acquisito
    Si resetta automaticamente per eventuale nuova selezione.
    """
    firstPointSelected = pyqtSignal(object)
    pointsSelected = pyqtSignal(object, object)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas
        self._points = []

    def canvasReleaseEvent(self, event):
        """Gestione del rilascio mouse: aggiunge il punto e emette segnali progressivi."""
        p = self.toMapCoordinates(event.pos())
        self._points.append(QgsPointXY(p))
        
        if len(self._points) == 1:
            # Primo punto selezionato
            self.firstPointSelected.emit(self._points[0])
        elif len(self._points) == 2:
            # Secondo punto selezionato, selezione completa
            self.pointsSelected.emit(self._points[0], self._points[1])
            self._points = []

    def activate(self):
        """Override cursore (placeholder: si potrebbe mostrare un cursore custom)."""
        QApplication.setOverrideCursor(QCursor())

    def deactivate(self):
        """Ripristina cursore di default."""
        QApplication.restoreOverrideCursor()
