# -*- coding: utf-8 -*-
"""
MetaArray.py -  Class encapsulating ndarray with meta data
Copyright 2010  Luke Campagnola
Distributed under MIT/X11 license. See license.txt for more information.

MetaArray is an array class based on numpy.ndarray that allows storage of per-axis meta data
such as axis values, names, units, column names, etc. It also enables several
new methods for slicing and indexing the array based on this meta data. 
More info at http://www.scipy.org/Cookbook/MetaArray
"""

import copy, os
import pickle
import numpy as np
import warnings


## By default, the library will use HDF5 when writing files.
## This can be overridden by setting USE_HDF5 = False
USE_HDF5 = True
try:
    import h5py

    # Older h5py versions tucked Group and Dataset deeper inside the library:
    if not hasattr(h5py, "Group"):
        import h5py.highlevel

        h5py.Group = h5py.highlevel.Group
        h5py.Dataset = h5py.highlevel.Dataset

    HAVE_HDF5 = True
except:
    USE_HDF5 = False
    HAVE_HDF5 = False


def axis(name=None, cols=None, values=None, units=None):
    """Convenience function for generating axis descriptions when defining MetaArrays"""
    ax = {}
    cNameOrder = ["name", "units", "title"]
    if name is not None:
        ax["name"] = name
    if values is not None:
        ax["values"] = values
    if units is not None:
        ax["units"] = units
    if cols is not None:
        ax["cols"] = []
        for c in cols:
            if type(c) != list and type(c) != tuple:
                c = [c]
            col = {}
            for i in range(0, len(c)):
                col[cNameOrder[i]] = c[i]
            ax["cols"].append(col)
    return ax


class sliceGenerator(object):
    """Just a compact way to generate tuples of slice objects."""

    def __getitem__(self, arg):
        return arg

    def __getslice__(self, arg):
        return arg


SLICER = sliceGenerator()


