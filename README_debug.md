# Script di Debug per Analisi di Stabilità

Questo script permette di eseguire l'analisi di stabilità dei pendii al di fuori dell'ambiente QGIS, utilizzando un profilo esportato come file CSV.

## File Inclusi

- `debug_stability_standalone.py` - Script principale per l'analisi
- `profilo_esempio.csv` - File CSV di esempio con un profilo semplice

## Come Usare

### 1. Preparazione del Profilo

**Opzione A: Usa il file di esempio**
```bash
cd /Users/pitta1981/Develop/RaiseOfSlope
python3 debug_stability_standalone.py
```

**Opzione B: Usa un profilo esportato da QGIS**
1. Apri QGIS con il plugin TheRaiseOfSlopes
2. Seleziona due punti sul DEM per creare un profilo
3. Calcola il profilo
4. Esporta il profilo come CSV (menu "Esporta profilo")
5. Modifica la variabile `csv_path` nello script per puntare al tuo file CSV

### 2. Modifica dei Parametri (Opzionale)

Modifica il dizionario `default_params` nello script per cambiare:
- `gamma`: Peso specifico del terreno (kN/m³)
- `cohesion`: Coesione (kPa)
- `friction_angle`: Angolo di attrito (gradi)
- `cohesion_depth_rate`: Aumento coesione con profondità (kPa/m)
- `depth_factor`: Fattore di estensione del bounding box

### 3. Esecuzione

```bash
cd /Users/pitta1981/Develop/RaiseOfSlope
python3 debug_stability_standalone.py
```

## Output

Lo script produce:
- ✓ Messaggi di conferma per ogni passaggio
- ✗ Messaggi di errore se qualcosa va storto
- Risultati dell'analisi di stabilità
- Fattore di sicurezza critico
- Geometria della superficie di scivolamento critica
- Primi 5 risultati ordinati per FS crescente

## Debug

Se riscontri l'errore `ValueError: not enough values to unpack (expected 12, got 11)`, lo script mostrerà messaggi di debug dettagliati per identificare quale valore manca nella tupla restituita da `slices_data`.

## Requisiti

- Python 3.x
- Moduli: numpy, scipy
- Framework LE in `external/gwf-le/src/LEM` e `external/gwf-le/src/searchCriticalF`