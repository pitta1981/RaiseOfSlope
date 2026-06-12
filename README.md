# The Raise Of Slopes - Plugin QGIS

Plugin QGIS per analisi di stabilità dei versanti con metodi di equilibrio limite (Bishop, Morgenstern-Price, Spencer).

## Funzionalità

### Profilo altimetrico
- Selezione di due punti su DEM
- Campionamento profilo con gestione CRS e nodata
- Visualizzazione grafica e export CSV / immagine / DXF

### Analisi di stabilità
- Ricerca a griglia e ottimizzazione simplex
- Parametri geotecnici per 1 o 2 strati
- Falda opzionale (profondità costante, raster o quota assoluta)
- Salvataggio/caricamento progetto `.rslope`

## Architettura corrente

Il plugin usa esclusivamente il framework esterno:

`external/gwf-le/src/LEM`

e i moduli di ricerca delle superfici critiche in:

`external/gwf-le/src/searchCriticalF`

Non sono più presenti fallback o copie locali dei moduli LE.

## Installazione plugin (sviluppo)

1. Clona anche il submodule `external/gwf-le`.
2. Copia la cartella del plugin nella directory plugin QGIS o usa lo script di installazione fornito.

Esempio macOS, copia manuale:

```bash
cp -r /percorso/RaiseOfSlope ~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/TheRaiseOfSlopes
```

Oppure, dalla directory principale del plugin:

```bash
python install_plugin.py            # copia nella directory predefinita
python install_plugin.py --dest /path/to/plugins
python install_plugin.py --zip      # genera TheRaiseOfSlopes.zip
```
Poi abilita il plugin in QGIS da `Plugin > Gestisci e Installa Plugin`.

## Utilizzo rapido

1. Carica un DEM.
2. Apri il plugin e seleziona due punti.
3. Il profilo viene calcolato automaticamente.
4. Esegui analisi in `Grid Analysis` o `Simplex Analysis`.

## Dipendenze

- QGIS 3.x
- NumPy, SciPy, Matplotlib (ambiente QGIS)
- Moduli LE in `external/gwf-le/src/LEM` e `external/gwf-le/src/searchCriticalF`

## Troubleshooting

### Errore import moduli LE

Verifica che esista:

`external/gwf-le/src/LEM`

e che esista anche:

`external/gwf-le/src/searchCriticalF`

con i moduli usati dal plugin (`lemInterface.py`, `gleMethods.py`, `searchInterface.py`, `circularSlipSurfaces.py`).

### Analisi non parte

- Verifica che il profilo sia stato calcolato
- Controlla bounds e parametri geotecnici
- Leggi la console Python di QGIS per dettagli errore

## File utili

- `the_raise_of_slopes_plugin.py` logica plugin
- `ui/profile_dialog.py` interfaccia
- `debug_stability_standalone.py` debug fuori QGIS
- `INTEGRATION_NOTES.md` note tecniche
