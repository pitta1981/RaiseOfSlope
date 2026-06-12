# Integrazione Framework LE nel Plugin QGIS

## Stato attuale

Il plugin usa esclusivamente i moduli del submodule `external/gwf-le`:

- `external/gwf-le/src/LEM`
- `external/gwf-le/src/searchCriticalF`

Sono stati rimossi i fallback e non vengono usate copie locali legacy dei moduli LE.

## Architettura operativa

### 1. Import e bootstrap

Nel file principale del plugin vengono aggiunti a `sys.path` i percorsi:

- `external/gwf-le`
- `external/gwf-le/src`
- `external/gwf-le/src/LEM`
- `external/gwf-le/src/searchCriticalF`

Import usati:

- da `LEM`: `Soil`, `lemOptions`, `uniform_subdivision`, `bishop`, `morgerstern_price`, `spencer`
- da `searchCriticalF`: `circularSlipSearchDomain`, `lemMethod`, `find_critical`, `simplex`

### 2. Modello geotecnico

Il plugin costruisce il terreno tramite `Soil` (nuova API), definendo funzioni per:

- `cohesion`
- `vertical_cohesion`
- `friction_angle`
- `vertical_friction_angle`
- `pore_pressure`
- `saturation`
- `column_weight`

Sono supportati:

- singolo strato
- doppio strato con interfaccia costante/raster/quota assoluta
- falda opzionale costante/raster/quota assoluta

### 3. Analisi Grid

Flusso:

1. Costruzione `ground_surface` dal profilo campionato
2. Creazione dominio con `circularSlipSearchDomain`
3. Campionamento geometrie con `sample_grid`
4. Valutazione con `find_critical`
5. Selezione della geometria critica e aggiornamento output/UI

### 4. Analisi Simplex

Flusso:

1. Seed iniziale da griglia (`sample_grid` + `find_critical`)
2. Ottimizzazione con `simplex` (API ufficiale)
3. Estrazione geometria critica e aggiornamento output/UI

### 5. Output e visualizzazione

Il plugin mantiene:

- visualizzazione del profilo
- sovrapposizione superfici critiche
- export CSV/TXT/immagine/DXF
- salvataggio/caricamento progetto `.rslope`

## Requisiti

- QGIS 3.x
- NumPy, SciPy, Matplotlib
- submodule `external/gwf-le` presente e aggiornato

## Nota manutenzione

Per aggiornare la libreria LE, aggiornare il submodule `external/gwf-le`; non duplicare moduli nel plugin.