class MetaArray(object):
    """N-dimensional array with meta data such as axis titles, units, and column names.

    May be initialized with a file name, a tuple representing the dimensions of the array,
    or any arguments that could be passed on to numpy.array()

    The info argument sets the metadata for the entire array. It is composed of a list
    of axis descriptions where each axis may have a name, title, units, and a list of column
    descriptions. An additional dict at the end of the axis list may specify parameters
    that apply to values in the entire array.

    For example:
        A 2D array of altitude values for a topographical map might look like
            info=[
        {'name': 'lat', 'title': 'Lattitude'},
        {'name': 'lon', 'title': 'Longitude'},
        {'title': 'Altitude', 'units': 'm'}
      ]
        In this case, every value in the array represents the altitude in feet at the lat, lon
        position represented by the array index. All of the following return the
        value at lat=10, lon=5:
            array[10, 5]
            array['lon':5, 'lat':10]
            array['lat':10][5]
        Now suppose we want to combine this data with another array of equal dimensions that
        represents the average rainfall for each location. We could easily store these as two
        separate arrays or combine them into a 3D array with this description:
            info=[
        {'name': 'vals', 'cols': [
          {'name': 'altitude', 'units': 'm'},
          {'name': 'rainfall', 'units': 'cm/year'}
        ]},
        {'name': 'lat', 'title': 'Lattitude'},
        {'name': 'lon', 'title': 'Longitude'}
      ]
        We can now access the altitude values with array[0] or array['altitude'], and the
        rainfall values with array[1] or array['rainfall']. All of the following return
        the rainfall value at lat=10, lon=5:
            array[1, 10, 5]
            array['lon':5, 'lat':10, 'val': 'rainfall']
            array['rainfall', 'lon':5, 'lat':10]
        Notice that in the second example, there is no need for an extra (4th) axis description
        since the actual values are described (name and units) in the column info for the first axis.
    """

    version = "2"

    # Default hdf5 compression to use when writing
    #   'gzip' is widely available and somewhat slow
    #   'lzf' is faster, but generally not available outside h5py
    #   'szip' is also faster, but lacks write support on windows
    # (so by default, we use no compression)
    # May also be a tuple (filter, opts), such as ('gzip', 3)
    defaultCompression = None

    ## Types allowed as axis or column names
    nameTypes = [str, tuple]

    @staticmethod
    def isNameType(var):
        return any(isinstance(var, t) for t in MetaArray.nameTypes)

    ## methods to wrap from embedded ndarray / HDF5
    wrapMethods = set(["__eq__", "__ne__", "__le__", "__lt__", "__ge__", "__gt__"])

    def __init__(self, data=None, info=None, dtype=None, file=None, copy=False, **kwargs):
        object.__init__(self)
        warnings.warn(
            "MetaArray is deprecated and will be removed in 0.14. "
            "Available though https://pypi.org/project/MetaArray/ as its own package.",
            DeprecationWarning,
            stacklevel=2,
        )
        self._isHDF = False

        if file is not None:
            self._data = None
            self.readFile(file, **kwargs)
            if kwargs.get("readAllData", True) and self._data is None:
                raise Exception("File read failed: %s" % file)
        else:
            self._info = info
            if hasattr(data, "implements") and data.implements("MetaArray"):
                self._info = data._info
                self._data = data.asarray()
            elif isinstance(data, tuple):  ## create empty array with specified shape
                self._data = np.empty(data, dtype=dtype)
            else:
                self._data = np.array(data, dtype=dtype, copy=copy)

        ## run sanity checks on info structure
        self.checkInfo()

    def checkInfo(self):
        info = self._info
        if info is None:
            if self._data is None:
                return
            else:
                self._info = [{} for i in range(self.ndim + 1)]
                return
        else:
            try:
                info = list(info)
            except:
                raise Exception("Info must be a list of axis specifications")
            if len(info) < self.ndim + 1:
                info.extend([{}] * (self.ndim + 1 - len(info)))
            elif len(info) > self.ndim + 1:
                raise Exception("Info parameter must be list of length ndim+1 or less.")
            for i in range(len(info)):
                if not isinstance(info[i], dict):
                    if info[i] is None:
                        info[i] = {}
                    else:
                        raise Exception("Axis specification must be Dict or None")
                if i < self.ndim and "values" in info[i]:
                    if type(info[i]["values"]) is list:
                        info[i]["values"] = np.array(info[i]["values"])
                    elif type(info[i]["values"]) is not np.ndarray:
                        raise Exception("Axis values must be specified as list or ndarray")
                    if info[i]["values"].ndim != 1 or info[i]["values"].shape[0] != self.shape[i]:
                        raise Exception(
                            "Values array for axis %d has incorrect shape. (given %s, but should be %s)"
                            % (i, str(info[i]["values"].shape), str((self.shape[i],)))
                        )
                if i < self.ndim and "cols" in info[i]:
                    if not isinstance(info[i]["cols"], list):
                        info[i]["cols"] = list(info[i]["cols"])
                    if len(info[i]["cols"]) != self.shape[i]:
                        raise Exception(
                            "Length of column list for axis %d does not match data. (given %d, but should be %d)"
                            % (i, len(info[i]["cols"]), self.shape[i])
                        )
            self._info = info

    def implements(self, name=None):
        ## Rather than isinstance(obj, MetaArray) use object.implements('MetaArray')
        if name is None:
            return ["MetaArray"]
        else:
            return name == "MetaArray"

    def __getitem__(self, ind):
        nInd = self._interpretIndexes(ind)

        a = self._data[nInd]
        if len(nInd) == self.ndim:
            if np.all(
                [not isinstance(ind, (slice, np.ndarray)) for ind in nInd]
            ):  ## no slices; we have requested a single value from the array
                return a

        ## indexing returned a sub-array; generate new info array to go with it
        info = []
        extraInfo = self._info[-1].copy()
        for i in range(0, len(nInd)):  ## iterate over all axes
            if type(nInd[i]) in [slice, list] or isinstance(
                nInd[i], np.ndarray
            ):  ## If the axis is sliced, keep the info but chop if necessary
                info.append(self._axisSlice(i, nInd[i]))
            else:  ## If the axis is indexed, then move the information from that single index to the last info dictionary
                newInfo = self._axisSlice(i, nInd[i])
                name = None
                colName = None
                for k in newInfo:
                    if k == "cols":
                        if "cols" not in extraInfo:
                            extraInfo["cols"] = []
                        extraInfo["cols"].append(newInfo[k])
                        if "units" in newInfo[k]:
                            extraInfo["units"] = newInfo[k]["units"]
                        if "name" in newInfo[k]:
                            colName = newInfo[k]["name"]
                    elif k == "name":
                        name = newInfo[k]
                    else:
                        if k not in extraInfo:
                            extraInfo[k] = newInfo[k]
                        extraInfo[k] = newInfo[k]
                if "name" not in extraInfo:
                    if name is None:
                        if colName is not None:
                            extraInfo["name"] = colName
                    else:
                        if colName is not None:
                            extraInfo["name"] = str(name) + ": " + str(colName)
                        else:
                            extraInfo["name"] = name

        info.append(extraInfo)

        return MetaArray(a, info=info)

    @property
    def ndim(self):
        return len(self.shape)  ## hdf5 objects do not have ndim property.

    @property
    def shape(self):
        return self._data.shape

    @property
    def dtype(self):
        return self._data.dtype

    def __len__(self):
        return len(self._data)

    def __getslice__(self, *args):
        return self.__getitem__(slice(*args))

    def __setitem__(self, ind, val):
        nInd = self._interpretIndexes(ind)
        try:
            self._data[nInd] = val
        except:
            print(self, nInd, val)
            raise

    def __getattr__(self, attr):
        if attr in self.wrapMethods:
            return getattr(self._data, attr)
        else:
            raise AttributeError(attr)

    def __eq__(self, b):
        return self._binop("__eq__", b)

    def __ne__(self, b):
        return self._binop("__ne__", b)

    def __sub__(self, b):
        return self._binop("__sub__", b)

    def __add__(self, b):
        return self._binop("__add__", b)

    def __mul__(self, b):
        return self._binop("__mul__", b)

    def __div__(self, b):
        return self._binop("__div__", b)

    def __truediv__(self, b):
        return self._binop("__truediv__", b)

    def _binop(self, op, b):
        if isinstance(b, MetaArray):
            b = b.asarray()
        a = self.asarray()
        c = getattr(a, op)(b)
        if c.shape != a.shape:
            raise Exception(
                "Binary operators with MetaArray must return an array of the same shape (this shape is %s, result shape was %s)"
                % (a.shape, c.shape)
            )
        return MetaArray(c, info=self.infoCopy())

    def asarray(self):
        if isinstance(self._data, np.ndarray):
            return self._data
        else:
            return np.array(self._data)

    def __array__(self, dtype=None):
        ## supports np.array(metaarray_instance)
        if dtype is None:
            return self.asarray()
        else:
            return self.asarray().astype(dtype)

    def view(self, typ):
        warnings.warn(
            "MetaArray.view is deprecated and will be removed in 0.13. "
            "Use MetaArray.asarray() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        if typ is np.ndarray:
            return self.asarray()
        else:
            raise Exception("invalid view type: %s" % str(typ))

    def axisValues(self, axis):
        """Return the list of values for an axis"""
        ax = self._interpretAxis(axis)
        if "values" in self._info[ax]:
            return self._info[ax]["values"]
        else:
            raise Exception("Array axis %s (%d) has no associated values." % (str(axis), ax))

    def xvals(self, axis):
        """Synonym for axisValues()"""
        return self.axisValues(axis)

    def axisHasValues(self, axis):
        ax = self._interpretAxis(axis)
        return "values" in self._info[ax]

    def axisHasColumns(self, axis):
        ax = self._interpretAxis(axis)
        return "cols" in self._info[ax]

    def axisUnits(self, axis):
        """Return the units for axis"""
        ax = self._info[self._interpretAxis(axis)]
        if "units" in ax:
            return ax["units"]

    def hasColumn(self, axis, col):
        ax = self._info[self._interpretAxis(axis)]
        if "cols" in ax:
            for c in ax["cols"]:
                if c["name"] == col:
                    return True
        return False

    def listColumns(self, axis=None):
        """Return a list of column names for axis. If axis is not specified, then return a dict of {axisName: (column names), ...}."""
        if axis is None:
            ret = {}
            for i in range(self.ndim):
                if "cols" in self._info[i]:
                    cols = [c["name"] for c in self._info[i]["cols"]]
                else:
                    cols = []
                ret[self.axisName(i)] = cols
            return ret
        else:
            axis = self._interpretAxis(axis)
            return [c["name"] for c in self._info[axis]["cols"]]

    def columnName(self, axis, col):
        ax = self._info[self._interpretAxis(axis)]
        return ax["cols"][col]["name"]

    def axisName(self, n):
        return self._info[n].get("name", n)

    def columnUnits(self, axis, column):
        """Return the units for column in axis"""
        ax = self._info[self._interpretAxis(axis)]
        if "cols" in ax:
            for c in ax["cols"]:
                if c["name"] == column:
                    return c["units"]
            raise Exception("Axis %s has no column named %s" % (str(axis), str(column)))
        else:
            raise Exception("Axis %s has no column definitions" % str(axis))

    def rowsort(self, axis, key=0):
        """Return this object with all records sorted along axis using key as the index to the values to compare. Does not yet modify meta info."""
        ## make sure _info is copied locally before modifying it!

        keyList = self[key]
        order = keyList.argsort()
        if type(axis) == int:
            ind = [slice(None)] * axis
            ind.append(order)
        elif isinstance(axis, str):
            ind = (slice(axis, order),)
        return self[tuple(ind)]

    def append(self, val, axis):
        """Return this object with val appended along axis. Does not yet combine meta info."""
        ## make sure _info is copied locally before modifying it!

        s = list(self.shape)
        axis = self._interpretAxis(axis)
        s[axis] += 1
        n = MetaArray(tuple(s), info=self._info, dtype=self.dtype)
        ind = [slice(None)] * self.ndim
        ind[axis] = slice(None, -1)
        n[tuple(ind)] = self
        ind[axis] = -1
        n[tuple(ind)] = val
        return n

    def extend(self, val, axis):
        """Return the concatenation along axis of this object and val. Does not yet combine meta info."""
        ## make sure _info is copied locally before modifying it!

        axis = self._interpretAxis(axis)
        return MetaArray(np.concatenate(self, val, axis), info=self._info)

    def infoCopy(self, axis=None):
        """Return a deep copy of the axis meta info for this object"""
        if axis is None:
            return copy.deepcopy(self._info)
        else:
            return copy.deepcopy(self._info[self._interpretAxis(axis)])

    def copy(self):
        return MetaArray(self._data.copy(), info=self.infoCopy())

    def _interpretIndexes(self, ind):
        # print "interpret", ind
        if not isinstance(ind, tuple):
            ## a list of slices should be interpreted as a tuple of slices.
            if isinstance(ind, list) and len(ind) > 0 and isinstance(ind[0], slice):
                ind = tuple(ind)
            ## everything else can just be converted to a length-1 tuple
            else:
                ind = (ind,)

        nInd = [slice(None)] * self.ndim
        numOk = True  ## Named indices not started yet; numbered sill ok
        for i in range(0, len(ind)):
            (axis, index, isNamed) = self._interpretIndex(ind[i], i, numOk)
            nInd[axis] = index
            if isNamed:
                numOk = False
        return tuple(nInd)

    def _interpretAxis(self, axis):
        if isinstance(axis, (str, tuple)):
            return self._getAxis(axis)
        else:
            return axis

    def _interpretIndex(self, ind, pos, numOk):
        # print "Interpreting index", ind, pos, numOk

        ## should probably check for int first to speed things up..
        if type(ind) is int:
            if not numOk:
                raise Exception("string and integer indexes may not follow named indexes")
            # print "  normal numerical index"
            return (pos, ind, False)
        if MetaArray.isNameType(ind):
            if not numOk:
                raise Exception("string and integer indexes may not follow named indexes")
            # print "  String index, column is ", self._getIndex(pos, ind)
            return (pos, self._getIndex(pos, ind), False)
        elif type(ind) is slice:
            # print "  Slice index"
            if MetaArray.isNameType(ind.start) or MetaArray.isNameType(
                ind.stop
            ):  ## Not an actual slice!
                # print "    ..not a real slice"
                axis = self._interpretAxis(ind.start)
                # print "    axis is", axis

                ## x[Axis:Column]
                if MetaArray.isNameType(ind.stop):
                    # print "    column name, column is ", self._getIndex(axis, ind.stop)
                    index = self._getIndex(axis, ind.stop)

                ## x[Axis:min:max]
                elif (isinstance(ind.stop, float) or isinstance(ind.step, float)) and (
                    "values" in self._info[axis]
                ):
                    # print "    axis value range"
                    if ind.stop is None:
                        mask = self.xvals(axis) < ind.step
                    elif ind.step is None:
                        mask = self.xvals(axis) >= ind.stop
                    else:
                        mask = (self.xvals(axis) >= ind.stop) * (self.xvals(axis) < ind.step)
                    ##print "mask:", mask
                    index = mask

                ## x[Axis:columnIndex]
                elif isinstance(ind.stop, int) or isinstance(ind.step, int):
                    # print "    normal slice after named axis"
                    if ind.step is None:
                        index = ind.stop
                    else:
                        index = slice(ind.stop, ind.step)

                ## x[Axis: [list]]
                elif type(ind.stop) is list:
                    # print "    list of indexes from named axis"
                    index = []
                    for i in ind.stop:
                        if type(i) is int:
                            index.append(i)
                        elif MetaArray.isNameType(i):
                            index.append(self._getIndex(axis, i))
                        else:
                            ## unrecognized type, try just passing on to array
                            index = ind.stop
                            break

                else:
                    # print "    other type.. forward on to array for handling", type(ind.stop)
                    index = ind.stop
                # print "Axis %s (%s) : %s" % (ind.start, str(axis), str(type(index)))
                # if type(index) is np.ndarray:
                # print "    ", index.shape
                return (axis, index, True)
            else:
                # print "  Looks like a real slice, passing on to array"
                return (pos, ind, False)
        elif type(ind) is list:
            # print "  List index., interpreting each element individually"
            indList = [self._interpretIndex(i, pos, numOk)[1] for i in ind]
            return (pos, indList, False)
        else:
            if not numOk:
                raise Exception("string and integer indexes may not follow named indexes")
            # print "  normal numerical index"
            return (pos, ind, False)

    def _getAxis(self, name):
        for i in range(0, len(self._info)):
            axis = self._info[i]
            if "name" in axis and axis["name"] == name:
                return i
        raise Exception("No axis named %s.\n  info=%s" % (name, self._info))

    def _getIndex(self, axis, name):
        ax = self._info[axis]
        if ax is not None and "cols" in ax:
            for i in range(0, len(ax["cols"])):
                if "name" in ax["cols"][i] and ax["cols"][i]["name"] == name:
                    return i
        raise Exception("Axis %d has no column named %s.\n  info=%s" % (axis, name, self._info))

    def _axisCopy(self, i):
        return copy.deepcopy(self._info[i])

    def _axisSlice(self, i, cols):
        # print "axisSlice", i, cols
        if "cols" in self._info[i] or "values" in self._info[i]:
            ax = self._axisCopy(i)
            if "cols" in ax:
                # print "  slicing columns..", array(ax['cols']), cols
                sl = np.array(ax["cols"])[cols]
                if isinstance(sl, np.ndarray):
                    sl = list(sl)
                ax["cols"] = sl
                # print "  result:", ax['cols']
            if "values" in ax:
                ax["values"] = np.array(ax["values"])[cols]
        else:
            ax = self._info[i]
        # print "     ", ax
        return ax

    def prettyInfo(self):
        s = ""
        titles = []
        maxl = 0
        for i in range(len(self._info) - 1):
            ax = self._info[i]
            axs = ""
            if "name" in ax:
                axs += '"%s"' % str(ax["name"])
            else:
                axs += "%d" % i
            if "units" in ax:
                axs += " (%s)" % str(ax["units"])
            titles.append(axs)
            if len(axs) > maxl:
                maxl = len(axs)

        for i in range(min(self.ndim, len(self._info) - 1)):
            ax = self._info[i]
            axs = titles[i]
            axs += "%s[%d] :" % (
                " " * (maxl - len(axs) + 5 - len(str(self.shape[i]))),
                self.shape[i],
            )
            if "values" in ax:
                if self.shape[i] > 0:
                    v0 = ax["values"][0]
                    axs += "  values: [%g" % (v0)
                    if self.shape[i] > 1:
                        v1 = ax["values"][-1]
                        axs += " ... %g] (step %g)" % (v1, (v1 - v0) / (self.shape[i] - 1))
                    else:
                        axs += "]"
                else:
                    axs += "  values: []"
            if "cols" in ax:
                axs += " columns: "
                colstrs = []
                for c in range(len(ax["cols"])):
                    col = ax["cols"][c]
                    cs = str(col.get("name", c))
                    if "units" in col:
                        cs += " (%s)" % col["units"]
                    colstrs.append(cs)
                axs += "[" + ", ".join(colstrs) + "]"
            s += axs + "\n"
        s += str(self._info[-1])
        return s

    def __repr__(self):
        return "%s\n-----------------------------------------------\n%s" % (
            self.view(np.ndarray).__repr__(),
            self.prettyInfo(),
        )

    def __str__(self):
        return self.__repr__()

    def axisCollapsingFn(self, fn, axis=None, *args, **kargs):
        fn = getattr(self._data, fn)
        if axis is None:
            return fn(axis, *args, **kargs)
        else:
            info = self.infoCopy()
            axis = self._interpretAxis(axis)
            info.pop(axis)
            return MetaArray(fn(axis, *args, **kargs), info=info)

    def mean(self, axis=None, *args, **kargs):
        return self.axisCollapsingFn("mean", axis, *args, **kargs)

    def min(self, axis=None, *args, **kargs):
        return self.axisCollapsingFn("min", axis, *args, **kargs)

    def max(self, axis=None, *args, **kargs):
        return self.axisCollapsingFn("max", axis, *args, **kargs)

    def transpose(self, *args):
        if len(args) == 1 and hasattr(args[0], "__iter__"):
            order = args[0]
        else:
            order = args

        order = [self._interpretAxis(ax) for ax in order]
        infoOrder = order + list(range(len(order), len(self._info)))
        info = [self._info[i] for i in infoOrder]
        order = order + list(range(len(order), self.ndim))

        try:
            if self._isHDF:
                return MetaArray(np.array(self._data).transpose(order), info=info)
            else:
                return MetaArray(self._data.transpose(order), info=info)
        except:
            print(order)
            raise

    #### File I/O Routines
    def readFile(self, filename, **kwargs):
        """Load the data and meta info stored in *filename*
        Different arguments are allowed depending on the type of file.
        For HDF5 files:

            *writable* (bool) if True, then any modifications to data in the array will be stored to disk.
            *readAllData* (bool) if True, then all data in the array is immediately read from disk
                          and the file is closed (this is the default for files < 500MB). Otherwise, the file will
                          be left open and data will be read only as requested (this is
                          the default for files >= 500MB).


        """
        ## decide which read function to use
        with open(filename, "rb") as fd:
            magic = fd.read(8)
            if magic == b"\x89HDF\r\n\x1a\n":
                fd.close()
                self._readHDF5(filename, **kwargs)
                self._isHDF = True
            else:
                fd.seek(0)
                meta = MetaArray._readMeta(fd)
                if not kwargs.get("readAllData", True):
                    self._data = np.empty(meta["shape"], dtype=meta["type"])
                if "version" in meta:
                    ver = meta["version"]
                else:
                    ver = 1
                rFuncName = "_readData%s" % str(ver)
                if not hasattr(MetaArray, rFuncName):
                    raise Exception(
                        "This MetaArray library does not support array version '%s'" % ver
                    )
                rFunc = getattr(self, rFuncName)
                rFunc(fd, meta, **kwargs)
                self._isHDF = False

    @staticmethod
    def _readMeta(fd):
        """Read meta array from the top of a file. Read lines until a blank line is reached.
        This function should ideally work for ALL versions of MetaArray.
        """
        meta = ""
        ## Read meta information until the first blank line
        while True:
            line = fd.readline().strip()
            if line == "":
                break
            meta += line
        ret = eval(meta)
        # print ret
        return ret

    def _readData1(self, fd, meta, mmap=False, **kwds):
        ## Read array data from the file descriptor for MetaArray v1 files
        ## read in axis values for any axis that specifies a length
        frameSize = 1
        for ax in meta["info"]:
            if "values_len" in ax:
                ax["values"] = np.fromstring(fd.read(ax["values_len"]), dtype=ax["values_type"])
                frameSize *= ax["values_len"]
                del ax["values_len"]
                del ax["values_type"]
        self._info = meta["info"]
        if not kwds.get("readAllData", True):
            return
        ## the remaining data is the actual array
        if mmap:
            subarr = np.memmap(fd, dtype=meta["type"], mode="r", shape=meta["shape"])
        else:
            subarr = np.fromstring(fd.read(), dtype=meta["type"])
            subarr.shape = meta["shape"]
        self._data = subarr

    def _readData2(self, fd, meta, mmap=False, subset=None, **kwds):
        ## read in axis values
        dynAxis = None
        frameSize = 1
        ## read in axis values for any axis that specifies a length
        for i in range(len(meta["info"])):
            ax = meta["info"][i]
            if "values_len" in ax:
                if ax["values_len"] == "dynamic":
                    if dynAxis is not None:
                        raise Exception(
                            "MetaArray has more than one dynamic axis! (this is not allowed)"
                        )
                    dynAxis = i
                else:
                    ax["values"] = np.fromstring(fd.read(ax["values_len"]), dtype=ax["values_type"])
                    frameSize *= ax["values_len"]
                    del ax["values_len"]
                    del ax["values_type"]
        self._info = meta["info"]
        if not kwds.get("readAllData", True):
            return

        ## No axes are dynamic, just read the entire array in at once
        if dynAxis is None:
            if meta["type"] == "object":
                if mmap:
                    raise Exception("memmap not supported for arrays with dtype=object")
                subarr = pickle.loads(fd.read())
            else:
                if mmap:
                    subarr = np.memmap(fd, dtype=meta["type"], mode="r", shape=meta["shape"])
                else:
                    subarr = np.fromstring(fd.read(), dtype=meta["type"])
            subarr.shape = meta["shape"]
        ## One axis is dynamic, read in a frame at a time
        else:
            if mmap:
                raise Exception(
                    "memmap not supported for non-contiguous arrays. Use rewriteContiguous() to convert."
                )
            ax = meta["info"][dynAxis]
            xVals = []
            frames = []
            frameShape = list(meta["shape"])
            frameShape[dynAxis] = 1
            frameSize = np.prod(frameShape)
            n = 0
            while True:
                ## Extract one non-blank line
                while True:
                    line = fd.readline()
                    if line != "\n":
                        break
                if line == "":
                    break

                ## evaluate line
                inf = eval(line)

                ## read data block
                # print "read %d bytes as %s" % (inf['len'], meta['type'])
                if meta["type"] == "object":
                    data = pickle.loads(fd.read(inf["len"]))
                else:
                    data = np.fromstring(fd.read(inf["len"]), dtype=meta["type"])

                if data.size != frameSize * inf["numFrames"]:
                    # print data.size, frameSize, inf['numFrames']
                    raise Exception("Wrong frame size in MetaArray file! (frame %d)" % n)

                ## read in data block
                shape = list(frameShape)
                shape[dynAxis] = inf["numFrames"]
                data.shape = shape
                if subset is not None:
                    dSlice = subset[dynAxis]
                    if dSlice.start is None:
                        dStart = 0
                    else:
                        dStart = max(0, dSlice.start - n)
                    if dSlice.stop is None:
                        dStop = data.shape[dynAxis]
                    else:
                        dStop = min(data.shape[dynAxis], dSlice.stop - n)
                    newSubset = list(subset[:])
                    newSubset[dynAxis] = slice(dStart, dStop)
                    if dStop > dStart:
                        frames.append(data[tuple(newSubset)].copy())
                else:
                    frames.append(data)

                n += inf["numFrames"]
                if "xVals" in inf:
                    xVals.extend(inf["xVals"])
            subarr = np.concatenate(frames, axis=dynAxis)
            if len(xVals) > 0:
                ax["values"] = np.array(xVals, dtype=ax["values_type"])
            del ax["values_len"]
            del ax["values_type"]
        self._info = meta["info"]
        self._data = subarr

    def _readHDF5(self, fileName, readAllData=None, writable=False, **kargs):
        if "close" in kargs and readAllData is None:  ## for backward compatibility
            readAllData = kargs["close"]

        if readAllData is True and writable is True:
            raise Exception("Incompatible arguments: readAllData=True and writable=True")

        if not HAVE_HDF5:
            try:
                assert writable == False
                assert readAllData != False
                self._readHDF5Remote(fileName)
                return
            except:
                raise Exception(
                    "The file '%s' is HDF5-formatted, but the HDF5 library (h5py) was not found."
                    % fileName
                )

        ## by default, readAllData=True for files < 500MB
        if readAllData is None:
            size = os.stat(fileName).st_size
            readAllData = size < 500e6

        if writable is True:
            mode = "r+"
        else:
            mode = "r"
        f = h5py.File(fileName, mode)

        ver = f.attrs["MetaArray"]
        try:
            ver = ver.decode("utf-8")
        except:
            pass
        if ver > MetaArray.version:
            print(
                "Warning: This file was written with MetaArray version %s, but you are using version %s. (Will attempt to read anyway)"
                % (str(ver), str(MetaArray.version))
            )
        meta = MetaArray.readHDF5Meta(f["info"])
        self._info = meta

        if writable or not readAllData:  ## read all data, convert to ndarray, close file
            self._data = f["data"]
            self._openFile = f
        else:
            self._data = f["data"][:]
            f.close()

    def _readHDF5Remote(self, fileName):
        ## Used to read HDF5 files via remote process.
        ## This is needed in the case that HDF5 is not importable due to the use of python-dbg.
        proc = getattr(MetaArray, "_hdf5Process", None)

        if proc == False:
            raise Exception("remote read failed")
        if proc == None:
            from .. import multiprocess as mp

            # print "new process"
            proc = mp.Process(executable="/usr/bin/python")
            proc.setProxyOptions(deferGetattr=True)
            MetaArray._hdf5Process = proc
            MetaArray._h5py_metaarray = proc._import("pyqtgraph.metaarray")
        ma = MetaArray._h5py_metaarray.MetaArray(file=fileName)
        self._data = ma.asarray()._getValue()
        self._info = ma._info._getValue()

    @staticmethod
    def mapHDF5Array(data, writable=False):
        off = data.id.get_offset()
        if writable:
            mode = "r+"
        else:
            mode = "r"
        if off is None:
            raise Exception(
                "This dataset uses chunked storage; it can not be memory-mapped. (store using mappable=True)"
            )
        return np.memmap(
            filename=data.file.filename, offset=off, dtype=data.dtype, shape=data.shape, mode=mode
        )

    @staticmethod
    def readHDF5Meta(root, mmap=False):
        data = {}

        ## Pull list of values from attributes and child objects
        for k in root.attrs:
            val = root.attrs[k]
            if isinstance(val, bytes):
                val = val.decode()
            if isinstance(val, str):  ## strings need to be re-evaluated to their original types
                try:
                    val = eval(val)
                except:
                    raise Exception('Can not evaluate string: "%s"' % val)
            data[k] = val
        for k in root:
            obj = root[k]
            if isinstance(obj, h5py.Group):
                val = MetaArray.readHDF5Meta(obj)
            elif isinstance(obj, h5py.Dataset):
                if mmap:
                    val = MetaArray.mapHDF5Array(obj)
                else:
                    val = obj[:]
            else:
                raise Exception("Don't know what to do with type '%s'" % str(type(obj)))
            data[k] = val

        typ = root.attrs["_metaType_"]
        try:
            typ = typ.decode("utf-8")
        except:
            pass
        del data["_metaType_"]

        if typ == "dict":
            return data
        elif typ == "list" or typ == "tuple":
            d2 = [None] * len(data)
            for k in data:
                d2[int(k)] = data[k]
            if typ == "tuple":
                d2 = tuple(d2)
            return d2
        else:
            raise Exception("Don't understand metaType '%s'" % typ)

    def write(self, fileName, **opts):
        """Write this object to a file. The object can be restored by calling MetaArray(file=fileName)
        opts:
            appendAxis: the name (or index) of the appendable axis. Allows the array to grow.
            appendKeys: a list of keys (other than "values") for metadata to append to on the appendable axis.
            compression: None, 'gzip' (good compression), 'lzf' (fast compression), etc.
            chunks: bool or tuple specifying chunk shape
        """
        if USE_HDF5 is False:
            return self.writeMa(fileName, **opts)
        elif HAVE_HDF5 is True:
            return self.writeHDF5(fileName, **opts)
        else:
            raise Exception(
                "h5py is required for writing .ma hdf5 files, but it could not be imported."
            )

    def writeMeta(self, fileName):
        """Used to re-write meta info to the given file.
        This feature is only available for HDF5 files."""
        f = h5py.File(fileName, "r+")
        if f.attrs["MetaArray"] != MetaArray.version:
            raise Exception(
                "The file %s was created with a different version of MetaArray. Will not modify."
                % fileName
            )
        del f["info"]

        self.writeHDF5Meta(f, "info", self._info)
        f.close()

    def writeHDF5(self, fileName, **opts):
        ## default options for writing datasets
        comp = self.defaultCompression
        if isinstance(comp, tuple):
            comp, copts = comp
        else:
            copts = None

        dsOpts = {
            "compression": comp,
            "chunks": True,
        }
        if copts is not None:
            dsOpts["compression_opts"] = copts

        ## if there is an appendable axis, then we can guess the desired chunk shape (optimized for appending)
        appAxis = opts.get("appendAxis", None)
        if appAxis is not None:
            appAxis = self._interpretAxis(appAxis)
            cs = [min(100000, x) for x in self.shape]
            cs[appAxis] = 1
            dsOpts["chunks"] = tuple(cs)

        ## if there are columns, then we can guess a different chunk shape
        ## (read one column at a time)
        else:
            cs = [min(100000, x) for x in self.shape]
            for i in range(self.ndim):
                if "cols" in self._info[i]:
                    cs[i] = 1
            dsOpts["chunks"] = tuple(cs)

        ## update options if they were passed in
        for k in dsOpts:
            if k in opts:
                dsOpts[k] = opts[k]

        ## If mappable is in options, it disables chunking/compression
        if opts.get("mappable", False):
            dsOpts = {"chunks": None, "compression": None}

        ## set maximum shape to allow expansion along appendAxis
        append = False
        if appAxis is not None:
            maxShape = list(self.shape)
            ax = self._interpretAxis(appAxis)
            maxShape[ax] = None
            if os.path.exists(fileName):
                append = True
            dsOpts["maxshape"] = tuple(maxShape)
        else:
            dsOpts["maxshape"] = None

        if append:
            f = h5py.File(fileName, "r+")
            if f.attrs["MetaArray"] != MetaArray.version:
                raise Exception(
                    "The file %s was created with a different version of MetaArray. Will not modify."
                    % fileName
                )

            ## resize data and write in new values
            data = f["data"]
            shape = list(data.shape)
            shape[ax] += self.shape[ax]
            data.resize(tuple(shape))
            sl = [slice(None)] * len(data.shape)
            sl[ax] = slice(-self.shape[ax], None)
            data[tuple(sl)] = self.view(np.ndarray)

            ## add axis values if they are present.
            axKeys = ["values"]
            axKeys.extend(opts.get("appendKeys", []))
            axInfo = f["info"][str(ax)]
            for key in axKeys:
                if key in axInfo:
                    v = axInfo[key]
                    v2 = self._info[ax][key]
                    shape = list(v.shape)
                    shape[0] += v2.shape[0]
                    v.resize(shape)
                    v[-v2.shape[0] :] = v2
                else:
                    raise TypeError(
                        'Cannot append to axis info key "%s"; this key is not present in the target file.'
                        % key
                    )
            f.close()
        else:
            f = h5py.File(fileName, "w")
            f.attrs["MetaArray"] = MetaArray.version
            # print dsOpts
            f.create_dataset("data", data=self.view(np.ndarray), **dsOpts)

            ## dsOpts is used when storing meta data whenever an array is encountered
            ## however, 'chunks' will no longer be valid for these arrays if it specifies a chunk shape.
            ## 'maxshape' is right-out.
            if isinstance(dsOpts["chunks"], tuple):
                dsOpts["chunks"] = True
                if "maxshape" in dsOpts:
                    del dsOpts["maxshape"]
            self.writeHDF5Meta(f, "info", self._info, **dsOpts)
            f.close()

    def writeHDF5Meta(self, root, name, data, **dsOpts):
        if isinstance(data, np.ndarray):
            dsOpts["maxshape"] = (None,) + data.shape[1:]
            root.create_dataset(name, data=data, **dsOpts)
        elif isinstance(data, list) or isinstance(data, tuple):
            gr = root.create_group(name)
            if isinstance(data, list):
                gr.attrs["_metaType_"] = "list"
            else:
                gr.attrs["_metaType_"] = "tuple"
            # n = int(np.log10(len(data))) + 1
            for i in range(len(data)):
                self.writeHDF5Meta(gr, str(i), data[i], **dsOpts)
        elif isinstance(data, dict):
            gr = root.create_group(name)
            gr.attrs["_metaType_"] = "dict"
            for k, v in data.items():
                self.writeHDF5Meta(gr, k, v, **dsOpts)
        elif (
            isinstance(data, int)
            or isinstance(data, float)
            or isinstance(data, np.integer)
            or isinstance(data, np.floating)
        ):
            root.attrs[name] = data
        else:
            try:  ## strings, bools, None are stored as repr() strings
                root.attrs[name] = repr(data)
            except:
                print(
                    "Can not store meta data of type '%s' in HDF5. (key is '%s')"
                    % (str(type(data)), str(name))
                )
                raise

    def writeMa(self, fileName, appendAxis=None, newFile=False):
        """Write an old-style .ma file"""
        meta = {
            "shape": self.shape,
            "type": str(self.dtype),
            "info": self.infoCopy(),
            "version": MetaArray.version,
        }
        axstrs = []

        ## copy out axis values for dynamic axis if requested
        if appendAxis is not None:
            if MetaArray.isNameType(appendAxis):
                appendAxis = self._interpretAxis(appendAxis)

            ax = meta["info"][appendAxis]
            ax["values_len"] = "dynamic"
            if "values" in ax:
                ax["values_type"] = str(ax["values"].dtype)
                dynXVals = ax["values"]
                del ax["values"]
            else:
                dynXVals = None

        ## Generate axis data string, modify axis info so we know how to read it back in later
        for ax in meta["info"]:
            if "values" in ax:
                axstrs.append(ax["values"].tostring())
                ax["values_len"] = len(axstrs[-1])
                ax["values_type"] = str(ax["values"].dtype)
                del ax["values"]

        ## Decide whether to output the meta block for a new file
        if not newFile:
            ## If the file does not exist or its size is 0, then we must write the header
            newFile = (not os.path.exists(fileName)) or (os.stat(fileName).st_size == 0)

        ## write data to file
        if appendAxis is None or newFile:
            fd = open(fileName, "wb")
            fd.write(str(meta) + "\n\n")
            for ax in axstrs:
                fd.write(ax)
        else:
            fd = open(fileName, "ab")

        if self.dtype != object:
            dataStr = self.view(np.ndarray).tostring()
        else:
            dataStr = pickle.dumps(self.view(np.ndarray))
        # print self.size, len(dataStr), self.dtype
        if appendAxis is not None:
            frameInfo = {"len": len(dataStr), "numFrames": self.shape[appendAxis]}
            if dynXVals is not None:
                frameInfo["xVals"] = list(dynXVals)
            fd.write("\n" + str(frameInfo) + "\n")
        fd.write(dataStr)
        fd.close()

    def writeCsv(self, fileName=None):
        """Write 2D array to CSV file or return the string if no filename is given"""
        if self.ndim > 2:
            raise Exception("CSV Export is only for 2D arrays")
        if fileName is not None:
            file = open(fileName, "w")
        ret = ""
        if "cols" in self._info[0]:
            s = ",".join([x["name"] for x in self._info[0]["cols"]]) + "\n"
            if fileName is not None:
                file.write(s)
            else:
                ret += s
        for row in range(0, self.shape[1]):
            s = ",".join(["%g" % x for x in self[:, row]]) + "\n"
            if fileName is not None:
                file.write(s)
            else:
                ret += s
        if fileName is not None:
            file.close()
        else:
            return ret


# -*- coding: utf-8 -*-
import numpy as np
from pyqtgraph.Qt import QtCore, QtGui

translate = QtCore.QCoreApplication.translate

__all__ = ["TableWidget"]


def _defersort(fn):
    def defersort(self, *args, **kwds):
        # may be called recursively; only the first call needs to block sorting
        setSorting = False
        if self._sorting is None:
            self._sorting = self.isSortingEnabled()
            setSorting = True
            self.setSortingEnabled(False)
        try:
            return fn(self, *args, **kwds)
        finally:
            if setSorting:
                self.setSortingEnabled(self._sorting)
                self._sorting = None

    return defersort


class TableWidget(QtGui.QTableWidget):
    """Extends QTableWidget with some useful functions for automatic data handling
    and copy / export context menu. Can automatically format and display a variety
    of data types (see :func:`setData() <pyqtgraph.TableWidget.setData>` for more
    information.
    """

    def __init__(self, *args, **kwds):
        """
        All positional arguments are passed to QTableWidget.__init__().

        ===================== =================================================
        **Keyword Arguments**
        editable              (bool) If True, cells in the table can be edited
                              by the user. Default is False.
        sortable              (bool) If True, the table may be soted by
                              clicking on column headers. Note that this also
                              causes rows to appear initially shuffled until
                              a sort column is selected. Default is True.
                              *(added in version 0.9.9)*
        ===================== =================================================
        """

        QtGui.QTableWidget.__init__(self, *args)

        self.itemClass = TableWidgetItem

        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QtGui.QAbstractItemView.SelectionMode.ContiguousSelection)
        self.setSizePolicy(QtGui.QSizePolicy.Policy.Preferred, QtGui.QSizePolicy.Policy.Preferred)
        self.clear()

        kwds.setdefault("sortable", True)
        kwds.setdefault("editable", False)
        self.setEditable(kwds.pop("editable"))
        self.setSortingEnabled(kwds.pop("sortable"))

        if len(kwds) > 0:
            raise TypeError("Invalid keyword arguments '%s'" % list(kwds.keys()))

        self._sorting = None  # used when temporarily disabling sorting

        self._formats = {None: None}  # stores per-column formats and entire table format
        self.sortModes = {}  # stores per-column sort mode

        self.itemChanged.connect(self.handleItemChanged)

        self.contextMenu = QtGui.QMenu()
        self.contextMenu.addAction(translate("TableWidget", "Copy Selection")).triggered.connect(
            self.copySel
        )
        self.contextMenu.addAction(translate("TableWidget", "Copy All")).triggered.connect(
            self.copyAll
        )
        self.contextMenu.addAction(translate("TableWidget", "Save Selection")).triggered.connect(
            self.saveSel
        )
        self.contextMenu.addAction(translate("TableWidget", "Save All")).triggered.connect(
            self.saveAll
        )

    def clear(self):
        """Clear all contents from the table."""
        QtGui.QTableWidget.clear(self)
        self.verticalHeadersSet = False
        self.horizontalHeadersSet = False
        self.items = []
        self.setRowCount(0)
        self.setColumnCount(0)
        self.sortModes = {}

    def setData(self, data):
        """Set the data displayed in the table.
        Allowed formats are:

          * numpy arrays
          * numpy record arrays
          * metaarrays
          * list-of-lists  [[1,2,3], [4,5,6]]
          * dict-of-lists  {'x': [1,2,3], 'y': [4,5,6]}
          * list-of-dicts  [{'x': 1, 'y': 4}, {'x': 2, 'y': 5}, ...]
        """
        self.clear()
        self.appendData(data)
        self.resizeColumnsToContents()

    @_defersort
    def appendData(self, data):
        """
        Add new rows to the table.

        See :func:`setData() <pyqtgraph.TableWidget.setData>` for accepted
        data types.
        """
        startRow = self.rowCount()

        fn0, header0 = self.iteratorFn(data)
        if fn0 is None:
            self.clear()
            return
        it0 = fn0(data)
        try:
            first = next(it0)
        except StopIteration:
            return
        fn1, header1 = self.iteratorFn(first)
        if fn1 is None:
            self.clear()
            return

        firstVals = [x for x in fn1(first)]
        self.setColumnCount(len(firstVals))

        if not self.verticalHeadersSet and header0 is not None:
            labels = [self.verticalHeaderItem(i).text() for i in range(self.rowCount())]
            self.setRowCount(startRow + len(header0))
            self.setVerticalHeaderLabels(labels + header0)
            self.verticalHeadersSet = True
        if not self.horizontalHeadersSet and header1 is not None:
            self.setHorizontalHeaderLabels(header1)
            self.horizontalHeadersSet = True

        i = startRow
        self.setRow(i, firstVals)
        for row in it0:
            i += 1
            self.setRow(i, [x for x in fn1(row)])

        if (
            self._sorting
            and self.horizontalHeadersSet
            and self.horizontalHeader().sortIndicatorSection() >= self.columnCount()
        ):
            self.sortByColumn(0, QtCore.Qt.SortOrder.AscendingOrder)

    def setEditable(self, editable=True):
        self.editable = editable
        for item in self.items:
            item.setEditable(editable)

    def setFormat(self, format, column=None):
        """
        Specify the default text formatting for the entire table, or for a
        single column if *column* is specified.

        If a string is specified, it is used as a format string for converting
        float values (and all other types are converted using str). If a
        function is specified, it will be called with the item as its only
        argument and must return a string. Setting format = None causes the
        default formatter to be used instead.

        Added in version 0.9.9.

        """
        if format is not None and not isinstance(format, str) and not callable(format):
            raise ValueError("Format argument must string, callable, or None. (got %s)" % format)

        self._formats[column] = format

        if column is None:
            # update format of all items that do not have a column format
            # specified
            for c in range(self.columnCount()):
                if self._formats.get(c, None) is None:
                    for r in range(self.rowCount()):
                        item = self.item(r, c)
                        if item is None:
                            continue
                        item.setFormat(format)
        else:
            # set all items in the column to use this format, or the default
            # table format if None was specified.
            if format is None:
                format = self._formats[None]
            for r in range(self.rowCount()):
                item = self.item(r, column)
                if item is None:
                    continue
                item.setFormat(format)

    def iteratorFn(self, data):
        ## Return 1) a function that will provide an iterator for data and 2) a list of header strings
        if isinstance(data, list) or isinstance(data, tuple):
            return lambda d: d.__iter__(), None
        elif isinstance(data, dict):
            return lambda d: iter(d.values()), list(map(str, data.keys()))
        elif hasattr(data, "implements") and data.implements("MetaArray"):
            if data.axisHasColumns(0):
                header = [str(data.columnName(0, i)) for i in range(data.shape[0])]
            elif data.axisHasValues(0):
                header = list(map(str, data.xvals(0)))
            else:
                header = None
            return self.iterFirstAxis, header
        elif isinstance(data, np.ndarray):
            return self.iterFirstAxis, None
        elif isinstance(data, np.void):
            return self.iterate, list(map(str, data.dtype.names))
        elif data is None:
            return (None, None)
        elif np.isscalar(data):
            return self.iterateScalar, None
        else:
            msg = "Don't know how to iterate over data type: {!s}".format(type(data))
            raise TypeError(msg)

    def iterFirstAxis(self, data):
        for i in range(data.shape[0]):
            yield data[i]

    def iterate(self, data):
        # for numpy.void, which can be iterated but mysteriously
        # has no __iter__ (??)
        for x in data:
            yield x

    def iterateScalar(self, data):
        yield data

    def appendRow(self, data):
        self.appendData([data])

    @_defersort
    def addRow(self, vals):
        row = self.rowCount()
        self.setRowCount(row + 1)
        self.setRow(row, vals)

    @_defersort
    def setRow(self, row, vals):
        if row > self.rowCount() - 1:
            self.setRowCount(row + 1)
        for col in range(len(vals)):
            val = vals[col]
            item = self.itemClass(val, row)
            item.setEditable(self.editable)
            sortMode = self.sortModes.get(col, None)
            if sortMode is not None:
                item.setSortMode(sortMode)
            format = self._formats.get(col, self._formats[None])
            item.setFormat(format)
            self.items.append(item)
            self.setItem(row, col, item)
            item.setValue(val)  # Required--the text-change callback is invoked
            # when we call setItem.

    def setSortMode(self, column, mode):
        """
        Set the mode used to sort *column*.

        ============== ========================================================
        **Sort Modes**
        value          Compares item.value if available; falls back to text
                       comparison.
        text           Compares item.text()
        index          Compares by the order in which items were inserted.
        ============== ========================================================

        Added in version 0.9.9
        """
        for r in range(self.rowCount()):
            item = self.item(r, column)
            if hasattr(item, "setSortMode"):
                item.setSortMode(mode)
        self.sortModes[column] = mode

    def sizeHint(self):
        # based on http://stackoverflow.com/a/7195443/54056
        width = sum(self.columnWidth(i) for i in range(self.columnCount()))
        width += self.verticalHeader().sizeHint().width()
        width += self.verticalScrollBar().sizeHint().width()
        width += self.frameWidth() * 2
        height = sum(self.rowHeight(i) for i in range(self.rowCount()))
        height += self.verticalHeader().sizeHint().height()
        height += self.horizontalScrollBar().sizeHint().height()
        return QtCore.QSize(width, height)

    def serialize(self, useSelection=False):
        """Convert entire table (or just selected area) into tab-separated text values"""
        if useSelection:
            selection = self.selectedRanges()[0]
            rows = list(range(selection.topRow(), selection.bottomRow() + 1))
            columns = list(range(selection.leftColumn(), selection.rightColumn() + 1))
        else:
            rows = list(range(self.rowCount()))
            columns = list(range(self.columnCount()))

        data = []
        if self.horizontalHeadersSet:
            row = []
            if self.verticalHeadersSet:
                row.append("")

            for c in columns:
                row.append(self.horizontalHeaderItem(c).text())
            data.append(row)

        for r in rows:
            row = []
            if self.verticalHeadersSet:
                row.append(self.verticalHeaderItem(r).text())
            for c in columns:
                item = self.item(r, c)
                if item is not None:
                    row.append(str(item.value))
                else:
                    row.append("")
            data.append(row)

        s = ""
        for row in data:
            s += "\t".join(row) + "\n"
        return s

    def copySel(self):
        """Copy selected data to clipboard."""
        QtGui.QApplication.clipboard().setText(self.serialize(useSelection=True))

    def copyAll(self):
        """Copy all data to clipboard."""
        QtGui.QApplication.clipboard().setText(self.serialize(useSelection=False))

    def saveSel(self):
        """Save selected data to file."""
        self.save(self.serialize(useSelection=True))

    def saveAll(self):
        """Save all data to file."""
        self.save(self.serialize(useSelection=False))

    def save(self, data):
        fileName = QtGui.QFileDialog.getSaveFileName(
            self,
            f"{translate('TableWidget', 'Save As')}...",
            "",
            f"{translate('TableWidget', 'Tab-separated values')} (*.tsv)",
        )
        if isinstance(fileName, tuple):
            fileName = fileName[0]  # Qt4/5 API difference
        if fileName == "":
            return
        with open(fileName, "w") as fd:
            fd.write(data)

    def contextMenuEvent(self, ev):
        self.contextMenu.popup(ev.globalPos())

    def keyPressEvent(self, ev):
        if ev.matches(QtGui.QKeySequence.StandardKey.Copy):
            ev.accept()
            self.copySel()
        else:
            super().keyPressEvent(ev)

    def handleItemChanged(self, item):
        item.itemChanged()


