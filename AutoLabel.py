#!/usr/bin/env python
# -*- coding: utf-8 -*-
# 准备只将原来的界面布局改掉，但发现比较困难
# pyrcc5 -o libs/resources.py resources.qrc
import argparse
import ast
import codecs
import os.path
import platform
import subprocess
import sys
from functools import partial
from collections import defaultdict
import json
from win32com.shell import shell,shellcon



# 整个项目放在PaddleOCR/tools目录下
__dir__ = os.path.dirname(os.path.abspath(__file__))
sys.path.append(__dir__)
sys.path.append(os.path.abspath(os.path.join(__dir__, '../..')))

from paddleocr_new import PaddleOCR

try:
    from PyQt5 import QtCore, QtGui, QtWidgets
    from PyQt5.QtGui import *
    from PyQt5.QtCore import *
    from PyQt5.QtWidgets import *
except ImportError:
    # needed for py3+qt4
    # Ref:
    # http://pyqt.sourceforge.net/Docs/PyQt4/incompatible_apis.html
    # http://stackoverflow.com/questions/21217399/pyqt4-qtcore-qvariant-object-instead-of-a-string
    if sys.version_info.major >= 3:
        import sip

        sip.setapi('QVariant', 2)
    from PyQt4.QtGui import *
    from PyQt4.QtCore import *

from combobox import ComboBox
from libs.constants import *
from libs.utils import *
from libs.settings import Settings
from libs.shape import Shape, DEFAULT_LINE_COLOR, DEFAULT_FILL_COLOR
from libs.stringBundle import StringBundle
from libs.canvas import Canvas
from libs.zoomWidget import ZoomWidget
from libs.autoDialog import AutoDialog
from libs.labelDialog import LabelDialog
from libs.colorDialog import ColorDialog
from libs.labelFile import LabelFile, LabelFileError, LabelFileFormat
from libs.toolBar import ToolBar
from libs.pascal_voc_io import PascalVocReader
from libs.pascal_voc_io import XML_EXT
from libs.yolo_io import YoloReader
from libs.yolo_io import TXT_EXT
from libs.ustr import ustr
from libs.hashableQListWidgetItem import HashableQListWidgetItem

__appname__ = 'AutoLabel'


class WindowMixin(object):

    def menu(self, title, actions=None):
        menu = self.menuBar().addMenu(title)
        if actions:
            addActions(menu, actions)
        return menu

    def toolbar(self, title, actions=None):  # 顶和底层
        toolbar = ToolBar(title)
        toolbar.setObjectName(u'%sToolBar' % title)
        # toolbar.setOrientation(Qt.Vertical)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        if actions:
            addActions(toolbar, actions)
        self.addToolBar(Qt.LeftToolBarArea, toolbar)
        return toolbar


