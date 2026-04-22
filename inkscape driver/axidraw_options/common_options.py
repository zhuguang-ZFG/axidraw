import argparse

from ink_extensions import inkex

def core_axidraw_options(config):
    mode_options = core_mode_options(config)
    options = core_options(config)
    return argparse.ArgumentParser(add_help = False, parents = [mode_options, options])

def core_options(config):
    ''' options that are used in extensions in this library, as well as in hershey-advanced and
    potentially others '''
    options = argparse.ArgumentParser(add_help = False) # parent parser

    options.add_argument("--speed_pendown",\
                        type=int, action="store", dest="speed_pendown", \
                        default=config["speed_pendown"], \
                        help="Maximum plotting speed, when pen is down (1-100)")

    options.add_argument("--speed_penup",\
                        type=int, action="store", dest="speed_penup", \
                        default=config["speed_penup"], \
                        help="Maximum transit speed, when pen is up (1-100)")

    options.add_argument("--accel",\
                        type=int, action="store", dest="accel", \
                        default=config["accel"], \
                        help="Acceleration rate factor (1-100)")

    options.add_argument("--pen_pos_down",\
                        type=int, action="store", dest="pen_pos_down",\
                        default=config["pen_pos_down"],\
                        help="Height of pen when lowered (0-100)")

    options.add_argument("--pen_pos_up",\
                        type=int, action="store", dest="pen_pos_up", \
                        default=config["pen_pos_up"], \
                        help="Height of pen when raised (0-100)")

    options.add_argument("--pen_rate_lower",\
                        type=int, action="store", dest="pen_rate_lower",\
                        default=config["pen_rate_lower"], \
                        help="Rate of lowering pen (1-100)")

    options.add_argument("--pen_rate_raise",\
                        type=int, action="store", dest="pen_rate_raise",\
                        default=config["pen_rate_raise"],\
                        help="Rate of raising pen (1-100)")

    options.add_argument("--pen_delay_down",\
                        type=int, action="store", dest="pen_delay_down",\
                        default=config["pen_delay_down"],\
                        help="Optional delay after pen is lowered (ms)")

    options.add_argument("--pen_delay_up",\
                        type=int, action="store", dest="pen_delay_up", \
                        default=config["pen_delay_up"],\
                        help="Optional delay after pen is raised (ms)")

    options.add_argument("--no_rotate",\
                        type=inkex.boolean_option, action="store", dest="no_rotate",\
                        default=False,\
                        help="Disable auto-rotate; preserve plot orientation")

    options.add_argument("--const_speed",\
                        type=inkex.boolean_option, action="store", dest="const_speed",\
                        default=config["const_speed"],\
                        help="Use constant velocity when pen is down")

    options.add_argument("--report_time",\
                        type=inkex.boolean_option, action="store", dest="report_time",\
                        default=config["report_time"],\
                        help="Report time elapsed")

    options.add_argument("--page_delay",\
                        type=int, action="store", dest="page_delay",\
                        default=config["page_delay"],\
                        help="Optional delay between copies (s).")

    options.add_argument("--preview",\
                        type=inkex.boolean_option, action="store", dest="preview",\
                        default=config["preview"],\
                        help="Preview mode; simulate plotting only.")

    options.add_argument("--rendering",\
                        type=int, action="store", dest="rendering",\
                        default=config["rendering"],\
                        help="Preview mode rendering option (0-3). 0: None. " \
                        + "1: Pen-down movement. 2: Pen-up movement. 3: All movement.")

    options.add_argument("--model",\
                        type=int, action="store", dest="model",\
                        default=config["model"],\
                        help="AxiDraw Model (1-6). 1: AxiDraw V2 or V3. " \
                        + "2: AxiDraw V3/A3 or SE/A3. 3: AxiDraw V3 XLX. " \
                        + "4: AxiDraw MiniKit. 5: AxiDraw SE/A1. 6: AxiDraw SE/A2.")

    options.add_argument("--penlift",\
                        type=int, action="store", dest="penlift",\
                        default=config["penlift"],\
                        help="pen lift servo configuration (1-3). " \
                        + "1: Default for AxiDraw model. " \
                        + "2: Standard servo (lowest connector position). " \
                        + "3: Narrow-band brushless servo (3rd position up).")

    options.add_argument("--port_config",\
                        type=int, action="store", dest="port_config",\
                        default=config["port_config"],\
                        help="Port use code (0-3)."\
                        +" 0: Plot to first unit found, unless port is specified."\
                        + "1: Plot to first AxiDraw Found. "\
                        + "2: Plot to specified AxiDraw. "\
                        + "3: Plot to all AxiDraw units. ")

    options.add_argument("--port",\
                        type=str, action="store", dest="port",\
                        default=config["port"],\
                        help="Serial port or named AxiDraw to use")
    options.add_argument("--port_choice",\
                        type=str, action="store", dest="port_choice",\
                        default=config.get("port_choice", "auto"),\
                        help="Quick-select serial port from UI dropdown; 'auto' to skip.")

    options.add_argument("--controller",\
                        type=str, action="store", dest="controller",\
                        default=config.get("controller", "grbl_esp32"),\
                        help="Controller backend: 'grbl_esp32'.")

    options.add_argument("--grbl_baud_rate",\
                        type=int, action="store", dest="grbl_baud_rate",\
                        default=config.get("grbl_baud_rate", 115200),\
                        help="Serial baud rate for Grbl backend.")

    options.add_argument("--grbl_auto_fetch",\
                        type=inkex.boolean_option, action="store", dest="grbl_auto_fetch",\
                        default=config.get("grbl_auto_fetch", True),\
                        help="Auto-fetch $$ settings from Grbl on connect.")

    options.add_argument("--grbl_command_timeout",\
                        type=float, action="store", dest="grbl_command_timeout",\
                        default=config.get("grbl_command_timeout", 2.0),\
                        help="Command timeout (seconds) for Grbl backend.")
    options.add_argument("--grbl_pen_up_cmd",\
                        type=str, action="store", dest="grbl_pen_up_cmd",\
                        default=config.get("grbl_pen_up_cmd", "G1 Z0 F3000"),\
                        help="G-code command for pen-up in Grbl mode.")
    options.add_argument("--grbl_pen_down_cmd",\
                        type=str, action="store", dest="grbl_pen_down_cmd",\
                        default=config.get("grbl_pen_down_cmd", "G1 Z5 F3000"),\
                        help="G-code command for pen-down in Grbl mode.")
    options.add_argument("--grbl_pen_down_slow_feed",\
                        type=float, action="store", dest="grbl_pen_down_slow_feed",\
                        default=config.get("grbl_pen_down_slow_feed", 0.0),\
                        help="If > 0, use this slower mm/min feed for pen-down Z moves in Grbl mode.")
    options.add_argument("--grbl_pen_down_settle_ms",\
                        type=int, action="store", dest="grbl_pen_down_settle_ms",\
                        default=config.get("grbl_pen_down_settle_ms", 0),\
                        help="Extra settle delay after pen-down in Grbl mode, milliseconds.")
    options.add_argument("--grbl_disable_motors_cmd",\
                        type=str, action="store", dest="grbl_disable_motors_cmd",\
                        default=config.get("grbl_disable_motors_cmd", "$MD"),\
                        help="G-code command for disabling steppers in Grbl mode.")
    options.add_argument("--grbl_coordinate_origin",\
                        type=str, action="store", dest="grbl_coordinate_origin",\
                        default=config.get("grbl_coordinate_origin", "top_left"),\
                        help="Logical document origin for Grbl mapping: top_left/top_right/bottom_left/bottom_right/center.")
    options.add_argument("--grbl_axis_swap_xy",\
                        type=inkex.boolean_option, action="store", dest="grbl_axis_swap_xy",\
                        default=config.get("grbl_axis_swap_xy", False),\
                        help="Software axis transform: swap X and Y for Grbl backend.")
    options.add_argument("--grbl_axis_invert_x",\
                        type=inkex.boolean_option, action="store", dest="grbl_axis_invert_x",\
                        default=config.get("grbl_axis_invert_x", False),\
                        help="Software axis transform: invert X sign for Grbl backend.")
    options.add_argument("--grbl_axis_invert_y",\
                        type=inkex.boolean_option, action="store", dest="grbl_axis_invert_y",\
                        default=config.get("grbl_axis_invert_y", False),\
                        help="Software axis transform: invert Y sign for Grbl backend.")
    options.add_argument("--grbl_set_dir_mask",\
                        type=int, action="store", dest="grbl_set_dir_mask",\
                        default=config.get("grbl_set_dir_mask", -1),\
                        help="In axis_apply mode, write this integer bitmask to $3.")
    options.add_argument("--grbl_set_homing_dir_mask",\
                        type=int, action="store", dest="grbl_set_homing_dir_mask",\
                        default=config.get("grbl_set_homing_dir_mask", -1),\
                        help="In axis_apply mode, write this integer bitmask to $23.")
    options.add_argument("--grbl_dir_invert_x",\
                        type=inkex.boolean_option, action="store", dest="grbl_dir_invert_x",\
                        default=config.get("grbl_dir_invert_x", False),\
                        help="In axis_apply mode, set X bit in Grbl $3 mask.")
    options.add_argument("--grbl_dir_invert_y",\
                        type=inkex.boolean_option, action="store", dest="grbl_dir_invert_y",\
                        default=config.get("grbl_dir_invert_y", False),\
                        help="In axis_apply mode, set Y bit in Grbl $3 mask.")
    options.add_argument("--grbl_dir_invert_z",\
                        type=inkex.boolean_option, action="store", dest="grbl_dir_invert_z",\
                        default=config.get("grbl_dir_invert_z", False),\
                        help="In axis_apply mode, set Z bit in Grbl $3 mask.")
    options.add_argument("--grbl_home_invert_x",\
                        type=inkex.boolean_option, action="store", dest="grbl_home_invert_x",\
                        default=config.get("grbl_home_invert_x", False),\
                        help="In axis_apply mode, set X bit in Grbl $23 mask.")
    options.add_argument("--grbl_home_invert_y",\
                        type=inkex.boolean_option, action="store", dest="grbl_home_invert_y",\
                        default=config.get("grbl_home_invert_y", False),\
                        help="In axis_apply mode, set Y bit in Grbl $23 mask.")
    options.add_argument("--grbl_home_invert_z",\
                        type=inkex.boolean_option, action="store", dest="grbl_home_invert_z",\
                        default=config.get("grbl_home_invert_z", False),\
                        help="In axis_apply mode, set Z bit in Grbl $23 mask.")
    options.add_argument("--manual_pen_change",\
                        type=inkex.boolean_option, action="store", dest="manual_pen_change",\
                        default=config.get("manual_pen_change", False),\
                        help="Enable pause-based pen change flow between layers.")
    options.add_argument("--pen_change_to_home",\
                        type=inkex.boolean_option, action="store", dest="pen_change_to_home",\
                        default=config.get("pen_change_to_home", True),\
                        help="Move to home during pen change flow.")
    options.add_argument("--pen_change_prompt",\
                        type=inkex.boolean_option, action="store", dest="pen_change_prompt",\
                        default=config.get("pen_change_prompt", True),\
                        help="Prompt user to confirm pen change before resuming.")
    options.add_argument("--auto_pause_between_layers",\
                        type=inkex.boolean_option, action="store", dest="auto_pause_between_layers",\
                        default=config.get("auto_pause_between_layers", False),\
                        help="Automatically pause before each non-empty layer after the first.")
    options.add_argument("--bounds_auto_scale",\
                        type=inkex.boolean_option, action="store", dest="bounds_auto_scale",\
                        default=config.get("bounds_auto_scale", False),\
                        help="Automatically scale oversized plots to fit travel bounds.")
    options.add_argument("--bounds_auto_scale_prompt",\
                        type=inkex.boolean_option, action="store", dest="bounds_auto_scale_prompt",\
                        default=config.get("bounds_auto_scale_prompt", True),\
                        help="Prompt before applying automatic bounds scaling.")
    options.add_argument("--auto_sparse_linework",\
                        type=inkex.boolean_option, action="store", dest="auto_sparse_linework",\
                        default=config.get("auto_sparse_linework", True),\
                        help="Automatically thin dense line-only artwork before plotting.")
    options.add_argument("--auto_sparse_line_mode",\
                        type=str, action="store", dest="auto_sparse_line_mode",\
                        default=config.get("auto_sparse_line_mode", "standard"),\
                        help="Dense linework thinning mode: off/conservative/standard/aggressive.")
    options.add_argument("--grbl_path_optim_mode",\
                        type=str, action="store", dest="grbl_path_optim_mode",\
                        default=config.get("grbl_path_optim_mode", "standard"),\
                        help="Plotter path cleanup mode: off/conservative/standard/aggressive.")

    options.add_argument("--setup_type",\
                        type=str, action="store", dest="setup_type",\
                        default="align",\
                        help="Setup option selected (GUI Only)")

    options.add_argument("--resume_type",\
                        type=str, action="store", dest="resume_type",\
                        default="plot",
                        help="The resume option selected (GUI Only)")

    options.add_argument("--auto_rotate",\
                        type=inkex.boolean_option, action="store", dest="auto_rotate",\
                        default=config["auto_rotate"], \
                        help="Auto select portrait vs landscape orientation")

    options.add_argument("--random_start",\
                        type=inkex.boolean_option, action="store", dest="random_start",\
                        default=config["random_start"], \
                        help="Randomize start locations of closed paths")

    options.add_argument("--hiding",\
                        type=inkex.boolean_option, action="store", dest="hiding",\
                        default=config["hiding"], \
                        help="Hidden-line removal")

    options.add_argument("--reordering",\
                        type=int, action="store", dest="reordering",\
                        default=config["reordering"],\
                        help="SVG reordering option (0-4; 3 deprecated)."\
                        + " 0: Least: Only connect adjoining paths."\
                        + " 1: Basic: Also reorder paths for speed."\
                        + " 2: Full: Also allow path reversal."\
                        + " 4: None: Strictly preserve file order.")

    options.add_argument("--resolution",\
                        type=int, action="store", dest="resolution",\
                        default=config["resolution"],\
                        help="Resolution option selected")

    options.add_argument("--digest",\
                        type=int, action="store", dest="digest",\
                        default=config["digest"],\
                        help="Plot optimization option (0-2)."\
                        + "0: No change to behavior or output (Default)."\
                        + "1: Output 'plob' digest, not full SVG, when saving file. "\
                        + "2: Disable plots and previews; generate digest only. ")

    options.add_argument("--webhook",\
                        type=inkex.boolean_option, action="store", dest="webhook",\
                        default=config["webhook"],\
                        help="Enable webhook callback when a plot finishes")

    options.add_argument("--webhook_url",\
                        type=str, action="store", dest="webhook_url",\
                        default=config["webhook_url"],\
                        help="Webhook URL to be used if webhook is enabled")

    options.add_argument("--submode",\
                        action="store", type=str, dest="submode",\
                        default="none", \
                        help="Secondary GUI tab.")

    options.add_argument("--language",\
                        action="store", type=str, dest="language",\
                        default=config.get("language", "auto"),\
                        help="UI language override: auto/en/zh_CN.")

    return options

