"""
Cache stat calls for better performance.
"""

from glob import glob as glob_files
from sortedcontainers import SortedDict  # type: ignore
from stat import S_ISDIR
from typing import List
from typing import Optional
from typing import Union

import os
import shutil


class Stat:
    """
    Cache stat calls for better performance.
    """

    _cache: SortedDict

    @staticmethod
    def reset() -> None:
        """
        Clear the cached data.
        """
        Stat._cache = SortedDict()

    @staticmethod
    def stat(path: str) -> os.stat_result:
        """
        Return the ``stat`` data for a file.
        """
        return Stat._result(path, throw=True)  # type: ignore

    @staticmethod
    def try_stat(path: str) -> Optional[os.stat_result]:
        """
        Return the ``stat`` data for a file.
        """
        result = Stat._result(path, throw=False)
        if isinstance(result, BaseException):
            return None
        return result

    @staticmethod
    def exists(path: str) -> bool:
        """
        Test whether a file exists on disk.
        """
        result = Stat._result(path, throw=False)
        return not isinstance(result, BaseException)

    @staticmethod
    def isfile(path: str) -> bool:
        """
        Whether a file exists and is not a directory.
        """
        result = Stat._result(path, throw=False)
        return not isinstance(result, BaseException) and not S_ISDIR(result.st_mode)

    @staticmethod
    def isdir(path: str) -> bool:
        """
        Whether a file exists and is a directory.
        """
        result = Stat._result(path, throw=False)
        return not isinstance(result, BaseException) and S_ISDIR(result.st_mode)

    @staticmethod
    def _result(path: str, *, throw: bool) -> Union[BaseException, os.stat_result]:
        path = os.path.abspath(path)
        result = Stat._cache.get(path)

        if result is not None and (not throw or not isinstance(result, BaseException)):
            return result

        try:
            result = os.stat(path)
        except OSError as exception:
            result = exception

        Stat._cache[path] = result

        if throw and isinstance(result, BaseException):
            raise result

        return result

    @staticmethod
    def glob(pattern: str) -> List[str]:
        """
        Fast glob through the cache.

        If the pattern is a file name we know about, we can just return the result without touching
        the file system.
        """

        path = os.path.abspath(pattern)
        result = Stat._cache.get(path)

        if isinstance(result, BaseException):
            return []

        if result is None:
            paths = glob_files(pattern, recursive=True)
            if paths != [pattern]:
                return paths
            result = Stat._result(pattern, throw=False)
            assert not isinstance(result, BaseException)

        return [pattern]

    @staticmethod
    def forget(path: str) -> None:
        """
        Forget the cached ``stat`` data about a file. If it is a directory,
        also forget all the data about any files it contains.
        """
        path = os.path.abspath(path)
        index = Stat._cache.bisect_left(path)
        while index < len(Stat._cache):
            index_path = Stat._cache.iloc[index]
            if os.path.commonpath([path, index_path]) != path:
                return
            Stat._cache.popitem(index)

    @staticmethod
    def remove(path: str) -> None:
        """
        Force remove of a file or a directory.
        """
        if Stat.isfile(path):
            os.remove(path)
        elif Stat.exists(path):
            shutil.rmtree(path)
        Stat.forget(path)

    @staticmethod
    def touch(path: str) -> None:
        """
        Set the last modified time of a file (or a directory) to now.
        """
        os.utime(path)
        Stat.forget(path)

    @staticmethod
    def rmdir(path: str) -> None:
        """
        Remove an empty directory.
        """
        os.rmdir(path)
        Stat.forget(path)


Stat.reset()
