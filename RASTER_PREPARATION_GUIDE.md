# Guida: Preparazione Raster per Stratigrafia e Falda

## Panoramica

Il plugin supporta l'uso di raster per definire:
1. **Profondità dell'interfaccia del secondo strato** dal piano campagna
2. **Profondità della falda freatica** dal piano campagna

Questa guida spiega come preparare questi raster in QGIS.

---

## Concetti Chiave

### Profondità vs Quota

Il plugin distingue tra due rappresentazioni:

- **PROFONDITÀ** (metri dal piano campagna, verso il basso)
  - Esempio: falda a 2 m di profondità = 2 m sotto la superficie
  - I raster devono contenere **valori positivi** che rappresentano la profondità
  - Modalità: "Profondità da raster"

- **QUOTA ASSOLUTA** (metri s.l.m. o altro datum)
  - Esempio: falda a quota 100 m s.l.m.
  - Modalità: "Quota assoluta" (costante) o conversione da raster

---

## Caso 1: Raster di Profondità dell'Interfaccia Strato 2

### Scenario Tipico
Hai eseguito indagini geognostiche e vuoi rappresentare la profondità del substrato roccioso o di uno strato più competente.

### Preparazione del Raster

#### Opzione A: Da Punti di Sondaggio

1. **Crea un layer di punti** con i risultati delle indagini:
   ```
   X, Y, Profondita_Substrato
   100, 200, 5.2
   150, 250, 6.8
   200, 200, 4.5
   ...
   ```

2. **Importa in QGIS** come CSV:
   - Layer → Add Layer → Add Delimited Text Layer
   - Selezione il file CSV
   - X field: X, Y field: Y
   - Geometry CRS: quello del progetto

3. **Interpola i punti** per creare un raster:
   - Processing Toolbox → Interpolation → IDW/TIN/Kriging
   - Input: layer punti
   - Attribute: Profondita_Substrato
   - Pixel size: appropriata (es. 1 m)
   - Output: `profondita_strato2.tif`

4. **Verifica il risultato**:
   - Valori positivi (profondità)
   - Unità: metri
   - CRS corretto

#### Opzione B: Da Raster di Quote Assolute

Se hai un raster con le **quote** dell'interfaccia (es. da modello geologico 3D):

