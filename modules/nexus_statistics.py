import json
import sqlite3
from sqlite3 import Error

class NexusStatistics:
    def __init__(self):
        self.database = sqlite3.connect('memory/responses.db')
        c = self.database.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS responses (
                    id INTEGER PRIMARY KEY,
                    message TEXT,
                    response TEXT
                )""")
        self.database.commit()

    def get_responses(self, message=None):
        if message:
            c = self.database.cursor()
            c.execute(f"SELECT * FROM responses WHERE message LIKE ?", ('%' + message + '%',))
            return c.fetchall()
        else:
            c = self.database.cursor()
            c.execute("SELECT * FROM responses")
            return c.fetchall()

    def calculate_accuracy(self, predicted_responses, actual_responses):
        correct_count = 0
        for i in range(len(predicted_responses)):
            if predicted_responses[i] == actual_responses[i]:
                correct_count += 1
        accuracy = (correct_count / len(predicted_responses)) * 100
        return accuracy

    def calculate_precision(self, predicted_responses, actual_responses):
        true_positives = 0
        false_positives = 0
        for i in range(len(predicted_responses)):
            if predicted_responses[i] == actual_responses[i]:
                true_positives += 1
            else:
                false_positives += 1
        precision = (true_positives / (true_positives + false_positives)) * 100
        return precision

    def calculate_recall(self, predicted_responses, actual_responses):
        true_positives = 0
        false_negatives = 0
        for i in range(len(predicted_responses)):
            if predicted_responses[i] == actual_responses[i]:
                true_positives += 1
            else:
                false_negatives += 1
        recall = (true_positives / (true_positives + false_negatives)) * 100
        return recall

    def calculate_f1_score(self, predicted_responses, actual_responses):
        precision = self.calculate_precision(predicted_responses, actual_responses)
        recall = self.calculate_recall(predicted_responses, actual_responses)
        f1 = ((2 * precision * recall) / (precision + recall)) * 100
        return f1

    def analyze_performance(self, message=None):
        responses = self.get_responses(message)
        if not responses:
            print("Aucune réponse trouvée.")
            return
        response_texts = [row[2] for row in responses]
        print(f"Total réponses : {len(response_texts)}")
        print(f"Longueur moyenne : {sum(len(r) for r in response_texts) // len(response_texts)} chars")
        print(f"Exemple : {response_texts[0][:80]!r}")

if __name__ == "__main__":
    nexus_stats = NexusStatistics()
    nexus_stats.analyze_performance("Bonjour")