#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script di debug per l'analisi di stabilità - singolo metodo
Versione semplificata per testare un metodo alla volta
"""

import sys
import os

# Aggiungi il path del plugin per importare i moduli
plugin_dir = os.path.dirname(os.path.abspath(__file__))
le_external_path = os.path.join(plugin_dir, 'external', 'gwf-le', 'src', 'limit-equilibrium')
if le_external_path not in sys.path:
    sys.path.insert(0, le_external_path)

# Import del modulo principale
from debug_stability_standalone import run_stability_analysis

def main():
    """Funzione principale per testare un singolo metodo."""
    
    # Parametri di default
    params = {
        'gamma': 18.0,          # kN/m³ - peso specifico del terreno
        'cohesion': 10.0,       # kPa - coesione
        'friction_angle': 25.0, # gradi - angolo di attrito
        'cohesion_depth_rate': 0.0,  # kPa/m - aumento coesione con profondità
        'depth_factor': 0.5     # fattore per estendere il bounding box in profondità
    }

    csv_path = "profilo_esempio.csv"
    
    if not os.path.exists(csv_path):
        print(f"File CSV non trovato: {csv_path}")
        return

    # Chiedi all'utente quale metodo usare
    print("Scegli il metodo di calcolo:")
    print("1. Grid Computation (ricerca su griglia)")
    print("2. Simplex Computation (ottimizzazione)")
    
    while True:
        choice = input("Inserisci 1 o 2: ").strip()
        if choice in ['1', '2']:
            break
        print("Scelta non valida. Inserisci 1 o 2.")
    
    method = 'grid' if choice == '1' else 'simplex'
    
    print(f"\nEseguendo analisi con {method}...")
    print(f"Parametri: {params}")
    
    # Esegui l'analisi
    results = run_stability_analysis(csv_path, params, method=method)
    
    if results:
        print("\n✓ Analisi completata con successo!")
    else:
        print("\n✗ Analisi fallita")

if __name__ == "__main__":
    main()