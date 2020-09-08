import os
import time
import numpy as np
import pandas as pd
import warnings
import matplotlib.pyplot as plt
from dataclasses import dataclass
from typing import NamedTuple, Tuple, Union, List
import itertools
from survos2.frontend.nb_utils import summary_stats
from survos2.entity.anno.geom import centroid_3d, rescale_3d

from loguru import logger


def centroid_to_bvol(cents, bvol_dim=(10, 10, 10)):
    bd, bw, bh = bvol_dim
    bvols = np.array(
        [
            (cz - bd, cx - bw, cy - bh, cz + bd, cx + bw, cy + bh)
            for cz, cx, cy, _ in cents
        ]
    )
    return bvols


def viz_bvols(input_array, bvols):
    bvol_mask = np.zeros_like(input_array)
    for bvol in bvols:
        bvol = bvol.astype(int)
        bvol_mask[bvol[0] : bvol[3], bvol[1] : bvol[4], bvol[2] : bvol[5]] = 1.0
    return bvol_mask


def sample_region_at_pt(img_volume, pt, dim):
    z, x, y = pt
    d, w, h = dim

    z_st = np.max((0, z - d))
    z_end = np.min((z + d, img_volume.shape[0]))
    x_st = np.max((0, x - w))
    x_end = np.min((x + w, img_volume.shape[1]))
    y_st = np.max((0, y - h))
    y_end = np.min((y + h, img_volume.shape[2]))

    return img_volume[z_st:z_end, x_st:x_end, y_st:y_end]


def sample_bvol(img_volume, bvol):
    z_st, z_end, x_st, x_end, y_st, y_end = bvol
    return img_volume[z_st:z_end, x_st:x_end, y_st:y_end]


def get_vol_in_cent_box(img_volume, z_st, z_end, x, y, w, h):
    return img_volume[z_st:z_end, x - w : x + w, y - h : y + h]


def sample_roi(img_vol, tabledata, i=0, vol_size=(32, 32, 32)):
    # Sampling ROI from an entity table
    print(f"Sampling from vol of shape {img_vol.shape}")
    pad_slice, pad_x, pad_y = np.array(vol_size) // 2

    z, x, y = tabledata["z"][i], tabledata["x"][i], tabledata["y"][i]
    logger.info(f"Sampling location {z} {x} {y}")
    # make a bv
    bb_zb = np.clip(int(z) - pad_slice, 0, img_vol.shape[0])
    bb_zt = np.clip(int(z) + pad_slice, 0, img_vol.shape[0])
    bb_xl = np.clip(int(x) - pad_slice, 0, img_vol.shape[1])
    bb_xr = np.clip(int(x) + pad_slice, 0, img_vol.shape[1])
    bb_yl = np.clip(int(y) - pad_slice, 0, img_vol.shape[2])
    bb_yr = np.clip(int(y) + pad_slice, 0, img_vol.shape[2])

    vol1 = get_vol_in_bbox(img_vol, bb_zb, bb_zt, bb_xl, bb_xr, bb_yl, bb_yr)

    print(f"Sampled vol of shape {vol1.shape}")
    if vol1.shape[0] == 0 or vol1.shape[1] == 0 or vol1.shape[2] == 0:
        vol1 = np.zeros(vol_size)
    return vol1


def get_vol_in_bbox(image_volume, slice_start, slice_end, xst, xend, yst, yend):
    return image_volume[slice_start:slice_end, xst:xend, yst:yend]


def get_centered_vol_in_bbox(image_volume, slice_start, slice_end, x, y, w, h):
    return image_volume[slice_start:slice_end, x - w : x + w, y - h : y + h]


def crop_vol_in_bbox(image_volume, slice_start, slice_end, x, y, w, h):
    return image_volume[slice_start:slice_end, x : x + w, y : y + h]


def get_centered_img_in_bbox(image_volume, sliceno, x, y, w, h):
    w = w // 2
    h = h // 2
    return image_volume[int(sliceno), x - w : x + w, y - h : y + h]


def get_img_in_bbox(image_volume, sliceno, x, y, w, h):
    return image_volume[int(sliceno), x - w : x + w, y - h : y + h]