class TableWidgetItem(QtGui.QTableWidgetItem):
    def __init__(self, val, index, format=None):
        QtGui.QTableWidgetItem.__init__(self, "")
        self._blockValueChange = False
        self._format = None
        self._defaultFormat = "%0.3g"
        self.sortMode = "value"
        self.index = index
        flags = QtCore.Qt.ItemFlag.ItemIsSelectable | QtCore.Qt.ItemFlag.ItemIsEnabled
        self.setFlags(flags)
        self.setValue(val)
        self.setFormat(format)

    def setEditable(self, editable):
        """
        Set whether this item is user-editable.
        """
        if editable:
            self.setFlags(self.flags() | QtCore.Qt.ItemFlag.ItemIsEditable)
        else:
            self.setFlags(self.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)

    def setSortMode(self, mode):
        """
        Set the mode used to sort this item against others in its column.

        ============== ========================================================
        **Sort Modes**
        value          Compares item.value if available; falls back to text
                       comparison.
        text           Compares item.text()
        index          Compares by the order in which items were inserted.
        ============== ========================================================
        """
        modes = ("value", "text", "index", None)
        if mode not in modes:
            raise ValueError("Sort mode must be one of %s" % str(modes))
        self.sortMode = mode

    def setFormat(self, fmt):
        """Define the conversion from item value to displayed text.

        If a string is specified, it is used as a format string for converting
        float values (and all other types are converted using str). If a
        function is specified, it will be called with the item as its only
        argument and must return a string.

        Added in version 0.9.9.
        """
        if fmt is not None and not isinstance(fmt, str) and not callable(fmt):
            raise ValueError("Format argument must string, callable, or None. (got %s)" % fmt)
        self._format = fmt
        self._updateText()

    def _updateText(self):
        self._blockValueChange = True
        try:
            self._text = self.format()
            self.setText(self._text)
        finally:
            self._blockValueChange = False

    def setValue(self, value):
        self.value = value
        self._updateText()

    def itemChanged(self):
        """Called when the data of this item has changed."""
        if self.text() != self._text:
            self.textChanged()

    def textChanged(self):
        """Called when this item's text has changed for any reason."""
        self._text = self.text()

        if self._blockValueChange:
            # text change was result of value or format change; do not
            # propagate.
            return

        try:
            self.value = type(self.value)(self.text())
        except ValueError:
            self.value = str(self.text())

    def format(self):
        if callable(self._format):
            return self._format(self)
        if isinstance(self.value, (float, np.floating)):
            if self._format is None:
                return self._defaultFormat % self.value
            else:
                return self._format % self.value
        else:
            return str(self.value)

    def __lt__(self, other):
        if self.sortMode == "index" and hasattr(other, "index"):
            return self.index < other.index
        if self.sortMode == "value" and hasattr(other, "value"):
            return self.value < other.value
        else:
            return self.text() < other.text()
