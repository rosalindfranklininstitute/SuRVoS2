import numpy as np
from loguru import logger
from survos2.api import workspace as ws
from survos2.data_io import dataset_from_uri
from survos2.improc.utils import DatasetManager
from survos2.model import DataModel
from survos2.api.annotations import (
    add_level,
    get_level,
    add_label,
    update_label,
    get_single_level,
    get_labels,
)

__export_fill__ = 0
__export_dtype__ = "float32"
__export_group__ = "roi"


from typing import List, Union
from fastapi import APIRouter, Query

roi = APIRouter()


@roi.get("/create")
def create(
    workspace: str,
    roi_fname: str,
    original_workspace: str,
    original_level: str,
    roi: List[int] = Query(),
):
    DataModel.g.current_workspace = original_workspace
    logger.debug(f"Original workspace: {original_workspace}")
    if original_level != "None":
        anno_ds = ws.get_dataset(
            original_workspace, original_level, group="annotations", session="default"
        )
        anno_ds_crop = anno_ds[roi[0] : roi[1], roi[2] : roi[3], roi[4] : roi[5]] & 15
        original_labels = get_labels(original_workspace, original_level)
    roi_dict = {}
    DataModel.g.current_workspace = workspace

    if original_level != "None":
        add_level(roi_fname)
        new_anno_ds = get_level(roi_fname, level="001_level")

    logger.debug(f"Switching to new workspace: {workspace}")
    src = DataModel.g.dataset_uri("__data__")
    with DatasetManager(src, out=None, dtype="float32", fillvalue=0) as DM:
        src_dataset = DM.sources[0]
        ds_metadata = src_dataset.get_metadata()

        if not "roi_fnames" in ds_metadata:
            src_dataset.set_metadata("roi_fnames", roi_dict)
        else:
            roi_dict = src_dataset.get_metadata("roi_fnames")
        num_entries = len(roi_dict.keys())
        roi_dict[num_entries + 1] = roi_fname
        src_dataset.set_metadata("roi_fnames", roi_dict)
        metadata = dict()
        metadata["id"] = len(roi_dict.keys())
        metadata["name"] = roi_fname

    if original_level != "None":
        label_values = np.unique(anno_ds_crop)

        for v in label_values:
            if v != 0:
                params = dict(
                    level="001_level",
                    idx=int(v) - 2,
                    name=str(int(v) - 2),
                    color="#11FF11",
                    workspace=True,
                )
                label_result = add_label(workspace=roi_fname, level="001_level")

        levels_result = get_single_level(roi_fname, level="001_level")

        cmap_colors = []
        for k, v in original_labels.items():
            cmap_colors.append(v["color"])

        for i, v in enumerate(levels_result["labels"].keys()):
            # label_rgb = (255 * label_rgb).astype(np.uint8)
            # label_hex = "#{:02x}{:02x}{:02x}".format(*label_rgb)
            label = dict(idx=int(v), name=str(int(v) - 1), color=cmap_colors[i])
            params = dict(level="001_level", workspace=roi_fname)
            label_result = update_label(**params, **label)

        new_anno_ds[:] = anno_ds_crop

    DataModel.g.current_workspace = original_workspace

    return metadata


@roi.get("/pull_anno")
def pull_anno(roi_fname: str, anno_id="001_level", target_anno_id="001_level"):
    logger.debug(f"{roi_fname} {anno_id}")
    ds = ws.get_dataset(roi_fname, anno_id, group="annotations", session="default")
    roi_parts = roi_fname.split("_")
    z_min = int(roi_parts[-6])
    z_max = int(roi_parts[-5])
    x_min = int(roi_parts[-4])
    x_max = int(roi_parts[-3])
    y_min = int(roi_parts[-2])
    y_max = int(roi_parts[-1])

    dst = DataModel.g.dataset_uri(target_anno_id, group="annotations")
    main_anno = dataset_from_uri(dst, mode="rw")
    main_anno[z_min:z_max, x_min:x_max, y_min:y_max] = ds[:]


@roi.get("/existing")
def existing():
    src = DataModel.g.dataset_uri("__data__")
    with DatasetManager(src, out=None, dtype="float32", fillvalue=0) as DM:
        src_dataset = DM.sources[0]
        ds_metadata = src_dataset.get_metadata()

        if not "roi_fnames" in ds_metadata:
            src_dataset.set_metadata("roi_fnames", {})
            return {}

        roi_fnames = ds_metadata["roi_fnames"]
        return roi_fnames


@roi.get("/remove")
def remove(workspace: str, roi_fname: str):
    src = DataModel.g.dataset_uri("__data__")
    with DatasetManager(src, out=None, dtype="float32", fillvalue=0) as DM:
        src_dataset = DM.sources[0]
        roi_fnames = src_dataset.get_metadata("roi_fnames")
        for k, v in roi_fnames.items():
            if v == roi_fname:
                selected = k
        del roi_fnames[selected]
        src_dataset.set_metadata("roi_fnames", roi_fnames)
