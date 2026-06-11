import sys
from functools import wraps


def memory_usage(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        initial_memory = sum(sys.getsizeof(i) for i in args)
        result = func(*args, **kwargs)
        final_memory = sum(sys.getsizeof(i) for i in (args + (result,)))
        print(f'Memory usage: {initial_memory} -> {final_memory}')
        return result
    return wrapper

# Exemple d'utilisation dans memory_manager.py
@memory_usage
def allocate_memory(size):
    # Code pour allouer de la mémoire...
    allocated_block = [0] * size
    return allocated_block