class MainWindow(QMainWindow, WindowMixin):
    FIT_WINDOW, FIT_WIDTH, MANUAL_ZOOM = list(range(3))

    def __init__(self, defaultFilename=None, defaultPrefdefClassFile=None, defaultSaveDir=None):
        super(MainWindow, self).__init__()
        self.setWindowTitle(__appname__)

        # Load setting in the main thread
        self.settings = Settings()
        self.settings.load()  # 读入现有的设置
        settings = self.settings

        # Load string bundle for i18n
        self.stringBundle = StringBundle.getBundle(localeStr="zh-CN") # 'en'
        getStr = lambda strId: self.stringBundle.getString(strId)

        self.defaultSaveDir = defaultSaveDir
        self.ocr = PaddleOCR(use_pdserving=False, use_angle_cls=True, det=True, cls=True, lang="ch")
        # 先启动一次模型
        if os.path.exists('./paddle.png'):
            result = self.ocr.ocr('./paddle.png', cls=True, det=True)

        # For loading all image under a directory
        self.mImgList = []
        self.mImgList5 = []
        self.dirname = None
        self.labelHist = []
        self.lastOpenDir = None
        self.result_dic = []

        self.changeFileFolder = False
        self.haveAutoReced = False
        self.labelFile = None
        self.currIndex = 0


        # Whether we need to save or not.
        self.dirty = False

        self._noSelectionSlot = False
        self._beginner = True
        self.screencastViewer = self.getAvailableScreencastViewer()
        self.screencast = "https://github.com/PaddlePaddle/PaddleOCR"

        # Load predefined classes to the list
        self.loadPredefinedClasses(defaultPrefdefClassFile)

        # Main widgets and related state.
        self.labelDialog = LabelDialog(parent=self, listItem=self.labelHist)
        self.autoDialog = AutoDialog(parent=self)

        self.itemsToShapes = {}
        self.shapesToItems = {}
        self.itemsToShapesbox = {}
        self.shapesToItemsbox = {}
        self.prevLabelText = getStr('tempLabel')
        self.model = 'paddle'
        self.PPreader = None
        self.autoSaveNum = 10

        ################# 文件列表  ###############
        self.fileListWidget = QListWidget()
        self.fileListWidget.itemClicked.connect(self.fileitemDoubleClicked)
        self.fileListWidget.setIconSize(QSize(25, 25))
        filelistLayout = QVBoxLayout()
        filelistLayout.setContentsMargins(0, 0, 0, 0)
        filelistLayout.addWidget(self.fileListWidget)
        
        self.AutoRecognition = QToolButton()
        self.AutoRecognition.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.AutoRecognition.setIcon(newIcon('Auto'))
        # self.AutoRecognition.setIconSize(QSize(100,20))
        self.AutoRecognition.setFixedSize(QSize(80,30))
        # self.AutoRecognition.setStyleSheet('text-align:center;')#border:none;font-size : 12pt;
        autoRecLayout = QHBoxLayout()
        autoRecLayout.setContentsMargins(0, 0, 0, 0)
        autoRecLayout.addWidget(self.AutoRecognition)
        autoRecContainer = QWidget()
        autoRecContainer.setLayout(autoRecLayout)
        filelistLayout.addWidget(autoRecContainer)

        fileListContainer = QWidget()
        fileListContainer.setLayout(filelistLayout)
        self.filedock = QDockWidget(getStr('fileList'), self)
        self.filedock.setObjectName(getStr('files'))
        self.filedock.setWidget(fileListContainer)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.filedock)
        
        ######## Right area ##########
        listLayout = QVBoxLayout()
        listLayout.setContentsMargins(0, 0, 0, 0)

        # Create a widget for edit and diffc button
        self.diffcButton = QCheckBox(getStr('useDifficult'))
        self.diffcButton.setChecked(False)
        self.diffcButton.stateChanged.connect(self.btnstate)
        self.editButton = QToolButton()
        self.reRecogButton = QToolButton()
        self.reRecogButton.setIcon(newIcon('reRec', 30))
        self.reRecogButton.setFixedSize(QSize(80,30))
        self.reRecogButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.comboBox = QtWidgets.QComboBox()
        self.comboBox.setObjectName("comboBox")        
        self.comboBox.addItems(['server','mobile','slim'])
        self.comboBox.currentIndexChanged.connect(self.seclectmodel)
        # 模型选择下拉框


        # self.reRecogButton.setIcon(newIcon())
        # 增加一个新建框？或直接将下面的按钮移动到上面
        self.newButton = QToolButton()
        self.newButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.newButton.setFixedSize(QSize(80, 30))
        self.SaveButton = QToolButton()
        self.SaveButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.SaveButton.setFixedSize(QSize(60, 30))
        self.DelButton = QToolButton()
        self.DelButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.DelButton.setFixedSize(QSize(80, 30))


        lefttoptoolbox = QHBoxLayout()
        lefttoptoolbox.addWidget(self.newButton)
        lefttoptoolbox.addWidget(self.reRecogButton)
        lefttoptoolbox.addWidget(self.comboBox)
        lefttoptoolboxcontainer = QWidget()
        lefttoptoolboxcontainer.setLayout(lefttoptoolbox)
        listLayout.addWidget(lefttoptoolboxcontainer)


        ################## label list ####################
        # Create and add a widget for showing current label items
        self.labelList = QListWidget()
        labelListContainer = QWidget()
        labelListContainer.setLayout(listLayout)
        self.labelList.itemActivated.connect(self.labelSelectionChanged)
        self.labelList.itemSelectionChanged.connect(self.labelSelectionChanged)
        self.labelList.itemDoubleClicked.connect(self.editLabel)
        # Connect to itemChanged to detect checkbox changes.
        self.labelList.itemChanged.connect(self.labelItemChanged)
        self.labelListDock = QDockWidget(getStr('recognitionResult'),self)
        self.labelListDock.setWidget(self.labelList)
        self.labelListDock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        listLayout.addWidget(self.labelListDock)

        ################## detection box ####################
        self.BoxList = QListWidget()

        self.BoxList.itemActivated.connect(self.boxSelectionChanged)
        self.BoxList.itemSelectionChanged.connect(self.boxSelectionChanged)
        self.BoxList.itemDoubleClicked.connect(self.editBox)
        # Connect to itemChanged to detect checkbox changes.
        self.BoxList.itemChanged.connect(self.boxItemChanged)
        self.BoxListDock = QDockWidget(getStr('detectionBoxposition'), self)
        self.BoxListDock.setWidget(self.BoxList)
        self.BoxListDock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        listLayout.addWidget(self.BoxListDock)

        ############ lower right area ############
        leftbtmtoolbox = QHBoxLayout()
        leftbtmtoolbox.addWidget(self.SaveButton)
        leftbtmtoolbox.addWidget(self.DelButton)
        leftbtmtoolboxcontainer = QWidget()
        leftbtmtoolboxcontainer.setLayout(leftbtmtoolbox)
        listLayout.addWidget(leftbtmtoolboxcontainer)

        self.dock = QDockWidget(getStr('boxLabelText'), self)
        self.dock.setObjectName(getStr('labels'))
        self.dock.setWidget(labelListContainer)


        ########## zoom bar #########
        self.imgsplider = QSlider(Qt.Horizontal)
        self.imgsplider.valueChanged.connect(self.CanvasSizeChange)
        self.imgsplider.setMinimum(-150)
        self.imgsplider.setMaximum(150)
        self.imgsplider.setSingleStep(1)
        self.imgsplider.setTickPosition(QSlider.TicksBelow)
        self.imgsplider.setTickInterval(1) 
        op = QGraphicsOpacityEffect()
        op.setOpacity(0.2)
        self.imgsplider.setGraphicsEffect(op)
        # self.imgsplider.setAttribute(Qt.WA_TranslucentBackground)
        self.imgsplider.setStyleSheet("background-color:transparent")
        self.imgsliderDock = QDockWidget(getStr('ImageResize'), self)
        self.imgsliderDock.setObjectName(getStr('IR'))
        self.imgsliderDock.setWidget(self.imgsplider)
        self.imgsliderDock.setFeatures(QDockWidget.DockWidgetFloatable)
        # op = QGraphicsOpacityEffect()
        # op.setOpacity(0.2)
        # self.imgsliderDock.setGraphicsEffect(op)
        self.imgsliderDock.setAttribute(Qt.WA_TranslucentBackground)
        self.addDockWidget(Qt.RightDockWidgetArea, self.imgsliderDock)

        self.zoomWidget = ZoomWidget()
        self.colorDialog = ColorDialog(parent=self)
        self.zoomWidgetValue = self.zoomWidget.value()
        
        ########## thumbnail #########
        hlayout = QHBoxLayout()
        m = (0, 0, 0, 0)
        hlayout.setSpacing(0)
        hlayout.setContentsMargins(*m)
        self.preButton = QToolButton()
        # self.preButton.setFixedHeight(100)
        # self.preButton.setText(getStr("prevImg"))
        self.preButton.setIcon(newIcon("prev",40))
        self.preButton.setIconSize(QSize(40, 100))
        self.preButton.clicked.connect(self.openPrevImg)
        # self.preButton.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        self.preButton.setStyleSheet('border: none;')
        self.iconlist = QListWidget()
        self.iconlist.setViewMode(QListView.IconMode)
        self.iconlist.setFlow(QListView.TopToBottom)
        self.iconlist.setSpacing(10)
        self.iconlist.setIconSize(QSize(50, 50))
        self.iconlist.setMovement(False)
        self.iconlist.setResizeMode(QListView.Adjust)
        # self.iconlist.itemDoubleClicked.connect(self.iconitemDoubleClicked)
        self.iconlist.itemClicked.connect(self.iconitemDoubleClicked)
        self.iconlist.setStyleSheet("background-color:transparent; border: none;")
        # self.iconlist.setStyleSheet('border: none;')
        self.nextButton = QToolButton()
        # self.nextButton.setFixedHeight(100)
        # self.nextButton.setText(getStr("nextImg"))
        self.nextButton.setIcon(newIcon("next", 40))
        self.nextButton.setIconSize(QSize(40, 100))
        self.nextButton.setStyleSheet('border: none;')
        self.nextButton.clicked.connect(self.openNextImg)
        # self.nextButton.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        
        hlayout.addWidget(self.preButton)
        hlayout.addWidget(self.iconlist)
        hlayout.addWidget(self.nextButton)

        # self.setLayout(hlayout)

        iconListContainer = QWidget()
        iconListContainer.setLayout(hlayout)
        iconListContainer.setFixedHeight(100)
        iconListContainer.setFixedWidth(530)
        # op = QGraphicsOpacityEffect()
        # op.setOpacity(0.5)
        # iconListContainer.setGraphicsEffect(op)
        
        ########### Canvas ###########
        self.canvas = Canvas(parent=self)
        self.canvas.zoomRequest.connect(self.zoomRequest)
        self.canvas.setDrawingShapeToSquare(settings.get(SETTING_DRAW_SQUARE, False))

        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(True)
        self.scrollBars = {
            Qt.Vertical: scroll.verticalScrollBar(),
            Qt.Horizontal: scroll.horizontalScrollBar()
        }
        self.scrollArea = scroll
        self.canvas.scrollRequest.connect(self.scrollRequest)

        self.canvas.newShape.connect(self.newShape)
        self.canvas.shapeMoved.connect(self.updateBoxlist)  # self.setDirty
        self.canvas.selectionChanged.connect(self.shapeSelectionChanged)
        self.canvas.drawingPolygon.connect(self.toggleDrawingSensitive)

        centerLayout = QVBoxLayout()
        centerLayout.setContentsMargins(0, 0, 0, 0)
        centerLayout.addWidget(scroll)
        #centerLayout.addWidget(self.icondock)
        centerLayout.addWidget(iconListContainer,0,Qt.AlignCenter)
        centercontainer = QWidget()
        centercontainer.setLayout(centerLayout)

        # self.scrolldock = QDockWidget('WorkSpace',self)
        # self.scrolldock.setObjectName('WorkSpace')
        # self.scrolldock.setWidget(centercontainer)
        # self.scrolldock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        # orititle = self.scrolldock.titleBarWidget()
        # tmpwidget = QWidget()
        # self.scrolldock.setTitleBarWidget(tmpwidget)
        # del orititle
        self.setCentralWidget(centercontainer) #self.scrolldock
        self.addDockWidget(Qt.RightDockWidgetArea, self.dock)


        # self.filedock.setFeatures(QDockWidget.DockWidgetFloatable)
        self.filedock.setFeatures(self.filedock.features() ^ QDockWidget.DockWidgetFloatable)

        self.dockFeatures = QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetFloatable
        self.dock.setFeatures(self.dock.features() ^ self.dockFeatures)

        self.filedock.setFeatures(QDockWidget.NoDockWidgetFeatures)

        ###### Actions #######
        action = partial(newAction, self)
        quit = action(getStr('quit'), self.close,
                      'Ctrl+Q', 'quit', getStr('quitApp'))

        open = action(getStr('openFile'), self.openFile,
                      'Ctrl+O', 'open', getStr('openFileDetail'))

        opendir = action(getStr('openDir'), self.openDirDialog,
                         'Ctrl+u', 'open', getStr('openDir'))

        openNextImg = action(getStr('nextImg'), self.openNextImg,
                             'd', 'next', getStr('nextImgDetail'))

        openPrevImg = action(getStr('prevImg'), self.openPrevImg,
                             'a', 'prev', getStr('prevImgDetail'))

        verify = action(getStr('verifyImg'), self.verifyImg,
                        'space', 'verify', getStr('verifyImgDetail'))

        save = action(getStr('save'), self.saveFile,
                      'Ctrl+S', 'save', getStr('saveDetail'), enabled=False)

        alcm = action(getStr('choosemodel'), self.autolcm,
                                        'Ctrl+M', 'next', getStr('tipchoosemodel'))

        deleteImg = action(getStr('deleteImg'), self.deleteImg, 'Ctrl+D', 'close', getStr('deleteImgDetail'),
                           enabled=True)

        resetAll = action(getStr('resetAll'), self.resetAll, None, 'resetall', getStr('resetAllDetail'))

        color1 = action(getStr('boxLineColor'), self.chooseColor1,
                        'Ctrl+L', 'color_line', getStr('boxLineColorDetail'))

        createMode = action(getStr('crtBox'), self.setCreateMode,
                            'w', 'new', getStr('crtBoxDetail'), enabled=False)
        editMode = action('&Edit\nRectBox', self.setEditMode,
                          'Ctrl+J', 'edit', u'Move and edit Boxs', enabled=False)

        create = action(getStr('crtBox'), self.createShape,
                        'w', 'new', getStr('crtBoxDetail'), enabled=False)

        delete = action(getStr('delBox'), self.deleteSelectedShape,
                        'Delete', 'delete', getStr('delBoxDetail'), enabled=False)
        copy = action(getStr('dupBox'), self.copySelectedShape,
                      'Ctrl+D', 'copy', getStr('dupBoxDetail'),
                      enabled=False)

        hideAll = action('&Hide\nRectBox', partial(self.togglePolygons, False),
                         'Ctrl+H', 'hide', getStr('hideAllBoxDetail'),
                         enabled=False)
        showAll = action('&Show\nRectBox', partial(self.togglePolygons, True),
                         'Ctrl+A', 'hide', getStr('showAllBoxDetail'),
                         enabled=False)

        help = action(getStr('tutorial'), self.showTutorialDialog, None, 'help', getStr('tutorialDetail'))
        showInfo = action(getStr('info'), self.showInfoDialog, None, 'help', getStr('info'))

        zoom = QWidgetAction(self)
        zoom.setDefaultWidget(self.zoomWidget)
        self.zoomWidget.setWhatsThis(
            u"Zoom in or out of the image. Also accessible with"
            " %s and %s from the canvas." % (fmtShortcut("Ctrl+[-+]"),
                                             fmtShortcut("Ctrl+Wheel")))
        self.zoomWidget.setEnabled(False)

        zoomIn = action(getStr('zoomin'), partial(self.addZoom, 10),
                        'Ctrl++', 'zoom-in', getStr('zoominDetail'), enabled=False)
        zoomOut = action(getStr('zoomout'), partial(self.addZoom, -10),
                         'Ctrl+-', 'zoom-out', getStr('zoomoutDetail'), enabled=False)
        zoomOrg = action(getStr('originalsize'), partial(self.setZoom, 100),
                         'Ctrl+=', 'zoom', getStr('originalsizeDetail'), enabled=False)
        fitWindow = action(getStr('fitWin'), self.setFitWindow,
                           'Ctrl+F', 'fit-window', getStr('fitWinDetail'),
                           checkable=True, enabled=False)
        fitWidth = action(getStr('fitWidth'), self.setFitWidth,
                          'Ctrl+Shift+F', 'fit-width', getStr('fitWidthDetail'),
                          checkable=True, enabled=False)
        # Group zoom controls into a list for easier toggling.
        zoomActions = (self.zoomWidget, zoomIn, zoomOut,
                       zoomOrg, fitWindow, fitWidth)
        self.zoomMode = self.MANUAL_ZOOM
        self.scalers = {
            self.FIT_WINDOW: self.scaleFitWindow,
            self.FIT_WIDTH: self.scaleFitWidth,
            # Set to one to scale to 100% when loading files.
            self.MANUAL_ZOOM: lambda: 1,
        }

        edit = action(getStr('editLabel'), self.editLabel,
                      'Ctrl+E', 'edit', getStr('editLabelDetail'),
                      enabled=False)

        ######## New actions #######
        AutoRec = action(getStr('autoRecognition'), self.autoRecognition,
                      'Ctrl+Shif+A', 'Auto', getStr('autoRecognition'), enabled=False)

        reRec = action(getStr('reRecognition'), self.reRecognition, 
                      'Ctrl+Shif+R', 'reRec', getStr('reRecognition'), enabled=False)

        createpoly = action(getStr('creatPolygon'), self.createPolygon,
                            'p', 'new', 'Creat Polygon', enabled=True)

        saveRec = action(getStr('saveRec'), self.saveRecResult,
                            '', 'saveRec', getStr('saveRec'), enabled=False)

        self.editButton.setDefaultAction(edit)
        self.newButton.setDefaultAction(create)
        self.DelButton.setDefaultAction(deleteImg)
        self.SaveButton.setDefaultAction(save)
        self.AutoRecognition.setDefaultAction(AutoRec)
        self.reRecogButton.setDefaultAction(reRec)
        # self.preButton.setDefaultAction(openPrevImg)
        # self.nextButton.setDefaultAction(openNextImg)

        ############# Zoom layout ##############
        zoomLayout = QHBoxLayout()
        zoomLayout.addStretch()
        self.zoominButton = QToolButton()
        self.zoominButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.zoominButton.setDefaultAction(zoomIn)
        self.zoomoutButton = QToolButton()
        self.zoomoutButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.zoomoutButton.setDefaultAction(zoomOut)
        self.zoomorgButton = QToolButton()
        self.zoomorgButton.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.zoomorgButton.setDefaultAction(zoomOrg)
        zoomLayout.addWidget(self.zoominButton)
        zoomLayout.addWidget(self.zoomorgButton)
        zoomLayout.addWidget(self.zoomoutButton)

        zoomContainer = QWidget()
        zoomContainer.setLayout(zoomLayout)
        zoomContainer.setGeometry(0, 0, 30, 150)

        shapeLineColor = action(getStr('shapeLineColor'), self.chshapeLineColor,
                                icon='color_line', tip=getStr('shapeLineColorDetail'),
                                enabled=False)
        shapeFillColor = action(getStr('shapeFillColor'), self.chshapeFillColor,
                                icon='color', tip=getStr('shapeFillColorDetail'),
                                enabled=False)

        labels = self.dock.toggleViewAction()
        labels.setText(getStr('showHide'))
        labels.setShortcut('Ctrl+Shift+L')

        # Label list context menu.
        labelMenu = QMenu()
        addActions(labelMenu, (edit, delete))

        self.labelList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.labelList.customContextMenuRequested.connect(
            self.popLabelListMenu)

        # Draw squares/rectangles
        self.drawSquaresOption = QAction(getStr('drawSquares'), self)
        self.drawSquaresOption.setShortcut('Ctrl+Shift+R')
        self.drawSquaresOption.setCheckable(True)
        self.drawSquaresOption.setChecked(settings.get(SETTING_DRAW_SQUARE, False))
        self.drawSquaresOption.triggered.connect(self.toogleDrawSquare)

        # Store actions for further handling.
        self.actions = struct(save=save,  open=open,  resetAll=resetAll, deleteImg=deleteImg,
                              lineColor=color1, create=create, delete=delete, edit=edit, copy=copy,
                              saveRec=saveRec,
                              createMode=createMode, editMode=editMode,
                              shapeLineColor=shapeLineColor, shapeFillColor=shapeFillColor,
                              zoom=zoom, zoomIn=zoomIn, zoomOut=zoomOut, zoomOrg=zoomOrg,
                              fitWindow=fitWindow, fitWidth=fitWidth,
                              zoomActions=zoomActions,
                              fileMenuActions=(
                                  open, opendir, save,  resetAll, quit),
                              beginner=(), advanced=(),
                              editMenu=(createpoly, edit, copy, delete,
                                        None, color1, self.drawSquaresOption),
                              beginnerContext=(create, edit, copy, delete),
                              advancedContext=(createMode, editMode, edit, copy,
                                               delete, shapeLineColor, shapeFillColor),
                              onLoadActive=(
                                   create, createMode, editMode),
                              onShapesPresent=(hideAll, showAll))

        # menus
        self.menus = struct(
            file=self.menu('&'+getStr('mfile')),
            edit=self.menu('&'+getStr('medit')),
            view=self.menu('&'+getStr('mview')),
            autolabel=self.menu('&PaddleOCR'),
            help=self.menu('&'+getStr('mhelp')),
            recentFiles=QMenu('Open &Recent'),
            labelList=labelMenu)


        # Sync single class mode from PR#106
        self.singleClassMode = QAction(getStr('singleClsMode'), self)
        self.singleClassMode.setShortcut("Ctrl+Shift+S")
        self.singleClassMode.setCheckable(True)
        self.singleClassMode.setChecked(settings.get(SETTING_SINGLE_CLASS, False))
        self.lastLabel = None
        # Add option to enable/disable labels being displayed at the top of bounding boxes
        self.displayLabelOption = QAction(getStr('displayLabel'), self)
        self.displayLabelOption.setShortcut("Ctrl+Shift+P")
        self.displayLabelOption.setCheckable(True)
        self.displayLabelOption.setChecked(settings.get(SETTING_PAINT_LABEL, False))
        self.displayLabelOption.triggered.connect(self.togglePaintLabelsOption)

        addActions(self.menus.file,
                   (opendir, None, save,  resetAll, deleteImg, quit))

        addActions(self.menus.help, (help, showInfo))
        addActions(self.menus.view, (
            self.displayLabelOption,
            labels, None,
            hideAll, showAll, None,
            zoomIn, zoomOut, zoomOrg, None,
            fitWindow, fitWidth))

        addActions(self.menus.autolabel, (alcm, saveRec, None, help))

        self.menus.file.aboutToShow.connect(self.updateFileMenu)

        # Custom context menu for the canvas widget:
        addActions(self.canvas.menus[0], self.actions.beginnerContext)
        addActions(self.canvas.menus[1], (
            action('&Copy here', self.copyShape),
            action('&Move here', self.moveShape)))

        # self.tools = self.toolbar('Tools')

        self.actions.beginner = (
            open, opendir, openNextImg, openPrevImg, verify, save, None, create, copy, delete, None,
            zoomIn, zoom, zoomOut, fitWindow, fitWidth)

        self.actions.advanced = (
            open, opendir, openNextImg, openPrevImg, save, None,
            createMode, editMode, None,
            hideAll, showAll)

        self.statusBar().showMessage('%s started.' % __appname__)
        self.statusBar().show()

        # Application state.
        self.image = QImage()
        self.filePath = ustr(defaultFilename)
        self.lastOpenDir = None
        self.recentFiles = []
        self.maxRecent = 7
        self.lineColor = None
        self.fillColor = None
        self.zoom_level = 100
        self.fit_window = False
        # Add Chris
        self.difficult = False

        ## Fix the compatible issue for qt4 and qt5. Convert the QStringList to python list
        if settings.get(SETTING_RECENT_FILES):
            if have_qstring():
                recentFileQStringList = settings.get(SETTING_RECENT_FILES)
                self.recentFiles = [ustr(i) for i in recentFileQStringList]
            else:
                self.recentFiles = recentFileQStringList = settings.get(SETTING_RECENT_FILES)

        size = settings.get(SETTING_WIN_SIZE, QSize(1200, 800))

        position = QPoint(0, 0)
        saved_position = settings.get(SETTING_WIN_POSE, position)
        # Fix the multiple monitors issue
        for i in range(QApplication.desktop().screenCount()):
            if QApplication.desktop().availableGeometry(i).contains(saved_position):
                position = saved_position
                break
        self.resize(size)
        self.move(position)
        saveDir = ustr(settings.get(SETTING_SAVE_DIR, None))
        self.lastOpenDir = ustr(settings.get(SETTING_LAST_OPEN_DIR, None))

        self.restoreState(settings.get(SETTING_WIN_STATE, QByteArray()))
        Shape.line_color = self.lineColor = QColor(settings.get(SETTING_LINE_COLOR, DEFAULT_LINE_COLOR))
        Shape.fill_color = self.fillColor = QColor(settings.get(SETTING_FILL_COLOR, DEFAULT_FILL_COLOR))
        self.canvas.setDrawingColor(self.lineColor)
        # Add chris
        Shape.difficult = self.difficult


        # ADD:
        # Populate the File menu dynamically.
        self.updateFileMenu()

        # Since loading the file may take some time, make sure it runs in the background.
        if self.filePath and os.path.isdir(self.filePath):
            self.queueEvent(partial(self.importDirImages, self.filePath or ""))
        elif self.filePath:
            self.queueEvent(partial(self.loadFile, self.filePath or ""))

        # Callbacks:
        self.zoomWidget.valueChanged.connect(self.paintCanvas)

        self.populateModeActions()

        # Display cursor coordinates at the right of status bar
        self.labelCoordinates = QLabel('')
        self.statusBar().addPermanentWidget(self.labelCoordinates)

        # Open Dir if deafult file
        if self.filePath and os.path.isdir(self.filePath):
            self.openDirDialog(dirpath=self.filePath, silent=True)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Control:
            self.canvas.setDrawingShapeToSquare(False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Control:
            # Draw rectangle if Ctrl is pressed
            self.canvas.setDrawingShapeToSquare(True)


    def noShapes(self):
        return not self.itemsToShapes


    def populateModeActions(self):
        self.canvas.menus[0].clear()
        addActions(self.canvas.menus[0], self.actions.beginnerContext)
        self.menus.edit.clear()
        actions = (self.actions.create,)  # if self.beginner() else (self.actions.createMode, self.actions.editMode)
        addActions(self.menus.edit, actions + self.actions.editMenu)


    def setDirty(self):
        self.dirty = True
        self.actions.save.setEnabled(True)

    def setClean(self):
        self.dirty = False
        self.actions.save.setEnabled(False)
        self.actions.create.setEnabled(True)

    def toggleActions(self, value=True):
        """Enable/Disable widgets which depend on an opened image."""
        for z in self.actions.zoomActions:
            z.setEnabled(value)
        for action in self.actions.onLoadActive:
            action.setEnabled(value)

    def queueEvent(self, function):
        QTimer.singleShot(0, function)

    def status(self, message, delay=5000):
        self.statusBar().showMessage(message, delay)

    def resetState(self):
        self.itemsToShapes.clear()
        self.shapesToItems.clear()
        self.itemsToShapesbox.clear()  # ADD
        self.shapesToItemsbox.clear()
        self.labelList.clear()
        self.BoxList.clear()
        self.filePath = None
        self.imageData = None
        self.labelFile = None
        self.canvas.resetState()
        self.labelCoordinates.clear()
        # self.comboBox.cb.clear()
        self.result_dic = []

    def currentItem(self):
        items = self.labelList.selectedItems()
        if items:
            return items[0]
        return None

    def currentBox(self):
        items = self.BoxList.selectedItems()
        if items:
            return items[0]
        return None

    def addRecentFile(self, filePath):
        if filePath in self.recentFiles:
            self.recentFiles.remove(filePath)
        elif len(self.recentFiles) >= self.maxRecent:
            self.recentFiles.pop()
        self.recentFiles.insert(0, filePath)

    def beginner(self):
        return self._beginner

    def advanced(self):
        return not self.beginner()

    def getAvailableScreencastViewer(self):
        osName = platform.system()

        if osName == 'Windows':
            return ['C:\\Program Files\\Internet Explorer\\iexplore.exe']
        elif osName == 'Linux':
            return ['xdg-open']
        elif osName == 'Darwin':
            return ['open']

    ## Callbacks ##
    def showTutorialDialog(self):
        subprocess.Popen(self.screencastViewer + [self.screencast])

    def showInfoDialog(self):
        from libs.__init__ import __version__
        msg = u'Name:{0} \nApp Version:{1} \n{2} '.format(__appname__, __version__, sys.version_info)
        QMessageBox.information(self, u'Information', msg)

    def createShape(self):
        assert self.beginner()
        self.canvas.setEditing(False)
        self.actions.create.setEnabled(False)
        self.canvas.fourpoint = False

    def createPolygon(self):
        assert self.beginner()
        self.canvas.setEditing(False)
        self.canvas.fourpoint = True
        self.actions.create.setEnabled(False)

    def toggleDrawingSensitive(self, drawing=True):
        """In the middle of drawing, toggling between modes should be disabled."""
        self.actions.editMode.setEnabled(not drawing)
        if not drawing and self.beginner():
            # Cancel creation.
            print('Cancel creation.')
            self.canvas.setEditing(True)
            self.canvas.restoreCursor()
            self.actions.create.setEnabled(True)

    def toggleDrawMode(self, edit=True):
        self.canvas.setEditing(edit)
        self.actions.createMode.setEnabled(edit)
        self.actions.editMode.setEnabled(not edit)

    def setCreateMode(self):
        assert self.advanced()
        self.toggleDrawMode(False)

    def setEditMode(self):
        assert self.advanced()
        self.toggleDrawMode(True)
        self.labelSelectionChanged()

    def updateFileMenu(self):
        currFilePath = self.filePath

        def exists(filename):
            return os.path.exists(filename)

        menu = self.menus.recentFiles
        menu.clear()
        files = [f for f in self.recentFiles if f !=
                 currFilePath and exists(f)]
        for i, f in enumerate(files):
            icon = newIcon('labels')
            action = QAction(
                icon, '&%d %s' % (i + 1, QFileInfo(f).fileName()), self)
            action.triggered.connect(partial(self.loadRecent, f))
            menu.addAction(action)

    def popLabelListMenu(self, point):
        self.menus.labelList.exec_(self.labelList.mapToGlobal(point))

    def editLabel(self):
        if not self.canvas.editing():
            return
        item = self.currentItem()
        if not item:
            return
        text = self.labelDialog.popUp(item.text())
        if text is not None:
            item.setText(text)
            # item.setBackground(generateColorByText(text))
            self.setDirty()
            self.updateComboBox()

    ######## detection box related functions #######

    def boxItemChanged(self, item):
        shape = self.itemsToShapesbox[item]

        box = ast.literal_eval(item.text())
        # print('shape in labelItemChanged is',shape.points)
        if box != [(p.x(), p.y()) for p in shape.points]:
            # shape.points = box
            shape.points = [QPointF(p[0], p[1]) for p in box]

            # QPointF(x,y)
            # shape.line_color = generateColorByText(shape.label)
            self.setDirty()
        else:  # User probably changed item visibility
            self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)

    def editBox(self):  # ADD
        if not self.canvas.editing():
            return
        item = self.currentBox()
        if not item:
            return
        text = self.labelDialog.popUp(item.text())

        imageSize = str(self.image.size())
        width, height = self.image.width(), self.image.height()
        try:
            text_list = eval(text)
        except:
            msg_box = QMessageBox(QMessageBox.Warning, 'Warning', 'Please enter the correct format')
            msg_box.exec_()
            return
        if len(text_list) < 4:
            msg_box = QMessageBox(QMessageBox.Warning, 'Warning', 'Please enter the coordinates of 4 points')
            msg_box.exec_()
            return
        for box in text_list:
            if box[0] > width or box[0] < 0 or box[1] > height or box[1] < 0:
                msg_box = QMessageBox(QMessageBox.Warning, 'Warning', 'Out of picture size')
                msg_box.exec_()
                return

        if text is not None:
            item.setText(text)
            # item.setBackground(generateColorByText(text))
            self.setDirty()
            self.updateComboBox()

    def updateBoxlist(self):
        shape = self.canvas.selectedShape
        item = self.shapesToItemsbox[shape]  # listitem
        text = [(int(p.x()), int(p.y())) for p in shape.points]
        item.setText(str(text))
        self.setDirty()

    def indexTo5Files(self, currIndex):
        if currIndex < 2:
            return self.mImgList[:5]
        elif currIndex > len(self.mImgList)-3:
            return self.mImgList[-5:]
        else:
            return self.mImgList[currIndex - 2 : currIndex + 3]


    # Tzutalin 20160906 : Add file list and dock to move faster
    def fileitemDoubleClicked(self, item=None):
        self.currIndex = self.mImgList.index(ustr(os.path.join(os.path.abspath(self.dirname), item.text())))
        filename = self.mImgList[self.currIndex]
        if filename:
            self.mImgList5 = self.indexTo5Files(self.currIndex)
            self.additems5(None)
            self.loadFile(filename)

    def iconitemDoubleClicked(self, item=None):
        self.currIndex = self.mImgList.index(ustr(os.path.join(item.toolTip())))
        filename = self.mImgList[self.currIndex]
        if filename:
            self.mImgList5 = self.indexTo5Files(self.currIndex)
            self.additems5(None)
            self.loadFile(filename)

    def CanvasSizeChange(self):
        if len(self.mImgList) > 0:
            self.zoomWidget.setValue(self.zoomWidgetValue + self.imgsplider.value())

    # Add chris
    def btnstate(self, item=None):
        """ Function to handle difficult examples
        Update on each object """
        if not self.canvas.editing():
            return

        item = self.currentItem()
        if not item:  # If not selected Item, take the first one
            item = self.labelList.item(self.labelList.count() - 1)

        difficult = self.diffcButton.isChecked()

        try:
            shape = self.itemsToShapes[item]
        except:
            pass
        # Checked and Update
        try:
            if difficult != shape.difficult:
                shape.difficult = difficult
                self.setDirty()
            else:  # User probably changed item visibility
                self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)
        except:
            pass

    # React to canvas signals.
    def shapeSelectionChanged(self, selected=False):
        if self._noSelectionSlot:
            self._noSelectionSlot = False
        else:
            shape = self.canvas.selectedShape
            if shape:
                self.shapesToItems[shape].setSelected(True)
                self.shapesToItemsbox[shape].setSelected(True)  # ADD
            else:
                self.labelList.clearSelection()
        self.actions.delete.setEnabled(selected)
        self.actions.copy.setEnabled(selected)
        self.actions.edit.setEnabled(selected)
        self.actions.shapeLineColor.setEnabled(selected)
        self.actions.shapeFillColor.setEnabled(selected)

    def addLabel(self, shape):
        shape.paintLabel = self.displayLabelOption.isChecked()
        item = HashableQListWidgetItem(shape.label)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        # item.setBackground(generateColorByText(shape.label))
        self.itemsToShapes[item] = shape
        self.shapesToItems[shape] = item
        self.labelList.addItem(item)
        # print('item in add label is ',[(p.x(), p.y()) for p in shape.points], shape.label)

        # ADD for box
        item = HashableQListWidgetItem(str([(int(p.x()), int(p.y())) for p in shape.points]))
        # item = QListWidgetItem(str([(p.x(), p.y()) for p in shape.points]))
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)
        # item.setBackground(generateColorByText(shape.label))
        self.itemsToShapesbox[item] = shape
        self.shapesToItemsbox[shape] = item
        self.BoxList.addItem(item)
        for action in self.actions.onShapesPresent:
            action.setEnabled(True)
        self.updateComboBox()

    def remLabel(self, shape):
        if shape is None:
            # print('rm empty label')
            return
        item = self.shapesToItems[shape]
        self.labelList.takeItem(self.labelList.row(item))
        del self.shapesToItems[shape]
        del self.itemsToShapes[item]
        self.updateComboBox()

        # ADD:
        item = self.shapesToItemsbox[shape]
        self.BoxList.takeItem(self.BoxList.row(item))
        del self.shapesToItemsbox[shape]
        del self.itemsToShapesbox[item]
        self.updateComboBox()

    def loadLabels(self, shapes):
        s = []
        for label, points, line_color, fill_color, difficult in shapes:
            shape = Shape(label=label)
            for x, y in points:

                # Ensure the labels are within the bounds of the image. If not, fix them.
                x, y, snapped = self.canvas.snapPointToCanvas(x, y)
                if snapped:
                    self.setDirty()

                shape.addPoint(QPointF(x, y))
            shape.difficult = difficult
            shape.close()
            s.append(shape)

            # if line_color:
            #     shape.line_color = QColor(*line_color)
            # else:
            #     shape.line_color = generateColorByText(label)
            #
            # if fill_color:
            #     shape.fill_color = QColor(*fill_color)
            # else:
            #     shape.fill_color = generateColorByText(label)

            self.addLabel(shape)
        self.updateComboBox()
        self.canvas.loadShapes(s)

    def updateComboBox(self):
        # Get the unique labels and add them to the Combobox.
        itemsTextList = [str(self.labelList.item(i).text()) for i in range(self.labelList.count())]

        uniqueTextList = list(set(itemsTextList))
        # Add a null row for showing all the labels
        uniqueTextList.append("")
        uniqueTextList.sort()

        # self.comboBox.update_items(uniqueTextList)

    def saveLabels(self, annotationFilePath, mode='Auto'):
        annotationFilePath = ustr(annotationFilePath)
        if self.labelFile is None:
            self.labelFile = LabelFile()
            self.labelFile.verified = self.canvas.verified

        def format_shape(s):
            # print('s in saveLabels is ',s)
            return dict(label=s.label,  # str
                        line_color=s.line_color.getRgb(),
                        fill_color=s.fill_color.getRgb(),
                        points=[(p.x(), p.y()) for p in s.points],  # QPonitF
                       # add chris
                        difficult=s.difficult)  # bool

        shapes = [] if mode == 'Auto' else \
            [format_shape(shape) for shape in self.canvas.shapes]
        # Can add differrent annotation formats here

        if self.model == 'paddle':
            for box in self.result_dic:
                trans_dic = {"label": box[1][0], "points": box[0], 'difficult': False}
                if trans_dic["label"] is "" and mode == 'Auto':
                    continue
                shapes.append(trans_dic)

        try:
            trans_dic = []
            for box in shapes:
                trans_dic.append({"transcription": box['label'], "points": box['points'], 'difficult': False})
            self.PPlabel[annotationFilePath] = trans_dic

            if mode == 'Auto':
                self.Cachelabel[annotationFilePath] = trans_dic

            # else:
            #     self.labelFile.save(annotationFilePath, shapes, self.filePath, self.imageData,
            #                         self.lineColor.getRgb(), self.fillColor.getRgb())
            # print('Image:{0} -> Annotation:{1}'.format(self.filePath, annotationFilePath))
            return True
        except LabelFileError as e:
            self.errorMessage(u'Error saving label data', u'<b>%s</b>' % e)
            return False

    def copySelectedShape(self):
        self.addLabel(self.canvas.copySelectedShape())
        # fix copy and delete
        self.shapeSelectionChanged(True)


    def labelSelectionChanged(self):
        item = self.currentItem()
        if item and self.canvas.editing():
            self._noSelectionSlot = True
            self.canvas.selectShape(self.itemsToShapes[item])
            shape = self.itemsToShapes[item]
            # Add Chris
            self.diffcButton.setChecked(shape.difficult)

    def boxSelectionChanged(self):
        item = self.currentBox()
        if item and self.canvas.editing():
            self._noSelectionSlot = True
            self.canvas.selectShape(self.itemsToShapesbox[item])
            shape = self.itemsToShapesbox[item]
            # Add Chris
            self.diffcButton.setChecked(shape.difficult)

    def labelItemChanged(self, item):
        shape = self.itemsToShapes[item]
        label = item.text()
        if label != shape.label:
            shape.label = item.text()
            # shape.line_color = generateColorByText(shape.label)
            self.setDirty()
        else:  # User probably changed item visibility
            self.canvas.setShapeVisible(shape, item.checkState() == Qt.Checked)

    # Callback functions:
    def newShape(self):
        """Pop-up and give focus to the label editor.

        position MUST be in global coordinates.
        """
        if len(self.labelHist) > 0:
            self.labelDialog = LabelDialog(
                parent=self, listItem=self.labelHist)

        # Sync single class mode from PR#106
        if self.singleClassMode.isChecked() and self.lastLabel:
            text = self.lastLabel
        else:
            text = self.labelDialog.popUp(text=self.prevLabelText)
            self.lastLabel = text

        # Add Chris
        self.diffcButton.setChecked(False)
        if text is not None:
            self.prevLabelText = self.stringBundle.getString('tempLabel')
            # generate_color = generateColorByText(text)
            shape = self.canvas.setLastLabel(text, None, None)#generate_color, generate_color
            self.addLabel(shape)
            if self.beginner():  # Switch to edit mode.
                self.canvas.setEditing(True)
                self.actions.create.setEnabled(True)
            else:
                self.actions.editMode.setEnabled(True)
            self.setDirty()

        else:
            # self.canvas.undoLastLine()
            self.canvas.resetAllLines()

    def scrollRequest(self, delta, orientation):
        units = - delta / (8 * 15)
        bar = self.scrollBars[orientation]
        bar.setValue(bar.value() + bar.singleStep() * units)

    def setZoom(self, value):
        self.actions.fitWidth.setChecked(False)
        self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.MANUAL_ZOOM
        self.zoomWidget.setValue(value)

    def addZoom(self, increment=10):
        self.setZoom(self.zoomWidget.value() + increment)

    def zoomRequest(self, delta):
        # get the current scrollbar positions
        # calculate the percentages ~ coordinates
        h_bar = self.scrollBars[Qt.Horizontal]
        v_bar = self.scrollBars[Qt.Vertical]

        # get the current maximum, to know the difference after zooming
        h_bar_max = h_bar.maximum()
        v_bar_max = v_bar.maximum()

        # get the cursor position and canvas size
        # calculate the desired movement from 0 to 1
        # where 0 = move left
        #       1 = move right
        # up and down analogous
        cursor = QCursor()
        pos = cursor.pos()
        relative_pos = QWidget.mapFromGlobal(self, pos)

        cursor_x = relative_pos.x()
        cursor_y = relative_pos.y()

        w = self.scrollArea.width()
        h = self.scrollArea.height()

        # the scaling from 0 to 1 has some padding
        # you don't have to hit the very leftmost pixel for a maximum-left movement
        margin = 0.1
        move_x = (cursor_x - margin * w) / (w - 2 * margin * w)
        move_y = (cursor_y - margin * h) / (h - 2 * margin * h)

        # clamp the values from 0 to 1
        move_x = min(max(move_x, 0), 1)
        move_y = min(max(move_y, 0), 1)

        # zoom in
        units = delta / (8 * 15)
        scale = 10
        self.addZoom(scale * units)

        # get the difference in scrollbar values
        # this is how far we can move
        d_h_bar_max = h_bar.maximum() - h_bar_max
        d_v_bar_max = v_bar.maximum() - v_bar_max

        # get the new scrollbar values
        new_h_bar_value = h_bar.value() + move_x * d_h_bar_max
        new_v_bar_value = v_bar.value() + move_y * d_v_bar_max

        h_bar.setValue(new_h_bar_value)
        v_bar.setValue(new_v_bar_value)

    def setFitWindow(self, value=True):
        if value:
            self.actions.fitWidth.setChecked(False)
        self.zoomMode = self.FIT_WINDOW if value else self.MANUAL_ZOOM
        self.adjustScale()

    def setFitWidth(self, value=True):
        if value:
            self.actions.fitWindow.setChecked(False)
        self.zoomMode = self.FIT_WIDTH if value else self.MANUAL_ZOOM
        self.adjustScale()

    def togglePolygons(self, value):
        for item, shape in self.itemsToShapes.items():
            item.setCheckState(Qt.Checked if value else Qt.Unchecked)

    def loadFile(self, filePath=None):
        """Load the specified file, or the last opened file if None."""
        self.resetState()
        self.canvas.setEnabled(False)
        if filePath is None:
            filePath = self.settings.get(SETTING_FILENAME)

        # Make sure that filePath is a regular python string, rather than QString
        filePath = ustr(filePath)
        # Fix bug: An index error after select a directory when open a new file.
        unicodeFilePath = ustr(filePath)
        # unicodeFilePath = os.path.abspath(unicodeFilePath)
        # Tzutalin 20160906 : Add file list and dock to move faster
        # Highlight the file item
        if unicodeFilePath and self.fileListWidget.count() > 0:
            if unicodeFilePath in self.mImgList:
                index = self.mImgList.index(unicodeFilePath)
                fileWidgetItem = self.fileListWidget.item(index)
                print('unicodeFilePath is', unicodeFilePath)
                fileWidgetItem.setSelected(True)
                ###
                self.iconlist.clear()
                self.additems5(None)
                for i in range(5):
                    item_tooltip = self.iconlist.item(i).toolTip()
                    # print(i,"---",item_tooltip)
                    if item_tooltip == ustr(filePath):
                        titem = self.iconlist.item(i)
                        titem.setSelected(True)
                        self.iconlist.scrollToItem(titem)
                        break 
            else:
                self.fileListWidget.clear()
                self.mImgList.clear()
                self.iconlist.clear()

        # if unicodeFilePath and self.iconList.count() > 0:
        #     if unicodeFilePath in self.mImgList:
                
        if unicodeFilePath and os.path.exists(unicodeFilePath):
            if LabelFile.isLabelFile(unicodeFilePath):
                try:
                    self.labelFile = LabelFile(unicodeFilePath)
                except LabelFileError as e:
                    self.errorMessage(u'Error opening file',
                                      (u"<p><b>%s</b></p>"
                                       u"<p>Make sure <i>%s</i> is a valid label file.")
                                      % (e, unicodeFilePath))
                    self.status("Error reading %s" % unicodeFilePath)
                    return False
                self.imageData = self.labelFile.imageData
                self.lineColor = QColor(*self.labelFile.lineColor)
                self.fillColor = QColor(*self.labelFile.fillColor)
                self.canvas.verified = self.labelFile.verified
            else:
                # Load image:
                # read data first and store for saving into label file.
                self.imageData = read(unicodeFilePath, None)
                self.labelFile = None
                self.canvas.verified = False

            image = QImage.fromData(self.imageData)
            if image.isNull():
                self.errorMessage(u'Error opening file',
                                  u"<p>Make sure <i>%s</i> is a valid image file." % unicodeFilePath)
                self.status("Error reading %s" % unicodeFilePath)
                return False
            self.status("Loaded %s" % os.path.basename(unicodeFilePath))
            self.image = image
            self.filePath = unicodeFilePath
            self.canvas.loadPixmap(QPixmap.fromImage(image))
            if self.labelFile:
                self.loadLabels(self.labelFile.shapes)
            if self.validFilestate(filePath) is True:
                self.setClean()
            else:
                self.dirty = False
                self.actions.save.setEnabled(True)

            self.canvas.setEnabled(True)
            self.adjustScale(initial=True)
            self.paintCanvas()
            self.addRecentFile(self.filePath)
            self.toggleActions(True)
            self.showBoundingBoxFromPPlabel(filePath)
            
            self.setWindowTitle(__appname__ + ' ' + filePath)

            # Default : select last item if there is at least one item
            if self.labelList.count():
                self.labelList.setCurrentItem(self.labelList.item(self.labelList.count() - 1))
                self.labelList.item(self.labelList.count() - 1).setSelected(True)

            self.canvas.setFocus(True)
            return True
        return False

    
    def showBoundingBoxFromPPlabel(self, filePath):
        imgidx = self.getImglabelidx(filePath)
        if imgidx not in self.PPlabel.keys():
            return
        shapes = []
        for box in self.PPlabel[imgidx]:
            shapes.append((box['transcription'], box['points'], None, None, box['difficult']))

        print(shapes)
        self.loadLabels(shapes)
        self.canvas.verified = False

    def validAnnoExist(self, filePath):
        if self.defaultSaveDir is not None:
            basename = os.path.basename(
                os.path.splitext(filePath)[0])
            xmlPath = os.path.join(self.defaultSaveDir, basename + XML_EXT)
            txtPath = os.path.join(self.defaultSaveDir, basename + TXT_EXT)
            """Annotation file priority:
            PascalXML > YOLO
            """
            if os.path.isfile(xmlPath):
                return True
            elif os.path.isfile(txtPath):
                return True
        else:
            xmlPath = os.path.splitext(filePath)[0] + XML_EXT
            txtPath = os.path.splitext(filePath)[0] + TXT_EXT
            if os.path.isfile(xmlPath):
                return True
            elif os.path.isfile(txtPath):
                return True

    def validFilestate(self, filePath):
        if filePath not in self.fileStatedict.keys():
            return None
        elif self.fileStatedict[filePath] == 1:
            return True
        else:
            return False

    def resizeEvent(self, event):
        if self.canvas and not self.image.isNull() \
           and self.zoomMode != self.MANUAL_ZOOM:
            self.adjustScale()
        super(MainWindow, self).resizeEvent(event)

    def paintCanvas(self):
        assert not self.image.isNull(), "cannot paint null image"
        self.canvas.scale = 0.01 * self.zoomWidget.value()
        self.canvas.adjustSize()
        self.canvas.update()

    def adjustScale(self, initial=False):
        value = self.scalers[self.FIT_WINDOW if initial else self.zoomMode]()
        self.zoomWidget.setValue(int(100 * value))

    def scaleFitWindow(self):
        """Figure out the size of the pixmap in order to fit the main widget."""
        e = 2.0  # So that no scrollbars are generated.
        w1 = self.centralWidget().width() - e
        h1 = self.centralWidget().height() - e
        a1 = w1 / h1
        # Calculate a new scale value based on the pixmap's aspect ratio.
        w2 = self.canvas.pixmap.width() - 0.0
        h2 = self.canvas.pixmap.height() - 0.0
        a2 = w2 / h2
        return w1 / w2 if a2 >= a1 else h1 / h2

    def scaleFitWidth(self):
        # The epsilon does not seem to work too well here.
        w = self.centralWidget().width() - 2.0
        return w / self.canvas.pixmap.width()

    def closeEvent(self, event):
        if not self.mayContinue():
            event.ignore()
        else:
            settings = self.settings
            # If it loads images from dir, don't load it at the begining
            if self.dirname is None:
                settings[SETTING_FILENAME] = self.filePath if self.filePath else ''
            else:
                settings[SETTING_FILENAME] = ''

            settings[SETTING_WIN_SIZE] = self.size()
            settings[SETTING_WIN_POSE] = self.pos()
            settings[SETTING_WIN_STATE] = self.saveState()
            settings[SETTING_LINE_COLOR] = self.lineColor
            settings[SETTING_FILL_COLOR] = self.fillColor
            settings[SETTING_RECENT_FILES] = self.recentFiles
            settings[SETTING_ADVANCE_MODE] = not self._beginner
            if self.defaultSaveDir and os.path.exists(self.defaultSaveDir):
                settings[SETTING_SAVE_DIR] = ustr(self.defaultSaveDir)
            else:
                settings[SETTING_SAVE_DIR] = ''

            if self.lastOpenDir and os.path.exists(self.lastOpenDir):
                settings[SETTING_LAST_OPEN_DIR] = self.lastOpenDir
            else:
                settings[SETTING_LAST_OPEN_DIR] = ''

            settings[SETTING_SINGLE_CLASS] = self.singleClassMode.isChecked()
            settings[SETTING_PAINT_LABEL] = self.displayLabelOption.isChecked()
            settings[SETTING_DRAW_SQUARE] = self.drawSquaresOption.isChecked()
            settings.save()
            try:
                self.saveFilestate()
                self.savePPlabel()
            except:
                pass

    def loadRecent(self, filename):
        if self.mayContinue():
            self.loadFile(filename)

    def scanAllImages(self, folderPath):
        extensions = ['.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        images = []

        for file in os.listdir(folderPath):
            if file.lower().endswith(tuple(extensions)):
                relativePath = os.path.join(folderPath, file)
                path = ustr(os.path.abspath(relativePath))
                images.append(path)
        natural_sort(images, key=lambda x: x.lower())
        return images



    def openDirDialog(self, _value=False, dirpath=None, silent=False):
        if not self.mayContinue():
            return

        defaultOpenDirPath = dirpath if dirpath else '.'
        if self.lastOpenDir and os.path.exists(self.lastOpenDir):
            defaultOpenDirPath = self.lastOpenDir
        else:
            defaultOpenDirPath = os.path.dirname(self.filePath) if self.filePath else '.'
        if silent != True:
            targetDirPath = ustr(QFileDialog.getExistingDirectory(self,
                                                         '%s - Open Directory' % __appname__,
                                                         defaultOpenDirPath,
                                                         QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks))
        else:
            targetDirPath = ustr(defaultOpenDirPath)
        self.lastOpenDir = targetDirPath
        self.importDirImages(targetDirPath)

    def importDirImages(self, dirpath):
        if not self.mayContinue() or not dirpath:
            return
        if self.defaultSaveDir and self.defaultSaveDir != dirpath:
            self.saveFilestate()
            self.savePPlabel()

        self.loadFilestate(dirpath)
        self.PPlabelpath = dirpath+ '/Label.txt'
        self.PPlabel = self.loadLabelFile(self.PPlabelpath)
        self.Cachelabelpath = dirpath + '/Cache.cach'
        self.Cachelabel = self.loadLabelFile(self.Cachelabelpath)
        if self.Cachelabel:
            self.PPlabel = dict(self.Cachelabel, **self.PPlabel)
        self.lastOpenDir = dirpath
        self.dirname = dirpath


        self.defaultSaveDir = dirpath
        self.statusBar().showMessage('%s started. Annotation will be saved to %s' %
                                     (__appname__, self.defaultSaveDir))
        self.statusBar().show()

        self.filePath = None
        self.fileListWidget.clear()
        self.mImgList = self.scanAllImages(dirpath)
        self.mImgList5 = self.mImgList[:5]
        self.openNextImg()
        doneicon = newIcon('done')
        closeicon = newIcon('close')
        for imgPath in self.mImgList:
            filename = os.path.basename(imgPath)
            if self.validFilestate(imgPath) is True:
                item = QListWidgetItem(doneicon, filename)
            else:
                item = QListWidgetItem(closeicon, filename)
            self.fileListWidget.addItem(item)

        print('dirPath in importDirImages is', dirpath)
        self.iconlist.clear()
        self.additems5(dirpath)
        self.changeFileFolder = True
        self.haveAutoReced = False
        self.AutoRecognition.setEnabled(True)
        self.reRecogButton.setEnabled(True)

    def verifyImg(self, _value=False):
        # Proceding next image without dialog if having any label
        if self.filePath is not None:
            try:
                self.labelFile.toggleVerify()
            except AttributeError:
                # If the labelling file does not exist yet, create if and
                # re-save it with the verified attribute.
                self.saveFile()
                if self.labelFile != None:
                    self.labelFile.toggleVerify()
                else:
                    return

            self.canvas.verified = self.labelFile.verified
            self.paintCanvas()
            self.saveFile()

    def openPrevImg(self, _value=False):
        if len(self.mImgList) <= 0:
            return

        if self.filePath is None:
            return
        
        currIndex = self.mImgList.index(self.filePath)
        self.mImgList5 = self.mImgList[:5]
        if currIndex - 1 >= 0:
            filename = self.mImgList[currIndex - 1]
            self.mImgList5 = self.indexTo5Files(currIndex - 1)
            if filename:
                self.loadFile(filename)

    def openNextImg(self, _value=False):
        if not self.mayContinue():
            return

        if len(self.mImgList) <= 0:
            return

        filename = None
        if self.filePath is None:
            filename = self.mImgList[0]
            self.mImgList5 = self.mImgList[:5]
        else:
            currIndex = self.mImgList.index(self.filePath)
            if currIndex + 1 < len(self.mImgList):
                filename = self.mImgList[currIndex + 1]
                self.mImgList5 = self.indexTo5Files(currIndex + 1)
            else:
                self.mImgList5 = self.indexTo5Files(currIndex)
        if filename:
            print('file name in openNext is ',filename)
            self.loadFile(filename)

    def openFile(self, _value=False):
        if not self.mayContinue():
            return
        path = os.path.dirname(ustr(self.filePath)) if self.filePath else '.'
        formats = ['*.%s' % fmt.data().decode("ascii").lower() for fmt in QImageReader.supportedImageFormats()]
        filters = "Image & Label files (%s)" % ' '.join(formats + ['*%s' % LabelFile.suffix])
        filename = QFileDialog.getOpenFileName(self, '%s - Choose Image or Label file' % __appname__, path, filters)
        if filename:
            if isinstance(filename, (tuple, list)):
                filename = filename[0]
            self.loadFile(filename)
            # print('filename in openfile is ', self.filePath)
        self.filePath = None
        self.fileListWidget.clear()
        self.iconlist.clear()
        self.mImgList = [filename]
        self.openNextImg()
        if self.validFilestate(filename) is True:
            item = QListWidgetItem(newIcon('done'), filename)
            self.setClean()
        elif self.validFilestate(filename) is None:
            item = QListWidgetItem(newIcon('close'), filename)
        else:
            item = QListWidgetItem(newIcon('close'), filename)
            self.setDirty()
        self.fileListWidget.addItem(filename)
        self.additems5(None)
        print('opened image is', filename)
        
    def updateFileListIcon(self, filename):
        pass

    def saveFile(self, _value=False, mode='Manual'):
        if self.defaultSaveDir is not None and len(ustr(self.defaultSaveDir)):
            if self.filePath:
                imgidx = self.getImglabelidx(self.filePath)
                self._saveFile(imgidx, mode=mode)

        else:
            imgFileDir = os.path.dirname(self.filePath)
            imgFileName = os.path.basename(self.filePath)
            savedFileName = os.path.splitext(imgFileName)[0]
            savedPath = os.path.join(imgFileDir, savedFileName)
            self._saveFile(savedPath if self.labelFile
                           else self.saveFileDialog(removeExt=False), mode=mode)

    def saveFileAs(self, _value=False):
        assert not self.image.isNull(), "cannot save empty image"
        self._saveFile(self.saveFileDialog())

    def saveFileDialog(self, removeExt=True):
        caption = '%s - Choose File' % __appname__
        filters = 'File (*%s)' % LabelFile.suffix
        openDialogPath = self.currentPath()
        dlg = QFileDialog(self, caption, openDialogPath, filters)
        dlg.setDefaultSuffix(LabelFile.suffix[1:])
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        filenameWithoutExtension = os.path.splitext(self.filePath)[0]
        dlg.selectFile(filenameWithoutExtension)
        dlg.setOption(QFileDialog.DontUseNativeDialog, False)
        if dlg.exec_():
            fullFilePath = ustr(dlg.selectedFiles()[0])
            if removeExt:
                return os.path.splitext(fullFilePath)[0]  # Return file path without the extension.
            else:
                return fullFilePath
        return ''

    def _saveFile(self, annotationFilePath, mode='Manual'):
        if mode == 'Manual':
            if annotationFilePath and self.saveLabels(annotationFilePath, mode=mode):
                self.setClean()
                self.statusBar().showMessage('Saved to  %s' % annotationFilePath)
                self.statusBar().show()
                currIndex = self.mImgList.index(self.filePath)
                item = self.fileListWidget.item(currIndex)
                item.setIcon(newIcon('done'))

                self.fileStatedict[self.filePath] = 1
                if len(self.fileStatedict)%self.autoSaveNum ==0:
                    self.saveFilestate()
                    self.savePPlabel(mode='Auto')

                self.fileListWidget.insertItem(int(currIndex), item)
                self.openNextImg()
                self.actions.saveRec.setEnabled(True)

        elif mode == 'Auto':
            if annotationFilePath and self.saveLabels(annotationFilePath, mode=mode):

                self.setClean()
                self.statusBar().showMessage('Saved to  %s' % annotationFilePath)
                self.statusBar().show()

    def closeFile(self, _value=False):
        if not self.mayContinue():
            return
        self.resetState()
        self.setClean()
        self.toggleActions(False)
        self.canvas.setEnabled(False)
        self.actions.saveAs.setEnabled(False)

    def deleteImg(self):
        deletePath = self.filePath
        if deletePath is not None:
            deleteInfo = self.deleteImgDialog()
            if deleteInfo == QMessageBox.Yes:

                res = shell.SHFileOperation((0, shellcon.FO_DELETE, deletePath, None,
                                             shellcon.FOF_SILENT | shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION,
                                             None, None))
                if not res[1]:
                    os.system('del ' + deletePath)

                if self.filePath in self.fileStatedict.keys():
                    self.fileStatedict.pop(self.filePath)
                imgidx = self.getImglabelidx(self.filePath)
                if imgidx in self.PPlabel.keys():
                    self.PPlabel.pop(imgidx)
                self.openNextImg()
                self.importDirImages(self.lastOpenDir)

    def deleteImgDialog(self):
        yes, cancel = QMessageBox.Yes, QMessageBox.Cancel
        msg = u'The image will be deleted to the recycle bin'
        return QMessageBox.warning(self, u'Attention', msg, yes | cancel)

    def resetAll(self):
        self.settings.reset()
        self.close()
        proc = QProcess()
        proc.startDetached(os.path.abspath(__file__))

    def mayContinue(self):  #
        if not self.dirty:                                    
            return True
        else:
            discardChanges = self.discardChangesDialog()
            if discardChanges == QMessageBox.No:
                return True
            elif discardChanges == QMessageBox.Yes:
                self.saveFile()
                return True
            else:
                return False

    def discardChangesDialog(self):
        yes, no, cancel = QMessageBox.Yes, QMessageBox.No, QMessageBox.Cancel
        msg = u'You have unsaved changes, would you like to save them and proceed?\nClick "No" to undo all changes.'
        return QMessageBox.warning(self, u'Attention', msg, yes | no | cancel)

    def errorMessage(self, title, message):
        return QMessageBox.critical(self, title,
                                    '<p><b>%s</b></p>%s' % (title, message))

    def currentPath(self):
        return os.path.dirname(self.filePath) if self.filePath else '.'

    def chooseColor1(self):
        color = self.colorDialog.getColor(self.lineColor, u'Choose line color',
                                          default=DEFAULT_LINE_COLOR)
        if color:
            self.lineColor = color
            Shape.line_color = color
            self.canvas.setDrawingColor(color)
            self.canvas.update()
            self.setDirty()

    def deleteSelectedShape(self):
        self.remLabel(self.canvas.deleteSelected())
        self.setDirty()
        if self.noShapes():
            for action in self.actions.onShapesPresent:
                action.setEnabled(False)

    def chshapeLineColor(self):
        color = self.colorDialog.getColor(self.lineColor, u'Choose line color',
                                          default=DEFAULT_LINE_COLOR)
        if color:
            self.canvas.selectedShape.line_color = color
            self.canvas.update()
            self.setDirty()

    def chshapeFillColor(self):
        color = self.colorDialog.getColor(self.fillColor, u'Choose fill color',
                                          default=DEFAULT_FILL_COLOR)
        if color:
            self.canvas.selectedShape.fill_color = color
            self.canvas.update()
            self.setDirty()

    def copyShape(self):
        self.canvas.endMove(copy=True)
        self.addLabel(self.canvas.selectedShape)
        self.setDirty()

    def moveShape(self):
        self.canvas.endMove(copy=False)
        self.setDirty()

    def loadPredefinedClasses(self, predefClassesFile):
        if os.path.exists(predefClassesFile) is True:
            with codecs.open(predefClassesFile, 'r', 'utf8') as f:
                for line in f:
                    line = line.strip()
                    if self.labelHist is None:
                        self.labelHist = [line]
                    else:
                        self.labelHist.append(line)


    def togglePaintLabelsOption(self):
        for shape in self.canvas.shapes:
            shape.paintLabel = self.displayLabelOption.isChecked()

    def toogleDrawSquare(self):
        self.canvas.setDrawingShapeToSquare(self.drawSquaresOption.isChecked())

    def additems(self, dirpath):
        for file in self.mImgList:
            pix = QPixmap(file)
            _, filename = os.path.split(file)
            filename, _ = os.path.splitext(filename)
            item = QListWidgetItem(QIcon(pix.scaled(100, 100, Qt.IgnoreAspectRatio, Qt.FastTransformation)),
                                   filename[:10])
            item.setToolTip(file)
            self.iconlist.addItem(item)

    def additems5(self, dirpath):
        for file in self.mImgList5:
            pix = QPixmap(file)
            _, filename = os.path.split(file)
            filename, _ = os.path.splitext(filename)
            pfilename = filename[:10]
            if len(pfilename) < 10:
                bfilename = (12 - len(pfilename)) * " "
            else:
                bfilename = ""
            # item = QListWidgetItem(QIcon(pix.scaled(100, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation)),filename[:10])
            item = QListWidgetItem(QIcon(pix.scaled(100, 100, Qt.IgnoreAspectRatio, Qt.FastTransformation)),pfilename + bfilename )
            # item.setForeground(QBrush(Qt.white))
            item.setToolTip(file)
            self.iconlist.addItem(item)

    def getImglabelidx(self, filePath):
        filepathsplit = filePath.split('\\')[-2:]
        return filepathsplit[0] + '/' + filepathsplit[1]

    def autoRecognition(self):
        assert self.mImgList is not None
        print('Using model from ', self.model)

        uncheckedList = [i for i in self.mImgList if i not in self.fileStatedict.keys()]
        self.autoDialog = AutoDialog(parent=self, ocr=self.ocr, mImgList=uncheckedList, lenbar=len(uncheckedList))
        self.autoDialog.popUp()
        self.currIndex=len(self.mImgList)
        self.loadFile(self.filePath) # ADD
        self.haveAutoReced = True
        self.AutoRecognition.setEnabled(False)
        self.setDirty()
        self.saveCacheLabel()


    def reRecognition(self):
        img = cv2.imread(self.filePath)

        if self.canvas.shapes:
            self.result_dic = []
            rec_flag = 0
            for shape in self.canvas.shapes:
                box = [[int(p.x()), int(p.y())] for p in shape.points]
                assert len(box) == 4
                img_crop = get_rotate_crop_image(img, np.array(box, np.float32))
                if img_crop is None:
                    msg = 'Can not recognise the detection box in ' + self.filePath + '. Please change manually'
                    QMessageBox.information(self, "Information", msg)
                    return
                print(self.comboBox.currentText())
                result = self.ocr.ocr(img_crop, cls=True, det=False)
                if result[0][0] is not '':
                    result.insert(0, box)
                    print('result in reRec is ', result)
                    self.result_dic.append(result)
                    if result[1][0] == shape.label:
                        print('label no change')
                    else:
                        rec_flag += 1

            if len(self.result_dic) > 0 and rec_flag > 0:
                self.saveFile(mode='Auto')
                self.loadFile(self.filePath)
                self.setDirty()
            elif len(self.result_dic) == len(self.canvas.shapes) and rec_flag == 0:
                QMessageBox.information(self, "Information", "Not any change!")
            else:
                print('Can not recgonise in ', self.filePath)
        else:
            QMessageBox.information(self, "Information", "Draw a box!")


    def seclectmodel(self):
        print('model change')
        print(self.comboBox.currentText())
    
    def autolcm(self):
        print('autolabelchoosemodel')

    def loadFilestate(self, saveDir):
        self.fileStatepath = saveDir + '/fileState.txt'
        self.fileStatedict = {}
        if not os.path.exists(self.fileStatepath):
            f = open(self.fileStatepath, 'w', encoding='utf-8')
        else:
            with open(self.fileStatepath, 'r', encoding='utf-8') as f:
                states = f.readlines()
                for each in states:
                    file, state = each.split('\t')
                    self.fileStatedict[file] = 1


    def saveFilestate(self):
        with open(self.fileStatepath, 'w', encoding='utf-8') as f:
            for key in self.fileStatedict:
                f.write(key + '\t')
                f.write(str(self.fileStatedict[key]) + '\n')


    def loadLabelFile(self, labelpath):
        labeldict = {}
        if not os.path.exists(labelpath):
            f = open(labelpath, 'w', encoding='utf-8')

        else:
            with open(labelpath, 'r', encoding='utf-8') as f:
                data = f.readlines()
                for each in data:
                    file, label = each.split('\t')
                    if label:
                        label = label.replace('false', 'False')
                        labeldict[file] = eval(label)
                    else:
                        labeldict[file] = []
        return labeldict


    def savePPlabel(self,mode='Manual'):
        savedfile = [self.getImglabelidx(i) for i in self.fileStatedict.keys()]
        with open(self.PPlabelpath, 'w', encoding='utf-8') as f:
            for key in self.PPlabel:
                if key in savedfile:
                    f.write(key + '\t')
                    f.write(json.dumps(self.PPlabel[key], ensure_ascii=False) + '\n')

        if mode=='Manual':
            msg = 'Labels that have been checked are saved in '+ self.PPlabelpath
            QMessageBox.information(self, "Information", msg)

    def saveCacheLabel(self):
        with open(self.Cachelabelpath, 'w', encoding='utf-8') as f:
            for key in self.Cachelabel:
                f.write(key + '\t')
                f.write(json.dumps(self.Cachelabel[key], ensure_ascii=False) + '\n')

    def saveRecResult(self):
        if None in [self.PPlabelpath, self.PPlabel, self.fileStatedict]:
            QMessageBox.information(self, "Information", "Save file first")
            return

        rec_gt_dir = os.path.dirname(self.PPlabelpath) + '/rec_gt.txt'
        crop_img_dir = os.path.dirname(self.PPlabelpath) + '/crop_img/'
        if not os.path.exists(crop_img_dir):
            os.mkdir(crop_img_dir)

        with open(rec_gt_dir, 'w', encoding='utf-8') as f:
            for key in self.fileStatedict:
                idx = self.getImglabelidx(key)
                for i, label in enumerate(self.PPlabel[idx]):
                    img = cv2.imread(key)
                    img_crop = get_rotate_crop_image(img, np.array(label['points'], np.float32))
                    img_name = os.path.splitext(os.path.basename(idx))[0] + '_crop_'+str(i)+'.jpg'
                    cv2.imwrite(crop_img_dir+img_name, img_crop)
                    f.write('crop_img/'+ img_name + '\t')
                    f.write(label['transcription'] + '\n')

        QMessageBox.information(self, "Information", "Cropped images has been saved in "+str(crop_img_dir))

def inverted(color):
    return QColor(*[255 - v for v in color.getRgb()])


def read(filename, default=None):
    try:
        with open(filename, 'rb') as f:
            return f.read()
    except:
        return default


def get_main_app(argv=[]):
    """
    Standard boilerplate Qt application code.
    Do everything but app.exec_() -- so that we can test the application in one thread
    """
    app = QApplication(argv)
    app.setApplicationName(__appname__)
    app.setWindowIcon(newIcon("app"))
    # Tzutalin 201705+: Accept extra agruments to change predefined class file
    argparser = argparse.ArgumentParser()
    argparser.add_argument("image_dir", nargs="?")
    argparser.add_argument("predefined_classes_file",
                           default=os.path.join(os.path.dirname(__file__), "data", "predefined_classes.txt"),
                           nargs="?")
    argparser.add_argument("save_dir", nargs="?")
    args = argparser.parse_args(argv[1:])
    # Usage : labelImg.py image predefClassFile saveDir
    win = MainWindow(args.image_dir,
                     args.predefined_classes_file,
                     args.save_dir)
    win.show()
    return app, win


def main():
    '''construct main app and run it'''
    app, _win = get_main_app(sys.argv)
    return app.exec_()


if __name__ == '__main__':
        
    resource_file = './libs/resources.py'
    if not os.path.exists(resource_file):
        output = os.system('pyrcc5 -o libs/resources.py resources.qrc')
        assert output is 0, "operate the cmd have some problems ,please check  whether there is a in the lib " \
                            "directory resources.py "
    import libs.resources
    sys.exit(main())
