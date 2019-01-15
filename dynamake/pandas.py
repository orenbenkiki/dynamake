"""
De/serialize Pandas data in disk files.

This is just a convenience provided for implementing file-based applications that use Pandas, which
happens to be the problem domain DynaMake was originally developed for. Arguably, if the ``feather``
implementation was better, there wouldn't have been a need for this at all.
"""

import os
from typing import Optional

import pandas as pd
from feather import read_dataframe
from feather import write_dataframe


def write_pandas_series(series: pd.Series, path: str, *, name: Optional[str] = None) -> None:
    """
    Write a Pandas series to a file.

    This wraps the series as a column in a data frame, then writes it.
    The ``feather.write_dataframe`` only preserves the column names of the data frame.
    This function also preserves the row (series entry) names (index) by storing them next to the
    data frame file, in a file with the same name and an ``.index`` suffix.

    Parameters
    ----------
    series
        The Pandas series to write.
    path
        The path to write the data frame to.
    name
        An optional name for the column in the data frame.
        By default, the series name is used, or ``series`` if it is ``None``.
    """
    data_frame = pd.DataFrame({name or series.name or 'series': series})
    write_pandas_data_frame(data_frame, path)


def read_pandas_series(path: str) -> pd.Series:
    """
    Read a Pandas series from a file.

    This expects a second file with a ``.index`` suffix
    to contain the entry names (index) of the data frame.
    """
    data_frame = read_pandas_data_frame(path)
    assert len(data_frame.columns) == 1
    series = data_frame.iloc[:, 0]
    return series


def write_pandas_data_frame(data_frame: pd.DataFrame, path: str) -> None:
    """
    Write a Pandas data frame to a file.

    The ``feather.write_dataframe`` only preserves the column names of the data frame.
    This function also preserves the row names (index) by storing them next to the
    data frame file, in a file with the same name and an ``.index`` suffix.

    Parameters
    ----------
    data_frame
        The Pandas data frame to write.
    path
        The path to write the data frame to.
    """
    frame_path = path
    if not path.endswith('.feather'):
        frame_path = path + '.feather'
    with open(frame_path, 'wb') as file:
        write_dataframe(data_frame, file)

    if data_frame.index.equals(pd.RangeIndex(len(data_frame.index))):
        return

    if path.endswith('.feather'):
        index_path = (path + '.index').replace('.feather.index', '.index.feather')
    else:
        index_path = path + '.index.feather'
    with open(index_path, 'wb') as file:
        write_dataframe(pd.DataFrame(data_frame.index), file)


def read_pandas_data_frame(path: str) -> pd.DataFrame:
    """
    Read a Pandas data frame from a file.

    This expects a second file with a ``.index`` suffix
    to contain the row names (index) of the data frame.
    """
    frame_path = path
    if not path.endswith('.feather'):
        frame_path = path + '.feather'
    with open(frame_path, 'rb') as file:
        data_frame = read_dataframe(file)

    if path.endswith('.feather'):
        index_path = (path + '.index').replace('.feather.index', '.index.feather')
    else:
        index_path = path + '.index.feather'
    if not os.path.exists(index_path):
        return data_frame

    with open(index_path, 'rb') as file:
        index_frame = read_dataframe(file)
    index_series = index_frame.iloc[:, 0]
    index_series.name = None
    return data_frame.set_index(index_series)