@dataclass
class MarkedPatches:
    """Set of N patches, with associated per-patch 3d points
    There is also a per-patch location which is the location the patch was sampled from in the original volume.
    """

    vols: np.ndarray  # (N, Z, X, Y) image data within patch
    vols_pts: np.ndarray  # (N, Z, X, Y) cropped point geometry within patch
    vols_locs: np.ndarray  # (N, Z, X, Y, C) centroid location of patch and class code
    vols_bbs: np.ndarray  # (N, Z_start, Z_fin, X_start, X_fin, Y_start, Y_fin)bounding box for patch


# todo: list of patch sizes
# todo: pad
def sample_marked_patches(
    img_volume, locs, pts, patch_size=(32, 32, 32), debug_verbose=False
):
    """Samples a large image volume into a MarkedPatches object.
    Uses bounding volumes, and crops the image volume and associated geometry 
    into a list of cropped volumes and cropped geometry.

    Parameters
    ----------
    img_volume : {np.ndarray}
        image volume
    locs : {np.array of N x 4}
        N point locations, with a label in the final column
    pts : {np.array of P x k}
         point cloud of size P (the first 3 columns are used as the z,x,y coords)
    patch_size : {tuple, int x 3)
        -- Size of patch to sample (default: {(32,32,32)}), optional
    debug_verbose : bool, optional
        [description], by default False

    Returns
    -------
    MarkedPatches
        volumes with associated geometry
    """
    vols = []
    img_titles = []
    vols_pts = []
    vols_locs = []
    vols_bbs = []

    print(
        f"Generating {len(locs)} patch volumes from image of shape {img_volume.shape}"
    )

    for j in range(len(locs)):

        if locs[j].shape[0] == 4:
            sliceno, x, y, c = locs[j]
        else:
            sliceno, x, y = locs[j]

        d, w, h = patch_size

        w = w // 2
        h = h // 2

        x = int(np.ceil(x))  # why take np.ceil???
        y = int(np.ceil(y))

        sliceno = int(sliceno)

        slice_start = np.max([0, sliceno - np.int(patch_size[0] / 2.0)])

        slice_end = np.min([sliceno + np.int(patch_size[0] / 2.0), img_volume.shape[0]])

        out_of_bounds = np.unique(
            np.hstack(
                (
                    np.where(pts[:, 1] <= x - w)[0],
                    np.where(pts[:, 1] >= x + w)[0],
                    np.where(pts[:, 2] <= y - h)[0],
                    np.where(pts[:, 2] >= y + h)[0],
                    np.where(pts[:, 0] <= slice_start)[0],
                    np.where(pts[:, 0] >= slice_end)[0],
                )
            )
        )

        pts_c = pts.copy()
        vol_pts = np.delete(pts_c, out_of_bounds, axis=0)

        if debug_verbose:
            print("Shape of original pt data {}".format(pts.shape))
            print("Number of out of bounds pts: {}".format(out_of_bounds.shape))

        img = get_centered_vol_in_bbox(img_volume, slice_start, slice_end, y, x, h, w)

        print(vol_pts.shape)
        if img.shape == patch_size:
            vols.append(img)
            vols_pts.append(
                [vol_pts[0, 0], vol_pts[0, 1], vol_pts[0, 2], vol_pts[0, 3]]
            )
            vols_bbs.append([slice_start, slice_end, x - w, x + w, y - h, y + h])
            vols_locs.append(locs[j])

    vols = np.array(vols)
    vols_pts = np.array(vols_pts)
    vols_bbs = np.array(vols_bbs)
    vols_locs = np.array(vols_locs)

    marked_patches = MarkedPatches(vols, vols_pts, vols_locs, vols_bbs)
    print(f"Generated {len(locs)} MarkedPatches from image of shape {vols.shape}")

    return marked_patches


