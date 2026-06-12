#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script di debug per l'analisi di stabilità fuori da QGIS
Legge un profilo esportato come CSV dal plugin e esegue l'analisi
"""

import sys
import os
import csv
import numpy as np
import scipy.optimize as optimize
from scipy import interpolate

# Aggiungi il path del plugin per importare i moduli
plugin_dir = os.path.dirname(os.path.abspath(__file__))
le_external_path = os.path.join(plugin_dir, 'external', 'gwf-le', 'src', 'limit-equilibrium')
if le_external_path not in sys.path:
    sys.path.insert(0, le_external_path)

try:
    # Import delle classi del framework limit-equilibrium
    from base_classes import SoilProperties, SoilState, UniformQuadrature, Options
    from circularSlipSurface import circularSlipSurface
    from bishop import bishop
    from gle import morgerstern_price, spencer
    from gridOfCircles import GridOptions, gridComputation, computeEtaMinForSurface
    print("✓ Moduli limit-equilibrium importati correttamente")
except ImportError as e:
    print(f"✗ Errore importazione moduli: {e}")
    print(f"Percorso external LE: {le_external_path}")
    print(f"Esiste external LE? {os.path.exists(le_external_path)}")
    if os.path.exists(le_external_path):
        print(f"File in external LE: {os.listdir(le_external_path)}")
    sys.exit(1)


def simplexComputation(method, ground_surface, bounding_box, soil_properties, soil_state, gridOptions, methodOptions, bounds):
    """Simplex optimizer compatible with current external/gwf-le API."""
    def objective(v):
        x_in, x_out, eta_deg = float(v[0]), float(v[1]), float(v[2])
        if (
            x_in < bounds[0][0] or x_in > bounds[0][1] or
            x_out < bounds[1][0] or x_out > bounds[1][1] or
            eta_deg < bounds[2][0] or eta_deg > bounds[2][1]
        ):
            return np.inf
        if x_in <= x_out:
            return np.inf
        eta = np.radians(eta_deg)
        if eta <= 0.0 or eta >= np.pi / 2:
            return np.inf
        try:
            y_in = float(np.asarray(ground_surface(x_in)).item())
            y_out = float(np.asarray(ground_surface(x_out)).item())
            if y_in <= y_out:
                return np.inf
            eta_min = computeEtaMinForSurface(ground_surface, bounding_box, x_in, x_out)
            if not np.isfinite(eta_min) or eta < eta_min + 1e-3:
                return np.inf
            geometry = circularSlipSurface.fromInOutAndEta(ground_surface, bounding_box, x_in, x_out, eta)
            fos = method(geometry, soil_properties, soil_state, methodOptions).factor_of_safety
            return float(fos) if np.isfinite(fos) else np.inf
        except Exception:
            return np.inf

    trial = None
    try:
        grid_results, _ = gridComputation(method, ground_surface, bounding_box, soil_properties, soil_state, gridOptions, methodOptions)
        candidates = []
        if isinstance(grid_results, list):
            for item in grid_results:
                if isinstance(item, list):
                    candidates.extend(item)
                else:
                    candidates.append(item)
        else:
            candidates = [grid_results]
        candidates = [r for r in candidates if r is not None]
        if candidates and hasattr(candidates[0], 'inputs') and candidates[0].inputs:
            start_geo = candidates[0].inputs[0]
            trial = [
                float(start_geo.landslide_interval[1]),
                float(start_geo.landslide_interval[0]),
                float(np.degrees(start_geo.eta)),
            ]
    except Exception:
        trial = None

    if trial is None:
        x_in0 = (bounds[0][0] + bounds[0][1]) / 2.0
        x_out0 = (bounds[1][0] + bounds[1][1]) / 2.0
        if x_in0 <= x_out0:
            x_out0 = bounds[1][0]
            x_in0 = min(bounds[0][1], max(bounds[0][0], x_out0 + (bounds[0][1] - bounds[0][0]) * 0.25))
        eta0 = (bounds[2][0] + bounds[2][1]) / 2.0
        trial = [float(x_in0), float(x_out0), float(eta0)]

    return optimize.minimize(
        objective,
        trial,
        method='Nelder-Mead',
        bounds=bounds,
        options={
            'disp': False,
            'xatol': methodOptions.tolerance,
            'fatol': methodOptions.tolerance,
            'maxiter': methodOptions.max_iteration,
            'return_all': True,
        },
    )


def load_profile_from_csv(csv_path):
    """Carica il profilo da un file CSV esportato dal plugin."""
    distances = []
    elevations = []

    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # Salta l'header
            for row in reader:
                if len(row) >= 2:
                    try:
                        dist = float(row[0])
                        elev = float(row[1])
                        distances.append(dist)
                        elevations.append(elev)
                    except ValueError:
                        print(f"Warning: Riga non valida nel CSV: {row}")
    except FileNotFoundError:
        print(f"Errore: File CSV non trovato: {csv_path}")
        return None, None
    except Exception as e:
        print(f"Errore nella lettura del CSV: {e}")
        return None, None

    if len(distances) < 2:
        print("Errore: Il profilo deve contenere almeno 2 punti validi")
        return None, None

    print(f"✓ Profilo caricato: {len(distances)} punti")
    print(".1f")
    print(".1f")
    return distances, elevations


def create_ground_surface_function(distances, elevations):
    """Crea una funzione lineare a tratti per la superficie del terreno."""
    valid_points = [(d, e) for d, e in zip(distances, elevations) if e is not None]
    if len(valid_points) < 2:
        raise ValueError("Dati insufficienti per creare la funzione del terreno")

    valid_distances, valid_elevations = zip(*valid_points)

    # Interpolatore lineare
    interpolator = interpolate.interp1d(
        valid_distances,
        valid_elevations,
        kind='linear',
        fill_value='extrapolate'
    )

    def ground_surface(x):
        return interpolator(x)

    return ground_surface


def run_stability_analysis(csv_path, params, method='grid'):
    """
    Esegue l'analisi di stabilità usando i parametri forniti.
    
    Args:
        csv_path: Percorso del file CSV con il profilo
        params: Dizionario con i parametri del terreno
        method: 'grid' per gridComputation, 'simplex' per simplexComputation
    """

    print("=" * 60)
    method_name = "GRID COMPUTATION" if method == 'grid' else "SIMPLEX COMPUTATION"
    print(f"ANALISI DI STABILITÀ - {method_name}")
    print("=" * 60)

    # 1. Carica il profilo dal CSV
    print(f"Caricamento profilo da: {csv_path}")
    distances, elevations = load_profile_from_csv(csv_path)
    if distances is None:
        return None

    # 2. Crea la funzione ground_surface
    ground_surface = create_ground_surface_function(distances, elevations)

    # 3. Calcola il bounding box
    valid_elevations = [e for e in elevations if e is not None]
    x_min = distances[0]
    x_max = distances[-1]
    y_min = min(valid_elevations)
    y_max = max(valid_elevations)

    # Espande il bounding box per la superficie di scivolamento
    y_min_extended = y_min - (y_max - y_min) * params['depth_factor']
    bounding_box = np.array([[x_min, x_max], [y_min_extended, y_max * 1.1]])

    print(f"Bounding box: x=[{x_min:.1f}, {x_max:.1f}], y=[{y_min_extended:.1f}, {y_max*1.1:.1f}]")

    # 4. Opzioni griglia
    grid_options = GridOptions(
        in_interval=[x_max * 0.7, x_max],
        out_interval=[x_min, x_min + (x_max - x_min) * 0.3],
        in_pts=None,
        out_pts=None,
        min_eta_inc=np.radians(5),
        num_in_pts=20,
        num_out_pts=20
    )

    # 5. Parametri del terreno
    constant_dry_density = params['gamma']
    soil_properties = SoilProperties(
        cohesion=lambda x, y: params['cohesion'] + params.get('cohesion_depth_rate', 0.0) * (ground_surface(x) - y),
        friction_angle=lambda x, y: params['friction_angle'] * np.ones_like(x + y),
        dry_density=lambda x, y: constant_dry_density * np.ones_like(x + y),
        porosity=lambda x, y: 0.0 * np.ones_like(x + y),
        grain_density=lambda x, y: 0.0 * np.ones_like(x + y)
    )

    # 6. Stato del terreno
    soil_state = SoilState(
        saturation=lambda x, y: 1.0 * np.ones_like(x + y),
        pore_pressure=lambda x, y: 0.0 * np.ones_like(x + y),
        integrated_density=lambda x, y: constant_dry_density * (ground_surface(x) - y)
    )

    # 7. Opzioni del metodo
    method_options = Options(
        max_iteration=200,
        tolerance=1e-4,
        quadrature=lambda interval: UniformQuadrature(x_interval=interval, num=50)
    )

    print(f"Parametri terreno: γ={params['gamma']:.1f} kN/m³, c={params['cohesion']:.1f} kPa, φ={params['friction_angle']:.1f}°")
    print(f"Aumento coesione con profondità: {params.get('cohesion_depth_rate', 0.0):.3f} kPa/m")

    # 8. Esegui calcolo
    method_text = "griglia di cerchi" if method == 'grid' else "ottimizzazione simplex"
    print(f"Calcolo in corso... ({method_text})")

    try:
        mOptions = Options(
            max_iteration=200,
            tolerance=1e-4,
            quadrature=lambda interval: UniformQuadrature(
                x_interval=interval,
                num=50
            )
        )
        bounds = ((0.5*x_max, x_max),
                  (x_min, 0.5*x_max),
                  (0., 90))

        if method == 'grid':
            print("Chiamata a gridComputation...")
            results = gridComputation(morgerstern_price, ground_surface, bounding_box, soil_properties, soil_state, grid_options, mOptions)
        elif method == 'simplex':
            print("Chiamata a simplexComputation...")
            results = simplexComputation(morgerstern_price, ground_surface, bounding_box, soil_properties, soil_state, grid_options, mOptions, bounds)
        else:
            raise ValueError(f"Metodo non supportato: {method}")

        print("✓ Calcolo completato!")

        # Debug: analizza la struttura dei risultati
        if method == 'simplex':
            first_result = results
            print(f"\nDEBUG - Tipo risultato simplex: {type(first_result)}")
        else:
            if not results:
                print("✗ Nessun risultato ottenuto")
                return None
            print(f"✓ Calcolo completato! Numero risultati: {len(results)}")
            first_result = results[0]
            print(f"\nDEBUG - Tipo primo risultato: {type(first_result)}")
        
        # Gestione diversa per grid e simplex
        if method == 'simplex':
            # simplexComputation restituisce scipy.optimize.OptimizeResult
            print("Gestione risultati simplexComputation...")
            if hasattr(first_result, 'fun'):
               
                fs = first_result.fun
                print(f"🎯 FATTORE DI SICUREZZA (FS): {fs:.4f}")
                
                # Interpretazione del risultato
                if fs < 1.0:
                    print("⚠️  PENDIO INSTABILE (FS < 1)")
                elif fs < 1.5:
                    print("⚠️  PENDIO A RISCHIO (FS < 1.5)")
                else:
                    print("✅ PENDIO STABILE (FS ≥ 1.5)")
                
                # Informazioni sull'ottimizzazione
                print(f"\n📊 DETTAGLI OTTIMIZZAZIONE:")
                print(f"   Successo: {'✓' if first_result.success else '✗'}")
                print(f"   Messaggio: {first_result.message}")
                print(f"   Iterazioni: {first_result.nit}")
                
                if hasattr(first_result, 'x'):
                    print(f"   Parametri ottimali: {first_result.x}")
                    print(f"   x_in: {first_result.x[0]:.2f} m")
                    print(f"   x_out: {first_result.x[1]:.2f} m") 
                    print(f"   eta: {first_result.x[2]:.2f}°")
                
                print("=" * 60)
                return [{'factor_of_safety': fs, 'optimize_result': first_result}]
            else:
                print("❌ Risultato simplex non valido")
                return None
        else:
            # gridComputation restituisce liste di oggetti Result
            if isinstance(first_result, list):
                print(f"Primo risultato è una lista con {len(first_result)} elementi")
                if len(first_result) > 0:
                    print(f"Tipo primo elemento della lista: {type(first_result[0])}")
                    critical_result = first_result[0]
                else:
                    print("❌ Lista risultati vuota")
                    return None
            else:
                critical_result = first_result
                
            print(f"Tipo critical_result finale: {type(critical_result)}")

            print("\nRISULTATO CRITICO:")
            
            # Stampa attributi principali se presenti
            print("\n--- Proprietà dell'oggetto critical_result ---")
            if hasattr(critical_result, '__dict__'):
                for attr, value in vars(critical_result).items():
                    if attr in ["factor_of_safety", "method", "Lambda"]:
                        print(f"{attr}: {value}")
                    elif attr in ["nodes", "depths", "weight_forces", "resisting_forces"]:
                        if hasattr(value, 'shape'):
                            print(f"{attr}: array shape {value.shape}")
                        else:
                            print(f"{attr}: {type(value)}")
            elif hasattr(critical_result, '__len__') and not isinstance(critical_result, str):
                print(f"critical_result è una sequenza con {len(critical_result)} elementi")
                for i, item in enumerate(critical_result):
                    print(f"  Elemento {i}: {type(item)} - {item}")
            else:
                print(f"critical_result: {critical_result}")
            
            # Stampa il fattore di sicurezza se disponibile
            if hasattr(critical_result, "factor_of_safety"):
                print(f"\n🎯 FATTORE DI SICUREZZA (FS): {critical_result.factor_of_safety:.4f}")
                
                # Interpretazione del risultato
                if critical_result.factor_of_safety < 1.0:
                    print("⚠️  PENDIO INSTABILE (FS < 1)")
                elif critical_result.factor_of_safety < 1.5:
                    print("⚠️  PENDIO A RISCHIO (FS < 1.5)")
                else:
                    print("✅ PENDIO STABILE (FS ≥ 1.5)")
            else:
                print("❌ Fattore di sicurezza non trovato nel risultato")
                # Proviamo a estrarre il FS se è una sequenza numerica
                if isinstance(critical_result, (list, tuple)) and len(critical_result) > 0:
                    if isinstance(critical_result[0], (int, float)):
                        fs = critical_result[0]
                        print(f"🎯 FATTORE DI SICUREZZA (da sequenza): {fs:.4f}")
                        if fs < 1.0:
                            print("⚠️  PENDIO INSTABILE (FS < 1)")
                        elif fs < 1.5:
                            print("⚠️  PENDIO A RISCHIO (FS < 1.5)")
                        else:
                            print("✅ PENDIO STABILE (FS ≥ 1.5)")
                    else:
                        return None
                else:
                    return None
            # Informazioni dettagliate sulla superficie critica (solo per grid)
            try:
                if hasattr(critical_result, 'inputs') and critical_result.inputs:
                    geometry = critical_result.inputs[0]
                    print(f"\n📍 GEOMETRIA SUPERFICIE CRITICA:")
                    
                    if hasattr(geometry, 'landslide_interval'):
                        x_out = geometry.landslide_interval[0]
                        x_in = geometry.landslide_interval[1]
                        print(f"   Punto ingresso (x_in): {x_in:.2f} m")
                        print(f"   Punto uscita (x_out): {x_out:.2f} m")
                        print(f"   Lunghezza superficie: {x_in - x_out:.2f} m")
                    
                    if hasattr(critical_result, 'nodes') and critical_result.nodes is not None:
                        if hasattr(critical_result.nodes, 'shape') and len(critical_result.nodes.shape) > 1:
                            num_slices = critical_result.nodes.shape[1]
                            print(f"   Numero di conci: {num_slices}")
                        elif hasattr(critical_result.nodes, '__len__'):
                            num_slices = len(critical_result.nodes[0]) if len(critical_result.nodes) > 0 else 0
                            print(f"   Numero di conci: {num_slices}")
                else:
                    print("⚠️  Informazioni geometriche non disponibili")
            except Exception as e:
                print(f"⚠️  Errore nell'estrazione delle informazioni geometriche: {e}")

            # Mostra statistiche sui risultati (solo per grid)
            print(f"\n📊 STATISTICHE RISULTATI:")
            
            # Appiattisci la lista dei risultati se necessario
            all_results = []
            for result_group in results:
                if isinstance(result_group, list):
                    all_results.extend(result_group)
                else:
                    all_results.append(result_group)
            
            print(f"   Numero totale di superfici analizzate: {len(all_results)}")
            
            # Ordina i risultati per fattore di sicurezza
            valid_results = []
            for result in all_results:
                if hasattr(result, 'factor_of_safety') and result.factor_of_safety is not None:
                    valid_results.append(result)
            
            if valid_results:
                sorted_results = sorted(valid_results, key=lambda r: r.factor_of_safety)
                fs_values = [r.factor_of_safety for r in sorted_results]
                
                print(f"   FS minimo: {min(fs_values):.4f}")
                print(f"   FS massimo: {max(fs_values):.4f}")
                print(f"   FS medio: {np.mean(fs_values):.4f}")
                
                # Mostra i primi 5 risultati
                print(f"\n🏆 TOP 5 RISULTATI CRITICI (FS crescente):")
                for i, result in enumerate(sorted_results[:5], 1):
                    fs = result.factor_of_safety
                    status = "❌ INSTABILE" if fs < 1.0 else "⚠️ CRITICO" if fs < 1.5 else "✅ STABILE"
                    
                    try:
                        if hasattr(result, 'inputs') and result.inputs:
                            geom = result.inputs[0]
                            if hasattr(geom, 'landslide_interval'):
                                x_out_r = geom.landslide_interval[0]
                                x_in_r = geom.landslide_interval[1]
                                print(f"   {i}. FS={fs:.4f} {status} - x_in={x_in_r:.2f}m, x_out={x_out_r:.2f}m")
                            else:
                                print(f"   {i}. FS={fs:.4f} {status}")
                        else:
                            print(f"   {i}. FS={fs:.4f} {status}")
                    except:
                        print(f"   {i}. FS={fs:.4f} {status}")
            else:
                print("❌ Nessun risultato valido trovato")

            print("=" * 60)
            return results

    except Exception as e:
        print(f"✗ Errore nell'analisi: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    """Funzione principale dello script di debug."""

    # Parametri di default (puoi modificarli)
    default_params = {
        'gamma': 18.0,          # kN/m³ - peso specifico del terreno
        'cohesion': 10.0,       # kPa - coesione
        'friction_angle': 30.0, # gradi - angolo di attrito
        'cohesion_depth_rate': 0.0,  # kPa/m - aumento coesione con profondità
        'depth_factor': 0.5     # fattore per estendere il bounding box in profondità
    }

    # Percorso del file CSV (modifica questo percorso!)
    csv_path = "profilo_esempio.csv"  # File di esempio incluso

    # Verifica se il file CSV esiste
    if not os.path.exists(csv_path):
        print(f"File CSV non trovato: {csv_path}")
        print("\nIstruzioni:")
        print("1. Apri QGIS con il plugin TheRaiseOfSlopes")
        print("2. Seleziona due punti sul DEM per creare un profilo")
        print("3. Calcola il profilo")
        print("4. Esporta il profilo come CSV")
        print("5. Modifica la variabile 'csv_path' in questo script con il percorso del file esportato")
        print("6. Esegui nuovamente lo script")
        return

    print(f"Usando file CSV: {csv_path}")
    print(f"Parametri: {default_params}")

    # Scegli il metodo di calcolo
    print("\n🔄 CONFRONTO TRA METODI DI CALCOLO")
    print("=" * 60)
    
    # Test con Grid Computation
    print("1️⃣  ESEGUENDO GRID COMPUTATION...")
    results_grid = run_stability_analysis(csv_path, default_params, method='grid')
    
    print("\n" + "="*60)
    
    # Test con Simplex Computation  
    print("2️⃣  ESEGUENDO SIMPLEX COMPUTATION...")
    results_simplex = run_stability_analysis(csv_path, default_params, method='simplex')
    
    # Confronto risultati
    print("\n" + "="*60)
    print("📊 CONFRONTO RISULTATI")
    print("="*60)
    
    if results_grid and results_simplex:
        # Estrai FS da grid
        if isinstance(results_grid[0], list) and len(results_grid[0]) > 0:
            fs_grid = results_grid[0][0].factor_of_safety
        else:
            fs_grid = "N/A"
            
        # Estrai FS da simplex  
        if isinstance(results_simplex[0], dict):
            fs_simplex = results_simplex[0]['factor_of_safety']
        else:
            fs_simplex = "N/A"
            
        print(f"Grid Computation FS:    {fs_grid}")
        print(f"Simplex Computation FS: {fs_simplex}")
        
        if fs_grid != "N/A" and fs_simplex != "N/A":
            diff = abs(fs_grid - fs_simplex)
            diff_perc = (diff / fs_grid) * 100
            print(f"Differenza assoluta:    {diff:.4f}")
            print(f"Differenza percentuale: {diff_perc:.2f}%")
            
            if diff_perc < 1:
                print("✅ Risultati molto concordi (< 1% diff)")
            elif diff_perc < 5:
                print("⚠️  Risultati accettabili (< 5% diff)")
            else:
                print("❌ Risultati molto diversi (≥ 5% diff)")
    
    print("="*60)
    if results_grid or results_simplex:
        print("✓ Analisi completata con successo!")
    else:
        print("✗ Entrambe le analisi sono fallite")


if __name__ == "__main__":
    main()