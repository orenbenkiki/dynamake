#!/usr/bin/env python

from setuptools import setup, find_packages

with open("README.rst") as readme_file:
    readme = readme_file.read()

with open("HISTORY.rst") as history_file:
    history = history_file.read()

requirements = open("requirements.txt").read().split()
test_requirements = open("requirements_test.txt").read().split()
dev_requirements = open("requirements_dev.txt").read().split()

setup(  #
    author="Oren Ben-Kiki",
    author_email="oren@ben-kiki.org",
    python_requires=">=3.7",
    classifiers=[
        "Development Status :: 2 - Pre-Alpha",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
    ],
    description="Dynamic Make in Python",
    entry_points={
        "console_scripts": [
            "dynamake=dynamake.__main__:main",
        ],
    },
    install_requires=requirements,
    license="MIT license",
    long_description=readme + "\n\n" + history,
    include_package_data=True,
    keywords="dynamake,make",
    name="dynamake",
    packages=find_packages(include=["dynamake"]),
    test_suite="tests",
    tests_require=test_requirements,
    extras_require = {"dev": dev_requirements},
    url="https://github.com/orenbenkiki/dynamake.git",
    version="0.6.2-dev.1",
)