def crop_vol_and_pts_bb(
    img_volume, pts, bounding_box, debug_verbose=False, offset=False
):

    # TODO: clip bbox to img_volume
    z_st, z_end, x_st, x_end, y_st, y_end = bvol

    out_of_bounds_w = np.hstack(
        (
            np.where(pts[:, 0] <= z_st)[0],
            np.where(pts[:, 0] >= z_end)[0],
            np.where(pts[:, 1] >= x_st)[0],
            np.where(pts[:, 1] <= x_end)[0],
            np.where(pts[:, 2] >= z_st)[0],
            np.where(pts[:, 2] <= z_end)[0],
        )
    )

    cropped_pts = np.array(np.delete(pts, out_of_bounds_w, axis=0))

    if offset:
        cropped_pts[:, 0] = cropped_pts[:, 0] - location[0]
        cropped_pts[:, 1] = cropped_pts[:, 1] - location[1]
        cropped_pts[:, 2] = cropped_pts[:, 2] - location[2]

    if debug_verbose:
        print(
            "\n z x y w h: {}".format(
                (location[0], location[1], location[2], patch_size[1], patch_size[2])
            )
        )
        print("Slice start, slice end {} {}".format(slice_start, slice_end))
        print("Cropped points array shape: {}".format(cropped_pts.shape))

    img = sample_bvol(img_volume, bounding_box)

    return img, cropped_pts


# old
def crop_vol_and_pts_centered(
    img_volume,
    pts,
    location=(60, 700, 700),
    patch_size=(40, 300, 300),
    debug_verbose=False,
    offset=False,
):
    patch_size = np.array(patch_size).astype(np.uint32)
    location = np.array(location).astype(np.uint32)

    # z, x_bl, x_ur, y_bl, y_ur = location[0], location[1], location[1]+patch_size[1], location[2], location[2]+patch_size[2]

    slice_start = np.max([0, location[0]])
    slice_end = np.min([location[0] + patch_size[0], img_volume.shape[0]])

    out_of_bounds_w = np.hstack(
        (
            np.where(pts[:, 2] >= location[2] + patch_size[2])[0],
            np.where(pts[:, 2] <= location[2])[0],
            np.where(pts[:, 1] >= location[1] + patch_size[1])[0],
            np.where(pts[:, 1] <= location[1])[0],
            np.where(pts[:, 0] <= location[0])[0],
            np.where(pts[:, 0] >= location[0] + patch_size[0])[0],
        )
    )

    cropped_pts = np.array(np.delete(pts, out_of_bounds_w, axis=0))

    if offset:

        cropped_pts[:, 0] = cropped_pts[:, 0] - location[0]
        cropped_pts[:, 1] = cropped_pts[:, 1] - location[1]
        cropped_pts[:, 2] = cropped_pts[:, 2] - location[2]

    if debug_verbose:
        print(
            "\n z x y w h: {}".format(
                (location[0], location[1], location[2], patch_size[1], patch_size[2])
            )
        )
        print("Slice start, slice end {} {}".format(slice_start, slice_end))
        print("Cropped points array shape: {}".format(cropped_pts.shape))

    img = crop_vol_in_bbox(
        img_volume,
        slice_start,
        slice_end,
        location[2],
        location[1],
        patch_size[2],
        patch_size[1],
    )

    return img, cropped_pts


def sample_patch_slices(img_vol, entities_df):
    entities_locs = np.array(entities_df[["slice", "x", "y"]])
    vol_list, vol_locs, vol_pts = sample_patch_locs(
        img_vol, entities_locs, entities_locs, patch_size=(64, 64, 64)
    )
    slice_list = np.array([v[vol_list[0].shape[0] // 2, :, :] for v in vol_list])

    print(f"Generated slice {slice_list.shape}")

    return slice_list, vol_pts


def gather_single_class(img_vol, entities_locs, class_code, patch_size=(64, 64, 64)):
    entities_locs_singleclass = entities_locs.loc[
        entities_locs["class_code"].isin([class_code])
    ]
    entities_locs_singleclass = np.array(entities_locs_singleclass[["slice", "x", "y"]])

    return sample_patch_locs(
        img_vol,
        entities_locs_singleclass,
        entities_locs_singleclass,
        patch_size=patch_size,
    )


def sample_patch2d(img_volume, pts, patch_size=(40, 40)):
    img_shortlist = []
    img_titles = []

    print(f"Sampling {len(pts)} pts from image volume of shape {img_volume.shape}")

    for j in range(len(pts)):
        sliceno, y, x = pts[j]
        w, h = patch_size
        img = get_centered_img_in_bbox(
            img_volume, sliceno, int(np.ceil(x)), int(np.ceil(y)), w, h
        )
        img_shortlist.append(img)
        img_titles.append(str(int(x)) + "_" + str(int(y)) + "_" + str(sliceno))

    return img_shortlist, img_titles
