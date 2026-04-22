# Copyright 2023 Windell H. Oskay, Evil Mad Scientist Laboratories
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""
axidraw.py

Part of the AxiDraw driver for Inkscape
https://github.com/evil-mad/AxiDraw

See version_string below for current version and date.

Requires Python 3.8 or newer
"""
# pylint: disable=pointless-string-statement

__version__ = '3.9.7'  # Dated 2024-01-16

import copy
import gettext
from importlib import import_module
import logging
import math
import time
import socket  # for exception handling only
try:
    import tkinter
    from tkinter import messagebox
except Exception:  # pragma: no cover
    tkinter = None
    messagebox = None

from lxml import etree

from axidrawinternal.axidraw_options import common_options, versions

from axidrawinternal import path_objects
from axidrawinternal import digest_svg
from axidrawinternal import boundsclip
from axidrawinternal import plot_optimizations
from axidrawinternal import plot_status
from axidrawinternal import pen_handling
from axidrawinternal import plot_warnings
from axidrawinternal import serial_utils
from axidrawinternal import motion
from axidrawinternal import dripfeed
from axidrawinternal import preview
from axidrawinternal import i18n

from axidrawinternal.plot_utils_import import from_dependency_import # plotink
simplepath = from_dependency_import('ink_extensions.simplepath')
simplestyle = from_dependency_import('ink_extensions.simplestyle')
cubicsuperpath = from_dependency_import('ink_extensions.cubicsuperpath')
simpletransform = from_dependency_import('ink_extensions.simpletransform')
inkex = from_dependency_import('ink_extensions.inkex')
exit_status = from_dependency_import('ink_extensions_utils.exit_status')
message = from_dependency_import('ink_extensions_utils.message')
ebb_serial = from_dependency_import('plotink.ebb_serial')  # https://github.com/evil-mad/plotink
ebb_motion = from_dependency_import('plotink.ebb_motion')
plot_utils = from_dependency_import('plotink.plot_utils')
text_utils = from_dependency_import('plotink.text_utils')
requests = from_dependency_import('requests')
urllib3 = from_dependency_import('urllib3') # for exception handling only

logger = logging.getLogger(__name__)

class AxiDraw(inkex.Effect):
    """ Main class for AxiDraw """

    logging_attrs = {"default_handler": message.UserMessageHandler()}

    def __init__(self, default_logging=True, user_message_fun=message.emit, params=None):
        if params is None:
            params = import_module("axidrawinternal.axidraw_conf") # Default configuration file
        self.params = params
        i18n.init_gettext(params=params)

        # axidraw.py is never actually called as a commandline tool, so why add options to
        # self.arg_parser here? Because it helps populate the self.options object
        # (argparse.Namespace) with necessary attributes and set the right defaults.
        # See self.initialize_options
        core_axidraw_options = common_options.core_axidraw_options(params.__dict__)
        inkex.Effect.__init__(self, common_options = [core_axidraw_options])

        self.initialize_options()

        self.version_string = __version__

        self.plot_status = plot_status.PlotStatus()
        self.pen = pen_handling.PenHandler()
        self.warnings = plot_warnings.PlotWarnings()
        self.preview = preview.Preview()

        self.spew_debugdata = False # Possibly add this as a PlotStatus variable
        self.set_defaults()
        self.digest = None
        self.vb_stash = [1, 1, 0, 0] # Viewbox storage
        self.bounds = [[0, 0], [0, 0]]
        self.connected = False # Python API variable.

        self.plot_status.secondary = False
        self.user_message_fun = user_message_fun

        if default_logging: # logging setup
            logger.setLevel(logging.INFO)
            logger.addHandler(self.logging_attrs["default_handler"])

        if self.spew_debugdata:
            logger.setLevel(logging.DEBUG) # by default level is INFO

    def set_up_pause_receiver(self, software_pause_event):
        """ use a multiprocessing.Event/threading.Event to communicate a
        keyboard interrupt (ctrl-C) to pause the AxiDraw """
        self._software_pause_event = software_pause_event

    def receive_pause_request(self):
        """pause receiver"""
        return hasattr(self, "_software_pause_event") and self._software_pause_event.is_set()

    def set_secondary(self, suppress_standard_out=True):
        """ If a "secondary" AxiDraw called by axidraw_control """
        self.plot_status.secondary = True
        self.called_externally = True
        if suppress_standard_out:
            self.suppress_standard_output_stream()

    def suppress_standard_output_stream(self):
        """ Save values we will need later in unsuppress_standard_output_stream """
        self.logging_attrs["additional_handlers"] = [SecondaryErrorHandler(self),\
            SecondaryNonErrorHandler(self)]
        self.logging_attrs["emit_fun"] = self.user_message_fun
        logger.removeHandler(self.logging_attrs["default_handler"])
        for handler in self.logging_attrs["additional_handlers"]:
            logger.addHandler(handler)

    def unsuppress_standard_output_stream(self):
        """ Release logging stream """
        logger.addHandler(self.logging_attrs["default_handler"])
        if self.logging_attrs["additional_handlers"]:
            for handler in self.logging_attrs["additional_handlers"]:
                logger.removeHandler(handler)

        self.user_message_fun = self.logging_attrs["emit_fun"]

    def set_defaults(self):
        """ Set default values of certain parameters
            These are set when the class is initialized.
            Also called in plot_run() in the Python API, to ensure that
            these defaults are set before plotting additional pages."""

        self.use_layer_speed = False
        self.plot_status.reset() # Clear serial port and pause status flags
        self.pen.reset() # Clear pen state, lift count, layer pen height flag
        self.warnings.reset() # Clear any warning messages
        self.time_elapsed = 0 # Available for use by python API

        self.svg_transform = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
        self.digest = None

    def initialize_options(self):
        """ Use the flags and arguments defined in __init__ to populate self.options with
            the necessary attributes and set defaults """
        self.getoptions([])
        # self.getoptions initializes self.options by calling self.arg_parser.parse_args, which is
        # not the intended use of parse_args

    def update_options(self):
        """ Parse and update certain options; called in effect and in interactive modes
            whenever the options are updated """

        x_bounds_min = 0.0
        y_bounds_min = 0.0

        # Physical travel bounds, based on AxiDraw model:
        if self.options.model == 2:
            x_bounds_max = self.params.x_travel_V3A3
            y_bounds_max = self.params.y_travel_V3A3
        elif self.options.model == 3:
            x_bounds_max = self.params.x_travel_V3XLX
            y_bounds_max = self.params.y_travel_V3XLX
        elif self.options.model == 4:
            x_bounds_max = self.params.x_travel_MiniKit
            y_bounds_max = self.params.y_travel_MiniKit
        elif self.options.model == 5:
            x_bounds_max = self.params.x_travel_SEA1
            y_bounds_max = self.params.y_travel_SEA1
        elif self.options.model == 6:
            x_bounds_max = self.params.x_travel_SEA2
            y_bounds_max = self.params.y_travel_SEA2
        elif self.options.model == 7:
            x_bounds_max = self.params.x_travel_V3B6
            y_bounds_max = self.params.y_travel_V3B6
        else:
            x_bounds_max = self.params.x_travel_default
            y_bounds_max = self.params.y_travel_default

        self.bounds = [[x_bounds_min - 1e-9, y_bounds_min - 1e-9],
                       [x_bounds_max + 1e-9, y_bounds_max + 1e-9]]

        # Speeds in inches/second:
        self.speed_pendown = self.params.speed_pendown * self.params.speed_lim_xy_hr / 110.0
        self.speed_penup = self.params.speed_penup * self.params.speed_lim_xy_hr / 110.0

        # Input limit checking; constrain input values and prevent zero speeds:
        self.options.pen_pos_up = plot_utils.constrainLimits(self.options.pen_pos_up, 0, 100)
        self.options.pen_pos_down = plot_utils.constrainLimits(self.options.pen_pos_down, 0, 100)
        self.options.pen_rate_raise = \
            plot_utils.constrainLimits(self.options.pen_rate_raise, 1, 200)
        self.options.pen_rate_lower = \
            plot_utils.constrainLimits(self.options.pen_rate_lower, 1, 200)
        self.options.speed_pendown = plot_utils.constrainLimits(self.options.speed_pendown, 1, 110)
        self.options.speed_penup = plot_utils.constrainLimits(self.options.speed_penup, 1, 200)
        self.options.accel = plot_utils.constrainLimits(self.options.accel, 1, 110)

    def _apply_grbl_runtime_commands(self):
        """Allow UI/CLI Grbl command strings to override config defaults."""
        for opt_name, param_name in (
            ("grbl_pen_up_cmd", "grbl_pen_up_cmd"),
            ("grbl_pen_down_cmd", "grbl_pen_down_cmd"),
            ("grbl_disable_motors_cmd", "grbl_disable_motors_cmd"),
            ("grbl_pen_down_slow_feed", "grbl_pen_down_slow_feed"),
            ("grbl_pen_down_settle_ms", "grbl_pen_down_settle_ms"),
        ):
            opt_value = getattr(self.options, opt_name, None)
            if opt_value is None:
                continue
            if isinstance(opt_value, str):
                cmd = str(opt_value).strip()
            else:
                cmd = opt_value
            if cmd != "":
                setattr(self.params, param_name, cmd)

    def _report_grbl_command_profile(self):
        """Print active Grbl pen/motor commands once per run."""
        if getattr(self.plot_status, "secondary", False):
            return
        self.user_message_fun(
            gettext.gettext(
                "Grbl command profile: pen-up='{0}', pen-down='{1}', disable='{2}'.").format(
                    self.params.grbl_pen_up_cmd,
                    self.params.grbl_pen_down_cmd,
                    self.params.grbl_disable_motors_cmd))
        slow_feed = float(getattr(self.params, "grbl_pen_down_slow_feed", 0.0) or 0.0)
        settle_ms = int(getattr(self.params, "grbl_pen_down_settle_ms", 0) or 0)
        if slow_feed > 0 or settle_ms > 0:
            self.user_message_fun(
                gettext.gettext(
                    "低速落笔保护已启用：落笔速度 {0:.0f} mm/min，触纸缓冲 {1} ms。").format(
                        slow_feed,
                        settle_ms))

    def _auto_sparse_settings(self):
        """Return effective dense-line thinning settings from UI/options."""
        enabled = bool(getattr(self.options, "auto_sparse_linework",
                               getattr(self.params, "auto_sparse_linework", True)))
        mode = str(getattr(self.options, "auto_sparse_line_mode",
                           getattr(self.params, "auto_sparse_line_mode", "standard")) or "standard")
        mode = mode.strip().lower()
        presets = {
            "off": {"enabled": False, "threshold": 0.0035, "min_run": 999999},
            "conservative": {"enabled": enabled, "threshold": 0.0024, "min_run": 18},
            "standard": {"enabled": enabled, "threshold": 0.0035, "min_run": 12},
            "aggressive": {"enabled": enabled, "threshold": 0.0048, "min_run": 8},
        }
        return presets.get(mode, presets["standard"])

    def _describe_grbl_axis_mapping(self):
        """Return a readable summary of current logical-to-physical XY mapping."""
        map_x = serial_utils._axis_map_out(self.plot_status, 1.0, 0.0)
        map_y = serial_utils._axis_map_out(self.plot_status, 0.0, 1.0)

        def _axis_label(mapped):
            x_val, y_val = mapped
            if abs(x_val) >= abs(y_val):
                return "机器 X+" if x_val >= 0 else "机器 X-"
            return "机器 Y+" if y_val >= 0 else "机器 Y-"

        return (
            f"逻辑 X+ -> {_axis_label(map_x)}，"
            f"逻辑 Y+ -> {_axis_label(map_y)}"
        )

    def _describe_grbl_coordinate_model(self):
        """Return a short description of the active working-origin coordinate model."""
        if bool(getattr(self.plot_status, "grbl_xy_zeroed", False)):
            return "当前坐标模型：以当前点作为工作原点 0,0；XY 对调/反转只改变逻辑轴投射到机器轴的方向，不会重写 G53 机器坐标。"
        return "当前坐标模型：尚未把当前点设为工作原点；XY 对调/反转仅作用于工作坐标运动，G53 机器坐标不参与映射。"

    def _grbl_auto_zero_on_connect_enabled(self):
        """Return whether connect should automatically set current XY as work origin."""
        return bool(getattr(
            self.options,
            "grbl_zero_xy_on_connect",
            getattr(self.params, "grbl_zero_xy_on_connect", True)))

    def _grbl_return_to_origin_after_plot_enabled(self):
        """Return whether a successful plot should automatically return to work origin."""
        return bool(getattr(
            self.options,
            "grbl_return_to_origin_after_plot",
            getattr(self.params, "grbl_return_to_origin_after_plot", True)))


    def effect(self):
        """Main entry point: check to see which mode/tab is selected, and act accordingly."""
        self.start_time = time.time()

        try:
            self.plot_status.secondary
        except AttributeError:
            self.plot_status.secondary = False

        self.text_out = '' # Text log for basic communication messages
        self.error_out = '' # Text log for significant errors

        self.plot_status.stats.reset() # Reset plot duration and distance statistics

        self.doc_units = "in"

        self.pen.phys.xpos = self.params.start_pos_x
        self.pen.phys.ypos = self.params.start_pos_y

        self.layer_speed_pendown = -1
        self.plot_status.copies_to_plot = 1

        self.plot_status.resume.reset() # New values to write to file:

        self.svg_width = 0
        self.svg_height = 0
        self.rotate_page = False

        self.update_options()

        self.options.mode = self.options.mode.strip("\"") # Input sanitization
        self.options.setup_type = self.options.setup_type.strip("\"")
        self.options.manual_cmd = self.options.manual_cmd.strip("\"")
        self.options.resume_type = self.options.resume_type.strip("\"")
        self.options.page_delay = max(self.options.page_delay, 0)
        self._apply_grbl_runtime_commands()

        try:
            self.called_externally
        except AttributeError:
            self.called_externally = False

        if self.options.mode == "options":
            return
        if self.options.mode == "timing":
            return
        if self.options.mode == "version":
            # Return the version of _this python script_.
            self.user_message_fun(self.version_string)
            return
        if self.options.mode == "manual":
            if self.options.manual_cmd == "none":
                return  # No option selected. Do nothing and return no error.
            if self.options.manual_cmd == "strip_data":
                self.svg = self.document.getroot()
                for slug in ['WCB', 'MergeData', 'plotdata', 'eggbot']:
                    for node in self.svg.xpath('//svg:' + slug, namespaces=inkex.NSS):
                        self.svg.remove(node)
                self.user_message_fun(gettext.gettext(\
                    "All AxiDraw data has been removed from this SVG file."))
                return
            if self.options.manual_cmd in ("res_read", "res_adj_in", "res_adj_mm"):
                self.svg = self.document.getroot()
                self.user_message_fun(self.plot_status.resume.manage_offset(self))
                self.res_dist = max(self.plot_status.resume.new.pause_dist*25.4, 0) # Python API
                return
            if self.options.manual_cmd == "list_names":
                self.user_message_fun(gettext.gettext(
                    "当前绘图机版插件不支持设备昵称列表。"))
                return

        if self.options.mode == "resume":
            if self.options.resume_type == "home":
                self.options.mode = "res_home"
            else:
                self.options.mode = "res_plot"
                self.options.copies = 1

        if self.options.mode == "setup":
            # setup mode -> either align, toggle, or cycle modes.
            self.options.mode = self.options.setup_type

        if self.options.digest > 1: # Generate digest only; do not run plot or preview
            self.options.preview = True # Disable serial communication; restrict certain functions

        if not self.options.preview:
            i18n.init_gettext(options=self.options, params=self.params)
            self.serial_connect()
            if serial_utils.is_grbl(self.plot_status):
                conflict_messages = serial_utils.grbl_settings_conflicts(
                    self.options, self.params, self.plot_status)
                serial_utils.apply_grbl_settings_to_params(self.plot_status, self.params)
                self.update_options()
                self._report_grbl_command_profile()
                for conflict_message in conflict_messages:
                    self.user_message_fun(conflict_message)
            self.plot_status.resume.clear_button(self) # Query button to clear its state

        if self.options.mode == "sysinfo":
            versions.report_version_info(self.plot_status, self.params.check_updates,
                                         self.version_string, self.options.preview,
                                         self.user_message_fun)

        if self.plot_status.port is None and not self.options.preview:
            return # unable to connect to axidraw

        if self.options.mode in ('align', 'toggle', 'cycle'):
            self.setup_command()
            self.warnings.report(self.called_externally, self.user_message_fun) # print warnings
            return

        if self.options.mode == "manual":
            self.manual_command() # Handle manual commands that use both power and usb.
            self.warnings.report(self.called_externally, self.user_message_fun) # print warnings
            return

        self.svg = self.document.getroot()
        self.plot_status.resume.update_needed = False
        self.plot_status.resume.new.model = self.options.model # Save model in file

        if self.options.mode in ("plot", "layers", "res_plot", "res_home"):
            # Read saved data from SVG file, including plob version information
            self.plot_status.resume.read_from_svg(self.svg)

        if self.options.mode == "res_plot":  # Initialization for resuming plots
            if self.plot_status.resume.old.pause_dist >= 0:
                self.pen.phys.xpos = self.plot_status.resume.old.last_x
                self.pen.phys.ypos = self.plot_status.resume.old.last_y
                if serial_utils.is_grbl(self.plot_status):
                    grbl_pos = serial_utils.grbl_get_position(
                        self.plot_status, timeout_s=max(0.5, float(self.options.grbl_command_timeout)))
                    if grbl_pos is not None:
                        delta_x = abs(grbl_pos[0] - self.pen.phys.xpos)
                        delta_y = abs(grbl_pos[1] - self.pen.phys.ypos)
                        if delta_x > 0.05 or delta_y > 0.05:
                            logger.error(gettext.gettext(
                                "Resume aborted: machine position differs from stored resume position."))
                            logger.error(gettext.gettext(
                                "Home the machine or realign coordinates before resuming."))
                            return
                self.plot_status.resume.new.rand_seed = self.plot_status.resume.old.rand_seed
                self.plot_status.resume.new.layer = self.plot_status.resume.old.layer
            else:
                logger.error(gettext.gettext(\
                    "No in-progress plot data found in file; unable to resume."))
                return

        if self.options.mode in ("plot", "layers", "res_plot"):
            self.plot_status.copies_to_plot = self.options.copies
            if self.plot_status.copies_to_plot == 0: # Special case: Continuous copies selected
                self.plot_status.copies_to_plot = -1 # Flag for continuous copies

            if self.options.preview and not self.options.random_start:
                # Special preview case: Without randomizing, pages have identical print time:
                self.plot_status.copies_to_plot = 1

            if self.options.mode == "plot":
                self.plot_status.resume.new.layer = -1  # Plot all layers
            if self.options.mode == "layers":
                self.plot_status.resume.new.layer = self.options.layer

            # Parse & digest SVG document, perform initial optimizations, prepare to resume:
            if not self.prepare_document():
                return

            if self.options.digest > 1: # Generate digest only; do not run plot or preview
                self.plot_cleanup()     # Revert document to save plob & print time elapsed
                self.plot_status.resume.new.plob_version = str(path_objects.PLOB_VERSION)
                self.plot_status.resume.write_to_svg(self.svg)
                self.warnings.report(False, self.user_message_fun) # print warnings
                return

            if self.options.mode == "res_plot": # Crop digest up to when the plot resumes:
                self.digest.crop(self.plot_status.resume.old.pause_dist)

            # CLI PROGRESS BAR: SET UP DRY RUN TO ESTIMATE PLOT LENGTH & TIME
            if self.plot_status.progress.review(self.plot_status, self.options):
                self.plot_document() # "Dry run": Estimate plot length & time

                self.user_message_fun(self.plot_status.progress.restore(self))
                self.plot_status.stats.reset() # Reset plot duration and distance statistics

            if self.options.mode == "res_plot":
                self.pen.phys.xpos = self.plot_status.resume.old.last_x
                self.pen.phys.ypos = self.plot_status.resume.old.last_y

                # Update so that if the plot is paused, we can resume again
                self.plot_status.stats.down_travel_inch = self.plot_status.resume.old.pause_dist

            first_copy = True
            while self.plot_status.copies_to_plot != 0:

                self.preview.reset() # Clear preview data before starting each plot
                self.plot_status.resume.update_needed = True
                self.plot_status.copies_to_plot -= 1

                if first_copy:
                    first_copy = False
                else:
                    self.plot_status.stats.next_page() # Update distance stats for next page
                    if self.options.random_start:
                        self.randomize_optimize() # Only need to re-optimize if randomizing
                self.plot_document()
                dripfeed.page_layer_delay(self, between_pages=True) # Delay between pages

            self.plot_cleanup() # Revert document, print time reports, send webhooks

        elif self.options.mode  == "res_home":
            self.plot_status.resume.copy_old()
            self.plot_status.resume.update_needed = True

            self.query_ebb_voltage()
            self.pen.servo_init(self)
            self.pen.pen_raise(self)
            self.enable_motors()
            self.go_to_parking_position(wait_for_completion=True)
            self.plot_status.resume.new.clean()
            self.plot_cleanup()
            return

        if self.plot_status.resume.update_needed:
            self.plot_status.resume.new.last_x = self.pen.phys.xpos
            self.plot_status.resume.new.last_y = self.pen.phys.ypos
            if self.options.digest: # i.e., if self.options.digest > 0
                self.plot_status.resume.new.plob_version = str(path_objects.PLOB_VERSION)
            self.plot_status.resume.write_to_svg(self.svg)
        if self.plot_status.port is not None:
            if serial_utils.is_grbl(self.plot_status):
                time.sleep(0.01)
            else:
                ebb_motion.doTimedPause(self.plot_status.port, 10, False) # Final timed motion command
            if self.options.port is None:  # Do not close serial port if it was opened externally.
                self.disconnect()
        self.warnings.report(self.called_externally, self.user_message_fun) # print warnings


    def setup_command(self):
        """ Commands from the setup modes. Need power and USB, but not SVG file. """

        if self.options.preview:
            self.user_message_fun('Command unavailable while in preview mode.')
            return

        if self.plot_status.port is None:
            return

        self.query_ebb_voltage()
        self.pen.servo_init(self)

        if self.options.mode == "align":
            self.pen.pen_raise(self)
            if serial_utils.is_grbl(self.plot_status):
                result = serial_utils.grbl_send_result(
                    self.plot_status,
                    self.params.grbl_disable_motors_cmd,
                    expect_ok=True,
                    timeout_s=max(1.0, float(self.options.grbl_command_timeout)))
                if not result["ok"]:
                    logger.error(
                        gettext.gettext("Failed to disable motors in Grbl mode ({0}).").format(
                            result["kind"]))
            else:
                ebb_motion.sendDisableMotors(self.plot_status.port, False)
        elif self.options.mode == "cycle":
            self.pen.cycle(self)
        # Note that "toggle" mode is handled within self.pen.servo_init(self)

    def manual_command(self):
        """ Manual mode commands that need USB connectivity and don't need SVG file """

        if self.options.preview: # First: Commands that require serial but not power
            self.user_message_fun('Command unavailable while in preview mode.')
            return
        if self.plot_status.port is None:
            return

        if self.options.manual_cmd == "fw_version":
            info_lines = serial_utils.grbl_get_identity(
                self.plot_status,
                timeout_s=max(1.0, float(self.options.grbl_command_timeout)))
            self.user_message_fun(f"端口: {getattr(self.plot_status, 'port_name', 'n/a')}")
            if info_lines:
                self.plot_status.fw_version = info_lines[0]
                for line in info_lines:
                    self.user_message_fun(line)
            else:
                self.user_message_fun(self.plot_status.fw_version)
            return

        self.manual_command_grbl()
        return

        if self.options.manual_cmd == "fw_version":
            if serial_utils.is_grbl(self.plot_status):
                info_lines = serial_utils.grbl_get_identity(
                    self.plot_status,
                    timeout_s=max(1.0, float(self.options.grbl_command_timeout)))
                if info_lines:
                    self.plot_status.fw_version = info_lines[0]
                    self.user_message_fun(f"端口: {getattr(self.plot_status, 'port_name', 'n/a')}")
                    for line in info_lines:
                        self.user_message_fun(line)
                else:
                    self.user_message_fun(self.plot_status.fw_version)
            else:
                self.user_message_fun(self.plot_status.fw_version)
            return

        if serial_utils.is_grbl(self.plot_status):
            self.manual_command_grbl()
            return

        if self.options.manual_cmd == "bootload":
            success = ebb_serial.bootload(self.plot_status.port)
            if success:
                self.user_message_fun(
                    gettext.gettext("Entering bootloader mode for firmware programming.\n" +
                                    "To resume normal operation, you will need to first\n" +
                                    "disconnect the AxiDraw from both USB and power."))
                self.disconnect() # Disconnect from AxiDraw; end serial session
            else:
                logger.error('Failed while trying to enter bootloader.')
            return

        if self.options.manual_cmd == "read_name":
            name_string = ebb_serial.query_nickname(self.plot_status.port)
            if name_string is None:
                logger.error(gettext.gettext("Error; unable to read nickname.\n"))
            else:
                self.user_message_fun(name_string)
            return

        if (self.options.manual_cmd).startswith("write_name"):
            temp_string = self.options.manual_cmd
            temp_string = temp_string.split("write_name", 1)[1] # Get part after "write_name"
            temp_string = temp_string[:16] # Only use first 16 characters in name
            if not temp_string:
                temp_string = "" # Use empty string to clear nickname.

            if versions.min_fw_version(self.plot_status, "2.5.5"):
                renamed = ebb_serial.write_nickname(self.plot_status.port, temp_string)
                if renamed is True:
                    if temp_string == "":
                        self.user_message_fun('Writing "blank" Nickname; setting to default.')
                    else:
                        self.user_message_fun(f'Nickname "{temp_string}" written.')
                    self.user_message_fun('Rebooting EBB.')
                else:
                    logger.error('Error encountered while writing nickname.')
                ebb_serial.reboot(self.plot_status.port)    # Reboot required after writing nickname
                self.disconnect() # Disconnect from AxiDraw; end serial session
            else:
                logger.error("This function requires a newer firmware version. See: axidraw.com/fw")
            return

        self.query_ebb_voltage() # Next: Commands that also require both power to move motors:
        if self.options.manual_cmd == "raise_pen":
            self.pen.servo_init(self) # Initializes to pen-up position
        elif self.options.manual_cmd == "lower_pen":
            self.pen.servo_init(self) # Initializes to pen-down position
        elif self.options.manual_cmd == "enable_xy":
            self.enable_motors()
        elif self.options.manual_cmd == "disable_xy":
            ebb_motion.sendDisableMotors(self.plot_status.port, False)
        else:  # walk motors or move home cases:
            self.pen.servo_init(self)
            self.enable_motors()  # Set plotting resolution
            if self.options.manual_cmd == "walk_home":
                if versions.min_fw_version(self.plot_status, "2.6.2"):
                    serial_utils.exhaust_queue(self) # Wait until all motion stops
                    a_pos, b_pos = ebb_motion.query_steps(self.plot_status.port, False)
                    n_delta_x = -(a_pos + b_pos) / (4 * self.params.native_res_factor)
                    n_delta_y = -(a_pos - b_pos) / (4 * self.params.native_res_factor)
                    if self.options.resolution == 2:  # Low-resolution mode
                        n_delta_x *= 2
                        n_delta_y *= 2
                else:
                    logger.error("This function requires newer firmware. Update at: axidraw.com/fw")
                    return
            elif self.options.manual_cmd == "walk_y":
                n_delta_x = 0
                n_delta_y = self.options.dist
            elif self.options.manual_cmd == "walk_x":
                n_delta_y = 0
                n_delta_x = self.options.dist
            elif self.options.manual_cmd == "walk_mmy":
                n_delta_x = 0
                n_delta_y = self.options.dist / 25.4
            elif self.options.manual_cmd == "walk_mmx":
                n_delta_y = 0
                n_delta_x = self.options.dist / 25.4
            else:
                return
            f_x = self.pen.phys.xpos + n_delta_x # Note: Walks are relative, not absolute!
            f_y = self.pen.phys.ypos + n_delta_y # New position is not saved; use with care.
            self.go_to_position(f_x, f_y)

    def manual_command_grbl(self):
        """Manual commands for Grbl backend."""
        cmd = self.options.manual_cmd
        timeout_s = max(1.0, float(self.options.grbl_command_timeout))
        state_blocked = {"Run", "Hold", "Jog", "Home", "Check", "Door"}

        def _state_guard():
            status_line = serial_utils.grbl_query_status(self.plot_status, timeout_s=0.3)
            state = serial_utils.grbl_status_state(status_line)
            if state in state_blocked:
                logger.error(
                    gettext.gettext(
                        "Controller busy state: {0}. Please resume/idle before this command.").format(state))
                return False
            return True

        if cmd == "ports_scan":
            ports = serial_utils.list_grbl_port_info()
            if ports:
                self.user_message_fun("Detected Grbl serial ports:")
                for port_info in ports:
                    desc = port_info["description"] or "n/a"
                    self.user_message_fun(
                        f"  {port_info['device']} | {desc}")
            else:
                self.user_message_fun("No serial ports detected.")
            return

        if cmd in ("raise_pen", "lower_pen"):
            if not _state_guard():
                return
            self.pen.servo_init(self)
            if cmd == "raise_pen":
                self.pen.pen_raise(self)
            else:
                self.pen.pen_lower(self)
            return

        if cmd == "enable_xy":
            ok, _lines = serial_utils.grbl_send(self.plot_status, "$X", expect_ok=True, timeout_s=timeout_s)
            if not ok:
                logger.error(gettext.gettext("Failed to unlock Grbl with $X."))
            return

        if cmd == "disable_xy":
            ok, _lines = serial_utils.grbl_send(
                self.plot_status,
                self.params.grbl_disable_motors_cmd,
                expect_ok=True,
                timeout_s=timeout_s)
            if not ok:
                logger.error(gettext.gettext("Failed to disable motors in Grbl mode."))
            return

        if cmd == "axis_read":
            self.plot_status.grbl_settings = serial_utils.read_grbl_settings(
                self.plot_status, timeout_s=timeout_s)
            dir_mask = self.plot_status.grbl_settings.get(3, "n/a")
            homing_mask = self.plot_status.grbl_settings.get(23, "n/a")
            positions = serial_utils.grbl_get_positions(self.plot_status, timeout_s=0.6)
            self.user_message_fun(f"Grbl $3（方向反转）: {dir_mask}")
            self.user_message_fun(f"Grbl $23（回零方向反转）: {homing_mask}")
            self.user_message_fun(
                f"状态: {positions['state'] or 'n/a'} | "
                f"逻辑机械坐标(in): {positions['mpos'] or 'n/a'} | "
                f"逻辑工作坐标(in): {positions['wpos'] or 'n/a'}")
            self.user_message_fun(
                f"物理机械坐标(in): {positions['mpos_phys'] or 'n/a'} | "
                f"物理工作坐标(in): {positions['wpos_phys'] or 'n/a'}")
            self.user_message_fun(
                "软件坐标映射: "
                f"对调XY={bool(self.options.grbl_axis_swap_xy)}, "
                f"反转X={bool(self.options.grbl_axis_invert_x)}, "
                f"反转Y={bool(self.options.grbl_axis_invert_y)}")
            self.user_message_fun(self._describe_grbl_axis_mapping())
            self.user_message_fun(self._describe_grbl_coordinate_model())
            return

        if cmd == "status_refresh":
            positions = serial_utils.grbl_get_positions(self.plot_status, timeout_s=0.6)
            self.user_message_fun(
                f"状态: {positions['state'] or 'n/a'} | "
                f"逻辑机械坐标(in): {positions['mpos'] or 'n/a'} | "
                f"逻辑工作坐标(in): {positions['wpos'] or 'n/a'}")
            self.user_message_fun(
                f"物理机械坐标(in): {positions['mpos_phys'] or 'n/a'} | "
                f"物理工作坐标(in): {positions['wpos_phys'] or 'n/a'}")
            self.user_message_fun(self._describe_grbl_axis_mapping())
            self.user_message_fun(self._describe_grbl_coordinate_model())
            return

        if cmd == "axis_apply":
            if not _state_guard():
                return
            apply_ok = True
            dir_mask = int(self.options.grbl_set_dir_mask)
            home_mask = int(self.options.grbl_set_homing_dir_mask)
            ui_dir_bits = (
                (1 if bool(self.options.grbl_dir_invert_x) else 0) |
                (2 if bool(self.options.grbl_dir_invert_y) else 0) |
                (4 if bool(self.options.grbl_dir_invert_z) else 0)
            )
            ui_home_bits = (
                (1 if bool(self.options.grbl_home_invert_x) else 0) |
                (2 if bool(self.options.grbl_home_invert_y) else 0) |
                (4 if bool(self.options.grbl_home_invert_z) else 0)
            )
            # Checkbox-derived masks take priority when any checkbox is selected.
            if ui_dir_bits > 0:
                dir_mask = ui_dir_bits
            if ui_home_bits > 0:
                home_mask = ui_home_bits

            if dir_mask >= 0:
                apply_ok &= serial_utils.grbl_write_setting(
                    self.plot_status, 3, dir_mask, timeout_s)
            if home_mask >= 0:
                apply_ok &= serial_utils.grbl_write_setting(
                    self.plot_status, 23, home_mask, timeout_s)
            self.plot_status.grbl_axis_swap_xy = bool(self.options.grbl_axis_swap_xy)
            self.plot_status.grbl_axis_invert_x = bool(self.options.grbl_axis_invert_x)
            self.plot_status.grbl_axis_invert_y = bool(self.options.grbl_axis_invert_y)
            self.plot_status.grbl_settings = serial_utils.read_grbl_settings(
                self.plot_status, timeout_s=timeout_s)
            if apply_ok:
                self.user_message_fun(gettext.gettext("坐标轴设置已应用。"))
                self.user_message_fun(f"已写入掩码: $3={dir_mask}, $23={home_mask}")
                self.user_message_fun(self._describe_grbl_axis_mapping())
                self.user_message_fun(self._describe_grbl_coordinate_model())
            else:
                logger.error(gettext.gettext("一个或多个坐标轴设置写入失败。"))
            return

        if cmd == "walk_home":
            if not _state_guard():
                return
            ok_mode, _lines = serial_utils.grbl_send(
                self.plot_status, "G90", expect_ok=True, timeout_s=timeout_s)
            saved_origin = getattr(self.plot_status, "grbl_saved_origin_phys_in", None)
            if saved_origin is not None:
                park_x, park_y = saved_origin
                self.user_message_fun(
                    gettext.gettext("正在回到机器原点快照（G53/机器坐标）：X={0:.3f}, Y={1:.3f}").format(
                        park_x, park_y))
                ok_move, _lines = serial_utils.grbl_move_machine_linear(
                    self.plot_status,
                    park_x,
                    park_y,
                    rapid=True,
                    timeout_s=max(timeout_s, 3.0))
                self.pen.phys.xpos = 0.0
                self.pen.phys.ypos = 0.0
            else:
                park_x, park_y = self._origin_target_xy()
                ok_move, _lines = serial_utils.grbl_move_linear(
                    self.plot_status,
                    park_x,
                    park_y,
                    feed_in_s=max(self.speed_penup, 0.2),
                    rapid=True,
                    timeout_s=max(timeout_s, 3.0))
                self.pen.phys.xpos = park_x
                self.pen.phys.ypos = park_y
            if not (ok_mode and ok_move):
                logger.error(gettext.gettext("Failed to execute walk_home in Grbl mode."))
                return
            return

        if cmd == "set_origin_here":
            if not _state_guard():
                return
            self._capture_grbl_origin_snapshot(timeout_s)
            ok_zero, _lines = serial_utils.grbl_send(
                self.plot_status, "G92 X0 Y0", expect_ok=True, timeout_s=timeout_s)
            if not ok_zero:
                logger.error(gettext.gettext("Failed to set the current point as origin."))
                return
            self.plot_status.grbl_xy_zeroed = True
            self.pen.phys.xpos = 0.0
            self.pen.phys.ypos = 0.0
            self.user_message_fun(gettext.gettext("已将当前点设置为原点。"))
            self.user_message_fun(self._describe_grbl_axis_mapping())
            self.user_message_fun(self._describe_grbl_coordinate_model())
            return

        if cmd == "home_cycle":
            if not _state_guard():
                return
            ok, _lines = serial_utils.grbl_send(
                self.plot_status, "$H", expect_ok=True, timeout_s=max(timeout_s, 3.0))
            if not ok:
                logger.error(gettext.gettext("Failed to execute Grbl homing cycle ($H)."))
            return

        if cmd in ("walk_x", "walk_y", "walk_mmx", "walk_mmy",
                    "walk_mmx_pos", "walk_mmx_neg", "walk_mmy_pos", "walk_mmy_neg"):
            if not _state_guard():
                return
            dist_in = self.options.dist
            if cmd in ("walk_mmx", "walk_mmy", "walk_mmx_pos", "walk_mmx_neg", "walk_mmy_pos", "walk_mmy_neg"):
                dist_in = dist_in / 25.4
            if cmd in ("walk_mmx_neg", "walk_mmy_neg"):
                dist_in = -abs(dist_in)
            elif cmd in ("walk_mmx_pos", "walk_mmy_pos"):
                dist_in = abs(dist_in)
            delta_x = dist_in if cmd in ("walk_x", "walk_mmx", "walk_mmx_pos", "walk_mmx_neg") else 0
            delta_y = dist_in if cmd in ("walk_y", "walk_mmy", "walk_mmy_pos", "walk_mmy_neg") else 0
            current_x = self.pen.phys.xpos
            current_y = self.pen.phys.ypos
            target_x = current_x + delta_x
            target_y = current_y + delta_y
            target_x = min(max(target_x, self.bounds[0][0]), self.bounds[1][0])
            target_y = min(max(target_y, self.bounds[0][1]), self.bounds[1][1])
            clipped_dx = target_x - current_x
            clipped_dy = target_y - current_y
            if abs(clipped_dx) < 1e-9 and abs(clipped_dy) < 1e-9:
                self.user_message_fun(gettext.gettext("点动目标超出行程，已忽略。"))
                return
            speed_in_s = self.speed_penup if self.pen.phys.z_up else self.speed_pendown
            ok, _lines = serial_utils.grbl_jog(
                self.plot_status,
                clipped_dx,
                clipped_dy,
                speed_in_s,
                timeout_s=max(1.0, float(self.options.grbl_command_timeout)))
            if not ok:
                logger.error(gettext.gettext("Failed to execute Grbl jog command."))
                return
            self.pen.phys.xpos = target_x
            self.pen.phys.ypos = target_y
            return

        if cmd in ("bootload", "read_name", "write_name", "list_names"):
            logger.error(gettext.gettext("当前绘图机版插件不支持该命令。"))
            return

        logger.error(gettext.gettext("未识别的手动命令。"))


    def prepare_document(self):
        """
        Prepare the SVG document for plotting: Create the plot digest, join nearby ends,
        and perform supersampling. If not using randomization, then optimize the digest as well.
        """
        if not self.get_doc_props():
            logger.error(gettext.gettext('This document does not have valid dimensions.'))
            logger.error(gettext.gettext(
                'The page size should be in either millimeters (mm) or inches (in).\r\r'))
            logger.error(gettext.gettext(
                'Consider starting with the Letter landscape or '))
            logger.error(gettext.gettext('the A4 landscape template.\r\r'))
            logger.error(gettext.gettext('The page size may also be set in Inkscape,\r'))
            logger.error(gettext.gettext('using File > Document Properties.'))
            return False

        if not hasattr(self, 'backup_original'):
            self.backup_original = copy.deepcopy(self.document)

        # Modifications to SVG -- including re-ordering and text substitution
        #   may be made at this point, and will not be preserved.

        v_b = self.svg.get('viewBox')
        if v_b:
            p_a_r = self.svg.get('preserveAspectRatio')
            s_x, s_y, o_x, o_y = plot_utils.vb_scale(v_b, p_a_r, self.svg_width, self.svg_height)
        else:
            s_x = 1.0 / float(plot_utils.PX_PER_INCH) # Handle case of no viewbox
            s_y = s_x
            o_x = 0.0
            o_y = 0.0
        self.vb_stash = s_x, s_y, o_x, o_y

        # Initial transform of document is based on viewbox, if present:
        self.svg_transform = simpletransform.parseTransform(\
                f'scale({s_x:.6E},{s_y:.6E}) translate({o_x:.6E},{o_y:.6E})')

        valid_plob = False
        if self.plot_status.resume.old.plob_version:
            logger.debug('Checking Plob')
            valid_plob = digest_svg.verify_plob(self.svg, self.options.model)
        if valid_plob:
            logger.debug('Valid plob found; skipping standard pre-processing.')
            self.digest = path_objects.DocDigest()
            self.digest.from_plob(self.svg)
            self.plot_status.resume.new.plob_version = str(path_objects.PLOB_VERSION)
        else: # Process the input SVG into a simplified, restricted-format DocDigest object:
            digester = digest_svg.DigestSVG() # Initialize class
            if self.options.hiding: # Process all visible layers
                digest_params = [self.svg_width, self.svg_height, s_x, s_y,\
                    -2, self.params.curve_tolerance]
            else: # Process only selected layer, if in layers mode
                digest_params = [self.svg_width, self.svg_height, s_x, s_y,\
                    self.plot_status.resume.new.layer, self.params.curve_tolerance]
            self.digest = digester.process_svg(self.svg, self.warnings,
                digest_params, self.svg_transform,)

            if self.rotate_page: # Rotate digest
                self.digest.rotate(self.params.auto_rotate_ccw)

            if self.options.hiding:
                """
                Perform hidden-line clipping at this point, based on object
                    fills, clipping masks, and document and plotting bounds, via self.bounds
                """
                # clipping involves a non-pure Python dependency (pyclipper), so only import
                # when necessary
                from axidrawinternal.clipping import ClipPathsProcess
                bounds = ClipPathsProcess.calculate_bounds(self.bounds, self.svg_height,\
                    self.svg_width, self.params.clip_to_page, self.rotate_page)
                # flattening removes essential information for the clipping process
                assert not self.digest.flat
                self.digest.layers = ClipPathsProcess().run(self.digest.layers,\
                    bounds, clip_on=True)
                self.digest.layer_filter(self.plot_status.resume.new.layer) # For Layers mode
                self.digest.remove_unstroked() # Only stroked objects can plot
                self.digest.flatten() # Flatten digest before optimizations and plotting
            else:
                """
                Clip digest at plot bounds
                """
                if self.params.bounds_auto_scale:
                    digest_bounds = self._digest_bounds()
                    if digest_bounds is not None:
                        min_x, min_y, max_x, max_y = digest_bounds
                        tolerance = self.params.bounds_tolerance
                        exceeds = (
                            min_x < (self.bounds[0][0] - tolerance) or
                            min_y < (self.bounds[0][1] - tolerance) or
                            max_x > (self.bounds[1][0] + tolerance) or
                            max_y > (self.bounds[1][1] + tolerance)
                        )
                        if exceeds:
                            self._scale_digest_to_bounds()
                if self.rotate_page:
                    doc_bounds = [self.svg_height + 1e-9, self.svg_width + 1e-9]
                else:
                    doc_bounds = [self.svg_width + 1e-9, self.svg_height + 1e-9]
                out_of_bounds_flag = boundsclip.clip_at_bounds(self.digest, self.bounds,\
                    doc_bounds, self.params.bounds_tolerance, self.params.clip_to_page)
                if out_of_bounds_flag:
                    self.warnings.add_new('bounds')

            """
            Possible future work: Perform automatic hatch filling at this point, based on object
                fill colors and possibly other factors.
            """

            """
            Optimize digest
            """

            allow_reverse = self.options.reordering in [2, 3]

            if self.options.reordering < 3: # Set reordering to 4 to disable path joining
                plot_optimizations.connect_nearby_ends(self.digest, allow_reverse,\
                    self.params.min_gap)

            plot_optimizations.supersample(self.digest,\
                self.params.segment_supersample_tolerance)

            if self.options.controller == "grbl_esp32":
                optimize_stats = plot_optimizations.optimize_digest_for_plotter(
                    self.digest,
                    self.params.grbl_min_segment,
                    self.params.grbl_collinear_tolerance,
                    getattr(self.params, "grbl_near_dist", self.params.min_gap),
                    getattr(self.params, "grbl_simple_path_tolerance", 0.0),
                    allow_reverse)
                if (
                        optimize_stats["vertices_removed"] > 0 or
                        optimize_stats["paths_removed"] > 0 or
                        optimize_stats["paths_before_join"] > optimize_stats["paths_after_join"]
                ) and not self.plot_status.secondary:
                    self.user_message_fun(gettext.gettext(
                        "路径优化：移除 {0} 个冗余节点，清理 {1} 条极短路径，合并 {2} 条近邻路径。").format(
                            optimize_stats["vertices_removed"],
                            optimize_stats["paths_removed"],
                            max(0, optimize_stats["paths_before_join"] - optimize_stats["paths_after_join"])))
                sparse_cfg = self._auto_sparse_settings()
                if sparse_cfg["enabled"]:
                    sparse_stats = plot_optimizations.auto_sparse_linework(
                        self.digest,
                        sparse_cfg["threshold"],
                        min_dense_run=sparse_cfg["min_run"],
                        min_candidate_count=self.params.auto_sparse_line_min_count)
                    if sparse_stats["paths_removed"] > 0 and not self.plot_status.secondary:
                        self.user_message_fun(gettext.gettext(
                            "自动降级已稀疏线稿：移除 {0} 条密排线，涉及 {1} 个图层。").format(
                                sparse_stats["paths_removed"],
                                sparse_stats["layers_touched"]))

            self.randomize_optimize(True) # Do plot randomization & optimizations
            self._enable_auto_layer_pauses()

        # If it is necessary to save as a Plob, that conversion can be made like so:
        # plob = self.digest.to_plob() # Unnecessary re-conversion for testing only
        # self.digest.from_plob(plob)  # Unnecessary re-conversion for testing only
        return True


    def randomize_optimize(self, first_copy=False):
        """ Randomize start points & perform reordering """

        if self.plot_status.resume.new.plob_version != "n/a":
            return # Working from valid plob; do not perform any optimizations.
        if self.options.random_start:
            if self.options.mode != "res_plot": # Use old rand seed when resuming a plot.
                self.plot_status.resume.new.rand_seed = int(time.time()*100)
            plot_optimizations.randomize_start(self.digest, self.plot_status.resume.new.rand_seed)

        allow_reverse = self.options.reordering in [2, 3]

        if self.options.reordering in [1, 2, 3]:
            travel_before = None
            if first_copy and self.options.controller == "grbl_esp32":
                travel_before = plot_optimizations.estimate_pen_up_travel(self.digest)
            plot_optimizations.reorder(self.digest, allow_reverse, start=[0.0, 0.0])
            if travel_before is not None and not self.plot_status.secondary:
                travel_after = plot_optimizations.estimate_pen_up_travel(self.digest)
                travel_saved = max(0.0, travel_before - travel_after)
                if travel_saved > 0.001:
                    self.user_message_fun(gettext.gettext(
                        "路径重排：预计抬笔空走从 {0:.1f} mm 降到 {1:.1f} mm，减少 {2:.1f} mm。").format(
                            travel_before * 25.4,
                            travel_after * 25.4,
                            travel_saved * 25.4))

        if first_copy and self.options.digest: # Will return Plob, not full SVG; back it up here.
            self.backup_original = copy.deepcopy(self.digest.to_plob())

    def _enable_auto_layer_pauses(self):
        """Automatically insert pause markers at each new plotted layer."""
        if not bool(getattr(self.options, "auto_pause_between_layers",
                            getattr(self.params, "auto_pause_between_layers", False))):
            return
        if not self.digest or not self.digest.layers:
            return
        seen_first = False
        for layer in self.digest.layers:
            if not layer.paths:
                continue
            if not seen_first:
                seen_first = True
                continue
            layer.props.pause = True


    def plot_document(self):
        """ Plot the prepared SVG document, if so selected in the interface """

        if not self.options.preview:
            self.plot_status.resume.clear_button(self) # Query button to clear its state
            self.options.rendering = 0 # Only render previews if we are in preview mode.
            self.preview.v_chart.enable = False
            if self.plot_status.port is None:
                return
            self.query_ebb_voltage()

        self.plot_status.progress.launch(self)

        try:  # wrap everything in a try so we can be sure to close the serial port
            self.pen.servo_init(self)
            self.pen.pen_raise(self)
            self.enable_motors()  # Set plotting resolution

            self.plot_doc_digest(self.digest) # Step through and plot contents of document digest
            self.pen.pen_raise(self)

            if self.plot_status.stopped == 0: # Return Home after normal plot
                self.plot_status.resume.new.clean() # Clear flags indicating resume status
                if self._grbl_return_to_origin_after_plot_enabled():
                    self.go_to_parking_position(wait_for_completion=True)
                else:
                    self.user_message_fun(gettext.gettext("按当前设置，完成后不自动回工作原点。"))

        finally: # In case of an exception and loss of the serial port...
            pass

        self.plot_status.progress.close()

    def plot_cleanup(self):
        """
        Perform standard actions after a plot or the last copy from a set of plots:
        Revert file, render previews, print time reports, run webhook.

        Reverting is back to original SVG document, prior to adding preview layers.
            and prior to saving updated "plotdata" progress data in the file.
            No changes to the SVG document prior to this point will be saved.

        Doing so allows us to use routines that alter the SVG prior to this point,
            e.g., plot re-ordering for speed or font substitutions.
        """
        self.document = copy.deepcopy(self.backup_original)

        try: # Handle cases: backup_original May be etree Element or ElementTree
            self.svg = self.document.getroot() # For ElementTree, get the root
        except AttributeError:
            self.svg = self.document # For Element; no need to get the root

        if self.options.digest:
            self.options.rendering = 0 # Turn off rendering

        if self.options.digest > 1: # Save Plob file only and exit.
            elapsed_time = time.time() - self.start_time
            self.time_elapsed = elapsed_time # Available for use by python API
            if self.options.report_time and not self.called_externally: # Print time only
                self.user_message_fun("Elapsed time: " + text_utils.format_hms(elapsed_time))
            return

        self.preview.render(self) # Render preview on the page, if enabled and in preview mode

        if self.plot_status.progress.enable and self.plot_status.stopped == 0:
            self.user_message_fun("\nAxiCLI plot complete.\n") # If sequence ended normally.
        elapsed_time = time.time() - self.start_time
        self.time_elapsed = elapsed_time # Available for use by python API

        if not self.called_externally: # Compile time estimates & print time reports
            self.plot_status.stats.report(self.options, self.user_message_fun, elapsed_time)
            self.pen.status.report(self, self.user_message_fun)
            if self.options.report_time and self.plot_status.resume.new.plob_version != "n/a":
                self.user_message_fun("Document printed from valid Plob digest.")

        if self.options.webhook and not self.options.preview:
            if self.options.webhook_url is not None:
                payload = {'value1': str(self.digest.name),
                    'value2': str(text_utils.format_hms(elapsed_time)),
                    'value3': str(self.options.port),
                    }
                try:
                    requests.post(self.options.webhook_url, data=payload, timeout=7)
                except (TimeoutError, urllib3.exceptions.ConnectTimeoutError,\
                    urllib3.exceptions.MaxRetryError, requests.exceptions.ConnectTimeout):
                    self.user_message_fun("Webhook notification failed (Timed out).\n")
                except (urllib3.exceptions.NewConnectionError,\
                    socket.gaierror, requests.exceptions.ConnectionError):
                    self.user_message_fun("An error occurred while posting webhook. " +
                        "Check your internet connection and webhook URL.\n")

    def plot_doc_digest(self, digest):
        """
        Step through the document digest and plot each of the vertex lists.

        Takes a flattened path_objects.DocDigest object as input. All
        selection of elements to plot and their rendering, including
        transforms, needs to be handled before this routine.
        """

        if not digest:
            return

        for layer in digest.layers:

            self.pen.end_temp_height(self)
            old_use_layer_speed = self.use_layer_speed  # A Boolean
            old_layer_speed_pendown = self.layer_speed_pendown  # Numeric value
            self.pen.pen_raise(self) # Raise pen prior to computing layer properties

            if self.options.mode == "layers": # Special case: The plob contains all layers
                if layer.props.number != self.options.layer: # and is plotted in layers mode.
                    continue # Here, ensure that only certain layers should be printed.

            self.eval_layer_props(layer.props)

            for path_item in layer.paths:
                if self.plot_status.stopped:
                    return
                self.plot_polyline(path_item.subpaths[0])
            self.use_layer_speed = old_use_layer_speed # Restore old layer status variables

            if self.layer_speed_pendown != old_layer_speed_pendown:
                self.layer_speed_pendown = old_layer_speed_pendown
                self.enable_motors() # Set speed value variables for this layer.
            self.pen.end_temp_height(self)

    def eval_layer_props(self, layer_props):
        """
        Check for encoded pause, delay, speed, or height in the layer name, and act upon them.
        Syntax described at: https://wiki.evilmadscientist.com/AxiDraw_Layer_Control
        """

        if layer_props.pause: # Insert programmatic pause
            if self.options.preview:
                layer_props.pause = False
                return
            if not self.plot_status.progress.dry_run: # Skip during dry run only
                manual_pen_change = bool(getattr(
                    self.options,
                    "manual_pen_change",
                    getattr(self.params, "manual_pen_change", False)))
                if manual_pen_change and self.options.mode in ("plot", "layers", "res_plot"):
                    if not self.run_pen_change_flow():
                        if self.plot_status.stopped == 0:
                            self.plot_status.stopped = -1
                        self.pause_check()
                else:
                    if self.plot_status.stopped == 0: # If not already stopped
                        self.plot_status.stopped = -1 # Set flag for programmatic pause
                    self.pause_check()  # Carry out the pause, or resume if required.

        old_speed = self.layer_speed_pendown

        self.use_layer_speed = False
        self.layer_speed_pendown = -1

        if layer_props.delay:
            dripfeed.page_layer_delay(self, between_pages=False, delay_ms=layer_props.delay)
        if layer_props.height is not None: # New height will be used when we next lower the pen.
            self.pen.set_temp_height(self, layer_props.height)
        if layer_props.speed:
            self.use_layer_speed = True
            self.layer_speed_pendown = layer_props.speed

        if self.layer_speed_pendown != old_speed:
            self.enable_motors()  # Set speed value variables for this layer.

    def run_pen_change_flow(self):
        """
        Pen-change flow modeled after sender-based tool change:
        save plotting position, move to Home for swapping pen, prompt user,
        then return to saved position and continue.
        """
        saved_x = self.pen.phys.xpos
        saved_y = self.pen.phys.ypos
        if saved_x is None or saved_y is None:
            return False
        self.user_message_fun(gettext.gettext(
            "检测到图层切换，准备手动换笔。"))
        try:
            self.pen.pen_raise(self)
            if bool(getattr(self.options, "pen_change_to_home",
                            getattr(self.params, "pen_change_to_home", True))):
                self.go_to_parking_position(wait_for_completion=True)
            if bool(getattr(self.options, "pen_change_prompt",
                            getattr(self.params, "pen_change_prompt", True))):
                if not self.confirm_pen_change():
                    self.user_message_fun(gettext.gettext("用户取消了换笔，已暂停绘图。"))
                    return False
            self.go_to_position(saved_x, saved_y)
            return True
        except Exception:
            logger.error(gettext.gettext("换笔流程执行失败；为安全起见，绘图已暂停。"))
            return False

    def confirm_pen_change(self):
        """Prompt user to confirm pen change and continue plotting."""
        prompt_text = gettext.gettext(
            "请现在手动换笔，完成后点击“是”继续绘图。")
        if self.options.mode == "interactive":
            return False
        if self.plot_status.cli_api:
            self.user_message_fun(prompt_text)
            try:
                user_reply = input("Continue after pen change? [y/N]: ").strip().lower()
            except EOFError:
                return False
            return user_reply in ("y", "yes")
        try:
            if tkinter is None or messagebox is None:
                return False
            root = tkinter.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
                root.lift()
                root.focus_force()
                root.update_idletasks()
            except Exception:
                pass
            result = messagebox.askyesno("绘图机换笔", prompt_text)
            root.destroy()
            return result
        except Exception:
            self.user_message_fun(prompt_text)
            return False

    def _digest_bounds(self):
        """Return digest bounds as (min_x, min_y, max_x, max_y), or None."""
        if not self.digest or not self.digest.layers:
            return None
        min_x = None
        min_y = None
        max_x = None
        max_y = None
        for layer in self.digest.layers:
            for path in layer.paths:
                if not path.subpaths:
                    continue
                for subpath in path.subpaths:
                    for vertex in subpath:
                        x_value = vertex[0]
                        y_value = vertex[1]
                        min_x = x_value if min_x is None else min(min_x, x_value)
                        min_y = y_value if min_y is None else min(min_y, y_value)
                        max_x = x_value if max_x is None else max(max_x, x_value)
                        max_y = y_value if max_y is None else max(max_y, y_value)
        if min_x is None:
            return None
        return min_x, min_y, max_x, max_y

    def _confirm_bounds_auto_scale(self, scale_factor):
        """Prompt user to approve automatic scaling into travel bounds."""
        prompt_text = gettext.gettext(
            f"Detected out-of-bounds drawing. Auto-scale to fit travel area "
            f"(scale {scale_factor:.3f}) and continue?")
        if self.plot_status.cli_api:
            self.user_message_fun(prompt_text)
            try:
                user_reply = input("Apply auto-scale and continue? [y/N]: ").strip().lower()
            except EOFError:
                return False
            return user_reply in ("y", "yes")
        if tkinter is None or messagebox is None:
            self.user_message_fun(prompt_text)
            return False
        try:
            root = tkinter.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
                root.lift()
                root.focus_force()
                root.update_idletasks()
            except Exception:
                pass
            result = messagebox.askyesno("AxiDraw Bounds Warning", prompt_text)
            root.destroy()
            return result
        except Exception:
            self.user_message_fun(prompt_text)
            return False

    def _scale_digest_to_bounds(self):
        """Scale digest uniformly into machine bounds; return True if applied."""
        digest_bounds = self._digest_bounds()
        if digest_bounds is None:
            return False
        min_x, min_y, max_x, max_y = digest_bounds
        width = max(max_x - min_x, 1e-9)
        height = max(max_y - min_y, 1e-9)
        target_min_x = self.bounds[0][0]
        target_min_y = self.bounds[0][1]
        target_width = max(self.bounds[1][0] - self.bounds[0][0], 1e-9)
        target_height = max(self.bounds[1][1] - self.bounds[0][1], 1e-9)
        scale_factor = min(target_width / width, target_height / height)
        if scale_factor >= 0.999999:
            return False
        if self.params.bounds_auto_scale_prompt:
            if not self._confirm_bounds_auto_scale(scale_factor):
                return False
        for layer in self.digest.layers:
            for path in layer.paths:
                if not path.subpaths:
                    continue
                for subpath in path.subpaths:
                    for vertex in subpath:
                        vertex[0] = (vertex[0] - min_x) * scale_factor + target_min_x
                        vertex[1] = (vertex[1] - min_y) * scale_factor + target_min_y
        self.digest.width = self.digest.width * scale_factor
        self.digest.height = self.digest.height * scale_factor
        self.user_message_fun(gettext.gettext(
            f"Applied auto-scale factor {scale_factor:.3f} to fit machine travel bounds."))
        return True

    def plot_polyline(self, vertex_list):
        """
        Plot a polyline object; a single pen-down XY movement.
        - No transformations, no curves, no neat clipping at document bounds;
            those are all performed _before_ we get to this point.
        - Truncate motion, brute-force, at travel bounds, without mercy or printed warnings.
        """

        if self.plot_status.stopped:
            logger.debug('Polyline: self.plot_status.stopped.')
            return
        if not vertex_list:
            logger.debug('No vertex list to plot. Returning.')
            return
        if len(vertex_list) < 2:
            logger.debug('No full segments in vertex list. Returning.')
            return

        self.pen.pen_raise(self) # Raise, if necessary, prior to pen-up travel to first vertex

        for vertex in vertex_list:
            vertex[0], _t_x = plot_utils.checkLimitsTol(vertex[0], 0, self.bounds[1][0], 2e-9)
            vertex[1], _t_y = plot_utils.checkLimitsTol(vertex[1], 0, self.bounds[1][1], 2e-9)
            # if _t_x or _t_y:
            #     logger.debug('Travel truncated to bounds at plot_polyline.')

        # Pen up straight move, zero velocity at endpoints, to first vertex location
        self.go_to_position(vertex_list[0][0], vertex_list[0][1])

        if serial_utils.is_grbl(self.plot_status) and not self.options.preview:
            self._plot_polyline_grbl(vertex_list)
            return

        # Plan and feed trajectory, including lowering and raising pen before and after:
        the_trajectory = motion.trajectory(self, vertex_list)
        dripfeed.feed(self, the_trajectory[0])

    def go_to_position(self, x_dest, y_dest, ignore_limits=False, xyz_pos=None):
        '''
        Immediate XY move to destination, using normal motion planning. Replaces legacy
        function "plot_seg_with_v", assuming zero initial and final velocities.
        '''
        if serial_utils.is_grbl(self.plot_status) and not self.options.preview:
            self._go_to_position_grbl(x_dest, y_dest, ignore_limits=ignore_limits)
            return
        target_data = (x_dest, y_dest, 0, 0, ignore_limits)
        the_trajectory = motion.compute_segment(self, target_data, xyz_pos)
        dripfeed.feed(self, the_trajectory[0])

    def _go_to_position_grbl(self, x_dest, y_dest, ignore_limits=False):
        """Direct Grbl move without EBB-style micro-segmentation."""
        if self.plot_status.stopped:
            return
        if not ignore_limits:
            x_dest, _ = plot_utils.checkLimitsTol(x_dest, 0, self.bounds[1][0], 2e-9)
            y_dest, _ = plot_utils.checkLimitsTol(y_dest, 0, self.bounds[1][1], 2e-9)
        current_x = self.pen.phys.xpos if self.pen.phys.xpos is not None else x_dest
        current_y = self.pen.phys.ypos if self.pen.phys.ypos is not None else y_dest
        move_dist = plot_utils.distance(x_dest - current_x, y_dest - current_y)
        rapid = bool(self.pen.phys.z_up)
        speed_in_s = self.speed_penup if rapid else self.speed_pendown
        ok, _lines = serial_utils.grbl_move_linear(
            self.plot_status,
            x_dest,
            y_dest,
            feed_in_s=max(speed_in_s, 0.2),
            rapid=rapid,
            timeout_s=max(4.0, float(getattr(self.options, "grbl_command_timeout", 2.0)) * 3.0))
        if not ok:
            self.plot_status.stopped = 104
            self.user_message_fun(gettext.gettext(
                "Grbl direct motion command failed; plotting was stopped."))
            return
        self.plot_status.stats.add_dist(bool(self.pen.phys.z_up), move_dist)
        self.plot_status.progress.update_auto(self.plot_status.stats)
        self.pen.phys.xpos = x_dest
        self.pen.phys.ypos = y_dest

    def _plot_polyline_grbl(self, vertex_list):
        """Plot one polyline by streaming direct G-code vertices to Grbl."""
        if self.plot_status.stopped or len(vertex_list) < 2:
            return
        self.pen.pen_lower(self)
        if self.plot_status.stopped:
            return
        timeout_s = max(4.0, float(getattr(self.options, "grbl_command_timeout", 2.0)) * 3.0)
        for vertex in vertex_list[1:]:
            x_dest = vertex[0]
            y_dest = vertex[1]
            current_x = self.pen.phys.xpos if self.pen.phys.xpos is not None else x_dest
            current_y = self.pen.phys.ypos if self.pen.phys.ypos is not None else y_dest
            move_dist = plot_utils.distance(x_dest - current_x, y_dest - current_y)
            ok, _lines = serial_utils.grbl_queue_linear(
                self.plot_status,
                x_dest,
                y_dest,
                self.speed_pendown,
                rapid=False,
                timeout_s=timeout_s)
            if not ok:
                self.plot_status.stopped = 104
                self.user_message_fun(gettext.gettext(
                    "Grbl motion stream failed; plotting was stopped."))
                return
            self.plot_status.stats.add_dist(False, move_dist)
            self.plot_status.progress.update_auto(self.plot_status.stats)
            self.pen.phys.xpos = x_dest
            self.pen.phys.ypos = y_dest
        serial_utils.grbl_flush_motion(self.plot_status, timeout_s=max(15.0, timeout_s), wait_idle=False)
        self.pen.pen_raise(self)

    def _parking_target_xy(self):
        """Return logical XY target for parking/home motion."""
        parking_corner = str(getattr(self.params, "parking_corner", "origin") or "origin").strip().lower()
        if serial_utils.is_grbl(self.plot_status) and bool(getattr(self.plot_status, "grbl_xy_zeroed", False)):
            if parking_corner in ("left_upper", "origin"):
                return 0.0, 0.0
        if serial_utils.is_grbl(self.plot_status) and parking_corner == "left_upper":
            physical_x = 0.0
            physical_y = max(float(getattr(self.params, "y_travel_default", self.bounds[1][1])), 0.0)
            return serial_utils._axis_map_in(self.plot_status, physical_x, physical_y)
        return self.params.start_pos_x, self.params.start_pos_y

    def _origin_target_xy(self):
        """Return logical XY target for configured default origin."""
        origin_corner = str(getattr(self.params, "origin_corner", "origin") or "origin").strip().lower()
        if serial_utils.is_grbl(self.plot_status) and bool(getattr(self.plot_status, "grbl_xy_zeroed", False)):
            if origin_corner in ("left_upper", "origin"):
                return 0.0, 0.0
        if origin_corner != "left_upper":
            return self.params.start_pos_x, self.params.start_pos_y

        physical_x = 0.0
        physical_y = max(float(getattr(self.params, "y_travel_default", self.bounds[1][1])), 0.0)
        return serial_utils._axis_map_in(self.plot_status, physical_x, physical_y)

    def _capture_grbl_origin_snapshot(self, timeout_s):
        """Capture both machine and work coordinate snapshots for origin/home actions."""
        origin_positions = serial_utils.grbl_get_positions(
            self.plot_status, timeout_s=min(timeout_s, 0.8))
        self.plot_status.grbl_saved_origin_phys_in = origin_positions.get("mpos_phys")
        self.plot_status.grbl_saved_origin_work_in = origin_positions.get("wpos")
        self.plot_status.grbl_saved_origin_xy = origin_positions.get("mpos_phys")
        return origin_positions

    def go_to_parking_position(self, wait_for_completion=True):
        """Move to configured parking/home position and optionally wait until motion completes."""
        park_x, park_y = self._parking_target_xy()
        if serial_utils.is_grbl(self.plot_status) and not self.options.preview:
            timeout_s = max(5.0, float(getattr(self.options, "grbl_command_timeout", 2.0)) * 4.0)
            self.user_message_fun(
                gettext.gettext(
                    "正在回到工作原点：X={0:.3f}, Y={1:.3f}").format(park_x, park_y))
            ok_mode, mode_lines = serial_utils.grbl_send(
                self.plot_status,
                "G90",
                expect_ok=True,
                timeout_s=timeout_s)
            if getattr(self.plot_status, "grbl_saved_origin_phys_in", None) is not None:
                saved_x, saved_y = self.plot_status.grbl_saved_origin_phys_in
                self.user_message_fun(
                    gettext.gettext(
                        "正在回到机器原点快照（G53/机器坐标）：X={0:.3f}, Y={1:.3f}").format(
                            saved_x, saved_y))
                ok, move_lines = serial_utils.grbl_move_machine_linear(
                    self.plot_status,
                    saved_x,
                    saved_y,
                    rapid=True,
                    timeout_s=timeout_s)
            else:
                ok, move_lines = serial_utils.grbl_move_linear(
                    self.plot_status,
                    park_x,
                    park_y,
                    feed_in_s=max(self.speed_penup, 0.2),
                    rapid=True,
                    timeout_s=timeout_s)
            if ok_mode and ok:
                self.pen.phys.xpos = park_x
                self.pen.phys.ypos = park_y
                if wait_for_completion:
                    serial_utils.grbl_wait_idle(self.plot_status, timeout_s=timeout_s)
            else:
                self.user_message_fun(gettext.gettext(
                    "回工作原点动作发送失败。G90={0} {1}; MOVE={2} {3}").format(
                        ok_mode, mode_lines, ok, move_lines))
        else:
            self.go_to_position(park_x, park_y)
            if wait_for_completion and serial_utils.is_grbl(self.plot_status) and not self.options.preview:
                serial_utils.grbl_flush_motion(
                    self.plot_status,
                    timeout_s=max(5.0, float(getattr(self.options, "grbl_command_timeout", 2.0)) * 4.0),
                    wait_idle=True)

    def pause_check(self):
        """ Manage Pause functionality and stop plot if requested or at certain errors """
        if self.plot_status.stopped > 0:
            return  # Plot is already stopped. No need to proceed.

        if serial_utils.is_grbl(self.plot_status) and not self.options.preview:
            status_age = time.time() - float(getattr(
                self.plot_status, "grbl_last_status_timestamp", 0) or 0)
            status_line = None
            if status_age > 0.8:
                status_line = serial_utils.grbl_query_status(self.plot_status, timeout_s=0.12)
            if status_line and status_line.startswith("<Alarm"):
                self.user_message_fun(gettext.gettext(
                    "Plot paused because the Grbl controller reported ALARM state."))
                self.plot_status.stopped = -104

        pause_button_pressed = self.plot_status.resume.check_button(self)

        if self.receive_pause_request(): # Keyboard interrupt detected!
            self.plot_status.stopped = -103 # Code 104: "Keyboard interrupt"
            if self.plot_status.delay_between_copies: # However... it could have been...
                self.plot_status.stopped = -2 # Paused between copies (OK).

        if self.plot_status.stopped == -1:
            self.user_message_fun('Plot paused programmatically.\n')
        if self.plot_status.stopped == -103:
            self.user_message_fun('\nPlot paused by keyboard interrupt.\n')

        if pause_button_pressed == -1:
            self.user_message_fun('\nError: USB connection to AxiDraw lost. ' +\
                f'[Position: {25.4 * self.plot_status.stats.down_travel_inch:.3f} mm]\n')


            self.connected = False # Python interactive API variable
            self.plot_status.stopped = -104 # Code 104: "Lost connectivity"

        if pause_button_pressed == 1:
            if self.plot_status.delay_between_copies:
                self.plot_status.stopped = -2 # Paused between copies.
            elif self.options.mode == "interactive":
                logger.warning('Plot halted by button press during interactive session.')
                logger.warning('Manually home this AxiDraw before plotting next item.\n')
                self.plot_status.stopped = -102 # Code 102: "Paused by button press"
            else:
                self.user_message_fun('Plot paused by button press.\n')
                self.plot_status.stopped = -102 # Code 102: "Paused by button press"

        if self.plot_status.stopped == -2:
            self.user_message_fun('Plot sequence ended between copies.\n')

        if self.plot_status.stopped in (-1, -102, -103):
            self.user_message_fun('(Paused after: ' +\
                f'{25.4 * self.plot_status.stats.down_travel_inch:.3f} mm of pen-down travel.)')

        if self.plot_status.stopped < 0: # Stop plot
            self.pen.pen_raise(self)
            if not self.plot_status.delay_between_copies and \
                not self.plot_status.secondary  and self.options.mode != "interactive":
                # Only print if we're not in the delay between copies, nor a "second" unit.
                if self.plot_status.stopped != -104: # Do not display after loss of USB.
                    self.user_message_fun('Use the resume feature to continue.\n')
            self.plot_status.stopped = - self.plot_status.stopped
            self.plot_status.copies_to_plot = 0

            if self.options.mode not in ("plot", "layers", "res_plot"):
                return # Don't update pause_dist in res_home or repositioning modes

            self.plot_status.resume.new.pause_dist = self.plot_status.stats.down_travel_inch
            self.plot_status.resume.new.pause_ref = self.plot_status.stats.down_travel_inch

    def serial_connect(self):
        """ Connect to AxiDraw over USB """
        if serial_utils.connect(self.options, self.plot_status, self.user_message_fun, logger):
            self.connected = True  # Variable available in the Python interactive API.
            if serial_utils.is_grbl(self.plot_status) and not self.options.preview:
                timeout_s = max(4.0, float(getattr(self.options, "grbl_command_timeout", 2.0)) * 3.0)
                self._capture_grbl_origin_snapshot(timeout_s)
                if self._grbl_auto_zero_on_connect_enabled():
                    ok_zero, _lines = serial_utils.grbl_send(
                        self.plot_status, "G92 X0 Y0", expect_ok=True, timeout_s=timeout_s)
                    if ok_zero:
                        self.plot_status.grbl_xy_zeroed = True
                        self.pen.phys.xpos = 0.0
                        self.pen.phys.ypos = 0.0
                        self.user_message_fun(gettext.gettext("已将当前位置设为原点。"))
                        self.user_message_fun(self._describe_grbl_axis_mapping())
                        self.user_message_fun(self._describe_grbl_coordinate_model())
                    else:
                        self.user_message_fun(gettext.gettext("设置当前位置为原点失败。"))
                else:
                    self.user_message_fun(gettext.gettext("按当前设置，连接后不自动设置工作原点。"))
        else:
            self.plot_status.stopped = 101 # Will become exit code 101; failed to connect

    def enable_motors(self):
        """
        Enable motors, set native motor resolution, and set speed scales.
        The "pen down" speed scale is adjusted by reducing speed when using 8X microstepping or
        disabling aceleration. These factors prevent unexpected dramatic changes in speed when
        turning those two options on and off.
        """
        if self.use_layer_speed:
            local_speed_pendown = self.layer_speed_pendown
        else:
            local_speed_pendown = self.options.speed_pendown

        if self.options.resolution == 1:  # High-resolution ("Super") mode
            if not self.options.preview and not serial_utils.is_grbl(self.plot_status):
                res_1, res_2 = ebb_motion.query_enable_motors(self.plot_status.port, False)
                if not (res_1 == 1 and res_2 == 1): # Do not re-enable if already enabled
                    ebb_motion.sendEnableMotors(self.plot_status.port, 1)  # 16X microstepping
            self.step_scale = 2.0 * self.params.native_res_factor
            self.speed_pendown = local_speed_pendown * self.params.speed_lim_xy_hr / 110.0
            self.speed_penup = self.options.speed_penup * self.params.speed_lim_xy_hr / 110.0
            if self.options.const_speed:
                self.speed_pendown = self.speed_pendown * self.params.const_speed_factor_hr
        else:  # i.e., self.options.resolution == 2; Low-resolution ("Normal") mode
            if not self.options.preview and not serial_utils.is_grbl(self.plot_status):
                res_1, res_2 = ebb_motion.query_enable_motors(self.plot_status.port, False)
                if not (res_1 == 2 and res_2 == 2): # Do not re-enable if already enabled
                    ebb_motion.sendEnableMotors(self.plot_status.port, 2)  # 8X microstepping
            self.step_scale = self.params.native_res_factor
            # Low-res mode: Allow faster pen-up moves. Keep maximum pen-down speed the same.
            self.speed_penup = self.options.speed_penup * self.params.speed_lim_xy_lr / 110.0
            self.speed_pendown = local_speed_pendown * self.params.speed_lim_xy_lr / 110.0
            if self.options.const_speed:
                self.speed_pendown = self.speed_pendown * self.params.const_speed_factor_lr
        if serial_utils.is_grbl(self.plot_status) and not self.options.preview:
            timeout_s = max(4.0, float(self.options.grbl_command_timeout) * 3.0)
            ok_init, failed_cmd, _result = serial_utils.grbl_initialize_motion(
                self.plot_status,
                timeout_s=timeout_s,
                zero_z=False,
                zero_xy=False)
            if not ok_init:
                self.plot_status.stopped = 104
                logger.error(
                    gettext.gettext(
                        "Failed to initialize Grbl motion mode ({0}).").format(failed_cmd))
        # ebb_serial.command(self.plot_status.port, "CU,3,1\r") # EBB 2.8.1+: Enable data-low LED

    def query_ebb_voltage(self):
        """ Check that power supply is detected. """
        serial_utils.query_voltage(self.options, self.params, self.plot_status, self.warnings)

    def get_doc_props(self):
        """
        Get the document's height and width attributes from the <svg> tag. Use a default value in
        case the property is not present or is expressed in units of percentages.
        """

        self.svg_height = plot_utils.getLengthInches(self, 'height')
        self.svg_width = plot_utils.getLengthInches(self, 'width')

        width_string = self.svg.get('width')
        if width_string:
            _value, units = plot_utils.parseLengthWithUnits(width_string)
            self.doc_units = units
        if self.svg_height is None or self.svg_width is None:
            return False
        if self.options.no_rotate: # Override regular auto_rotate option
            self.options.auto_rotate = False
        if self.options.auto_rotate and (self.svg_height > self.svg_width):
            self.rotate_page = True
        return True

    def get_output(self):
        """Return serialized copy of svg document output"""
        result = etree.tostring(self.document)
        return result.decode("utf-8")

    def disconnect(self):
        '''End serial session; disconnect from AxiDraw '''
        if self.plot_status.port:
            serial_utils.disconnect_port(self.plot_status)
        self.plot_status.port = None
        self.connected = False  # Python interactive API variable

