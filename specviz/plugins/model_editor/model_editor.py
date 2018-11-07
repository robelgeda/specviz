import os
import uuid
import re

import numpy as np
from astropy import units as u
from astropy.modeling import models, fitting, optimizers
from qtpy.QtCore import Qt
from qtpy.QtGui import QIcon, QValidator
from qtpy.QtWidgets import QAction, QMenu, QMessageBox, QToolButton, QWidget, QDialog,  QDialogButtonBox
from qtpy.uic import loadUi
from specutils.fitting import fit_lines
from specutils.spectra import Spectrum1D
from specutils.manipulation import extract_region
from specutils.spectra.spectral_region import SpectralRegion

from .equation_editor_dialog import ModelEquationEditorDialog
from .items import ModelDataItem
from .models import ModelFittingModel
from ...core.plugin import plugin

MODELS = {
    'Const1D': models.Const1D,
    'Linear1D': models.Linear1D,
    'Gaussian1D': models.Gaussian1D,
}

FITTERS = {
    "Levenberg-Marquardt": fitting.LevMarLSQFitter,
    "SLSQP Optimization": fitting.SLSQPLSQFitter,
    "Simplex": fitting.SimplexLSQFitter,
}


@plugin.plugin_bar("Model Editor", icon=QIcon(":/icons/012-file.svg"))
class ModelEditor(QWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fitting_options = {
            "fitter": "Levenberg-Marquardt",
            "max_iterations": optimizers.DEFAULT_MAXITER,
            "relative_error": optimizers.DEFAULT_ACC,
            "epsilon": optimizers.DEFAULT_EPS,
        }

        self._init_ui()

    def _init_ui(self):
        loadUi(os.path.abspath(
            os.path.join(os.path.dirname(__file__),
                         ".", "model_editor.ui")), self)

        # Populate the add mode button with a dropdown containing available
        # fittable model objects
        self.add_model_button.setPopupMode(QToolButton.InstantPopup)
        models_menu = QMenu(self.add_model_button)
        self.add_model_button.setMenu(models_menu)

        for k, v in MODELS.items():
            action = QAction(k, models_menu)
            action.triggered.connect(lambda x, m=v: self._add_fittable_model(m))
            models_menu.addAction(action)

        # Initially hide the model editor tools until user has selected an
        # editable model spectrum object
        self.editor_holder_widget.setHidden(True)
        self.setup_holder_widget.setHidden(False)

        self.equation_edit_button.clicked.connect(
            self._on_equation_edit_button_clicked)
        self.new_model_button.clicked.connect(self._on_create_new_model)
        self.remove_model_button.clicked.connect(self._on_remove_model)

        self.advanced_settings_button.clicked.connect(
            lambda: ModelAdvancedSettingsDialog(self, self).exec())

        self.data_selection_combo.setModel(self.hub.model)

        # When a plot data item is select, get its model editor model
        # representation
        self.hub.workspace.current_selected_changed.connect(
            self._on_plot_item_selected)

        # When the plot window changes, reset model editor
        self.hub.workspace.mdi_area.subWindowActivated.connect(self._on_new_plot_activated)

        # Connect the fit model button
        self.fit_button.clicked.connect(self._on_fit_clicked)

        self._setup_eq_editor()

    def _setup_eq_editor(self):
        # When the insert button is pressed, parse the current text of the
        # combo box and put that variable in the equation.
        self.insert_eq_button.clicked.connect(
            lambda: self.equation_text_edit.textCursor().insertHtml(
                self.model_list_combo.currentText()))

        # Whenever the text inside the text edit box changes, do two things:
        # 1. Parse the text and bold/color all verified variable names, and 2.
        # validate that the equation would actually produce a compound model.
        self.equation_text_edit.textChanged.connect(self._parse_variables)

    def _parse_variables(self):
        """
        Whenever the text in the text edit box changes, parse and highlight the
        verified model variable names.
        """
        full_string = self.equation_text_edit.toPlainText()

        model_data_item = self.hub.data_item
        if not isinstance(model_data_item, ModelDataItem):
            return

        model_editor_model = model_data_item.model_editor_model

        model_editor_model.equation = full_string

        for var in model_editor_model.fittable_models.keys():
            comp_reg = re.compile(r"\b{}\b".format(var))

            if len(comp_reg.findall(full_string)) > 0:
                full_string = re.sub(r"\b{}\b".format(var),
                                     "<span style='color:blue; font-weight:bold'>{0}</span>".format(var),
                                     full_string)

        # Store the cursor position because setting the html explicitly will
        # reset it to the beginning. Also, disable signals so that setting the
        # text in the box doesn't cause infinite recursion.
        cursor = self.equation_text_edit.textCursor()
        cursor_pos = cursor.position()
        self.equation_text_edit.blockSignals(True)
        self.equation_text_edit.setHtml(full_string)
        self.equation_text_edit.blockSignals(False)
        cursor.setPosition(cursor_pos)
        self.equation_text_edit.setTextCursor(cursor)
        self.equation_text_edit.repaint()

    def _update_status_text(self, state, status_text):
        """
        Update dialog status text depending on the state of the validator.
        """
        self.status_label.setText(status_text)

        # if state == QValidator.Acceptable:
        #     self.button_box.button(QDialogButtonBox.Ok).setEnabled(True)
        # else:
        #     self.button_box.button(QDialogButtonBox.Ok).setEnabled(False)

        # self.button_box.repaint()

    def new_message_box(self, text, info=None, icon=QMessageBox.Warning):
        message_box = QMessageBox()
        message_box.setText(text)
        message_box.setIcon(icon)
        if info is not None:
            message_box.setInformativeText(info)
        message_box.exec()
        return

    @plugin.tool_bar(name="New Model", icon=QIcon(":/icons/012-file.svg"))
    def on_new_model_triggered(self):
        self._on_create_new_model()

    def _on_create_new_model(self):
        if self.hub.data_item is None:
            self.new_message_box(text="No item selected, cannot create model.",
                                 info="There is currently no item selected. "
                                      "Please select an item before attempting"
                                      " to create a new model.")
            return

        # Set the currently displayed plugin panel widget to the model editor
        self.hub.set_active_plugin_bar(name="Model Editor")

        # Grab the currently selected plot data item
        new_spec = Spectrum1D(flux=np.zeros(self.hub.data_item.spectral_axis.size) * self.hub.data_item.flux.unit,
                              spectral_axis=self.hub.data_item.spectral_axis)

        model_data_item = ModelDataItem(model=ModelFittingModel(),
                                        name="Fittable Model Spectrum",
                                        identifier=uuid.uuid4(),
                                        data=new_spec)

        self.hub.append_data_item(model_data_item)

        plot_data_item = self.hub.plot_data_item_from_data_item(model_data_item)

        # Connect data change signals so that the plot updates when the user
        # changes a parameter in the model view model
        model_data_item.model_editor_model.dataChanged.connect(
            lambda tl, br, r, pi=plot_data_item: self._on_model_data_changed(tl, br, pi))

        model_data_item.model_editor_model.status_changed.connect(self._update_status_text)

    def _on_remove_model(self):
        """Remove an astropy model from the model editor tree view."""
        model_editor_model = self.model_tree_view.model()
        indexes = self.model_tree_view.selectionModel().selectedIndexes()

        if len(indexes) > 0:
            selected_idx = indexes[0]
            model_editor_model.removeRow(selected_idx.row())

        # Populate the drop down list with the model names
        self.model_list_combo.clear()
        self.model_list_combo.addItems(model_editor_model.fittable_models.keys())

    def _add_fittable_model(self, model):
        model_editor_model = self.model_tree_view.model()
        idx = model_editor_model.add_model(model())
        self.model_tree_view.setExpanded(idx, True)

        for i in range(0, 3):
            self.model_tree_view.resizeColumnToContents(i)

        # Populate the drop down list with the model names
        self.model_list_combo.clear()
        self.model_list_combo.addItems(model_editor_model.fittable_models.keys())

    def _on_model_data_changed(self, top_left, bottom_right, plot_data_item):
        if top_left.column() == 1:
            # We only want to update the model if the parameter values
            # are changed, which exist in column 1.
            plot_data_item.set_data()
        elif top_left.column() == 0:
            # In this case, the user has renamed a model. Since the equation
            # editor now doesn't know about the old model, reset the equation
            self.hub.data_item.model_editor_model.reset_equation()

    def _on_equation_edit_button_clicked(self):
        # Get the current model
        model_data_item = self.hub.data_item

        if not isinstance(model_data_item, ModelDataItem):
            self.new_message_box(text="No model available.",
                                 info="The currently selected item does not"
                                      " contain a fittable model. Create a new"
                                      " one, or select an item containing a model.")
            return

        equation_editor_dialog = ModelEquationEditorDialog(
            model_data_item.model_editor_model)
        equation_editor_dialog.accepted.connect(self.hub.plot_item.set_data)
        equation_editor_dialog.exec_()

    def _clear_tree_view(self):
        self.model_tree_view.setModel(None)
        self.editor_holder_widget.setHidden(True)
        self.setup_holder_widget.setHidden(False)

    def _on_new_plot_activated(self):
        plot_data_item = self.hub.plot_item
        if plot_data_item is not None:
            if isinstance(plot_data_item.data_item, ModelDataItem):
                return self._on_plot_item_selected(plot_data_item)
        self._clear_tree_view()

    def _on_plot_item_selected(self, plot_data_item):
        if not isinstance(plot_data_item.data_item, ModelDataItem):
            return self._clear_tree_view()

        self.editor_holder_widget.setHidden(False)
        self.setup_holder_widget.setHidden(True)

        model_data_item = plot_data_item.data_item
        model_editor_model = model_data_item.model_editor_model

        # Set the model on the tree view and expand all children initially.
        self.model_tree_view.setModel(model_editor_model)
        self.model_tree_view.expandAll()

        for i in range(0, 3):
            self.model_tree_view.resizeColumnToContents(i)

        # Populate the drop down list with the model names
        self.model_list_combo.clear()
        self.model_list_combo.addItems(model_editor_model.fittable_models.keys())

        self.equation_text_edit.setPlainText(model_editor_model.equation)

        # Do an initial validation just to easily update the status text when
        # the dialog is first shown.
        model_editor_model.evaluate()

    def _combine_all_workspace_regions(self):
        """Get current widget region."""
        regions = self.hub.list_all_regions
        if len(regions) == 0:
            return None

        units = u.Unit(self.hub.plot_window.plot_widget.spectral_axis_unit or "")

        positions = []
        for region in regions:
            pos = (region.getRegion()[0] * units,
                   region.getRegion()[1] * units)
            if pos is not None:
                positions.append(pos)

        return SpectralRegion(positions)

    @staticmethod
    def _astropy_fit(spec, model, fitter):
        if isinstance(spec, list):
            x = []
            y = []
            for i in range(len(spec)):
                if spec[i] is None:
                    continue
                x += list(spec[i].spectral_axis.value)
                y += list(spec[i].flux.value)
            x = np.array(x)
            y = np.array(y)
        else:
            if spec is None:
                return
            x = spec.spectral_axis.value
            y = spec.flux.value

        if len(x) == 0:
            return

        return fitter(model, x, y)

    def _on_fit_clicked(self, spectrum_data_item=None):
        # The spectrum_data_item would be the data item that this model is to
        # be fit to. This selection is done via the data_selection_combo.
        combo_index = self.data_selection_combo.currentIndex()
        spectrum_data_item = self.data_selection_combo.itemData(combo_index)

        # If user chooses a model instead of a data item, notify and return
        if isinstance(spectrum_data_item, ModelDataItem):
            return self.new_message_box(text="Selected data is a model.",
                                        info="The currently selected data "
                                             "is a model. Please select a "
                                             "data item containing spectra.")

        # Grab the currntly selected plot data item from the data list
        plot_data_item = self.hub.plot_item

        # If this item is not a model data item, bail
        if not isinstance(plot_data_item.data_item, ModelDataItem):
            return

        # Compose the compound model from the model editor sub model tree view
        model_editor_model = plot_data_item.data_item.model_editor_model
        result = model_editor_model.evaluate()

        if result is None:
            return self.new_message_box(text="Please add models to fit.",
                                        info="Models can be added by clicking the"
                                             " green \"add\" button and selecting a"
                                             " model from the drop-down menu")

        spectrum = spectrum_data_item.spectrum
        spectral_region = self._combine_all_workspace_regions()

        if spectral_region is not None:
            try:
                idx1, idx2 = spectral_region.bounds
                if not idx1 == idx2:
                    spectrum = extract_region(spectrum, spectral_region)
            except ValueError as e:
                return

        # Load options
        fitter = FITTERS[self.fitting_options["fitter"]]
        max_iterations = self.fitting_options["max_iterations"]
        relative_error = self.fitting_options["relative_error"]
        epsilon = self.fitting_options["epsilon"]

        # Run the compound model through the specutils fitting routine
        """
        # Uncomment for when specutils function is working
        fit_mod = fit_lines(spectrum_data_item.spectrum, result, fitter=fitter())
        """

        print("spectral_region", spectral_region)

        # fit_mod = fit_lines(spectrum_data_item.spectrum, result, fitter=fitter(),
        #                     window=spectral_region,
        #                     ignore_units=True)

        fit_mod = fit_lines(spectrum_data_item.spectrum, result, fitter=fitter(),
                            window=spectral_region,
                            ignore_units=True,
                            maxiter=max_iterations,
                            acc=relative_error,
                            epsilon=epsilon)

        #fit_mod = self._astropy_fit(spectrum, result, fitter())
        print(fit_mod)
        if fit_mod is None:
            return

        # Fitted quantity models do not preserve the names of the sub models
        # which are used to relate the fitted sub models back to the displayed
        # models in the model editor. Go through and hope that their order is
        # preserved.

        """
        # Uncomment for when specutils function is working
        if result.n_submodels() > 1:
            for i, x in enumerate(result):
                fit_mod.unitless_model._submodels[i].name = x.name
            sub_mods = [x for x in fit_mod.unitless_model]
        else:
            fit_mod.unitless_model.name = result.name
            sub_mods = [fit_mod.unitless_model]
        """

        if result.n_submodels() > 1:
            sub_mods = [x for x in fit_mod._submodels]
            for i, x in enumerate(result):
                fit_mod._submodels[i].name = x.name
        else:
            fit_mod.name = result.name
            sub_mods = [fit_mod]

        # Get a list of the displayed name for each sub model in the tree view
        disp_mods = {item.text(): item for item in model_editor_model.items}

        for i, sub_mod in enumerate(sub_mods):
            # Get the base astropy model object
            model_item = disp_mods.get(sub_mod.name)

            # For each of the children `StandardItem`s, parse out their
            # individual stored values
            for cidx in range(model_item.rowCount()):
                param_name = model_item.child(cidx, 0).data()

                if result.n_submodels() > 1:
                    parameter = getattr(fit_mod, "{0}_{1}".format(param_name, i))
                else:
                    parameter = getattr(fit_mod, param_name)

                model_item.child(cidx, 1).setText(str(parameter.value))
                model_item.child(cidx, 1).setData(parameter.value, Qt.UserRole + 1)
                model_item.child(cidx, 3).setData(parameter.fixed, Qt.UserRole + 1)

        for i in range(0, 3):
            self.model_tree_view.resizeColumnToContents(i)

        # Update the displayed data on the plot
        plot_data_item.set_data()


class ModelAdvancedSettingsDialog(QDialog):
    def __init__(self, model_editor, parent=None):
        super().__init__(parent)

        self.model_editor = model_editor
        self._init_ui()

    def _init_ui(self):
        loadUi(os.path.abspath(
            os.path.join(os.path.dirname(__file__), ".",
                         "model_advanced_settings.ui")), self)

        self.fitting_type_combo_box.addItems(list(FITTERS.keys()))

        self.buttonBox.accepted.connect(self.apply_settings)
        self.buttonBox.rejected.connect(self.cancel)

        fitting_options = self.model_editor.fitting_options

        self.max_iterations_line_edit.setText(str(fitting_options["max_iterations"]))
        self.relative_error_line_edit.setText(str(fitting_options["relative_error"]))
        self.epsilon_line_edit.setText(str(fitting_options["epsilon"]))
        index = self.fitting_type_combo_box.findText(fitting_options["fitter"],
                                                     Qt.MatchFixedString)
        if index >= 0:
            self.fitting_type_combo_box.setCurrentIndex(index)

        # This section disables some of the options
        # until specutils fitting functions have been
        # updated to take them into account:
        # self.max_iterations_line_edit.setDisabled(True)
        # self.relative_error_line_edit.setDisabled(True)
        # self.epsilon_line_edit.setDisabled(True)

    def _validate_inputs(self):
        """
        Check if user inputs are valid.
        return
        ------
        success : bool
            True if all input boxes are valid.
        """
        red = "background-color: rgba(255, 0, 0, 128);"
        success = True

        for widget in [self.max_iterations_line_edit]:
            try:
                int(widget.text())
                widget.setStyleSheet("")
            except ValueError:
                widget.setStyleSheet(red)
                success = False

        for widget in [self.relative_error_line_edit,
                       self.epsilon_line_edit]:
            try:
                float(widget.text())
                widget.setStyleSheet("")
            except ValueError:
                widget.setStyleSheet(red)
                success = False

        return success

    def apply_settings(self):
        if not self._validate_inputs():
            return

        fitting_type = self.fitting_type_combo_box.currentText()
        max_iterations = int(self.max_iterations_line_edit.text())
        relative_error = float(self.relative_error_line_edit.text())
        epsilon = float(self.epsilon_line_edit.text())

        self.model_editor.fitting_options = {
            "fitter": fitting_type,
            "max_iterations": max_iterations,
            "relative_error": relative_error,
            "epsilon": epsilon,
        }

        self.close()

    def cancel(self):
        self.close()