1. **Calcola la profondità** usando la Raster Calculator:
   ```
   Raster → Raster Calculator

   Espressione:
   "DEM@1" - "quota_interfaccia@1"
   
   Output: profondita_strato2.tif
   ```

   Dove:
   - `DEM@1` è il Digital Elevation Model del terreno
   - `quota_interfaccia@1` è il raster con le quote dell'interfaccia
   - Il risultato è la profondità (sempre positiva se l'interfaccia è sotto il terreno)

2. **Verifica** che i valori siano positivi e ragionevoli

---

## Caso 2: Raster di Profondità della Falda

### Scenario Tipico
Hai misure piezometriche e vuoi rappresentare la profondità della falda dal piano campagna.

### Preparazione del Raster

#### Opzione A: Da Misure Piezometriche

1. **Crea layer di punti** con le misure:
   ```
   X, Y, Profondita_Falda
   100, 200, 2.3
   150, 250, 3.1
   200, 200, 1.8
   ...
   ```

2. **Interpola** come per lo strato 2:
   - Processing → Interpolation → IDW (più adatto per falda)
   - Attribute: Profondita_Falda
   - Output: `profondita_falda.tif`

#### Opzione B: Da Modello Idrogeologico

Se hai un modello con le **quote piezometriche**:

1. **Calcola profondità dalla superficie**:
   ```
   Raster Calculator:
   "DEM@1" - "quota_piezometrica@1"
   ```

2. **Gestisci valori negativi** (falda sopra terreno - caso raro):
   ```
   ("DEM@1" - "quota_piezometrica@1") * ("DEM@1" > "quota_piezometrica@1")
   ```
   Questo azzera i valori dove la falda emerge

---

## Caso 3: Da Litologia o Classi

### Scenario
Hai una carta litologica e vuoi assegnare profondità tipiche a ciascuna litologia.

### Procedura

1. **Rasterizza la carta litologica** (se vettoriale)

2. **Riclassifica i valori**:
   ```
   Raster → Raster Calculator
   
   Esempio:
   ("litologia@1" = 1) * 3.0 +     # Argilla: 3 m
   ("litologia@1" = 2) * 5.0 +     # Sabbia: 5 m
   ("litologia@1" = 3) * 8.0       # Ghiaia: 8 m
   ```

3. **Salva** come `profondita_per_litologia.tif`

---

## Caso 4: Raster Esistenti con Quote Assolute

### Se hai già raster con quote assolute

Due opzioni:

#### A. Converte in Profondità (consigliato)
Usa Raster Calculator come mostrato sopra

#### B. Usa Modalità "Quota Assoluta"
Se la quota è costante, usa direttamente l'opzione "Quota assoluta" nell'interfaccia

---

## Best Practices

### Risoluzione Spaziale
- **Match con il DEM**: Usa risoluzione simile o leggermente più grossolana
- Tipico: 1-5 m per pendii, 10-25 m per bacini estesi

### Estensione
- Assicurati che il raster copra **tutta l'area del profilo**
- Margini extra per evitare problemi ai bordi

### Valori NoData
- **Gestiti automaticamente** dal plugin con valori di default
- Default: 5 m per strato 2, 2 m per falda
- Se possibile, riempi le zone nodata con valori ragionevoli

### Unità di Misura
- **Sempre in METRI**
- Verifica che DEM e raster di profondità usino le stesse unità

### Sistema di Riferimento (CRS)
- **Non necessariamente identico al DEM** (la trasformazione è automatica)
- Assicurati che il CRS sia **correttamente definito**
- Evita CRS "sconosciuti" o personalizzati

---

## Verifica dei Raster

Prima di usarli nel plugin:

### 1. Controllo Visivo
```
1. Carica DEM e raster di profondità in QGIS
2. Usa "Identify Features" per controllare alcuni valori
3. Verifica che i valori siano ragionevoli (es. profondità 0-20 m)
```

### 2. Statistiche
```
1. Layer Properties → Information
2. Controlla min/max/mean
3. Verifica che non ci siano valori anomali (es. negativi, troppo grandi)
```

### 3. Profilo di Test
```
1. Plugin → Profile tool (o simile)
2. Traccia una linea
3. Visualizza i valori del raster lungo il profilo
4. Verifica la continuità e ragionevolezza
```

---

## Esempi Pratici

### Esempio 1: Substrato Roccioso a Profondità Variabile

```python
# Script Python in QGIS Console per generare un raster di test

from qgis.core import *
import processing
import numpy as np

# Parametri
extent = iface.mapCanvas().extent()
pixel_size = 5.0

# Calcola dimensioni griglia
width = int((extent.xMaximum() - extent.xMinimum()) / pixel_size)
height = int((extent.yMaximum() - extent.yMinimum()) / pixel_size)

# Genera profondità variabile (esempio: gradiente NE-SW)
x = np.linspace(0, 1, width)
y = np.linspace(0, 1, height)
X, Y = np.meshgrid(x, y)
depth = 3.0 + 5.0 * (X + Y) / 2  # Da 3 a 8 m

# Salva come raster (necessita ulteriore processing)
# ... codice per salvare l'array come GeoTIFF
```

### Esempio 2: Falda con Gradiente Idraulico

```python
# Falda con gradiente da monte (profonda) a valle (superficiale)
depth_falda = 5.0 - 3.0 * Y  # Da 5 m a 2 m
```

---

## Risoluzione Problemi

### Problema: "Tutti valori di default"
**Causa**: Raster non si sovrappone al profilo, o CRS errato
**Soluzione**: Verifica estensione e CRS del raster

### Problema: "Valori irrealistici"
**Causa**: Unità di misura errate o conversione sbagliata
**Soluzione**: Controlla che i valori siano in metri e positivi

### Problema: "Errore di trasformazione CRS"
**Causa**: CRS del raster non definito correttamente
**Soluzione**: Layer Properties → Source → CRS, imposta il CRS corretto

### Problema: "Superficie critica strana"
**Causa**: Profondità troppo piccole o grandi, o inversione resistenze
**Soluzione**: Verifica parametri geotecnici e profondità degli strati

---

## Risorse Utili

### Tool QGIS per Interpolazione
- **IDW** (Inverse Distance Weighted): Veloce, semplice, buono per dati densi
- **TIN** (Triangulated Irregular Network): Preciso con punti irregolari
- **Kriging**: Più accurato ma più lento, richiede analisi variogramma

### Plugin Utili
- **Profile Tool**: Per visualizzare profili dei raster
- **Qgis2threejs**: Per visualizzazione 3D di DEM e strati
- **Point Sampling Tool**: Per estrarre valori raster in punti

---

## Template CSV per Indagini

```csv
X,Y,Profondita_Substrato,Profondita_Falda,Note
450123.5,5012345.2,5.2,2.1,Sondaggio S01
450234.1,5012456.8,6.8,3.5,Sondaggio S02
450345.7,5012234.3,4.5,1.8,Sondaggio S03
```

Salva come `indagini_geotecniche.csv` e importa in QGIS per interpolazione.
