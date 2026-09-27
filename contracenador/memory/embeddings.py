"""Serialização compacta e comparação de vetores de embedding."""
import array
import math


def serialize_vector(vector):
    return array.array("f", vector).tobytes()


def deserialize_vector(blob):
    vector = array.array("f")
    vector.frombytes(blob)
    return vector.tolist()


def cosine_similarity(first, second):
    count = min(len(first), len(second))
    if count == 0:
        return 0.0
    dot_product = sum(first[index] * second[index] for index in range(count))
    first_norm = math.sqrt(sum(value * value for value in first))
    second_norm = math.sqrt(sum(value * value for value in second))
    if first_norm == 0 or second_norm == 0:
        return 0.0
    return dot_product / (first_norm * second_norm)