def core_mode_options(config):
    ''' these are also common options, but unlike options in `core_options`, these
    are options that are more specific to this repo '''
    options = argparse.ArgumentParser(add_help = False) # parent parser

    options.add_argument("--mode",\
                        action="store", type=str, dest="mode",\
                        default=config["mode"], \
                        help="Mode or GUI tab. One of: [plot, layers, align, toggle, cycle"\
                        + ", manual, sysinfo, version, res_plot, res_home]. Default: plot.")

    options.add_argument("--manual_cmd",\
                        type=str, action="store", dest="manual_cmd",\
                        default=config["manual_cmd"],\
                        help="Manual command. One of: [fw_version, raise_pen, lower_pen, "\
                        + "walk_x, walk_y, walk_mmx, walk_mmy, walk_mmx_pos, walk_mmx_neg, "\
                        + "walk_mmy_pos, walk_mmy_neg, jog_stop, walk_home, enable_xy, "\
                        + "disable_xy, axis_read, axis_apply, status_refresh, home_cycle, ports_scan, "\
                        + "res_read, res_adj_in, res_adj_mm, strip_data]. Default: fw_version")

    options.add_argument("--dist", "--walk_dist",\
                        type=float, action="store", dest="dist",\
                        default=config["dist"],\
                        help="Distance for manual walk or changing resume position. "\
                            + "(The argument name walk_dist is deprecated.)")
    options.add_argument("--manual_jog_step_preset",\
                        type=str, action="store", dest="manual_jog_step_preset",\
                        default=config.get("manual_jog_step_preset", "1"),\
                        help="Manual jog step preset in mm: custom/0.1/1/5/10.")
    options.add_argument("--manual_jog_repeat",\
                        type=int, action="store", dest="manual_jog_repeat",\
                        default=config.get("manual_jog_repeat", 1),\
                        help="Repeat count for manual jog burst actions in plugin mode.")
    options.add_argument("--manual_auto_status_refresh",\
                        type=inkex.boolean_option, action="store", dest="manual_auto_status_refresh",\
                        default=config.get("manual_auto_status_refresh", True),\
                        help="Refresh position/status after manual jog actions.")

    options.add_argument("--layer",\
                        type=int, action="store", dest="layer",\
                        default=config["default_layer"],\
                        help="Layer(s) selected for layers mode (1-1000). Default: 1")

    options.add_argument("--copies",\
                        type=int, action="store", dest="copies",\
                        default=config["copies"],\
                        help="Copies to plot, or 0 for continuous plotting. Default: 1")

    return options
