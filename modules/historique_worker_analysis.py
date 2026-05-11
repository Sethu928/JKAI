}import os
from datetime import datetime

def get_historique_workers_analysis(nexus_data_dir):
    # Récupérer les fichiers de logs du worker 'analysis'
    log_files = [f for f in os.listdir(nexus_data_dir) if f.startswith('analysis_')]

    # Créer un dictionnaire pour stocker l'historique des tâches
    historique = {}

    # Parcourir les fichiers de logs et extraire les informations
    for log_file in log_files:
        with open(os.path.join(nexus_data_dir, log_file), 'r') as f:
            for ligne in f:
                if ligne.startswith('INFO: '):
                    date = datetime.strptime(ligne.split(' ')[1], '%Y-%m-%d %H:%M:%S')
                    historique[date] = {'status': 'success' if 'SUCCESS' in ligne else 'error'}

    return historique

# Exemple d'utilisation
if __name__ == '__main__':
    nexus_data_dir = '/chemin/vers/nexus/data'
    historique = get_historique_workers_analysis(nexus_data_dir)
    print(historique)