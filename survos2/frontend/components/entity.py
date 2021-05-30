import numpy as np
import pandas as pd
import pyqtgraph as pg
import seaborn as sns
from loguru import logger
from qtpy import QtWidgets
from qtpy.QtCore import QSize, Signal

from survos2.entity.entities import make_entity_df
from survos2.server.state import cfg
from survos2.entity.sampler import crop_pts_bb

MAX_SIZE = 10000

def setup_entity_table(entities_fullname,
                    entities_df = None,
                    scale=1.0, 
                    offset=(0, 0, 0), 
                    crop_start=(0,0,0), 
                    crop_end=(MAX_SIZE,MAX_SIZE,MAX_SIZE)):
    if entities_df == None:
        print(f"Reading entity csv: {entities_fullname}")
        entities_df = pd.read_csv(entities_fullname)
        print(entities_df)


    #otherwise ignore filename
    index_column = len([col for col in entities_df.columns if "index" in col]) > 0
    print(index_column)
    entities_df.drop(
        entities_df.columns[entities_df.columns.str.contains("unnamed", case=False)],
        axis=1,
        inplace=True,
    )
    # entities_df.drop(
    #     entities_df.columns[entities_df.columns.str.contains("index", case=False)],
    #     axis=1,
    #     inplace=True,
    # )
    # class_code_column = (
    #     len([col for col in entities_df.columns if "class_code" in col]) > 0
    # )
    
    # if not class_code_column:
    #     entities_df["class_code"] = 0

    #cropped_pts = crop_pts_bb(np.array(entities_df), [crop_start[0],crop_end[0],crop_start[1], crop_end[1], crop_start[2], crop_end[2]])
    #print(cropped_pts)
    entities_df = make_entity_df(np.array(entities_df), flipxy=True)
    logger.debug(
        f"Loaded entities {entities_df.shape} applying scale {scale} and offset {offset} and crop start {crop_start}, crop_end {crop_end}"
    )
    tabledata = []
    entities_df["z"] = (entities_df["z"] * scale) + offset[0]
    entities_df["x"] = (entities_df["x"] * scale) + offset[1]
    entities_df["y"] = (entities_df["y"] * scale) + offset[2]

    print("-" * 100)

    if index_column:
        logger.debug("Loading pts")
        for i in range(len(entities_df)):
            entry = (
                i,  # entities_df.iloc[i]["index"],
                entities_df.iloc[i]["z"],
                entities_df.iloc[i]["x"],
                entities_df.iloc[i]["y"],
                0,
            )
            tabledata.append(entry)

    else:
        logger.debug("Loading entities")
        for i in range(len(entities_df)):
            entry = (
                i,
                entities_df.iloc[i]["z"],
                entities_df.iloc[i]["x"],
                entities_df.iloc[i]["y"],
                entities_df.iloc[i]["class_code"],
            )
            tabledata.append(entry)

    tabledata = np.array(
        tabledata,
        dtype=[
            ("index", int),
            ("z", float),
            ("x", float),
            ("y", float),
            ("class_code", int),
        ],
    )

    logger.debug(f"Loaded {len(tabledata)} entities.")
    return tabledata, entities_df


class SmallVolWidget:
    def __init__(self, smallvol):

        self.imv = pg.ImageView()
        self.imv.setImage(smallvol, xvals=np.linspace(1.0, 3.0, smallvol.shape[0]))

    def set_vol(self, smallvol):
        self.imv.setImage(smallvol, xvals=np.linspace(1.0, 3.0, smallvol.shape[0]))
        self.imv.jumpFrames(smallvol.shape[0] // 2)


# have to inherit from QGraphicsObject in order for signal to work
class TableWidget(QtWidgets.QGraphicsObject):
    clientEvent = Signal(object)

    def __init__(self):
        super().__init__()
        self.w = pg.TableWidget()

        self.w.show()
        self.w.resize(500, 500)
        self.w.setWindowTitle("Entity table")

        self.w.cellClicked.connect(self.cell_clicked)
        self.w.doubleClicked.connect(self.double_clicked)
        self.w.selected_row = 0

        stylesheet = "QHeaderView::section{Background-color:rgb(30,60,80)}"
        self.w.setStyleSheet(stylesheet)

    def set_data(self, data):
        self.w.setData(data)

    def double_clicked(self):
        cfg.ppw.clientEvent.emit(
            {"source": "table", "data": "show_roi", "selected_roi": self.w.selected_row}
        )

        for index in self.w.selectedIndexes():
            logger.debug(f"Retrieved item from table: {self.w.model().data(index)}")

    def cell_clicked(self, row, col):
        logger.debug("Row %d and Column %d was clicked" % (row, col))
        self.w.selected_row = row
