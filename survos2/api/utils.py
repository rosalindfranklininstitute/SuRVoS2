import hug
import inspect
import logging as log
import numpy as np
from loguru import logger
import os.path as op
from functools import wraps
from survos2.data_io import dataset_from_uri
from survos2.config import Config
from survos2.improc.utils import DatasetManager
from loguru import logger
from survos2.improc import map_blocks

CHUNK = Config["computing.chunks"]
CHUNK_SIZE = Config["computing.chunk_size"]
CHUNK_PAD = Config["computing.chunk_padding"]
SCALE = Config["computing.scale"]
STRETCH = Config["computing.stretch"]




def _unpack_lists(input_list):
    """Unpacks a list of strings containing sublists of strings
    such as provided by the data table in the 2d U-net training pipeline card

    Args:
        input_list (list): list of strings which contain sublists

    Returns:
        list: list of hidden items from the table (first member in sublist)
    """
    return [item[0] for item in input_list]
    # return [ast.literal_eval(item)[0] for item in input_list]



def rescale_denan(img):
    img = img - np.min(img)
    img = img / np.max(img)
    img = np.nan_to_num(img)
    return img

def simple_norm(dst):
    with DatasetManager(dst, out=None, dtype="float32", fillvalue=0) as DM:
        arr = DM.sources[0][:]
        arr -= np.min(arr)
        arr /= np.max(arr)
    map_blocks(pass_through, arr, out=dst, normalize=False)




def pass_through(x):
    return x


def dataset_repr(ds):
    metadata = dict()
    metadata.update(ds.metadata())
    metadata.pop("__data__", None)
    metadata.setdefault("id", ds.id)
    metadata.setdefault("name", op.basename(ds._path))
    metadata.setdefault("kind", "unknown")
    return metadata


def get_function_api(func):
    annotations = func.__annotations__.copy()
    descriptor = dict(params=dict(), returns=annotations.pop("return", None))
    defaults = get_default_args(func)
    for k, v in annotations.items():
        ptype = getattr(v, "__desc__", v.__class__.__name__)
        default = None
        if k in defaults:
            default = defaults[k]
        descriptor["params"][k] = dict(type=ptype, default=default)
    return descriptor


def get_default_args(func):
    signature = inspect.signature(func)
    return {
        k: v.default
        for k, v in signature.parameters.items()
        if v.default is not inspect.Parameter.empty
    }


def save_metadata(func):
    """
    Decorator to save the arguments of a function call as
    metadata in the resulting dataset.
    """
    fname = func.__name__

    @wraps(func)
    def wrapper(src, dst, *args, **kwargs):
        logger.debug(f"save_metadata wrapper: src {src}, dst {dst}")
        from pprint import pprint

        pprint(args)
        pprint(kwargs)
        result = func(src, dst, *args, **kwargs)
        ds = dataset_from_uri(dst, mode="r+")
        if ds.supports_metadata():
            for param in ["kind", "name"]:
                if not ds.has_attr(param):
                    print(f"Setting param {param} {fname}")
                    ds.set_attr(param, fname)
            for k, v in kwargs.items():
                print(f"Setting key value {k}, {v}")
                ds.set_attr(k, v)
            if type(src) == list:
                src_id = [dataset_from_uri(s, mode="r").id for s in src]
            else:
                src_id = dataset_from_uri(src, mode="r").id
            ds.set_attr("source", src_id)
        else:
            logger.debug("Dataset doesn't support metadata.")
        result = dataset_repr(ds)
        ds.close()
        logger.info("+ Computed: {}".format(fname))
        return result

    return wrapper


###############################################################################
# Session Handling

__session = dict()


@hug.directive(apply_globally=True)
def APISession(interface=None, request=None, **kwargs):
    if interface == hug.interface.HTTP:
        session = hug.directives.session(request=request)
        if session is None:
            logger.warning("HTTP Session does not exist.")
            return __session
        return session
    return __session


###############################################################################
# Exceptions


class APIException(Exception):
    def __init__(self, message, critical=False):
        super().__init__(message)
        self.critical = critical


def handle_api_exceptions(exception):
    msg = str(exception)
    if exception.critical:
        log.critical("#########################################")
        log.exception(exception)
        log.critical(msg)
        log.critical("=========================================")
    else:
        log.error(msg)
    return dict(error=True, error_message=msg, critical=exception.critical)


def handle_exceptions(exception):
    msg = type(exception).__name__ + ": " + str(exception)
    log.critical(">>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
    log.exception(exception)
    log.critical(msg)
    log.critical("<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<")
    return dict(error=True, error_message=msg, critical=True)
