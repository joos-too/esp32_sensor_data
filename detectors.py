from collections import deque
import math

# =======================================
# Z-Score Detector (gleitendes Fenster)
# =======================================
class ZScoreDetector:
    def __init__(self, window_size=10, threshold=3.0):
        self.window = deque((), window_size)
        self.window_size = window_size
        self.threshold = threshold

    def update(self, value):
        self.window.append(value)
        if len(self.window) < self.window_size:
            return False  # Noch nicht genug Daten
        mean = sum(self.window) / len(self.window)
        std = math.sqrt(sum((x - mean) ** 2 for x in self.window) / len(self.window))
        z = abs((value - mean) / std) if std > 0 else 0
        return z > self.threshold


# =======================================
# EWMA-Detector (Exponentially Weighted Moving Average)
# =======================================
class EWMADetector:
    def __init__(self, alpha=0.2, threshold=3.0):
        self.alpha = alpha
        self.threshold = threshold
        self.ewma = None
        self.variance = 0.0

    def update(self, value):
        if self.ewma is None:
            self.ewma = value
            self.variance = 0.0
            return False

        # Update EWMA und Varianz rekursiv
        diff = value - self.ewma
        self.ewma += self.alpha * diff
        self.variance = (1 - self.alpha) * (self.variance + self.alpha * diff * diff)

        std = math.sqrt(self.variance)
        deviation = abs(value - self.ewma)
        return deviation > self.threshold * std if std > 0 else False


# =======================================
# Adaptive Schwellenlogik
# (gleitendes Mittel mit Faktor für Abweichung)
# =======================================
class AdaptiveThresholdDetector:
    def __init__(self, window_size=20, sensitivity=1.5):
        self.window = deque((), window_size)
        self.window_size = window_size
        self.sensitivity = sensitivity

    def update(self, value):
        if len(self.window) < self.window_size:
            return False
        mean = sum(self.window) / len(self.window)
        max_dev = max(abs(x - mean) for x in self.window)
        upper = mean + self.sensitivity * max_dev
        lower = mean - self.sensitivity * max_dev
        self.window.append(value)
        return value > upper or value < lower


# =======================================
# Beispielhafte Nutzung
# =======================================
if __name__ == "__main__":
    zdet = ZScoreDetector(window_size=5, threshold=1.0)
    ewma = EWMADetector(alpha=0.3, threshold=1.5)
    adpt = AdaptiveThresholdDetector(window_size=5, sensitivity=0.5)

    # Beispiel-Datenstrom (z. B. Temperaturwerte)
    data = [22.0, 22.1, 22.2, 22.3, 22.2, 25.0, 22.1, 22.5, 21.9, 40.0, 21.8]

    for v in data:
        print("v =", v)
        if zdet.update(v):
            print("Z-Score Anomalie bei:", v)
        if ewma.update(v):
            print("EWMA Anomalie bei:", v)
        if adpt.update(v):
            print("Adaptive Schwelle Anomalie bei:", v)