class SecondaryLoggingHandler(logging.Handler):
    '''To be used for logging to AxiDraw.text_out and AxiDraw.error_out.'''
    def __init__(self, axidraw, log_name, level = logging.NOTSET):
        super().__init__(level=level)

        log = getattr(axidraw, log_name) if hasattr(axidraw, log_name) else ""
        setattr(axidraw, log_name, log)

        self.axidraw = axidraw
        self.log_name = log_name

        self.setFormatter(logging.Formatter()) # pass message through unchanged

    def emit(self, record):
        assert(hasattr(self.axidraw, self.log_name))
        new_log = getattr(self.axidraw, self.log_name) + "\n" + self.format(record)
        setattr(self.axidraw, self.log_name, new_log)

class SecondaryErrorHandler(SecondaryLoggingHandler):
    '''Handle logging for "secondary" machines, plotting alongside primary.'''
    def __init__(self, axidraw):
        super().__init__(axidraw, 'error_out', logging.ERROR)

class SecondaryNonErrorHandler(SecondaryLoggingHandler):
    class ExceptErrorsFilter(logging.Filter):
        def filter(self, record):
            return record.levelno < logging.ERROR

    def __init__(self, axidraw):
        super().__init__(axidraw, 'text_out')
        self.addFilter(self.ExceptErrorsFilter())

if __name__ == '__main__':
    logging.basicConfig()
    e = AxiDraw()
    exit_status.run(e.affect)
