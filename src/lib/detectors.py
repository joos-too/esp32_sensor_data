from collections import deque
import math

# Z-Score detector (sliding window)
class ZScoreDetector:
    def __init__(self, window_size=10, threshold=3.0, min_std=0):
        self.window = deque((), window_size)
        self.window_size = window_size
        self.threshold = threshold
        self.min_std = max(0.0, min_std)

    def update(self, value):
        if len(self.window) < self.window_size:
            self.window.append(value)
            return False  # not enough data yet
        mean = sum(self.window) / len(self.window)
        std = math.sqrt(sum((x - mean) ** 2 for x in self.window) / len(self.window))
        std = max(std, self.min_std)
        z = abs((value - mean) / std) if std > 0 else 0

        self.window.append(value) # append afterwards to avoid skewing mean and std
        return z > self.threshold


# EWMA-Detector (Exponentially Weighted Moving Average)
class EWMADetector:
    def __init__(self, alpha=0.2, threshold=3.0, min_samples=5, min_std=0):
        self.alpha = alpha
        self.threshold = threshold
        self.min_samples = max(0, int(min_samples))
        self.min_std = max(0.0, min_std)
        self.ewma = None
        self.variance = 0.0
        self.samples = 0

    def update(self, value):
        if self.ewma is None:
            self.ewma = value
            self.variance = 0.0
            self.samples = 1
            return False

        # Use the previous EWMA as the baseline to avoid shrinking the residual.
        diff = value - self.ewma
        self.variance = (1 - self.alpha) * (self.variance + self.alpha * diff * diff)
        std = math.sqrt(self.variance)
        std = max(std, self.min_std)
        self.ewma += self.alpha * diff
        self.samples += 1
        if self.samples <= self.min_samples:
            return False
        return abs(diff) > self.threshold * std if std > 0 else False

# Simple rule-based detector
class RuleBasedDetector:
    def __init__(self, upper, lower):
        self.upper = upper
        self.lower = lower

    def update(self, value):
        return value > self.upper or value < self.lower

# Factory
DETECTOR_REGISTER = {
    "zscore": ZScoreDetector,
    "ewma": EWMADetector,
    "rulebased": RuleBasedDetector
}


def create_detector(name, **kwargs):
    cls = DETECTOR_REGISTER.get(name)
    if cls is None:
        raise ValueError("Unknown detector: {}".format(name))
    return cls(**kwargs)
