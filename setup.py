import sys
import os
from setuptools import setup, find_packages


_top_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_top_dir, "lib"))
try:
    import depgraph
finally:
    del sys.path[0]
README = open(os.path.join(_top_dir, 'README.rst')).read()

setup(name='depgraph',
    version=depgraph.__version__,
    description="Dependency resolution algorithms for Python packages",
    long_description=README,
    classifiers=[c.strip() for c in """
        Development Status :: 4 - Beta
        Intended Audience :: Developers
        License :: OSI Approved :: MIT License
        Operating System :: OS Independent
        Programming Language :: Python :: 2.6
        Programming Language :: Python :: 2.7
        Programming Language :: Python :: 3
        Topic :: Software Development :: Libraries :: Python Modules
        """.split('\n') if c.strip()],
    keywords='distutils metadata dependencies algorithm setuptools',
    author='Sridhar Ratnakumar',
    author_email='sridhar.ratna@gmail.com',
    url='http://github.com/ActiveState/depgraph',
    license='MIT',
    py_modules=["depgraph"],
    package_dir={"": "lib"},
    include_package_data=True,
    zip_safe=False,
)
