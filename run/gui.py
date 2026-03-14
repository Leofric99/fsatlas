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
        self.setWindowTitle("FSDispatch")
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
        
        # Use Grid Layout to allow overlay
        main_layout = QGridLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Map Area (Background) ---
        self.map_view = QWebEngineView()
        
        # Enable console logging using custom Page
        page = ConsolePage(self.map_view, callback=self.on_js_console)
        self.map_view.setPage(page)
        
        # Add map view to grid (0,0) so it covers everything
        main_layout.addWidget(self.map_view, 0, 0)

        # --- Show Filters Button (Generally Hidden) ---
        self.show_filters_btn = QPushButton("Filters ▼")
        self.show_filters_btn.clicked.connect(self.toggle_filters_visibility)
        self.show_filters_btn.setVisible(True)
        self.show_filters_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # Add to top-right
        main_layout.addWidget(self.show_filters_btn, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        # --- Filters Overlay (Foreground) ---
        self.filter_container = QFrame()
        self.filter_container.setObjectName("filter_container")
        self.filter_container.hide() # Start hidden by default
        self.filter_container.setStyleSheet("""
            QFrame#filter_container {
                background-color: rgba(255, 255, 255, 0.85); 
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
                border-bottom: 1px solid #ccc;
                border-left: 1px solid #ccc;
                border-right: 1px solid #ccc;
            }
        """)

        filter_layout = QVBoxLayout(self.filter_container)
        filter_layout.setContentsMargins(15, 10, 15, 5)
        
        # 1. Filter Header (Always visible)
        header_layout = QHBoxLayout()
        title = QLabel("Flight Filters")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #333;")
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

        # 2. Collapsible Content Wrapper
        self.filter_content = QWidget()
        content_layout = QVBoxLayout(self.filter_content)
        content_layout.setContentsMargins(0, 5, 0, 0)
        content_layout.setSpacing(5)

        # Dynamic Filters Area
        self.filters_widget = QWidget()
        self.filters_grid = QGridLayout(self.filters_widget)
        self.filters_grid.setContentsMargins(0, 0, 0, 0)
        self.filters_grid.setSpacing(5)
        
        self.create_filters()
        content_layout.addWidget(self.filters_widget)
        
        # Filter Buttons
        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 5, 0, 0)
        
        self.apply_btn = QPushButton("Apply Filters & Show Map")
        self.apply_btn.clicked.connect(self.on_apply_filters)
        self.apply_btn.setMinimumWidth(200)
        
        reset_btn = QPushButton("Reset Filters")
        reset_btn.clicked.connect(self.reset_filters)

        btn_layout.addStretch()
        btn_layout.addWidget(reset_btn)
        btn_layout.addWidget(self.apply_btn)
        
        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # Indeterminate mode
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedWidth(150)
        self.progress_bar.setStyleSheet("QProgressBar { border: 1px solid grey; border-radius: 4px; text-align: center; } QProgressBar::chunk { background-color: #0078d4; }")
        
        btn_layout.addWidget(self.progress_bar)
        
        content_layout.addWidget(btn_widget)
        
        filter_layout.addWidget(self.filter_content)

        # 3. Toggle Button (Bottom Corner)
        toggle_layout = QHBoxLayout()
        toggle_layout.addStretch() # Push to right
        
        self.toggle_btn = QPushButton("▲") 
        self.toggle_btn.setFixedSize(24, 20)
        self.toggle_btn.setToolTip("Collapse filters")
        self.toggle_btn.clicked.connect(self.toggle_filters_visibility)
        self.toggle_btn.setStyleSheet("""
            QPushButton {
                border: none; 
                background: transparent; 
                font-weight: bold;
                color: #555;
            }
            QPushButton:hover {
                color: #000;
                background-color: rgba(0,0,0,0.05);
                border-radius: 3px;
            }
        """)
        toggle_layout.addWidget(self.toggle_btn)
        
        filter_layout.addLayout(toggle_layout)
        
        # Add filter overlay to main grid (0,0) aligned Top
        # We wrap it in a container widget if we want margin from top/screen edges, 
        # or just add margins to main_layout if needed.
        # Here we align top so it sticks to the top.
        main_layout.addWidget(self.filter_container, 0, 0, Qt.AlignmentFlag.AlignTop)

        # Force initial style now that filter_container exists
        self.apply_theme()

    def toggle_filters_visibility(self):
        """
        Toggles between [Full Panel Visible] and [Only 'Filters' Button Visible].
        """
        is_expanded = self.filter_container.isVisible()
        
        if is_expanded:
            # Collapse: Hide panel, Show button
            self.filter_container.hide()
            self.show_filters_btn.show()
        else:
            # Expand: Show panel, Hide button
            self.filter_container.show()
            self.show_filters_btn.hide()

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
            # Set background: transparent so the main container's opacity shows through
            group.setStyleSheet("margin: 2px; border: 1px solid #ccc; border-radius: 4px; padding: 2px; background-color: transparent;")
            
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
        # Only re-render map if data hasn't changed, just style update
        # But change_map_type does render_map, so we just call that or update map HTML
        self.render_map(self.current_filtered_df)

    def apply_theme(self):
        style = config.DARK_STYLE if self.current_theme == 'dark' else config.LIGHT_STYLE
        self.setStyleSheet(style)
        
        if not hasattr(self, 'filter_container'):
            return

        if self.current_theme == 'dark':
            bg_color = "rgba(30, 30, 30, 0.85)"
            border_color = "#555"
            text_color = "#ddd"
            input_bg = "rgba(40, 40, 40, 0.9)"
            input_text = "white"
            input_border = "#666"
            toggle_color = "#aaa"
        else:
            bg_color = "rgba(255, 255, 255, 0.85)"
            border_color = "#ccc"
            text_color = "#333"
            input_bg = "rgba(255, 255, 255, 0.9)"
            input_text = "black"
            input_border = "#ccc"
            toggle_color = "#555"

        sheet = f"""
            QFrame#filter_container {{
                background-color: {bg_color}; 
                border-bottom-left-radius: 10px;
                border-bottom-right-radius: 10px;
                border-bottom: 1px solid {border_color};
                border-left: 1px solid {border_color};
                border-right: 1px solid {border_color};
            }}
            /* Make layouts and containers transparent */
            QFrame#filter_container QWidget {{
                background-color: transparent;
            }}
            QFrame#filter_container QLabel {{
                color: {text_color};
                background-color: transparent;
            }}
            /* Inputs need background */
            QFrame#filter_container QComboBox, 
            QFrame#filter_container QLineEdit, 
            QFrame#filter_container QDoubleSpinBox,
            QFrame#filter_container QSpinBox {{
                background-color: {input_bg};
                color: {input_text};
                border: 1px solid {input_border};
                border-radius: 3px;
            }}
            /* PushButtons (except toggle which is transparent) need background */
            QFrame#filter_container QPushButton {{
               background-color: {input_bg};
               border: 1px solid {input_border};
               border-radius: 4px;
               padding: 4px 8px;
               color: {input_text};
            }}
            QFrame#filter_container QPushButton:hover {{
               background-color: {border_color}; 
            }}
        """
        self.filter_container.setStyleSheet(sheet)
        
        # Style the "Show Filters" button
        if hasattr(self, 'show_filters_btn'):
            self.show_filters_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {bg_color};
                    border: 1px solid {border_color};
                    border-top: none; 
                    border-bottom-left-radius: 8px;
                    border-bottom-right-radius: 8px;
                    padding: 6px 12px;
                    color: {text_color};
                    font-weight: bold;
                    margin-right: 60px;
                }}
                QPushButton:hover {{
                    background-color: {input_bg};
                }}
            """)
        
        if hasattr(self, 'toggle_btn'):
             self.toggle_btn.setStyleSheet(f"border: none; background: transparent; color: {toggle_color}; font-weight: bold;")

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
                        "arr_icao": str(row.get('arr_airport_icao', '')),
                        "airline": str(row.get('owner', ''))
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
