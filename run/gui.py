import sys
import pandas as pd
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QGridLayout, QLabel, QComboBox, QLineEdit, 
                             QCheckBox, QPushButton, QScrollArea, QFrame,
                             QMessageBox, QSpinBox, QDoubleSpinBox, QStyle, QProgressBar)
from PyQt6.QtCore import Qt, QUrl, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage
from run import config, data_loader, filtering, mapping

class ConsolePage(QWebEnginePage):
    def __init__(self, parent=None, callback=None):
        super().__init__(parent)
        self.callback = callback

    def javaScriptConsoleMessage(self, level, message, line, source):
        # Print logs for debug, but also check for commands
        # print(f"JS: {message}", flush=True) 
        if self.callback:
            self.callback(message)

class CheckableComboBox(QComboBox):
    def __init__(self):
        super().__init__()
        self.view().pressed.connect(self.handle_item_pressed)
        self.setModel(self.model())
        self.changed = False

    def handle_item_pressed(self, index):
        item = self.model().itemFromIndex(index)
        text = item.text()
        
        if text == "(Select All)":
            for i in range(self.count()):
                it = self.model().item(i)
                if it.text() not in ["(Select All)", "(Clear)"]:
                    it.setCheckState(Qt.CheckState.Checked)
        elif text == "(Clear)":
            for i in range(self.count()):
                it = self.model().item(i)
                if it.text() not in ["(Select All)", "(Clear)"]:
                    it.setCheckState(Qt.CheckState.Unchecked)
        else:
            if item.checkState() == Qt.CheckState.Checked:
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                item.setCheckState(Qt.CheckState.Checked)
        self.changed = True

    def hidePopup(self):
        if not self.changed:
            super().hidePopup()
        self.changed = False

    def get_checked_items(self):
        checked_items = []
        for i in range(self.count()):
            item = self.model().item(i, 0)
            if item.text() in ["(Select All)", "(Clear)"]:
                continue
            if item.checkState() == Qt.CheckState.Checked:
                checked_items.append(item.text())
        return checked_items

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Global flight dispatch")
        self.resize(1200, 800)

        # State
        self.df = data_loader.load_data()
        
        # Calculate global airport frequency for coloring logic
        if not self.df.empty:
            all_iata = pd.concat([self.df['dep_airport_iata'], self.df['arr_airport_iata']])
            self.airport_counts = all_iata.value_counts()
        else:
            self.airport_counts = pd.Series()
            
        self.current_theme = 'dark'
        self.map_type = 'Hybrid'
        self.filters = {} # Stores current filter widgets
        self.map_view = None
        self.current_filtered_df = self.df  # Start with full dataset

        # UI Setup
        self.setup_ui()
        self.apply_theme()
        
        # Initial map (full dataset)
        self.render_map(self.df)
        
        # Determine filter columns
        if self.df.empty:
            QMessageBox.critical(self, "Error", "No flight data found or CSV is empty.")

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Top Bar (Filters) ---
        filter_container = QFrame()
        filter_container.setObjectName("filter_container")
        filter_layout = QVBoxLayout(filter_container)
        filter_layout.setContentsMargins(10, 10, 10, 10)
        
        # Filter Header
        header_layout = QHBoxLayout()
        title = QLabel("Flight Filters")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        header_layout.addWidget(title)
        
        header_layout.addStretch()
        
        # Theme Toggle
        self.theme_btn = QPushButton("Switch to Dark Mode")
        self.theme_btn.clicked.connect(self.toggle_theme)
        header_layout.addWidget(self.theme_btn)

        # Map Type
        self.map_type_combo = QComboBox()
        self.map_type_combo.addItems(config.TILES.keys())
        self.map_type_combo.currentTextChanged.connect(self.change_map_type)
        header_layout.addWidget(QLabel("Map Type:"))
        header_layout.addWidget(self.map_type_combo)

        filter_layout.addLayout(header_layout)

        # Dynamic Filters Area
        # Use a simple container with grid layout instead of scroll area for compactness
        filters_widget = QWidget()
        self.filters_grid = QGridLayout(filters_widget)
        self.filters_grid.setContentsMargins(0, 0, 0, 0)
        self.filters_grid.setSpacing(5) # Tight spacing
        
        self.create_filters()
        
        filter_layout.addWidget(filters_widget)
        
        # Filter Buttons
        btn_layout = QHBoxLayout()
        self.apply_btn = QPushButton("Apply Filters & Show Map")
        self.apply_btn.clicked.connect(self.on_apply_filters)
        self.apply_btn.setMinimumWidth(200)
        
        reset_btn = QPushButton("Reset Filters")
        reset_btn.clicked.connect(self.reset_filters)

        btn_layout.addStretch()
        btn_layout.addWidget(reset_btn)
        btn_layout.addWidget(self.apply_btn)
        
        # Progress Bar (right side of filters)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # Indeterminate mode
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedWidth(150)
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid grey; border-radius: 4px; text-align: center; } QProgressBar::chunk { background-color: #0078d4; }")
        
        btn_layout.addWidget(self.progress_bar)
        
        filter_layout.addLayout(btn_layout)
        
        # Add filter container with 0 stretch so it only takes needed space
        main_layout.addWidget(filter_container, 0)

        # --- Map Area ---
        self.map_view = QWebEngineView()
        
        # Enable console logging using custom Page
        page = ConsolePage(self.map_view, callback=self.on_js_console)
        # Use default profile unless specified otherwise
        self.map_view.setPage(page)
        
        # Map will be populated after setup_ui() returns
        # Add map view with stretch factor 1 to take all remaining space
        main_layout.addWidget(self.map_view, 1)

    def create_filters(self):
        # Clear existing
        try:
            # Clear layout
            while self.filters_grid.count():
                child = self.filters_grid.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
        except:
            pass
        self.filters = {}
        
        columns = config.FILTER_COLUMNS
        if not columns and not self.df.empty:
            columns = self.df.columns[:5] 
            
        row = 0
        col_count = 0
        COL_LIMIT = 3 # 3 columns max

        for col in columns:
            if col not in self.df.columns:
                continue
                
            group = QFrame()
            group.setStyleSheet("margin: 2px; border: 1px solid #ccc; border-radius: 4px; padding: 2px;")
            
            # --- Important layout fix ---
            # We use a grid within the group frame itself to pack label and widget nicely
            g_layout = QGridLayout(group) 
            g_layout.setContentsMargins(5, 5, 5, 5)
            g_layout.setSpacing(5)
            
            display_name = config.COLUMN_DISPLAY_NAMES.get(col, col)
            label = QLabel(display_name)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter) # Right align label
            label.setStyleSheet("font-size: 11px; font-weight: bold; padding-right: 5px;")
            g_layout.addWidget(label, 0, 0) # Label at col 0
            
            # Decide widget type based on data
            widget = None
            filter_info = {'col': col}

            is_numeric = pd.api.types.is_numeric_dtype(self.df[col])
            unique_vals = filtering.get_unique_values(self.df, col)
            
            # --- WIDGET CREATION ---
            if len(unique_vals) < 15 and not is_numeric: # Small set of options
                widget = CheckableComboBox()
                widget.setFixedHeight(24)
                widget.setStyleSheet("font-size: 11px;")
                widget.addItem("(Select All)")
                widget.addItem("(Clear)")
                for i in [0, 1]:
                   widget.model().item(i).setCheckState(Qt.CheckState.Unchecked) 
                
                for val in unique_vals:
                    widget.addItem(str(val))
                    item = widget.model().item(widget.count()-1, 0)
                    item.setCheckState(Qt.CheckState.Unchecked)
                filter_info['type'] = 'select'
                
            elif is_numeric:
                container = QWidget()
                h = QHBoxLayout(container)
                h.setContentsMargins(0,0,0,0)
                h.setSpacing(2)
                
                op_combo = QComboBox()
                # Map display text to internal operator
                op_combo.addItem(">", ">")
                op_combo.addItem("<", "<")
                op_combo.addItem("=", "equals")
                op_combo.addItem(">=", ">=")
                op_combo.addItem("<=", "<=")
                
                op_combo.setFixedWidth(60) # Increased from 50
                op_combo.setFixedHeight(24)
                op_combo.setStyleSheet("font-size: 11px;")
                
                val_input = QDoubleSpinBox()
                val_input.setRange(-999999, 999999)
                val_input.setValue(val_input.minimum())
                val_input.setSpecialValueText(" ") 
                val_input.setFixedHeight(24)
                val_input.setStyleSheet("font-size: 11px;")
                
                h.addWidget(op_combo)
                h.addWidget(val_input)
                widget = container
                
                filter_info['type'] = 'number'
                filter_info['widgets'] = (op_combo, val_input)
                
            else: # Text search
                container = QWidget()
                h = QHBoxLayout(container) # Horizontal now for space
                h.setContentsMargins(0,0,0,0)
                h.setSpacing(2)
                
                op_combo = QComboBox()
                # Map display text to internal operator
                op_combo.addItem("Contains", "contains")
                op_combo.addItem("Equals", "equals")
                op_combo.addItem("Starts with", "starts_with")
                op_combo.addItem("Ends with", "ends_with")
                
                # Allow it to size naturally, or set a reasonable min width
                op_combo.setMinimumWidth(100)  # Increased from 80
                op_combo.setFixedHeight(24) 
                op_combo.setStyleSheet("font-size: 11px;")
                
                txt_input = QLineEdit()
                txt_input.setPlaceholderText("Search...")
                txt_input.setFixedHeight(24)
                txt_input.setStyleSheet("font-size: 11px;")
                
                h.addWidget(op_combo)
                h.addWidget(txt_input)
                widget = container
                
                filter_info['type'] = 'text'
                filter_info['widgets'] = (op_combo, txt_input)
            
            if widget:
                try:
                    if isinstance(widget, CheckableComboBox):
                        widget.lineEdit().setPlaceholderText("Select...")
                except:
                    pass

                g_layout.addWidget(widget, 0, 1) # Widget at col 1
                g_layout.setColumnStretch(1, 1) # Widget takes remaining space
                
                filter_info['widget'] = widget
                self.filters[col] = filter_info
                
                self.filters_grid.addWidget(group, row, col_count)
                
                col_count += 1
                if col_count >= COL_LIMIT:
                    col_count = 0
                    row += 1

    def toggle_theme(self):
        if self.current_theme == 'light':
            self.current_theme = 'dark'
            self.theme_btn.setText("Switch to Light Mode")
        else:
            self.current_theme = 'light'
            self.theme_btn.setText("Switch to Dark Mode")
        self.apply_theme()
        self.render_map(self.current_filtered_df)

    def apply_theme(self):
        if self.current_theme == 'dark':
            self.setStyleSheet(config.DARK_STYLE)
        else:
            self.setStyleSheet(config.LIGHT_STYLE)

    def change_map_type(self, text):
        self.map_type = text
        self.render_map(self.current_filtered_df)

    def get_current_filters(self):
        active_filters = {}
        for col, info in self.filters.items():
            ftype = info['type']
            
            if ftype == 'select':
                widget = info['widget']
                checked = widget.get_checked_items()
                if checked:
                    active_filters[col] = {
                        'type': 'select',
                        'value': checked,
                        'operator': 'in'
                    }
                    print(f"Filter found: {col} = {checked}", flush=True)

            elif ftype == 'number':
                op_widget, val_widget = info['widgets']
                if val_widget.text() != " ":
                    active_filters[col] = {
                        'type': 'number',
                        'operator': op_widget.currentData() if op_widget.currentData() else op_widget.currentText(),
                        'value': val_widget.value()
                    }
                    print(f"Filter found: {col} = {val_widget.value()}", flush=True)

            elif ftype == 'text':
                op_widget, val_widget = info['widgets']
                text = val_widget.text().strip()
                if text:
                    active_filters[col] = {
                        'type': 'text',
                        'operator': op_widget.currentData() if op_widget.currentData() else op_widget.currentText(),
                        'value': text
                    }
                    print(f"Filter found: {col} = {text}", flush=True)
        
        return active_filters
    
    def on_apply_filters(self):
        current_filters = self.get_current_filters()
        if not current_filters:
            QMessageBox.warning(self, "No Filters", "Please select or enter at least one filter option.")
            return

        print(f"Applying filters: {current_filters}", flush=True)
        
        # Show progress
        self.progress_bar.setVisible(True)
        self.apply_btn.setEnabled(False)
        self.apply_btn.setText("Rendering...")
        QApplication.processEvents() # Force UI update
        
        # Use QTimer to allow UI to render first frame of progress before blocking work starts
        QTimer.singleShot(100, lambda: self.process_map_update(current_filters))

    def process_map_update(self, current_filters):
        try:
            self.update_map(current_filters)
        finally:
            self.progress_bar.setVisible(False)
            self.apply_btn.setEnabled(True)
            self.apply_btn.setText("Apply Filters & Show Map")

    def reset_filters(self):
        # Reset widgets
        for col, info in self.filters.items():
            ftype = info['type']
            if ftype == 'select':
                widget = info['widget']
                for i in range(widget.count()):
                    widget.model().item(i, 0).setCheckState(Qt.CheckState.Unchecked)
            elif ftype == 'number':
                _, val_widget = info['widgets']
                val_widget.setValue(val_widget.minimum()) # Triggers special text
            elif ftype == 'text':
                _, val_widget = info['widgets']
                val_widget.clear()
        
        # Reset to full dataset
        self.current_filtered_df = self.df
        self.render_map(self.df)

    def on_js_console(self, message):
        if not message: return
        # print(f"JS: {message}", flush=True)
        
        if message.startswith("REQUEST_ROUTES|"):
            try:
                code = message.split("|")[1]
                # Filter current DF for matches
                df = getattr(self, 'current_filtered_df', pd.DataFrame())
                if df.empty: 
                    self.map_view.page().runJavaScript(f"loadRoutes([])")
                    return

                # Find matches (Dep OR Arr)
                matches = df[ (df['dep_airport_iata'] == code) | (df['arr_airport_iata'] == code) ]
                
                # Convert to JSON format expected by JS
                routes = []
                for _, row in matches.iterrows():
                    routes.append({
                        "dep": str(row.get('dep_airport_iata', '')),
                        "arr": str(row.get('arr_airport_iata', '')),
                        "flight": str(row.get('flight_number', '')),
                        "type": str(row.get('type', '')),
                        # Additional details
                        "callsign": str(row.get('calsign', '')),
                        "type_icao": str(row.get('type_icao', '')),
                        "reg": str(row.get('reg', '')),
                        "dep_icao": str(row.get('dep_airport_icao', '')),
                        "arr_icao": str(row.get('arr_airport_icao', ''))
                    })
                
                import json
                routes_json = json.dumps(routes)
                
                # Call JS
                self.map_view.page().runJavaScript(f"loadRoutes({routes_json})")
                
            except Exception as e:
                print(f"Error handling route request: {e}", flush=True)

    def render_map(self, filtered_df):
        """Render the map from the given dataframe."""
        self.current_filtered_df = filtered_df
        try:
            html = mapping.create_map_html(filtered_df, self.map_type, self.current_theme, self.airport_counts)
            self.map_view.setHtml(html)
        except Exception as e:
            print(f"Error generating map: {e}", flush=True)

    def update_map(self, filters=None):
        """Apply filters (if any) and re-render."""
        filtered_df = self.df

        if filters:
            try:
                filtered_df = filtering.apply_filters(self.df, filters)
                print(f"Filtered result: {len(filtered_df)} rows", flush=True)
            except Exception as e:
                print(f"Error filtering: {e}", flush=True)
                return

        if filtered_df.empty and filters:
            QMessageBox.information(self, "No Results", "No flights matched your filter criteria.")

        self.render_map(filtered_df)

    def process_map_update(self, current_filters):
        try:
            self.update_map(current_filters)
        finally:
            self.progress_bar.setVisible(False)
            self.apply_btn.setEnabled(True)
            self.apply_btn.setText("Apply Filters & Show Map")

def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
