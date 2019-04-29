"""
De/serialize Numpy arrays in disk files.

This is just a convenience provided for implementing file-based applications that use Numpy, which
happens to be the problem domain DynaMake was originally developed for.
"""

from typing import Optional

import numpy as np  # type: ignore


def write_numpy_array(array: np.ndarray, path: str) -> None:
    """
    Write a Numpy array to a file.
    """
    if not path.endswith('.npy'):
        path = path + '.npy'
    np.save(path, array)


def read_numpy_array(path: str, mmap_mode: Optional[str] = None) -> np.ndarray:
    """
    Read a Numpy array from a file.
    """
    if not path.endswith('.npy'):
        path = path + '.npy'
    return np.load(path, mmap_mode)
