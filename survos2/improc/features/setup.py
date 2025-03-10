import os
from numpy.distutils.core import setup
from numpy.distutils.misc_util import Configuration, get_numpy_include_dirs
from Cython.Build import build_ext


def configuration(parent_package="", top_path=None):
    config = Configuration("features", parent_package, top_path, cmdclass={"build_ext": build_ext})
    config.add_extension(
        "_symeigval", sources="_symeigval.pyx", include_dirs=[get_numpy_include_dirs()]
    )
    config.add_extension(
        "_spencoding", sources=["_spencoding.pyx"], include_dirs=get_numpy_include_dirs()
    )
    config.add_extension(
        "_features", sources=["_features.pyx"], include_dirs=get_numpy_include_dirs()
    )
    config.add_extension("_rag", sources=["_rag.pyx"], include_dirs=get_numpy_include_dirs())
    config.add_extension("_dist", sources=["_dist.pyx"], include_dirs=get_numpy_include_dirs())

    return config


if __name__ == "__main__":
    config = configuration(top_path="").todict()
    setup(**config)
