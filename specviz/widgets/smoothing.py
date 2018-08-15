import os

from qtpy.QtCore import QThread, Signal
from qtpy.QtWidgets import QDialog, QMessageBox
from qtpy.uic import loadUi

from specutils.manipulation.smoothing import (box_smooth, gaussian_smooth,
                                              trapezoid_smooth, median_smooth)
from ..utils import UI_PATH

KERNEL_REGISTRY = {
    """
    Dictionary to store available kernel options.

    KERNEL_REGISTRY:
        kernel_type: Type of kernel
            name: Display name
            unit_label: Display units of kernel size
            size_dimension: Dimension of kernel (width, radius, etc..)
            function: Smoothing function
    """
    "box": {"name": "Box",
            "unit_label": "Pixels",
            "size_dimension": "Width",
            "function": box_smooth},
    "gaussian": {"name": "Gaussian",
                 "unit_label": "Pixels",
                 "size_dimension": "Std Dev",
                 "function": gaussian_smooth},
    "trapezoid": {"name": "Trapezoid",
                  "unit_label": "Pixels",
                  "size_dimension": "Width",
                  "function": trapezoid_smooth},
    "median": {"name": "Median",
               "unit_label": "Pixels",
               "size_dimension": "Width",
               "function": median_smooth}
}


class SmoothingDialog(QDialog):
    def __init__(self, workspace, parent=None):
        super(SmoothingDialog, self).__init__(parent=parent)
        self.setWindowTitle("Spectral Smoothing")
        self.workspace = workspace

        self._smoothing_thread = None

        self.function = None
        self.data = None

        self._load_ui()

    def _load_ui(self):
        # Load UI form .ui file
        loadUi(os.path.join(UI_PATH, "smoothing.ui"), self)

        for index, data in enumerate(self.workspace.model.items):
            self.data_combo.addItem(data.name, index)
        self.data_combo.currentIndexChanged.connect(self._on_data_change)

        for key in KERNEL_REGISTRY:
            kernel = KERNEL_REGISTRY[key]
            self.kernel_combo.addItem(kernel["name"], key)
        self.kernel_combo.currentIndexChanged.connect(self._on_kernel_change)

        self.smooth_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.cancel)

        self._on_data_change(0)
        self._on_kernel_change(0)
        self.show()

    def _on_kernel_change(self, index):
        """Callback for kernel combo index change"""
        key = self.kernel_combo.currentData()
        kernel = KERNEL_REGISTRY[key]  # Kernel type
        self.size_label.setText(kernel["size_dimension"])
        self.unit_label.setText(kernel["unit_label"])
        self.function = kernel["function"]

    def _on_data_change(self, index):
        """Callback for data combo index change"""
        data_index = self.data_combo.currentData()
        self.data = self.workspace.model.items[data_index].data

    def accept(self):
        self.smooth_button.setEnabled(False)
        self.cancel_button.setEnabled(False)

        size = float(self.size_input.text())
        self._smoothing_thread = SmoothingThread(self.data, size, self.function)
        self._smoothing_thread.finished.connect(self.on_finished)
        self._smoothing_thread.exception.connect(self.on_exception)

        self._smoothing_thread.start()

    def on_finished(self, data):
        print(self.data)
        print(data)
        self.close()

    def on_exception(self, exception):
        self.smooth_button.setEnabled(True)
        self.cancel_button.setEnabled(True)

        info_box = QMessageBox(parent=self)
        info_box.setWindowTitle("Smoothing Error")
        info_box.setIcon(QMessageBox.Critical)
        info_box.setText(str(exception))
        info_box.setStandardButtons(QMessageBox.Ok)
        info_box.show()

    def cancel(self):
        self.close()


class SmoothingThread(QThread):
    finished = Signal(object)
    exception = Signal(Exception)

    def __init__(self, data, size, func, parent=None):
        super(SmoothingThread, self).__init__(parent)
        self._data = data
        self._size = size
        self._function = func
        self._tracker = None

    def run(self):
        """Run the thread."""
        try:
            new_data = self._function(self._data, self._size)
            self.finished.emit(new_data)
        except Exception as e:
            self.exception.emit(e)

