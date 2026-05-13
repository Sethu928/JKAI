import logging

# Configurer les logs pour faciliter les débogages
logging.basicConfig(level=logging.INFO)

class ContextController:
    def __init__(self, max_context_size):
        self.max_context_size = max_context_size

    def get_context(self, context_size):
        if context_size > self.max_context_size:
            logging.warning(f'Context size has been exceeded. Reducing to {self.max_context_size}.
')
            return context_size[:self.max_context_size]
        else:
            return context_size

    def set_max_context_size(self, new_max_context_size):
        self.max_context_size = new_max_context_size

# Exemple d'utilisation du module
if __name__ == '__main__':
    # Créer un instance de la classe ContextController
    controller = ContextController(max_context_size=512)

    # Récupérer une liste de contextes avec une taille supérieure à la limite autorisée
    context_list = ['Contexte 1', 'Contexte 2', 'Contexte 3', 'Contexte 4', 'Contexte 5']

    # Contrôler les contextes et réduire ceux qui dépassent la taille maximale
    controlled_contexts = [controller.get_context(context) for context in context_list]

    logging.info(controlled_contexts)
