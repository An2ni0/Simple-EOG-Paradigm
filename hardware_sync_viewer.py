import sys
import os
import json
import numpy as np
import mne
import mne.filter
from PyQt5 import QtWidgets, QtCore, QtGui
import pyqtgraph as pg

class HardwareSyncViewer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EOG & DAQ Hardware Sync Visualizer")
        self.resize(1350, 850)
        
        # Stylesheet for a modern, clean, premium UI look
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f4f5f7;
            }
            QWidget {
                font-family: "Segoe UI", Arial, sans-serif;
            }
            QGroupBox {
                font-size: 12px;
                font-weight: bold;
                border: 1px solid #dcdfe6;
                border-radius: 6px;
                margin-top: 15px;
                padding-top: 15px;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 5px;
                color: #409eff;
            }
            QPushButton {
                background-color: #409eff;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #66b1ff;
            }
            QPushButton:pressed {
                background-color: #3a8ee6;
            }
            QLabel {
                color: #606266;
                font-size: 11px;
                font-weight: normal;
            }
            QTableWidget {
                border: 1px solid #e4e7ed;
                background-color: #ffffff;
                gridline-color: #f2f6fc;
                font-size: 11px;
                border-radius: 4px;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #f5f7fa;
                padding: 5px;
                border: 1px solid #e4e7ed;
                font-weight: bold;
                color: #606266;
            }
            QScrollBar:horizontal {
                border: 1px solid #dcdfe6;
                background: #f5f7fa;
                height: 16px;
                margin: 0px 16px 0 16px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal {
                background: #dcdfe6;
                min-width: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #c0c4cc;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                background: none;
            }
            QDoubleSpinBox, QSpinBox {
                border: 1px solid #dcdfe6;
                border-radius: 4px;
                padding: 4px;
                background: #ffffff;
                color: #606266;
            }
            QDoubleSpinBox:focus, QSpinBox:focus {
                border-color: #409eff;
            }
            QCheckBox {
                font-size: 11px;
                color: #606266;
            }
            QSplitter::handle {
                background-color: #dcdfe6;
            }
        """)

        # Data states (EEG BDF)
        self.raw = None
        self.raw_data_scaled = None
        self.data = None
        self.times = None
        self.events = []
        self.event_lines = []
        self.curves = []
        self.sfreq = 1000.0
        self.n_channels = 0
        self.ch_names = []
        self.filepath = ""
        self.raw_data_loaded = False

        # Data states (DAQ segments list for multi-segment files)
        self.daq_loaded = False
        self.daq_bin_path = ""
        self.daq_segments = []

        self.init_ui()
        self.load_default_file()

    def init_ui(self):
        # Main Layout splitter
        main_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.setCentralWidget(main_splitter)

        # ------------------ LEFT PANEL (SIDEBAR) ------------------
        sidebar = QtWidgets.QWidget()
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(10, 10, 10, 10)
        
        # Scroll Area for sidebar widgets
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll_widget = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(10)

        # 1. File Loader Button (EEG BDF)
        self.import_button = QtWidgets.QPushButton("📂 Open BDF File")
        self.import_button.setIcon(self.style().standardIcon(QtWidgets.QStyle.SP_DialogOpenButton))
        self.import_button.clicked.connect(self.open_file_dialog)
        scroll_layout.addWidget(self.import_button)

        # 2. File Metadata Group
        meta_group = QtWidgets.QGroupBox("EEG File Metadata")
        meta_layout = QtWidgets.QVBoxLayout(meta_group)
        self.meta_table = QtWidgets.QTableWidget()
        self.meta_table.setColumnCount(2)
        self.meta_table.setRowCount(7)
        self.meta_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.meta_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.meta_table.verticalHeader().setVisible(False)
        self.meta_table.setMinimumHeight(220)
        self.meta_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        meta_layout.addWidget(self.meta_table)
        scroll_layout.addWidget(meta_group)

        # 3. DAQ Integration Group
        daq_group = QtWidgets.QGroupBox("DAQ Integration")
        daq_layout = QtWidgets.QVBoxLayout(daq_group)
        
        self.load_daq_btn = QtWidgets.QPushButton("📂 Load DAQ BIN Segment")
        self.load_daq_btn.clicked.connect(self.open_daq_bin_dialog)
        daq_layout.addWidget(self.load_daq_btn)
        
        self.load_daq_meta_btn = QtWidgets.QPushButton("📂 Load DAQ Meta Segment")
        self.load_daq_meta_btn.clicked.connect(self.open_daq_meta_dialog)
        daq_layout.addWidget(self.load_daq_meta_btn)
        
        self.daq_status_label = QtWidgets.QLabel("No DAQ Data Loaded")
        self.daq_status_label.setStyleSheet("color: #909399; font-weight: bold; font-size: 11px; margin-top: 5px;")
        daq_layout.addWidget(self.daq_status_label)
        
        self.daq_metrics_label = QtWidgets.QLabel("")
        self.daq_metrics_label.setStyleSheet("color: #2c3e50; font-family: 'Consolas', monospace; font-size: 10px;")
        daq_layout.addWidget(self.daq_metrics_label)
        
        scroll_layout.addWidget(daq_group)

        # 4. Filter Settings Group
        filter_group = QtWidgets.QGroupBox("Filter Settings")
        filter_grid = QtWidgets.QGridLayout(filter_group)
        filter_grid.setSpacing(8)
        
        self.filter_checkbox = QtWidgets.QCheckBox("Enable Bandpass Filter")
        self.filter_checkbox.setChecked(True)
        self.filter_checkbox.stateChanged.connect(self.apply_filter)
        filter_grid.addWidget(self.filter_checkbox, 0, 0, 1, 2)

        filter_grid.addWidget(QtWidgets.QLabel("High-pass (Hz):"), 1, 0)
        self.hp_spin = QtWidgets.QDoubleSpinBox()
        self.hp_spin.setRange(0.0, 100.0)
        self.hp_spin.setDecimals(1)
        self.hp_spin.setValue(0.5)
        self.hp_spin.setSingleStep(0.1)
        self.hp_spin.valueChanged.connect(self.apply_filter)
        filter_grid.addWidget(self.hp_spin, 1, 1)

        filter_grid.addWidget(QtWidgets.QLabel("Low-pass (Hz):"), 2, 0)
        self.lp_spin = QtWidgets.QDoubleSpinBox()
        self.lp_spin.setRange(1.0, 500.0)
        self.lp_spin.setDecimals(1)
        self.lp_spin.setValue(40.0)
        self.lp_spin.setSingleStep(1.0)
        self.lp_spin.valueChanged.connect(self.apply_filter)
        filter_grid.addWidget(self.lp_spin, 2, 1)
        
        scroll_layout.addWidget(filter_group)

        # 5. Visualization Controls Group
        vis_group = QtWidgets.QGroupBox("Visualization Controls")
        vis_grid = QtWidgets.QGridLayout(vis_group)
        vis_grid.setSpacing(8)

        vis_grid.addWidget(QtWidgets.QLabel("Time Window (s):"), 0, 0)
        self.window_duration_spin = QtWidgets.QDoubleSpinBox()
        self.window_duration_spin.setRange(1.0, 120.0)
        self.window_duration_spin.setValue(10.0)
        self.window_duration_spin.setSingleStep(1.0)
        self.window_duration_spin.valueChanged.connect(self.on_window_duration_changed)
        vis_grid.addWidget(self.window_duration_spin, 0, 1)

        vis_grid.addWidget(QtWidgets.QLabel("Amplitude Zoom (%):"), 1, 0)
        self.zoom_spin = QtWidgets.QSpinBox()
        self.zoom_spin.setRange(10, 5000)
        self.zoom_spin.setValue(100)
        self.zoom_spin.setSingleStep(10)
        self.zoom_spin.setSuffix(" %")
        self.zoom_spin.valueChanged.connect(self.update_plot)
        vis_grid.addWidget(self.zoom_spin, 1, 1)

        vis_grid.addWidget(QtWidgets.QLabel("Channel Spacing (μV):"), 2, 0)
        self.spacing_spin = QtWidgets.QDoubleSpinBox()
        self.spacing_spin.setRange(1.0, 100000.0)
        self.spacing_spin.setValue(500.0)
        self.spacing_spin.setSingleStep(50.0)
        self.spacing_spin.valueChanged.connect(self.on_spacing_changed)
        vis_grid.addWidget(self.spacing_spin, 2, 1)

        scroll_layout.addWidget(vis_group)

        # 6. Event Markers Group
        event_group = QtWidgets.QGroupBox("EEG Event Markers")
        event_layout = QtWidgets.QVBoxLayout(event_group)
        self.event_table = QtWidgets.QTableWidget()
        self.event_table.setColumnCount(2)
        self.event_table.setHorizontalHeaderLabels(["Time (s)", "Event ID"])
        self.event_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.event_table.verticalHeader().setVisible(False)
        self.event_table.setMinimumHeight(200)
        self.event_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.event_table.cellDoubleClicked.connect(self.on_event_clicked)
        event_layout.addWidget(self.event_table)
        scroll_layout.addWidget(event_group)

        scroll_area.setWidget(scroll_widget)
        sidebar_layout.addWidget(scroll_area)
        
        # Add sidebar to splitter (set default width to 360px)
        main_splitter.addWidget(sidebar)
        main_splitter.setSizes([360, 940])

        # ------------------ RIGHT PANEL (WAVEFORM PLOT) ------------------
        plot_container = QtWidgets.QWidget()
        plot_layout = QtWidgets.QVBoxLayout(plot_container)
        plot_layout.setContentsMargins(5, 10, 10, 10)

        # pyqtgraph PlotWidget
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')  # Clean white background
        self.plot_widget.showGrid(x=True, y=True, alpha=0.15)
        self.plot_widget.setLabel('left', 'Channels')
        self.plot_widget.setLabel('bottom', 'Time', units='s')
        self.plot_widget.getViewBox().setMouseEnabled(x=False, y=False) # Disable default dragging
        plot_layout.addWidget(self.plot_widget)

        # Horizontal ScrollBar for temporal scrolling
        self.scrollbar = QtWidgets.QScrollBar(QtCore.Qt.Horizontal)
        self.scrollbar.valueChanged.connect(self.update_plot)
        plot_layout.addWidget(self.scrollbar)

        main_splitter.addWidget(plot_container)

    def load_default_file(self):
        """Checks typical locations for wuxujie.bdf and auto-loads on startup."""
        paths = [
            r'F:\20260615171156_11_wuxujie\20260615171156_11_wuxujie\wuxujie\wuxujie.bdf',
            r'F:\20260615171156_11_wuxujie\20260615171156_11_wuxujie\wuxujie\data.bdf',
            'wuxujie.bdf',
            'data.bdf'
        ]
        for path in paths:
            if os.path.exists(path):
                self.load_file(path)
                return

    def open_file_dialog(self):
        options = QtWidgets.QFileDialog.Options()
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select BDF File", "", 
            "BDF Files (*.bdf);;All Files (*)", options=options
        )
        if filepath:
            self.load_file(filepath)

    def load_file(self, filepath):
        if not filepath:
            return

        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            mne.set_log_level('WARNING')
            
            # Load raw BDF structure
            raw = mne.io.read_raw_bdf(filepath, preload=True)
            
            # Auto-Scale Validation
            std_val = np.std(raw._data[0])
            if std_val > 1.0:
                raw._data *= 1e-6

            self.raw = raw
            self.filepath = filepath
            self.sfreq = raw.info['sfreq']
            self.n_channels = raw.info['nchan']
            self.ch_names = raw.info['ch_names']
            self.times = raw.times
            
            self.raw_data_scaled = raw._data.copy()
            self.data = self.raw_data_scaled.copy()
            self.raw_data_loaded = True
            
            # Reset DAQ segment variables
            self.daq_loaded = False
            self.daq_bin_path = ""
            self.daq_segments = []
            self.daq_status_label.setText("No DAQ Data Loaded")
            self.daq_metrics_label.setText("")
            
            # Extract Event markers
            self.events = self.find_bdf_events(raw)
            
            # Fill Left Panel Info tables
            self.populate_metadata()
            self.populate_events()
            
            # Setup curves in PlotWidget
            self.setup_plot_curves()
            
            # Update temporal Scrollbar bounds
            duration = self.times[-1]
            win_dur = self.window_duration_spin.value()
            max_scroll = max(0, int((duration - win_dur) * self.sfreq))
            self.scrollbar.setMaximum(max_scroll)
            self.scrollbar.setValue(0)
            self.scrollbar.setPageStep(int(win_dur * self.sfreq))
            
            # Apply default filters
            if self.filter_checkbox.isChecked():
                self.apply_filter()
            else:
                self.update_plot()

            self.statusBar().showMessage(f"Successfully loaded: {os.path.basename(filepath)}", 5000)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to load BDF file:\n{str(e)}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def find_bdf_events(self, raw):
        """Extract event markers using annotations, find_events, or trigger channels."""
        events_list = []
        
        # 1. Check annotations
        if len(raw.annotations) > 0:
            for ann in raw.annotations:
                events_list.append({
                    'time': ann['onset'],
                    'sample': int(ann['onset'] * self.sfreq),
                    'id': ann['description']
                })
            return events_list
            
        # 2. Check general events using MNE standard search
        try:
            events = mne.find_events(raw, verbose=False)
            for ev in events:
                events_list.append({
                    'time': ev[0] / self.sfreq,
                    'sample': ev[0],
                    'id': str(ev[2])
                })
            return events_list
        except Exception:
            pass

        # 3. Check for specific status/trigger channel
        status_ch = [ch for ch in raw.ch_names if 'status' in ch.lower() or 'trigger' in ch.lower() or 'stim' in ch.lower()]
        if status_ch:
            try:
                events = mne.find_events(raw, stim_channel=status_ch[0], verbose=False)
                for ev in events:
                    events_list.append({
                        'time': ev[0] / self.sfreq,
                        'sample': ev[0],
                        'id': str(ev[2])
                    })
                return events_list
            except Exception:
                pass

        return events_list

    def open_daq_bin_dialog(self):
        if not self.raw_data_loaded:
            QtWidgets.QMessageBox.warning(self, "Warning", "Please load a BDF file first.")
            return
        options = QtWidgets.QFileDialog.Options()
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select DAQ BIN Segment File", "", 
            "Binary Files (*.bin);;All Files (*)", options=options
        )
        if filepath:
            self.daq_bin_path = filepath
            # Auto-search for same name metadata JSON file
            base = os.path.splitext(filepath)[0]
            meta_path = base + "_meta.json"
            if os.path.exists(meta_path):
                self.load_daq_data(filepath, meta_path)
            else:
                meta_path_alt = filepath.replace(".bin", "_meta.json")
                if os.path.exists(meta_path_alt):
                    self.load_daq_data(filepath, meta_path_alt)
                else:
                    QtWidgets.QMessageBox.information(
                        self, "Select Metadata File",
                        "Matching metadata file was not found automatically.\n"
                        "Please select the DAQ Metadata file (*_meta.json) manually."
                    )
                    self.open_daq_meta_dialog()

    def open_daq_meta_dialog(self):
        if not self.daq_bin_path:
            QtWidgets.QMessageBox.warning(self, "Warning", "Please load the DAQ BIN file first.")
            return
        options = QtWidgets.QFileDialog.Options()
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select DAQ Metadata File", "", 
            "JSON Files (*_meta.json *.json);;All Files (*)", options=options
        )
        if filepath:
            self.load_daq_data(self.daq_bin_path, filepath)

    def load_daq_data(self, bin_path, meta_path):
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)

            daq_sfreq = meta['rate']
            chunk_size = meta['chunk_size']
            num_chans = len(meta['channels'])

            # Read binary float64 data
            raw_data = np.fromfile(bin_path, dtype=np.float64)
            num_samples = len(raw_data)
            num_chunks = num_samples // (num_chans * chunk_size)

            if num_chunks == 0:
                raise ValueError("DAQ binary file is empty or too small.")

            # Reshape chunks to contiguous channels
            raw_data = raw_data[:num_chunks * num_chans * chunk_size]
            raw_data = raw_data.reshape(num_chunks, num_chans, chunk_size)
            
            daq_data = raw_data.transpose(1, 0, 2).reshape(num_chans, -1)
            
            # Retrieve aliases if available from the new config format, otherwise fallback to physical channel names
            daq_ch_names = []
            if 'all_channels_info' in meta:
                daq_ch_names = [ch.get('alias', ch.get('name', 'Unknown')) for ch in meta['all_channels_info']]
            else:
                daq_ch_names = [ch.split('/')[-1] for ch in meta['channels']]
            
            daq_times = np.arange(daq_data.shape[1]) / daq_sfreq
            daq_events = meta.get('events', [])
            task_name = meta.get('task_name', 'Unknown')

            # Compute alignment relative to the BDF events
            daq_offset, mean_delay, jitter, max_d, min_d, num_matches = self.compute_alignment(
                daq_sfreq, daq_events, task_name
            )

            # Store the segment data
            seg = {
                'bin_path': bin_path,
                'meta_path': meta_path,
                'data': daq_data,
                'sfreq': daq_sfreq,
                'ch_names': daq_ch_names,
                'times': daq_times,
                'events': daq_events,
                'offset': daq_offset,
                'task_name': task_name,
                'mean_delay': mean_delay,
                'jitter': jitter,
                'max_d': max_d,
                'min_d': min_d,
                'num_matches': num_matches,
                'curves': []
            }

            # Filter out duplicate segments with the exact same path
            self.daq_segments = [s for s in self.daq_segments if s['bin_path'] != bin_path]
            self.daq_segments.append(seg)
            self.daq_loaded = True

            # Update sidebar text UI
            self.update_daq_sidebar_ui()

            # Rebuild plot curves to accommodate the new segment
            self.setup_plot_curves()
            self.update_plot()

            # Show report
            status_text = "🟢 同步时延极其稳定，抖动极小！" if jitter < 5.0 else "⚠️ 检测到较大同步抖动，请检查进程优先级和网络状况！"
            QtWidgets.QMessageBox.information(
                self, "Sync Alignment Report",
                f"🎉 BDF & DAQ Segment Alignment Successful!\n\n"
                f"Segment Action         : {task_name}\n"
                f"Alignment Start Offset : {daq_offset:.3f} s\n"
                f"Average Delay (EEG-DAQ): {mean_delay:.2f} ms\n"
                f"Jitter (Std Dev)       : {jitter:.2f} ms\n"
                f"Max / Min Delay        : {max_d:.2f} ms / {min_d:.2f} ms\n"
                f"Matched Markers count  : {num_matches} / {len(daq_events)}\n\n"
                f"{status_text}"
            )

            self.statusBar().showMessage(f"Loaded DAQ Segment: {task_name}", 5000)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "DAQ Load Error", f"Failed to load DAQ data:\n{str(e)}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def compute_alignment(self, daq_sfreq, daq_events, task_name):
        """Finds segment offset, matched triggers, delays, and jitter standard deviation."""
        # 1. Load trigger mappings from Simple_EOG_Paradigm
        mapping = {}
        mapping_paths = [
            os.path.join(os.path.dirname(self.filepath), 'trigger_mappings.json'),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trigger_mappings.json'),
            r'd:\OneDrive\Data\DoCs\Tools\Simple_EOG_Paradigm\trigger_mappings.json',
            'trigger_mappings.json'
        ]
        for path in mapping_paths:
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        mappings_data = json.load(f)
                        mapping = mappings_data.get('眼动网格', {})
                        if mapping:
                            break
                except Exception:
                    pass

        # 2. Find task start trigger based on expected start codes
        expected_start_code = None
        if "X负半轴" in task_name:
            expected_start_code = 160
        elif "X正半轴" in task_name:
            expected_start_code = 170
        elif "Y正半轴" in task_name:
            expected_start_code = 180
        elif "Y负半轴" in task_name:
            expected_start_code = 190
        elif "眨眼" in task_name:
            expected_start_code = 200
        else:
            expected_start_code = 150 # default grid start code

        bdf_start_time = None
        for ev in self.events:
            try:
                ev_id = int(ev['id'])
                if ev_id == expected_start_code:
                    bdf_start_time = ev['time']
                    break
            except ValueError:
                pass

        # Fallback to any generic task start triggers
        start_triggers = {150, 160, 170, 180, 190, 200}
        if bdf_start_time is None:
            for ev in self.events:
                try:
                    ev_id = int(ev['id'])
                    if ev_id in start_triggers:
                        bdf_start_time = ev['time']
                        break
                except ValueError:
                    pass

        # 3. Match subsequent triggers
        matched_pairs = []
        for daq_ev in daq_events:
            event_name = daq_ev['event']
            expected_trigger = mapping.get(event_name)
            if expected_trigger is not None:
                daq_time = daq_ev['daq_sample_index'] / daq_sfreq
                for bdf_ev in self.events:
                    try:
                        bdf_id = int(bdf_ev['id'])
                        if bdf_id == expected_trigger:
                            if bdf_start_time is not None and bdf_ev['time'] < bdf_start_time:
                                continue
                            matched_pairs.append((daq_time, bdf_ev['time'], event_name))
                            break
                    except ValueError:
                        pass

        # 4. Set final alignment offset
        if bdf_start_time is not None:
            daq_offset = bdf_start_time
        elif matched_pairs:
            daq_offset = matched_pairs[0][1] - matched_pairs[0][0]
        else:
            daq_offset = 0.0

        # 5. Compute delay differences
        if matched_pairs:
            delays = []
            for daq_time, bdf_time, name in matched_pairs:
                actual_delay = (bdf_time - (daq_time + daq_offset)) * 1000.0
                delays.append(actual_delay)
                
            mean_delay = np.mean(delays)
            jitter = np.std(delays)
            max_d = np.max(delays)
            min_d = np.min(delays)
            num_matches = len(matched_pairs)
        else:
            mean_delay, jitter, max_d, min_d, num_matches = 0.0, 0.0, 0.0, 0.0, 0

        return daq_offset, mean_delay, jitter, max_d, min_d, num_matches

    def update_daq_sidebar_ui(self):
        if not self.daq_segments:
            self.daq_status_label.setText("No DAQ Data Loaded")
            self.daq_status_label.setStyleSheet("color: #909399; font-weight: bold; font-size: 11px;")
            self.daq_metrics_label.setText("")
            return
            
        self.daq_status_label.setText(f"Loaded {len(self.daq_segments)} Segment(s)")
        self.daq_status_label.setStyleSheet("color: #27ae60; font-weight: bold; font-size: 11px;")
        
        metrics = []
        for s in self.daq_segments:
            name_short = s['task_name'].split('_')[-1]
            metrics.append(
                f"▶ {name_short}:\n"
                f"  Offset: {s['offset']:.2f} s\n"
                f"  Jitter: {s['jitter']:.2f} ms (N={s['num_matches']})"
            )
        self.daq_metrics_label.setText("\n".join(metrics))

    def populate_metadata(self):
        """Populates the metadata table with loaded BDF values."""
        self.meta_table.clearContents()
        
        filename = os.path.basename(self.filepath)
        sfreq_str = f"{self.sfreq:.1f} Hz"
        
        duration = self.times[-1]
        duration_str = f"{duration:.1f} s ({duration/60:.2f} min)"
        
        subject = "Unknown"
        if 'subject_info' in self.raw.info and self.raw.info['subject_info'] is not None:
            info = self.raw.info['subject_info']
            first = info.get('first_name', '')
            last = info.get('last_name', '')
            subject = f"{first} {last}".strip() or info.get('his_id', 'Unknown')
            
        meas_date = self.raw.info.get('meas_date')
        date_str = meas_date.strftime('%Y-%m-%d %H:%M:%S') if meas_date else "Unknown"
        
        bads_str = ", ".join(self.raw.info.get('bads', [])) or "None"
        
        metadata = [
            ("File Name", filename),
            ("Sampling Rate", sfreq_str),
            ("Duration", duration_str),
            ("Subject Name", subject),
            ("Record Date", date_str),
            ("Channels Count", str(self.n_channels)),
            ("Bad Channels", bads_str)
        ]
        
        for row, (prop, val) in enumerate(metadata):
            prop_item = QtWidgets.QTableWidgetItem(prop)
            val_item = QtWidgets.QTableWidgetItem(val)
            prop_item.setFont(QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold))
            
            self.meta_table.setItem(row, 0, prop_item)
            self.meta_table.setItem(row, 1, val_item)

    def populate_events(self):
        """Fills the sidebar event table."""
        self.event_table.setRowCount(0)
        if not self.events:
            self.event_table.setRowCount(1)
            item = QtWidgets.QTableWidgetItem("No event markers found")
            item.setTextAlignment(QtCore.Qt.AlignCenter)
            item.setFlags(QtCore.Qt.NoItemFlags)
            self.event_table.setItem(0, 0, item)
            self.event_table.setSpan(0, 0, 1, 2)
            return

        self.event_table.clearSpans()
        self.event_table.setRowCount(len(self.events))
        for idx, ev in enumerate(self.events):
            time_item = QtWidgets.QTableWidgetItem(f"{ev['time']:.3f}")
            id_item = QtWidgets.QTableWidgetItem(str(ev['id']))
            
            time_item.setTextAlignment(QtCore.Qt.AlignCenter)
            id_item.setTextAlignment(QtCore.Qt.AlignCenter)
            
            # Make columns un-editable
            time_item.setFlags(time_item.flags() & ~QtCore.Qt.ItemIsEditable)
            id_item.setFlags(id_item.flags() & ~QtCore.Qt.ItemIsEditable)
            
            self.event_table.setItem(idx, 0, time_item)
            self.event_table.setItem(idx, 1, id_item)

    def setup_plot_curves(self):
        """Resets the graphics layout, plotting curves."""
        self.plot_widget.clear()
        self.curves = []
        self.event_lines = []
        
        # 1. Add standard BDF EEG curves (Dark Charcoal Grey)
        for _ in range(self.n_channels):
            pen = pg.mkPen(color=(50, 60, 70), width=1.2)
            curve = self.plot_widget.plot(pen=pen)
            self.curves.append(curve)
            
        # 2. Add curves for each loaded DAQ segment (Emerald Green)
        for seg in self.daq_segments:
            seg['curves'] = []
            for _ in range(seg['data'].shape[0]):
                pen = pg.mkPen(color=(39, 174, 96), width=1.5)
                curve = self.plot_widget.plot(pen=pen)
                seg['curves'].append(curve)
                
        self.update_y_axis_ticks()

    def update_y_axis_ticks(self):
        """Labels the Y-axis ticks with EEG/DAQ channel names at their offset location."""
        if not self.raw_data_loaded:
            return
        spacing = self.spacing_spin.value() * 1e-6
        ay = self.plot_widget.getAxis('left')
        
        ticks = [(-i * spacing, name) for i, name in enumerate(self.ch_names)]
        
        # Add DAQ ticks below BDF ticks using channel names from first segment
        if self.daq_segments:
            daq_ch_names = self.daq_segments[0]['ch_names']
            for i, name in enumerate(daq_ch_names):
                pos = -(self.n_channels + i) * spacing
                ticks.append((pos, f"[DAQ] {name}"))
                
        ay.setTicks([ticks])

    def on_spacing_changed(self):
        self.update_y_axis_ticks()
        self.update_plot()

    def on_window_duration_changed(self):
        if not self.raw_data_loaded:
            return
        duration = self.times[-1]
        win_dur = self.window_duration_spin.value()
        max_scroll = max(0, int((duration - win_dur) * self.sfreq))
        self.scrollbar.setMaximum(max_scroll)
        self.scrollbar.setPageStep(int(win_dur * self.sfreq))
        self.update_plot()

    def apply_filter(self):
        """Applies Bandpass filter to the BDF data buffer."""
        if not self.raw_data_loaded:
            return
            
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            if self.filter_checkbox.isChecked():
                hp = self.hp_spin.value()
                lp = self.lp_spin.value()
                if hp >= lp:
                    QtWidgets.QMessageBox.warning(self, "Filter Values Error", "High-pass limit must be less than Low-pass limit.")
                    self.filter_checkbox.setChecked(False)
                    self.data = self.raw_data_scaled.copy()
                else:
                    l_freq = hp if hp > 0 else None
                    h_freq = lp if lp < (self.sfreq / 2) else None
                    self.data = mne.filter.filter_data(
                        self.raw_data_scaled, self.sfreq, l_freq=l_freq, h_freq=h_freq,
                        fir_design='firwin', verbose=False
                    )
            else:
                self.data = self.raw_data_scaled.copy()
            
            self.update_plot()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Filter Application Failed", f"{str(e)}")
            self.data = self.raw_data_scaled.copy()
            self.update_plot()
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

    def update_plot(self):
        """Main rendering engine for sliced waveforms."""
        if not self.raw_data_loaded:
            return
            
        # Slicing indexes based on scrollbar position
        start_sample = self.scrollbar.value()
        win_dur = self.window_duration_spin.value()
        win_samples = int(win_dur * self.sfreq)
        end_sample = min(len(self.times), start_sample + win_samples)
        
        times_slice = self.times[start_sample:end_sample]
        data_slice = self.data[:, start_sample:end_sample]
        
        zoom = self.zoom_spin.value() / 100.0
        spacing = self.spacing_spin.value() * 1e-6
        
        # 1. Update BDF EEG curves
        for i in range(self.n_channels):
            offset_y = -i * spacing
            self.curves[i].setData(times_slice, data_slice[i] * zoom + offset_y)
            
        # 2. Update DAQ curves for each loaded segment
        start_time = start_sample / self.sfreq
        end_time = end_sample / self.sfreq
        
        for seg in self.daq_segments:
            daq_sfreq = seg['sfreq']
            offset = seg['offset']
            daq_data = seg['data']
            
            # Calculate corresponding samples in DAQ data
            daq_start = max(0, int((start_time - offset) * daq_sfreq))
            daq_end = min(daq_data.shape[1], int((end_time - offset) * daq_sfreq))
            
            if daq_start < daq_end:
                daq_slice = daq_data[:, daq_start:daq_end]
                daq_times = np.arange(daq_start, daq_end) / daq_sfreq + offset
                
                for i in range(daq_slice.shape[0]):
                    offset_y = -(self.n_channels + i) * spacing
                    seg['curves'][i].setData(daq_times, daq_slice[i] * zoom + offset_y)
            else:
                for i in range(len(seg['curves'])):
                    seg['curves'][i].setData([], [])
            
        # Adjust Y-axis range to accommodate the max number of channels
        max_daq_chans = max([s['data'].shape[0] for s in self.daq_segments]) if self.daq_segments else 0
        total_ch = self.n_channels + max_daq_chans
        
        self.plot_widget.setXRange(start_time, end_time, padding=0)
        self.plot_widget.setYRange(-total_ch * spacing, spacing, padding=0.1)
        
        # Remove old event vertical lines
        for line in self.event_lines:
            self.plot_widget.removeItem(line)
        self.event_lines.clear()
        
        # Plot markers on the visual region
        for ev in self.events:
            ev_time = ev['time']
            if start_time <= ev_time <= end_time:
                pen = pg.mkPen('r', style=QtCore.Qt.DashLine, width=1.2)
                line = pg.InfiniteLine(
                    pos=ev_time, angle=90, movable=False, pen=pen, 
                    label=f"Evt {ev['id']}", 
                    labelOpts={'color': (180, 0, 0), 'position': 0.95}
                )
                self.plot_widget.addItem(line)
                self.event_lines.append(line)

    def on_event_clicked(self, row, column):
        """Centers visualization around double-clicked event marker in the sidebar."""
        if not self.events:
            return
        time_item = self.event_table.item(row, 0)
        if time_item:
            try:
                time_val = float(time_item.text())
                win_dur = self.window_duration_spin.value()
                start_time = max(0.0, time_val - win_dur / 2.0)
                start_sample = int(start_time * self.sfreq)
                
                # Constrain to valid scroll limits
                max_scroll = self.scrollbar.maximum()
                self.scrollbar.setValue(min(start_sample, max_scroll))
            except ValueError:
                pass

def main():
    app = QtWidgets.QApplication(sys.argv)
    viewer = HardwareSyncViewer()
    viewer.show()
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()
